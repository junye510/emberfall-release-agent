"""Offline turn planner and echo completer.

OpenMinion 0.1.22's echo provider repeats the last user text and never selects
a tool. The public config also rejects the in-package ``stub`` provider, which
does understand ``tool:<name> {json}`` directives. For a credential-free run
this module plans the next tool call and patches ``EchoProvider.complete``
only while a release turn is in flight. Internal SDK phases that do not offer
the release tools still hit the original echo adapter.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import json
import re
from typing import Any, Iterator
from uuid import uuid4

_WORKFLOW_TOOLS = {
    "read_release_file",
    "compile_release_packet",
    "write_release_files",
}
_STAGE_RE = re.compile(r"(?m)^stage:[ \t]*(draft|publish)\s*$")


@dataclass(frozen=True)
class CompletedTool:
    name: str
    arguments: dict[str, Any]
    output: str


@dataclass(frozen=True)
class ToolRequest:
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class TextReply:
    text: str


Plan = ToolRequest | TextReply


def message_field(text: str, name: str) -> str:
    match = re.search(rf"(?m)^{re.escape(name)}:[ \t]*(.*)$", text)
    if match is None:
        return ""
    return match.group(1).strip()


def extract_block(text: str, name: str) -> str:
    start = f"<<<{name}>>>\n"
    end = f"<<<END_{name}>>>"
    begin = text.find(start)
    if begin < 0:
        return ""
    finish = text.find(end, begin + len(start))
    if finish < 0:
        return ""
    return text[begin + len(start) : finish]


def unwrap_tool_output(content: str) -> str:
    """Pull the tool's return value out of the runtime's result envelope."""

    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        return content
    if isinstance(payload, dict):
        outputs = payload.get("outputs")
        if isinstance(outputs, dict) and "data" in outputs:
            data = outputs["data"]
            if isinstance(data, str):
                return data
            return json.dumps(data, ensure_ascii=True)
    return content


def plan_release_turn(
    *,
    offered_tools: set[str],
    user_text: str,
    completed: list[CompletedTool],
) -> Plan | None:
    """Choose the next release action, or None when this call is not ours."""

    if not offered_tools & _WORKFLOW_TOOLS:
        return None
    stage_match = _STAGE_RE.search(user_text)
    if stage_match is None:
        return None
    stage = stage_match.group(1)
    if stage == "publish":
        return _plan_publish(offered_tools, user_text, completed)
    return _plan_draft(offered_tools, user_text, completed)


def _plan_draft(
    offered_tools: set[str],
    user_text: str,
    completed: list[CompletedTool],
) -> Plan:
    compiled = [call for call in completed if call.name == "compile_release_packet"]
    if compiled and compiled[-1].output.strip():
        return TextReply(compiled[-1].output.strip())
    reads = {
        str(call.arguments.get("path", "")): call.output
        for call in completed
        if call.name == "read_release_file"
    }
    changelog_path = message_field(user_text, "changelog_path")
    bug_path = message_field(user_text, "bug_log_path")
    if (
        "read_release_file" in offered_tools
        and changelog_path
        and changelog_path not in reads
    ):
        return ToolRequest("read_release_file", {"path": changelog_path})
    if "read_release_file" in offered_tools and bug_path and bug_path not in reads:
        return ToolRequest("read_release_file", {"path": bug_path})
    if "compile_release_packet" in offered_tools and changelog_path in reads and bug_path in reads:
        return ToolRequest(
            "compile_release_packet",
            {
                "changelog_text": reads[changelog_path],
                "bug_log_text": reads[bug_path],
            },
        )
    return TextReply(
        "The draft turn could not read both files and compile the packet."
    )


def _plan_publish(
    offered_tools: set[str],
    user_text: str,
    completed: list[CompletedTool],
) -> Plan:
    writes = [call for call in completed if call.name == "write_release_files"]
    if writes:
        directory = str(writes[-1].arguments.get("directory", "")).strip()
        return TextReply(f"Wrote release files to {directory}.")
    if "write_release_files" not in offered_tools:
        return TextReply("The publish turn did not offer write_release_files.")
    directory = message_field(user_text, "output_dir")
    markdown = extract_block(user_text, "RELEASE_MARKDOWN")
    payload_json = extract_block(user_text, "RELEASE_JSON")
    if not directory or not markdown or not payload_json:
        return TextReply("The publish turn was missing the release file payload.")
    return ToolRequest(
        "write_release_files",
        {
            "directory": directory,
            "markdown": markdown,
            "payload_json": payload_json,
        },
    )


def _attach_thread_task_update(
    plan: ToolRequest,
    messages: list[Any],
    user_text: str,
) -> ToolRequest:
    """Continued threads reject an entry tool call that omits task_update.

    The runtime strips this sidecar before the tool handler runs. A brand-new
    thread has no task yet, and an ``update`` operation is invalid there, so
    this is only attached to the publish stage.
    """

    if message_field(user_text, "stage") != "publish":
        return plan
    system_text = "\n".join(
        str(getattr(message, "content", "") or "")
        for message in messages
        if getattr(message, "role", "") == "system"
    )
    if "include task_update" not in system_text:
        return plan
    quote = "Write the release files with these exact bytes."
    if quote not in user_text:
        return plan
    arguments = dict(plan.arguments)
    arguments["task_update"] = {
        "operation": "update",
        "expected_revision": 1,
        "user_quote": quote,
        "objective": "Write the approved Emberfall release notes and packet.",
    }
    return ToolRequest(plan.name, arguments)


def select_release_user_text(messages: list[Any]) -> str:
    candidates = [
        str(getattr(message, "content", "") or "")
        for message in messages
        if getattr(message, "role", "") == "user"
    ]
    # Later messages win. A publish turn keeps the draft message in history,
    # and the first match would plan another draft instead of the write.
    chosen = ""
    for text in candidates:
        if _STAGE_RE.search(text):
            chosen = text
    return chosen or "\n".join(candidates)


def completed_from_messages(messages: list[Any]) -> list[CompletedTool]:
    outputs: dict[str, str] = {}
    for message in messages:
        if getattr(message, "role", "") != "tool":
            continue
        call_id = str(getattr(message, "tool_call_id", "") or "")
        content = str(getattr(message, "content", "") or "")
        if call_id:
            outputs[call_id] = unwrap_tool_output(content)
    completed: list[CompletedTool] = []
    for message in messages:
        if getattr(message, "role", "") != "assistant":
            continue
        for call in getattr(message, "tool_calls", []) or []:
            call_id = str(getattr(call, "id", "") or "")
            arguments = getattr(call, "arguments", {}) or {}
            if not isinstance(arguments, dict):
                arguments = {}
            completed.append(
                CompletedTool(
                    name=str(getattr(call, "name", "") or ""),
                    arguments=dict(arguments),
                    output=outputs.get(call_id, ""),
                )
            )
    return completed


@contextmanager
def install_scripted_echo() -> Iterator[None]:
    """Route release turns through the planner. Restore echo afterwards."""

    from openminion.modules.llm.constants import LLM_TOOL_CALL_STATUS_PARSED
    from openminion.modules.llm.contracts.adapter import (
        ProviderAdapterResult,
        adapter_result_to_llm_response,
    )
    from openminion.modules.llm.providers.echo.adapter import EchoProvider
    from openminion.modules.llm.schemas import Message, ToolCall, UsageInfo

    original = EchoProvider.complete

    def complete(self: EchoProvider, request: Any, config: dict[str, Any]) -> Any:
        offered = {spec.name for spec in (request.tools or [])}
        user_text = select_release_user_text(request.messages)
        plan = plan_release_turn(
            offered_tools=offered,
            user_text=user_text,
            completed=completed_from_messages(request.messages),
        )
        if plan is None:
            return original(self, request, config)
        if isinstance(plan, ToolRequest):
            plan = _attach_thread_task_update(plan, request.messages, user_text)
        if isinstance(plan, TextReply):
            # Tool results above 1200 characters come back clipped. The compile
            # tool stashes the full packet before that clip, and the final
            # assistant text is not subject to the same limit.
            from release_agent.tools import last_compiled_packet_json

            compiled_seen = any(
                call.name == "compile_release_packet"
                for call in completed_from_messages(request.messages)
            )
            full_packet = last_compiled_packet_json().strip()
            if (
                full_packet
                and compiled_seen
                and message_field(user_text, "stage") == "draft"
            ):
                plan = TextReply(full_packet)
            return adapter_result_to_llm_response(
                ProviderAdapterResult(
                    provider="echo",
                    model=request.model or "echo",
                    output_text=plan.text,
                    assistant_messages=[Message(role="assistant", content=plan.text)],
                    tool_calls=[],
                    usage=UsageInfo(),
                    latency_ms=1,
                    finish_reason="stop",
                    provider_raw={"provider": "echo", "adapter": "scripted-release"},
                    normalization_meta={"adapter": "echo", "path": "scripted-release"},
                )
            )
        raw_arguments = json.dumps(plan.arguments, ensure_ascii=True)
        tool_call = ToolCall(
            id=f"call-{uuid4().hex}",
            name=plan.name,
            arguments=plan.arguments,
            raw_arguments=raw_arguments,
            status=LLM_TOOL_CALL_STATUS_PARSED,
        )
        return adapter_result_to_llm_response(
            ProviderAdapterResult(
                provider="echo",
                model=request.model or "echo",
                output_text="",
                assistant_messages=[],
                tool_calls=[tool_call],
                usage=UsageInfo(),
                latency_ms=1,
                finish_reason="tool_calls",
                provider_raw={"provider": "echo", "adapter": "scripted-release"},
                normalization_meta={"adapter": "echo", "path": "scripted-release"},
            )
        )

    EchoProvider.complete = complete  # type: ignore[method-assign]
    try:
        yield
    finally:
        EchoProvider.complete = original
