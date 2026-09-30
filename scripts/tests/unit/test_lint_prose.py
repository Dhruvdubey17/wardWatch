import subprocess
from pathlib import Path

import lint_prose
import pytest
from lint_prose import BANNED_TERMS, check_text, is_checked, lint_paths

pytestmark = pytest.mark.unit


def messages(text: str) -> list[str]:
    return [finding.message for finding in check_text("doc.md", text)]


@pytest.mark.parametrize("term", BANNED_TERMS)
def test_every_banned_term_is_flagged(term: str) -> None:
    assert messages(f"The ingest path is {term} here.") == [f"banned term '{term}'"]


@pytest.mark.parametrize("term", BANNED_TERMS)
def test_banned_terms_match_regardless_of_case(term: str) -> None:
    assert messages(term.upper()) == [f"banned term '{term}'"]


@pytest.mark.parametrize(
    "text",
    [
        "scaler = RobustScaler()",
        "The score was elevated for six hours.",
        "harnessed_power = 1",
        "leveraged_ratio",
        "denote that the field is optional",
        "streamlined_output",
        "furthermore_flag",
        "Theorem utilized_x",
    ],
)
def test_terms_inside_longer_words_are_allowed(text: str) -> None:
    assert messages(text) == []


def test_phrases_match_across_whitespace_and_curly_apostrophes() -> None:
    assert messages("Note  that the ring is full.") == ["banned term 'note that'"]
    assert messages("Let\N{RIGHT SINGLE QUOTATION MARK}s parse it.") == ["banned term 'let's'"]
    assert messages("it's\tworth noting") == ["banned term 'it's worth noting'"]


def test_em_and_en_dashes_are_flagged() -> None:
    assert messages("one \N{EM DASH} two") == ["em or en dash"]
    assert messages("9\N{EN DASH}11") == ["em or en dash"]
    assert messages("a - b and a -- b") == []


@pytest.mark.parametrize("emoji", ["\U0001f680", "✅", "❤️", "\U0001f600", "⭐"])
def test_emoji_are_flagged(emoji: str) -> None:
    assert "emoji" in messages(f"done {emoji}")


def test_plain_unicode_is_not_emoji() -> None:
    assert messages("SpO2 ≤ 91, temperature 38.1 °C, été") == []


@pytest.mark.parametrize(("marker"), ["TODO", "FIXME"])
def test_todo_and_fixme_markers_are_flagged(marker: str) -> None:
    assert messages(f"# {marker}: handle this") == [f"{marker} marker"]
    assert messages(f"# {marker}(someone) later") == [f"{marker} marker"]


def test_marker_inside_identifier_or_lowercase_is_allowed() -> None:
    assert messages("TODOS = 3\nMY_FIXMEX = 1\ntodo list") == []


def test_finding_reports_line_and_column() -> None:
    (finding,) = check_text("a.py", "x = 1\n# a robust parser\n")
    assert (finding.line, finding.column) == (2, 5)
    assert str(finding) == "a.py:2:5: banned term 'robust'"


@pytest.mark.parametrize(
    ("path", "checked"),
    [
        ("CLAUDE.md", False),
        ("PROGRESS.md", False),
        ("README.md", True),
        ("docs/architecture.md", True),
        ("ingest/src/parser.cpp", True),
        ("ingest/CMakeLists.txt", True),
        ("Makefile", True),
        ("python/ml/src/wardwatch_ml/news2.py", True),
        ("python/ml/tests/fixtures/setA/p000001.psv", False),
        ("python/uv.lock", False),
        ("scripts/lint_prose.py", False),
        ("scripts/tests/unit/test_lint_prose.py", False),
        ("docs/diagram.png", False),
    ],
)
def test_is_checked(path: str, checked: bool) -> None:
    assert is_checked(path) is checked


def test_lint_paths_skips_exempt_and_missing_files(tmp_path: Path) -> None:
    (tmp_path / "CLAUDE.md").write_text("seamless")
    (tmp_path / "README.md").write_text("A seamless demo.\n")
    findings = list(lint_paths(tmp_path, ["CLAUDE.md", "README.md", "gone.md"]))
    assert [str(f) for f in findings] == ["README.md:1:3: banned term 'seamless'"]


def test_main_exit_codes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "scripts").mkdir()
    fake_script = tmp_path / "scripts" / "lint_prose.py"
    fake_script.write_text("")
    (tmp_path / "ok.md").write_text("Plain text.\n")
    monkeypatch.setattr(lint_prose, "__file__", str(fake_script))
    assert lint_prose.main([]) == 0
    (tmp_path / "bad.md").write_text("Moreover, it failed.\n")
    assert lint_prose.main([]) == 1
    assert lint_prose.main([str(tmp_path / "ok.md")]) == 0
