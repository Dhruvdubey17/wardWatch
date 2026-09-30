import json
import math
from pathlib import Path

import pytest
from wardwatch_ml.cli import build_parser, parse_eval
from wardwatch_ml.reporting import summary_markdown, write_metrics, write_plots

pytestmark = pytest.mark.unit


def point(threshold: float) -> dict[str, object]:
    return {
        "operating_point": {
            "policy": {"threshold": threshold, "refractory_hours": 6, "rise_margin": 1}
        },
        "events": {
            "event_sensitivity": 0.8,
            "median_lead_time_hours": 5.0,
            "lead_time_iqr_hours": [2.0, 9.0],
            "alerts_per_patient_day": 1.25,
            "false_alerts_per_patient_day": 0.5,
            "ppv_per_alert": 0.2,
        },
        "utility": 0.31,
        "ci95": {
            name: [0.1, 0.9]
            for name in (
                "utility",
                "event_sensitivity",
                "median_lead_time_hours",
                "alerts_per_patient_day",
                "false_alerts_per_patient_day",
                "ppv_per_alert",
            )
        },
    }


def model(with_calibration: bool) -> dict[str, object]:
    block: dict[str, object] = {
        "auroc": 0.81,
        "auprc": 0.12,
        "ci95": {"auroc": [0.8, 0.82], "auprc": [0.1, 0.14]},
        "operating_points": {"matched_burden": point(0.5), "fixed_sensitivity": point(-0.2)},
    }
    if with_calibration:
        bins = [
            {"count": 3, "mean_predicted": 0.1, "observed_rate": 0.2},
            {"count": 0, "mean_predicted": math.nan, "observed_rate": math.nan},
        ]
        block["calibration"] = {
            name: {"brier": 0.05, "ece": 0.02, "reliability": bins}
            for name in ("uncalibrated", "platt", "isotonic")
        }
    return block


def metrics() -> dict[str, object]:
    curve = {"x": [0.0, 0.5, 1.0], "y": [0.0, 0.7, 1.0]}
    burden = [{"threshold": 0.1, "event_sensitivity": 0.9, "alerts_per_patient_day": 2.0}]
    news2 = model(False)
    news2["operating_points"] = {"news2_urgent": point(5.0)}
    return {
        "run_id": "20260101T000000Z-abc1234",
        "git_sha": "abc1234",
        "command": "wardwatch-ml eval",
        "config": {"bootstrap_resamples": 1000, "seed": 2019},
        "cohort_markdown": "| Site |\n|---|\n| A |\n",
        "directions": {
            "A_to_B": {
                "train_site": "A",
                "training": {"news2_target_alerts_per_day": 1.1},
                "test": {
                    "test_site": "B",
                    "test_stays": 10,
                    "test_hours": 300,
                    "unknown_onset_septic_stays": 2,
                    "results": {"xgboost": model(True), "gru": model(True), "news2": news2},
                    "curves": {
                        "roc": {"xgboost": curve, "gru": curve, "news2": curve},
                        "pr": {"xgboost": curve, "gru": curve, "news2": curve},
                        "burden": {"xgboost": burden, "gru": burden, "news2": burden},
                    },
                },
            }
        },
        "leakage_checks": {"A_to_B: no stay in both sites": True},
    }


def test_summary_has_every_table_and_names_the_command() -> None:
    text = summary_markdown(metrics())
    assert "Produced by `wardwatch-ml eval`" in text
    assert "| XGBoost | 0.810 (0.800 to 0.820) | 0.120 (0.100 to 0.140) |" in text
    assert "| NEWS2 | news2_urgent | 5.000 |" in text
    assert "| GRU | isotonic | 0.0500 | 0.0200 |" in text
    assert "left out of event metrics: 2." in text
    assert "- A_to_B: no stay in both sites: pass" in text


def test_write_metrics_turns_nan_into_null(tmp_path: Path) -> None:
    path = tmp_path / "metrics.json"
    write_metrics(path, {"value": math.nan, "nested": [1.0, math.nan]})
    assert json.loads(path.read_text()) == {"nested": [1.0, None], "value": None}


def test_plots_are_written_for_each_direction(tmp_path: Path) -> None:
    written = write_plots(tmp_path, metrics())
    assert sorted(written) == sorted(
        f"{kind}_A_to_B.png"
        for kind in ("roc", "pr", "reliability", "lead_time", "burden_vs_sensitivity")
    )
    assert all((tmp_path / name).stat().st_size > 1000 for name in written)


def test_parse_eval_directions() -> None:
    parser = build_parser()
    config = parse_eval(
        parser.parse_args(["eval", "--directions", "A:B", "--smoke", "--limit", "5"]), parser
    )
    assert config.directions == (("A", "B"),)
    assert config.smoke
    assert config.limit == 5
    assert config.training().folds == 2
    default = parse_eval(parser.parse_args(["eval"]), parser)
    assert default.directions == (("A", "B"), ("B", "A"))
    assert default.training().folds == 5
    assert default.bootstrap_resamples == 1000


@pytest.mark.parametrize("pairs", ["A:A", "A:C", "AB"])
def test_parse_eval_rejects_bad_directions(pairs: str) -> None:
    parser = build_parser()
    with pytest.raises(SystemExit):
        parse_eval(parser.parse_args(["eval", "--directions", pairs]), parser)
