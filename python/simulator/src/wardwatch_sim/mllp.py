"""MLLP framing and a client that waits for ACKs, retries and reconnects."""

import socket
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

START_BLOCK = b"\x0b"
END_BLOCK = b"\x1c\r"


def frame(payload: bytes) -> bytes:
    return START_BLOCK + payload + END_BLOCK


class MllpDecoder:
    """Collects complete frames from a byte stream; bytes outside frames are dropped."""

    def __init__(self) -> None:
        self._buffer = bytearray()

    def feed(self, data: bytes) -> list[bytes]:
        self._buffer.extend(data)
        frames: list[bytes] = []
        while True:
            start = self._buffer.find(START_BLOCK)
            if start < 0:
                self._buffer.clear()
                return frames
            end = self._buffer.find(END_BLOCK, start + 1)
            if end < 0:
                del self._buffer[:start]
                return frames
            frames.append(bytes(self._buffer[start + 1 : end]))
            del self._buffer[: end + len(END_BLOCK)]


@dataclass(frozen=True)
class Ack:
    code: str
    control_id: str
    error_code: str | None = None


def parse_ack(payload: bytes) -> Ack:
    """Read MSA-1, MSA-2 and our error code from ERR-5 of an ACK."""
    segments: dict[str, list[str]] = {}
    for segment in payload.decode("utf-8", errors="replace").replace("\n", "\r").split("\r"):
        if segment:
            segments.setdefault(segment[:3], segment.split("|"))
    msa = segments.get("MSA")
    if msa is None or len(msa) < 3:
        raise ProtocolError(f"ACK has no MSA segment: {payload[:80]!r}")
    err = segments.get("ERR")
    error_code = err[5].split("^")[0] if err is not None and len(err) > 5 else None
    return Ack(code=msa[1], control_id=msa[2], error_code=error_code or None)


class ProtocolError(RuntimeError):
    pass


class DeliveryError(RuntimeError):
    pass


class Transport(Protocol):
    def send(self, data: bytes) -> None: ...

    def recv(self, max_bytes: int, timeout: float) -> bytes:
        """Return received bytes, b"" when the peer closed; raise TimeoutError."""
        ...

    def close(self) -> None: ...


class TcpTransport:
    def __init__(self, host: str, port: int, connect_timeout: float = 5.0) -> None:
        self._socket = socket.create_connection((host, port), timeout=connect_timeout)
        self._socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

    def send(self, data: bytes) -> None:
        self._socket.sendall(data)

    def recv(self, max_bytes: int, timeout: float) -> bytes:
        self._socket.settimeout(timeout)
        try:
            return self._socket.recv(max_bytes)
        except TimeoutError:
            raise
        except OSError as error:
            raise ConnectionError(str(error)) from error

    def close(self) -> None:
        self._socket.close()


@dataclass(frozen=True)
class SendResult:
    ack: Ack
    attempts: int
    latency_seconds: float


@dataclass
class MllpClient:
    """Sends one message at a time in original acknowledgment mode.

    A timeout or broken connection closes the socket, waits with exponential
    backoff, reconnects and resends; the ingest engine acknowledges an exact
    resend of an accepted message again without publishing it twice. AE is
    retried the same way when asked, AR never is.
    """

    connect: Callable[[], Transport]
    ack_timeout: float = 10.0
    max_retries: int = 3
    backoff_initial: float = 0.2
    backoff_max: float = 5.0
    sleep: Callable[[float], None] = time.sleep
    clock: Callable[[], float] = time.monotonic
    reconnects: int = field(default=0, init=False)
    _transport: Transport | None = field(default=None, init=False)
    _decoder: MllpDecoder = field(default_factory=MllpDecoder, init=False)

    def send(self, message: str, control_id: str, retry_on_ae: bool = True) -> SendResult:
        payload = frame(message.encode("utf-8"))
        started = self.clock()
        attempt = 0
        while True:
            attempt += 1
            try:
                ack = self._exchange(payload)
            except (TimeoutError, ConnectionError, OSError, ProtocolError) as error:
                self._drop_connection()
                if attempt > self.max_retries:
                    raise DeliveryError(
                        f"{control_id}: no ACK after {attempt} attempts: {error}"
                    ) from error
                self._back_off(attempt)
                continue
            if ack.control_id != control_id:
                # A late ACK for an earlier message; start over on a clean socket.
                self._drop_connection()
                if attempt > self.max_retries:
                    raise DeliveryError(f"{control_id}: ACK was for {ack.control_id}")
                self._back_off(attempt)
                continue
            if ack.code == "AE" and retry_on_ae and attempt <= self.max_retries:
                self._back_off(attempt)
                continue
            return SendResult(ack=ack, attempts=attempt, latency_seconds=self.clock() - started)

    def close(self) -> None:
        self._drop_connection()

    def _exchange(self, payload: bytes) -> Ack:
        if self._transport is None:
            self._transport = self.connect()
            self._decoder = MllpDecoder()
        self._transport.send(payload)
        deadline = self.clock() + self.ack_timeout
        while True:
            remaining = deadline - self.clock()
            if remaining <= 0:
                raise TimeoutError("ACK timeout")
            data = self._transport.recv(65536, remaining)
            if not data:
                raise ConnectionError("connection closed while waiting for an ACK")
            frames = self._decoder.feed(data)
            if frames:
                return parse_ack(frames[0])

    def _drop_connection(self) -> None:
        if self._transport is not None:
            self._transport.close()
            self._transport = None
            self.reconnects += 1

    def _back_off(self, attempt: int) -> None:
        self.sleep(min(self.backoff_max, self.backoff_initial * 2 ** (attempt - 1)))
