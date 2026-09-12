"""A scripted stand-in for the agent, for developing the UI without spending tokens.

Enabled with `DEX_FAKE_AGENT=1`. It writes a real (small) package into the task
directory and emits the same events a real run does — text, tool calls, a diff,
a clarifying question, assets, a result — so every surface in the UI can be
exercised offline. It is not a simulator: it always produces the same package.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import time
import uuid
from pathlib import Path
from typing import Any

from .models import TaskState
from .runner import TaskRunner


class FakeTaskRunner(TaskRunner):
    """Replays a fixed script, reusing the real runner's event plumbing."""

    async def run(self) -> None:
        self.task.started_at = time.time()
        self.task_dir.mkdir(parents=True, exist_ok=True)
        self.task.activity = "starting"
        self.set_state(TaskState.RUNNING)

        try:
            await self._script()
        except asyncio.CancelledError:
            self.task.finished_at = time.time()
            self.set_state(TaskState.CANCELLED)
            raise
        except Exception as exc:
            self._fail(f"{type(exc).__name__}: {exc}")
        else:
            self.task.finished_at = time.time()
            self.task.activity = None
            self.set_state(TaskState.SUCCEEDED)
        finally:
            for future in list(self.task.pending.values()):
                if not future.done():
                    future.cancel()
            self.task.pending.clear()

    async def _tool(self, name: str, title: str, output: str, seconds: float = 0.9) -> None:
        tool_id = uuid.uuid4().hex[:12]
        self.task.activity = title
        # Mirrors the real runner exactly: activity rides the tool event and no
        # state transition is emitted. Calling set_state here instead is what
        # previously hid a staleness bug from every test.
        self.emit("tool", id=tool_id, name=name, title=title, input={}, activity=title)
        await asyncio.sleep(seconds)
        self.emit("tool_result", id=tool_id, ok=True, output=output)

    async def _write(self, name: str, body: str) -> None:
        target = self.task_dir / name
        before = target.read_text() if target.exists() else ""
        from .diffs import build_patch

        change = build_patch(target, before, body, self.config.workspace)
        self.emit("diff", id=uuid.uuid4().hex[:12], tool="Write", **change.to_json())
        await self._tool("Write", f"Write {name}", f"wrote {len(body)} bytes", 0.6)
        target.write_text(body)

    async def _say(self, text: str, chunk: int = 12) -> None:
        """Emits the text as deltas, so the streaming path is exercised."""
        block = uuid.uuid4().hex[:12]
        for i in range(0, len(text), chunk):
            self.emit("text_delta", id=block, delta=text[i : i + chunk])
            await asyncio.sleep(0.04)
        self.emit("block_end", id=block)

    async def _think(self, text: str, chunk: int = 20) -> None:
        block = uuid.uuid4().hex[:12]
        for i in range(0, len(text), chunk):
            self.emit("thinking_delta", id=block, delta=text[i : i + chunk])
            await asyncio.sleep(0.03)
        self.emit("block_end", id=block)

    async def _script(self) -> None:
        await self._think(
            "The obvious approach is a nested loop over every pair, which is quadratic. "
            "A hash map trades space for time by remembering complements as we scan, "
            "so one pass suffices. Worth showing both so the improvement is visible."
        )
        await self._say("Reading the problem and sketching the **approaches**.")
        await self._tool("Read", "Read problem statement", "ok", 0.5)

        question_id = uuid.uuid4().hex[:12]
        self.emit(
            "question",
            id=question_id,
            question="Should the solution return indices or the values themselves?",
            options=["Indices", "Values", "Either is fine"],
        )
        answer = await self._park(question_id, "waiting on a clarifying question")
        self.emit("question_answered", id=question_id, answer=answer)
        await self._say(f"Going with {str(answer).lower()}. Writing the solutions now.")

        # Exercises the approval path, and therefore the auto-approve toggle.
        approval_id = uuid.uuid4().hex[:12]
        if await self.settings.auto_approve():
            self.emit("approval", id=approval_id, tool="Bash", title="Install a dependency", input={}, auto=True)
            self.emit("approval_resolved", id=approval_id, decision="allow", auto=True)
        else:
            self.emit("approval", id=approval_id, tool="Bash", title="Install a dependency", input={})
            decision = await self._park(approval_id, "waiting on approval for Bash", approval=True)
            self.emit("approval_resolved", id=approval_id, decision=str(decision))
            if decision == "deny":
                await self._say("Skipping the dependency and using the standard library.")

        await self._write("solutions.py", SOLUTIONS)
        await self._write("test_solutions.py", TESTS)
        await self._tool("Bash", "pytest test_solutions.py -q", "8 passed in 0.05s", 1.4)

        await self._say(
            "Suite is green. Writing the explanation and rendering the animation.\n\n"
            "- `solutions.py` - two approaches\n- `test_solutions.py` - 8 cases"
        )
        await self._write("explanation.md", EXPLANATION.replace("{answer}", str(answer)))
        await self._write("animation.py", ANIMATION)

        # Kept out of the assets root so it is not mistaken for a project.
        gif_source = self.config.state_dir / "samples" / "brute_force.gif"
        if gif_source.exists():
            await self._tool("Bash", "render brute_force.gif", "rendered", 1.6)
            shutil.copyfile(gif_source, self.task_dir / "brute_force.gif")

        await self._write("manifest.json", json.dumps(MANIFEST, indent=2))
        self.emit(
            "result",
            ok=True,
            durationMs=int((time.time() - (self.task.started_at or time.time())) * 1000),
            turns=9,
            costUsd=0.0,
            summary="Two approaches implemented, 8 tests passing, one animation rendered.",
        )


SOLUTIONS = '''"""Two Sum — return the indices of the pair summing to `target`.

| approach     | time    | space |
|--------------|---------|-------|
| brute_force  | O(n^2)  | O(1)  |
| hash_map     | O(n)    | O(n)  |
"""

from __future__ import annotations


def solve_brute_force(nums: list[int], target: int) -> tuple[int, int] | None:
    """Check every pair.

    Time: O(n^2)
    Space: O(1)
    """
    for i in range(len(nums)):
        for j in range(i + 1, len(nums)):
            if nums[i] + nums[j] == target:
                return i, j
    return None


def solve_hash_map(nums: list[int], target: int) -> tuple[int, int] | None:
    """Remember what each value would need to pair with.

    Time: O(n)
    Space: O(n)
    """
    seen: dict[int, int] = {}
    for index, value in enumerate(nums):
        if (complement := target - value) in seen:
            return seen[complement], index
        seen[value] = index
    return None


SOLUTIONS = {"brute_force": solve_brute_force, "hash_map": solve_hash_map}
'''

TESTS = '''import random

import pytest

from solutions import SOLUTIONS


@pytest.fixture(params=sorted(SOLUTIONS), ids=sorted(SOLUTIONS))
def solve(request):
    return SOLUTIONS[request.param]


def test_finds_a_pair(solve):
    assert solve([2, 7, 11, 15], 9) == (0, 1)


@pytest.mark.parametrize("nums,target", [([], 0), ([1], 1), ([1, 2], 99)])
def test_returns_none_when_no_pair_exists(solve, nums, target):
    assert solve(nums, target) is None


def test_handles_duplicates_and_negatives(solve):
    assert solve([3, 3], 6) == (0, 1)
    assert solve([-1, -2, -3], -5) == (1, 2)


def test_optimal_agrees_with_brute_force(solve):
    rng = random.Random(0)
    for _ in range(200):
        nums = [rng.randint(-20, 20) for _ in range(rng.randint(0, 8))]
        target = rng.randint(-40, 40)
        expected = SOLUTIONS["brute_force"](nums, target)
        assert (solve(nums, target) is None) == (expected is None)
'''

EXPLANATION = """# Two Sum

Given `nums` and `target`, return the indices of the two numbers that add to
`target`. Returning {answer}.

## Brute force

Try every pair. Correct, and the thing you should be able to write in ten
seconds before improving on it.

```mermaid
flowchart LR
    A[pick i] --> B[scan j > i]
    B --> C{nums i + nums j == target?}
    C -- yes --> D[return i, j]
    C -- no --> B
    B -- exhausted --> A
```

Time O(n^2), space O(1).

## Hash map

The insight: while scanning, you already know what the current value *needs*.
Store each value's index as you pass it, and every later value can ask in O(1)
whether its complement has already been seen.

```mermaid
sequenceDiagram
    participant S as scan
    participant M as seen{}
    S->>M: need 9-2 = 7?
    M-->>S: no — store 2 at 0
    S->>M: need 9-7 = 2?
    M-->>S: yes, index 0 → return (0, 1)
```

One pass, so time O(n) and space O(n) for the map.

## Comparison

| approach | time | space | reach for it when |
|---|---|---|---|
| brute force | O(n^2) | O(1) | n is tiny, or you need a baseline to test against |
| hash map | O(n) | O(n) | almost always |
"""

ANIMATION = '''from manim import *


class BruteForce(Scene):
    def construct(self):
        caption = Text("scanning for target 9", font_size=26).to_edge(UP)
        self.play(Write(caption))
        boxes = VGroup(*[
            VGroup(Square(side_length=1.0, color=GREY_B), Text(str(v), font_size=28))
            for v in (2, 7, 11)
        ]).arrange(RIGHT, buff=0.35)
        self.play(FadeIn(boxes))
        self.play(boxes[0][0].animate.set_fill(BLUE, opacity=0.5))
        self.wait(0.6)
        self.play(boxes[1][0].animate.set_fill(GREEN, opacity=0.6))
        self.wait(0.6)
'''

MANIFEST: dict[str, Any] = {
    "problem": "Return the indices of the two numbers summing to target.",
    "approaches": [
        {"name": "brute_force", "time": "O(n^2)", "space": "O(1)", "animation": "brute_force.gif"},
        {"name": "hash_map", "time": "O(n)", "space": "O(n)", "animation": None},
    ],
    "files": {
        "solutions": "solutions.py",
        "tests": "test_solutions.py",
        "explanation": "explanation.md",
        "animations": ["brute_force.gif"],
    },
    "tests": {"command": "pytest test_solutions.py -q", "passed": 8, "failed": 0},
}
