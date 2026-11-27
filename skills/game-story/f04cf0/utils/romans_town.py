"""Staging people in the Romans town: map coordinates and acts that post named, grouped figures.

A story that stands its own people on the stage of a living world should do it by id.
``unit.select`` with a ``kind`` picks out the town's own folk first, so a beat that
orders ``{kind: "legionary", count: 12}`` walks four townsfolk past the ring its card
is pointing at and leaves the eight it spawned to drift: the e2e run reports it as a
highlight ringing empty ground. ``stand_acts`` spawns each figure with an id and posts
that figure on that spot, so every later ``highlight`` of ``group:<name>`` and every
``unit.select`` by id lands on the people the card means.
"""


def stage_to_map(at, half=16.0):
    """A stage point [x, z] as a point in the world's map (the sim map is centred: map = stage + half)."""
    return [round(at[0] + half, 3), round(at[1] + half, 3)]


def standing_spots(centre, n, dx=0.8, dz=0.7):
    """`n` places in rows about `centre`, far enough apart that nothing is spawned on top of anything."""
    cols = max(1, round((n - 1) ** 0.5))
    return [[round(centre[0] + (i % cols - (cols - 1) / 2) * dx, 2),
             round(centre[1] + (i // cols - (n // cols) / 2) * dz, 2)] for i in range(n)]


def figure_ids(group, n=None, first=1):
    """The ids `stand_acts` gives a group: `<group>-<n>`, from `first` up (all of them when `n` is None)."""
    if n is None:
        n = first
    return [f"{group}-{i}" for i in range(first, first + n)]


def stand_acts(kind, group, spots, label=None, half=16.0):
    """Acts that spawn one figure of `kind` per spot in `group`, each posted on its own spot by id.

    `spots` is one [x, z] or a list of them (repeated if there are fewer than `group` needs).
    Each figure's id is `<group>-<n>` (see `figure_ids`); `label` is the name the town floats
    over it, `None` to show the id and `""` to show nothing.
    """
    one = isinstance(spots[0], (int, float))
    out = []
    for i, spot in enumerate(spots if not one else [spots[0]] * len(spots)):
        who = f"{group}-{i + 1}"
        at = list(spot)
        out += [
            {"action": "sim.spawn", "args": {"kind": kind, "at": stage_to_map(at, half),
                                             "group": group,
                                             "set": {"id": who, "name": who if label is None else label}}},
            {"action": "unit.select", "args": {"ids": [who]}},
            {"action": "unit.go", "args": {"to": at}},
            {"action": "unit.deselect"},
        ]
    return out


def walk_acts(ids, to, deselect=True):
    """Acts that select these world ids, walk them to a stage point `to`, and (unless `deselect` is false) leave nobody selected.

    Leaving the selection on is what puts a town panel's job buttons on screen, so a beat
    whose `glow` rings one of them must arrive that way.
    """
    out = [{"action": "unit.select", "args": {"ids": list(ids)}},
           {"action": "unit.go", "args": {"to": list(to)}}]
    if deselect:
        out.append({"action": "unit.deselect"})
    return out


def send_acts(group, to, n=None, first=1, deselect=True):
    """`walk_acts` for the first `n` figures of a `group` (see `figure_ids`)."""
    return walk_acts(figure_ids(group, n, first), to, deselect)


def select_acts(group, n=None, first=1, deselect=True):
    """Acts that select the first `n` figures of a `group` by id and leave them standing there."""
    out = [{"action": "unit.select", "args": {"ids": figure_ids(group, n, first)}}]
    if deselect:
        out.append({"action": "unit.deselect"})
    return out


def work_acts(group, site, n=None, first=1):
    """Acts that put the first `n` figures of a `group` to work at a building (`site`), by id."""
    return [{"action": "unit.select", "args": {"ids": figure_ids(group, n, first)}},
            {"action": "unit.work", "args": {"site": site}},
            {"action": "unit.deselect"}]
