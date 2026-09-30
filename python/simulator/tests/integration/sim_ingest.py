"""Starts the real ingest binary for simulator integration tests."""

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[4]
DEFAULT_BINARY = REPO / "ingest" / "build" / "debug" / "apps" / "wardwatch-ingest"


@dataclass
class RunningIngest:
    port: int
    metrics_port: int
    sink_dir: Path
    process: subprocess.Popen[bytes]

    def stop(self) -> int:
        self.process.terminate()
        code = self.process.wait(timeout=30)
        self.close()
        return code

    def close(self) -> None:
        if self.process.poll() is None:
            self.process.kill()
            self.process.wait()
        if self.process.stdout is not None:
            self.process.stdout.close()

    def records(self, topic: str) -> list[dict[str, Any]]:
        path = self.sink_dir / f"{topic}.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text().splitlines()]


def start_ingest(sink_dir: Path, extra_args: list[str]) -> RunningIngest:
    binary = Path(os.environ.get("WARDWATCH_INGEST_BINARY", DEFAULT_BINARY))
    if not binary.is_file():
        raise FileNotFoundError(f"{binary} is missing; run `make ingest-build` first")
    process = subprocess.Popen(
        [
            str(binary),
            "--port",
            "0",
            "--metrics-port",
            "0",
            "--sink",
            "file",
            "--file-sink-dir",
            str(sink_dir),
            *extra_args,
        ],
        stdout=subprocess.PIPE,
    )
    assert process.stdout is not None
    started = json.loads(process.stdout.readline())
    return RunningIngest(started["port"], started["metrics_port"], sink_dir, process)
