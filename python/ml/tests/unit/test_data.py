import math
from pathlib import Path

import pandas as pd
import pytest
from wardwatch_ml.data import (
    LABEL,
    PSV_COLUMNS,
    load_physionet,
    load_site,
    read_stay,
    stay_paths,
)

pytestmark = pytest.mark.unit

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "physionet"


def test_psv_columns_are_the_forty_one_challenge_columns() -> None:
    assert len(PSV_COLUMNS) == 41
    assert PSV_COLUMNS[0] == "HR"
    assert PSV_COLUMNS[-1] == LABEL


def test_read_stay_tags_site_patient_and_hour() -> None:
    stay = read_stay(FIXTURES / "training_setA" / "p000001.psv", "A")
    assert list(stay.columns[:3]) == ["site", "patient_id", "hour"]
    assert set(stay["site"]) == {"A"}
    assert set(stay["patient_id"]) == {"p000001"}
    assert stay["hour"].tolist() == list(range(1, 41))
    assert stay["hour"].dtype == "int64"
    assert stay[LABEL].dtype == "int64"


def test_nan_strings_become_missing_values() -> None:
    stay = read_stay(FIXTURES / "training_setA" / "p000001.psv", "A")
    # EtCO2 is never recorded in the fixtures, like most of the real data.
    assert stay["EtCO2"].isna().all()
    # First row: HR 81.66 is present, Temp is recorded every four hours from hour 1.
    first = stay.iloc[0]
    assert first["HR"] == pytest.approx(81.66)
    assert not math.isnan(first["Temp"])
    assert math.isnan(stay.iloc[1]["Temp"])
    assert stay["HR"].dtype == "float64"


def test_rejects_file_with_wrong_columns(tmp_path: Path) -> None:
    bad = tmp_path / "p1.psv"
    bad.write_text("HR|O2Sat\n80|97\n")
    with pytest.raises(ValueError, match="41 PhysioNet"):
        read_stay(bad, "A")


def test_load_site_orders_by_patient_and_hour() -> None:
    site = load_site(FIXTURES / "training_setA", "A")
    assert site["patient_id"].nunique() == 9
    assert (
        site.groupby("patient_id")["hour"].apply(lambda hours: hours.is_monotonic_increasing).all()
    )
    assert site["patient_id"].is_monotonic_increasing


def test_limit_reads_first_stays_only() -> None:
    site = load_site(FIXTURES / "training_setA", "A", limit=2)
    assert sorted(site["patient_id"].unique()) == ["p000001", "p000002"]


def test_empty_directory_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_site(tmp_path, "A")


def test_load_physionet_keeps_sites_apart() -> None:
    both = load_physionet(FIXTURES)
    counts = both.groupby("site")["patient_id"].nunique().to_dict()
    assert counts == {"A": 9, "B": 8}
    assert not set(both.loc[both["site"] == "A", "patient_id"]) & set(
        both.loc[both["site"] == "B", "patient_id"]
    )
    assert isinstance(both, pd.DataFrame)


def test_stay_paths_are_sorted() -> None:
    names = [path.name for path in stay_paths(FIXTURES / "training_setB")]
    assert names == sorted(names)
