"""OpenMinion 0.1.24 rejects entry tool calls that mishandle task_update.

A fresh task may omit the sidecar. An active task may not, and a sidecar that
is duplicated or not a verbatim quote of the latest user message is rejected
before any tool runs. The turn text is the sentence from the live glm-4.7-flash
runs. The repair installs one valid sidecar on real provider responses, and
OpenMinion strips it before the tool handler.
"""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from openminion.modules.brain.loop.context.task_context import (
    input_digest,
    task_context_message,
)
from openminion.modules.brain.loop.context.task_entry import apply_entry_task_context
from openminion.modules.brain.schemas.task_context import ResolvedTaskContext
from openminion.modules.llm.constants import LLM_TOOL_CALL_STATUS_PARSED
from openminion.modules.llm.contracts.adapter import (
    ProviderAdapterResult,
    adapter_result_to_llm_response,
)
from openminion.modules.llm.providers.openai.adapter import OpenAIProvider
from openminion.modules.llm.schemas import LLMRequest, LLMResponse, Message, ToolCall, ToolSpec, UsageInfo

from release_agent.cli import main
from release_agent.compile import compile_release
from release_agent.models import packet_json
from release_agent.render import render_release_notes
from release_agent.scripted import (
    TextReply,
    ToolRequest,
    completed_from_messages,
    message_field,
    plan_release_turn,
    select_release_user_text,
)
from release_agent.task_update import (
    build_entry_task_update,
    install_entry_task_update_repair,
    repair_entry_task_update,
)
from release_agent.tools import last_compiled_packet_json

ROOT = Path(__file__).resolve().parents[1]
CHANGELOG = ROOT / "examples" / "emberfall" / "CHANGELOG.md"
BUGS = ROOT / "examples" / "emberfall" / "playtest-log.md"
_REJECTION = (
    "I could not safely update the task conditions. "
    "Please restate the current task and its required conditions."
)
_DRAFT = (
    "stage: draft\n"
    "changelog_path: /game/CHANGELOG.md\n"
    "bug_log_path: /game/bugs.md\n"
    "\n"
    "Read both files, compile the release packet, and return that JSON.\n"
)
_PUBLISH = (
    "stage: publish\n"
    "output_dir: /out\n"
    "Write the release files with these exact bytes.\n"
)


class _Logger:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def emit(self, name: str, payload: dict | None = None, **kwargs: object) -> None:
        del kwargs
        self.events.append((name, dict(payload or {})))


class _State:
    def __init__(self, task: ResolvedTaskContext | None = None) -> None:
        self.resolved_task_context = task
        self.task_context_needs_update = False
        self.trace_id = "trace"
        self.goal = None


def _task(objective: str, *, revision: int = 1) -> ResolvedTaskContext:
    digest = input_digest(objective)
    return ResolvedTaskContext(
        revision=revision,
        objective=objective,
        objective_kind="verbatim",
        sources={"$objective": digest},
        consumed_input=digest,
    )


def _call(name: str, arguments: dict) -> ToolCall:
    return ToolCall(
        id=f"call-{uuid4().hex}",
        name=name,
        arguments=arguments,
        raw_arguments=json.dumps(arguments),
        status=LLM_TOOL_CALL_STATUS_PARSED,
    )


def _response(calls: list[ToolCall]) -> LLMResponse:
    return LLMResponse(
        ok=True,
        provider="openai",
        model="glm-4.7-flash",
        tool_calls=calls,
        finish_reason="tool_calls",
    )


def _entry_request(user_text: str, *extra: Message) -> LLMRequest:
    return LLMRequest(
        messages=[
            Message(role="system", content="Include exactly one task_update across the entry response."),
            *extra,
            Message(role="user", content=user_text),
        ],
        tools=[
            ToolSpec(
                name="read_release_file",
                input_schema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "task_update": {"type": "object"},
                    },
                    "required": ["path"],
                },
            )
        ],
        metadata={"purpose": "entry"},
    )


def _bad_update() -> dict:
    return {
        "operation": "start",
        "expected_revision": 1,
        "user_quote": "this quote is not in the user message",
        "objective": "Read the files.",
    }


def test_active_task_rejects_a_tool_call_that_omits_task_update():
    state = _State(_task(_DRAFT))
    logger = _Logger()
    response = _response([_call("read_release_file", {"path": "/game/CHANGELOG.md"})])

    decision = apply_entry_task_context(
        state,
        response,
        user_input=_PUBLISH,
        clarify=False,
        logger=logger,
    )

    assert decision is not None
    assert decision.question == _REJECTION
    assert decision.reason_code == "invalid_task_context_update"
    assert logger.events == [
        ("brain.task_context.rejected", {"reason": "invalid_or_missing_update"})
    ]
    assert response.tool_calls[0].arguments == {"path": "/game/CHANGELOG.md"}
    assert state.task_context_needs_update is True


def test_fresh_task_rejects_duplicate_and_malformed_task_updates_before_tools():
    state = _State()
    logger = _Logger()
    bad = _bad_update()
    response = _response(
        [
            _call("read_release_file", {"path": "/game/CHANGELOG.md", "task_update": bad}),
            _call("read_release_file", {"path": "/game/bugs.md", "task_update": dict(bad)}),
        ]
    )

    decision = apply_entry_task_context(
        state,
        response,
        user_input=_DRAFT,
        clarify=False,
        logger=logger,
    )

    assert decision is not None
    assert decision.question == _REJECTION
    assert logger.events[0] == (
        "brain.task_context.rejected",
        {"reason": "invalid_or_missing_update"},
    )
    assert state.resolved_task_context is None

    lone = _State()
    lone_logger = _Logger()
    malformed = _response(
        [_call("read_release_file", {"path": "/game/CHANGELOG.md", "task_update": bad})]
    )
    decision = apply_entry_task_context(
        lone,
        malformed,
        user_input=_DRAFT,
        clarify=False,
        logger=lone_logger,
    )
    assert decision is not None
    assert decision.question == _REJECTION
    assert malformed.tool_calls[0].arguments["path"] == "/game/CHANGELOG.md"


def test_repair_makes_omitted_and_malformed_entry_calls_executable():
    absent = Message(role="user", content="CURRENT TASK: absent (revision 0)")
    draft_request = _entry_request(_DRAFT, absent)
    bad = _bad_update()
    drafted = repair_entry_task_update(
        draft_request,
        _response(
            [
                _call(
                    "read_release_file",
                    {"path": "/game/CHANGELOG.md", "task_update": json.dumps(bad)},
                ),
                _call(
                    "read_release_file",
                    {"path": "/game/bugs.md", "task_update": bad},
                ),
            ]
        ),
    )
    assert [call.arguments.get("task_update") for call in drafted.tool_calls][1] is None
    sidecar = drafted.tool_calls[0].arguments["task_update"]
    assert sidecar["operation"] == "start"
    assert sidecar["expected_revision"] == 0
    assert sidecar["user_quote"] == "stage: draft"
    assert sidecar["user_quote"] in _DRAFT
    assert list(drafted.tool_calls[0].arguments) == ["path", "task_update"]
    assert "task_update" not in drafted.tool_calls[1].arguments

    state = _State()
    decision = apply_entry_task_context(
        state,
        drafted,
        user_input=_DRAFT,
        clarify=False,
        logger=_Logger(),
    )
    assert decision is None
    assert state.resolved_task_context is not None
    assert state.resolved_task_context.revision == 1
    assert drafted.tool_calls[0].arguments == {"path": "/game/CHANGELOG.md"}
    assert drafted.tool_calls[1].arguments == {"path": "/game/bugs.md"}

    published_request = _entry_request(_PUBLISH, task_context_message(state.resolved_task_context))
    published = repair_entry_task_update(
        published_request,
        _response([_call("write_release_files", {"directory": "/out", "markdown": "# n\n", "payload_json": "{}"})]),
    )
    update = build_entry_task_update(published_request.messages)
    assert update is not None
    assert update["operation"] == "update"
    assert update["expected_revision"] == 1
    assert update["user_quote"] == "Write the release files with these exact bytes."
    assert published.tool_calls[0].arguments["task_update"] == update

    publish_state = _State(state.resolved_task_context)
    decision = apply_entry_task_context(
        publish_state,
        published,
        user_input=_PUBLISH,
        clarify=False,
        logger=_Logger(),
    )
    assert decision is None
    assert publish_state.resolved_task_context is not None
    assert publish_state.resolved_task_context.revision == 2
    assert published.tool_calls[0].arguments == {
        "directory": "/out",
        "markdown": "# n\n",
        "payload_json": "{}",
    }


def test_repair_leaves_act_phase_and_recovery_calls_unchanged():
    call = _call("read_release_file", {"path": "/game/CHANGELOG.md"})
    response = _response([call])
    act = LLMRequest(
        messages=[Message(role="user", content=_DRAFT)],
        tools=[
            ToolSpec(
                name="read_release_file",
                input_schema={"type": "object", "properties": {"path": {"type": "string"}}},
            )
        ],
        metadata={"purpose": "act"},
    )
    assert repair_entry_task_update(act, response) is response

    recovery = LLMRequest(
        messages=[
            Message(
                role="system",
                content=(
                    "Only task_update operation replace or cancel is allowed. "
                    "Do not continue the omitted task."
                ),
            ),
            Message(role="user", content=_DRAFT),
        ],
        tools=_entry_request(_DRAFT).tools,
        metadata={"purpose": "entry"},
    )
    untouched = _response([_call("read_release_file", {"path": "/x"})])
    assert repair_entry_task_update(recovery, untouched) is untouched


def test_installer_restores_provider_complete_methods():
    original = OpenAIProvider.complete
    with install_entry_task_update_repair():
        assert OpenAIProvider.complete is not original
    assert OpenAIProvider.complete is original


def test_simulated_glm_entry_omits_or_misformats_task_update(tmp_path: Path, monkeypatch, capsys):
    """A live-shaped provider response never reaches OpenMinion's rejection.

    The draft entry emits two read_release_file calls and puts a bad
    task_update on both, which is what glm-4.7-flash did when the schema
    offered the field. The publish entry omits task_update on an active
    task. Both are rejected before tools run unless the completer repair
    installs one valid sidecar.
    """

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-a-real-key")
    monkeypatch.delenv("OPENMINION_BASE_URL", raising=False)
    emitted: list[dict] = []
    original = OpenAIProvider.complete

    def complete(self, request, config):
        del self, config
        offered = {spec.name for spec in (request.tools or [])}
        user_text = select_release_user_text(request.messages)
        purpose = str((request.metadata or {}).get("purpose") or "")
        plan = plan_release_turn(
            offered_tools=offered,
            user_text=user_text,
            completed=completed_from_messages(request.messages),
        )
        if (
            purpose == "entry"
            and message_field(user_text, "stage") == "draft"
            and not any(call.name == "read_release_file" for call in completed_from_messages(request.messages))
        ):
            bad = _bad_update()
            calls = [
                _call("read_release_file", {"path": message_field(user_text, "changelog_path"), "task_update": bad}),
                _call("read_release_file", {"path": message_field(user_text, "bug_log_path"), "task_update": dict(bad)}),
            ]
            emitted.append({"purpose": purpose, "stage": "draft", "calls": calls})
            return _tool_response(request, calls)
        if (
            purpose == "entry"
            and isinstance(plan, ToolRequest)
            and plan.name == "write_release_files"
        ):
            call = _call(plan.name, dict(plan.arguments))
            emitted.append({"purpose": purpose, "stage": "publish", "calls": [call]})
            return _tool_response(request, [call])
        if isinstance(plan, ToolRequest):
            call = _call(plan.name, dict(plan.arguments))
            return _tool_response(request, [call])
        if isinstance(plan, TextReply):
            text = plan.text
            compiled_seen = any(
                call.name == "compile_release_packet"
                for call in completed_from_messages(request.messages)
            )
            full_packet = last_compiled_packet_json().strip()
            if full_packet and compiled_seen and message_field(user_text, "stage") == "draft":
                text = full_packet
            return _text_response(request, text)
        return _text_response(request, "ok")

    OpenAIProvider.complete = complete  # type: ignore[method-assign]
    try:
        code = main(
            [
                "--changelog",
                str(CHANGELOG),
                "--bugs",
                str(BUGS),
                "--out",
                str(tmp_path / "out"),
                "--approval",
                "yes",
                "--provider",
                "openai",
                "--model",
                "glm-4.7-flash",
                "--base-url",
                "https://open.bigmodel.cn/api/paas/v4/",
                "--home",
                str(tmp_path / "home"),
                "--session-id",
                "glm-task-update",
                "--timeout",
                "90",
                "--env-file",
                str(tmp_path / "missing.env"),
            ]
        )
    finally:
        OpenAIProvider.complete = original  # type: ignore[method-assign]

    captured = capsys.readouterr()
    assert code == 0, captured.err
    assert _REJECTION not in captured.out
    assert _REJECTION not in captured.err
    assert "Reply only with JSON" not in captured.err
    assert "compiled from the changelog and playtest log" not in captured.err

    draft = next(item for item in emitted if item["stage"] == "draft")
    assert [call.name for call in draft["calls"]] == ["read_release_file", "read_release_file"]
    assert all("task_update" in call.arguments for call in draft["calls"])
    assert all(call.arguments["task_update"]["user_quote"] not in _DRAFT for call in draft["calls"])

    publish = next(item for item in emitted if item["stage"] == "publish")
    assert publish["calls"][0].name == "write_release_files"
    assert "task_update" not in publish["calls"][0].arguments

    expected = compile_release(
        CHANGELOG.read_text(encoding="utf-8"),
        BUGS.read_text(encoding="utf-8"),
    )
    notes_path = tmp_path / "out" / "RELEASE_NOTES.md"
    packet_path = tmp_path / "out" / "release-packet.json"
    assert notes_path.read_text(encoding="utf-8") == render_release_notes(expected)
    assert packet_path.read_text(encoding="utf-8") == packet_json(expected)
    assert "Decision: allow" in captured.out
    assert "tool: write_release_files" in captured.out
    assert "approved=True" in captured.out


def _tool_response(request, calls: list[ToolCall]):
    return adapter_result_to_llm_response(
        ProviderAdapterResult(
            provider="openai",
            model=request.model or "glm-4.7-flash",
            output_text="",
            assistant_messages=[],
            tool_calls=calls,
            usage=UsageInfo(),
            latency_ms=1,
            finish_reason="tool_calls",
        )
    )


def _text_response(request, text: str):
    return adapter_result_to_llm_response(
        ProviderAdapterResult(
            provider="openai",
            model=request.model or "glm-4.7-flash",
            output_text=text,
            assistant_messages=[Message(role="assistant", content=text)],
            tool_calls=[],
            usage=UsageInfo(),
            latency_ms=1,
            finish_reason="stop",
        )
    )
