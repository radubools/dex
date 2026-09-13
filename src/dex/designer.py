"""The project design chat: shaping a project's AGENTS.md by talking about it.

Deliberately a single tool-less call per turn. The model is handed the current
guide and the conversation, and returns the whole guide back plus a short note
on what changed; dex writes the file. Giving it filesystem tools would let it
edit anything, for no benefit — there is exactly one file in play.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, TextBlock, query

from .config import Config

log = logging.getLogger("dex.designer")

SYSTEM = """\
You maintain one file: a project's AGENTS.md, the standing brief every task in \
that project reads before it starts. You are talking to the person who owns the \
project. Your job is to turn what they say into a guide that an agent can follow \
without asking questions.

A good guide states what to produce, the conventions to follow, and which tools \
are already available so no task wastes turns rediscovering them. It is specific \
and short. It does not explain what the agent could work out, and it does not \
hedge.
"""

TEMPLATE = """\
# Current AGENTS.md for the project "{name}"

```markdown
{guide}
```

# Conversation so far

{history}

# What they just said

{message}

# Reply with exactly these two blocks and nothing else

<summary>
Two or three sentences, in markdown, on what you changed and why. If you changed
nothing — because they asked a question, or you need something from them — say
that here and return the guide unchanged. Address them directly.
</summary>

<guide>
The complete AGENTS.md as it should now read. The whole file every time, not a
patch. Keep everything still correct; change only what their message calls for.
</guide>
"""

_SUMMARY = re.compile(r"<summary>(.*?)</summary>", re.DOTALL)
_GUIDE = re.compile(r"<guide>(.*?)</guide>", re.DOTALL)


@dataclass
class DesignReply:
    summary: str
    guide: str
    changed: bool

    def to_json(self) -> dict[str, Any]:
        return {"summary": self.summary, "changed": self.changed}


def parse(raw: str, current: str) -> DesignReply:
    """Pull the two blocks out, tolerating a model that adds prose around them."""
    summary = _SUMMARY.search(raw)
    guide = _GUIDE.search(raw)

    text = (summary.group(1).strip() if summary else raw.strip())[:2000]
    if guide is None:
        # No guide block: treat the whole reply as commentary and keep the file.
        return DesignReply(summary=text or "I could not draft a change to the guide.",
                           guide=current, changed=False)

    drafted = guide.group(1).strip()
    # Models sometimes wrap the file in a fence despite being asked not to.
    fenced = re.fullmatch(r"```(?:markdown|md)?\n(.*?)\n```", drafted, re.DOTALL)
    if fenced:
        drafted = fenced.group(1)

    changed = drafted.strip() != current.strip() and bool(drafted.strip())
    return DesignReply(summary=text, guide=drafted if changed else current, changed=changed)


def history_text(messages: list[dict[str, str]], limit: int = 12) -> str:
    """The recent conversation, as the design brief embeds it."""
    return _history(messages, limit)


def _history(messages: list[dict[str, str]], limit: int = 12) -> str:
    recent = messages[-limit:]
    if not recent:
        return "(nothing yet)"
    return "\n\n".join(
        f"{'They' if m['role'] == 'user' else 'You'}: {m['text']}" for m in recent if m.get("text")
    )


async def draft(
    *,
    message: str,
    name: str,
    guide: str,
    history: list[dict[str, str]],
    config: Config,
    model: str | None = None,
) -> DesignReply:
    options = ClaudeAgentOptions(
        system_prompt=SYSTEM,
        model=model or config.planner_model,
        allowed_tools=[],
        max_turns=1,
        permission_mode="dontAsk",
        setting_sources=[],
        cwd=str(config.workspace),
    )
    prompt = TEMPLATE.format(
        name=name, guide=guide or "(empty)", history=_history(history), message=message
    )

    chunks: list[str] = []
    async for reply in query(prompt=prompt, options=options):
        if isinstance(reply, AssistantMessage):
            chunks.extend(b.text for b in reply.content if isinstance(b, TextBlock))
    return parse("".join(chunks), guide)
