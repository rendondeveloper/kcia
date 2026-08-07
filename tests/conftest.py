"""Test configuration."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLI_SRC = ROOT / "cli" / "src"
sys.path.insert(0, str(CLI_SRC))
