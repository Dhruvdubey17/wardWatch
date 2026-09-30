from pathlib import Path

import pytest
from wardwatch_sim.cli import (
    Endpoint,
    LoadConfig,
    ReplayConfig,
    SyntheaConfig,
    _percentile,
    _sent_control_id,
    parse_config,
)

pytestmark = pytest.mark.unit


def test_replay_defaults() -> None:
    config = parse_config(["replay"])
    assert isinstance(config, ReplayConfig)
    assert config.endpoint == Endpoint("127.0.0.1", 2575)
    assert config.sites == ("A", "B")
    assert config.seconds_per_hour == 2.0
    assert config.fault_rates == {}
    assert config.limit is None


def test_replay_options() -> None:
    config = parse_config(
        [
            "replay",
            "--host",
            "ingest",
            "--port",
            "9000",
            "--physionet-dir",
            "/data/p",
            "--synthea-dir",
            "/data/s",
            "--sites",
            "B",
            "--beds",
            "4",
            "--seconds-per-hour",
            "0.5",
            "--seed",
            "7",
            "--fault",
            "missing_pid=0.1",
            "--fault",
            "lf_segment_terminators=0.05",
            "--max-frame-bytes",
            "4096",
            "--limit",
            "3",
            "--report",
            "out.json",
        ]
    )
    assert isinstance(config, ReplayConfig)
    assert config.endpoint == Endpoint("ingest", 9000)
    assert config.physionet_dir == Path("/data/p")
    assert config.sites == ("B",)
    assert config.beds == 4
    assert config.seconds_per_hour == 0.5
    assert config.fault_rates == {"missing_pid": 0.1, "lf_segment_terminators": 0.05}
    assert config.max_frame_bytes == 4096
    assert config.limit == 3
    assert config.report == Path("out.json")


@pytest.mark.parametrize(
    "argv",
    [
        ["replay", "--sites", "C"],
        ["replay", "--sites", ""],
        ["replay", "--beds", "0"],
        ["replay", "--seconds-per-hour", "0"],
        ["replay", "--fault", "missing_pid"],
        ["load"],
        ["load", "--rate", "0"],
        ["load", "--rate", "10", "--connections", "0"],
        ["unknown"],
        [],
    ],
)
def test_invalid_arguments_exit_with_usage_error(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as raised:
        parse_config(argv)
    assert raised.value.code == 2


def test_synthea_config() -> None:
    config = parse_config(["synthea", "--synthea-dir", "x", "--port", "1"])
    assert config == SyntheaConfig(Endpoint("127.0.0.1", 1), Path("x"), None)


def test_load_config() -> None:
    config = parse_config(["load", "--rate", "500", "--duration", "5", "--connections", "8"])
    assert config == LoadConfig(Endpoint("127.0.0.1", 2575), 500.0, 5.0, 8, None)


def test_sent_control_id_reads_msh10() -> None:
    assert _sent_control_id("MSH|^~\\&|A|B|C|D|20240101||ORU^R01|CTRL9|P|2.5.1\rPID|1\r") == "CTRL9"
    assert _sent_control_id("MSH|^~\\&|A|B|C|D|20240101||ORU^R01|LF1|P\nPID|1") == "LF1"
    assert _sent_control_id("garbage") == ""


def test_percentile_picks_nearest_rank() -> None:
    values = [float(v) for v in range(1, 101)]
    assert _percentile(values, 0.5) == 51.0
    assert _percentile(values, 0.99) == 99.0
    assert _percentile(values, 1.0) == 100.0
    assert _percentile([3.0], 0.95) == 3.0
