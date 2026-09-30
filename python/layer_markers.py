"""Pytest plugin that keeps unit and integration tests apart.

A test under a `tests/unit/` directory must carry the `unit` marker and not
`integration`, and the reverse for `tests/integration/`. Mixing them would let
a slow or networked test slip into the fast suite.
"""

from pathlib import Path

import pytest

LAYERS = ("unit", "integration")


def _layer_for_path(path: Path) -> str | None:
    parts = path.parts
    for index in range(len(parts) - 1):
        if parts[index] == "tests" and parts[index + 1] in LAYERS:
            return parts[index + 1]
    return None


def layer_errors(path: Path, marker_names: set[str]) -> list[str]:
    """Return the marker problems for one test, empty when it is correctly placed."""
    expected = _layer_for_path(path)
    if expected is None:
        return [f"{path} is not under tests/unit/ or tests/integration/"]
    other = "integration" if expected == "unit" else "unit"
    errors: list[str] = []
    if expected not in marker_names:
        errors.append(f"missing the '{expected}' marker")
    if other in marker_names:
        errors.append(f"marked '{other}' but lives under tests/{expected}/")
    return errors


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    problems: list[str] = []
    for item in items:
        names = {marker.name for marker in item.iter_markers()}
        problems.extend(f"{item.nodeid}: {error}" for error in layer_errors(item.path, names))
    if problems:
        raise pytest.UsageError("test layer markers are wrong:\n" + "\n".join(problems))
