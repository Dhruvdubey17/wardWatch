"""MSH-10 control IDs: a prefix and a zero-padded counter, unique per run."""

from dataclasses import dataclass, field


@dataclass
class ControlIdSequence:
    prefix: str
    width: int = 9
    _next: int = field(default=1, init=False)

    def next(self) -> str:
        value = f"{self.prefix}{self._next:0{self.width}d}"
        self._next += 1
        return value
