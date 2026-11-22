"""One release workflow: read, compile, approve, write."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import hashlib
from collections.abc import Callable

import sys

from openminion import APIRuntime, Agent, AgentOutputValidationError, ProviderError

from release_agent.json_extract import parse_release_packet
from release_agent.models import ReleasePacket, packet_json
from release_agent.render import render_release_notes
from release_agent.runtime_config import RuntimeSettings, prepare_runtime_home
from release_agent.scripted import install_scripted_echo
from release_agent.tools import (
    ReleaseBounds,
    compile_release_packet,
    read_release_file,
    reset_tool_memory,
    use_bounds,
    write_release_files,
)

_INSTRUCTIONS = """
You are the Emberfall release assistant. You prepare a ship decision from a
changelog and a playtest log, then write the notes only after a human approves
the exact file bytes.

When the user message says stage: draft:
1. Call read_release_file with changelog_path.
2. Call read_release_file with bug_log_path.
3. Call compile_release_packet with those exact file texts.
4. Your final answer is the JSON object from compile_release_packet and nothing else.

When the user message says stage: publish:
1. Call write_release_files exactly once.
2. Copy directory, markdown, and payload_json from the user message without edits.
3. If approval is denied, stop and do not call the tool again.
4. Your final answer is one sentence that says whether the files were written.
""".strip()

_YES = {"yes", "y", "proceed", "go", "confirm", "sure", "affirmative", "sounds good"}
_JSON_ONLY_INSTRUCTION = (
    "Reply only with JSON. Do not write prose. Do not prefix the answer with a name. "
    "The final answer must be one JSON object and nothing else."
)


class RateLimitedError(Exception):
    """A hosted turn stayed rate-limited after one retry."""

    def __init__(self, message: str, *, original: ProviderError) -> None:
        super().__init__(message)
        self.original = original
        self.code = original.code


class StructuredOutputError(Exception):
    """Structured output was still not JSON after one stricter retry."""

    def __init__(self, message: str, *, raw_text: str) -> None:
        super().__init__(message)
        self.raw_text = raw_text


def run_turn_with_retry(run: Callable[[str], object], message: str, *, json_retry: bool) -> object:
    """Run one turn, and retry once on a rate limit or a non-JSON final answer.

    ``run`` receives the user message. The JSON retry appends a stricter
    instruction. A second failure of the same kind becomes a clear error.
    """

    try:
        return run(message)
    except ProviderError as exc:
        if str(getattr(exc, "code", "")) != "RATE_LIMITED":
            raise
        print(
            "Provider rate limited (RATE_LIMITED). Retrying this turn once.",
            file=sys.stderr,
        )
        try:
            return run(message)
        except ProviderError as second:
            if str(getattr(second, "code", "")) != "RATE_LIMITED":
                raise
            raise RateLimitedError(
                "The provider stayed rate-limited after one retry. "
                "Z.ai's free tier allows about one request at a time "
                "(Zhipu 1302, HTTP 429) and glm-4.7-flash often returns 1305 "
                "when it is overloaded. OpenMinion already waited through its "
                "own retries before raising RATE_LIMITED. Wait, then run again.",
                original=second,
            ) from second
    except AgentOutputValidationError as exc:
        if not json_retry:
            raise
        print(
            "The model reply was not JSON. Retrying once with a reply-only-with-JSON instruction.",
            file=sys.stderr,
        )
        retry_message = f"{message.rstrip()}\n\n{_JSON_ONLY_INSTRUCTION}"
        try:
            return run(retry_message)
        except AgentOutputValidationError as second:
            raise StructuredOutputError(
                "The model replied with prose instead of the release JSON "
                "(the body is often prefixed with 'openminion: '). "
                "A second turn asked it to reply only with JSON, and that reply "
                "also failed validation.",
                raw_text=second.raw_text,
            ) from second


@dataclass(frozen=True)
class ReleaseOutcome:
    packet: ReleasePacket
    notes_markdown: str
    approved: bool | None
    wrote_files: bool
    session_id: str
    draft_thread_id: str | None
    publish_thread_id: str | None
    publish_text: str
    provider: str
    reported_provider: str
    reported_model: str
    output_dir: Path
    openminion_home: Path


def run_release(
    *,
    changelog: Path,
    bugs: Path,
    output_dir: Path,
    home: Path,
    session_id: str,
    settings: RuntimeSettings,
    draft_only: bool = False,
    approval_input: Callable[[str], str] | None = None,
) -> ReleaseOutcome:
    """Run the draft turn and, unless draft_only, the approval-gated write turn."""

    changelog = changelog.expanduser().resolve()
    bugs = bugs.expanduser().resolve()
    output_dir = output_dir.expanduser().resolve()
    if not changelog.is_file():
        raise FileNotFoundError(f"Changelog not found: {changelog}")
    if not bugs.is_file():
        raise FileNotFoundError(f"Bug log not found: {bugs}")

    reset_tool_memory()
    config_path = prepare_runtime_home(home, settings)
    bounds = ReleaseBounds(
        read_roots=tuple(dict.fromkeys((changelog.parent, bugs.parent))),
        output_dir=output_dir,
    )
    scripted = install_scripted_echo() if settings.provider == "echo" else _nullcontext()
    with scripted:
        runtime = APIRuntime.from_config_path(
            str(config_path),
            home_root=str(home),
            data_root=str(home / ".openminion"),
            logging_mode="interactive",
        )
        try:
            with use_bounds(bounds):
                return _run_with_runtime(
                    runtime=runtime,
                    changelog=changelog,
                    bugs=bugs,
                    output_dir=output_dir,
                    home=home.expanduser().resolve(),
                    session_id=session_id,
                    settings=settings,
                    draft_only=draft_only,
                    approval_input=approval_input,
                )
        finally:
            runtime.close()


def _run_with_runtime(
    *,
    runtime: APIRuntime,
    changelog: Path,
    bugs: Path,
    output_dir: Path,
    home: Path,
    session_id: str,
    settings: RuntimeSettings,
    draft_only: bool,
    approval_input: Callable[[str], str] | None,
) -> ReleaseOutcome:
    draft_message = "\n".join(
        [
            "stage: draft",
            f"changelog_path: {changelog}",
            f"bug_log_path: {bugs}",
            "",
            "Read both files, compile the release packet, and return that JSON.",
        ]
    )
    with Agent(
        name="emberfall-release",
        instructions=_INSTRUCTIONS,
        tools=[read_release_file, compile_release_packet],
        forced_tools=["read_release_file", "compile_release_packet"],
        session_id=session_id,
        runtime=runtime,
    ) as draft_agent:
        def _draft_turn(text: str):
            result = draft_agent.run(text, timeout_seconds=settings.timeout_seconds)
            # Validate here, not via Agent(output_type=...), so a fenced reply
            # still yields session_id and thread_id when the object is valid.
            return result, parse_release_packet(result.text)

        draft, packet = run_turn_with_retry(
            _draft_turn,
            draft_message,
            json_retry=True,
        )
    if not isinstance(packet, ReleasePacket):
        raise RuntimeError("Draft turn did not return a ReleasePacket.")
    notes = render_release_notes(packet)
    payload = packet_json(packet)
    reported_provider = str(draft.metadata.get("provider", "") or "")
    reported_model = str(draft.metadata.get("model", "") or "")

    if draft_only:
        return ReleaseOutcome(
            packet=packet,
            notes_markdown=notes,
            approved=None,
            wrote_files=False,
            session_id=draft.session_id or session_id,
            draft_thread_id=draft.thread_id,
            publish_thread_id=None,
            publish_text="",
            provider=settings.provider,
            reported_provider=reported_provider,
            reported_model=reported_model,
            output_dir=output_dir,
            openminion_home=home,
        )

    decision: dict[str, bool | None] = {"allow": None}

    def approval_callback(tool_name: str, args: dict[str, object], approval_id: str) -> bool:
        allowed = _decide_approval(
            tool_name,
            args,
            approval_id,
            mode=settings.approval,
            approval_input=approval_input,
        )
        decision["allow"] = allowed
        return allowed

    publish_message = _publish_message(output_dir, notes, payload)
    # forced_tools is intentionally omitted here. On a continued thread the
    # entry phase treats the only offered tool as the task-update carrier and
    # rejects the call unless arguments include task_update. Leaving the tool
    # available, without forcing it, lets entry answer in text and the act
    # phase perform the write.
    with Agent(
        name="emberfall-release",
        instructions=_INSTRUCTIONS,
        tools=[write_release_files],
        session_id=session_id,
        runtime=runtime,
    ) as publish_agent:
        published = run_turn_with_retry(
            lambda text: publish_agent.run(
                text,
                thread_id=draft.thread_id,
                timeout_seconds=settings.timeout_seconds,
                approval_callback=approval_callback,
            ),
            publish_message,
            json_retry=False,
        )
    notes_path = output_dir / "RELEASE_NOTES.md"
    packet_path = output_dir / "release-packet.json"
    wrote = notes_path.is_file() and packet_path.is_file()
    return ReleaseOutcome(
        packet=packet,
        notes_markdown=notes,
        approved=decision["allow"],
        wrote_files=wrote,
        session_id=published.session_id or draft.session_id or session_id,
        draft_thread_id=draft.thread_id,
        publish_thread_id=published.thread_id,
        publish_text=published.text,
        provider=settings.provider,
        reported_provider=str(published.metadata.get("provider", "") or reported_provider),
        reported_model=str(published.metadata.get("model", "") or reported_model),
        output_dir=output_dir,
        openminion_home=home,
    )


def _publish_message(output_dir: Path, notes: str, payload: str) -> str:
    return (
        "stage: publish\n"
        f"output_dir: {output_dir}\n"
        "Write the release files with these exact bytes.\n"
        "<<<RELEASE_MARKDOWN>>>\n"
        f"{notes}"
        "<<<END_RELEASE_MARKDOWN>>>\n"
        "<<<RELEASE_JSON>>>\n"
        f"{payload}"
        "<<<END_RELEASE_JSON>>>\n"
    )


def _decide_approval(
    tool_name: str,
    args: dict[str, object],
    approval_id: str,
    *,
    mode: str,
    approval_input: Callable[[str], str] | None,
) -> bool:
    markdown = str(args.get("markdown", ""))
    payload = str(args.get("payload_json", ""))
    directory = str(args.get("directory", ""))
    print("Approval required before writing files.")
    print(f"  tool: {tool_name}")
    print(f"  approval_id: {approval_id}")
    print(f"  directory: {directory}")
    print(f"  RELEASE_NOTES.md sha256: {_sha256(markdown)} ({len(markdown.encode())} bytes)")
    print(f"  release-packet.json sha256: {_sha256(payload)} ({len(payload.encode())} bytes)")
    preview = "\n".join(markdown.splitlines()[:8])
    if preview:
        print("  notes preview:")
        for line in preview.splitlines():
            print(f"    {line}")
    if mode == "yes":
        print("Decision: allow")
        return True
    if mode == "no":
        print("Decision: deny")
        return False
    prompt = "Allow this exact action? [yes/no] "
    reader = approval_input or input
    answer = reader(prompt).strip().lower()
    allowed = answer in _YES
    print("Decision: allow" if allowed else "Decision: deny")
    return allowed


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class _nullcontext:
    def __enter__(self) -> None:
        return None

    def __exit__(self, *exc: object) -> None:
        return None
