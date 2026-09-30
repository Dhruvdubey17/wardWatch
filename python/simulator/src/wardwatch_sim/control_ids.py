"""MSH-10 control IDs and visit numbers: a prefix and a zero-padded counter.

The ingest engine remembers recent control IDs and rejects repeats, so every
run puts a tag from its start time in the prefix. Without it a second run
against the same ingest process would be rejected message by message.
"""

import string
from dataclasses import dataclass, field
from datetime import datetime

_BASE36 = string.digits + string.ascii_uppercase


def run_tag(started: datetime) -> str:
    """Base-36 seconds since the epoch: six characters until December 2038, then seven."""
    # ponytail: two runs started in the same second share a tag.
    value = int(started.timestamp())
    digits = ""
    while value:
        value, remainder = divmod(value, 36)
        digits = _BASE36[remainder] + digits
    return digits or "0"


@dataclass
class ControlIdSequence:
    prefix: str
    width: int = 9
    _next: int = field(default=1, init=False)

    def next(self) -> str:
        value = f"{self.prefix}{self._next:0{self.width}d}"
        self._next += 1
        return value
