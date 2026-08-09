import re
from importlib import metadata

from kcia import VERSION

SEMVER = re.compile(r"^\d+\.\d+\.\d+$")


def test_version_is_semver() -> None:
    assert SEMVER.match(VERSION), f"VERSION must be semver, got {VERSION!r}"


def test_installed_metadata_matches_source() -> None:
    """`kcia.VERSION` is the single source of truth; pyproject derives from it."""
    assert metadata.version("kcia") == VERSION
