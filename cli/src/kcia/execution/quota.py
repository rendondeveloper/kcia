"""Quota snapshots and persistence.

KCIA treats missing quota information as unknown, not zero. Percentages are
only actionable when the provider or a clearly-labelled local estimate supplies
enough information to compute them.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable, Literal

from kcia.config import USER_DATA_DIR

QuotaConfidence = Literal["exact", "estimated", "unknown"]


@dataclass(frozen=True)
class QuotaWindow:
    id: str
    scope: str
    window: str
    confidence: QuotaConfidence
    used_percent: float | None = None
    used: float | None = None
    limit: float | None = None
    resets_at: datetime | None = None
    source: str | None = None

    def is_exhausted(self) -> bool:
        if self.used_percent is not None:
            return self.used_percent >= 100
        if self.used is not None and self.limit is not None:
            return self.used >= self.limit
        return False

    def should_warn(self, threshold: float) -> bool:
        return self.used_percent is not None and self.used_percent >= threshold


@dataclass(frozen=True)
class QuotaSnapshot:
    account_key: str
    target_id: str
    observed_at: datetime
    confidence: QuotaConfidence
    windows: tuple[QuotaWindow, ...] = ()
    expires_at: datetime | None = None
    available: bool | None = None

    def is_available(self, now: datetime | None = None) -> bool | None:
        now = now or datetime.now(UTC)
        if self.expires_at is not None and self.expires_at <= now:
            return None
        if self.available is not None:
            return self.available
        if not self.windows:
            return None
        return not any(window.is_exhausted() for window in self.windows)

    def warning_windows(self, threshold: float) -> tuple[QuotaWindow, ...]:
        return tuple(window for window in self.windows if window.should_warn(threshold))


class QuotaStore:
    def __init__(
        self,
        root: Path | None = None,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.root = root or USER_DATA_DIR / "quota"
        self.clock = clock or (lambda: datetime.now(UTC))

    def save(self, snapshot: QuotaSnapshot) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        path = self._path(snapshot.account_key, snapshot.target_id)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(_snapshot_to_json(snapshot), indent=2, sort_keys=True),
            encoding="utf-8",
        )
        os.replace(tmp, path)

    def load(self, account_key: str, target_id: str) -> QuotaSnapshot | None:
        path = self._path(account_key, target_id)
        if not path.is_file():
            return None
        return _snapshot_from_json(json.loads(path.read_text(encoding="utf-8")))

    def _path(self, account_key: str, target_id: str) -> Path:
        raw = f"{account_key}__{target_id}"
        safe = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in raw)
        return self.root / f"{safe}.json"


def _snapshot_to_json(snapshot: QuotaSnapshot) -> dict:
    data = asdict(snapshot)
    for key in ("observed_at", "expires_at"):
        if data[key] is not None:
            data[key] = data[key].isoformat()
    for window in data["windows"]:
        if window["resets_at"] is not None:
            window["resets_at"] = window["resets_at"].isoformat()
    return data


def _snapshot_from_json(data: dict) -> QuotaSnapshot:
    windows = []
    for raw in data.get("windows", []):
        if raw.get("resets_at"):
            raw["resets_at"] = datetime.fromisoformat(raw["resets_at"])
        windows.append(QuotaWindow(**raw))
    expires_at = data.get("expires_at")
    return QuotaSnapshot(
        account_key=data["account_key"],
        target_id=data["target_id"],
        observed_at=datetime.fromisoformat(data["observed_at"]),
        confidence=data["confidence"],
        windows=tuple(windows),
        expires_at=datetime.fromisoformat(expires_at) if expires_at else None,
        available=data.get("available"),
    )
