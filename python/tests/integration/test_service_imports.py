"""Each service imports with only the dependencies its image installs.

The development environment has every wardwatch-ml extra, so an import of
torch from a scorer module passes every other test and fails only in the
image. Each case runs in a fresh interpreter with the missing extras blocked.
"""

import subprocess
import sys

import pytest

pytestmark = pytest.mark.integration

MODEL_EXTRA = ["xgboost", "sklearn", "shap"]
GRU_EXTRA = ["torch"]
REPORT_EXTRA = ["matplotlib"]

# Package, and the modules its image does not install (python/Dockerfile).
SERVICES = {
    "wardwatch_scorer": GRU_EXTRA + REPORT_EXTRA,
    "wardwatch_fhir": MODEL_EXTRA + GRU_EXTRA + REPORT_EXTRA,
    "wardwatch_sim": MODEL_EXTRA + GRU_EXTRA + REPORT_EXTRA,
}

PROGRAM = """
import importlib, importlib.abc, pkgutil, sys

blocked = set(sys.argv[2].split(","))

class Missing(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in blocked:
            raise ModuleNotFoundError(f"No module named {name!r} (not in this image)")
        return None

sys.meta_path.insert(0, Missing())
package = importlib.import_module(sys.argv[1])
for module in pkgutil.walk_packages(package.__path__, package.__name__ + "."):
    importlib.import_module(module.name)
"""


@pytest.mark.parametrize(("package", "missing"), SERVICES.items(), ids=list(SERVICES))
def test_service_imports_without_the_extras_its_image_leaves_out(
    package: str, missing: list[str]
) -> None:
    result = subprocess.run(
        [sys.executable, "-c", PROGRAM, package, ",".join(missing)],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
