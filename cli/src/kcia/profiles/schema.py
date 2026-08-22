"""Pydantic models for profile packs and manifests."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

# Leading underscore marks abstract (inheritance-only) profiles per §3.3.
PROFILE_ID_PATTERN = re.compile(r"^_{0,1}[a-z][a-z0-9-]*$")
SUPPORTED_PROFILE_SCHEMA = 2
SUPPORTED_PACK_SCHEMA = 1


class DetectRule(BaseModel):
    when: dict[str, Any]
    confidence: Literal["high", "medium", "low"]
    evidence: str


class CommandOverride(BaseModel):
    when: dict[str, Any]
    commands: dict[str, str]


class CursorAdapterConfig(BaseModel):
    globs: list[str] = Field(default_factory=lambda: ["**/*"])


class ClaudeAdapterConfig(BaseModel):
    nested_file: bool = False


class AdapterConfig(BaseModel):
    cursor: CursorAdapterConfig | None = None
    claude: ClaudeAdapterConfig | None = None


class EmptyTestSignature(BaseModel):
    command: str
    exit_code: int
    output_contains: list[str] = Field(min_length=1)


class ValidationConfig(BaseModel):
    required_commands: list[str] = Field(default_factory=list)
    optional_commands: list[str] = Field(default_factory=list)
    retry_limit: int = 3
    no_tests_signature: EmptyTestSignature | None = None


class ReferenceSpec(BaseModel):
    path: str
    tags: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def default_tag_from_stem(self) -> ReferenceSpec:
        if not self.tags:
            self.tags = [Path(self.path).stem]
        return self


class ProfileSpec(BaseModel):
    schema_version: int
    id: str
    display_name: str
    description: str = ""
    abstract: bool = False
    extends: str | None = None
    detect: list[DetectRule] = Field(default_factory=list)
    commands: dict[str, str] = Field(default_factory=dict)
    command_overrides: list[CommandOverride] = Field(default_factory=list)
    references: list[ReferenceSpec] = Field(default_factory=list)
    workflows: list[str] = Field(default_factory=list)
    rules: dict[str, Any] = Field(default_factory=dict)
    adapters: AdapterConfig = Field(default_factory=AdapterConfig)
    validation: ValidationConfig = Field(default_factory=ValidationConfig)

    @field_validator("references", mode="before")
    @classmethod
    def normalize_references(cls, value: Any) -> list[Any]:
        if not value:
            return []
        normalized: list[Any] = []
        for item in value:
            if isinstance(item, str):
                normalized.append({"path": item})
            else:
                normalized.append(item)
        return normalized

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        if not PROFILE_ID_PATTERN.match(value):
            raise ValueError(
                f"profile id '{value}' must match ^_?[a-z][a-z0-9-]*$"
            )
        return value

    @field_validator("schema_version")
    @classmethod
    def validate_schema_version(cls, value: int) -> int:
        if value != SUPPORTED_PROFILE_SCHEMA:
            raise ValueError(
                f"unsupported profile schema_version {value}; expected {SUPPORTED_PROFILE_SCHEMA}"
            )
        return value


class PackSpec(BaseModel):
    schema_version: int
    name: str
    version: str
    description: str = ""
    kcia_min_version: str = "0.1.0"
    profiles: list[str]

    @field_validator("schema_version")
    @classmethod
    def validate_schema_version(cls, value: int) -> int:
        if value != SUPPORTED_PACK_SCHEMA:
            raise ValueError(
                f"unsupported pack schema_version {value}; expected {SUPPORTED_PACK_SCHEMA}"
            )
        return value


class ProfileManifestEntry(BaseModel):
    id: str
    roots: list[str] = Field(default_factory=list)
    commands: dict[str, str] = Field(default_factory=dict)
    detection: dict[str, Any] = Field(default_factory=dict)


class Manifest(BaseModel):
    schema_version: int = 2
    project: dict[str, Any] = Field(default_factory=dict)
    profiles: list[ProfileManifestEntry] = Field(default_factory=list)
    dependencies: list[dict[str, Any]] = Field(default_factory=list)
    detection: dict[str, Any] = Field(default_factory=dict)

    @property
    def default_profile(self) -> str | None:
        return self.project.get("default_profile")
