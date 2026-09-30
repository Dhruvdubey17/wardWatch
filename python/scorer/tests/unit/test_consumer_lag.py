from typing import cast

import pytest
from aiokafka import AIOKafkaConsumer, TopicPartition
from prometheus_client import REGISTRY
from wardwatch_scorer.metrics import record_lag

pytestmark = pytest.mark.unit


class FakeConsumer:
    def __init__(
        self, ends: dict[TopicPartition, int | None], positions: dict[TopicPartition, int]
    ):
        self._ends = ends
        self._positions = positions

    def assignment(self) -> set[TopicPartition]:
        return set(self._ends)

    def highwater(self, partition: TopicPartition) -> int | None:
        return self._ends[partition]

    async def position(self, partition: TopicPartition) -> int:
        return self._positions[partition]


def lag(topic: str, partition: int) -> float | None:
    return REGISTRY.get_sample_value(
        "wardwatch_scorer_consumer_lag", {"topic": topic, "partition": str(partition)}
    )


async def test_lag_is_the_distance_to_the_end_of_each_partition() -> None:
    zero, one, two = (TopicPartition("lag.test", n) for n in range(3))
    consumer = FakeConsumer({zero: 120, one: 40, two: None}, {zero: 100, one: 40, two: 0})
    await record_lag(cast(AIOKafkaConsumer, consumer))
    assert lag("lag.test", 0) == 20
    assert lag("lag.test", 1) == 0
    assert lag("lag.test", 2) is None


def test_the_fake_matches_the_consumer_interface() -> None:
    for method in ("assignment", "highwater", "position"):
        assert callable(getattr(AIOKafkaConsumer, method))
