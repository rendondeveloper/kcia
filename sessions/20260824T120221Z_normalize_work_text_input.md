# Normalize free-text input for `kcia work` and `kcia work answer`

## Analysis

The user asked (in a mix of Spanish/English) for the free text passed to `kcia work "<text>"` and
`kcia work answer "<text>"` to be normalized before it is used, so that stray whitespace does not
end up baked into the task/prompt text. This is unrelated to a second topic raised earlier (a
`photoUrl` mapping bug in an unrelated checklist app, `system-track-monitor.web.app`) — the user's
clarifying answer confirms the actual request is scoped to `kcia`'s own `work`/`answer` text
handling, and the zsh `no matches found: dos` error was a shell-quoting issue on their end, not a
bug in kcia.

### Current behavior

- `cli/src/kcia/commands/work.py::WorkGroup.resolve_command` collects the leading non-flag
  argv tokens into `state["work_text"]` via `" ".join(text_parts)` (line 95). This is the text used
  to create a new task (`kcia work "<text>"`).
- `work_answer` (the `answer`/`inject` command, line 652-682) takes `text: list[str]` from Typer and
  joins it with `" ".join(text)` (line 672) before calling `session.add_injection(...)`.
- Neither path strips leading/trailing whitespace or collapses internal runs of whitespace
  (e.g. multiple spaces, tabs, embedded newlines from a multi-line paste) before the text is:
  - used as the task `title`/`prompt` (`Session.create`, `session.py:145-155`), and
  - matched against the ticket-key regex in `classify_input` (`session.py:86`, which already does
    `text.strip()` locally for matching, but the *stored* `text`/`title`/`prompt` themselves are
    not stripped/normalized), and
  - appended verbatim to `session.data["injections"]` (`add_injection`, `session.py:332`).
- Downstream, these strings are written into `.ai/*` session/manifest files and are presumably
  later interpolated into agent prompts (wave runner), so unnormalized whitespace (leading/trailing
  spaces, doubled spaces, stray newlines from copy-paste) propagates into the prompt text.

### Proposed change

Add a single small text-normalization helper and apply it at the two entry points where free text
enters the system, so all downstream consumers (`Session.create`, `add_injection`, `classify_input`)
already receive normalized text — no changes needed further downstream.

- New function `normalize_text(text: str) -> str` in a small shared location — proposed:
  `cli/src/kcia/text.py` (new module; `git/flow.py` and `profiles/schema.py` were checked and don't
  have a generic reusable helper here, so a new small module keeps this out of unrelated files).
  Behavior: strip leading/trailing whitespace, collapse any run of whitespace (including newlines/
  tabs) into a single ASCII space (`re.sub(r"\s+", " ", text).strip()`). This does not attempt
  Unicode NFC normalization or quote/shell escaping — the shell-quoting issue the user saw is
  outside kcia's control (it happens before argv reaches Python), so this plan only addresses
  whitespace cleanup of the text kcia itself receives via argv/Typer.
- Apply `normalize_text` to:
  - `state["work_text"]` in `WorkGroup.resolve_command` (`commands/work.py:95`), right after the
    `" ".join(text_parts)` call — this is the text used for `kcia work "<text>"` task creation.
  - the joined text in `work_answer` (`commands/work.py:672`), replacing
    `session.add_injection(" ".join(text))` with `session.add_injection(normalize_text(" ".join(text)))`.
- Guard against normalizing to an empty string: if `normalize_text(text)` is empty (e.g. the user
  passed only whitespace), keep existing behavior for `work_text` being falsy (routes to "continue
  active session" in `work()`, `commands/work.py:560`) and, for `work_answer`, echo an error and
  exit 1 rather than recording an empty injection (matches the pattern used elsewhere in this file,
  e.g. `_validate_scope`).

### Files touched

- `cli/src/kcia/text.py` (new): `normalize_text`.
- `cli/src/kcia/commands/work.py`: import and apply `normalize_text` at the two entry points above,
  plus the empty-after-normalization guard in `work_answer`.
- `tests/`: add unit coverage for `normalize_text` (whitespace collapsing, strip, empty-after-strip)
  and for `work answer` rejecting a whitespace-only answer. Exact test file location to be decided
  during implementation (likely alongside existing `tests/test_cli_help.py`-style CLI tests / a new
  `tests/test_text.py`).

## Open questions

None — clarified with the user: this is a kcia-only whitespace-normalization request for the
`work`/`answer` free-text arguments, unrelated to the checklist/photoUrl topic and unrelated to the
zsh glob error (confirmed shell-side).

## Version bump

**patch** (`0.16.3` → `0.16.4`): internal input-hygiene fix, no new capability, no breaking change.
