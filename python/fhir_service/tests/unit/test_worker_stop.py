import asyncio

import pytest
from wardwatch_fhir.workers import stop_tasks

pytestmark = pytest.mark.unit


async def test_a_step_in_progress_is_allowed_to_finish() -> None:
    stop = asyncio.Event()
    finished: list[str] = []

    async def loop() -> None:
        while not stop.is_set():
            await asyncio.sleep(0.05)  # a step: store, then publish
            finished.append("step")

    task = asyncio.create_task(loop())
    await asyncio.sleep(0.01)
    await stop_tasks([task], stop, grace=1.0)
    assert finished == ["step"]
    assert not task.cancelled()


async def test_a_loop_that_ignores_stop_is_cancelled_after_the_grace_period() -> None:
    stop = asyncio.Event()

    async def stuck() -> None:
        await asyncio.Event().wait()

    task = asyncio.create_task(stuck())
    await stop_tasks([task], stop, grace=0.05)
    assert stop.is_set()
    assert task.cancelled()


async def test_no_tasks() -> None:
    stop = asyncio.Event()
    await stop_tasks([], stop, grace=0.05)
    assert stop.is_set()
