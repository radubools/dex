# 07 · Live event stream

## Summary

Everything the UI shows while work is happening — streamed text, thinking, tool
calls, diffs, state changes, questions, cost, new files — arrives as **events**
over one Server-Sent Events connection per tab.

Every event is **written to Postgres before it is delivered**, and Postgres
assigns its sequence number. That makes `seq` mean the same thing to a client
reconnecting after a server restart as it does to one that never dropped, and
it is why a reload replays a run's full history instead of starting blank.

| Module | Responsibility |
|---|---|
| `src/dex/models.py` | `Event`, `EventType` |
| `src/dex/bus.py` | `EventBus` — persist, fan out, replay |
| `src/dex/api.py` | `GET /api/events` (SSE), `GET /api/tasks/{id}/events` |
| `web/src/api.ts` | `openStream` — EventSource with resume |
| `web/src/store.ts` | The reducer that folds events into UI state |

---

## Path of one event

```mermaid
flowchart LR
    subgraph producers
        R["TaskRunner"]
        Q["TaskManager"]
        A["API routes"]
        W["AssetWatcher"]
        U["UsageWatcher"]
    end
    R & Q & A & W & U -->|"publish()<br/>non-blocking"| PQ[["pending queue<br/>max 10,000"]]
    PQ --> DR["single writer<br/>_drain()"]
    DR -->|"INSERT … RETURNING seq"| PG[("events")]
    DR -->|"event.seq = seq"| FAN{{fan-out}}
    FAN --> S1[["subscriber queue<br/>max 1,000"]]
    FAN --> S2[["subscriber queue"]]
    S1 --> SSE1["SSE tab 1"]
    S2 --> SSE2["SSE tab 2"]
```

**One writer**, so rows land in the order they were published and `seq` is
monotonic in publish order.

**`publish()` never blocks.** A producer is often the agent loop, and a slow
database or client must not stall the agent. The costs of that choice are
explicit:

| Queue full | Behaviour | Rationale |
|---|---|---|
| Pending (10,000) | Event dropped, warning logged | The agent keeps working |
| A subscriber (1,000) | That subscriber misses it | A stalled tab must not back-pressure the agent |

A tab that missed live events recovers them on reconnect, because they are in
Postgres.

---

## Subscribing: replay, then live

```mermaid
sequenceDiagram
    autonumber
    participant T as Browser tab
    participant E as GET /api/events
    participant B as EventBus
    participant PG as Postgres

    T->>E: ?after=<last seq seen>
    E->>E: allowed = visible projects (once)
    E-->>T: ": connected"
    E->>B: subscribe(after, projects)
    B->>B: register live queue FIRST
    B->>PG: history(after_seq, limit 3000, projects)
    PG-->>B: newest ≤3000 after seq, in order
    loop replay
        B-->>E: event
        E-->>T: id: seq · data: json
    end
    B->>B: last_seq = last replayed
    loop live
        B->>B: event from queue
        alt seq ≤ last_seq
            B->>B: skip — already replayed
        else project not allowed
            B->>B: skip
        else
            B-->>E: event
            E-->>T: id: seq · data: json
        end
    end
    loop every 1s
        E->>E: client disconnected? → close
    end
    opt 20s without an event
        E-->>T: ": ping"
    end
```

- **The live queue is registered before history is read.** An event published
  during the replay query lands in the queue and is not lost; the `seq ≤
  last_seq` check drops any that replay already delivered.
- **Replay is newest-biased.** When more than 3,000 events qualify, the most
  recent are returned. Returning the oldest left a client permanently behind: it
  replayed ancient history, set its cursor there, and discarded every live event
  as already seen.
- **Disconnects are polled every second**, not only when an event or ping
  arrives. Otherwise a closed tab kept its generator and bus subscription alive
  until the next ping.
- **Pings every 20 seconds** stop proxies and sleeping phones from dropping the
  stream.
- Response headers `Cache-Control: no-cache, no-transform` and
  `X-Accel-Buffering: no` stop intermediaries buffering it.

---

## Event catalogue

```mermaid
classDiagram
    direction LR
    class Event {
        type
        data
        task_id
        seq
        ts
        project
        to_json()
    }
    class TaskScoped {
        task_state
        text_delta · thinking_delta · block_end
        text · thinking
        tool · tool_result
        diff
        question · question_answered
        approval · approval_resolved
        cost · result · error
        task_message · task_message_delivered
    }
    class ThreadScoped {
        thread_message
        thread_busy
        task_created
    }
    class ProjectScoped {
        asset
        tags
    }
    class Global {
        settings
    }
    Event <|-- TaskScoped
    Event <|-- ThreadScoped
    Event <|-- ProjectScoped
    Event <|-- Global
```

### How streamed blocks assemble

```mermaid
sequenceDiagram
    participant R as Runner
    participant S as UI store

    R->>S: text_delta {id: "uuid:0", delta: "Let me"}
    S->>S: create block uuid:0, append
    R->>S: text_delta {id: "uuid:0", delta: " look"}
    S->>S: append
    R->>S: block_end {id: "uuid:0"}
    S->>S: mark complete
    R->>S: thinking_delta {id: "uuid:1", …}
    S->>S: collapsed thinking step, streaming
```

Block ids combine the message uuid and the content-block index, so two blocks in
one message and blocks across messages never collide.

---

## Project scoping

For a signed-in user who is not an admin, the stream is filtered to the projects
they were granted — on **both** routes an event can take:

```mermaid
flowchart TD
    C([Subscriber]) --> AD{"admin or<br/>service token?"}
    AD -- yes --> ALL["projects = None<br/>everything"]
    AD -- no --> VIS["projects = granted slugs"]
    VIS --> REPLAY["replay SQL:<br/>WHERE project = ANY(granted)"]
    VIS --> LIVE["live filter:<br/>event.project in granted"]
    REPLAY --> NULLP{"event.project<br/>is NULL?"}
    LIVE --> NULLP
    NULLP -- yes --> DROP(["not delivered"])
    NULLP -- no --> KEEP(["delivered if granted"])
```

The project is carried **on the event** because live fan-out is in-process with
no database round trip; filtering cannot join to `tasks`. A migration backfilled
`events.project` from each event's task, so history filters the same way live
events do.

The visible set is resolved **once per connection**. Re-reading grants for each
of thousands of events per second would be a query storm, so a grant change
applies on reconnect — and removing a role ends the user's sessions to force one.

> ### Known gap: untagged events never reach scoped users
>
> An event published without `project` is dropped for every restricted
> subscriber. That is intended for `settings`, and the runner tags everything it
> emits. But several other producers publish without a project today:
>
> | Producer | Events |
> |---|---|
> | `TaskManager` | `task_state` from pause, resume, restart, archive, cancel, orphan sweep; `task_message`; `task_message_delivered`; `approval_resolved` from auto-approve |
> | API | `thread_message` (plan cards, "Started N tasks"); `thread_busy` |
> | `AssetWatcher` | `asset`, `tags` |
>
> **Effect:** with sign-in on, an operator or author sees a running task's text
> and tool calls live, but not their plan card appearing, not a pause or cancel
> they pressed taking effect, not new files lighting up — until they reload.
> Admins are unaffected, which is why it goes unnoticed. The fix is to pass
> `project=` at each of those call sites; the thread's or task's project is
> already in scope at every one.

---

## One task's full history

`GET /api/tasks/{id}/events` returns one task's events however long ago it ran,
independent of the stream — 2,000 by default, up to 10,000 with `?limit=`. Opening an old task's panel uses
this rather than the 3,000-event replay window, which is sized for catching up,
not for archaeology.
