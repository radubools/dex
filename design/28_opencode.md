# 28 · OpenCode: tasks on the OpenCode Go subscription

The third subscription dex runs tasks on, after Claude's and Google's
(design/27): **OpenCode Go**, through the OpenCode CLI (`opencode`, 2.2),
signed in with the operator's OpenCode account. Only the Go plan's models
(provider `opencode-go`) are offered: OpenCode's pay-as-you-go Zen models and
every provider reached with an API key are not, and every `*_API_KEY` variable
is removed from the environment `opencode` runs in.

## Choosing it

Settings pick a provider first (Claude, Gemini, OpenCode), then one of its
models. OpenCode's are the Go plan's, read from `opencode api --standalone GET
/api/model` at most every ten minutes, with Qwen3.8 Flash first
(`opencode.PREFERRED`). Their ids carry a prefix, `oc:<model>`. As for
Antigravity, only tasks run on it: planning, routing, design turns and
surveys stay on Claude (`api._planning_model`, `TaskRunner._client`), and no
Claude usage window holds its work (`usage.counts_for`).

dex's effort picks the model's variant (`opencode.variant`): the same one,
else the nearest below, so `high` on Qwen3.8 Flash runs `#medium`.

## How a run works

`opencode.OpencodeClient` has `ClaudeSDKClient`'s interface. Each turn is

    opencode run --standalone --format json --thinking --agent dex-task \
        -m opencode-go/<model>#<variant> [--session <id>] -- <prompt>

in the task's directory (and `PWD`, which is what OpenCode reads), on a
private server of its own. Its events become Claude SDK messages:

| OpenCode event | dex |
|---|---|
| `text` | a text block (whole parts: `run` prints no deltas) |
| `reasoning` | a thinking block, shown as the step's reasoning |
| `tool_use` | the call's outcome (the call itself is shown by the gate, as it starts) |
| `step_finish` | that model call's tokens, as a usage message |

The session id is the task's session; a follow-up turn resumes it with
`--session`. dex's appended system prompt goes in front of the first message.
An interrupt ends the process; the session stays.

A task continued on another agent than the one that kept its session (a
Claude task asked for a change while OpenCode is the model, say) cannot resume
it: OpenCode only takes its own `ses_…` ids. `TaskRunner._resumable` tells
the three apart (OpenCode's prefix, Antigravity's conversations on disk,
Claude the rest), and such a task starts a fresh session with the whole brief,
told that its earlier work is in the directory.

A run whose dex server went away (a reload) has nobody to answer its gate:
at its next tool call the plugin finds nothing listening and ends the run
(Antigravity's hook does the same), so it cannot go on working beside the
session dex resumes. A run that prints nothing at all for 150 seconds is
killed and started once more.

## Permissions

dex's own config directory, `~/.dex/opencode` (`OPENCODE_CONFIG_DIR`, so the
operator's config is never read by a dex session), holds:

- `modes/dex-task.md`, a primary agent with OpenCode's approval open (nobody
  could answer it) and `question`, `subagent`, `skill` and `browser` denied;
- `plugins/dex-gate.js` (`tools/opencode_gate.js`), whose `execute.before`
  hook posts every call to the task on 127.0.0.1 at a random port and path
  (`DEX_OC_GATE`). The task puts it to the policy as the Claude tool it
  amounts to (`shell` Bash, `write` Write, `edit` Edit, `read` Read, `glob`,
  `grep`, `webfetch`, `websearch`, `<server>_<tool>` `mcp__<server>__<tool>`).
  A deny's reason reaches the model as the tool's error; a confined command
  runs confined (the hook rewrites its input); a gate that cannot reach dex
  denies.

`patch` is refused with a pointer to `edit` and `write`: it changes many files
in one call, which the policy judges one at a time, and its permission is
`edit`'s, so it cannot be turned off alone. OpenCode's code tool, `execute`,
runs unasked: it is how the model calls MCP tools, and each call it makes
comes to the gate itself, shown in the panel under its own name.

## dex's tools

The same in-process MCP servers a Claude run gets, served over streamable
HTTP on the task's loopback port (`antigravity.Bridge`) and named to OpenCode
as remote servers in the run's inline config (`OPENCODE_CONFIG_CONTENT`).

## Usage and cost

Every model call's tokens are counted once, by message id: from the stream as
each call ends, and the last call's from the session's export
(`opencode session export`), since `run` exits before printing it. OpenCode
prices a Go call at nothing, but the plan counts its limits in dollars at each
model's list rates, cache included and tiered by prompt length; dex prices
every call at those rates (`opencode.rates`, `pricing.estimate`), which is the
task's cost. A turn's result carries no total, so that estimate stands.

## Signing in

Once per machine: `brew install opencode`, then `opencode auth login` with an
account that has Go. A model is offered as runnable once OpenCode lists the
plan's models.

## Improvement opportunities

- **No one-liners.** OpenCode 2.2's tools take no description and title each
  call with its tool's name, so the panel words every step from its
  arguments; the model's reasoning, shown per step, is the closest it says.
- **No live text.** `opencode run --format json` prints whole parts; the
  server's event stream (`opencode serve`) would give deltas.
- **Thinking tokens read zero** on the Go models tried: the provider does not
  report them, though their reasoning text arrives.
- **Starts sometimes stall.** A few OpenCode starts hung before their server
  answered, printing nothing, right after the gate's file changed; it could
  not be reproduced later. `install` loads the config after a change
  (`_warm`) and a silent run is restarted once (`STARTUP_TIMEOUT_S`).
- **Calls from one code-tool run share its output.** When the model calls
  two of dex's tools in one `execute`, OpenCode reports one result for both,
  and each step shows it.
- **An orphaned run works until its next tool call**, text and reasoning
  included, before it finds dex gone.
- **Only OpenCode retries.** A provider's passing failure (an upstream
  error, an overload, a rate limit, a dropped connection: `RETRYABLE`) is
  tried again on the same session, told to carry on, up to five times after
  5, 10, 20, 40 and 80 seconds; the task panel says each one. Antigravity's
  runs do not retry yet, and a message that merely looks transient (anything
  "unavailable") is retried too.
- **Its limits are not watched.** A Go limit fails the task rather than
  pausing it.
