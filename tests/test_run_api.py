"""dex-api's clean start (scripts/run_api.py): only DEX_*, a few basics and the token from ~/.zshrc."""

from __future__ import annotations

import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("run_api", Path(__file__).resolve().parents[1] / "scripts" / "run_api.py")
run_api = importlib.util.module_from_spec(spec)
spec.loader.exec_module(run_api)


def test_token_comes_from_the_last_export_in_zshrc(tmp_path):
    rc = tmp_path / ".zshrc"
    rc.write_text("export PATH=/x\nexport CLAUDE_CODE_OAUTH_TOKEN=old\n# later\nexport CLAUDE_CODE_OAUTH_TOKEN=\"sk-ant-oat-new\"\n")
    assert run_api.token_from_zshrc(rc) == "sk-ant-oat-new"
    assert run_api.token_from_zshrc(tmp_path / "missing") == ""


def test_only_dex_settings_basics_and_the_token_get_through(monkeypatch):
    monkeypatch.setattr(run_api, "token_from_zshrc", lambda: "tok")
    env = run_api.clean_env({
        "DEX_PORT": "4317", "HOME": "/h", "PATH": "/bin",
        "ANTHROPIC_API_KEY": "x", "ANTHROPIC_BASE_URL": "http://proxy", "CLAUDECODE": "1",
        "CLAUDE_CODE_SESSION_ID": "s", "CLAUDE_CODE_OAUTH_TOKEN": "from-the-shell", "OPENAI_API_KEY": "y",
    })
    assert env == {"DEX_PORT": "4317", "HOME": "/h", "PATH": "/bin", "CLAUDE_CODE_OAUTH_TOKEN": "tok"}
