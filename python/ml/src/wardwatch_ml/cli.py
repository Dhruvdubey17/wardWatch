"""wardwatch-ml: evaluate across sites and train the serving bundle."""

import argparse
import logging
import sys
import time
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from wardwatch_ml.cohort import cohort_markdown, cohort_report
from wardwatch_ml.data import SITE_DIRECTORIES, load_site
from wardwatch_ml.evaluation import evaluate_site
from wardwatch_ml.gru_model import GruParams
from wardwatch_ml.reporting import summary_markdown, write_metrics, write_plots
from wardwatch_ml.runinfo import git_sha, run_id
from wardwatch_ml.training import TrainingConfig, fit_site
from wardwatch_ml.xgb_model import XgbParams

log = logging.getLogger("wardwatch_ml")
REPOSITORY = Path(__file__).resolve().parents[4]


@dataclass(frozen=True)
class EvalConfig:
    data_dir: Path
    reports_dir: Path
    directions: tuple[tuple[str, str], ...]
    limit: int | None
    bootstrap_resamples: int
    seed: int
    smoke: bool

    def training(self) -> TrainingConfig:
        if self.smoke:
            # Fixture-sized: a handful of stays per site, so everything is tiny.
            return TrainingConfig(
                folds=3,
                calibration_fraction=0.34,
                seed=self.seed,
                xgb=XgbParams(max_rounds=30, early_stopping_rounds=10, threads=2, seed=self.seed),
                gru=GruParams(
                    hidden_size=8, epochs=2, batch_size=4, max_steps=6, threads=1, seed=self.seed
                ),
            )
        return TrainingConfig(
            seed=self.seed, xgb=XgbParams(seed=self.seed), gru=GruParams(seed=self.seed)
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="wardwatch-ml", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    evaluate = commands.add_parser("eval", help="train on one site, test on the other, both ways")
    evaluate.add_argument("--data-dir", type=Path, default=REPOSITORY / "data" / "physionet")
    evaluate.add_argument("--reports-dir", type=Path, default=REPOSITORY / "ml" / "reports")
    evaluate.add_argument(
        "--directions", default="A:B,B:A", help="comma-separated train:test pairs"
    )
    evaluate.add_argument("--limit", type=int, default=None, help="stays per site, for quick runs")
    evaluate.add_argument("--bootstrap-resamples", type=int, default=1000)
    evaluate.add_argument("--seed", type=int, default=2019)
    evaluate.add_argument("--smoke", action="store_true", help="tiny models for fixtures and CI")
    return parser


def parse_eval(args: argparse.Namespace, parser: argparse.ArgumentParser) -> EvalConfig:
    directions = []
    for pair in args.directions.split(","):
        train, _, test = pair.partition(":")
        if train not in SITE_DIRECTORIES or test not in SITE_DIRECTORIES or train == test:
            parser.error(f"--directions needs pairs like A:B, got {pair!r}")
        directions.append((train, test))
    return EvalConfig(
        data_dir=args.data_dir,
        reports_dir=args.reports_dir,
        directions=tuple(directions),
        limit=args.limit,
        bootstrap_resamples=args.bootstrap_resamples,
        seed=args.seed,
        smoke=args.smoke,
    )


def run_evaluation(config: EvalConfig, command: str) -> Path:
    """Run every direction and write the report directory; returns its path."""
    started = time.monotonic()
    sha = git_sha(REPOSITORY)
    identifier = run_id(sha)
    output = config.reports_dir / identifier
    output.mkdir(parents=True, exist_ok=False)

    sites = {site for pair in config.directions for site in pair}
    frames = {
        site: load_site(config.data_dir / SITE_DIRECTORIES[site], site, config.limit)
        for site in sorted(sites)
    }
    reports = [report for site in sorted(frames) for report in cohort_report(frames[site])]
    training = config.training()

    directions: dict[str, Any] = {}
    leakage: dict[str, bool] = {}
    for train_site, test_site in config.directions:
        name = f"{train_site}_to_{test_site}"
        log.info("fitting on site %s", train_site)
        fitted = fit_site(frames[train_site], training)
        log.info("testing on site %s", test_site)
        test = evaluate_site(fitted, frames[test_site], config.bootstrap_resamples, config.seed)
        test_ids = set(frames[test_site]["patient_id"])
        train_ids = set(frames[train_site]["patient_id"])
        leakage[f"{name}: no stay in both sites"] = not (test_ids & train_ids)
        leakage[f"{name}: every fitted artifact saw only site {train_site}"] = all(
            fingerprint.sites == (train_site,) for fingerprint in fitted.fingerprints.values()
        )
        leakage[f"{name}: thresholds unchanged by the test site"] = bool(
            test["thresholds_unchanged_by_test_site"]
        )
        directions[name] = {
            "train_site": train_site,
            "training": {
                "news2_target_alerts_per_day": fitted.news2_target_alerts_per_day,
                "fingerprints": {k: v.to_dict() for k, v in fitted.fingerprints.items()},
                "timings_seconds": fitted.timings_seconds,
                "xgboost": fitted.xgb.summary(),
                "gru": None
                if fitted.gru is None
                else {
                    "best_epoch": fitted.gru.best_epoch,
                    "validation_auprc": fitted.gru.validation_auprc,
                },
            },
            "test": test,
        }

    metrics: dict[str, Any] = {
        "run_id": identifier,
        "git_sha": sha,
        "created_at": datetime.now(UTC).isoformat(),
        "command": command,
        "config": {
            **{k: str(v) for k, v in asdict(config).items()},
            "bootstrap_resamples": config.bootstrap_resamples,
            "seed": config.seed,
        },
        "cohort": [report.to_dict() for report in reports],
        "cohort_markdown": cohort_markdown(reports),
        "directions": directions,
        "leakage_checks": leakage,
        "elapsed_seconds": time.monotonic() - started,
    }
    write_metrics(output / "metrics.json", metrics)
    write_plots(output, metrics)
    (output / "summary.md").write_text(summary_markdown(metrics))
    if not all(leakage.values()):
        raise RuntimeError(f"leakage check failed; see {output / 'summary.md'}")
    log.info("wrote %s in %.0f s", output, metrics["elapsed_seconds"])
    return output


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    arguments = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    args = parser.parse_args(arguments)
    if args.command == "eval":
        config = parse_eval(args, parser)
        run_evaluation(config, "wardwatch-ml " + " ".join(arguments))
    return 0


if __name__ == "__main__":
    sys.exit(main())
