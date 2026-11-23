# 30 · A task agent's context window

## Summary

Everything a task's agent knows is what is in its context window: what the
harness put there before the first token, what dex put in the brief, what each
tool call read back, and what was summarised away. This document draws that
window for the five situations that change it:

1. [a task starting](#1-a-task-starts) — system prompt, dex's instructions,
   the project guide, the skills list, the brief, the tools;
2. [a session resuming](#2-a-session-resumes) — a pause, a follow-up, a wake,
   a lost session, a message typed mid-run;
3. [skills read mid-run](#3-skills-pulled-in-mid-run) — a `SKILL.md`, a
   skill's files, `utils/API.md`;
4. [a tool call](#4-how-a-tool-call-runs) — call → policy → result, and
   subagents;
5. [summaries](#5-summaries-and-compaction) — the closing summary, dex's
   `/compact`, a fresh session from a summary, and an agent compacting itself
   when the window fills.

Each situation has a **sequence** (who adds what, in order) and a **block
diagram** of the window as stacked blocks, oldest at the top. Every block
names what it holds and the function that puts it there.

dex runs four kinds of agent. They share one runner (`TaskRunner.run`, in
`src/dex/runner.py`), one brief (`src/dex/prompts.py`) and one permission
policy (`src/dex/permissions.py`); they differ in who owns the system prompt,
where dex's own instructions land, how tool calls reach the policy and what
happens when the window fills.

| Agent | Model id | Client (`TaskRunner._client`) | Process | Session |
|---|---|---|---|---|
| Claude | `claude-…` | `ClaudeSDKClient` (Agent SDK) | Claude Code CLI over stdio, one per run | session id, resumed with `resume=` |
| Codex | `codex:<model>#<effort>` | `codex.CodexClient` (design 29) | `codex app-server`, JSON-RPC, one per run | a thread, `thread/resume` |
| OpenCode | `oc:<model>` (Go plan), `oc:local/<id>` (this Mac or Colab) | `opencode.OpencodeClient` (design 28) | `opencode run`, **one process per turn** | `ses_…`, `--session` |
| Antigravity | `agy:<model>` | `antigravity.AgyClient` (design 27) | `agy` print mode, stream-json, one per run | a conversation, `--conversation` |

A local model (`oc:local/…`) is OpenCode with one more hop: its requests go
through dex's relay (`antigravity.Bridge._relay`) to the model server, which
is how dex streams its tokens and keeps its prompt cache warm
(`antigravity.steady_system`).

### Legend

The block diagrams colour each block by who wrote it:

| Colour | Written by |
|---|---|
| grey | the agent's own harness (Claude Code, Codex, OpenCode, `agy`) |
| blue | dex's fixed text: a scope's system prompt, notes, nudges |
| green | the task's brief, built per task by `prompts.py` |
| amber | added while the run goes: tool calls and their results |
| red | a summary standing in for history that is gone |

---

## 1. A task starts

### Who adds what

```mermaid
sequenceDiagram
    autonumber
    participant Q as TaskManager (queue)
    participant R as TaskRunner.run
    participant P as prompts.py
    participant S as skills.py
    participant C as Agent client
    participant H as Agent harness (CLI)
    participant M as Model

    Q->>R: claim a task (problem, model, scope)
    R->>S: _prepare_skill_work: work_copies of each enabled skill
    R->>R: PermissionPolicy(...) then _with_diffs(policy.build())
    R->>R: _ask_user_server: dex MCP server (ask_user, task tools)
    R->>R: ClaudeAgentOptions(system_prompt append = GENERATION_SYSTEM ...)
    R->>R: _refresh_utils_index: rewrite utils/API.md
    R->>P: generation_prompt(problem, skills=_enabled_skills, focus=_thread_skill)
    P->>P: project_instructions: AGENTS.common.md + project AGENTS.md
    P->>P: skills_listing: one line per skill, focus skill in full
    P-->>R: the brief
    opt end-to-end tests off
        R->>R: prompt += E2E_OFF
    end
    R->>C: _client(options) by model prefix
    C->>H: start: system prompt, tools, MCP servers
    R->>C: _query(brief)
    Note over C: OpenCode, Antigravity: brief prefixed with the scope system prompt in dex-instructions tags
    C->>H: first user message
    H->>M: system + tools + first user message
    M-->>H: first reply (text, thinking, tool calls)
```

### The window at the first model call — Claude

```mermaid
block-beta
    columns 1
    sys["System prompt · Claude Code preset<br/>tools how-to, environment, cwd = task dir<br/>ClaudeAgentOptions system_prompt preset claude_code"]
    app["Appended system prompt · the scope's rules<br/>GENERATION_SYSTEM (or SURVEY, DESIGN, power_system, TODOS, IDEATION)<br/>TaskRunner.run, system_prompt append"]
    tools["Tool definitions · Claude Code built-ins<br/>Read Write Edit Bash Grep Glob Task WebFetch …<br/>tools= narrows it for todos and ideation, allowed_tools for a survey"]
    mcp["MCP tools · mcp__dex__*<br/>ask_user, utility_proposals_enabled, project_threads, find_tasks,<br/>start_task, continue_task, task_status, wait_for_tasks, …<br/>_ask_user_server + _task_tools (survey: survey_server instead)"]
    guide["Brief 1 · the guides<br/>How work is done here = AGENTS.common.md<br/>What this project produces = assets/project/AGENTS.md<br/>project_instructions"]
    how["Brief 2 · How a task works here<br/>python, what may be read and written, the data directory, linked sources<br/>generation_prompt"]
    skl["Brief 3 · Skills enabled for this project<br/>one line each: name, description, Read SKILL.md, promote into utils/<br/>skills_listing (thread's skill in full, others first sentence)"]
    fin["Brief 4 · shared utilities protocol and Finish<br/>generation_prompt"]
    req["Brief 5 · Request = task.problem<br/>(+ Sources, + a Follow-up or Resuming note on later attempts)"]
    this["Brief 6 · This task: working directory, skills root, existing files<br/>generation_prompt (+ E2E_OFF)"]

    classDef harness fill:#e5e7eb,stroke:#6b7280,color:#111
    classDef dex fill:#dbeafe,stroke:#2563eb,color:#111
    classDef brief fill:#dcfce7,stroke:#16a34a,color:#111
    class sys,tools harness
    class app,mcp dex
    class guide,how,skl,fin,req,this brief
```

Nothing else reaches it: `setting_sources=[]` keeps the CLI from reading any
`CLAUDE.md`, `.claude/` settings or Claude skills on the machine, so the brief
is the whole spec.

The brief runs **from what every task of a thread shares to what is this
task's alone** (the comment above the return in `generation_prompt`). Blocks
1–5 up to `# Request` are word for word the same for every task of a thread —
the skills are written against `<skills>` and that root is named only in
block 6 — so a model server's prompt cache reads them once per thread, not
once per task.

### The window at the first model call — Codex, OpenCode, Antigravity

```mermaid
block-beta
    columns 3
    c1["Codex"] o1["OpenCode"] a1["Antigravity"]
    c2["Codex's system prompt<br/>(its own, with AGENTS.md discovery off:<br/>project_doc_max_bytes=0, codex._flags)"] o2["OpenCode's system prompt<br/>agent dex-task (opencode.install)<br/>working directory line rewritten<br/>for a local model (steady_system)"] a2["agy's system prompt<br/>agent dex-task (antigravity.install)"]
    c3["Developer instructions =<br/>the scope's system prompt<br/>CodexClient._open_thread<br/>developerInstructions"] o3["Tools: OpenCode's own + execute<br/>dex MCP as a remote server<br/>OpencodeClient._config<br/>(question, subagent, skill denied)"] a3["Tools: antigravity.TOOLS<br/>dex MCP via agy_mcp.py relay<br/>(no subagents, no browser)"]
    c4["Tools: Codex built-ins<br/>+ dex MCP over HTTP (Bridge)<br/>subagents, plugins, apps off<br/>(FEATURES_OFF)"] o4["First user message, part 1<br/>dex-instructions tag = scope system prompt<br/>+ TOOLS_NOTE<br/>OpencodeClient.query, first prompt only"] a4["First user message, part 1<br/>dex-instructions tag = scope system prompt<br/>AgyClient.query, first prompt only"]
    c5["First user message = the brief<br/>turn/start input"] o5["First user message, part 2<br/>= the brief, on stdin"] a5["First user message, part 2<br/>= the brief, one stream-json line"]

    classDef harness fill:#e5e7eb,stroke:#6b7280,color:#111
    classDef dex fill:#dbeafe,stroke:#2563eb,color:#111
    classDef brief fill:#dcfce7,stroke:#16a34a,color:#111
    classDef head fill:#ffffff,stroke:#ffffff,color:#111
    class c1,o1,a1 head
    class c2,o2,a2,o3,a3,c4 harness
    class c3,o4,a4 dex
    class c5,o5,a5 brief
```

The difference that matters: **only Claude and Codex put dex's rules in the
system position.** OpenCode and `agy` keep their own system prompt (it is what
teaches the model their tools), so dex's part rides in front of the first user
message, once, and is never re-sent: `_first_prompt` is false for a session
that resumes.

### What each scope's brief is

| Scope | Appended system prompt | Brief |
|---|---|---|
| package task | `GENERATION_SYSTEM` | `generation_prompt` (above) |
| project-wide task | `GENERATION_SYSTEM` | `generation_prompt(project_wide=True)`: guide, request, "maintenance pass", finish |
| survey | `SURVEY_SYSTEM` | `survey_prompt`; tools held to `surveyor.TOOL_NAMES` + `ask_user`, always on Claude |
| design turn | `DESIGN_SYSTEM` | `design_prompt`: guide, chat history, message |
| power task | `power_system` | `power_prompt`: request and where things are, no project guide |
| todos split | `TODOS_SYSTEM` | `todos_prompt`, tools `Read Grep Glob Bash` |
| ideation | `IDEATION_SYSTEM` | `ideation_prompt` |

---

## 2. A session resumes

A run resumes a session when `task.resumed_from` holds a session id. Five
paths set it, and each sends something different as the first new turn:

| Path | Sets `resumed_from` | First new user turn |
|---|---|---|
| Pause for capacity, server restart, Resume in place | `TaskManager.pause` / `resume_paused` / `resume_in_place` (`src/dex/queue.py`) | the **whole brief again** (same `problem`) |
| Resume (a new attempt of a stopped task) | `TaskManager.resume` | the brief, with a `## Resuming` note appended to the request |
| Follow-up, Compact off | `TaskManager.deliver_messages` | the brief, with `## Follow-up` and the operator's notes |
| Follow-up, Compact on (`compact_follow_ups`) | not set: a **fresh session** | the brief, with `## Follow-up` holding the previous summary (section 5) |
| Woken after `wait_for_tasks` | `Dependencies` (deps.py) | only `wake_note`: how the waited-on tasks went |

### Who adds what

```mermaid
sequenceDiagram
    autonumber
    participant Q as TaskManager
    participant R as TaskRunner.run
    participant C as Agent client
    participant H as Agent harness
    participant M as Model

    Q->>R: task with resumed_from = old session
    R->>R: _resumable(model, session)?
    alt another agent's session
        R->>R: drop resume, prompt = FRESH_AFTER_LOST_SESSION + brief
    end
    R->>R: build the brief again (generation_prompt)
    opt woken (wake_note)
        R->>R: prompt = wake_note only (brief + note if fresh)
    end
    R->>C: _client(options with resume)
    C->>H: resume: --resume, thread/resume, --session, --conversation
    H->>H: reload the old history from its own store
    opt _compacts: Claude follow-up of a task that succeeded, auto_compact on
        R->>C: _query("/compact")
        H->>M: summarise the history
        H-->>R: compact_boundary
    end
    R->>C: _query(prompt)
    H->>M: old history (or its summary) + new turn
    alt the resumed session ran no turn
        R->>C: _query(RESUME_NUDGE)
        alt still no turn
            R->>C: new client, no resume: FRESH_AFTER_LOST_SESSION + brief
        end
    end
```

### The window after a resume

```mermaid
block-beta
    columns 1
    sys["System prompt + appended scope rules + tools<br/>re-sent by the harness on every call<br/>(Codex: developerInstructions passed again to thread/resume)"]
    old1["The old session, as the harness stored it<br/>first brief, every reply, every tool call and result<br/>resumed by session id, not re-sent by dex"]
    cut["Where the old run stopped: possibly mid-turn<br/>(a pause cancels the runner, TaskManager.pause)"]
    cmp["Optional · its summary, in place of all of the above<br/>Claude only, _compacts then _compact sends COMPACT"]
    new["New user turn · one of<br/>the brief again (pause, resume, follow-up)<br/>wake_note (woken), FRESH_AFTER_LOST_SESSION + brief (lost)<br/>TaskRunner.run"]
    nudge["Optional · RESUME_NUDGE<br/>when the resumed session answered with an empty turn"]
    work["New work: replies, tool calls, results"]

    classDef harness fill:#e5e7eb,stroke:#6b7280,color:#111
    classDef dex fill:#dbeafe,stroke:#2563eb,color:#111
    classDef brief fill:#dcfce7,stroke:#16a34a,color:#111
    classDef run fill:#fef3c7,stroke:#d97706,color:#111
    classDef summary fill:#fee2e2,stroke:#dc2626,color:#111
    class sys harness
    class old1,cut,work run
    class cmp summary
    class new brief
    class nudge dex
```

On OpenCode and Antigravity `dex-instructions` is not sent again on a resume
(`_first_prompt = not options.resume`): the copy from the session's first
message is still in the history.

### Messages that arrive while a run goes

Two more user turns can join a running session, both from
`TaskRunner._converse`:

```mermaid
sequenceDiagram
    autonumber
    actor P as Operator or player
    participant Q as TaskManager.say
    participant R as TaskRunner._converse
    participant C as Agent client
    participant M as Model

    P->>Q: a message for a running task
    Q->>R: task.inbox.put_nowait(body)
    R->>C: interrupt() the turn under way
    C-->>R: its ResultMessage (not counted)
    R->>C: _query(chat_interjection(text))
    C->>M: history + "The operator just sent this message ..."
    Note over R,M: a connection failure instead: wait, then _query(CARRY_ON) on the same session
```

A message typed after the last turn ended is kept (`TaskMessageStore.add`) and
starts the next follow-up attempt instead.

---

## 3. Skills pulled in mid-run

A skill enters the window in two tiers (the comment above `skills_listing` in
`generation_prompt`): **always** its one line in the brief, **on demand** its
`SKILL.md`, read with an ordinary file read once the agent decides it needs
it. dex never injects a `SKILL.md` itself, and no agent's own skill system is
used: Claude's CLI loads none (`setting_sources=[]`), OpenCode's `skill` tool
is denied (`opencode.REFUSED`), Codex's skills are not registered (design 29).

### Who adds what

```mermaid
sequenceDiagram
    autonumber
    participant M as Model
    participant H as Agent harness
    participant R as TaskRunner
    participant P as PermissionPolicy
    participant D as Disk

    Note over M: the brief lists game-story, Read its SKILL.md at skills root
    M->>H: Read skills/work/game-story/SKILL.md
    H->>R: can_use_tool(Read, path)
    R->>P: _auto_reason: Read is in READ_ONLY_TOOLS
    P-->>H: allow
    H->>D: read the task's working copy (skills.work_copies)
    D-->>H: SKILL.md text
    H-->>M: tool result appended to the window
    R->>R: _note_skill: spend now goes to game-story (skills.attribution)
    M->>H: Read utils/API.md, then a module or a reference file
    H-->>M: more tool results
    M->>H: Bash python -c "from game_story import ..."
    Note over M,D: a helper it writes goes into the working copy's utils/, published after a clean finish (_resync_skills)
```

### The window after reading a skill

```mermaid
block-beta
    columns 1
    start["System prompt, tools, the brief<br/>(the skills list: one line per skill)<br/>section 1"]
    call1["Tool call · Read SKILL.md<br/>AssistantMessage ToolUseBlock"]
    res1["Tool result · the skill's whole SKILL.md<br/>read from the task's working copy, skills.work_copies<br/>stays in the window for the rest of the session"]
    call2["Tool call · Read utils/API.md<br/>(rewritten before the run, _refresh_utils_index)"]
    res2["Tool result · signatures and a line per shared module"]
    more["Further reads: a module, a reference file the SKILL.md names<br/>each one a call and its result"]
    work["The work that uses it"]

    classDef harness fill:#e5e7eb,stroke:#6b7280,color:#111
    classDef brief fill:#dcfce7,stroke:#16a34a,color:#111
    classDef run fill:#fef3c7,stroke:#d97706,color:#111
    class start brief
    class call1,res1,call2,res2,more,work run
```

A skill's text costs its full size once it is read, and stays until the
session is compacted (section 5). The thread's own skill is described in full
in the listing (`skills_listing(focus=...)`), with the skills its `SKILL.md`
names, so the agent usually needs no extra read to find it.

---

## 4. How a tool call runs

### Claude

```mermaid
sequenceDiagram
    autonumber
    participant M as Model
    participant H as Claude Code CLI
    participant W as _with_diffs
    participant P as PermissionPolicy.build
    participant E as TaskRunner._escalate
    actor O as Operator
    participant T as Tool

    M->>H: tool_use block (name, input)
    H-->>W: AssistantMessage, shown as a tool event (_translate)
    H->>W: can_use_tool(name, input, context)
    alt parked by wait_for_tasks
        W-->>H: deny, end your turn
    end
    W->>W: preview_change: emit a diff event
    W->>P: decide
    P->>P: _refuse_reason (power, ideas, unattended, delegation, other package, linked source)
    alt refused
        P-->>H: PermissionResultDeny(message)
    else _auto_reason is None
        P-->>H: PermissionResultAllow(updated_input)
    else needs a person
        P->>E: escalate(reason)
        alt auto_approve on or ideation
            E-->>P: allow
        else
            E->>O: approval event, task parked
            O-->>E: allow or deny
        end
        P-->>H: allow or deny
    end
    H->>T: run it (Bash, Write, an mcp__dex__ tool in process)
    T-->>H: output or error
    H->>M: tool_result appended (UserMessage ToolResultBlock)
    H-->>W: tool_result event, _settle_change counts the lines
```

A denial is a tool result too: the model reads the policy's message
(`PermissionResultDeny.message`) as the call's output and goes on.
`mcp__dex__ask_user` is allowed outright and then holds the call open
(`_park`) until the operator answers, so the answer comes back as its result.

### Codex, OpenCode, Antigravity: the same policy behind a gate

```mermaid
sequenceDiagram
    autonumber
    participant M as Model
    participant H as Agent CLI
    participant G as Hook or plugin
    participant B as Bridge (loopback HTTP)
    participant C as Client._gate
    participant P as can_use_tool (same policy)
    participant T as Tool

    M->>H: a call in the agent's own shape (shell, apply_patch, write_to_file, ...)
    H->>G: PreToolUse (codex_hook.py, agy_hook.py) or tool hook (opencode_gate.js)
    G->>B: POST the call to DEX_..._GATE
    B->>C: _gate(payload)
    C->>C: claude_shape or claude_calls: as the Claude tool it amounts to
    C->>P: decide(name, shaped, context)
    P-->>C: allow, deny, or allow with a confined command
    C-->>G: decision (a hook that cannot reach dex denies)
    G-->>H: allow or deny
    H->>T: run it, or not
    T-->>H: output
    H->>M: result appended in the agent's own format
    H-->>C: the result event, turned into a ToolResultBlock for the runner
```

dex's MCP tools reach these agents over the same Bridge
(`antigravity.Bridge`), served from the in-process server a Claude run gets.
OpenCode calls them inside its `execute` code tool, each inner call gated on
its own (`OpencodeClient._gate`, `CODE_TOOL`).

### The window after tool calls

```mermaid
block-beta
    columns 1
    start["System prompt, tools, brief"]
    t1["Assistant turn · text, thinking, tool_use<br/>(Claude: one AssistantMessage per block)"]
    r1["Tool result · output, or the policy's refusal<br/>truncated by the harness, not by dex<br/>(dex trims only what it shows: _flatten [:4000])"]
    t2["Assistant turn · next tool_use"]
    r2["Tool result"]
    sub["Subagent call (Claude Task tool) · only its final report comes back<br/>its own window, own calls, own tokens"]
    end1["Final assistant text = the run's result (section 5)"]

    classDef harness fill:#e5e7eb,stroke:#6b7280,color:#111
    classDef brief fill:#dcfce7,stroke:#16a34a,color:#111
    classDef run fill:#fef3c7,stroke:#d97706,color:#111
    classDef summary fill:#fee2e2,stroke:#dc2626,color:#111
    class start brief
    class t1,r1,t2,r2,end1 run
    class sub summary
```

### Subagents

Only Claude has them in a dex task. Its `Task` tool has no auto-approval rule,
so it escalates (`_auto_reason`: "has no auto-approval rule"), which
auto-approve turns into an allow. The subagent runs in a **window of its own**:

```mermaid
sequenceDiagram
    autonumber
    participant M as Main agent
    participant H as Claude Code CLI
    participant P as can_use_tool
    participant S as Subagent
    participant R as TaskRunner

    M->>H: Task(prompt)
    H->>P: can_use_tool(Task) escalates
    P-->>H: allow
    H->>S: a new context: its own system prompt + the prompt it was given
    loop its own calls
        S->>H: tool_use
        H->>P: same can_use_tool, same policy
        H-->>R: messages with parent_tool_use_id (a lane of their own)
        R->>R: _accrue(subagent=True): billed, context_tokens not moved
    end
    S-->>H: its final message
    H->>M: one tool_result: the subagent's report
```

The others have none: OpenCode's `subagent` is denied (`REFUSED`, and
`deny` in the `dex-task` agent), Codex's are switched off (`multi_agent` in
`FEATURES_OFF`, and a message on another thread is dropped), `agy` is not
given them (`antigravity.TOOLS`).

---

## 5. Summaries and compaction

Four mechanisms shrink or replace a window. dex drives the first three; the
fourth is the agent's own.

| Mechanism | Who | When | What replaces the history |
|---|---|---|---|
| Closing summary | dex, every run | each `ResultMessage` | nothing yet: it is stored as the `result` event's `summary` |
| Fresh session from the summary | dex, `compact_follow_ups` on | a follow-up to a task that **succeeded** | the stored summary, quoted in the new brief (`deliver_messages`) |
| `/compact` before a follow-up | dex, `auto_compact` on | a follow-up resuming a successful task's session, **Claude only** | Claude Code's own summary (`_compact`) |
| Auto-compaction | the agent's harness | the window is nearly full | the harness's own summary, mid-turn |

### The closing summary and where it is reused

```mermaid
sequenceDiagram
    autonumber
    participant M as Model
    participant C as Agent client
    participant R as TaskRunner._translate
    participant DB as events (Postgres)
    participant Q as TaskManager
    participant D as Dependencies

    M-->>C: last assistant text of the turn
    C-->>R: ResultMessage(result)
    Note over C: OpenCode, Codex: result = the turn's last text part. Antigravity: its response (_finish_turn)
    R->>R: summary = result[:4000] + _summary_files (changes.files_section)
    R->>DB: result event with summary
    alt a follow-up note, compact_follow_ups on, parent succeeded
        Q->>DB: TaskStore.summary(task)
        Q->>Q: deliver_messages: note = What the previous attempt reported + notes
        Q->>Q: submit(session_id=None, resumed_from=None): a fresh session
    else a task waiting on this one
        D->>DB: statuses with the summary's first line, at most 600 characters (_first_line)
        D->>D: wake_note: "The tasks you waited for are done" + each summary
    else task_status, find_tasks from another agent
        D-->>M: the summary as a tool result in that agent's window
    end
```

### Fresh session from a summary (Compact on)

```mermaid
block-beta
    columns 1
    sys["System prompt, scope rules, tools (section 1)"]
    guide["Brief 1–4 · guides, how a task works, skills, finish<br/>generation_prompt, unchanged"]
    req["Request = the original problem"]
    fu["Follow-up · This is a fresh session<br/>What the previous attempt reported = stored summary + files changed<br/>The operator has since asked for = the notes<br/>TaskManager.deliver_messages, continued_problem"]
    this["This task · same directory, existing files listed"]
    work["New work: it re-reads the files the summary names"]

    classDef harness fill:#e5e7eb,stroke:#6b7280,color:#111
    classDef brief fill:#dcfce7,stroke:#16a34a,color:#111
    classDef run fill:#fef3c7,stroke:#d97706,color:#111
    classDef summary fill:#fee2e2,stroke:#dc2626,color:#111
    class sys harness
    class guide,req,this brief
    class fu summary
    class work run
```

This works the same on every agent: it is only text in a new brief.

### `/compact` before a follow-up (Claude only)

```mermaid
sequenceDiagram
    autonumber
    participant R as TaskRunner.run
    participant C as ClaudeSDKClient
    participant H as Claude Code CLI
    participant M as Model

    R->>R: _compacts: resumed, not woken, not chat, parent succeeded, auto_compact, not agy/oc/codex
    R->>C: _query("/compact")
    H->>M: the old history + summarise it
    M-->>H: the summary
    H->>H: replace the history with the summary
    H-->>R: SystemMessage compact_boundary, then a ResultMessage (not a turn of the task)
    R->>C: _query(brief with the Follow-up notes)
```

```mermaid
block-beta
    columns 1
    sys["System prompt, scope rules, tools"]
    s["Claude Code's summary of the old session<br/>written by the model on /compact<br/>TaskRunner._compact"]
    new["Brief with the Follow-up notes<br/>deliver_messages then generation_prompt"]
    work["New work"]

    classDef harness fill:#e5e7eb,stroke:#6b7280,color:#111
    classDef brief fill:#dcfce7,stroke:#16a34a,color:#111
    classDef run fill:#fef3c7,stroke:#d97706,color:#111
    classDef summary fill:#fee2e2,stroke:#dc2626,color:#111
    class sys harness
    class s summary
    class new brief
    class work run
```

The two settings do not stack: with `compact_follow_ups` on, a successful
parent's follow-up has no session to resume (`resumed_from=None`), so
`_compacts` is false and nothing is sent.

### The agent compacting itself when the window fills

Every harness dex runs keeps its own session and compacts it on its own when
the window is nearly full. dex neither starts nor configures it, beyond the
window size it declares for a local model:

| Agent | Threshold | What dex sees |
|---|---|---|
| Claude | Claude Code's auto-compact, near the model's window | a `compact_boundary` system message, ignored outside `_compact` (`_translate` returns on every other subtype) |
| OpenCode | near the model's `limit.context`: the Go plan's, or a local entry's `context` in `local-models.json` (default 65,536, `local_provider`) | a model request with no usage of its own (`SystemMessage model_request` from the relay, design 09), then `context_tokens` falling on the next call |
| Codex | the thread compacts itself when full (design 29) | only the falling token counts |
| Antigravity | `agy`'s own | only the falling token counts |

```mermaid
sequenceDiagram
    autonumber
    participant H as OpenCode (opencode run)
    participant B as Bridge relay
    participant L as Local model server
    participant R as TaskRunner

    H->>B: a call with ~169k tokens of history
    B->>L: forwarded (steady_system keeps the prefix cacheable)
    L-->>H: reply, tokens counted
    H-->>R: step_finish usage, context_tokens about 169k (_accrue)
    Note over H: history is near limit.context
    H->>B: a summary request: the history + a summarise instruction, no tools
    B-->>R: model_request (timed, no usage reported)
    L-->>H: the summary, an Objective section and the rest
    H->>H: the session continues from the summary
    H->>B: the next call: system + tools + summary + the last messages
    H-->>R: step_finish usage, context_tokens about 35k
```

```mermaid
block-beta
    columns 1
    sys["OpenCode's system prompt + tools (agent dex-task)"]
    s["OpenCode's summary of everything before<br/>written by the same model, in its own words<br/>this now stands in for the first user message too:<br/>the dex-instructions block and the brief are in it only as far as the summary kept them"]
    tail["What came after the compaction point"]
    work["New work"]

    classDef harness fill:#e5e7eb,stroke:#6b7280,color:#111
    classDef run fill:#fef3c7,stroke:#d97706,color:#111
    classDef summary fill:#fee2e2,stroke:#dc2626,color:#111
    class sys harness
    class s summary
    class tail,work run
```

That last block is the reason it matters most on OpenCode and Antigravity:
there, dex's rules live in the first user message, which compaction folds into
the summary. On Claude and Codex they are in the system position and survive
it whole.

---

## Improvement opportunities

- **A resume re-sends the whole brief.** `TaskRunner.run` builds the brief and
  sends it as the first new turn whenever the session is resumed (pause,
  restart, Resume in place, follow-up), so the session holds it twice — on a
  local model with a 64k window that is a large share of it. A resumed session
  needs only what changed: the note, or `RESUME_NUDGE`.
- **Compaction by the agent is invisible.** `compact_boundary` outside
  `_compact`, and OpenCode's summary request, raise no event; the task panel
  shows only the context number falling. One `text` event ("the agent
  compacted its context") would make the moment the user saw explainable
  without reading token counts.
- **dex's rules can be summarised away on OpenCode and Antigravity.** They
  travel in the first user message (`dex-instructions`), which the agent's own
  compaction rewrites. Re-sending them after a compaction, or OpenCode's
  `--agent` prompt carrying them, would keep them verbatim.
- **Two settings, two meanings of "compact".** `auto_compact` (Claude
  `/compact` on the resumed session) and `compact_follow_ups` (a fresh session
  from the stored summary) sound alike and do different things. The second
  works for every agent; the first could go.
- **Design 03 lags the code.** Its "Auto-compact for follow-ups" section lists
  Antigravity and OpenCode as resuming whole; `_compacts` excludes Codex too.
- **No size is shown per block.** Nothing measures how many tokens the guide,
  the skills list or a `SKILL.md` take; `context_tokens` is a single total.
  Counting the brief's sections when it is built would show which block to
  shrink.
