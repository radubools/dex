"""Old threads tied to skills: by the title a skill declares, then by what their tasks used."""

from __future__ import annotations

from types import SimpleNamespace as Row

from dex.migrate import _skill_of

declared = {
    "game-music": Row(name="game-music", thread="Music", base=False),
    "game-story": Row(name="game-story", thread="Stories", base=False),
    "game-characters": Row(name="game-characters", thread="Characters", base=False),
    "game-environment": Row(name="game-environment", thread="Scenery", base=False),
    "game-mesh": Row(name="game-mesh", thread="Models", base=True),
    "power": Row(name="power", thread="Power", base=False),
}


def test_a_declared_title_ties_it():
    assert _skill_of("Music", {}, declared) == "game-music"
    assert _skill_of("Stories — lessons and storylines", {"game-mesh": 5}, declared) == "game-story"


def test_a_specific_skill_beats_the_base_one_it_builds_on():
    assert _skill_of("Enemies — grumps and boss", {"game-mesh": 5, "game-characters": 2}, declared) == "game-characters"


def test_a_skill_that_only_passed_through_doesnt_tie_it():
    used = {"game-mesh": 11, "game-environment": 1, "game-characters": 1}
    assert _skill_of("Data visuals kit", used, declared) == "game-mesh"


def test_the_power_thread_is_powers_even_where_the_skill_is_off():
    assert _skill_of("Power", {}, {}) == "power"


def test_threads_of_no_skill_stay_untied():
    assert _skill_of("Frere jacques song", {}, declared) is None
    assert _skill_of("Malasana", {"yoga-figure": 62}, declared) is None
    assert _skill_of("Chats", {"game-story": 40}, declared) is None
