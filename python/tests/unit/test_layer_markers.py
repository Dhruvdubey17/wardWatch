from pathlib import Path

import pytest

from layer_markers import layer_errors

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("path", "markers", "expected"),
    [
        ("pkg/tests/unit/test_a.py", {"unit"}, []),
        ("pkg/tests/integration/test_a.py", {"integration"}, []),
        ("pkg/tests/unit/test_a.py", set(), ["missing the 'unit' marker"]),
        (
            "pkg/tests/unit/test_a.py",
            {"unit", "integration"},
            ["marked 'integration' but lives under tests/unit/"],
        ),
        (
            "pkg/tests/integration/test_a.py",
            {"unit"},
            [
                "missing the 'integration' marker",
                "marked 'unit' but lives under tests/integration/",
            ],
        ),
    ],
)
def test_layer_errors(path: str, markers: set[str], expected: list[str]) -> None:
    assert layer_errors(Path(path), markers) == expected


def test_file_outside_layer_directories_is_rejected() -> None:
    errors = layer_errors(Path("pkg/tests/test_a.py"), {"unit"})
    assert errors == ["pkg/tests/test_a.py is not under tests/unit/ or tests/integration/"]


def test_plugin_rejects_misplaced_marker(pytester: pytest.Pytester) -> None:
    pytester.makeini("[pytest]\nmarkers =\n    unit: u\n    integration: i\n")
    unit_dir = pytester.mkdir("tests").joinpath("unit")
    unit_dir.mkdir()
    unit_dir.joinpath("test_bad.py").write_text(
        "import pytest\n\n@pytest.mark.integration\ndef test_x():\n    pass\n"
    )
    result = pytester.runpytest("-p", "layer_markers")
    result.stderr.fnmatch_lines(["*marked 'integration' but lives under tests/unit/*"])
    assert result.ret != 0
