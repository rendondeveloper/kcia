"""Test configuration."""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CLI_SRC = ROOT / "cli" / "src"
sys.path.insert(0, str(CLI_SRC))

KCIA = ROOT / ".venv" / "bin" / "kcia"
MELOS_FIXTURE = ROOT / "tests" / "fixtures" / "repos" / "melos_mono"


@pytest.fixture
def melos_session(tmp_path: Path):
    """Initialized melos_mono repo with an active prompt-mode session."""
    from kcia.waves.session import Session

    repo = tmp_path / "melos_mono"
    shutil.copytree(MELOS_FIXTURE, repo)
    result = subprocess.run(
        [str(KCIA), "start", "--yes", "--path", str(repo)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    session = Session.create(repo, text="arregla el overflow", mode="prompt")
    return session
