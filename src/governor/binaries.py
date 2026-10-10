"""Fetch pinned official binaries, verify release checksums, extract executables only."""

import hashlib
import json
import os
import platform
import shutil
import tarfile
import tempfile
import urllib.request
from pathlib import Path

VERSIONS = {"temporal": "1.9.1", "prometheus": "3.15.0"}
REPOSITORIES = {"temporal": "temporalio/cli", "prometheus": "prometheus/prometheus"}


def fetch(directory: Path):
    system = platform.system().lower()
    machine = {"arm64": "arm64", "aarch64": "arm64", "x86_64": "amd64"}.get(platform.machine())
    if system not in {"darwin", "linux"} or not machine:
        raise RuntimeError("Native lab supports macOS/Linux on ARM64/AMD64; use Docker elsewhere")
    directory.mkdir(parents=True, exist_ok=True)
    for tool, repo in REPOSITORIES.items():
        if (directory / tool).exists():
            continue
        version = VERSIONS[tool]
        request = urllib.request.Request(
            f"https://api.github.com/repos/{repo}/releases/tags/v{version}",
            headers={"User-Agent": "telemetry-cardinality-governor"},
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            release = json.load(response)
        assets = {asset["name"]: asset["browser_download_url"] for asset in release["assets"]}
        archive = next(
            name
            for name in assets
            if name.endswith(".tar.gz")
            and ((system + "-" + machine) in name or (system + "_" + machine) in name)
        )
        checksum_name = "checksums.txt" if tool == "temporal" else "sha256sums.txt"
        with urllib.request.urlopen(assets[checksum_name], timeout=30) as response:
            checksums = response.read().decode()
        expected = next(
            line.split()[0]
            for line in checksums.splitlines()
            if line.split()[-1].lstrip("*") == archive
        )
        print(f"Downloading official {tool} {version} ({system}/{machine})", flush=True)
        with tempfile.TemporaryDirectory(dir=directory) as temporary:
            path = Path(temporary) / archive
            urllib.request.urlretrieve(assets[archive], path)
            if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                raise RuntimeError("Official release checksum mismatch")
            wanted = {"temporal"} if tool == "temporal" else {"prometheus", "promtool"}
            with tarfile.open(path) as bundle:
                for member in bundle.getmembers():
                    name = Path(member.name).name
                    if name in wanted and member.isfile():
                        source = bundle.extractfile(member)
                        assert source is not None
                        destination = directory / name
                        with destination.open("wb") as stream:
                            shutil.copyfileobj(source, stream)
                        os.chmod(destination, 0o755)
                        wanted.remove(name)
            if wanted:
                raise RuntimeError("Missing executable in verified release")
        print(f"Verified SHA-256 and installed {tool} into {directory}", flush=True)
