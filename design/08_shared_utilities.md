# 08 · Shared utilities

## Summary

> **`utils/API.md` earns its place, and is still generated.** It is the
> *signature* index — every public function, its arguments, and one line about
> it, at about a tenth of the modules' bytes. A `SKILL.md` says what a skill is
> *for* and what it will not do; the index says what you can actually call.
> Different jobs, and a task needs both.
>
> Each module's heading now names the skill it came from
> (``## `rig` — from `yoga-figure` ``), because `utils/` is materialised from
> several skills and a promotion targets one. Without it nothing joins the
> skills in the brief to the module names a reader has in hand.
>
> **Utilities ship inside skills now.** A module lives in
> `skills/<name>/<version>/utils/`, and `assets/<project>/utils/` is
> materialised from the skills a project has enabled — so it is derived state,
> and a task promoting a helper writes to the skill. Everything below still
> describes the protocol: when a helper is worth sharing, how it is proposed,
> and how a refusal is recorded. See [15](15_skills.md) for where it lands.


Tasks in one project keep writing the same helpers: a validator every package
needs, a conversion step, a geometry solver. Copied from package to package they
drift into six subtly different versions. The **shared utilities protocol** lets
a task promote a helper into the project's `utils/` — but only **at the end of
the task, after the code has actually run**, and only with a decision recorded
where the operator can see it.

The protocol lives almost entirely in **the project guide**, which every agent
reads. dex supplies three mechanisms around it: a generated index so finding a
utility is cheap, an MCP tool to check whether proposals are switched on, and an
automatic answer to the promotion question while they are.

| Piece | Where |
|---|---|
| The protocol text | `AGENTS.template` → each project's `AGENTS.md` |
| Index generator | `src/dex/tools/utils_api.py` |
| Index refresh before every task | `TaskRunner._refresh_utils_index` |
| `utility_proposals_enabled`, auto-answer | `TaskRunner._ask_user_server`, `_auto_answer` |
| Setting and its concurrency coupling | `PUT /api/settings`, `SettingsStore.UTILITY_PROPOSALS` |

---

## Project layout

```mermaid
flowchart TD
    subgraph proj ["assets/yoga/"]
        G["AGENTS.md"]
        subgraph gsec ["guide sections"]
            RO["Shared utilities roster<br/>between utils:begin / utils:end<br/>— generated"]
            KL["Kept local<br/>— operator's veto, hand-kept"]
        end
        subgraph utils ["utils/"]
            API["API.md<br/>signatures + one-liners<br/>— generated"]
            M1["skeleton.py"]
            M2["rig.py"]
        end
        PK["warrior-ii/pose_build.py"]
    end
    G --- gsec
    PK -->|"import from utils"| M1
    M1 -->|"docstrings parsed"| API
    M1 -->|"first docstring line"| RO
```

Three tiers of increasing cost, read only as far as needed:

| Tier | Content | Cost |
|---|---|---|
| Roster in `AGENTS.md` | One line per module | Free — already in the brief |
| `utils/API.md` | Every public signature and constant, first docstring line | ≈ 11% of the source |
| The module itself | Everything | Last resort, one module |

This tiering came from measurement: yoga's `utils/` grew to 21.9 KB in a quarter
of an hour, and every task that glanced at it paid for all of it.

---

## Generating the index

```mermaid
flowchart LR
    SRC["utils/*.py"] --> AST["ast.parse<br/>— never imported"]
    AST --> PUB["public names only<br/>no leading underscore"]
    PUB --> FN["def → signature + first doc line"]
    PUB --> CL["class → name + first doc line"]
    PUB --> CO["UPPER_CASE = → constant"]
    FN & CL & CO --> APIMD["utils/API.md"]
    AST --> MOD["module first doc line"]
    MOD --> ROSTER["roster rows between<br/>utils:begin / utils:end"]
```

- **Parsed, not imported.** A module with a heavy import or a syntax error costs
  nothing and cannot execute anything.
- **Generated, not hand-kept.** A hand-written index drifts the moment someone
  edits a module and forgets.
- **Refreshed before every task** by the runner, so it cannot fall behind a
  module a previous task changed. It writes only on a real difference, so the
  steady state touches nothing and the watcher stays quiet.
- **Opt-in for the roster.** A guide without the marker comments is left exactly
  as it is — a hand-edited file is never guessed at.
- To change what a row says, change the module's **docstring**. Editing the
  table is undone by the next task.

---

## The protocol, from the agent's side

```mermaid
flowchart TD
    START([Task needs a helper]) --> RR["Read roster + Kept local<br/>(already in the brief)"]
    RR --> LOOKS{"A row looks relevant?"}
    LOOKS -- yes --> APIR["Read utils/API.md"]
    LOOKS -- no --> LOCAL
    APIR --> FIT{"Does it do the job?"}
    FIT -- fully --> USE["Import it · carry on<br/>nothing to propose"]
    FIT -- nearly --> COMP["Call it for what it covers,<br/>do the rest locally.<br/>Do not edit it now."]
    FIT -- no --> LOCAL["Write it locally,<br/>inside the package"]
    COMP --> WORK
    LOCAL --> WORK
    USE --> WORK
    WORK["Build the deliverable ·<br/>run every check"] --> GREEN{"All green?"}
    GREEN -- no --> WORK
    GREEN -- yes --> END(["End-of-task review"])
```

**Nothing goes into `utils/` mid-task.** An untested module promoted into shared
space is worse than none, and stopping to ask halfway interrupts work that has
not yet shown the helper is worth sharing.

### The end-of-task review

```mermaid
flowchart TD
    E([Everything green]) --> CAND{"Something worth raising?<br/>generic + a named second user,<br/>or an additive change to a module"}
    CAND -- no --> SUM([Summary])
    CAND -- yes --> VETO{"Listed in Kept local?"}
    VETO -- yes --> SUM
    VETO -- no --> EN["mcp__dex__utility_proposals_enabled"]
    EN -- disabled --> NOTE["Ask nothing ·<br/>one summary line on<br/>what would have been shared"]
    NOTE --> SUM
    EN -- enabled --> ASK["mcp__dex__ask_user<br/>kind = utility<br/>1. Put it in the project utils/<br/>2. Keep it in the task directory"]
    ASK --> ANS{"answer"}
    ANS -- promote --> P1["write into utils/ with a<br/>one-line docstring"]
    P1 --> P2["import from utils/ ·<br/>delete the local copy"]
    P2 --> P3["re-run checks from<br/>the new home"]
    P3 --> P4["regenerate the index"]
    P4 --> SUM
    ANS -- keep --> SUM
```

Rules that make shared code stay shared:

| Rule | Because |
|---|---|
| **Return data, not verdicts or prose** | The next caller wants a different verdict and can only get it by editing |
| **Take a default, not a rule** | Thresholds belong in keyword arguments |
| **Do one thing** | Two composable functions beat one with a mode flag |
| **A second knob for one caller means it is not this function** | A utility that grows a knob per package is edited by every task |
| **Changes must be additive** | Packages you cannot see import it, and you cannot test them |
| **Prefer composing to changing** | Calling it and finishing locally is the utility working as intended |

---

## What dex does

### Answering the question automatically

```mermaid
sequenceDiagram
    autonumber
    participant A as Agent
    participant R as TaskRunner
    participant S as Settings
    participant B as EventBus
    actor O as Operator

    A->>R: utility_proposals_enabled
    R->>S: read now
    S-->>R: on
    R-->>A: "enabled"
    A->>R: ask_user {kind: utility, options: [Put…, Keep…]}
    R->>B: question
    R->>S: read now
    alt still on
        R->>B: question_answered {answer: options[0], auto: true}
        R-->>A: "Put it in the project utils/"
    else switched off since
        R->>R: park → awaiting_input
        O->>R: answer
        R->>B: question_answered
        R-->>A: answer
    end
```

The question is still **asked and recorded** when dex answers it. That keeps an
audit trail in the task panel without interrupting the operator for a decision
they already made by leaving the setting on.

Both the tool and the auto-answer read the setting **at the moment they run**,
not at task start, so switching it off silences runs already in flight.

The guide fixes `kind` and the option order because they are how dex recognises
the question and which option is affirmative. Get either wrong and the question
goes to the operator as an ordinary interruption — a safe failure.

### Approval for the writes

Promotion writes land outside the task directory, so each still passes through
the permission policy ([03](03_agent_runner.md)) and stops for approval unless
auto-approve is on. **A declined write is itself the answer**: the helper goes
back into the package, `utils/` is left as it was, the checks run again, and the
summary says so.

A project-scoped task already owns the project directory, so its writes need no
approval — it still asks, for the record.

---

## The setting and concurrency

```mermaid
stateDiagram-v2
    direction LR
    state "Proposals ON<br/>task_concurrency = 1" as ON
    state "Proposals OFF<br/>task_concurrency = 6" as OFF
    [*] --> ON: default
    ON --> OFF: toggle off
    OFF --> ON: toggle on
    note right of ON
        A promotion must land before the
        next task reads the index, so
        tasks run one at a time
    end note
```

The loop only works **in sequence**. Two tasks running side by side each read
the index before either promotes, and both write their own copy. Turning
proposals on therefore sets task concurrency to 1; turning them off restores 6.
An explicit `task_concurrency` in the same request is applied afterwards and
wins, and `rebalance()` runs immediately so the new limit takes effect at once.

---

## The veto

The **Kept local** table is the operator's, and the only hand-maintained part of
the protocol. A row there closes the question permanently: a task finding its
helper listed leaves it in the package and says nothing more.

This is what makes a "no" stick. Without a record, every future task that wrote
the same helper would ask again.

```mermaid
flowchart LR
    T1["Task A proposes<br/>check_symmetry"] -->|operator: keep local| TBL["Operator adds a row<br/>to Kept local"]
    TBL --> T2["Task B writes<br/>check_symmetry"]
    T2 --> R{"in Kept local?"}
    R -- yes --> Q(["stays local ·<br/>no question"])
```

Agents are told never to add, edit or remove rows. Guides are gitignored with
the rest of `assets/`, so the table is local to the machine.
