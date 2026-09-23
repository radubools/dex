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


def test_a_run_that_claims_to_be_tool_less_actually_disallows_the_tools():
    """`allowed_tools=[]` does not mean "no tools".

    The SDK writes `--allowedTools` only `if effective_allowed_tools:`, so an
    empty list is falsy, the flag is never passed, and the CLI runs with its
    whole default toolset. Three single-turn calls here — the planner, the
    designer and the usage probe — each said "deliberately tool-less" and had
    every tool available.

    It surfaced as something that names neither tools nor permissions: a tool
    call consumes the turn, so `max_turns=1` failed the request outright with
    "Reached maximum number of turns (1)". The planner reached for a tool in
    three runs out of five, so two in five requests planned fine and the rest
    died, which reads as flakiness rather than as a missing flag.

    Checked by reading the source, because the failure is in what is *not*
    passed and only a live call would otherwise show it.
    """
    import ast
    from pathlib import Path

    from dex.permissions import NO_TOOLS

    offenders: list[str] = []
    for source in sorted((Path(__file__).resolve().parent.parent / "src" / "dex").rglob("*.py")):
        tree = ast.parse(source.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if getattr(node.func, "id", getattr(node.func, "attr", "")) != "ClaudeAgentOptions":
                continue
            given = {kw.arg: kw.value for kw in node.keywords}
            allowed = given.get("allowed_tools")
            # An empty list literal is the shape that silently means "all".
            empty = isinstance(allowed, ast.List) and not allowed.elts
            if empty and "disallowed_tools" not in given:
                offenders.append(f"{source.name}:{node.lineno}")

    assert not offenders, (
        "these build ClaudeAgentOptions with allowed_tools=[] and no "
        "disallowed_tools, so the run has every built-in tool: "
        + ", ".join(offenders)
        + ". Pass disallowed_tools=NO_TOOLS."
    )
    # And the list has to be non-empty, or it is the same falsy no-op again.
    assert NO_TOOLS and "Bash" in NO_TOOLS and "Read" in NO_TOOLS
