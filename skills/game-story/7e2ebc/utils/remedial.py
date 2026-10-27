"""Remedial beats for schema 2 stories that only a wrong choice enters, so correct answers need no goto."""


def remedial_flag(beat_id: str) -> str:
    """The story variable that marks a remedial beat as entered: 'seen_' + the beat id with '-' as '_'."""
    return "seen_" + beat_id.replace("-", "_")


def remedial_option(option: dict, beat_id: str) -> dict:
    """A copy of a (wrong) choice option that sets the remedial beat's flag and goes to it."""
    return {**option, "goto": beat_id, "set": {**option.get("set", {}), remedial_flag(beat_id): "true"}}


def remedial_beat(beat: dict, back: str) -> dict:
    """A copy of a beat that runs only after its remedial_option (when the flag), clears the flag and goes to `back`.

    Put it right after the question: the main line falls through it (its `when` is
    false), so the correct option needs no goto and the end-to-end test plays no
    extra route for it.
    """
    f = remedial_flag(beat["id"])
    return {**beat, "when": f + " == true", "set": {**beat.get("set", {}), f: "false"}, "goto": back}
