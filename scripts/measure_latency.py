"""Quantiles of a Prometheus histogram scraped from a /metrics endpoint.

    python scripts/measure_latency.py --url http://127.0.0.1:9465/metrics \\
        --output ingest/bench/results/e2e-msh7-to-alert.json

Quantiles are interpolated linearly inside the bucket that holds them, as
Prometheus's histogram_quantile does, so they are only as fine as the buckets.
"""

import argparse
import json
import math
import platform
import re
import sys
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

DEFAULT_METRIC = "wardwatch_msh7_to_alert_publish_seconds"
QUANTILES = (0.5, 0.9, 0.95, 0.99)


def parse_buckets(text: str, metric: str) -> list[tuple[float, float]]:
    """(upper bound, cumulative count) pairs, summed over label sets, in bound order."""
    pattern = re.compile(rf"^{re.escape(metric)}_bucket\{{(?P<labels>[^}}]*)\}} (?P<value>\S+)$")
    totals: dict[float, float] = {}
    for line in text.splitlines():
        match = pattern.match(line)
        if not match:
            continue
        bound = re.search(r'le="([^"]+)"', match["labels"])
        if bound is None:
            continue
        upper = math.inf if bound.group(1) == "+Inf" else float(bound.group(1))
        totals[upper] = totals.get(upper, 0.0) + float(match["value"])
    return sorted(totals.items())


def quantile(q: float, buckets: list[tuple[float, float]]) -> float | None:
    """The q-quantile, or None without observations; capped at the last finite bound."""
    if not buckets or buckets[-1][1] == 0:
        return None
    rank = q * buckets[-1][1]
    lower_bound, lower_count = 0.0, 0.0
    for upper, count in buckets:
        if count >= rank:
            if math.isinf(upper):
                return lower_bound
            if count == lower_count:
                return upper
            return lower_bound + (upper - lower_bound) * (rank - lower_count) / (
                count - lower_count
            )
        lower_bound, lower_count = upper, count
    return lower_bound


def summarise(text: str, metric: str) -> dict[str, object]:
    buckets = parse_buckets(text, metric)
    return {
        "metric": metric,
        "observations": int(buckets[-1][1]) if buckets else 0,
        "quantiles_seconds": {f"p{round(q * 100)}": quantile(q, buckets) for q in QUANTILES},
        "bucket_bounds_seconds": [bound for bound, _ in buckets if not math.isinf(bound)],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--url", required=True)
    parser.add_argument("--metric", default=DEFAULT_METRIC)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--note", default="", help="how the load was produced, for the record")
    args = parser.parse_args(argv)
    with urllib.request.urlopen(args.url, timeout=10) as response:
        text = response.read().decode()
    result = summarise(text, args.metric) | {
        "source": args.url,
        "measured_at": datetime.now(UTC).isoformat(),
        "host": {"platform": platform.platform(), "machine": platform.machine()},
        "note": args.note,
    }
    rendered = json.dumps(result, indent=2) + "\n"
    if args.output:
        args.output.write_text(rendered)
    sys.stdout.write(rendered)
    return 0 if result["observations"] else 1


if __name__ == "__main__":
    sys.exit(main())
