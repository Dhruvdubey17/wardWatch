from datetime import UTC, datetime, timedelta

import pytest
from wardwatch_fhir.fhir_query import (
    DEFAULT_COUNT,
    SearchParameterError,
    parse_count,
    parse_date,
    parse_offset,
    parse_reference,
    parse_sort,
    parse_token,
)

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("value", "prefix", "start", "end"),
    [
        ("2024", "eq", datetime(2024, 1, 1, tzinfo=UTC), datetime(2025, 1, 1, tzinfo=UTC)),
        ("2024-12", "eq", datetime(2024, 12, 1, tzinfo=UTC), datetime(2025, 1, 1, tzinfo=UTC)),
        (
            "ge2024-03-15",
            "ge",
            datetime(2024, 3, 15, tzinfo=UTC),
            datetime(2024, 3, 16, tzinfo=UTC),
        ),
        (
            "lt2024-03-15T13:00",
            "lt",
            datetime(2024, 3, 15, 13, tzinfo=UTC),
            datetime(2024, 3, 15, 13, 1, tzinfo=UTC),
        ),
        (
            "le2024-03-15T13:00:05Z",
            "le",
            datetime(2024, 3, 15, 13, 0, 5, tzinfo=UTC),
            datetime(2024, 3, 15, 13, 0, 6, tzinfo=UTC),
        ),
        (
            "gt2024-03-15T08:00:00-05:00",
            "gt",
            datetime(2024, 3, 15, 13, tzinfo=UTC),
            datetime(2024, 3, 15, 13, 0, 1, tzinfo=UTC),
        ),
        (
            "ne2024-03-15T13:00:05.25+00:00",
            "ne",
            datetime(2024, 3, 15, 13, 0, 5, 250000, tzinfo=UTC),
            datetime(2024, 3, 15, 13, 0, 5, 260000, tzinfo=UTC),
        ),
    ],
)
def test_parse_date_intervals(value: str, prefix: str, start: datetime, end: datetime) -> None:
    parsed = parse_date(value)
    assert (parsed.prefix, parsed.start, parsed.end) == (prefix, start, end)


def test_time_without_zone_is_utc() -> None:
    parsed = parse_date("2024-03-15T13:00:00")
    assert parsed.start == datetime(2024, 3, 15, 13, tzinfo=UTC)
    assert parsed.end - parsed.start == timedelta(seconds=1)


@pytest.mark.parametrize(
    "value", ["sa2024-01-01", "eb2024", "2024-13-01", "2024-02-30", "yesterday", "2024-3-1", ""]
)
def test_parse_date_rejects(value: str) -> None:
    with pytest.raises(SearchParameterError):
        parse_date(value)


@pytest.mark.parametrize(
    ("value", "system", "code"),
    [
        ("http://loinc.org|8867-4", "http://loinc.org", "8867-4"),
        ("|8867-4", "", "8867-4"),
        ("8867-4", None, "8867-4"),
    ],
)
def test_parse_token(value: str, system: str | None, code: str) -> None:
    token = parse_token(value)
    assert (token.system, token.code) == (system, code)


@pytest.mark.parametrize("value", ["", "http://loinc.org|"])
def test_parse_token_rejects(value: str) -> None:
    with pytest.raises(SearchParameterError):
        parse_token(value)


def test_parse_reference() -> None:
    assert parse_reference("Patient/MRN1", "Patient") == "MRN1"
    assert parse_reference("MRN1", "Patient") == "MRN1"
    assert parse_reference("http://x/fhir/Patient/MRN1", "Patient") == "MRN1"
    for bad in ("Encounter/E1", "Patient/"):
        with pytest.raises(SearchParameterError):
            parse_reference(bad, "Patient")


def test_paging_and_sort_parameters() -> None:
    assert parse_count(None) == DEFAULT_COUNT
    assert parse_count("10") == 10
    assert parse_offset(None) == 0
    assert parse_offset("20") == 20
    assert parse_sort(None) == "-date"
    assert parse_sort("date") == "date"
    for count in ("0", "1001", "-1", "ten"):
        with pytest.raises(SearchParameterError):
            parse_count(count)
    with pytest.raises(SearchParameterError):
        parse_offset("-5")
    with pytest.raises(SearchParameterError):
        parse_sort("code")
