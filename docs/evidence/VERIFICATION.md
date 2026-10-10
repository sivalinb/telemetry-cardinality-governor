# Verification record

Verified on October 9, 2026 using Python 3.12.14 on macOS ARM64, official Temporal CLI 1.9.1, Prometheus 3.15.0 and Temporal Python SDK 1.34.0.

## Local automated verification

```text
GOVERNOR_RUN_E2E=1 pytest -q --junitxml=docs/evidence/test-results.xml
46 passed in 96.01s
```

The run included 33 unit/adapter tests, eight tests against real Temporal development servers, deterministic history replay, and five data-plane tests against actual Temporal/Prometheus processes. One upstream Starlette/httpx test-client deprecation warning appeared; no tests failed or were skipped.

| Scenario | Verified result |
|---|---|
| Explosion, approval, canary and promotion | 1,617 control series; 17 candidate and lab production series; 98.9487% reduction |
| Protected signal retention | Request counters, all duration histogram components and error ratio retained |
| Explicit rejection | Byte-identical managed config and zero policy journal entries |
| Approval expiry | Real Temporal timer expired without scheduling policy deployment |
| Canary errors | Measured 16% error ratio exceeds 3%; restore before-image; no promotion |
| Production verification failure | Compensation restores both managed pools in the workflow contract test |
| SIGKILL during observation | Supervisor restarts worker; same workflow succeeds; two unique policy operations, each applied once |
| Lost reload acknowledgment | Prepared operation retries the same config bytes and journal ID |
| External config modification | Compare-and-swap refuses overwrite and retains external bytes |
| Automatic monitor | Breach starts a real workflow; human rejection prevents deployment |
| History export and replay | Actual event history exported; workflow replays deterministically |

`ruff check .` and formatting checks passed. The native binaries were verified against their official release SHA-256 checksums. The documented exact container tags were verified against Docker Hub manifests. Docker Compose distribution execution is separately covered by the public CI `compose` job; Docker is not installed on the local verification host.

The committed [JUnit report](test-results.xml) records individual local test outcomes. Personal device identifiers are removed from its suite metadata. The [CI workflow](../../.github/workflows/ci.yml) repeats verification on Linux and exercises the container distribution.

## Interpretation

These are bounded lab measurements, not fleet-scale performance results. The workload is synthetic, while Prometheus scraping, relabeling, reloads, Temporal execution, durable history and process recovery are real. The percentage describes current query-visible series, not immediate TSDB disk reclamation or a financial saving.
