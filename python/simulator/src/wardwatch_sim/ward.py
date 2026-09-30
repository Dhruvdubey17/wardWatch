"""Replays PhysioNet stays through a ward of beds on an accelerated clock.

Each bed admits a stay, sends one ORU^R01 per ICU hour that has at least one
measurement, discharges, and then admits the next stay from a shared queue.
Clinical times (admission, OBR-7, OBX-14) run on the simulated clock; MSH-7 is
set when the message is actually sent, so latency measured from MSH-7 is real.
"""

import heapq
import math
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Literal

import pandas as pd
from wardwatch_ml.contracts import loinc_table

from wardwatch_sim.clock import Clock
from wardwatch_sim.control_ids import ControlIdSequence
from wardwatch_sim.hl7 import (
    Gender,
    Measurement,
    MessageHeader,
    PatientIdentity,
    Visit,
    build_adt_a01,
    build_adt_a03,
    build_oru_r01,
)
from wardwatch_sim.identities import IdentityAssigner

HOUR = timedelta(hours=1)
# A stay's last row is its last full ICU hour; discharge follows half an hour
# later and the bed stays empty for an hour of cleaning before the next admit.
DISCHARGE_DELAY = timedelta(minutes=30)
TURNOVER = timedelta(hours=1)
DEFAULT_SECONDS_PER_HOUR = 2.0

MessageKind = Literal["admit", "result", "discharge"]


@dataclass(frozen=True)
class HourRow:
    hour: int
    values: dict[str, float]


@dataclass(frozen=True)
class Stay:
    site: str
    patient_id: str
    age: float | None
    gender: Gender | None
    rows: tuple[HourRow, ...]

    @property
    def key(self) -> str:
        return f"{self.site}/{self.patient_id}"


def stay_from_frame(frame: pd.DataFrame) -> Stay:
    """Build a Stay from one read_stay() frame, keeping only the LOINC-mapped variables."""
    variables = [entry.physionet_variable for entry in loinc_table()]
    rows = []
    for record in frame.to_dict("records"):
        values = {
            name: float(record[name]) for name in variables if not math.isnan(float(record[name]))
        }
        rows.append(HourRow(hour=int(record["hour"]), values=values))
    first = frame.iloc[0]
    age = None if math.isnan(first["Age"]) else float(first["Age"])
    gender: Gender | None = None
    if not math.isnan(first["Gender"]):
        gender = "M" if int(first["Gender"]) == 1 else "F"
    return Stay(
        site=str(first["site"]),
        patient_id=str(first["patient_id"]),
        age=age,
        gender=gender,
        rows=tuple(rows),
    )


@dataclass(frozen=True)
class ScheduledMessage:
    due_seconds: float
    simulated_at: datetime
    bed: str
    kind: MessageKind
    stay_key: str
    mrn: str
    control_id: str
    render: Callable[[datetime], str] = field(compare=False, repr=False)


@dataclass
class Ward:
    stays: Iterable[Stay]
    beds: int
    assigner: IdentityAssigner
    simulated_start: datetime
    seconds_per_hour: float = DEFAULT_SECONDS_PER_HOUR
    control_ids: ControlIdSequence = field(default_factory=lambda: ControlIdSequence("SIM"))
    encounter_ids: ControlIdSequence = field(
        default_factory=lambda: ControlIdSequence("ENC", width=6)
    )

    def __post_init__(self) -> None:
        if self.beds < 1:
            raise ValueError("a ward needs at least one bed")
        if self.seconds_per_hour <= 0:
            raise ValueError("seconds_per_hour must be positive")
        self._queue = iter(self.stays)

    def _due(self, moment: datetime) -> float:
        return (moment - self.simulated_start) / HOUR * self.seconds_per_hour

    def _bed_messages(self, bed_index: int) -> Iterator[ScheduledMessage]:
        bed = f"{bed_index + 1:02d}"
        clock = self.simulated_start
        for stay in self._queue:
            identity = self.assigner.assign(stay.key, stay.gender, stay.age, clock.date())
            yield from self._stay_messages(stay, identity, bed, clock)
            self.assigner.release(identity.mrn)
            last_hour = stay.rows[-1].hour if stay.rows else 0
            clock = clock + last_hour * HOUR + DISCHARGE_DELAY + TURNOVER

    def _stay_messages(
        self, stay: Stay, identity: PatientIdentity, bed: str, admitted_at: datetime
    ) -> Iterator[ScheduledMessage]:
        last_hour = stay.rows[-1].hour if stay.rows else 0
        visit = Visit(
            visit_number=self.encounter_ids.next(),
            room=bed,
            bed="A",
            admitted_at=admitted_at,
            discharged_at=admitted_at + last_hour * HOUR + DISCHARGE_DELAY,
        )

        def message(
            kind: MessageKind, moment: datetime, build: Callable[[MessageHeader], str]
        ) -> ScheduledMessage:
            control_id = self.control_ids.next()
            return ScheduledMessage(
                due_seconds=self._due(moment),
                simulated_at=moment,
                bed=bed,
                kind=kind,
                stay_key=stay.key,
                mrn=identity.mrn,
                control_id=control_id,
                render=lambda sent_at: build(MessageHeader(control_id, sent_at)),
            )

        yield message("admit", admitted_at, lambda header: build_adt_a01(header, identity, visit))
        for row in stay.rows:
            if not row.values:
                continue
            # ICULOS counts hours from ICU admission, so row k is observed at
            # admission + k hours; the scorer recovers k from that difference.
            observed_at = admitted_at + row.hour * HOUR
            measurements = [
                Measurement(entry.code, row.values[entry.physionet_variable], observed_at)
                for entry in loinc_table()
                if entry.physionet_variable in row.values
            ]
            order_id = f"{visit.visit_number}-H{row.hour:04d}"
            yield message(
                "result",
                observed_at,
                _result_builder(identity, visit, order_id, observed_at, measurements),
            )
        assert visit.discharged_at is not None
        yield message(
            "discharge", visit.discharged_at, lambda header: build_adt_a03(header, identity, visit)
        )

    def schedule(self) -> Iterator[ScheduledMessage]:
        """Every bed's messages merged in due order; ties go to the lower bed number."""
        heap: list[tuple[float, int, int, ScheduledMessage, Iterator[ScheduledMessage]]] = []
        sequence = 0
        for bed_index in range(self.beds):
            messages = self._bed_messages(bed_index)
            first = next(messages, None)
            if first is not None:
                heapq.heappush(heap, (first.due_seconds, bed_index, sequence, first, messages))
                sequence += 1
        while heap:
            _, bed_index, _, current, messages = heapq.heappop(heap)
            yield current
            following = next(messages, None)
            if following is not None:
                heapq.heappush(
                    heap, (following.due_seconds, bed_index, sequence, following, messages)
                )
                sequence += 1


def _result_builder(
    identity: PatientIdentity,
    visit: Visit,
    order_id: str,
    observed_at: datetime,
    measurements: list[Measurement],
) -> Callable[[MessageHeader], str]:
    def build(header: MessageHeader) -> str:
        return build_oru_r01(
            header,
            identity,
            visit,
            order_id=order_id,
            observed_at=observed_at,
            measurements=measurements,
        )

    return build


def replay(
    messages: Iterable[ScheduledMessage],
    clock: Clock,
    send: Callable[[ScheduledMessage, str], None],
) -> int:
    """Send each message when it falls due; returns how many were sent."""
    started = clock.monotonic()
    sent = 0
    for scheduled in messages:
        clock.sleep_until(started + scheduled.due_seconds)
        send(scheduled, scheduled.render(clock.wall()))
        sent += 1
    return sent
