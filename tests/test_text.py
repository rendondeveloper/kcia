from kcia.text import normalize_text


def test_normalize_text_strips_surrounding_whitespace() -> None:
    assert normalize_text("  fix   the overflow  ") == "fix   the overflow"


def test_normalize_text_preserves_markdown_structure() -> None:
    spec = "# Title\n\n---\n\n1. item\n2. `path/to/file`"
    assert normalize_text(f"\n{spec}\n") == spec


def test_normalize_text_normalizes_line_endings() -> None:
    assert normalize_text("a\r\nb\rc") == "a\nb\nc"


def test_normalize_text_empty_after_strip() -> None:
    assert normalize_text("  \t\n  ") == ""
    assert normalize_text("") == ""
