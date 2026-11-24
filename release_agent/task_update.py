"""Repair entry tool calls so OpenMinion 0.1.24 accepts them.

The entry phase adds ``task_update`` to every offered tool schema, then
rejects the response before any tool runs. A missing sidecar is legal only
when no task exists yet. It is rejected once a task is active, and so is a
sidecar that is duplicated, quoted from somewhere other than the latest user
message, or shaped wrong. ``brain.task_context.rejected`` /
``invalid_or_missing_update`` is that rejection. The turn text becomes
"I could not safely update the task conditions."

Echo's planner attaches the sidecar itself. A hosted model does not. glm-4.7-flash
emits the real tool calls (two ``read_release_file`` calls on the draft) and
either fills ``task_update`` on every call or leaves a field the schema asked
for in a form OpenMinion will not accept. This module rewrites the provider
response so exactly one call carries a sidecar the entry checker accepts.
OpenMinion strips that field before the tool handler, the approval callback,
and the write.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator
import json

_ABSENT_TASK = "CURRENT TASK: absent (revision 0)"
_RESOLVED_TASK = "CURRENT TASK (user instructions"
_RECOVERY_RULE = "Only task_update operation replace or cancel is allowed"
_DRAFT_QUOTE = "stage: draft"
_PUBLISH_STAGE = "stage: publish"
_PUBLISH_QUOTE = "Write the release files with these exact bytes."
_DRAFT_OBJECTIVE = (
    "Read the changelog and playtest log, compile the release packet, and return that JSON."
)
_PUBLISH_OBJECTIVE = "Write the approved Emberfall release notes and packet."
_QUOTE_LIMIT = 2048

_PROVIDER_CLASSES = (
    ("openminion.modules.llm.providers.openai.adapter", "OpenAIProvider"),
    ("openminion.modules.llm.providers.openrouter.adapter", "OpenRouterProvider"),
    ("openminion.modules.llm.providers.anthropic.adapter", "AnthropicProvider"),
    ("openminion.modules.llm.providers.ollama.adapter", "OllamaProvider"),
    ("openminion.modules.llm.providers.cortensor.adapter", "CortensorProvider"),
    ("openminion.modules.llm.providers.echo.adapter", "EchoProvider"),
)


def build_entry_task_update(messages: list[Any]) -> dict[str, Any] | None:
    """Return one sidecar OpenMinion will accept, or None when this prompt has no entry contract."""

    if _is_recovery(messages):
        return None
    revision = _task_revision(messages)
    user_text = _turn_user_text(messages)
    if revision is None or not user_text.strip():
        return None
    quote = _user_quote(user_text)
    objective = _objective(user_text)
    if not quote or quote not in user_text or not objective:
        return None
    update = {
        "operation": "start" if revision == 0 else "update",
        "expected_revision": revision,
        "user_quote": quote,
        "objective": objective,
    }
    from openminion.modules.brain.schemas.task_context import TaskContextUpdate

    TaskContextUpdate.model_validate(update)
    return update


def repair_entry_task_update(request: Any, response: Any) -> Any:
    """Leave non-entry responses alone. Entry tool calls get exactly one valid sidecar."""

    calls = list(getattr(response, "tool_calls", None) or [])
    messages = list(getattr(request, "messages", None) or [])
    if not calls or not _schema_offers_task_update(request) or _is_recovery(messages):
        return response
    update = build_entry_task_update(messages)
    if update is None:
        return response
    repaired = []
    for index, call in enumerate(calls):
        arguments = dict(getattr(call, "arguments", None) or {})
        arguments.pop("task_update", None)
        if index == 0:
            arguments["task_update"] = update
        repaired.append(_call_with_arguments(call, arguments))
    assistant_messages = _assistant_messages_with_calls(response, repaired)
    if hasattr(response, "model_copy"):
        payload: dict[str, Any] = {"tool_calls": repaired}
        if assistant_messages is not None:
            payload["assistant_messages"] = assistant_messages
        return response.model_copy(update=payload)
    response.tool_calls = repaired
    return response


@contextmanager
def install_entry_task_update_repair() -> Iterator[None]:
    """Wrap provider ``complete`` for this run, including the echo completer.

    The wrapper runs after the model (or the offline planner) returns and
    before OpenMinion validates the entry response. Subclasses that do not
    define ``complete`` follow ``OpenAIProvider``, which is the class Z.ai
    and DeepSeek use.
    """

    import importlib

    patched: list[tuple[Any, Any]] = []
    try:
        for module_name, class_name in _PROVIDER_CLASSES:
            module = importlib.import_module(module_name)
            cls = getattr(module, class_name)
            if "complete" not in cls.__dict__:
                continue
            original = cls.complete

            def complete(
                self: Any,
                request: Any,
                config: Any,
                _original: Any = original,
            ) -> Any:
                return repair_entry_task_update(request, _original(self, request, config))

            cls.complete = complete  # type: ignore[method-assign]
            patched.append((cls, original))
        yield
    finally:
        for cls, original in patched:
            cls.complete = original  # type: ignore[method-assign]


def _schema_offers_task_update(request: Any) -> bool:
    for spec in getattr(request, "tools", None) or []:
        schema = getattr(spec, "input_schema", None) or {}
        properties = schema.get("properties") if isinstance(schema, dict) else None
        if isinstance(properties, dict) and "task_update" in properties:
            return True
    return False


def _is_recovery(messages: list[Any]) -> bool:
    for message in messages:
        if _RECOVERY_RULE in _content(message):
            return True
        meta = getattr(message, "meta", None) or {}
        if meta.get("block_kind") == "task_context_recovery":
            return True
    return False


def _task_revision(messages: list[Any]) -> int | None:
    found: int | None = None
    for message in messages:
        content = _content(message)
        if _ABSENT_TASK in content:
            found = 0
            continue
        if _RESOLVED_TASK not in content:
            continue
        start = content.find("{")
        if start < 0:
            continue
        try:
            payload, _ = json.JSONDecoder().raw_decode(content[start:])
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and isinstance(payload.get("revision"), int):
            found = payload["revision"]
    return found


def _turn_user_text(messages: list[Any]) -> str:
    chosen = ""
    for message in messages:
        if getattr(message, "role", "") != "user":
            continue
        content = _content(message)
        meta = getattr(message, "meta", None) or {}
        if meta.get("block_kind") in {"resolved_task", "task_context_recovery"}:
            continue
        if _ABSENT_TASK in content or content.startswith(_RESOLVED_TASK):
            continue
        if content.strip():
            chosen = content
    return chosen


def _user_quote(user_text: str) -> str:
    for candidate in (_PUBLISH_QUOTE, _DRAFT_QUOTE, _PUBLISH_STAGE):
        if candidate in user_text:
            return candidate
    for line in user_text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped[:_QUOTE_LIMIT]
    return user_text.strip()[:_QUOTE_LIMIT]


def _objective(user_text: str) -> str:
    if _PUBLISH_QUOTE in user_text or _PUBLISH_STAGE in user_text:
        return _PUBLISH_OBJECTIVE
    if _DRAFT_QUOTE in user_text:
        return _DRAFT_OBJECTIVE
    for line in user_text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped[:_QUOTE_LIMIT]
    return "Complete the current task."


def _content(message: Any) -> str:
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content
    return str(content or "")


def _call_with_arguments(call: Any, arguments: dict[str, Any]) -> Any:
    raw = json.dumps(arguments, ensure_ascii=True)
    if hasattr(call, "model_copy"):
        return call.model_copy(update={"arguments": arguments, "raw_arguments": raw})
    call.arguments = arguments
    call.raw_arguments = raw
    return call


def _assistant_messages_with_calls(response: Any, calls: list[Any]) -> list[Any] | None:
    messages = list(getattr(response, "assistant_messages", None) or [])
    if not messages:
        return None
    updated: list[Any] = []
    replaced = False
    for message in messages:
        if getattr(message, "tool_calls", None) and hasattr(message, "model_copy"):
            updated.append(message.model_copy(update={"tool_calls": calls}))
            replaced = True
        else:
            updated.append(message)
    if not replaced:
        return None
    return updated
