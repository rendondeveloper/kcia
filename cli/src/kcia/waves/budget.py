"""Token estimation and prompt composition accounting."""

from __future__ import annotations

from dataclasses import dataclass, field

CHARS_PER_TOKEN = 4


def estimate_tokens(text: str) -> int:
    """Heurística de 4 chars/token. Es una estimación, no un contador exacto;
    se usa para decisiones de truncado y para métricas comparativas."""
    if not text:
        return 0
    return len(text) // CHARS_PER_TOKEN


@dataclass(frozen=True)
class SectionStat:
    name: str
    chars: int
    tokens: int
    dropped: bool = False


@dataclass
class PromptStats:
    sections: list[SectionStat] = field(default_factory=list)

    @property
    def total_tokens(self) -> int:
        return sum(section.tokens for section in self.sections)

    @property
    def dropped_tokens(self) -> int:
        return sum(section.tokens for section in self.sections if section.dropped)

    def as_dict(self) -> dict:
        return {
            "total_tokens": self.total_tokens,
            "dropped_tokens": self.dropped_tokens,
            "sections": [
                {
                    "name": section.name,
                    "chars": section.chars,
                    "tokens": section.tokens,
                    "dropped": section.dropped,
                }
                for section in self.sections
            ],
        }
