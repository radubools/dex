# 27 · Antigravity: tasks on the Google subscription

dex runs tasks on subscriptions, never on per-call API keys. Claude is one;
Google's is the other, through the **Antigravity CLI** (`agy`). Gemini CLI was
the first choice and stopped serving personal accounts on 18 June 2026.

## Choosing it

Settings' model pickers (the global one and each project's) list Claude's
models and, under "Google subscription (Antigravity)", every model the signed-in
plan offers (`agy models`, read at most every ten minutes): Gemini, and the
Claude and GPT models Google serves. Their ids carry a prefix, `agy:<model>`,
which is how the runner, the usage watcher and the pickers tell them apart. A
model that cannot run here (no `agy`, or not signed in) is listed, disabled,
with what to do.

Only tasks run on it. Planning, routing and design-chat calls are single
Claude calls (`api._planning_model`), and a survey stays on Claude because its
four-tool limit is enforced by Claude's CLI (`allowed_tools`). Claude's usage
windows never hold Antigravity work (`usage.counts_for`). `agy` reports no
dollars, so its result carries no total and each call is priced at the model's
public list price (`pricing.SUBSCRIPTION_PRICES`, by family, effort suffix
dropped), the same table OpenCode falls back on for a model its plan gives no
price. Its tokens are counted as for Claude.

The prices were collected once, on 3 November 2026, from aggregators quoting
Google's and Anthropic's rate sheets, and are edited by hand:

| Family | Input / output per 1M | Cache read | Changes |
|---|---|---|---|
| Gemini 4 Argon | $2 / $10 | $0.10 | $4 / $20 from 1 Dec 2026 |
| Gemini 3.8 Flash | $0.75 / $3.75 | $0.075 (assumed 10%) | $1.50 / $7.50 from 1 Jan 2027 |
| Claude Opus 5.5 | $4 / $20 | $0.40 | |
| Claude Sonnet 5.5 | $2 / $10 | $0.20 | |
| Claude Haiku 5.5 | $0.10 / $0.50 | $0.01 | |
| GPT-OSS 120B | $0.09 / $0.36 (Vertex AI) | | |

Sources: [eesel on Argon](https://www.eesel.ai/blog/gemini-4-argon-pricing),
[morphllm's Gemini table](https://www.morphllm.com/gemini-api-pricing),
[Apidog on Sonnet 5.5](https://apidog.com/blog/claude-sonnet-5-5-pricing/),
[cloudprice on GPT-OSS](https://cloudprice.net/models/openai-gpt-oss-120b).

## How a run works

`antigravity.AgyClient` has `ClaudeSDKClient`'s interface and is built from
the same `ClaudeAgentOptions`, so `TaskRunner.run` and `_converse` drive it
unchanged (`TaskRunner._client` picks it by model). It runs

    agy --input-format stream-json --output-format stream-json \
        --agent dex-task --settings '<session permissions>' --model <model> \
        [--conversation <id>] -p=

in the task's directory, writes one `{"event": "user", ...}` line per turn and
turns `step_update` and `result` events into Claude SDK messages: text deltas
as `StreamEvent`s, each tool call as an `AssistantMessage` and its outcome as
a `UserMessage`, one `ResultMessage` per turn. dex's appended system prompt
goes in front of the first message, since `agy` keeps its own.

The conversation id is the task's session: a follow-up attempt resumes it
with `--conversation`. `agy` cannot stop a turn and keep its process, so an
interrupt (a message typed while it works) ends the process and the next turn
starts it again on the same conversation.

## Permissions

In print mode `agy` can ask nobody, so dex decides every call, with the same
`PermissionPolicy` as a Claude run:

- Each session runs as `dex-task`, a custom agent dex writes to
  `~/.gemini/config/agents/dex-task.md` (`antigravity.install`). Its plugin, in
  `~/.dex/antigravity/dex-plugin`, carries a PreToolUse hook for every tool.
  The operator's own `agy` sessions load neither.
- The hook (`tools/agy_hook.py`) posts the call to the task, on 127.0.0.1 at a
  random port and path (`DEX_AGY_GATE`). The task puts it to the policy as the
  Claude tool it amounts to (`claude_shape`: `run_command` is Bash,
  `write_to_file` Write, `replace_file_content` Edit, `view_file` Read,
  `call_mcp_tool` `mcp__<server>__<tool>`), which emits the diff and may ask
  the operator, as for Claude. A deny's reason reaches the model. A command
  the policy confines runs confined (the hook's `overwrite`). A hook that
  cannot reach dex denies.
- `agy`'s own approval is opened for the session only (`--settings`, never
  saved), so that the hook is what decides.
- The agent has files, commands, the web and MCP only: browser automation,
  subagents and schedules are not a dex task's, nor anything the policy can
  judge.

## dex's tools

`ask_user`, `start_task`, `wait_for_tasks` and the rest are the same
in-process MCP server a Claude run gets, served over streamable HTTP on the
task's loopback port. `agy` reads MCP servers from static config, so the
plugin names a stdio relay (`tools/agy_mcp.py`) that forwards each JSON-RPC
line to the address in `DEX_AGY_MCP`.

## Signing in

Once per machine: install `agy` (`curl -fsSL
https://antigravity.google/cli/install.sh | bash`), run `agy`, choose Google
OAuth. dex removes every API-key variable from the environment it starts `agy`
in, so a run is always on the subscription.

Whether Google's terms allow a personal plan to be driven by another program
is not settled: dex runs the unmodified CLI, locally, for one operator, and
never touches its tokens.

## Improvement opportunities

- **No live reasoning.** `agy` streams only a step's thinking token count;
  its summary is read from the conversation's transcript once the step ends.
- **Reads have no one-liner.** `agy` writes a summary for commands and edits
  (`toolSummary`, `Description`) but none for reads and searches, so the
  panel words those from their arguments.
- **An interrupt restarts the process**, since `agy` cannot stop a turn and
  keep running.
- **Prices are a hand-kept table.** Nothing refreshes them; a new model
  family is priced at dex's default until it is added.
- **Its usage limits are not watched.** A Google plan limit fails the task
  rather than pausing it as a Claude window does.
