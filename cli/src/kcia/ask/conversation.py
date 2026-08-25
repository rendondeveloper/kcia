"""Persistent ask conversation state in `.ai/local/ask.json`."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

ASK_SCHEMA_VERSION = 1
ASK_REL_PATH = Path(".ai") / "local" / "ask.json"

TurnRole = Literal["user", "planner"]


def ask_path(repo_root: Path) -> Path:
    return repo_root / ASK_REL_PATH


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@dataclass
class AskTurn:
    role: TurnRole
    text: str
    at: str

    def to_json(self) -> dict[str, str]:
        return {"role": self.role, "text": self.text, "at": self.at}

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> AskTurn:
        role = raw.get("role")
        if role not in ("user", "planner"):
            raise ValueError(f"invalid ask turn role: {role!r}")
        text = str(raw.get("text") or "")
        at = str(raw.get("at") or "")
        return cls(role=role, text=text, at=at)


@dataclass
class AskConversation:
    turns: list[AskTurn] = field(default_factory=list)
    provider: str | None = None
    model: str | None = None
    provider_session_id: str | None = None
    updated_at: str | None = None
    schema_version: int = ASK_SCHEMA_VERSION

    @classmethod
    def load(cls, repo_root: Path) -> AskConversation:
        path = ask_path(repo_root)
        if not path.is_file():
            return cls()
        data = json.loads(path.read_text(encoding="utf-8"))
        turns = [AskTurn.from_json(item) for item in data.get("turns") or []]
        return cls(
            schema_version=int(data.get("schema_version") or ASK_SCHEMA_VERSION),
            provider=data.get("provider"),
            model=data.get("model"),
            provider_session_id=data.get("provider_session_id"),
            updated_at=data.get("updated_at"),
            turns=turns,
        )

    def save(self, repo_root: Path) -> None:
        path = ask_path(repo_root)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": ASK_SCHEMA_VERSION,
            "provider": self.provider,
            "model": self.model,
            "provider_session_id": self.provider_session_id,
            "updated_at": self.updated_at,
            "turns": [turn.to_json() for turn in self.turns],
        }
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def clear(self, repo_root: Path) -> None:
        path = ask_path(repo_root)
        if path.is_file():
            path.unlink()
        self.turns = []
        self.provider = None
        self.model = None
        self.provider_session_id = None
        self.updated_at = None

    def matches_planner(self, provider: str, model: str) -> bool:
        return self.provider == provider and self.model == model

    def can_resume(self, provider: str, model: str) -> bool:
        return bool(self.provider_session_id) and self.matches_planner(provider, model)

    def append_turns(
        self,
        *,
        user_text: str,
        planner_text: str,
        provider: str,
        model: str,
        provider_session_id: str | None,
    ) -> None:
        now = _now_iso()
        self.turns.append(AskTurn(role="user", text=user_text, at=now))
        self.turns.append(AskTurn(role="planner", text=planner_text, at=now))
        self.provider = provider
        self.model = model
        if provider_session_id:
            self.provider_session_id = provider_session_id
        self.updated_at = now

    def last_user_text(self) -> str | None:
        for turn in reversed(self.turns):
            if turn.role == "user":
                return turn.text
        return None


def clear_conversation(repo_root: Path) -> None:
    AskConversation().clear(repo_root)


def build_work_prompt(conversation: AskConversation) -> str:
    last_user = conversation.last_user_text()
    if last_user is None:
        raise ValueError("conversation has no user turns")

    parts = [
        "From a kcia ask conversation.",
        "",
        "## Latest user intent",
        last_user,
        "",
        "## Conversation",
    ]
    for turn in conversation.turns:
        label = "User" if turn.role == "user" else "Planner"
        parts.extend([f"### {label}", turn.text, ""])
    return "\n".join(parts).strip() + "\n"
