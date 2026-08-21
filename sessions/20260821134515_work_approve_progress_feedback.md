# Working feedback during `implementation` wave (post-approval)

## Analysis

The user reports that after approving a wave (e.g. `kcia work approve`) and while the
`implementation` wave runs, no animation or feedback is visible — it feels like nothing
is happening. They explicitly do **not** want a scrolling, top-to-bottom stream of
output like Claude Code or Cursor show; they want something more like a small, contained
"working" indicator that communicates progress without pushing the console content
around.

A live-progress mechanism already exists in this codebase:

- `cli/src/kcia/waves/progress.py` — `WaveProgress` renders a single self-updating
  status line (spinner + activity + elapsed time/tokens), using `\r` to redraw in
  place. This is exactly the "small, non-scrolling indicator" shape the user is asking
  for.
- It's wired in via `cli/src/kcia/commands/work.py`'s `_ProgressReporter`
  (`work.py:244-272`), constructed with `enabled=not quiet` (`work.py:327`) and driven
  by `on_event`/`on_wave_start` callbacks threaded through `run_wave()` in
  `waves/runner.py`.
- Crucially, `WaveProgress.__init__` (`progress.py:53`) decides whether to animate
  based on `stream.isatty()`:
  ```python
  self._tty = enabled if enabled is not None else self._stream.isatty()
  ```
  When the output stream is **not** a TTY (piped output, output captured by another
  tool/agent, redirected to a file/log), it falls back to writing exactly **one**
  static line (`"{header} — running"`) at `start()` and does not print anything again
  until `finish()`. On a non-TTY stream, this matches the user's exact complaint:
  visually, nothing happens between "Approved" and the final completion line, even
  though work is progressing.

So there are two candidate root causes, and they call for different fixes:

1. **The user is on an interactive terminal (TTY) and still sees nothing.** That would
   point to a bug in the spinner itself (e.g. rendering to `stderr` while something
   else swallows/redirects `stderr`, buffering, or the reporter never receiving
   `on_event` callbacks for the `implementation` wave specifically).
2. **The user is running `kcia work` through a non-interactive wrapper** (e.g. Claude
   Code's own Bash tool, a CI runner, a script capturing output) where `isatty()` is
   `False` by construction. In that case, no in-place redraw is possible in the
   traditional terminal-control-code sense, but we can still improve the "static
   fallback" mode to periodically emit updated status lines (still not a wall of
   scrolling text, but a small number of update lines: e.g. "implementation — writing
   foo.py · 12s · 3 tools" appearing every N seconds) instead of one line that never
   updates again.

## Open questions

1. ~~Where exactly are you running `kcia work approve` when you see no feedback?~~
   **Answered**: the user is running `kcia work approve` in a non-interactive context
   (output captured by a wrapping tool/agent, not a real TTY). The fix must therefore
   improve the non-TTY fallback path, not just the TTY animation.
2. **Scope, answered**: this change applies **only** to `kcia work approve` — so the
   user can watch progress during that specific approve+run flow. Every other command
   (`kcia work`, `kcia work retry`, etc.) keeps using the existing `\r`-based
   `WaveProgress` spinner exactly as it works today; no behavior change for them.
3. ~~Display shape for the `work approve` non-TTY fallback~~ **Answered**: no fixed
   format required — any animation/representation that visibly communicates "the model
   is building right now" is acceptable. Going with the periodic single-line reprint
   (spinner frame + activity + elapsed/tool-count, reusing the existing formatting
   already built in `_animate_once`/`finish`) described in the plan below, since it's
   the smallest change that reuses existing machinery and stays non-scrolling in
   spirit; open to adjusting the exact wording/format later if it doesn't feel right
   once seen in practice.

## Proposed plan

Scope: `cli/src/kcia/commands/work.py` (`work_approve` / `_execute` /
`_ProgressReporter`) and `cli/src/kcia/waves/progress.py` (`WaveProgress`) only.
Behavior for every other command that uses `WaveProgress` is unchanged.

1. Add a non-TTY periodic-update mode to `WaveProgress`, opted into only from the
   `work approve` path (e.g. a constructor flag like `periodic_updates: bool = False`,
   or a small subclass), so the class's default behavior for all other callers is
   untouched:
   - When `not self._tty` and periodic updates are enabled, instead of writing exactly
     one static line at `start()` and staying silent until `finish()`, spawn the same
     kind of background loop as `_animate()`, but on a longer interval (e.g. every
     2 seconds) and using `_write_line(...)` (plain newline-terminated), not `_render`
     (`\r`-based), since there's no cursor control to rely on off a TTY.
   - Each periodic line reuses the existing activity/elapsed/tool-count/token
     formatting already built in `_animate_once`/`finish` (extract a shared formatter
     to avoid duplicating that logic).
2. In `work.py`, thread this flag through only for the `work_approve` command's call
   into `_execute`/`_ProgressReporter` (e.g. `_ProgressReporter(enabled=not quiet,
   periodic_updates=True)` for `work_approve`, default `False` everywhere else,
   including the plain `work()` command).
3. Tests: extend `WaveProgress` unit tests (wherever `progress.py` is currently
   tested) to cover the new non-TTY periodic mode — assert multiple lines get written
   over simulated time via the injectable `clock`, and that the default (no flag /
   other commands) path is unchanged (still exactly one static line off-TTY).
4. Bump `VERSION` in `cli/src/kcia/__init__.py` — **minor** bump (new opt-in
   capability, no breaking change, no change to default behavior for existing
   commands).

All open questions are resolved. Ready to implement pending your go-ahead.

## Implementation

- **Version**: `0.9.0` (minor — new opt-in `periodic_updates` capability for `work approve` non-TTY path; default behavior unchanged for all other commands).
