"""Validation for the installed tradebot console script."""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path

ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _tradebot_script_path() -> Path:
    script_path = Path.cwd() / ".venv" / "bin" / "tradebot"
    if not script_path.exists():
        script_path = Path(sys.executable).resolve().with_name("tradebot")
    if sys.platform == "win32":
        script_path = script_path.with_suffix(".exe")
    return script_path


def _plain_text(text: str) -> str:
    return ANSI_ESCAPE_RE.sub("", text)


def test_tradebot_console_script_help() -> None:
    script_path = _tradebot_script_path()

    assert script_path.exists()

    result = subprocess.run(
        [str(script_path), "--help"],
        capture_output=True,
        check=False,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "CLI for the crypto spot trading bot." in _plain_text(result.stdout)


def test_tradebot_console_script_no_args_prints_help_non_interactively(tmp_path: Path) -> None:
    script_path = _tradebot_script_path()

    assert script_path.exists()

    env = dict(os.environ)
    env["TRADEBOT_HOME"] = str(tmp_path / "tradebot-home")
    validate_result = subprocess.run(
        [str(script_path), "config", "validate"],
        capture_output=True,
        check=False,
        text=True,
        env=env,
    )
    assert validate_result.returncode == 0, validate_result.stderr

    result = subprocess.run(
        [str(script_path)],
        capture_output=True,
        check=False,
        text=True,
        env=env,
        stdin=subprocess.DEVNULL,
    )

    assert result.returncode == 0, result.stderr
    assert "Usage: tradebot" in _plain_text(result.stdout)


def test_pyproject_uses_renamed_distribution_metadata() -> None:
    pyproject = Path.cwd() / "pyproject.toml"
    with pyproject.open("rb") as handle:
        payload = tomllib.load(handle)

    project = payload["project"]
    assert project["name"] == "CryptoTradeBot"
    assert project["scripts"]["tradebot"] == "tradebot.cli:main"
