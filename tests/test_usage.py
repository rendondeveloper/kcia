from kcia.usage import collect_usage, format_tokens
from kcia.waves.runner import _Usage


class _Result:
    def __init__(self, inp: int, out: int, cached: int = 0, tool_calls: int = 0) -> None:
        self.input_tokens = inp
        self.output_tokens = out
        self.cached_tokens = cached
        self.tool_calls = tool_calls


def test_format_tokens_is_compact() -> None:
    assert format_tokens(0) == "0"
    assert format_tokens(999) == "999"
    assert format_tokens(1_000) == "1k"
    assert format_tokens(18_422) == "18.4k"
    assert format_tokens(1_200_000) == "1.2M"


def test_usage_accumulates_across_provider_calls() -> None:
    """A wave that retries validation calls the provider more than once."""
    usage = _Usage()
    usage.add(_Result(1_000, 200, cached=50, tool_calls=3))
    usage.add(_Result(1_500, 300, cached=80, tool_calls=4))

    assert usage.input_tokens == 2_500
    assert usage.output_tokens == 500
    assert usage.cached_tokens == 130
    assert usage.tool_calls == 7
    assert usage.calls == 2
    assert usage.total == 3_000


def test_collect_usage_sums_session_waves() -> None:
    waves = {
        "understanding": {
            "tokens": 18_000,
            "input_tokens": 16_000,
            "output_tokens": 2_000,
            "cached_tokens": 9_000,
            "tool_calls": 31,
            "provider_calls": 1,
        },
        "analysis": {
            "tokens": 22_000,
            "input_tokens": 20_000,
            "output_tokens": 2_000,
            "cached_tokens": 11_000,
            "tool_calls": 12,
            "provider_calls": 1,
        },
        "implementation": {"status": "pending"},
    }

    totals = collect_usage(waves)
    assert totals.total == 40_000
    assert totals.input_tokens == 36_000
    assert totals.output_tokens == 4_000
    assert totals.cached_tokens == 20_000
    assert totals.tool_calls == 43
    assert totals.provider_calls == 2
    assert totals.per_wave == {"understanding": 18_000, "analysis": 22_000}
    assert "implementation" not in totals.per_wave
