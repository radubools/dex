from dex.planner import parse_plan


def test_extracts_fenced_json():
    plan = parse_plan(
        'Here is the split:\n```json\n{"tasks": [{"title": "Two Sum", '
        '"problem": "Given nums and target...", "slug": "two-sum"}], '
        '"notes": "one problem", "needs_clarification": ""}\n```\nDone.',
        existing=[],
    )
    assert [t.slug for t in plan.tasks] == ["two-sum"]
    assert plan.notes == "one problem"


def test_extracts_bare_json_without_fence():
    plan = parse_plan(
        '{"tasks": [{"title": "LRU Cache", "problem": "Design an LRU cache.", "slug": "lru-cache"}]}',
        existing=[],
    )
    assert plan.tasks[0].title == "LRU Cache"


def test_slug_collisions_get_suffixed():
    plan = parse_plan(
        '{"tasks": ['
        '{"title": "Two Sum", "problem": "a", "slug": "two-sum"},'
        '{"title": "Two Sum", "problem": "b", "slug": "two-sum"}]}',
        existing=["two-sum"],
    )
    assert [t.slug for t in plan.tasks] == ["two-sum-2", "two-sum-3"]


def test_tasks_without_a_problem_are_dropped():
    plan = parse_plan('{"tasks": [{"title": "Empty", "problem": "   ", "slug": "x"}]}', existing=[])
    assert plan.tasks == []


def test_unparseable_output_asks_for_clarification():
    plan = parse_plan("I could not do that", existing=[])
    assert plan.tasks == []
    assert plan.needs_clarification


def test_missing_slug_is_derived_from_title():
    plan = parse_plan('{"tasks": [{"title": "Merge K Sorted Lists!", "problem": "p"}]}', existing=[])
    assert plan.tasks[0].slug == "merge-k-sorted-lists"
