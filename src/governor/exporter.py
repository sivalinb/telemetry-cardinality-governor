"""Synthetic workload, real Prometheus exposition and ingestion."""


def exposition(pool: str, scenario: str) -> str:
    exploding = scenario in {"explosion", "rollback"}
    lines = [
        "# HELP checkout_requests_total Completed requests",
        "# TYPE checkout_requests_total counter",
    ]
    for route in ["/cart", "/pay"]:
        for status in ["200", "500"]:
            lines.append(f'checkout_requests_total{{route="{route}",status="{status}"}} 1000')
    lines.extend(
        [
            "# HELP checkout_request_duration_seconds Request duration",
            "# TYPE checkout_request_duration_seconds histogram",
        ]
    )
    for route in ["/cart", "/pay"]:
        for bound, count in [("0.1", 100), ("0.3", 800), ("1", 990), ("+Inf", 1000)]:
            lines.append(
                f'checkout_request_duration_seconds_bucket{{route="{route}",le="{bound}"}} {count}'
            )
        lines.append(f'checkout_request_duration_seconds_sum{{route="{route}"}} 210')
        lines.append(f'checkout_request_duration_seconds_count{{route="{route}"}} 1000')
    error = 0.16 if scenario == "rollback" and pool == "canary" else 0.001
    lines.extend(
        [
            "# HELP checkout_error_ratio Workload error ratio",
            "# TYPE checkout_error_ratio gauge",
            f"checkout_error_ratio {error}",
        ]
    )
    lines.extend(
        [
            "# HELP checkout_debug_events_total Optional debug events",
            "# TYPE checkout_debug_events_total counter",
        ]
    )
    for index in range(1600 if exploding else 12):
        route = "/cart" if index % 2 else "/pay"
        lines.append(
            f'checkout_debug_events_total{{request_id="req-{index:05d}",route="{route}"}} 1'
        )
    return "\n".join(lines) + "\n"
