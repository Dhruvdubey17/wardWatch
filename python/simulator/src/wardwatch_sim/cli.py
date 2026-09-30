"""wardwatch-sim: replay PhysioNet stays, send Synthea vital signs, or generate load."""

import argparse
import json
import logging
import os
import platform
import subprocess
import sys
import threading
import time
from collections import Counter
from collections.abc import Iterator, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from wardwatch_ml.contracts import contracts_dir
from wardwatch_ml.data import SITE_DIRECTORIES, iter_stays

from wardwatch_sim.clock import SystemClock
from wardwatch_sim.control_ids import ControlIdSequence, run_tag
from wardwatch_sim.faults import FaultInjector, parse_rates
from wardwatch_sim.identities import IdentityAssigner
from wardwatch_sim.mllp import DeliveryError, MllpClient, TcpTransport
from wardwatch_sim.synthea import load_patients
from wardwatch_sim.synthea_oru import convert_patient
from wardwatch_sim.ward import (
    DEFAULT_SECONDS_PER_HOUR,
    ScheduledMessage,
    Stay,
    Ward,
    replay,
    stay_from_frame,
)

log = logging.getLogger("wardwatch_sim")


@dataclass(frozen=True)
class Endpoint:
    host: str
    port: int


@dataclass(frozen=True)
class ReplayConfig:
    endpoint: Endpoint
    physionet_dir: Path
    synthea_dir: Path
    sites: tuple[str, ...]
    beds: int
    seconds_per_hour: float
    seed: int
    fault_rates: dict[str, float]
    max_frame_bytes: int
    limit: int | None
    report: Path | None


@dataclass(frozen=True)
class SyntheaConfig:
    endpoint: Endpoint
    synthea_dir: Path
    report: Path | None


@dataclass(frozen=True)
class LoadConfig:
    endpoint: Endpoint
    rate: float
    duration: float
    connections: int
    output: Path | None


@dataclass
class DeliveryStats:
    sent: int = 0
    acks: Counter[str] = field(default_factory=Counter)
    faults: Counter[str] = field(default_factory=Counter)
    retries: int = 0
    undelivered: int = 0

    def summary(self) -> dict[str, object]:
        return {
            "sent": self.sent,
            "acks": dict(self.acks),
            "faults": dict(self.faults),
            "retries": self.retries,
            "undelivered": self.undelivered,
        }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="wardwatch-sim", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    def endpoint(command: argparse.ArgumentParser) -> None:
        command.add_argument("--host", default="127.0.0.1")
        command.add_argument("--port", type=int, default=2575)

    replay_command = commands.add_parser("replay", help="replay PhysioNet stays through a ward")
    endpoint(replay_command)
    replay_command.add_argument("--physionet-dir", type=Path, default=Path("data/physionet"))
    replay_command.add_argument("--synthea-dir", type=Path, default=Path("data/synthea/fhir"))
    replay_command.add_argument("--sites", default="A,B", help="comma-separated: A, B or A,B")
    replay_command.add_argument("--beds", type=int, default=12)
    replay_command.add_argument("--seconds-per-hour", type=float, default=DEFAULT_SECONDS_PER_HOUR)
    replay_command.add_argument("--seed", type=int, default=2019)
    replay_command.add_argument(
        "--fault", action="append", default=[], metavar="NAME=RATE", help="repeatable"
    )
    replay_command.add_argument("--max-frame-bytes", type=int, default=1 << 20)
    replay_command.add_argument("--limit", type=int, default=None, help="stays per site")
    replay_command.add_argument("--report", type=Path, default=None)

    synthea_command = commands.add_parser("synthea", help="send Synthea vital signs")
    endpoint(synthea_command)
    synthea_command.add_argument("--synthea-dir", type=Path, default=Path("data/synthea/fhir"))
    synthea_command.add_argument("--report", type=Path, default=None)

    load_command = commands.add_parser("load", help="measure ingest throughput")
    endpoint(load_command)
    load_command.add_argument("--rate", type=float, required=True, help="messages per second")
    load_command.add_argument("--duration", type=float, default=30.0, help="seconds")
    load_command.add_argument("--connections", type=int, default=4)
    load_command.add_argument("--output", type=Path, default=None)
    return parser


def parse_config(argv: Sequence[str]) -> ReplayConfig | SyntheaConfig | LoadConfig:
    """Parse and check the command line into one of the three configurations."""
    parser = build_parser()
    args = parser.parse_args(argv)
    endpoint = Endpoint(args.host, args.port)
    if args.command == "replay":
        sites = tuple(site.strip() for site in args.sites.split(",") if site.strip())
        unknown = [site for site in sites if site not in SITE_DIRECTORIES]
        if not sites or unknown:
            parser.error(f"--sites must name A and/or B, got {args.sites!r}")
        if args.beds < 1 or args.seconds_per_hour <= 0:
            parser.error("--beds must be at least 1 and --seconds-per-hour positive")
        try:
            rates = parse_rates(args.fault)
        except ValueError as error:
            parser.error(str(error))
        return ReplayConfig(
            endpoint=endpoint,
            physionet_dir=args.physionet_dir,
            synthea_dir=args.synthea_dir,
            sites=sites,
            beds=args.beds,
            seconds_per_hour=args.seconds_per_hour,
            seed=args.seed,
            fault_rates=rates,
            max_frame_bytes=args.max_frame_bytes,
            limit=args.limit,
            report=args.report,
        )
    if args.command == "synthea":
        return SyntheaConfig(endpoint=endpoint, synthea_dir=args.synthea_dir, report=args.report)
    if args.rate <= 0 or args.duration <= 0 or args.connections < 1:
        parser.error("--rate and --duration must be positive and --connections at least 1")
    return LoadConfig(
        endpoint=endpoint,
        rate=args.rate,
        duration=args.duration,
        connections=args.connections,
        output=args.output,
    )


def _client(endpoint: Endpoint) -> MllpClient:
    return MllpClient(connect=lambda: TcpTransport(endpoint.host, endpoint.port))


def _write_report(path: Path | None, report: dict[str, object]) -> None:
    text = json.dumps(report, indent=2, sort_keys=True)
    if path is not None:
        path.write_text(text + "\n")
    print(text)


def _stays(config: ReplayConfig) -> Iterator[Stay]:
    for site in config.sites:
        directory = config.physionet_dir / SITE_DIRECTORIES[site]
        for frame in iter_stays(directory, site, config.limit):
            yield stay_from_frame(frame)


def run_replay(config: ReplayConfig) -> int:
    identities = [patient.identity for patient in load_patients(config.synthea_dir)]
    if not identities:
        log.error("no Synthea patients in %s; run make data first", config.synthea_dir)
        return 1
    started = datetime.now(UTC)
    tag = run_tag(started)
    ward = Ward(
        stays=_stays(config),
        beds=config.beds,
        assigner=IdentityAssigner(identities, config.seed),
        control_ids=ControlIdSequence(f"SIM{tag}"),
        encounter_ids=ControlIdSequence(f"ENC{tag}", width=6),
        simulated_start=started.replace(minute=0, second=0, microsecond=0),
        seconds_per_hour=config.seconds_per_hour,
    )
    injector = FaultInjector(config.fault_rates, config.seed, config.max_frame_bytes)
    client = _client(config.endpoint)
    stats = DeliveryStats()

    def send(message: ScheduledMessage, text: str) -> None:
        damaged, fault = injector.apply(text, message.control_id, message.kind)
        if fault is not None:
            stats.faults[fault.name] += 1
        stats.sent += 1
        try:
            # A damaged message is supposed to be rejected; retrying it would
            # only dead-letter the same bytes again.
            result = client.send(damaged, _sent_control_id(damaged), retry_on_ae=fault is None)
        except DeliveryError as error:
            stats.undelivered += 1
            log.warning("undelivered %s: %s", message.control_id, error)
            return
        stats.acks[result.ack.code] += 1
        stats.retries += result.attempts - 1

    log.info("replaying %s with %d beds", ",".join(config.sites), config.beds)
    replay(ward.schedule(), SystemClock(), send)
    client.close()
    _write_report(config.report, {"command": "replay", **stats.summary()})
    return 0


def _sent_control_id(text: str) -> str:
    # MSH-10 as actually sent, which differs from the scheduled one when the
    # duplicate fault reused an earlier ID. The ACK names that ID.
    header = text.replace("\n", "\r").split("\r", 1)[0]
    fields = header.split("|")
    return fields[9] if len(fields) > 9 else ""


def run_synthea(config: SyntheaConfig) -> int:
    client = _client(config.endpoint)
    stats = DeliveryStats()
    control_ids = ControlIdSequence(f"SYN{run_tag(datetime.now(UTC))}")
    for patient in load_patients(config.synthea_dir):
        for text in convert_patient(patient, control_ids).messages:
            stats.sent += 1
            try:
                result = client.send(text, _sent_control_id(text))
            except DeliveryError as error:
                stats.undelivered += 1
                log.warning("undelivered: %s", error)
                continue
            stats.acks[result.ack.code] += 1
    client.close()
    _write_report(config.report, {"command": "synthea", **stats.summary()})
    return 0


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(fraction * (len(ordered) - 1))))
    return ordered[index]


def host_description() -> dict[str, str]:
    """CPU model and platform, so a throughput figure is never quoted without its hardware."""
    model = platform.processor() or "unknown"
    if sys.platform == "darwin":
        result = subprocess.run(
            ["sysctl", "-n", "machdep.cpu.brand_string"],
            capture_output=True,
            text=True,
            check=False,
        )
        model = result.stdout.strip() or model
    else:
        cpuinfo = Path("/proc/cpuinfo")
        if cpuinfo.exists():
            for line in cpuinfo.read_text().splitlines():
                if line.startswith("model name"):
                    model = line.split(":", 1)[1].strip()
                    break
    return {"cpu_model": model, "platform": platform.platform(), "cpu_count": str(os.cpu_count())}


def run_load(config: LoadConfig) -> int:
    """Send distinct ORU^R01 messages at a target rate and report what was acknowledged."""
    template = (contracts_dir() / "hl7" / "oru_r01.hl7").read_bytes().decode("ascii")
    interval = config.connections / config.rate
    latencies: list[float] = []
    codes: Counter[str] = Counter()
    lock = threading.Lock()
    started = time.monotonic()
    deadline = started + config.duration

    def worker(worker_index: int) -> None:
        client = _client(config.endpoint)
        next_send = time.monotonic()
        sequence = 0
        while time.monotonic() < deadline:
            control_id = f"LOAD{worker_index:03d}{sequence:09d}"
            text = template.replace("|SIM000007|", f"|{control_id}|").replace(
                "MRN0001234", f"MRNLOAD{worker_index:03d}{sequence % 100:02d}"
            )
            sequence += 1
            try:
                result = client.send(text, control_id)
            except DeliveryError:
                with lock:
                    codes["undelivered"] += 1
                continue
            with lock:
                codes[result.ack.code] += 1
                latencies.append(result.latency_seconds)
            next_send += interval
            time.sleep(max(0.0, next_send - time.monotonic()))
        client.close()

    threads = [
        threading.Thread(target=worker, args=(index,)) for index in range(config.connections)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    elapsed = time.monotonic() - started
    acknowledged = sum(count for code, count in codes.items() if code != "undelivered")
    report: dict[str, object] = {
        "command": "load",
        "config": {
            key: str(value) if isinstance(value, Path) else value
            for key, value in asdict(config).items()
        },
        "host": host_description(),
        "elapsed_seconds": round(elapsed, 3),
        "acknowledged": acknowledged,
        "acks": dict(codes),
        "throughput_per_second": round(acknowledged / elapsed, 1),
        "ack_latency_ms": {
            name: round(_percentile(latencies, fraction) * 1000, 3)
            for name, fraction in (("p50", 0.5), ("p95", 0.95), ("p99", 0.99), ("max", 1.0))
        }
        if latencies
        else {},
    }
    _write_report(config.output, report)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    config = parse_config(sys.argv[1:] if argv is None else argv)
    if isinstance(config, ReplayConfig):
        return run_replay(config)
    if isinstance(config, SyntheaConfig):
        return run_synthea(config)
    return run_load(config)


if __name__ == "__main__":
    sys.exit(main())
