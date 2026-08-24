"""Pydantic models for `.ai/skills.yaml`."""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from kcia.skills.constants import SHORTCUT_PATTERN, SKILLS_SCHEMA_VERSION


class SkillEntry(BaseModel):
    name: str
    path: str

    @field_validator("name")
    @classmethod
    def name_not_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("skill name must not be empty")
        return value

    @field_validator("path")
    @classmethod
    def path_not_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("skill path must not be empty")
        return value


class SkillsCatalog(BaseModel):
    schema_version: int = SKILLS_SCHEMA_VERSION
    commons: dict[str, SkillEntry] = Field(default_factory=dict)
    profiles: dict[str, dict[str, SkillEntry]] = Field(default_factory=dict)

    @field_validator("schema_version")
    @classmethod
    def validate_schema_version(cls, value: int) -> int:
        if value != SKILLS_SCHEMA_VERSION:
            raise ValueError(
                f"unsupported skills schema_version {value}; expected {SKILLS_SCHEMA_VERSION}"
            )
        return value

    @field_validator("commons")
    @classmethod
    def validate_commons_keys(cls, value: dict[str, SkillEntry]) -> dict[str, SkillEntry]:
        for shortcut in value:
            if not SHORTCUT_PATTERN.match(shortcut):
                raise ValueError(f"invalid shortcut key '{shortcut}' in commons")
        return value

    @field_validator("profiles")
    @classmethod
    def validate_profile_keys(
        cls, value: dict[str, dict[str, SkillEntry]]
    ) -> dict[str, dict[str, SkillEntry]]:
        for namespace, entries in value.items():
            for shortcut in entries:
                if not SHORTCUT_PATTERN.match(shortcut):
                    raise ValueError(
                        f"invalid shortcut key '{shortcut}' in profiles.{namespace}"
                    )
        return value
