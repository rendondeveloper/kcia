"""Shared constants for the local skill catalog."""

from __future__ import annotations

import re

SKILLS_SCHEMA_VERSION = 1
SKILLS_REL_PATH = ".ai/skills.yaml"

NAMESPACE_COMMONS = "commons"
NAMESPACE_FLAGS = frozenset({"backend", "mobile", "web", NAMESPACE_COMMONS})

RESERVED_FLAGS = frozenset(
    {
        "backend",
        "mobile",
        "web",
        NAMESPACE_COMMONS,
        "path",
        "remove",
        "help",
        "force",
        "yes",
    }
)

SHORTCUT_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")

SKILL_OK_MARKER = "SKILL_OK:"
