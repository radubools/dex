# 06 · Project widgets

## Summary

> **Widgets live inside skills, and are served from there.** A widget's source
> and its built bundle are in `skills/<name>/<version>/widgets/<widget>/`;
> enabling a skill adds its binding to the project's `widgets.json` and copies
> nothing. The browser fetches
> `/api/widgets/<skill>/<version>/<widget>/index.js`.
>
> There used to be a top-level `widgets/` holding a materialised copy of each,
> served by a `StaticFiles` mount. It meant a bundle could be built in one
> place and served from the other, and one silently was. `widgets/` now holds
> only the tooling: the builder, the test harness and its server.
>
> Everything below still describes how a widget *works* — the contract, the
> sandbox, the resolution rules — which is unchanged. Where it comes from is
> [15](15_skills.md).


A **widget** is a small ES module that turns one kind of file into something
worth looking at: a pose JSON into an orbitable 3D skeleton, a narrated MP4
into a player with synchronised captions, a score sidecar into sheet music with
a cursor that follows the audio. Without one, a file opens in the built-in
markdown or code viewer.

Widgets are **pluggable at runtime** — building one makes it live on the next
page that asks, with no server restart — and **sandboxed**: each runs in an
iframe with an opaque origin, so it cannot read the session, call the API, or
touch the page around it.

The design deliberately splits two halves:

| Half | Lives in | Owned by |
|---|---|---|
| **Code** — what a widget is | `widgets/<name>/` at the top level | Shared across every project |
| **Rules** — which files it opens | `assets/<project>/widgets.json` | Each project, independently |

| Module | Responsibility |
|---|---|
| `src/dex/widgets.py` | Discover built widgets, read rules, resolve a file |
| `src/dex/api.py` | `/api/widgets`, `/api/widgets/resolve`, `/widgets` static mount |
| `web/src/components/Viewer.tsx` | Choose widget or built-in viewer |
| `web/src/components/WidgetHost.tsx` | The sandbox, bootstrap, and bridge |
| `widgets/build.mjs` | Bundle each widget to one ESM file |
| `widgets/harness.html`, `serve.mjs`, `playwright.config.ts` | Testing |

---

## Layout

```mermaid
flowchart LR
    subgraph shared ["widgets/ — tracked, shared"]
        W1["pose-skeleton/<br/>widget.json · src/index.ts ·<br/>test/*.spec.ts · dist/index.js"]
        W2["narrated-video/"]
        W3["music-score/"]
        BLD["build.mjs"]
    end
    subgraph yoga ["assets/yoga/"]
        R1["widgets.json<br/>*/poses/*.json ·<br/>*_pose.json → pose-skeleton"]
    end
    subgraph alg ["assets/algorithms/"]
        R2["widgets.json<br/>.mp4 · .webm → narrated-video"]
    end
    subgraph mus ["assets/music/"]
        R3["widgets.json<br/>.score.json → music-score"]
    end
    R1 -.-> W1
    R2 -.-> W2
    R3 -.-> W3
```

`dist/` is gitignored, so a fresh clone must build widgets before any of them
work — until then every file falls back silently.

### The contract

```mermaid
classDiagram
    direction LR
    class WidgetModule {
        <<ES module>>
        +mount(el, ctx) Promise
    }
    class Ctx {
        +string path
        +string text
        +string theme
        +fetchAsset(relPath) Promise~string~
        +fetchAssetUrl(relPath) Promise~string~
    }
    class Manifest {
        <<widget.json>>
        name
        title
        description
    }
    class Rule {
        <<widgets.json entry>>
        widget
        extensions
        filenames
        matches(path) bool
    }
    class Widget {
        name
        title
        version: bundle mtime
        built: bool
        url
    }
    WidgetModule ..> Ctx : receives
    Manifest --> Widget : discovered as
    Rule --> Widget : names
```

A widget author writes `src/index.ts` exporting `mount` and a `widget.json` —
nothing else. There is no per-widget build config.

---

## Resolving which viewer opens a file

```mermaid
flowchart TD
    O([Open a file]) --> RES["GET /api/widgets/resolve?path"]
    RES --> RULES["rules from assets/project/widgets.json,<br/>in order"]
    RULES --> M{"first rule whose<br/>extension suffix or<br/>filename glob matches"}
    M -- none --> NONE["null"]
    M -- found --> B{"named widget built?"}
    B -- no --> NONE
    B -- yes --> WID["widget {url?v=mtime}"]
    NONE --> BI{"built-in viewer by type"}
    BI --> IMG["image / audio player"]
    BI --> MD["markdown · mermaid"]
    BI --> JSON["JSON · pose"]
    BI --> CODE["code, highlighted"]
    BI --> BIN["binary — download link"]
    WID --> HOST["WidgetHost"]
    HOST -->|throws| BI
```

- **First match wins.** Order in the file is the priority, which is easier to
  reason about — and to write from a conversation — than a priority number whose
  effect nobody can see.
- **Extensions match the end of the filename**, so multi-part suffixes work:
  `.pose.json` is what separates a pose from every other JSON file.
- **An unbuilt widget resolves to `null`.** A half-finished widget degrades to
  the code viewer rather than an empty frame.
- **A widget wins over the built-in player.** `.mp4` was once claimed by the
  image branch before the widget check ran, so a rule naming a video silently
  did nothing.
- **Resolution is asked on every open**, never cached, so a widget built a
  moment ago works now.

### Cache busting

`import()` and `<script>` cache by URL for the life of the page. The widget URL
carries `?v=<bundle mtime>`, which changes exactly when the bundle does —
otherwise the browser keeps running the old code while you debug a file you
already fixed.

---

## The sandbox

```mermaid
flowchart TB
    subgraph page ["dex page — origin localhost:4318"]
        HOST["WidgetHost"]
        COOKIE[("session cookie")]
        API["/api/*"]
    end
    subgraph frame ["iframe sandbox=allow-scripts — opaque origin"]
        BOOT["bootstrap"]
        MOD["widget module<br/>data: URL import"]
    end
    HOST -->|"srcdoc: bootstrap +<br/>base64 bundle + ctx"| BOOT
    BOOT --> MOD
    MOD <-->|postMessage only| HOST
    HOST --> API
    API --- COOKIE
    frame -. "✗ no cookies<br/>✗ no /api<br/>✗ no parent DOM<br/>✗ no network needed" .- page
```

The whole security argument is one attribute: **`allow-scripts` without
`allow-same-origin`**. That gives the frame a unique opaque origin. Widgets are
authored by admins and authors, but "only trusted people write them" is exactly
the assumption that stops being true later, and the sandbox costs almost nothing.

Because the origin is opaque, the frame **cannot even fetch its own bundle** —
that would be cross-origin. So the host does it:

```mermaid
sequenceDiagram
    autonumber
    participant V as Viewer
    participant H as WidgetHost
    participant S as /widgets static
    participant F as iframe
    participant M as widget module

    V->>H: widget, path, text
    H->>S: GET /widgets/pose-skeleton/dist/index.js?v=…
    S-->>H: bundle source
    H->>H: base64 in 32 KB chunks
    H->>H: ctx JSON, "<" escaped
    H->>F: srcdoc = BOOTSTRAP(code, theme) + ctx
    F->>F: install error handlers
    F->>M: dynamic import of a base64 data URL
    M-->>F: module
    F->>M: mount(#root, ctx)
    F-->>H: ready
    loop content resizes
        F-->>H: height
        H->>H: clamp 120–2400px
    end
```

Two encoding details that each broke real widgets:

- **Base64, not raw source.** Inlining raw source into `srcdoc` lets the
  bundle's own backticks or `</script>` terminate the host document early.
- **Chunked base64.** `String.fromCharCode(...bytes)` on a 646 KB bundle passes
  646,000 arguments and overflows the stack. The small widget worked and the
  three.js one did not.

---

## The asset bridge

A widget often needs more than the file it was opened on — the MIDI a score
names, the captions beside a video. It asks the host, and the host decides.

```mermaid
sequenceDiagram
    autonumber
    participant M as widget
    participant F as bootstrap
    participant H as WidgetHost
    participant API as /api/assets

    M->>F: fetchAssetUrl("narration.mp4")
    F->>H: {type: fetch, id, path, binary: true}
    H->>H: event.source is our frame?
    H->>H: resolve relative to the opened file's directory
    alt escapes that directory
        H-->>F: {type: asset, id, error: "outside this package"}
    else inside
        H->>API: GET /api/assets/raw?path (with session)
        API->>API: access.check(project)
        API-->>H: bytes
        H-->>F: {type: asset, id, body: ArrayBuffer} (transferred)
        F->>F: URL.createObjectURL(new Blob([bytes]))
        F-->>M: blob: URL
    end
```

- **Authorisation stays in one place.** The frame has no credentials, so every
  file it reaches goes through the host, which uses the signed-in session and
  the same project check as any other asset read.
- **Confined to the package.** Paths resolve relative to the opened file and may
  not leave its directory, so a widget cannot read the rest of the project.
- **Binary is transferred, not copied.** The `ArrayBuffer` moves to the frame,
  and the frame builds the blob URL itself — a plain `/api/...` `src` would be
  cross-origin and credentialless, and would 401 as soon as sign-in is on.
- **Only our own frame is heard.** Messages whose `source` is not this iframe's
  window are ignored.

---

## Failure

```mermaid
flowchart TD
    E1["bundle fetch fails"] --> FB
    E2["module has no mount()"] --> RPT
    E3["mount() throws"] --> RPT
    E4["uncaught error or rejection"] --> RPT
    RPT["frame posts {type: error}"] --> FAILED["host shows<br/>'This widget could not run: …'"]
    FB["onFallback()"] --> BUILTIN["built-in viewer"]
```

Every error is reported rather than swallowed. A silent blank frame is the worst
failure mode for code an agent just wrote.

---

## Building and testing

```mermaid
flowchart LR
    SRC["widgets/name/src/index.ts"] --> BUILD["node widgets/build.mjs name"]
    BUILD -->|"Vite library mode<br/>ES only · es2022 · no sourcemap"| DIST["dist/index.js"]
    DIST --> TEST["playwright test --config widgets/playwright.config.ts"]
    TEST --> SERVE["serve.mjs :4319<br/>static over repo root"]
    SERVE --> HARN["harness.html?widget=name"]
    HARN -->|"same mount(el, ctx)"| DIST
```

- **One bundle, no external imports.** The frame cannot fetch, so everything is
  inlined.
- **No sourcemaps.** They would double the bytes inlined into the frame, and
  stack traces already surface through the error bridge.
- **The harness mounts a widget exactly the way dex does**, so a passing test
  means the real host will run it. Tests use real project files where they
  exist, because a widget that only handles the data its author imagined is the
  failure that actually happens.
- **Fixtures resolve from `import.meta.url`**, never a bare relative path: a
  design task runs the suite from its own package directory, where `assets/…`
  points at nothing.

### Who builds them

The design chat ([05](05_project_design.md)) writes widget code, runs the build
and the tests, and adds the rule to `widgets.json`. `node` and `playwright` are
on the task allowlist and `widgets/` is writable for design turns, so the whole
loop runs without stopping for approval.
