"""A planner reply that is cut off mid-JSON must not lose everything.

Asking for the algorithms in a whole book made the planner emit so many tasks
that the reply hit its length limit. The JSON had no closing fence and no
balanced outer object, so parsing failed and twelve minutes of planning came
back as "I could not turn that into a task list".
"""

from __future__ import annotations

from dex.planner import parse_plan

WHOLE = """```json
{"tasks": [
  {"title": "Is Unique", "slug": "is-unique", "problem": "All characters unique?"},
  {"title": "Check Permutation", "slug": "check-permutation", "problem": "Is one a permutation of the other?"}
], "notes": "two of them", "needs_clarification": ""}
```"""

# The same reply stopped mid-way through the third task, as the model would
# leave it on hitting the limit: no closing brace, bracket or fence.
CUT_OFF = """```json
{"tasks": [
  {"title": "Is Unique", "slug": "is-unique", "problem": "All characters unique?"},
  {"title": "Check Permutation", "slug": "check-permutation", "problem": "Is one a permutation of the other?"},
  {"title": "URLify", "slug": "urlify", "problem": "Replace spaces with %20 in"""


def test_a_complete_reply_still_parses():
    plan = parse_plan(WHOLE, [])
    assert [t.slug for t in plan.tasks] == ["is-unique", "check-permutation"]
    assert plan.notes == "two of them"


def test_a_cut_off_reply_keeps_the_tasks_it_finished():
    plan = parse_plan(CUT_OFF, [])
    assert [t.slug for t in plan.tasks] == ["is-unique", "check-permutation"]
    # The half-written third task is not guessed at.
    assert all("URLify" not in t.title for t in plan.tasks)
    assert "cut off" in plan.notes
    assert not plan.needs_clarification


def test_a_reply_with_no_tasks_at_all_still_asks_for_a_restatement():
    plan = parse_plan("I am not sure what you mean, could you say more?", [])
    assert plan.tasks == []
    assert plan.needs_clarification


def test_braces_inside_strings_do_not_confuse_the_rescue():
    raw = '{"tasks": [{"title": "Braces", "slug": "braces", "problem": "Handle {\\"a\\": 1} literals"},'
    plan = parse_plan(raw, [])
    assert [t.slug for t in plan.tasks] == ["braces"]
    assert "{" in plan.tasks[0].problem
