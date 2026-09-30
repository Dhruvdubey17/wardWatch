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


def normalized_utility(stays: Sequence[tuple[npt.ArrayLike, npt.ArrayLike]]) -> float:
    """Challenge score: (observed - inaction) / (best - inaction) over all stays."""
    observed = best = inaction = 0.0
    for labels, predictions in stays:
        label_values = np.asarray(labels, dtype=bool)
        observed += stay_utility(label_values, predictions)
        best += stay_utility(label_values, _best_predictions(label_values))
        inaction += stay_utility(label_values, np.zeros(len(label_values), dtype=bool))
    return (observed - inaction) / (best - inaction)


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


def event_metrics(scored: pd.DataFrame, alerts: pd.Series, outcomes: pd.DataFrame) -> EventMetrics:
    """Event-level results from per-hour alert flags and per-stay outcomes.

    Septic stays whose onset is unknown (labelled from the first row) are left
    out of every event metric and counted in unknown_onset_excluded. An alert
    is true when it falls within 48 hours before a known onset, inclusive.
    """
    by_stay = {
        (str(record["site"]), str(record["patient_id"])): record
        for record in outcomes.to_dict("records")
    }
    table = scored[["site", "patient_id", "hour"]].assign(alert=alerts.to_numpy())
    stays = table.groupby(["site", "patient_id"], sort=False)

    lead_times: list[float] = []
    detected = true_alerts = counted_alerts = 0
    septic_known = unknown = non_septic = 0
    septic_hours = non_septic_hours = 0.0
    false_alerts_non_septic = 0
    for _, stay in stays:
        row = by_stay[(str(stay["site"].iloc[0]), str(stay["patient_id"].iloc[0]))]
        alert_hours = stay.loc[stay["alert"], "hour"].to_numpy(dtype=np.float64)
        if bool(row["septic"]) and not bool(row["onset_known"]):
            unknown += 1
            continue
        counted_alerts += len(alert_hours)
        if bool(row["septic"]):
            septic_known += 1
            septic_hours += len(stay)
            onset = float(row["onset_hour"])
            in_window = alert_hours[
                (alert_hours >= onset - EVENT_WINDOW_HOURS) & (alert_hours <= onset)
            ]
            true_alerts += len(in_window)
            if len(in_window):
                detected += 1
                lead_times.append(onset - float(in_window.min()))
        else:
            non_septic += 1
            non_septic_hours += len(stay)
            false_alerts_non_septic += len(alert_hours)

    patient_days = (septic_hours + non_septic_hours) / HOURS_PER_DAY
    non_septic_days = non_septic_hours / HOURS_PER_DAY
    nan = float("nan")
    return EventMetrics(
        septic_patients=septic_known,
        unknown_onset_excluded=unknown,
        non_septic_patients=non_septic,
        event_sensitivity=detected / septic_known if septic_known else nan,
        median_lead_time_hours=float(np.median(lead_times)) if lead_times else nan,
        lead_time_iqr_hours=(
            (float(np.percentile(lead_times, 25)), float(np.percentile(lead_times, 75)))
            if lead_times
            else (nan, nan)
        ),
        alerts=counted_alerts,
        alerts_per_patient_day=counted_alerts / patient_days if patient_days else nan,
        false_alerts_per_patient_day=(
            false_alerts_non_septic / non_septic_days if non_septic_days else nan
        ),
        ppv_per_alert=true_alerts / counted_alerts if counted_alerts else nan,
    )


def bootstrap_interval(
    stay_keys: Sequence[tuple[str, str]],
    statistic: Callable[[list[tuple[str, str]]], float],
    resamples: int = 1000,
    seed: int = 2019,
    level: float = 0.95,
) -> tuple[float, float]:
    """Percentile interval of a statistic over stays resampled with replacement."""
    random = np.random.default_rng(seed)
    keys = list(stay_keys)
    values = []
    for _ in range(resamples):
        chosen = random.integers(0, len(keys), size=len(keys))
        value = statistic([keys[index] for index in chosen])
        if not np.isnan(value):
            values.append(value)
    if not values:
        return (float("nan"), float("nan"))
    tail = (1 - level) / 2 * 100
    return (float(np.percentile(values, tail)), float(np.percentile(values, 100 - tail)))
