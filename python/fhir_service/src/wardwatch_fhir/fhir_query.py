"""Parsing of FHIR search parameters (FHIR R4 section 3.1.1).

A date value names an interval at its own precision, so "2024-03-15" is the
whole day. Prefixes compare an Observation's effective instant t with that
interval [start, end): eq is start <= t < end, ne is outside it, lt is t <
start, le is t < end, gt is t >= end and ge is t >= start.
"""

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from typing import Literal, cast

Prefix = Literal["eq", "ne", "lt", "le", "gt", "ge"]
PREFIXES: tuple[Prefix, ...] = ("eq", "ne", "lt", "le", "gt", "ge")
DEFAULT_COUNT = 50
MAX_COUNT = 1000

_DATE = re.compile(
    r"^(?P<year>\d{4})(?:-(?P<month>\d{2})(?:-(?P<day>\d{2})"
    r"(?:T(?P<hour>\d{2}):(?P<minute>\d{2})(?::(?P<second>\d{2})(?:\.(?P<fraction>\d{1,6}))?)?"
    r"(?P<zone>Z|[+-]\d{2}:\d{2})?)?)?)?$"
)


class SearchParameterError(ValueError):
    """Becomes a 400 response with an OperationOutcome."""


@dataclass(frozen=True)
class DateFilter:
    prefix: Prefix
    start: datetime
    end: datetime


@dataclass(frozen=True)
class TokenFilter:
    system: str | None
    code: str


def _add_months(moment: datetime, months: int) -> datetime:
    year, month = divmod(moment.month - 1 + months, 12)
    return moment.replace(year=moment.year + year, month=month + 1)


def parse_date(value: str) -> DateFilter:
    prefix: Prefix = "eq"
    if value[:2] in PREFIXES:
        prefix = cast(Prefix, value[:2])
        value = value[2:]
    elif value[:2].isalpha():
        raise SearchParameterError(f"date prefix '{value[:2]}' is not supported")
    match = _DATE.match(value)
    if not match:
        raise SearchParameterError(f"'{value}' is not a FHIR date or dateTime")
    parts = match.groupdict()
    if parts["hour"] is not None and parts["zone"] is None:
        # A time without a zone is read in UTC, the server's zone.
        parts["zone"] = "Z"
    zone = UTC
    if parts["zone"] and parts["zone"] != "Z":
        sign = 1 if parts["zone"][0] == "+" else -1
        zone_hours, zone_minutes = int(parts["zone"][1:3]), int(parts["zone"][4:6])
        zone = (
            UTC if (zone_hours, zone_minutes) == (0, 0) else _zone(sign, zone_hours, zone_minutes)
        )
    try:
        start = datetime(
            int(parts["year"]),
            int(parts["month"] or 1),
            int(parts["day"] or 1),
            int(parts["hour"] or 0),
            int(parts["minute"] or 0),
            int(parts["second"] or 0),
            int((parts["fraction"] or "0").ljust(6, "0")),
            tzinfo=zone,
        )
    except ValueError as error:
        raise SearchParameterError(f"'{value}' is not a valid date: {error}") from error
    if parts["month"] is None:
        end = start.replace(year=start.year + 1)
    elif parts["day"] is None:
        end = _add_months(start, 1)
    elif parts["hour"] is None:
        end = start + timedelta(days=1)
    elif parts["second"] is None:
        end = start + timedelta(minutes=1)
    elif parts["fraction"] is None:
        end = start + timedelta(seconds=1)
    else:
        end = start + timedelta(microseconds=10 ** (6 - len(parts["fraction"])))
    return DateFilter(prefix=prefix, start=start.astimezone(UTC), end=end.astimezone(UTC))


def _zone(sign: int, hours: int, minutes: int) -> timezone:
    return timezone(sign * timedelta(hours=hours, minutes=minutes))


def parse_token(value: str) -> TokenFilter:
    """system|code, |code (no system), or code (any system)."""
    if not value:
        raise SearchParameterError("an empty token matches nothing")
    if "|" not in value:
        return TokenFilter(system=None, code=value)
    system, code = value.split("|", 1)
    if not code:
        raise SearchParameterError(f"token '{value}' has no code")
    return TokenFilter(system=system, code=code)


def parse_reference(value: str, resource_type: str) -> str:
    """Patient/123 or 123 to the id 123."""
    if "/" in value:
        kind, _, identifier = value.rpartition("/")
        if kind.rsplit("/", 1)[-1] != resource_type:
            raise SearchParameterError(f"'{value}' is not a {resource_type} reference")
        value = identifier
    if not value:
        raise SearchParameterError("reference has no id")
    return value


def parse_count(value: str | None) -> int:
    if value is None:
        return DEFAULT_COUNT
    if not value.isdigit() or not 1 <= int(value) <= MAX_COUNT:
        raise SearchParameterError(f"_count must be a whole number from 1 to {MAX_COUNT}")
    return int(value)


def parse_offset(value: str | None) -> int:
    if value is None:
        return 0
    if not value.isdigit():
        raise SearchParameterError("_offset must be a non-negative whole number")
    return int(value)


def parse_sort(value: str | None) -> Literal["date", "-date"]:
    if value is None or value == "-date":
        return "-date"
    if value == "date":
        return "date"
    raise SearchParameterError("_sort supports date and -date only")
