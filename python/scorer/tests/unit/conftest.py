from pathlib import Path

import pytest
from scorer_support import train_bundle
from wardwatch_ml.bundle import ServingBundle


@pytest.fixture(scope="session")
def bundle(tmp_path_factory: pytest.TempPathFactory) -> ServingBundle:
    directory: Path = tmp_path_factory.mktemp("bundle")
    return train_bundle(directory)
