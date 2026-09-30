"""The alert workflow: which transitions exist and where they lead."""

from typing import Literal

Status = Literal["open", "acknowledged", "escalated", "resolved"]
Transition = Literal["acknowledge", "escalate", "resolve"]

STATUSES: tuple[Status, ...] = ("open", "acknowledged", "escalated", "resolved")
TRANSITIONS: dict[tuple[Status, Transition], Status] = {
    ("open", "acknowledge"): "acknowledged",
    ("open", "escalate"): "escalated",
    ("acknowledged", "escalate"): "escalated",
    ("acknowledged", "resolve"): "resolved",
    ("escalated", "resolve"): "resolved",
}

# Reasons a clinician can pick when escalating. The system uses its own
# reason, unacknowledged_timeout, which is not offered to people.
CLINICIAN_ESCALATION_REASONS = (
    "clinical_deterioration",
    "senior_review_needed",
    "sepsis_pathway_started",
    "critical_care_outreach",
    "other",
)
SYSTEM_ESCALATION_REASON = "unacknowledged_timeout"
SYSTEM_ACTOR = "system"


class InvalidTransitionError(ValueError):
    def __init__(self, current: str, transition: str) -> None:
        super().__init__(f"cannot {transition} an alert that is {current}")
        self.current = current
        self.transition = transition


def next_status(current: Status, transition: Transition) -> Status:
    """The status after `transition`, or InvalidTransitionError."""
    try:
        return TRANSITIONS[(current, transition)]
    except KeyError:
        raise InvalidTransitionError(current, transition) from None


def etag(version: int) -> str:
    return f'"{version}"'


def parse_etag(header: str) -> int | None:
    """The version from an If-Match value such as "3" or W/"3"; None if unreadable."""
    value = header.strip().removeprefix("W/")
    if len(value) >= 2 and value[0] == value[-1] == '"' and value[1:-1].isdigit():
        return int(value[1:-1])
    return None
