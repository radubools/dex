"""A story task never runs its own end-to-end test: a game-e2e dependency does."""

import pytest

from utils import story


@pytest.mark.parametrize("run", [story.e2e, story.e2e_segment, story.e2e_start])
def test_the_story_runners_point_at_the_game_e2e_skill(run, tmp_path):
    with pytest.raises(RuntimeError, match="game-e2e"):
        run(tmp_path, tmp_path)
