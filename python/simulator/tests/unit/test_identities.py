from datetime import date
from pathlib import Path

import pytest
from wardwatch_sim.hl7 import Gender, PatientIdentity
from wardwatch_sim.identities import IdentityAssigner, IdentityPoolExhaustedError, age_on
from wardwatch_sim.synthea import load_patients, read_bundle

pytestmark = pytest.mark.unit

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "synthea"
ADMITTED = date(2024, 3, 15)


def identity(mrn: str, gender: Gender, born: date) -> PatientIdentity:
    return PatientIdentity(mrn, "Family", "Given", born, gender)


def pool() -> list[PatientIdentity]:
    return [
        identity("F40", "F", date(1984, 1, 1)),
        identity("F70", "F", date(1954, 1, 1)),
        identity("F71", "F", date(1953, 1, 1)),
        identity("M65", "M", date(1959, 1, 1)),
        identity("M30", "M", date(1994, 1, 1)),
    ]


def test_reads_identity_from_synthea_bundle() -> None:
    patient = read_bundle(FIXTURES / "Ada432_Lindgren255_a1b2c3.json")
    person = patient.identity
    assert person.mrn == "7f1c2a90-1111-4a4a-9b9b-000000000001"
    assert (person.family, person.given, person.middle) == ("Lindgren", "Ada", "Marie")
    assert person.gender == "F"
    assert person.birth_date == date(1958, 2, 14)
    assert (person.street, person.city, person.postal_code) == ("12 Harbor Rd", "Boston", "02110")


def test_keeps_non_ascii_names() -> None:
    person = read_bundle(FIXTURES / "Zoe5_Muller14_c3d4e5.json").identity
    assert (person.family, person.given) == ("Müller", "Zoë")


def test_bundle_without_one_patient_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "empty.json"
    path.write_text('{"resourceType": "Bundle", "entry": []}')
    with pytest.raises(ValueError, match="0 Patient"):
        read_bundle(path)


def test_load_patients_skips_hospital_bundles(tmp_path: Path) -> None:
    for source in FIXTURES.glob("*.json"):
        (tmp_path / source.name).write_bytes(source.read_bytes())
    (tmp_path / "hospitalInformation1.json").write_text("{}")
    assert len(load_patients(tmp_path)) == 3


@pytest.mark.parametrize(
    ("born", "on", "expected"),
    [
        (date(1958, 2, 14), date(2024, 2, 14), 66.0),
        (date(1958, 2, 14), date(2024, 2, 13), 65 + 364 / 365),
        (date(2000, 2, 29), date(2001, 2, 28), 1.0),
    ],
)
def test_age_on(born: date, on: date, expected: float) -> None:
    assert age_on(born, on) == pytest.approx(expected)


def test_picks_same_gender_with_closest_age() -> None:
    assigner = IdentityAssigner(pool(), seed=1)
    assert assigner.assign("A/p1", "F", 69.5, ADMITTED).mrn == "F70"
    assert assigner.assign("A/p2", "M", 33.0, ADMITTED).mrn == "M30"


def test_active_identities_are_never_reused() -> None:
    assigner = IdentityAssigner(pool(), seed=1)
    chosen = [assigner.assign(f"A/p{i}", "F", 70.0, ADMITTED).mrn for i in range(3)]
    assert len(set(chosen)) == 3
    assert set(chosen) == {"F40", "F70", "F71"}
    # No women left, so the next woman falls back to any free identity.
    assert assigner.assign("A/p9", "F", 70.0, ADMITTED).gender == "M"


def test_released_identity_can_be_used_again() -> None:
    assigner = IdentityAssigner(pool()[:1], seed=1)
    first = assigner.assign("A/p1", "F", 40.0, ADMITTED)
    with pytest.raises(IdentityPoolExhaustedError):
        assigner.assign("A/p2", "F", 40.0, ADMITTED)
    assigner.release(first.mrn)
    assert assigner.assign("A/p2", "F", 40.0, ADMITTED) == first


def test_same_seed_and_sequence_give_same_identities() -> None:
    def run(seed: int) -> list[str]:
        assigner = IdentityAssigner(pool(), seed=seed)
        picks = []
        for i in range(8):
            picked = assigner.assign(f"B/p{i}", None, None, ADMITTED)
            picks.append(picked.mrn)
            if i % 2:
                assigner.release(picked.mrn)
        return picks

    assert run(7) == run(7)


def test_unknown_age_uses_seeded_tie_break() -> None:
    first = IdentityAssigner(pool(), seed=3).assign("A/p1", "M", None, ADMITTED)
    again = IdentityAssigner(pool(), seed=3).assign("A/p1", "M", None, ADMITTED)
    assert first == again


def test_duplicate_mrns_in_pool_are_rejected() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        IdentityAssigner([pool()[0], pool()[0]], seed=1)
