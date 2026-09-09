# Update OpenCode Account Model Catalog

## Analysis

The repository already has a working OpenCode provider adapter, registry entry,
CLI model command, runtime execution path, and OpenCode authentication check.
The remaining issue is the static `opencode` model catalog in
`control-plane/providers/catalog.yaml`.

The authenticated local OpenCode CLI is version `1.18.30`, and
`opencode models` currently reports these 34 model ids:

- `opencode/big-pickle`
- `opencode/ling-3.0-flash-fin-free`
- `opencode/mimo-v2.5-free`
- `opencode/muse-spark-1.2-contributor-free`
- `opencode/muse-spark-1.3-contributor-free`
- `opencode/nemotron-3-ultra-free`
- `opencode/nemotron-3.5-lightning-free`
- `opencode-go/deepseek-v4-flash`
- `opencode-go/deepseek-v4-flash-vision-exp`
- `opencode-go/deepseek-v4-pro`
- `opencode-go/glm-5.1`
- `opencode-go/glm-5.2`
- `opencode-go/glm-5.3`
- `opencode-go/glm-5.3-flash`
- `opencode-go/gpt-5.6-luna`
- `opencode-go/grok-4.6`
- `opencode-go/hy3`
- `opencode-go/hy4-preview`
- `opencode-go/kimi-k2.6`
- `opencode-go/kimi-k2.7-code`
- `opencode-go/kimi-k3`
- `opencode-go/longcat-2.0`
- `opencode-go/mimo-v2.5`
- `opencode-go/mimo-v2.5-pro`
- `opencode-go/minimax-m2.7`
- `opencode-go/minimax-m3`
- `opencode-go/muse-spark-1.2-contributor`
- `opencode-go/muse-spark-1.3-contributor`
- `opencode-go/omen-alpha`
- `opencode-go/qwen3.6-plus`
- `opencode-go/qwen3.7-max`
- `opencode-go/qwen3.7-plus`
- `opencode-go/qwen3.8-flash`
- `opencode-go/qwen3.8-max`

The current catalog contains seven older free-tier entries, including two ids
that the installed CLI no longer reports. Because `kcia agent set` validates
non-live providers against this catalog, users cannot select the other models
available to the authenticated OpenCode account.

No open questions remain: the requested source of truth is the current output
of the authenticated local `opencode models` command, not a hand-picked subset
or the broader upstream catalog.

## Plan

1. Replace the `opencode.models` list in `control-plane/providers/catalog.yaml`
   with the 34 model ids captured above, removing ids that are no longer
   reported by the account.
2. Keep `opencode/big-pickle` as the default because it is still available and
   is the existing balanced default. Preserve useful tier and `best_for`
   metadata for the known free-tier models; add the remaining account models
   with their ids and conservative metadata so they are selectable without
   claiming unsupported capabilities.
3. Update the CLI version in `cli/src/kcia/__init__.py` from `0.20.1` to
   `0.21.0`. This is a minor bump because the change expands the selectable
   model capability for an existing provider.
4. Run the provider/catalog-focused tests, then the complete test suite.
5. Verify with `opencode models`, `kcia agent models --live opencode`, and the
   relevant `kcia agent set` validation that current account models are accepted
   and obsolete ids are no longer presented.

## Version Bump

Minor: `0.21.0`. The provider already existed, but exposing the complete set of
models available to the authenticated account is an additive user-facing
capability rather than a bug-only or internal-only change.

## Implementation Results

- Updated `control-plane/providers/catalog.yaml` with all 34 ids reported by
  OpenCode `1.18.30` and removed the obsolete `opencode/hy3-free` and
  `opencode/x-preview-f-free` entries.
- Kept `opencode/big-pickle` as the default model.
- Bumped `cli/src/kcia/__init__.py` to `0.21.0`.
- Extended `tests/test_agents.py` to cover the expanded catalog and obsolete
  ids.
- Reinstalled the editable package so installed metadata matches `0.21.0`.
- `opencode-go/gpt-5.6-luna` is accepted by `kcia agent set`.
- `opencode/hy3-free` is rejected as expected.
- Exact comparison between `opencode models` and `kcia agent models --json
  opencode` produced no differences.
- Full test suite: `411 passed, 10 warnings`.
