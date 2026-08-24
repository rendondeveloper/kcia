"""Terminal title helpers for integrated terminals."""

from kcia.terminal_title import label_from_argv, set_terminal_title


def test_label_from_argv_work_subcommand() -> None:
    assert label_from_argv(["work", "approve"]) == "kcia work approve"


def test_label_from_argv_skips_flags_and_paths() -> None:
    assert label_from_argv(
        ["skill", "--backend", "--path", ".cursor/skills/deploy", "--deploy"]
    ) == "kcia skill"


def test_label_from_argv_version() -> None:
    assert label_from_argv(["--version"]) == "kcia"


def test_set_terminal_title_writes_osc_on_tty() -> None:
    import io

    buf = io.StringIO()
    buf.isatty = lambda: True  # type: ignore[method-assign]
    set_terminal_title("kcia work", stream=buf)
    out = buf.getvalue()
    assert "\033]0;kcia work\007" in out
    assert "\033]633;E;kcia work\033\\" in out


def test_set_terminal_title_skips_non_tty() -> None:
    import io

    buf = io.StringIO()
    set_terminal_title("kcia work", stream=buf)
    assert buf.getvalue() == ""
