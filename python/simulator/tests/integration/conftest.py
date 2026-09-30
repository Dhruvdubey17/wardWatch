from collections.abc import Iterator
from pathlib import Path

import pytest
from sim_ingest import RunningIngest, start_ingest


@pytest.fixture
def ingest(tmp_path: Path, request: pytest.FixtureRequest) -> Iterator[RunningIngest]:
    """Start the real ingest binary on free ports with the file sink."""
    running = start_ingest(tmp_path, getattr(request, "param", []))
    yield running
    running.close()
