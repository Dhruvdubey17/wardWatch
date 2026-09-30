"""Injects the defects listed in contracts/fault_catalog.json into outgoing messages.

Each defect is a pure function of the message text. The injector picks at
most one defect per message with a seeded random draw, so a run with the same
seed and message sequence damages the same messages in the same way.
"""

import json
import random
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Literal

from wardwatch_ml.contracts import contracts_dir

MessageKind = Literal["admit", "result", "discharge"]


@dataclass(frozen=True)
class Fault:
    name: str
    description: str
    outcome: Literal["error", "warning"]
    code: str
    ack_code: Literal["AA", "AE", "AR"]
    stage: Literal["frame", "parse", "validate"]


def load_fault_catalog() -> tuple[Fault, ...]:
    document = json.loads((contracts_dir() / "fault_catalog.json").read_text())
    return tuple(Fault(**entry) for entry in document["faults"])


def _segments(text: str) -> list[str]:
    return [segment for segment in text.split("\r") if segment]


def _join(segments: list[str]) -> str:
    return "\r".join(segments) + "\r"


def truncate(text: str) -> str:
    """Keep MSH and the first half of the next segment."""
    segments = _segments(text)
    following = segments[1]
    return segments[0] + "\r" + following[: max(4, len(following) // 2)]


def drop_pid(text: str) -> str:
    return _join([segment for segment in _segments(text) if not segment.startswith("PID|")])


def repeat_component_separator(text: str) -> str:
    """MSH-2 becomes ^^\\& so the encoding characters are no longer distinct."""
    return text.replace("MSH|^~\\&|", "MSH|^^\\&|", 1)


def lf_terminators(text: str) -> str:
    return text.replace("\r", "\n")


def non_numeric_nm(text: str) -> str:
    segments = _segments(text)
    for index, segment in enumerate(segments):
        fields = segment.split("|")
        if fields[0] == "OBX" and fields[2] == "NM":
            fields[5] = "high"
            segments[index] = "|".join(fields)
            return _join(segments)
    raise ValueError("message has no NM observation")


def month_thirteen(text: str) -> str:
    """MSH-7 gets month 13, a date that does not exist."""
    segments = _segments(text)
    fields = segments[0].split("|")
    # fields[6] is MSH-7 because MSH-1 is the separator between fields[0] and [1].
    fields[6] = fields[6][:4] + "13" + fields[6][6:]
    segments[0] = "|".join(fields)
    return _join(segments)


def trailing_separators(text: str) -> str:
    return _join([segment + "||" for segment in _segments(text)])


def non_ascii_family_name(text: str) -> str:
    """Add a UTF-8 letter to the family name and drop any MSH-18 character set."""
    segments = _segments(text)
    header = segments[0].split("|")
    # header[17] is MSH-18; the builder sets it when a name is already non-ASCII.
    if len(header) > 17:
        segments[0] = "|".join(header[:17]).rstrip("|")
    for index, segment in enumerate(segments):
        fields = segment.split("|")
        if fields[0] == "PID":
            family, _, rest = fields[5].partition("^")
            fields[5] = family + "é" + ("^" + rest if rest else "")
            segments[index] = "|".join(fields)
            return _join(segments)
    raise ValueError("message has no PID")


def replace_control_id(text: str, control_id: str) -> str:
    segments = _segments(text)
    fields = segments[0].split("|")
    fields[9] = control_id
    segments[0] = "|".join(fields)
    return _join(segments)


def pad_past_limit(text: str, max_frame_bytes: int) -> str:
    """Append an NTE whose comment pushes the message over the frame limit."""
    padding = max(1, max_frame_bytes - len(text.encode()) + 64)
    return text + f"NTE|1||{'x' * padding}\r"


_ONLY_FOR_RESULTS = {"non_numeric_nm"}


@dataclass
class FaultInjector:
    """Damages a fraction of messages; rates are per-message probabilities by fault name."""

    rates: Mapping[str, float]
    seed: int
    max_frame_bytes: int = 1 << 20
    catalog: tuple[Fault, ...] = field(default_factory=load_fault_catalog)

    def __post_init__(self) -> None:
        known = {fault.name for fault in self.catalog}
        unknown = set(self.rates) - known
        if unknown:
            raise ValueError(f"unknown faults: {', '.join(sorted(unknown))}")
        if any(rate < 0 for rate in self.rates.values()) or sum(self.rates.values()) > 1:
            raise ValueError("fault rates must be non-negative and sum to at most 1")
        self._random = random.Random(self.seed)
        # The last control ID the ingest engine will accept. Reusing one that
        # was rejected would not be a duplicate, since rejections are not
        # remembered.
        self._accepted_control_id: str | None = None
        self._transforms: dict[str, Callable[[str], str]] = {
            "truncated_message": truncate,
            "missing_pid": drop_pid,
            "wrong_encoding_characters": repeat_component_separator,
            "lf_segment_terminators": lf_terminators,
            "non_numeric_nm": non_numeric_nm,
            "invalid_timestamp": month_thirteen,
            "trailing_separators": trailing_separators,
            "non_ascii_bytes": non_ascii_family_name,
            "oversized_frame": lambda text: pad_past_limit(text, self.max_frame_bytes),
        }
        missing = known - set(self._transforms) - {"duplicate_control_id"}
        if missing:
            raise ValueError(f"no transform for catalogued faults: {', '.join(sorted(missing))}")

    def apply(self, text: str, control_id: str, kind: MessageKind) -> tuple[str, Fault | None]:
        """Return the text to send and the fault applied to it, if any."""
        draw = self._random.random()
        chosen: Fault | None = None
        cumulative = 0.0
        for fault in self.catalog:
            cumulative += self.rates.get(fault.name, 0.0)
            if draw < cumulative:
                chosen = fault
                break
        previous = self._accepted_control_id
        if chosen is not None and not self._applicable(chosen, kind, previous):
            chosen = None
        if chosen is None or chosen.outcome == "warning":
            self._accepted_control_id = control_id
        if chosen is None:
            return text, None
        if chosen.name == "duplicate_control_id":
            assert previous is not None
            return replace_control_id(text, previous), chosen
        return self._transforms[chosen.name](text), chosen

    @staticmethod
    def _applicable(fault: Fault, kind: MessageKind, previous: str | None) -> bool:
        if fault.name in _ONLY_FOR_RESULTS:
            return kind == "result"
        if fault.name == "duplicate_control_id":
            return previous is not None
        return True


def parse_rates(specs: list[str]) -> dict[str, float]:
    """Parse name=rate pairs from the command line."""
    rates: dict[str, float] = {}
    for spec in specs:
        name, separator, value = spec.partition("=")
        if not separator:
            raise ValueError(f"'{spec}' is not name=rate")
        rates[name] = float(value)
    return rates
