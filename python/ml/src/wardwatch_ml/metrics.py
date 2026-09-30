"""Hour-level and event-level metrics, and patient-level bootstrap intervals."""

from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass

import numpy as np
import numpy.typing as npt
import pandas as pd
from sklearn.metrics import average_precision_score, roc_auc_score

from wardwatch_ml.policy import AlertPolicy, alerts_for_stay

# Utility parameters from the PhysioNet/Computing in Cardiology Challenge
# 2019 scoring code (compute_scores_2019.py): predictions are rewarded from
# 12 hours before to 3 hours after the optimal time, which is 6 hours before
# onset; a false alarm costs 0.05 and a missed septic hour up to 2.
DT_EARLY = -12.0
DT_OPTIMAL = -6.0
DT_LATE = 3.0
MAX_U_TP = 1.0
MIN_U_FN = -2.0
U_FP = -0.05
U_TN = 0.0

EVENT_WINDOW_HOURS = 48
HOURS_PER_DAY = 24.0


def auroc(labels: npt.ArrayLike, scores: npt.ArrayLike) -> float:
    return float(roc_auc_score(labels, scores))


def auprc(labels: npt.ArrayLike, scores: npt.ArrayLike) -> float:
    return float(average_precision_score(labels, scores))


def stay_utility(labels: npt.ArrayLike, predictions: npt.ArrayLike) -> float:
    """Utility of one stay's hourly binary predictions (compute_prediction_utility)."""
    label_values = np.asarray(labels, dtype=bool)
    prediction_values = np.asarray(predictions, dtype=bool)
    septic = bool(label_values.any())
    # Row index of onset: the first positive label plus six hours.
    t_sepsis = float(np.argmax(label_values)) - DT_OPTIMAL if septic else float("inf")
    m_1 = MAX_U_TP / (DT_OPTIMAL - DT_EARLY)
    b_1 = -m_1 * DT_EARLY
    m_2 = -MAX_U_TP / (DT_LATE - DT_OPTIMAL)
    b_2 = -m_2 * DT_LATE
    m_3 = MIN_U_FN / (DT_LATE - DT_OPTIMAL)
    b_3 = -m_3 * DT_OPTIMAL
    total = 0.0
    for t, predicted in enumerate(prediction_values):
        if t > t_sepsis + DT_LATE:
            continue
        if septic and predicted:
            if t <= t_sepsis + DT_OPTIMAL:
                total += max(m_1 * (t - t_sepsis) + b_1, U_FP)
            else:
                total += m_2 * (t - t_sepsis) + b_2
        elif not septic and predicted:
            total += U_FP
        elif septic and not predicted:
            if t > t_sepsis + DT_OPTIMAL:
                total += m_3 * (t - t_sepsis) + b_3
        else:
            total += U_TN
    return total


def _best_predictions(labels: npt.NDArray[np.bool_]) -> npt.NDArray[np.bool_]:
    best = np.zeros(len(labels), dtype=bool)
    if labels.any():
        t_sepsis = int(np.argmax(labels) - DT_OPTIMAL)
        start = max(0, t_sepsis + int(DT_EARLY))
        stop = min(t_sepsis + int(DT_LATE) + 1, len(labels))
        best[start:stop] = True
    return best


def utility_components(stays: Sequence[tuple[npt.ArrayLike, npt.ArrayLike]]) -> np.ndarray:
    """(stays, 3) array of observed, best and inaction utility per stay."""
    rows = []
    for labels, predictions in stays:
        label_values = np.asarray(labels, dtype=bool)
        rows.append(
            (
                stay_utility(label_values, predictions),
                stay_utility(label_values, _best_predictions(label_values)),
                stay_utility(label_values, np.zeros(len(label_values), dtype=bool)),
            )
        )
    return np.array(rows, dtype=np.float64).reshape(-1, 3)


def normalized_from_components(components: np.ndarray, weights: np.ndarray | None = None) -> float:
    weight = np.ones(len(components)) if weights is None else weights
    observed, best, inaction = (weight[:, None] * components).sum(axis=0)
    if best == inaction:
        # No septic stay was drawn, so there is nothing to normalize against.
        return float("nan")
    return float((observed - inaction) / (best - inaction))


def normalized_utility(stays: Sequence[tuple[npt.ArrayLike, npt.ArrayLike]]) -> float:
    """Challenge score: (observed - inaction) / (best - inaction) over all stays."""
    return normalized_from_components(utility_components(stays))


@dataclass(frozen=True)
class EventMetrics:
    septic_patients: int
    unknown_onset_excluded: int
    non_septic_patients: int
    event_sensitivity: float
    median_lead_time_hours: float
    lead_time_iqr_hours: tuple[float, float]
    alerts: int
    alerts_per_patient_day: float
    false_alerts_per_patient_day: float
    ppv_per_alert: float

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def add_alerts(scored: pd.DataFrame, policy: AlertPolicy, score_column: str = "score") -> pd.Series:
    """Alert flags for every row of a scored table, applying the policy per stay."""
    flags = pd.Series(False, index=scored.index)
    for _, stay in scored.groupby(["site", "patient_id"], sort=False):
        flags.loc[stay.index] = alerts_for_stay(policy, stay["hour"], stay[score_column])
    return flags


def stay_summary(scored: pd.DataFrame, alerts: pd.Series, outcomes: pd.DataFrame) -> pd.DataFrame:
    """Per-stay counts that every event metric is built from.

    Computed once per operating point; bootstrap resamples then only reweight
    these rows, which keeps 1,000 resamples cheap on 40,000 stays.
    Septic stays with an unknown onset are marked `excluded`.
    """
    by_stay = {
        (str(record["site"]), str(record["patient_id"])): record
        for record in outcomes.to_dict("records")
    }
    rows = []
    table = scored[["site", "patient_id", "hour"]].assign(alert=alerts.to_numpy())
    for _, stay in table.groupby(["site", "patient_id"], sort=False):
        key = (str(stay["site"].iloc[0]), str(stay["patient_id"].iloc[0]))
        outcome = by_stay[key]
        alert_hours = stay.loc[stay["alert"], "hour"].to_numpy(dtype=np.float64)
        septic = bool(outcome["septic"])
        known = bool(outcome["onset_known"])
        true_alerts = 0
        lead_time = float("nan")
        if septic and known:
            onset = float(outcome["onset_hour"])
            window = alert_hours[
                (alert_hours >= onset - EVENT_WINDOW_HOURS) & (alert_hours <= onset)
            ]
            true_alerts = len(window)
            if true_alerts:
                lead_time = onset - float(window.min())
        rows.append(
            {
                "site": key[0],
                "patient_id": key[1],
                "excluded": septic and not known,
                "septic": septic,
                "hours": len(stay),
                "alerts": len(alert_hours),
                "true_alerts": true_alerts,
                "detected": true_alerts > 0,
                "lead_time": lead_time,
            }
        )
    return pd.DataFrame(rows)


def _weighted_percentile(values: np.ndarray, weights: np.ndarray, percent: float) -> float:
    order = np.argsort(values, kind="stable")
    sorted_values, sorted_weights = values[order], weights[order]
    cumulative = np.cumsum(sorted_weights)
    if cumulative[-1] == 0:
        return float("nan")
    # Same convention as np.percentile's linear method when all weights are 1.
    positions = (cumulative - sorted_weights / 2) / cumulative[-1] * 100
    return float(np.interp(percent, positions, sorted_values))


def summary_metrics(summary: pd.DataFrame, weights: np.ndarray | None = None) -> EventMetrics:
    """Event metrics from a stay summary, each stay counted `weights` times."""
    weight = np.ones(len(summary)) if weights is None else np.asarray(weights, dtype=np.float64)
    excluded = summary["excluded"].to_numpy(dtype=bool)
    septic = summary["septic"].to_numpy(dtype=bool) & ~excluded
    clean = ~summary["septic"].to_numpy(dtype=bool)
    kept = ~excluded
    hours = summary["hours"].to_numpy(dtype=np.float64)
    alerts = summary["alerts"].to_numpy(dtype=np.float64)
    true_alerts = summary["true_alerts"].to_numpy(dtype=np.float64)
    detected = summary["detected"].to_numpy(dtype=bool)
    lead = summary["lead_time"].to_numpy(dtype=np.float64)

    septic_weight = float(weight[septic].sum())
    patient_days = float((weight[kept] * hours[kept]).sum()) / HOURS_PER_DAY
    clean_days = float((weight[clean] * hours[clean]).sum()) / HOURS_PER_DAY
    counted_alerts = float((weight[kept] * alerts[kept]).sum())
    detected_weight = float(weight[septic & detected].sum())
    with_lead = septic & detected
    nan = float("nan")
    lead_values, lead_weights = lead[with_lead], weight[with_lead]
    has_lead = lead_weights.sum() > 0
    return EventMetrics(
        septic_patients=round(septic_weight),
        unknown_onset_excluded=round(float(weight[excluded].sum())),
        non_septic_patients=round(float(weight[clean].sum())),
        event_sensitivity=detected_weight / septic_weight if septic_weight else nan,
        median_lead_time_hours=_weighted_percentile(lead_values, lead_weights, 50)
        if has_lead
        else nan,
        lead_time_iqr_hours=(
            (
                _weighted_percentile(lead_values, lead_weights, 25),
                _weighted_percentile(lead_values, lead_weights, 75),
            )
            if has_lead
            else (nan, nan)
        ),
        alerts=round(counted_alerts),
        alerts_per_patient_day=counted_alerts / patient_days if patient_days else nan,
        false_alerts_per_patient_day=(
            float((weight[clean] * alerts[clean]).sum()) / clean_days if clean_days else nan
        ),
        ppv_per_alert=float((weight[kept] * true_alerts[kept]).sum()) / counted_alerts
        if counted_alerts
        else nan,
    )


def event_metrics(scored: pd.DataFrame, alerts: pd.Series, outcomes: pd.DataFrame) -> EventMetrics:
    """Event-level results from per-hour alert flags and per-stay outcomes.

    Septic stays whose onset is unknown (labelled from the first row) are left
    out of every event metric and counted in unknown_onset_excluded. An alert
    is true when it falls within 48 hours before a known onset, inclusive.
    """
    return summary_metrics(stay_summary(scored, alerts, outcomes))


class RankedScores:
    """Hour-level scores sorted once, so weighted AUROC and AUPRC are linear per resample."""

    def __init__(
        self, labels: npt.ArrayLike, scores: npt.ArrayLike, stay_index: npt.ArrayLike
    ) -> None:
        score_values = np.asarray(scores, dtype=np.float64)
        order = np.argsort(-score_values, kind="stable")
        self.labels = np.asarray(labels, dtype=np.float64)[order]
        self.scores = score_values[order]
        self.stay_index = np.asarray(stay_index, dtype=np.int64)[order]
        # Rows that share a score form one step of the curves.
        self.last_of_tie = np.append(self.scores[1:] != self.scores[:-1], True)

    def curves(self, stay_weights: np.ndarray | None) -> tuple[np.ndarray, np.ndarray]:
        weights = (
            np.ones(len(self.labels)) if stay_weights is None else stay_weights[self.stay_index]
        )
        true_positive = np.cumsum(weights * self.labels)[self.last_of_tie]
        false_positive = np.cumsum(weights * (1 - self.labels))[self.last_of_tie]
        return true_positive, false_positive

    def auroc(self, stay_weights: np.ndarray | None = None) -> float:
        tp, fp = self.curves(stay_weights)
        if tp[-1] == 0 or fp[-1] == 0:
            return float("nan")
        tpr = np.concatenate([[0.0], tp / tp[-1]])
        fpr = np.concatenate([[0.0], fp / fp[-1]])
        return float(np.trapezoid(tpr, fpr))

    def auprc(self, stay_weights: np.ndarray | None = None) -> float:
        """Average precision, the step-wise sum sklearn's average_precision_score uses."""
        tp, fp = self.curves(stay_weights)
        if tp[-1] == 0:
            return float("nan")
        precision = tp / np.maximum(tp + fp, 1e-12)
        recall = tp / tp[-1]
        return float(np.sum(np.diff(np.concatenate([[0.0], recall])) * precision))


def bootstrap_weights(stays: int, resamples: int = 1000, seed: int = 2019) -> np.ndarray:
    """(resamples, stays) counts: how often each stay is drawn in each resample."""
    random = np.random.default_rng(seed)
    return random.multinomial(stays, np.full(stays, 1.0 / stays), size=resamples).astype(np.float64)


def bootstrap_interval(
    weights: np.ndarray,
    statistic: Callable[[np.ndarray], float],
    level: float = 0.95,
) -> tuple[float, float]:
    """Percentile interval of a statistic over patient-level bootstrap resamples."""
    values = []
    for resample in weights:
        value = statistic(resample)
        if not np.isnan(value):
            values.append(value)
    if not values:
        return (float("nan"), float("nan"))
    tail = (1 - level) / 2 * 100
    return (float(np.percentile(values, tail)), float(np.percentile(values, 100 - tail)))
