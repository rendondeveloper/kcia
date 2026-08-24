from kcia.text import normalize_text


def test_normalize_text_strips_and_collapses_whitespace() -> None:
    assert normalize_text("  fix   the overflow  ") == "fix the overflow"


def test_normalize_text_collapses_newlines_and_tabs() -> None:
    assert normalize_text("fix\tthe\noverflow") == "fix the overflow"


def test_normalize_text_empty_after_strip() -> None:
    assert normalize_text("  \t\n  ") == ""
    assert normalize_text("") == ""
