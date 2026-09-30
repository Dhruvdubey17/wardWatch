from pathlib import Path

import pytest
from wardwatch_ml import contracts
from wardwatch_ml.contracts import (
    CONTRACTS_ENV,
    contracts_dir,
    loinc_by_code,
    loinc_by_variable,
    loinc_table,
)

pytestmark = pytest.mark.unit


def test_finds_contracts_by_searching_upward(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(CONTRACTS_ENV, raising=False)
    assert (contracts_dir() / "loinc_codes.json").is_file()


def test_environment_variable_overrides_search(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(CONTRACTS_ENV, str(tmp_path))
    assert contracts_dir() == tmp_path


def test_missing_contracts_is_a_clear_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv(CONTRACTS_ENV, raising=False)
    monkeypatch.setattr(contracts, "__file__", str(tmp_path / "a" / "b.py"))
    with pytest.raises(FileNotFoundError, match=CONTRACTS_ENV):
        contracts_dir()


def test_loinc_table_matches_the_contract() -> None:
    table = loinc_table()
    assert len(table) == 13
    assert loinc_by_code()["8867-4"].physionet_variable == "HR"
    assert loinc_by_variable()["FiO2"].code == "3150-0"
    assert loinc_by_variable()["FiO2"].ucum_unit == "1"
    assert {entry.category for entry in table} == {"vital-signs", "laboratory"}
