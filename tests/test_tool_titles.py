"""A step's one line in the task panel (runner._tool_title): words, not tool names."""

from dex.runner import _tool_title


def test_a_summary_the_agent_wrote_comes_first() -> None:
    assert _tool_title("Bash", {"command": "ls -la", "description": "List the package"}) == "List the package"
    # Antigravity writes one for file writes too.
    assert _tool_title("Write", {"file_path": "/a/b.md", "description": "Write the intro"}) == "Write the intro"
    assert _tool_title("Write", {"file_path": "/a/b.md"}) == "Write b.md"


def test_dex_tools_say_what_they_do() -> None:
    assert _tool_title("mcp__dex__ask_user", {"question": "Which level?\nMore"}) == "Ask: Which level?"
    assert _tool_title("mcp__dex__log_idea", {"text": "tested: the bench"}) == "Idea: tested: the bench"
    assert _tool_title("mcp__dex__query_database", {"sql": "select  1\nfrom t"}) == "Query: select 1 from t"
    assert _tool_title("mcp__dex__add_todos", {"todos": [{"title": "a"}, {"title": "b"}]}) == "Add 2 todos"
    assert _tool_title("mcp__dex__add_todos", {"todos": "[{'title': 'a'}]"}) == "Add 1 todo"
    assert _tool_title("mcp__dex__wait_for_tasks", {"tasks": "['x', 'y']"}) == "Wait for 2 tasks"
    assert _tool_title("mcp__dex__start_task", {"slug": "story-algo-x", "brief": "…"}) == "Start story-algo-x"
    assert _tool_title("mcp__dex__continue_task", {"task": "cat", "message": "Fix it"}) == "Message cat: Fix it"
    assert _tool_title("mcp__dex__utility_proposals_enabled", {}) == "Utility proposals enabled"


def test_web_steps_name_their_target() -> None:
    assert _tool_title("WebFetch", {"url": "https://huggingface.co/datasets/x"}) == "Fetch huggingface.co/datasets/x"
    assert _tool_title("WebSearch", {"query": "EnronQA columns"}) == "Search the web: EnronQA columns"
    assert _tool_title("Grep", {"pattern": "bench", "path": "/g/engine/src"}) == "Search bench in src"


def test_long_titles_are_cut() -> None:
    title = _tool_title("Bash", {"command": "x" * 300})
    assert len(title) == 120 and title.endswith("…")
