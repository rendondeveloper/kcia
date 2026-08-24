# Safe text input for `kcia work` and `kcia work answer`

## Analysis

Original ask: normalize the free text passed to `kcia work "<text>"` / `kcia work answer "<text>"`
so stray whitespace doesn't leak into the stored task/prompt text.

Follow-up ask (this revision): the user tried passing a large, multi-line Markdown spec
(headings, `---` rules, numbered lists, backticked paths, parens) directly as a quoted argument to
`kcia work "..."` and it "no lo puede leer bien" (doesn't read it well). This is not a whitespace
problem — it's that **arbitrarily-structured multi-line text does not survive shell argv reliably**:
double-quoted strings in zsh/bash still expand `` ` `` (command substitution), `$` (variable/command
substitution), and can be broken by an odd number of embedded `"`; long pastes with blank lines are
also just unwieldy and error-prone to quote correctly by hand. No amount of normalization *inside*
kcia fixes this, because by the time kcia's Python code sees `sys.argv`, the shell has already
possibly mis-parsed or mangled the text. The real fix is giving the user a way to supply this text
that never goes through shell argument parsing at all: read it from a file or from stdin.

This supersedes the whitespace-collapsing behavior proposed in the first draft of this plan for the
*body* text case: collapsing all internal whitespace/newlines to single spaces (as originally
proposed) would destroy exactly the Markdown structure (headings, lists, blank-line-separated
sections) the user needs to preserve when injecting a spec like the one above. Whitespace handling
is narrowed to trimming only (see below).

### Current behavior

- `cli/src/kcia/commands/work.py::WorkGroup.resolve_command` collects leading non-flag argv tokens
  into `state["work_text"]` via `" ".join(text_parts)` (line 95) — this only ever sees whatever the
  shell handed to argv, already possibly mangled for large/complex pastes.
- `work_answer` (`answer`/`inject`, lines 652-682) takes `text: list[str]` from Typer, joins with
  `" ".join(text)` (line 672), same constraint.
- Neither command has a way to read text from a file or stdin today — argv is the only input path.
- Downstream: `Session.create` (`session.py:129-168`) stores the text as `task["title"]` /
  `task["prompt"]`; `add_injection` (`session.py:332`) appends verbatim to `injections`. Both are
  presumably later interpolated into agent prompts by the wave runner.

### Proposed change

**1. New input paths that bypass the shell (the actual fix for this report):**

- Add `--file PATH` / `-f PATH` to both `kcia work` and `kcia work answer`. Reads the full file
  contents (UTF-8) as the text, instead of (or in addition to — mutually exclusive, error if both
  given) the positional text.
- Add stdin support: `kcia work --stdin` / `kcia work answer --stdin` reads all of stdin as the
  text. This needs to be an explicit flag rather than "no positional args = read stdin", because
  `kcia work` with no args already means "continue the active session" (`commands/work.py:560-564`)
  and must not change behavior.
- Both new flags are mutually exclusive with each other and with positional text: passing more than
  one text source is a usage error (`typer.echo(...)` + `raise typer.Exit(code=1)`), matching the
  file's existing error-reporting convention.
- Document in `--help` and in `_render_blocked`/`_render_approval_gate` hints (`commands/work.py:224-259`,
  which currently only show `kcia work answer "<your answer>"`) that `--file`/`--stdin` exist for
  long answers — small doc-string/help text change only, not a behavior change to those functions.

**2. Whitespace handling (narrowed from the original draft):**

- New `normalize_text(text: str) -> str` in a new `cli/src/kcia/text.py`. Behavior: strip only
  leading/trailing whitespace (`text.strip()`), and normalize line endings (`\r\n`/`\r` → `\n`) for
  text that may come from a file. **Internal whitespace, blank lines, and Markdown structure are
  left untouched** — no collapsing of newlines or repeated spaces, since that would corrupt
  multi-line input like the spec pasted above.
- Applied to all three sources (positional argv text, `--file` contents, `--stdin` contents) at the
  same point: `state["work_text"]` in `WorkGroup.resolve_command` (`commands/work.py:95`), and the
  joined text in `work_answer` (`commands/work.py:672`).
- Empty-after-strip guard unchanged from the original draft: falsy `work_text` continues to mean
  "resume active session"; `work_answer` errors out (exit 1) rather than recording an empty
  injection, if the file/stdin content is empty or whitespace-only.

### Files touched

- `cli/src/kcia/text.py` (new): `normalize_text`.
- `cli/src/kcia/commands/work.py`:
  - `--file`/`-f` and `--stdin` options on `work()` and `work_answer()`.
  - mutual-exclusion validation between positional text / `--file` / `--stdin`.
  - apply `normalize_text` at the two existing entry points.
  - help text updates in `_render_blocked`/`_render_approval_gate`.
- `tests/`: `normalize_text` unit tests (strip, line-ending normalization, empty-after-strip);
  CLI tests for `kcia work --file`, `kcia work answer --file`, stdin variants, and the
  mutual-exclusion error. Exact file(s) TBD during implementation (likely a new `tests/test_text.py`
  plus additions to the existing `work` command test file).

## Open questions

None currently. Confirm before implementation:
- Flag names `--file`/`-f` and `--stdin` are assumed; say so in this file if you'd prefer different
  names (e.g. `--from-file`, `-`  as a positional sentinel for stdin instead of a flag).

## Version bump

**minor** (`0.16.4` → `0.17.0`): `--file`/`--stdin` are new user-facing capabilities on
`work`/`answer`, not just an internal hygiene fix. The earlier patch (`0.16.4`) collapsed internal
whitespace; this release replaces that with strip + line-ending normalization so Markdown specs
stay intact.
