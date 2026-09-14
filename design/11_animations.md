# 11 · Animations

## Summary

Algorithm packages teach through **manim animations**: one scene per approach,
walking through the idea on a small concrete input. dex lets a reader **change
playback speed** and **step through named checkpoints** — "compare", "swap",
"merge" — rather than scrubbing a clip.

Two formats exist, and they need very different machinery:

| Format | Produced when | Speed | Stepping |
|---|---|---|---|
| **GIF** | A silent animation | Server rewrites frame delays | Server slices frames with ffmpeg |
| **MP4** | A narrated animation | The player's own rate | The player seeks to a time |

A GIF has no seek and plays at the delays baked into it, and an `<img>` offers
no controls, so every GIF variant is **derived on the server and cached**. A
video needs none of that; the server only tells the player where the sections
are.

| Module | Responsibility |
|---|---|
| `src/dex/tools/render_manim.py` | Render a scene to a chosen path; save sections; mux narration |
| `src/dex/animation.py` | Checkpoints, `describe`, speed and slice variants |
| `src/dex/gifinfo.py` | Read GIF timing and rewrite delays without decoding |
| `src/dex/tools/warm_animations.py` | Pre-build slices after a batch of tasks |
| `web/src/components/AnimationPlayer.tsx`, `VideoPlayer.tsx` | The players |

---

## Producing an animation

```mermaid
sequenceDiagram
    autonumber
    participant A as Agent
    participant T as render_manim
    participant M as manim
    participant F as ffmpeg
    participant P as package dir

    A->>T: python -m dex.tools.render_manim animation.py MergeSort -o merge.mp4 [--narration n.m4a]
    T->>T: ffmpeg and manim present?
    T->>M: render --format=mp4|gif --save_sections<br/>--media_dir tmp (this interpreter)
    M-->>T: whole-scene file + sections/MergeSort.json
    T->>P: move merge.mp4
    T->>P: copy index → merge.sections.json
    opt narration given
        T->>F: mux audio into the video (-c:v copy -shortest)
        F->>P: replace merge.mp4
    end
    T-->>A: prints the path
```

Why a wrapper rather than calling manim directly:

- **Deterministic output path.** manim buries output under
  `media/videos/<module>/<quality>/<Scene>.gif`, which is awkward for an agent to
  place reliably.
- **The right manim.** It runs `python -m manim` with *this* interpreter; a
  `manim` found on `PATH` may be a pyenv shim or another environment with a
  different Scene API.
- **The extension chooses the format.** `.mp4` when there is narration to carry,
  `.gif` when silent.
- **Narration is muxed in**, not left beside the video. A separate audio file
  has to be kept in sync by whoever plays it, and drifts the moment a reader
  seeks or loops a section; one file cannot drift.
- **Rendering runs in a scratch media directory**, so a stray render never
  leaves `media/` inside the package or the repository.

### Sections are checkpoints

A scene marks its steps with `self.next_section("name")`. `--save_sections`
makes manim write an index of those blocks, which the wrapper copies beside the
output as `<name>.sections.json`:

```mermaid
classDiagram
    direction LR
    class SectionsJson {
        <<file: name.sections.json>>
        list of entries
    }
    class Entry {
        name
        nb_frames
        duration
    }
    class Checkpoint {
        index
        name
        start_frame
        end_frame
        duration
        start
        to_json()
    }
    SectionsJson "1" --> "*" Entry
    Entry ..> Checkpoint : becomes
```

Without sections, the viewer falls back to unnamed equal slices — which is why
the algorithms guide tells the agent to mark its steps. The guide is re-read on
every run, including resumes.

---

## Describing an animation

`GET /api/assets/animation?path=` returns what the player needs to draw its
controls:

```mermaid
flowchart TD
    D([describe path]) --> V{"video suffix?<br/>.mp4 .webm .mov"}
    V -- yes --> VS["read sections.json"]
    VS --> VC["checkpoints by time<br/>start = running sum of durations"]
    VC --> VR["kind: video<br/>duration = sum of sections<br/>speeds: player-side"]
    V -- no --> GI["gifinfo.read: frames, delays"]
    GI --> SMALL{"fewer than 24 frames?"}
    SMALL -- yes --> NONE["no checkpoints"]
    SMALL -- no --> REC{"sections.json present<br/>and valid?"}
    REC -- yes --> RC["named checkpoints<br/>frames from cumulative nb_frames"]
    REC -- no --> EQ["6 equal slices<br/>Part 1 … Part 6"]
    RC --> GR["kind: gif · named: true"]
    EQ --> GR2["kind: gif · named: false"]
```

The response also carries `defaultSpeed` from settings and the speed choices:
`0.25, 0.5, 1, 1.5, 2, 4`.

---

## GIF playback variants

`GET /api/assets/animation/play?path=&speed=&segment=` serves the right bytes:

```mermaid
flowchart TD
    P([play request]) --> CL["clamp speed to 0.1 – 8"]
    CL --> NOOP{"speed ≈ 1<br/>and no segment?"}
    NOOP -- yes --> SRC(["serve the original"])
    NOOP -- no --> KEY["cache key = sha256 of<br/>path · mtime · speed · segment · delay"]
    KEY --> HIT{"cached file exists?"}
    HIT -- yes --> CACHED(["serve cached"])
    HIT -- no --> SEG{"segment requested?"}
    SEG -- no --> RT["retime whole GIF<br/>(bytes only)"]
    SEG -- yes --> SL["ffmpeg slice frames<br/>start..end"]
    SL --> FFOK{"ffmpeg ok?"}
    FFOK -- no --> SRC
    FFOK -- yes --> RT2["retime the slice<br/>if speed changed"]
    RT --> WR["write scratch .partial.gif"]
    RT2 --> WR
    WR --> MV["atomic rename into cache"]
    MV --> CACHED
```

### Speed: rewrite bytes, do not decode

```mermaid
flowchart LR
    subgraph gif [GIF stream]
        H["header · colour table"]
        GCE1["Graphic Control Ext<br/>delay = 10 cs"]
        IMG1["image data"]
        GCE2["Graphic Control Ext<br/>delay = 10 cs"]
        IMG2["image data"]
    end
    GCE1 -->|"rewrite 2 bytes"| N1["delay = 5 cs"]
    GCE2 -->|"rewrite 2 bytes"| N2["delay = 5 cs"]
```

Playback speed lives entirely in the **Graphic Control Extension** blocks, so
changing it never touches image data. Re-encoding a 500-frame animation through
an image tool took over a minute; rewriting delays takes milliseconds.

The new delay is the **median** source delay divided by the speed, floored at 2
hundredths. Below that, browsers rewrite a delay of 0 or 1 to 10 (about 10 fps),
so a faster request would paradoxically play slower. The same clamp is applied
when *reading* durations, or any animation faster than 10 fps would be
misreported.

### Stepping: slice with ffmpeg

GIF frames are **deltas** of one another, so a slice cannot be cut from the byte
stream — it has to be decoded and re-encoded. ffmpeg does that about an order of
magnitude faster than an image tool at these sizes, roughly half a second where
the alternative took thirteen.

```
select='between(n, start, end)', setpts=N/15/TB,
split → palettegen=stats_mode=diff → paletteuse=dither=bayer
```

A **generated palette** keeps the source's colours; the default palette visibly
banded manim's gradients.

### Caching

```mermaid
stateDiagram-v2
    direction LR
    [*] --> Missing
    Missing --> Writing: build variant
    Writing --> Cached: rename scratch → final
    Writing --> Missing: process killed (scratch left, never read)
    Cached --> Stale: source GIF re-rendered (mtime changes)
    Stale --> Missing: new key
```

- The cache lives in `.dex/animations/` and is **entirely rebuildable**.
- The key includes the source's **mtime**, so a re-rendered animation never
  serves a stale variant.
- Files are written to a scratch name and **renamed only when complete**, so a
  killed process cannot leave a truncated file that later looks like a valid hit.
- Every failure — no ffmpeg, an unreadable GIF, a failed write — **falls back to
  the original file**. A reader loses a control, never the animation.
- Responses carry `Cache-Control: private, max-age=300`, and the real media type
  of what was served.

### Warming

The first press of a step control pays for its slice. After a batch of tasks,

```bash
python -m dex.tools.warm_animations
```

builds every slice up front (`--speed` warms additional speeds; `--dry-run`
reports the work). It calls the same `variant()` the endpoint does, so a warmed
file is exactly the cache hit the endpoint will look for.

---

## Video playback

```mermaid
sequenceDiagram
    participant UI as VideoPlayer
    participant API
    participant V as merge.mp4

    UI->>API: GET /assets/animation?path=merge.mp4
    API-->>UI: checkpoints [{name, start, end}], duration
    UI->>API: GET /assets/animation/play?path=merge.mp4
    API-->>UI: the file unchanged (speed ignored for video)
    UI->>V: currentTime = checkpoint.start
    UI->>V: playbackRate = chosen speed
    opt loop a step
        UI->>V: on timeupdate past end → seek to start
    end
```

Nothing is re-encoded. A video's checkpoints are **times**, computed as running
sums of section durations — frames are a GIF's unit, not a player's. Its total
length is the sum of the sections, which is also how the review feed learns how
long to play it ([12](12_review_feed.md)).

---

## Narration: what the guide asks for

The renderer only muxes. Producing narration that matches the picture is the
agent's job, and the algorithms project guide spells out the method — the
narration sets the pace, and the animation is fitted to it rather than the
other way round:

```mermaid
flowchart TD
    S1["1 · Draft the script<br/>one cue per section, written for the ear"] --> S2["2 · Synthesise each cue separately<br/>Piper · measure with ffprobe"]
    S2 --> S3["3 · Fit the scene to the cues<br/>pad each section to its cue"]
    S3 --> S4["4 · Join cues into one track<br/>write the VTT from running totals"]
    S4 --> S5["Render with --narration"]
    S5 --> S6{"5 · Voice and picture<br/>line up?"}
    S6 -- "no, under 5 passes" --> S3
    S6 -- "yes, or 5 passes" --> DONE(["Deliver mp4 · sections.json · vtt<br/>delete .cues/"])
```

Synthesising cues separately is what makes the caption timings exact: each
cue's length is measured, so every caption's start is a running total rather
than an estimate. The five-pass cap exists because five renders of a 30-second
animation is already several minutes of work.
