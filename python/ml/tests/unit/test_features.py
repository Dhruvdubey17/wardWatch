import math
from collections.abc import Mapping, Sequence
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from wardwatch_ml.data import load_physionet
from wardwatch_ml.features import MAPPED_VARIABLES, FeatureSpec, build_features

pytestmark = pytest.mark.unit

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "physionet"
NAN = math.nan


def stay(patient_id: str, columns: Mapping[str, Sequence[float]], site: str = "A") -> pd.DataFrame:
    length = len(next(iter(columns.values())))
    frame = pd.DataFrame({"site": site, "patient_id": patient_id, "hour": np.arange(1, length + 1)})
    for variable in MAPPED_VARIABLES:
        frame[variable] = columns.get(variable, [NAN] * length)
    return frame


def column(features: pd.DataFrame, name: str) -> list[float | None]:
    return [None if math.isnan(v) else round(float(v), 6) for v in features[name]]


def test_feature_names_are_unique_and_complete() -> None:
    names = FeatureSpec().feature_names()
    assert len(names) == len(set(names))
    # 13 variables x (3 + 3 deltas + 6 window statistics) + 6 NEWS2 parts + total + hour
    assert len(names) == 13 * 12 + 6 + 2


def test_last_value_respects_staleness() -> None:
    hr = [80, NAN, NAN, NAN, NAN, NAN, NAN, NAN, 90]
    features = build_features(stay("p1", {"HR": hr}))
    # Heart rate goes stale after 6 hours: hours 1-7 carry 80, hour 8 is blank.
    assert column(features, "HR_last") == [80, 80, 80, 80, 80, 80, 80, None, 90]


def test_measured_indicator_and_hours_since() -> None:
    lactate = [NAN, 2.0, NAN, NAN, 3.0] + [NAN] * 25
    features = build_features(stay("p1", {"Lactate": lactate}))
    assert column(features, "Lactate_measured")[:6] == [0, 1, 0, 0, 1, 0]
    since = column(features, "Lactate_hours_since")
    assert since[:6] == [None, 0, 1, 2, 0, 1]
    # Last value at hour 5; hour 29 is 24 hours later (at the cap), hour 30 is past it.
    assert since[28] == 24
    assert since[29] is None


def test_deltas_use_last_values_hours_apart() -> None:
    resp = [16, 18, 20, 22, 24, 26, 28]
    features = build_features(stay("p1", {"Resp": resp}))
    assert column(features, "Resp_delta_1h") == [None, 2, 2, 2, 2, 2, 2]
    assert column(features, "Resp_delta_3h") == [None, None, None, 6, 6, 6, 6]
    # Hour 7 minus hour 1: 28 - 16.
    assert column(features, "Resp_delta_6h")[6] == 12


def test_window_statistics_use_recorded_values_only() -> None:
    sbp = [120, NAN, 100, NAN, 110, 90, 130, NAN]
    features = build_features(stay("p1", {"SBP": sbp}))
    # Hour 6 window (hours 1-6): 120, 100, 110, 90.
    assert column(features, "SBP_min_6h")[5] == 90
    assert column(features, "SBP_max_6h")[5] == 120
    assert column(features, "SBP_mean_6h")[5] == 105
    # Hour 8 window (hours 3-8): 100, 110, 90, 130; hour 1 has dropped out.
    assert column(features, "SBP_max_6h")[7] == 130
    assert column(features, "SBP_mean_6h")[7] == 107.5
    # Hour 2 window has only hour 1.
    assert column(features, "SBP_mean_12h")[1] == 120


def test_empty_window_is_blank() -> None:
    features = build_features(stay("p1", {"WBC": [NAN, NAN, NAN]}))
    assert column(features, "WBC_min_6h") == [None, None, None]
    assert column(features, "WBC_mean_12h") == [None, None, None]


def test_news2_features_follow_the_last_values() -> None:
    features = build_features(
        stay(
            "p1",
            {
                "Resp": [26, NAN],
                "O2Sat": [93, NAN],
                "FiO2": [0.4, NAN],
                "SBP": [95, NAN],
                "HR": [120, NAN],
                "Temp": [39.5, NAN],
            },
        )
    )
    first = features.iloc[0]
    # 3 + 2 + 2 + 2 + 2 + 2 = 13; the second hour carries every value forward.
    assert first["news2_total"] == 13
    assert features.iloc[1]["news2_total"] == 13
    assert first["news2_respiratory_rate"] == 3
    assert "news2_consciousness" not in features.columns


def test_icu_hour_and_keys() -> None:
    features = build_features(stay("p9", {"HR": [70, 71, 72]}, site="B"))
    assert list(features.columns[:3]) == ["site", "patient_id", "hour"]
    assert features["icu_hour"].tolist() == [1, 2, 3]
    assert set(features["site"]) == {"B"}


def test_stays_do_not_leak_into_each_other() -> None:
    first = stay("p1", {"HR": [100] * 12, "Lactate": [4.0] + [NAN] * 11})
    second = stay("p2", {"HR": [NAN, 60]})
    features = build_features(pd.concat([first, second], ignore_index=True))
    p2 = features[features["patient_id"] == "p2"]
    assert column(p2, "HR_last") == [None, 60]
    assert column(p2, "HR_max_12h") == [None, 60]
    assert column(p2, "Lactate_last") == [None, None]
    assert column(p2, "HR_delta_1h") == [None, None]


def test_missing_hours_are_rejected() -> None:
    frame = stay("p1", {"HR": [70, 71, 72]})
    frame.loc[2, "hour"] = 5
    with pytest.raises(ValueError, match="missing hours"):
        build_features(frame)


def test_spec_without_news2_inputs_is_rejected() -> None:
    with pytest.raises(ValueError, match="NEWS2 inputs"):
        build_features(stay("p1", {"HR": [70]}), FeatureSpec(variables=("HR",)))


def test_spec_round_trips_through_json() -> None:
    spec = FeatureSpec()
    assert FeatureSpec.from_json(spec.to_json()) == spec
    # Longest look-back: a 6-hour delta of a lab carried 24 hours, plus the current hour.
    assert spec.history_hours == 31


def test_one_stay_alone_matches_the_same_stay_in_a_table() -> None:
    frame = load_physionet(FIXTURES)
    together = build_features(frame).set_index(["site", "patient_id", "hour"])
    for _, rows in frame.groupby(["site", "patient_id"]):
        alone = build_features(rows.reset_index(drop=True)).set_index(
            ["site", "patient_id", "hour"]
        )
        pd.testing.assert_frame_equal(alone, together.loc[alone.index], check_exact=True)


def test_fixture_features_are_finite_where_present() -> None:
    features = build_features(load_physionet(FIXTURES))
    values = features.drop(columns=["site", "patient_id", "hour"]).to_numpy()
    assert not np.isinf(values).any()
    # Site A has 298 hours; site B 38 + 46 + 20 + 34 + 30 + 25 + 40 + 27 = 260.
    assert len(features) == 298 + 260


values = st.one_of(st.just(NAN), st.floats(min_value=0, max_value=300, allow_nan=False))


@settings(max_examples=60, deadline=None)
@given(
    data=st.data(),
    length=st.integers(min_value=2, max_value=40),
)
def test_features_at_t_ignore_rows_after_t(data: st.DataObject, length: int) -> None:
    columns = {
        variable: data.draw(st.lists(values, min_size=length, max_size=length))
        for variable in ("HR", "Resp", "SBP", "Lactate", "FiO2", "Temp", "O2Sat")
    }
    cutoff = data.draw(st.integers(min_value=1, max_value=length - 1))
    original = stay("p1", columns)
    altered = original.copy()
    replacement = data.draw(st.lists(values, min_size=length - cutoff, max_size=length - cutoff))
    for variable in columns:
        altered.loc[cutoff:, variable] = replacement
    before = build_features(original).iloc[:cutoff]
    after = build_features(altered).iloc[:cutoff]
    truncated = build_features(original.iloc[:cutoff])
    pd.testing.assert_frame_equal(before, after, check_exact=True)
    pd.testing.assert_frame_equal(before, truncated, check_exact=True)
