import math

import pytest
from measure_latency import parse_buckets, quantile, summarise

pytestmark = pytest.mark.unit

TEXT = """\
# HELP x_seconds help
# TYPE x_seconds histogram
x_seconds_bucket{le="0.5"} 2.0
x_seconds_bucket{le="1.0"} 6.0
x_seconds_bucket{le="2.0"} 10.0
x_seconds_bucket{le="+Inf"} 10.0
x_seconds_count 10.0
x_seconds_sum 9.1
other_bucket{le="1.0"} 99.0
"""


def test_buckets_are_parsed_in_bound_order() -> None:
    assert parse_buckets(TEXT, "x_seconds") == [
        (0.5, 2.0),
        (1.0, 6.0),
        (2.0, 10.0),
        (math.inf, 10.0),
    ]


def test_label_sets_are_summed() -> None:
    text = (
        'y_bucket{a="1",le="1"} 1\n'
        'y_bucket{a="2",le="1"} 2\n'
        'y_bucket{a="1",le="+Inf"} 1\n'
        'y_bucket{a="2",le="+Inf"} 3\n'
    )
    assert parse_buckets(text, "y") == [(1.0, 3.0), (math.inf, 4.0)]


def test_quantiles_interpolate_inside_the_bucket() -> None:
    buckets = parse_buckets(TEXT, "x_seconds")
    assert quantile(0.2, buckets) == pytest.approx(0.5)
    assert quantile(0.5, buckets) == pytest.approx(0.875)
    assert quantile(0.9, buckets) == pytest.approx(1.75)
    assert quantile(1.0, buckets) == pytest.approx(2.0)


def test_observations_past_the_last_bound_report_that_bound() -> None:
    buckets = [(1.0, 1.0), (math.inf, 4.0)]
    assert quantile(0.99, buckets) == 1.0


def test_no_observations() -> None:
    assert quantile(0.5, []) is None
    assert quantile(0.5, [(1.0, 0.0), (math.inf, 0.0)]) is None
    assert summarise("", "x_seconds")["observations"] == 0


def test_summary() -> None:
    result = summarise(TEXT, "x_seconds")
    assert result["observations"] == 10
    assert result["bucket_bounds_seconds"] == [0.5, 1.0, 2.0]
    quantiles = result["quantiles_seconds"]
    assert isinstance(quantiles, dict)
    assert quantiles["p50"] == pytest.approx(0.875)
