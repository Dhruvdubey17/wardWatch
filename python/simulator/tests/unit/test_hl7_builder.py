from datetime import UTC, date, datetime, timedelta

import hl7
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from wardwatch_ml.contracts import contracts_dir, loinc_table
from wardwatch_sim.hl7 import (
    Measurement,
    MessageHeader,
    PatientIdentity,
    Visit,
    build_adt_a01,
    build_adt_a03,
    build_oru_r01,
    escape,
    format_dtm,
    format_nm,
)

pytestmark = pytest.mark.unit

ADMITTED = datetime(2024, 3, 15, 8, 30, tzinfo=UTC)
PATIENT = PatientIdentity(
    mrn="MRN0001234",
    family="Lindgren",
    given="Ada",
    middle="M",
    birth_date=date(1958, 2, 14),
    gender="F",
    street="12 Harbor Rd",
    city="Boston",
    state="MA",
    postal_code="02110",
)
VISIT = Visit(
    visit_number="ENC000001",
    room="01",
    bed="A",
    admitted_at=ADMITTED,
    discharged_at=datetime(2024, 3, 17, 9, 15, tzinfo=UTC),
)
SAMPLE_VALUES = {
    "8867-4": 112.0,
    "9279-1": 24.0,
    "59408-5": 93.0,
    "8310-5": 38.6,
    "8480-6": 98.0,
    "8478-0": 71.0,
    "8462-4": 58.0,
    "3150-0": 0.4,
    "2524-7": 3.1,
    "6690-2": 14.2,
    "2160-0": 1.7,
    "777-3": 142.0,
    "1975-2": 1.2,
}


def contract_sample(name: str) -> str:
    return (contracts_dir() / "hl7" / name).read_bytes().decode("ascii")


def sample_oru() -> str:
    observed = datetime(2024, 3, 15, 13, tzinfo=UTC)
    return build_oru_r01(
        MessageHeader("SIM000007", datetime(2024, 3, 15, 13, 0, 5, tzinfo=UTC)),
        PATIENT,
        VISIT,
        order_id="ENC000001-H0005",
        observed_at=observed,
        measurements=[Measurement(code, value, observed) for code, value in SAMPLE_VALUES.items()],
    )


def test_oru_matches_contract_sample_byte_for_byte() -> None:
    assert sample_oru() == contract_sample("oru_r01.hl7")


def test_adt_a01_matches_contract_sample() -> None:
    header = MessageHeader("SIM000001", ADMITTED)
    assert build_adt_a01(header, PATIENT, VISIT) == contract_sample("adt_a01.hl7")


def test_adt_a03_matches_contract_sample() -> None:
    header = MessageHeader("SIM000099", VISIT.discharged_at or ADMITTED)
    assert build_adt_a03(header, PATIENT, VISIT) == contract_sample("adt_a03.hl7")


def test_independent_parser_reads_every_oru_field() -> None:
    message = hl7.parse(sample_oru())
    assert str(message.segment("MSH")[9]) == "ORU^R01^ORU_R01"
    assert str(message.segment("PID")[3][0][0]) == "MRN0001234"
    assert str(message.segment("PID")[5][0][0]) == "Lindgren"
    assert str(message.segment("PV1")[19][0][0]) == "ENC000001"
    observations = message.segments("OBX")
    assert len(observations) == len(loinc_table())
    units = {entry.code: entry.ucum_unit for entry in loinc_table()}
    for obx, (code, value) in zip(observations, SAMPLE_VALUES.items(), strict=True):
        assert str(obx[3][0][0]) == code
        assert float(str(obx[5])) == value
        assert str(obx[6][0][0]) == units[code]
        assert str(obx[11]) == "F"
        assert str(obx[14]) == "20240315130000+0000"


def test_discharge_needs_a_discharge_time() -> None:
    visit = Visit("E1", "01", "A", ADMITTED)
    with pytest.raises(ValueError, match="discharged_at"):
        build_adt_a03(MessageHeader("X", ADMITTED), PATIENT, visit)


def test_oru_needs_measurements() -> None:
    with pytest.raises(ValueError, match="at least one"):
        build_oru_r01(
            MessageHeader("X", ADMITTED),
            PATIENT,
            VISIT,
            order_id="O1",
            observed_at=ADMITTED,
            measurements=[],
        )


def test_non_ascii_names_declare_utf8_in_msh18() -> None:
    patient = PatientIdentity("M1", "Müller", "José", date(1970, 1, 1), "M")
    message = build_adt_a01(MessageHeader("X", ADMITTED), patient, VISIT)
    header = message.split("\r")[0].split("|")
    # Index 17 is MSH-18 because MSH-1 is the separator itself.
    assert header[17] == "UNICODE UTF-8"
    assert "UNICODE" not in build_adt_a01(MessageHeader("X", ADMITTED), PATIENT, VISIT)


def test_no_segment_ends_with_an_empty_field() -> None:
    for message in (sample_oru(), build_adt_a01(MessageHeader("X", ADMITTED), PATIENT, VISIT)):
        for segment in message.split("\r"):
            assert not segment.endswith("|")


@pytest.mark.parametrize(
    ("text", "escaped"),
    [
        ("plain", "plain"),
        ("a|b", "a\\F\\b"),
        ("a^b", "a\\S\\b"),
        ("a&b", "a\\T\\b"),
        ("a~b", "a\\R\\b"),
        ("a\\b", "a\\E\\b"),
        ("a\rb\nc", "a\\X0D\\b\\X0A\\c"),
    ],
)
def test_escape(text: str, escaped: str) -> None:
    assert escape(text) == escaped


@pytest.mark.parametrize(
    ("value", "text"),
    [
        (112.0, "112"),
        (38.6, "38.6"),
        (-0.03, "-0.03"),
        (0.0, "0"),
        (-0.0, "0"),
        (1e-05, "0.00001"),
        (123456789012.5, "123456789012.5"),
        (0.1 + 0.2, "0.30000000000000004"),
    ],
)
def test_format_nm(value: float, text: str) -> None:
    assert format_nm(value) == text


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_format_nm_rejects_non_finite(value: float) -> None:
    with pytest.raises(ValueError, match="NM"):
        format_nm(value)


@given(st.floats(allow_nan=False, allow_infinity=False, width=64))
def test_format_nm_round_trips_and_never_uses_exponent(value: float) -> None:
    text = format_nm(value)
    assert "e" not in text.lower()
    assert float(text) == value


def test_format_dtm_converts_to_utc_and_requires_timezone() -> None:
    eastern = datetime(2024, 3, 15, 8, 0, tzinfo=UTC) - timedelta(hours=5)
    assert format_dtm(eastern.astimezone(UTC)) == "20240315030000+0000"
    with pytest.raises(ValueError, match="timezone"):
        format_dtm(datetime(2024, 3, 15, 8, 0))


# Any printable text, delimiters and line breaks included, must survive a
# build with our escaping and a parse and unescape by the hl7 package.
names = st.text(
    alphabet=st.characters(min_codepoint=0x20, max_codepoint=0x7E) | st.sampled_from("\r\n|^~&\\"),
    min_size=1,
    max_size=40,
)


@settings(max_examples=300)
@given(family=names, given_name=names, mrn=names)
def test_build_then_parse_round_trips_arbitrary_text(
    family: str, given_name: str, mrn: str
) -> None:
    patient = PatientIdentity(mrn, family, given_name, date(1980, 5, 6), "U")
    text = build_adt_a01(MessageHeader("C1", ADMITTED), patient, VISIT)
    message = hl7.parse(text)
    pid = message.segment("PID")
    assert message.unescape(str(pid[5][0][0])) == family
    assert message.unescape(str(pid[5][0][1])) == given_name
    assert message.unescape(str(pid[3][0][0])) == mrn
    assert len(message.segments("PID")) == 1
