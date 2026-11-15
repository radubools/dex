# 29 · Codex: tasks on the ChatGPT subscription

The fourth subscription dex runs tasks on, after Claude's, Google's (design/27)
and OpenCode Go's (design/28): **ChatGPT**, through OpenAI's Codex CLI
(`codex`, 0.147), signed in with the operator's ChatGPT account. Every API-key
variable (`*_API_KEY`, `OPENAI_*`, `CODEX_*`) is removed from the environment
Codex runs in, and a model is offered as runnable only when Codex is signed in
with ChatGPT, not with a key (`account/read` says `chatgpt`).

## Choosing it

Settings pick a provider first (Claude, Gemini, OpenCode, **OpenAI**), then one
of its models. OpenAI's are every model Codex lists for the account
(`model/list`, read at most every ten minutes, hidden ones included) at every
reasoning effort each offers, as one id each: `codex:<model>#<effort>`, so
`codex:gpt-5.6-sol#medium`. Codex's own approval reviewer
(`codex-auto-review`) is not an agent and is left out. Switching the picker
to OpenAI chooses its first entry, **GPT-5.6-Sol at medium**
(`codex.DEFAULT_MODEL`, `DEFAULT_EFFORT`). The effort is the model id's, not
dex's global effort setting; an id without one runs at medium, and an effort
the model does not offer runs at the nearest it offers below.

As for the other subscriptions, only tasks (game chats included) run on it:
planning, routing, design turns and surveys stay on Claude
(`api._planning_model`, `TaskRunner._client`).

## How a run works

`codex.CodexClient` has `ClaudeSDKClient`'s interface. A run is one

    codex app-server -c <dex's flags>

over stdio: JSON-RPC, the protocol Codex's IDE extensions and desktop app use.
`codex exec --json` was the other way in; the app-server was chosen because
its process outlives a turn, it stops a turn without ending (`turn/interrupt`),
streams reasoning summaries, and reports token counts and rate-limit windows
as they change. A task's session is a Codex **thread**: `thread/start` (or
`thread/resume` with the task's session id, for a follow-up attempt or a
paused task), then one `turn/start` per dex turn, at the model id's effort and
with `summary: detailed`. dex's appended system prompt is the thread's
`developerInstructions`, so Codex keeps its own base instructions (what teaches
the model its tools).

Its notifications become Claude SDK messages:

| Codex notification | dex |
|---|---|
| `item/agentMessage/delta` | text deltas, one block per message item |
| an `agentMessage` with `phase: commentary` | also the task's one-line activity (its short progress note) |
| `item/reasoning/summaryTextDelta` | thinking deltas, a block per summary part |
| a `reasoning` item whose summary did not stream | a thinking block |
| the gate's question (below) | the tool call, as it starts |
| `item/completed` for a command, patch or MCP call | its outcome: output and exit code, files changed, MCP content |
| `webSearch`, `imageView` items (never gated) | shown from the item |
| `turn/plan/updated` | a `TodoWrite`, so the plan reads as Claude's to-do list |
| `thread/tokenUsage/updated` | that model call's tokens, as a usage message |
| `account/rateLimits/updated` | a `RateLimitEvent` per window |
| `turn/completed` | the turn's result |

A message on another thread (a subagent's) is not the task's and is dropped.

## Feeding it dex's context

A Codex run gets exactly what a Claude run does, the same way: the task's
brief is the prompt dex builds for any agent (`prompts.py`), which carries the
project's `AGENTS.md`, the enabled skills, one line each with the path of the
version materialised into `assets/<project>/utils/` and the instruction to
read a skill's `SKILL.md` before using it, and the project's rules. The scope's
system prompt (`GENERATION_SYSTEM` and the rest) is the developer
instructions. Codex's own instruction discovery is switched off
(`project_doc_max_bytes=0`), as Claude's is (`setting_sources=[]`), so nothing
from the task's directory or the operator's `~/.codex/AGENTS.md` is read twice
or instead. dex's skills are not registered as Codex skills: Codex's skill
format would only change how the same `SKILL.md` gets read.

## Permissions

dex decides every call, with the same `PermissionPolicy` as a Claude run.
Codex's own approval and sandbox are opened for the session
(`approvalPolicy: never`, `sandbox: danger-full-access`) and a PreToolUse
hook does the deciding:

- The hook (`tools/codex_hook.py`, copied to `~/.dex/codex/hook.py`) is named
  in dex's flags only (`hooks.PreToolUse`, matcher `*`), run on
  `/usr/bin/python3`, so the operator's own Codex sessions never load it. It
  posts each call to the task, on 127.0.0.1 at a random port and path
  (`DEX_CODEX_GATE`), and prints dex's decision in Codex's format. A hook that
  cannot reach dex denies; one that finds nothing listening ends the run, as
  Antigravity's does.
- The task puts the call to the policy as the Claude tools it amounts to
  (`claude_calls`): a shell command is `Bash`; an `apply_patch` is one call
  per file it touches (`Write` for a new file, `MultiEdit` with one edit per
  hunk for a change, `Bash` `rm`/`mv` for a delete or move), and is allowed only
  if every one is; an MCP tool is already named `mcp__<server>__<tool>`;
  `view_image` is `Read`. Each is shown, and emits its diff, as for Claude. A
  confined command runs confined (the hook's `updatedInput`). Codex's own plan
  tool runs unasked; anything else the policy has no name for asks the
  operator.
- Codex trusts a hook only once it has been reviewed, by a hash of how it is
  named. dex's is always named the same way, and `codex.install` records its
  trust once, through Codex's own config API (`config/value/write`): one
  `hooks.state."/<session-flags>/config.toml:pre_tool_use:0:0"` entry in
  `~/.codex/config.toml`. Trust passed as a flag (`-c hooks.state…`,
  `bypass_hook_trust`) is not honoured for session hooks.
- Nothing of the operator's own Codex setup reaches a dex session: plugins,
  apps, browser and computer use, image generation, subagents, goals and
  memories are switched off by flags, every MCP server their config names is
  disabled, and the notifier is unset, for dex's process only. Web search
  stays: it is hosted by OpenAI, never reaches the hook, and is read-only, like
  Claude's `WebSearch`.

With approvals off, Codex should ask the client nothing; if it does
(`requestApproval`), a command goes to the policy, and a patch, already judged
file by file at the gate, is accepted.

## dex's tools

`ask_user`, `start_task` and the rest are the same in-process MCP servers a
Claude run gets, served over streamable HTTP on the task's loopback port
(`antigravity.Bridge`) and named to Codex in the flags
(`mcp_servers.dex.url`).

## Usage and cost

Each model call's tokens arrive as `thread/tokenUsage/updated`; its `last`
breakdown is that call's (Codex counts cached input inside input and reasoning
inside output, so dex's `input_tokens` is input less cached, and reasoning is
`thinking_tokens`, a subset of output, as Claude's thinking is). A repeated
reading of the same running total is counted once. Codex reports no dollars,
so each call is priced at OpenAI's list price for the model
(`pricing.SUBSCRIPTION_PRICES`, by family, effort dropped), which is the
task's cost estimate. Collected on 15 November 2026 from aggregators quoting
OpenAI's rates, and edited by hand:

| Model | Input / output per 1M | Cached input | Changes |
|---|---|---|---|
| GPT-5.6-Sol | $4 / $20 | $0.40 (assumed 10%) | $5 / $30, cached $0.50, from 1 Dec 2026 |
| GPT-5.6-Terra | $2 / $12 | $0.20 | |
| GPT-5.6-Luna | $0.20 / $1.20 | $0.02 | |

GPT-Reserve has no published price and is estimated at dex's default.
Sources: [CloudZero](https://www.cloudzero.com/blog/gpt-5-6-pricing),
[morphllm](https://www.morphllm.com/gpt-5-6-sol-vs-terra-vs-luna),
[CometAPI](https://www.cometapi.com/gpt-5-6-pricing/),
[OpenAI's community announcement](https://community.openai.com/t/announcing-a-major-price-drop-for-5-6-terra-and-luna-and-fast-mode-for-5-6-sol/1388484).

## Limits

Unlike the other two CLIs, Codex reports the plan's windows as it goes: every
model call brings an `account/rateLimits/updated` (percent used, window length,
reset), recorded like Claude's readings under `codex_<span>` (`codex_5h`,
`codex_weekly`; a bucket other than `codex` is named in it). They hold Codex
work only, and no Claude window holds it (`usage.window_provider`,
`counts_for`). While a Codex model is the default, the usage watcher reads the
windows on every check (`account/rateLimits/read`, which costs no model call:
`UsageWatcher.read_codex`) instead of paying for a Claude probe, so a spent
window holds work at 95%, and a reset releases it, with nothing running. A
refusal (`usageLimitExceeded`) reads as a usage-limit error with the window's
reset, so the task pauses rather than fails. A dropped connection
(`responseStreamDisconnected` and the like) reads as one, so the runner tries
the turn again on the same thread.

## Signing in

Once per machine: `npm install -g @openai/codex` (or the Codex app), then
`codex login` with the ChatGPT account. dex runs the native binary inside the
npm package, so a run does not need `node` on its PATH. Codex keeps its home,
`~/.codex`: that is where its sign-in lives, and a second home would need a
second sign-in. dex's threads are therefore saved with the operator's own and
listed in Codex's apps.

## Improvement opportunities, and what Codex cannot do that Claude can

- **The status line is the model's, the tool titles are dex's.** Codex
  writes no per-call description (Claude's Bash `description`), so a command
  is titled from its text; the commentary notes are the closest it says, and
  become the activity.
- **Reasoning arrives as summaries**, not the full thinking, and on short
  turns often none: low efforts on Luna stream nothing.
- **No per-call MCP limits or tool allow-lists.** A survey's four-tool limit
  (`allowed_tools`) needs Claude's CLI, so surveys stay on Claude; Codex has
  no equivalent of `tools=[…]` (the todo split and ideation's reduced tool
  sets), so those scopes are held only by the policy.
- **No max-turns cap.** Codex has no `max_turns`; a runaway turn is stopped
  only by its context window or an interrupt.
- **No session compaction on follow-up.** dex's auto-compact before a
  follow-up is Claude's `/compact`; a Codex thread compacts itself when full,
  but a follow-up is not compacted first.
- **Web search is ungated.** Hosted tools never reach the hook, so the policy
  cannot refuse one; it is shown after the fact.
- **A call waiting on the operator does not stop the model.** The GPT-5.6
  models call tools from a code cell; while the hook holds a call for an
  approval, the cell times out and the model goes on reasoning and writing
  ("waiting for its result") until the answer lets the call run. Claude
  simply waits.
- **It reaches for the interpreter it was told about.** Told by dex's system
  prompt which Python to use, Sol ran even a listing as `python -c …`, which
  the policy flags as a chained command and asks about; a Claude run would
  have used `ls`.
- **Overloads fail the task.** "Selected model is at capacity"
  (`serverOverloaded`) was seen on Sol in testing; like other providers'
  overloads it is not retried.
- **Prices are a hand-kept table**, and Codex's credits (`credits.balance`)
  are not shown.
- **The operator's config is shared.** dex's flags switch off what they know
  of; a new kind of plugin or hook the operator adds to `~/.codex` could reach
  dex sessions until it is listed there.
