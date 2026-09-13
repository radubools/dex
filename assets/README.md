# assets

One directory per project; one directory per package inside that.

```
assets/
  <project>/
    AGENTS.md      the standing brief every task in this project reads
    widgets.json   which widget opens which of this project's files (optional)
    <package>/     one task's output
```

Nothing here is in git. This is generated work — it is what dex produces, not
what dex is — and a single project can run to hundreds of megabytes of rendered
video and audio. Back it up separately if it matters to you; a clone starts
empty and that is intended.

## Starting from nothing

There is nothing to set up. Create a project in the UI, or:

```bash
curl -X POST localhost:4317/api/projects \
  -H 'content-type: application/json' \
  -d '{"name":"Chemistry","description":"Reaction mechanisms, drawn and explained."}'
```

dex creates the directory, seeds `AGENTS.md` from `AGENTS.template` at the
repository root, and opens a design thread for shaping it. Edit the guide by
talking to that thread rather than by hand — the design chat can also read the
project, write widgets and build them.

`AGENTS.template` is the one guide that *is* tracked: it is the starting point
every new project is copied from, so a change there reaches every project
created afterwards and none created before.
