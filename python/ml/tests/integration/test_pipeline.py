import json
import time
from pathlib import Path
from typing import Any

import pytest
from wardwatch_ml.bundle import ServingBundle
from wardwatch_ml.cli import EvalConfig, main, run_evaluation

pytestmark = pytest.mark.integration

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "physionet"
# Values that differ between otherwise identical runs.
VOLATILE = {
    "run_id",
    "created_at",
    "elapsed_seconds",
    "timings_seconds",
    "git_sha",
    "command",
    "config",
}


def smoke_config(reports: Path) -> EvalConfig:
    return EvalConfig(
        data_dir=FIXTURES,
        reports_dir=reports,
        directions=(("A", "B"), ("B", "A")),
        limit=None,
        bootstrap_resamples=100,
        seed=2019,
        smoke=True,
    )


def stable(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: stable(item) for key, item in value.items() if key not in VOLATILE}
    if isinstance(value, list):
        return [stable(item) for item in value]
    return value


def test_full_train_and_evaluation_on_fixtures_within_a_minute(tmp_path: Path) -> None:
    started = time.monotonic()
    output = run_evaluation(smoke_config(tmp_path), "test")
    assert time.monotonic() - started < 60
    metrics = json.loads((output / "metrics.json").read_text())
    assert set(metrics["directions"]) == {"A_to_B", "B_to_A"}
    for direction in metrics["directions"].values():
        assert set(direction["test"]["results"]) == {"xgboost", "gru", "news2"}
        # Every fitted artifact saw only the training site.
        for fingerprint in direction["training"]["fingerprints"].values():
            assert fingerprint["sites"] == [direction["train_site"]]
        assert direction["test"]["thresholds_unchanged_by_test_site"] is True
    assert all(metrics["leakage_checks"].values())
    for name in (
        "summary.md",
        "roc_A_to_B.png",
        "reliability_B_to_A.png",
        "burden_vs_sensitivity_A_to_B.png",
    ):
        assert (output / name).is_file(), name


def test_training_twice_with_one_seed_gives_identical_metrics(tmp_path: Path) -> None:
    first = run_evaluation(smoke_config(tmp_path / "first"), "test")
    second = run_evaluation(smoke_config(tmp_path / "second"), "test")
    one = stable(json.loads((first / "metrics.json").read_text()))
    two = stable(json.loads((second / "metrics.json").read_text()))
    assert one == two


def test_train_command_writes_a_loadable_bundle(tmp_path: Path) -> None:
    assert (
        main(["train", "--data-dir", str(FIXTURES), "--artifacts-dir", str(tmp_path), "--smoke"])
        == 0
    )
    current = tmp_path / "current"
    assert current.is_symlink()
    bundle = ServingBundle.load(current.resolve())
    assert bundle.manifest["train_site"] == "A"
    assert bundle.manifest["data_fingerprint"]["sites"] == ["A"]
    assert (
        bundle.policy.threshold
        == bundle.manifest["operating_points"]["matched_burden"]["policy"]["threshold"]
    )
