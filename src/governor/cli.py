import argparse
import asyncio
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import httpx
import uvicorn

from governor.binaries import fetch
from governor.config import Config
from governor.policies import LabPolicies
from governor.store import Store


def wait_http(url: str, process: subprocess.Popen, timeout=45):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"Process exited before {url} was ready; inspect .runtime/logs")
        try:
            if httpx.get(url, timeout=1).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.25)
    raise RuntimeError(f"Startup deadline exceeded for {url}")


def local(args):
    runtime = Path(args.runtime_dir).resolve()
    binary_dir = Path(args.binary_dir).resolve() if args.binary_dir else runtime / "bin"
    fetch(binary_dir)
    runtime.mkdir(parents=True, exist_ok=True)
    (runtime / "logs").mkdir(exist_ok=True)
    environment = {
        **os.environ,
        "GOVERNOR_RUNTIME_DIR": str(runtime),
        "PROMETHEUS_URL": f"http://127.0.0.1:{args.prometheus_port}",
        "TEMPORAL_ADDRESS": f"127.0.0.1:{args.temporal_port}",
        "GOVERNOR_EXPORTER_TARGET": f"127.0.0.1:{args.port}",
        "GOVERNOR_MONITOR": "false" if args.no_monitor else "true",
        "GOVERNOR_MODE": "lab",
    }
    # Config.from_env is used by the parent only for initial config creation.
    os.environ.update(environment)
    config = Config.from_env()
    LabPolicies(config, Store(config.database)).initialize()
    commands = {
        "temporal": [
            str(binary_dir / "temporal"),
            "server",
            "start-dev",
            "--ip",
            "127.0.0.1",
            "--port",
            str(args.temporal_port),
            "--ui-port",
            str(args.ui_port),
            "--db-filename",
            str(runtime / "temporal.sqlite"),
            "--ui-disable-news-fetch",
        ],
        "api": [sys.executable, "-m", "governor", "serve", "--port", str(args.port)],
        "prometheus": [
            str(binary_dir / "prometheus"),
            "--config.file=" + str(config.prometheus_config),
            "--storage.tsdb.path=" + str(runtime / "prometheus-data"),
            f"--web.listen-address=127.0.0.1:{args.prometheus_port}",
            "--web.enable-lifecycle",
            "--storage.tsdb.retention.time=1h",
        ],
        "worker": [sys.executable, "-m", "governor", "worker"],
    }
    processes: dict[str, subprocess.Popen] = {}
    streams = []
    stopping = False

    def stop(_signum, _frame):
        nonlocal stopping
        stopping = True

    def spawn(name):
        stream = (runtime / "logs" / (name + ".log")).open("a")
        streams.append(stream)
        processes[name] = subprocess.Popen(
            commands[name], env=environment, stdout=stream, stderr=stream
        )
        (runtime / "processes.json").write_text(
            json.dumps({"supervisor": os.getpid(), **{key: p.pid for key, p in processes.items()}})
        )

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    try:
        spawn("temporal")
        wait_http(f"http://127.0.0.1:{args.ui_port}", processes["temporal"])
        spawn("api")
        wait_http(f"http://127.0.0.1:{args.port}/healthz", processes["api"])
        spawn("prometheus")
        wait_http(f"http://127.0.0.1:{args.prometheus_port}/-/ready", processes["prometheus"])
        spawn("worker")
        print(f"Governor dashboard: http://127.0.0.1:{args.port}", flush=True)
        print(f"Temporal history:   http://127.0.0.1:{args.ui_port}", flush=True)
        print(f"Prometheus:         http://127.0.0.1:{args.prometheus_port}", flush=True)
        print("All state persists in " + str(runtime) + ". Ctrl+C stops the stack.", flush=True)
        while not stopping:
            for name, process in list(processes.items()):
                if process.poll() is not None:
                    if name == "worker":
                        print(
                            "Worker exited; restarting it against persisted Temporal history",
                            flush=True,
                        )
                        spawn(name)
                    else:
                        raise RuntimeError(name + " exited; inspect " + str(runtime / "logs"))
            time.sleep(0.25)
    finally:
        for process in reversed(list(processes.values())):
            if process.poll() is None:
                process.terminate()
        for process in reversed(list(processes.values())):
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        for stream in streams:
            stream.close()


def main():
    parser = argparse.ArgumentParser(description="Telemetry Cardinality Governor")
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve", help="Run API, lab exporter and dashboard")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    commands.add_parser("worker", help="Run the Temporal worker")
    commands.add_parser("init", help="Initialize a managed lab scrape config")
    binaries = commands.add_parser(
        "fetch-binaries", help="Fetch official checksum-verified native binaries"
    )
    binaries.add_argument("--directory", default=".runtime/bin")
    launcher = commands.add_parser("local", help="Start a real local Temporal + Prometheus lab")
    launcher.add_argument("--runtime-dir", default=".runtime")
    launcher.add_argument("--binary-dir")
    launcher.add_argument("--port", type=int, default=8000)
    launcher.add_argument("--temporal-port", type=int, default=7233)
    launcher.add_argument("--ui-port", type=int, default=8233)
    launcher.add_argument("--prometheus-port", type=int, default=9090)
    launcher.add_argument("--no-monitor", action="store_true")
    args = parser.parse_args()
    if args.command == "serve":
        uvicorn.run("governor.api:create_app", factory=True, host=args.host, port=args.port)
    elif args.command == "worker":
        from governor.worker import run_worker

        asyncio.run(run_worker())
    elif args.command == "init":
        config = Config.from_env()
        LabPolicies(config, Store(config.database)).initialize()
    elif args.command == "fetch-binaries":
        fetch(Path(args.directory).resolve())
    elif args.command == "local":
        local(args)
