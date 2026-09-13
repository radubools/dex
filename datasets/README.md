# datasets

Reference data a project draws on — price tables, corpora, lookups — as opposed
to `assets/`, which is what dex produces.

Nothing here is in git, and nothing in dex reads this directory: it is a
convention, not an interface. A task reaches a file here the same way it reaches
anything else, by being told the path in its brief or its project's `AGENTS.md`.

A clone starts empty. Put what your projects need here, and say in the project's
guide what it is and where to find it — a task that has to discover a dataset by
listing directories will waste turns doing it.
