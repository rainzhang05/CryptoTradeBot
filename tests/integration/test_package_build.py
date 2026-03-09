"""Packaging validation for local worktree noise."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path


def test_build_ignores_local_ci_virtualenv(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    fixture_root = repo_root / ".ci-build-fixture"
    bin_dir = fixture_root / "bin"
    symlink_path = bin_dir / "python3.12"
    output_dir = tmp_path / "dist"

    if fixture_root.exists():
        shutil.rmtree(fixture_root)

    bin_dir.mkdir(parents=True)
    symlink_path.symlink_to(Path(sys.executable).resolve())
    (fixture_root / "pyvenv.cfg").write_text("home = /tmp\n", encoding="utf-8")

    try:
        result = subprocess.run(
            [sys.executable, "-m", "build", "--outdir", str(output_dir), "--no-isolation"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        failure_message = "\n".join(
            [
                "build command failed",
                result.stdout,
                result.stderr,
            ]
        )
        assert result.returncode == 0, failure_message

        sdists = sorted(output_dir.glob("*.tar.gz"))
        assert sdists, "expected an sdist artifact"
        with tarfile.open(sdists[0], "r:gz") as archive:
            archived_names = archive.getnames()
        assert not any(".ci-build-fixture" in name for name in archived_names)
    finally:
        shutil.rmtree(fixture_root, ignore_errors=True)
