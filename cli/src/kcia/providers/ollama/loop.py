"""In-process agent loop for Ollama."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from kcia.providers.base import RunRequest, RunResult
from kcia.providers.events import (
    FileRead,
    FileWrite,
    ProviderError,
    StreamEvent,
    StreamState,
    TextDelta,
    ToolCallEnd,
    ToolCallStart,
    TurnEnd,
    UsageUpdate,
)
from kcia.providers.ollama.client import OllamaClient, OllamaError
from kcia.providers.ollama.tools import (
    READ_TOOLS,
    WRITE_TOOLS,
    execute_tool,
    parse_tool_arguments,
    tool_definitions,
)

MAX_ITERATIONS = 50
DEFAULT_NUM_CTX = 32768

_SYSTEM_PROMPT = (
    "You are a coding agent working in a local repository. "
    "Use the provided tools to read, search, and modify files as needed. "
    "Prefer small, focused edits. When done, reply with a concise summary."
)


def run_agent_loop(
    client: OllamaClient,
    req: RunRequest,
    *,
    num_ctx: int = DEFAULT_NUM_CTX,
    on_event: Callable[[StreamEvent], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> RunResult:
    state = StreamState()
    events: list[StreamEvent] = []
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": req.prompt},
    ]
    tools = tool_definitions(req)
    think = _think_enabled(req.effort)
    options = {"num_ctx": num_ctx}
    cancelled = False

    def _emit(event: StreamEvent) -> None:
        events.append(event)
        _apply_event(state, event)
        if on_event:
            on_event(event)

    try:
        if not client.reachable():
            _emit(ProviderError(message="Ollama daemon is not reachable", fatal=True))
            return _build_result(state, events, cancelled=cancelled)

        available = client.model_names()
        if available and req.model not in available:
            _emit(
                ProviderError(
                    message=f"model '{req.model}' is not available locally",
                    fatal=True,
                )
            )
            return _build_result(state, events, cancelled=cancelled)

        for _ in range(MAX_ITERATIONS):
            if should_cancel and should_cancel():
                cancelled = True
                break

            assistant_message: dict[str, Any] = {"role": "assistant", "content": ""}
            tool_calls: list[dict[str, Any]] = []

            for chunk in client.chat_stream(
                model=req.model,
                messages=messages,
                tools=tools,
                options=options,
                think=think,
            ):
                if should_cancel and should_cancel():
                    cancelled = True
                    break

                message = chunk.get("message")
                if isinstance(message, dict):
                    content = message.get("content")
                    if isinstance(content, str) and content:
                        assistant_message["content"] = (
                            assistant_message.get("content", "") + content
                        )
                        if req.stream:
                            _emit(TextDelta(text=content))
                        state.final_text = (state.final_text or "") + content

                    thinking = message.get("thinking")
                    if isinstance(thinking, str) and thinking:
                        # Reasoning must not pollute the final assistant text.
                        pass

                    chunk_tools = message.get("tool_calls")
                    if isinstance(chunk_tools, list):
                        tool_calls = _merge_tool_calls(tool_calls, chunk_tools)

                if chunk.get("done"):
                    prompt_tokens = int(chunk.get("prompt_eval_count") or 0)
                    output_tokens = int(chunk.get("eval_count") or 0)
                    state.input_tokens = prompt_tokens
                    state.output_tokens = output_tokens
                    _emit(
                        UsageUpdate(
                            input_tokens=prompt_tokens,
                            output_tokens=output_tokens,
                            cached=0,
                        )
                    )

            if cancelled:
                break

            if tool_calls:
                assistant_message["tool_calls"] = tool_calls
                messages.append(assistant_message)
                for call in tool_calls:
                    if should_cancel and should_cancel():
                        cancelled = True
                        break
                    _handle_tool_call(call, req, state, _emit)
                    result_content = call.get("_result", "")
                    messages.append(
                        {
                            "role": "tool",
                            "content": result_content,
                        }
                    )
                if cancelled:
                    break
                continue

            messages.append(assistant_message)
            break

        if state.final_text is not None:
            _emit(TurnEnd(final_text=state.final_text))
    except OllamaError as exc:
        _emit(ProviderError(message=str(exc), fatal=True))

    return _build_result(
        state,
        events,
        cancelled=cancelled,
        cancel_reason="cancelled by user" if cancelled else None,
    )


def _think_enabled(effort: str | None) -> bool:
    if effort is None:
        return False
    return effort.lower() in {"high", "medium", "max"}


def _merge_tool_calls(
    existing: list[dict[str, Any]],
    incoming: list[Any],
) -> list[dict[str, Any]]:
    merged = list(existing)
    for item in incoming:
        if isinstance(item, dict):
            merged.append(item)
    return merged


def _handle_tool_call(
    call: dict[str, Any],
    req: RunRequest,
    state: StreamState,
    emit: Callable[[StreamEvent], None],
) -> None:
    function = call.get("function") if isinstance(call.get("function"), dict) else {}
    name = str(function.get("name") or "unknown")
    raw_args = function.get("arguments")
    args = parse_tool_arguments(raw_args)
    preview = json.dumps(args if args is not None else raw_args)[:200]

    state.tool_calls += 1
    emit(ToolCallStart(name=name, input_preview=preview))

    if args is None:
        result = "invalid tool arguments"
        ok = False
    else:
        ok, result = execute_tool(name, args, req)

    emit(ToolCallEnd(name=name, ok=ok))
    call["_result"] = result

    path = _tool_path(args) if args is not None else None
    if path and ok:
        if name in READ_TOOLS:
            state.files_read.add(path)
            emit(FileRead(path=path))
        elif name in WRITE_TOOLS:
            state.files_written.add(path)
            emit(FileWrite(path=path))


def _tool_path(args: dict[str, Any] | None) -> str | None:
    if not args:
        return None
    value = args.get("path")
    return value if isinstance(value, str) and value else None


def _apply_event(state: StreamState, event: StreamEvent) -> None:
    if isinstance(event, TurnEnd):
        state.final_text = event.final_text
    elif isinstance(event, UsageUpdate):
        state.input_tokens = event.input_tokens
        state.output_tokens = event.output_tokens
        state.cached_tokens = event.cached


def _build_result(
    state: StreamState,
    events: list[StreamEvent],
    *,
    cancelled: bool = False,
    cancel_reason: str | None = None,
) -> RunResult:
    fatal = any(
        isinstance(event, ProviderError) and event.fatal for event in events
    )
    output_text = state.final_text or "".join(
        event.text for event in events if isinstance(event, TextDelta)
    )
    tokens_used = None
    if state.input_tokens or state.output_tokens:
        tokens_used = state.input_tokens + state.output_tokens
    return RunResult(
        output_text=output_text,
        exit_code=1 if fatal else 0,
        tokens_used=tokens_used,
        input_tokens=state.input_tokens,
        output_tokens=state.output_tokens,
        cached_tokens=state.cached_tokens,
        tool_calls=state.tool_calls,
        files_read=tuple(sorted(state.files_read)),
        files_written=tuple(sorted(state.files_written)),
        session_id=state.session_id,
        cancelled=cancelled,
        cancel_reason=cancel_reason,
    )
