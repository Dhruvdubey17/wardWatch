"""Writes metrics.json, summary.md and the plots for one evaluation run."""

import json
import math
from pathlib import Path
from typing import Any

import matplotlib as mpl

mpl.use("Agg")
import matplotlib.pyplot as plt  # the backend must be chosen before pyplot loads

MODEL_NAMES = {"xgboost": "XGBoost", "gru": "GRU", "news2": "NEWS2"}


def _format(value: float, digits: int = 3) -> str:
    return (
        "n/a"
        if value is None or (isinstance(value, float) and math.isnan(value))
        else f"{value:.{digits}f}"
    )


def _with_ci(value: float, interval: list[float], digits: int = 3) -> str:
    low, high = interval
    return f"{_format(value, digits)} ({_format(low, digits)} to {_format(high, digits)})"


def write_metrics(path: Path, metrics: dict[str, Any]) -> None:
    def default(value: object) -> object:
        if isinstance(value, float) and math.isnan(value):
            return None
        raise TypeError(f"cannot serialize {type(value).__name__}")

    # NaN is not JSON; the replacement below turns it into null.
    text = json.dumps(metrics, indent=2, sort_keys=True, default=default)
    path.write_text(text.replace("NaN", "null") + "\n")


def _row(cells: list[str]) -> str:
    return "| " + " | ".join(cells) + " |"


def _event_row(model: str, point_name: str, point: dict[str, Any]) -> str:
    events, ci = point["events"], point["ci95"]
    low, high = events["lead_time_iqr_hours"]
    lead = (
        f"{_format(events['median_lead_time_hours'], 1)} ({_format(low, 1)} to {_format(high, 1)})"
    )
    return _row(
        [
            model,
            point_name,
            _format(point["operating_point"]["policy"]["threshold"]),
            _with_ci(events["event_sensitivity"], ci["event_sensitivity"]),
            lead,
            _with_ci(events["alerts_per_patient_day"], ci["alerts_per_patient_day"], 2),
            _with_ci(events["false_alerts_per_patient_day"], ci["false_alerts_per_patient_day"], 2),
            _with_ci(events["ppv_per_alert"], ci["ppv_per_alert"]),
            _with_ci(point["utility"], ci["utility"]),
        ]
    )


EVENT_HEADER = [
    "Model",
    "Operating point",
    "Threshold",
    "Sensitivity",
    "Median lead h (IQR)",
    "Alerts/patient-day",
    "False alerts/non-septic day",
    "PPV per alert",
    "Utility",
]


def _direction_section(direction_name: str, direction: dict[str, Any]) -> list[str]:
    test = direction["test"]
    results = test["results"]
    lines = [
        f"## Train on {direction['train_site']}, test on {test['test_site']} ({direction_name})",
        "",
        f"Test site: {test['test_stays']} stays and {test['test_hours']} hours. "
        "Septic stays with an unknown onset, left out of event metrics: "
        f"{test['unknown_onset_septic_stays']}.",
        "NEWS2 >= 5 burden on the training site: "
        f"{_format(direction['training']['news2_target_alerts_per_day'])} alerts per patient-day.",
        "",
        "### Hour-level",
        "",
        _row(["Model", "AUROC", "AUPRC"]),
        _row(["---"] * 3),
    ]
    for name, block in results.items():
        lines.append(
            _row(
                [
                    MODEL_NAMES[name],
                    _with_ci(block["auroc"], block["ci95"]["auroc"]),
                    _with_ci(block["auprc"], block["ci95"]["auprc"]),
                ]
            )
        )
    lines += ["", "### Event-level", "", _row(EVENT_HEADER), _row(["---"] * len(EVENT_HEADER))]
    for name, block in results.items():
        for point_name, point in block["operating_points"].items():
            lines.append(_event_row(MODEL_NAMES[name], point_name, point))
    lines += [
        "",
        "### Calibration on the test site",
        "",
        _row(["Model", "Calibrator", "Brier", "ECE"]),
        _row(["---"] * 4),
    ]
    for name, block in results.items():
        for calibrator, values in block.get("calibration", {}).items():
            lines.append(
                _row(
                    [
                        MODEL_NAMES[name],
                        calibrator,
                        _format(values["brier"], 4),
                        _format(values["ece"], 4),
                    ]
                )
            )
    lines.append("")
    return lines


def summary_markdown(metrics: dict[str, Any]) -> str:
    config = metrics["config"]
    lines = [
        f"# Evaluation {metrics['run_id']}",
        "",
        f"Git commit `{metrics['git_sha']}`. Produced by `{metrics['command']}`.",
        "Intervals are 95% patient-level bootstrap intervals "
        f"({config['bootstrap_resamples']} resamples, seed {config['seed']}).",
        "",
        "## Cohorts",
        "",
        metrics["cohort_markdown"].rstrip(),
        "",
    ]
    for direction_name, direction in metrics["directions"].items():
        lines += _direction_section(direction_name, direction)
    lines += ["## Leakage checks", ""]
    lines += [
        f"- {name}: {'pass' if passed else 'FAIL'}"
        for name, passed in metrics["leakage_checks"].items()
    ]
    return "\n".join(lines) + "\n"


def write_plots(directory: Path, metrics: dict[str, Any]) -> list[str]:
    written = []
    for direction_name, direction in metrics["directions"].items():
        test = direction["test"]
        curves = test["curves"]
        suffix = direction_name
        written.append(
            _line_plot(
                directory / f"roc_{suffix}.png",
                curves["roc"],
                x_label="False positive rate",
                y_label="True positive rate",
                title=f"ROC, {suffix}",
                diagonal=True,
            )
        )
        written.append(
            _line_plot(
                directory / f"pr_{suffix}.png",
                curves["pr"],
                x_label="Recall",
                y_label="Precision",
                title=f"PR, {suffix}",
            )
        )
        written.append(
            _reliability_plot(directory / f"reliability_{suffix}.png", test["results"], suffix)
        )
        written.append(
            _lead_time_plot(directory / f"lead_time_{suffix}.png", test["results"], suffix)
        )
        written.append(
            _burden_plot(
                directory / f"burden_vs_sensitivity_{suffix}.png",
                curves["burden"],
                test["results"],
                suffix,
            )
        )
    return written


def _line_plot(
    path: Path,
    curves: dict[str, dict[str, list[float]]],
    *,
    x_label: str,
    y_label: str,
    title: str,
    diagonal: bool = False,
) -> str:
    figure, axis = plt.subplots(figsize=(5, 4.5))
    for name, curve in curves.items():
        axis.plot(curve["x"], curve["y"], label=MODEL_NAMES[name])
    if diagonal:
        axis.plot([0, 1], [0, 1], color="grey", linestyle=":", linewidth=1)
    axis.set(xlabel=x_label, ylabel=y_label, title=title, xlim=(0, 1), ylim=(0, 1))
    axis.legend()
    figure.tight_layout()
    figure.savefig(path, dpi=120)
    plt.close(figure)
    return path.name


def _reliability_plot(path: Path, results: dict[str, Any], suffix: str) -> str:
    figure, axes = plt.subplots(1, 2, figsize=(9, 4.2), sharey=True)
    for axis, name in zip(axes, ("xgboost", "gru"), strict=True):
        axis.plot([0, 1], [0, 1], color="grey", linestyle=":", linewidth=1)
        for calibrator, values in results.get(name, {}).get("calibration", {}).items():
            bins = [b for b in values["reliability"] if b["count"]]
            axis.plot(
                [b["mean_predicted"] for b in bins],
                [b["observed_rate"] for b in bins],
                marker="o",
                label=f"{calibrator} (ECE {values['ece']:.3f})",
            )
        axis.set(
            title=f"{MODEL_NAMES[name]}, {suffix}",
            xlabel="Predicted probability",
            xlim=(0, 1),
            ylim=(0, 1),
        )
        axis.legend(fontsize=8)
    axes[0].set_ylabel("Observed rate")
    figure.tight_layout()
    figure.savefig(path, dpi=120)
    plt.close(figure)
    return path.name


def _lead_time_plot(path: Path, results: dict[str, Any], suffix: str) -> str:
    figure, axis = plt.subplots(figsize=(6, 4))
    for name, block in results.items():
        for point_name, point in block["operating_points"].items():
            if point_name in ("matched_burden", "news2_urgent"):
                median = point["events"]["median_lead_time_hours"]
                low, high = point["events"]["lead_time_iqr_hours"]
                if median is None or (isinstance(median, float) and math.isnan(median)):
                    continue
                axis.errorbar(
                    [MODEL_NAMES[name]],
                    [median],
                    yerr=[[median - low], [high - median]],
                    fmt="o",
                    capsize=6,
                )
    axis.set(
        ylabel="Hours before onset (median and IQR)", title=f"Lead time at matched burden, {suffix}"
    )
    figure.tight_layout()
    figure.savefig(path, dpi=120)
    plt.close(figure)
    return path.name


def _burden_plot(
    path: Path, curves: dict[str, list[dict[str, float]]], results: dict[str, Any], suffix: str
) -> str:
    figure, axis = plt.subplots(figsize=(6, 4.5))
    for name, points in curves.items():
        usable = [p for p in points if not math.isnan(p["alerts_per_patient_day"] or math.nan)]
        axis.plot(
            [p["alerts_per_patient_day"] for p in usable],
            [p["event_sensitivity"] for p in usable],
            marker=".",
            label=MODEL_NAMES[name],
        )
        for point in results[name]["operating_points"].values():
            events = point["events"]
            axis.scatter(
                [events["alerts_per_patient_day"]],
                [events["event_sensitivity"]],
                marker="x",
                color="black",
            )
    axis.set(
        xlabel="Alerts per patient-day",
        ylabel="Event sensitivity",
        title=f"Burden and sensitivity, {suffix}",
    )
    axis.legend()
    figure.tight_layout()
    figure.savefig(path, dpi=120)
    plt.close(figure)
    return path.name
