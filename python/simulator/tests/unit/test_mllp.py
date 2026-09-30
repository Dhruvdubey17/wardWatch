from dataclasses import dataclass, field

import pytest
from wardwatch_sim.mllp import (
    Ack,
    DeliveryError,
    MllpClient,
    MllpDecoder,
    ProtocolError,
    frame,
    parse_ack,
)

pytestmark = pytest.mark.unit


def ack_bytes(code: str, control_id: str, error: str = "") -> bytes:
    text = (
        "MSH|^~\\&|WARDWATCH|WARDWATCH|WWSIM|ICU|20240315130000+0000||ACK^R01^ACK|A1|P|2.5.1\r"
        f"MSA|{code}|{control_id}\r"
    )
    if error:
        text += f"ERR|||102^Data type error^HL70357|E|{error}^^WWERR|||detail\r"
    return frame(text.encode())


# Each scripted step answers one recv(): bytes to return, "timeout", or "eof".
Step = bytes | str


@dataclass
class FakeTransport:
    steps: list[Step]
    sent: list[bytes] = field(default_factory=list)
    closed: bool = False

    def send(self, data: bytes) -> None:
        self.sent.append(data)

    def recv(self, max_bytes: int, timeout: float) -> bytes:
        step = self.steps.pop(0)
        if step == "timeout":
            raise TimeoutError
        if step == "eof":
            return b""
        assert isinstance(step, bytes)
        return step

    def close(self) -> None:
        self.closed = True


@dataclass
class FakeNetwork:
    """Hands out one scripted transport per connection attempt."""

    connections: list[FakeTransport]
    opened: list[FakeTransport] = field(default_factory=list)

    def connect(self) -> FakeTransport:
        transport = self.connections.pop(0)
        self.opened.append(transport)
        return transport


def client(
    network: FakeNetwork, sleeps: list[float], max_retries: int = 3, backoff_max: float = 5.0
) -> MllpClient:
    return MllpClient(
        connect=network.connect,
        sleep=sleeps.append,
        clock=lambda: 0.0,
        max_retries=max_retries,
        backoff_max=backoff_max,
    )


def test_decoder_handles_split_and_multiple_frames() -> None:
    decoder = MllpDecoder()
    stream = b"noise" + frame(b"first") + frame(b"second")
    assert decoder.feed(stream[:8]) == []
    assert decoder.feed(stream[8:]) == [b"first", b"second"]
    assert decoder.feed(b"\x0bpart") == []
    assert decoder.feed(b"ial\x1c\r") == [b"partial"]


def test_parse_ack_reads_code_control_id_and_error() -> None:
    assert parse_ack(ack_bytes("AA", "C1")[1:-2]) == Ack("AA", "C1", None)
    assert parse_ack(ack_bytes("AE", "C2", "VALUE_TYPE_MISMATCH")[1:-2]) == Ack(
        "AE", "C2", "VALUE_TYPE_MISMATCH"
    )
    with pytest.raises(ProtocolError, match="MSA"):
        parse_ack(b"MSH|^~\\&|X\r")


def test_accepted_on_first_try() -> None:
    network = FakeNetwork([FakeTransport([ack_bytes("AA", "C1")])])
    sleeps: list[float] = []
    result = client(network, sleeps).send("MSH|x", "C1")
    assert result.ack.code == "AA"
    assert result.attempts == 1
    assert sleeps == []
    assert network.opened[0].sent == [frame(b"MSH|x")]


def test_ack_split_across_reads() -> None:
    whole = ack_bytes("AA", "C1")
    network = FakeNetwork([FakeTransport([whole[:10], whole[10:]])])
    assert client(network, []).send("MSH|x", "C1").ack.code == "AA"


def test_timeout_reconnects_and_resends_with_backoff() -> None:
    network = FakeNetwork(
        [FakeTransport(["timeout"]), FakeTransport(["eof"]), FakeTransport([ack_bytes("AA", "C1")])]
    )
    sleeps: list[float] = []
    sender = client(network, sleeps)
    result = sender.send("MSH|x", "C1")
    assert result.attempts == 3
    assert sleeps == [0.2, 0.4]
    assert [transport.closed for transport in network.opened] == [True, True, False]
    assert all(transport.sent == [frame(b"MSH|x")] for transport in network.opened)
    assert sender.reconnects == 2


def test_gives_up_after_max_retries() -> None:
    network = FakeNetwork([FakeTransport(["timeout"]) for _ in range(3)])
    sleeps: list[float] = []
    with pytest.raises(DeliveryError, match="no ACK after 3 attempts"):
        client(network, sleeps, max_retries=2).send("MSH|x", "C1")
    assert len(sleeps) == 2


def test_application_error_is_retried_when_asked() -> None:
    network = FakeNetwork([FakeTransport([ack_bytes("AE", "C1"), ack_bytes("AA", "C1")])])
    sleeps: list[float] = []
    result = client(network, sleeps).send("MSH|x", "C1")
    assert result.ack.code == "AA"
    assert result.attempts == 2
    assert len(network.opened) == 1


def test_application_error_returned_when_retry_disabled() -> None:
    network = FakeNetwork([FakeTransport([ack_bytes("AE", "C1", "TIMESTAMP_INVALID")])])
    result = client(network, []).send("MSH|x", "C1", retry_on_ae=False)
    assert result.ack == Ack("AE", "C1", "TIMESTAMP_INVALID")


def test_application_error_returned_after_retries_run_out() -> None:
    network = FakeNetwork([FakeTransport([ack_bytes("AE", "C1")] * 3)])
    result = client(network, [], max_retries=2).send("MSH|x", "C1")
    assert result.ack.code == "AE"
    assert result.attempts == 3


def test_reject_is_never_retried() -> None:
    network = FakeNetwork([FakeTransport([ack_bytes("AR", "C1")])])
    sleeps: list[float] = []
    result = client(network, sleeps).send("MSH|x", "C1")
    assert result.ack.code == "AR"
    assert sleeps == []


def test_ack_for_another_message_forces_a_clean_resend() -> None:
    network = FakeNetwork(
        [FakeTransport([ack_bytes("AA", "OLD")]), FakeTransport([ack_bytes("AA", "C1")])]
    )
    result = client(network, []).send("MSH|x", "C1")
    assert result.ack.control_id == "C1"
    assert result.attempts == 2


def test_backoff_is_capped() -> None:
    network = FakeNetwork(
        [FakeTransport(["timeout"]) for _ in range(6)] + [FakeTransport([ack_bytes("AA", "C1")])]
    )
    sleeps: list[float] = []
    client(network, sleeps, max_retries=6, backoff_max=1.0).send("MSH|x", "C1")
    assert sleeps == [0.2, 0.4, 0.8, 1.0, 1.0, 1.0]


def test_ack_timeout_uses_the_clock() -> None:
    times = iter([0.0, 0.0, 11.0])
    network = FakeNetwork([FakeTransport([]), FakeTransport([ack_bytes("AA", "C1")])])
    sender = MllpClient(
        connect=network.connect, sleep=lambda _: None, clock=lambda: next(times, 20.0)
    )
    result = sender.send("MSH|x", "C1")
    assert result.attempts == 2


def test_connection_is_reused_between_messages() -> None:
    network = FakeNetwork([FakeTransport([ack_bytes("AA", "C1"), ack_bytes("AA", "C2")])])
    sender = client(network, [])
    sender.send("MSH|1", "C1")
    sender.send("MSH|2", "C2")
    assert len(network.opened) == 1
    sender.close()
    assert network.opened[0].closed
