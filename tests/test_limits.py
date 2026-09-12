import asyncio

from dex.limits import DynamicLimiter


async def test_holds_work_at_the_limit_and_releases_it():
    limit = 1
    gate = DynamicLimiter(lambda: _value(limit))
    started: list[int] = []

    async def job(n: int, hold: asyncio.Event):
        async with gate:
            started.append(n)
            await hold.wait()

    first_hold, second_hold = asyncio.Event(), asyncio.Event()
    a = asyncio.create_task(job(1, first_hold))
    b = asyncio.create_task(job(2, second_hold))
    await asyncio.sleep(0.05)

    assert started == [1]  # the second is waiting on the gate
    first_hold.set()
    await asyncio.sleep(0.05)
    assert started == [1, 2]

    second_hold.set()
    await asyncio.gather(a, b)


async def test_raising_the_limit_frees_a_waiter_without_anything_finishing():
    limit = 1
    gate = DynamicLimiter(lambda: _value(limit))
    started: list[int] = []
    hold = asyncio.Event()

    async def job(n: int):
        async with gate:
            started.append(n)
            await hold.wait()

    tasks = [asyncio.create_task(job(n)) for n in (1, 2)]
    await asyncio.sleep(0.05)
    assert started == [1]

    limit = 2  # raised from the UI mid-flight
    await asyncio.sleep(1.3)  # the waiter re-checks on its own
    assert started == [1, 2]

    hold.set()
    await asyncio.gather(*tasks)


async def test_active_count_tracks_entries_and_exits():
    gate = DynamicLimiter(lambda: _value(4))
    assert gate.active == 0
    async with gate:
        assert gate.active == 1
        async with gate:
            assert gate.active == 2
    assert gate.active == 0


async def _value(n: int) -> int:
    return n
