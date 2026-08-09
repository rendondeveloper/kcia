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


DEFAULT_MAX_PROMPT_TOKENS = 120_000
DEFAULT_DROP_ORDER = (
    "architecture",
    "monorepo",
    "data",
    "api",
    "web",
    "accessibility",
    "testing",
    "validation",
    "coding",
)


def apply_budget(
    entries: list,
    *,
    fixed_tokens: int,
    max_tokens: int,
    drop_order: list[str],
) -> tuple[list, list]:
    """Devuelve (conservadas, descartadas).

    Algoritmo, exacto:
      1. Si fixed_tokens + suma(entries) <= max_tokens: devuelve (entries, []).
      2. Recorre drop_order. Por cada tag, descarta TODAS las entradas cuyo
         conjunto de tags contenga ese tag y que sigan presentes.
      3. Tras cada tag descartado, recomprueba el total. Para en cuanto quepa.
      4. Si tras agotar drop_order sigue sin caber, devuelve lo que queda; NO
         trunca a mitad de archivo y NO lanza.
    """
    from kcia.profiles.inheritance import ReferenceEntry

    def entry_tokens(entry: ReferenceEntry) -> int:
        if not entry.path.is_file():
            return 0
        return estimate_tokens(entry.path.read_text(encoding="utf-8"))

    def total_tokens(remaining: list[ReferenceEntry]) -> int:
        return fixed_tokens + sum(entry_tokens(entry) for entry in remaining)

    if total_tokens(entries) <= max_tokens:
        return list(entries), []

    kept = list(entries)
    dropped: list[ReferenceEntry] = []
    for tag in drop_order:
        if total_tokens(kept) <= max_tokens:
            break
        to_drop = [entry for entry in kept if tag in entry.tags]
        if not to_drop:
            continue
        for entry in to_drop:
            kept.remove(entry)
            dropped.append(entry)
        if total_tokens(kept) <= max_tokens:
            break
    return kept, dropped
