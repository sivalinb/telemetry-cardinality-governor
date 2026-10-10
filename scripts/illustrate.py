"""Generate editable README diagrams using only the Python standard library."""

from html import escape
from pathlib import Path

ROOT = Path(__file__).parents[1] / "docs" / "images"


def text(x, y, value, size=22, color="#17392c", weight=400):
    return f'<text x="{x}" y="{y}" fill="{color}" font-size="{size}" font-weight="{weight}">{escape(value)}</text>'


def rect(x, y, width, height, fill="white", stroke="#dce7dd", radius=18):
    return f'<rect x="{x}" y="{y}" width="{width}" height="{height}" rx="{radius}" fill="{fill}" stroke="{stroke}"/>'


def svg(name, width, height, body, background="#f5f8f2"):
    ROOT.mkdir(parents=True, exist_ok=True)
    content = f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-label="{name}"><defs><marker id="arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="#6d9680"/></marker></defs><rect width="100%" height="100%" fill="{background}"/><g font-family="Inter,Segoe UI,Arial,sans-serif">{body}</g></svg>'
    (ROOT / (name + ".svg")).write_text(content)


def arrow(x1, y1, x2, y2):
    return f'<path d="M{x1},{y1} L{x2},{y2}" fill="none" stroke="#6d9680" stroke-width="2.5" marker-end="url(#arrow)"/>'


def main():
    body = rect(42, 38, 61, 61, "#a6edcc", "#a6edcc", 17) + text(58, 82, "G", 43, weight=700)
    body += text(124, 61, "TEMPORAL  ×  PROMETHEUS", 16, "#95b9a2", 600)
    body += text(124, 91, "OPEN-SOURCE INFRASTRUCTURE LAB", 12, "#7f9f8b", 500)
    body += text(48, 183, "Telemetry Cardinality", 60, "#eef8ec", 600)
    body += text(48, 255, "Governor", 60, "#a6edcc", 600)
    body += text(50, 302, "More signal. Fewer runaway series.", 25, "#b3ccba")
    body += text(50, 344, "Detect → review → canary → verify → recover", 19, "#84a88f")
    body += rect(899, 122, 450, 227, "#234b37", "#416b4c", 25)
    body += text(925, 162, "MEASURED LOCAL LAB RESULT", 13, "#9ec5a5", 600)
    body += text(925, 232, "1,617", 57, "#eac790", 600)
    body += text(1100, 229, "→", 46, "#729b7b")
    body += text(1173, 232, "17", 57, "#a6edcc", 600)
    body += text(925, 277, "98.95% fewer query-visible series", 19, "#bde3c7", 500)
    body += text(925, 313, "Protected request, latency and error signals retained", 13, "#85aa8b")
    for index, label in enumerate(
        ["REAL WORKFLOWS", "HUMAN APPROVAL", "MEASURED CANARIES", "CRASH RECOVERY"]
    ):
        x = 48 + index * 336
        body += rect(x, 413, 316, 58, "#1c3c2b", "#385741", 12)
        body += text(x + 20, 448, "✓  " + label, 14, "#b9d9bc", 500)
    body += text(
        50,
        516,
        "Synthetic workload • Real Prometheus ingestion • Real Temporal event history",
        15,
        "#769c7d",
    )
    svg("hero", 1400, 560, body, "#153122")

    body = text(46, 62, "A policy control plane around real telemetry", 33, weight=600)
    body += text(
        47,
        96,
        "Temporal coordinates decisions. Prometheus remains the ingestion and query engine.",
        18,
        "#7c9788",
    )
    cards = [
        (
            46,
            148,
            335,
            "Dashboard + API",
            [
                "Review evidence and exact YAML",
                "Approve or reject one workflow",
                "Export Temporal history",
            ],
        ),
        (
            494,
            148,
            384,
            "Temporal service + worker",
            [
                "Durable approval windows and timers",
                "Activity retries and history replay",
                "Canary / promotion / compensation",
            ],
        ),
        (
            991,
            148,
            363,
            "Prometheus",
            [
                "Bounded read-only evidence queries",
                "Metric relabel rules before ingestion",
                "Fresh scrape and coverage checks",
            ],
        ),
    ]
    for x, y, w, title, lines in cards:
        body += rect(x, y, w, 156) + text(x + 23, y + 39, title, 22, weight=600)
        for index, line in enumerate(lines):
            body += text(x + 23, y + 76 + index * 27, line, 15, "#7c9788")
    body += arrow(384, 222, 489, 222) + text(391, 204, "Updates", 13, "#237b50")
    body += arrow(881, 222, 987, 222) + text(897, 204, "Activities", 13, "#237b50")
    body += arrow(1174, 307, 1174, 365)
    for index, (label, desc, fill) in enumerate(
        [
            ("CONTROL", "Unfiltered reference", "#edf3e6"),
            ("CANARY", "Approved rule first", "#e7f4e8"),
            ("PRODUCTION", "Promote after checks", "#faf1e2"),
        ]
    ):
        x = 905 + index * 155
        body += rect(x, 376, 145, 102, fill, "#dbe5d4", 12)
        body += text(x + 13, 409, label, 12, "#237b50", 700) + text(
            x + 13, 440, desc, 10, "#7c9788"
        )
    body += rect(46, 379, 727, 119, "#eef3e8") + text(
        70, 415, "Durable configuration journal + UI projection", 21, weight=600
    )
    body += text(
        70,
        450,
        "SQLite • before-image • atomic rename • operation IDs • exclusive owner",
        16,
        "#7c9788",
    )
    body += arrow(685, 307, 685, 373)
    body += rect(46, 554, 1308, 119, "#173b28", "#173b28")
    body += text(70, 594, "ONE SYNTHETIC EXPORTER, THREE REAL SCRAPE JOBS", 17, "#a6edcc", 600)
    body += text(
        70,
        634,
        "The same workload feeds all pools. Real ingestion rules create the measured series difference.",
        21,
        "#b6d0b9",
    )
    body += arrow(1174, 485, 1174, 547)
    svg("architecture", 1400, 715, body)

    body = text(42, 61, "One approval. Two measured verification gates.", 33, weight=600)
    body += text(
        42,
        96,
        "Detection, analysis, rejection and approval expiry do not modify configuration.",
        18,
        "#7c9788",
    )
    stages = [
        ("01", "Analyze", "1,617 > 400", "request_id: 1,600 values"),
        ("02", "Review", "Exact metric drop", "Approve / reject / expire"),
        ("03", "Canary", "1,617 → 17", "3 passing observations"),
        ("04", "Promote", "Apply same rule", "Lab production job"),
        ("05", "Verify", "Keep or restore", "Coverage + error ratio"),
    ]
    for index, (step, title, first, second) in enumerate(stages):
        x = 42 + index * 269
        body += rect(x, 150, 239, 190) + rect(x + 20, 171, 38, 33, "#e6f1e1", "#e6f1e1", 9)
        body += text(x + 27, 194, step, 14, "#237b50", 600) + text(
            x + 20, 240, title, 25, weight=600
        )
        body += text(x + 20, 279, first, 17, "#237b50", 500) + text(
            x + 20, 311, second, 13, "#7c9788"
        )
        if index < 4:
            body += arrow(x + 242, 243, x + 263, 243)
    body += rect(42, 391, 1316, 121, "#faf1e3", "#e9d9bd")
    body += text(
        66,
        428,
        "Failed canary or production guardrails restore the journaled configuration.",
        23,
        "#966529",
        600,
    )
    body += text(
        66,
        468,
        "Canary failure: 16% errors exceed a 3% guardrail → restore → no promotion.",
        20,
        "#a18456",
    )
    svg("policy-journey", 1400, 556, body)


if __name__ == "__main__":
    main()
