"""Validation for the installed tradebot console script."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_tradebot_console_script_help() -> None:
    script_path = Path.cwd() / ".venv" / "bin" / "tradebot"
    if not script_path.exists():
        script_path = Path(sys.executable).resolve().with_name("tradebot")
    if sys.platform == "win32":
        script_path = script_path.with_suffix(".exe")

    assert script_path.exists()

    result = subprocess.run(
        [str(script_path), "--help"],
        capture_output=True,
        check=False,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "CLI for the crypto spot trading bot." in result.stdout
