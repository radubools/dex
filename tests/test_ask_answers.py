"""The latest answer to a question is reused, briefly, for the same question.

A batch of sibling tasks can all hit the same snag; until now each one parked
on the operator for the same decision the one before it had already been given.
"""

from __future__ import annotations

import time

from dex.store import SettingsStore


async def test_an_answer_is_reused_for_the_same_question(db):
    settings = SettingsStore(db)
    question = "No PDF of hetushu page 3182272 is in inputs/ — stop, or build from the EPUB?"
    options = [
        "Build it from the EPUB chapter (as ch10 was)",
        "Stop and report the PDF input as missing; build nothing",
    ]
    await settings.remember_answer(
        question, options, options[0], project="traduceri")

    again = await settings.prior_answer(question, options, project="traduceri")
    assert again == options[0]


async def test_the_same_question_asked_differently_is_not_answered(db):
    """Matching is by the question's own text, not by topic.

    An option often names chapter numbers and file names that only apply to
    the task that asked; auto-picking one for a sibling would be a decision
    nobody made.
    """
    settings = SettingsStore(db)
    await settings.remember_answer(
        "No PDF of chapter 16 is in inputs/ — stop, or build from the EPUB?",
        ["Build from the EPUB, chapter 第16章", "Stop"], "Build from the EPUB, chapter 第16章",
        project="traduceri")

    assert await settings.prior_answer(
        "No PDF of chapter 17 is in inputs/ — stop, or build from the EPUB?",
        ["Build from the EPUB, chapter 第17章", "Stop"],
        project="traduceri",
    ) is None


async def test_an_answer_no_longer_offered_is_not_reused(db):
    """A question asked again with changed options is a different question."""
    settings = SettingsStore(db)
    question = "Two-column print missing from inputs/ — what now?"
    await settings.remember_answer(
        question, ["Build from EPUB", "Stop"], "Build from EPUB", project="traduceri")

    assert await settings.prior_answer(
        question, ["Wait for the PDF upload", "Stop"], project="traduceri",
    ) is None


async def test_answers_do_not_carry_across_projects(db):
    settings = SettingsStore(db)
    await settings.remember_answer(
        "Use the manim shim or the bundled renderer?",
        ["Bundled", "PATH shim"], "Bundled", project="algorithms")

    assert await settings.prior_answer(
        "Use the manim shim or the bundled renderer?",
        ["Bundled", "PATH shim"], project="traduceri",
    ) is None


async def test_an_answer_outlives_half_a_day_never(db):
    settings = SettingsStore(db)
    await settings.remember_answer(
        "Draft translation or fresh translation?", ["Fresh", "Proofread"],
        "Fresh", project="traduceri")

    # Nothing to age the entry for real: write the row back with an old time.
    await settings.set("ask_answers", {
        "stale": {"question": "Draft translation or fresh translation?",
                  "answer": "Proofread", "at": time.time() - 13 * 3600,
                  "project": "traduceri"},
    })

    assert await settings.prior_answer(
        "Draft translation or fresh translation?", ["Fresh", "Proofread"],
        project="traduceri",
    ) is None


async def test_the_latest_answer_wins(db):
    settings = SettingsStore(db)
    await settings.remember_answer("Stop the batch after chapter 9 or finish all 31?",
                                   ["All 31", "Stop at 9"], "Stop at 9", project="traduceri")
    await settings.remember_answer("Stop the batch after chapter 9 or finish all 31?",
                                   ["All 31", "Stop at 9"], "All 31", project="traduceri")

    assert await settings.prior_answer(
        "Stop the batch after chapter 9 or finish all 31?", ["All 31", "Stop at 9"],
        project="traduceri",
    ) == "All 31"
