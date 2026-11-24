"""Tools the release agent can call.

Read and compile tools are read-only. The write tool is a local write with
unknown reversibility, so the default OpenMinion action policy asks for
approval before it runs.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path

from openminion import tool

from release_agent.compile import compile_release
from release_agent.models import ReleasePacket, packet_json

_NOTES_NAME = "RELEASE_NOTES.md"
_PACKET_NAME = "release-packet.json"
# The runtime clips tool-result strings at 1200 characters before the model
# sees them. The tool itself still computed the full packet; keep that here so
# the offline completer can return it as the assistant's final answer.
_compiled_packet_json: dict[str, str] = {"value": ""}


def reset_tool_memory() -> None:
    _compiled_packet_json["value"] = ""


def last_compiled_packet_json() -> str:
    return _compiled_packet_json["value"]


@dataclass(frozen=True)
class ReleaseBounds:
    """Directories the tools are allowed to touch for this run."""

    read_roots: tuple[Path, ...]
    output_dir: Path


_bounds: ContextVar[ReleaseBounds | None] = ContextVar("release_bounds", default=None)


@contextmanager
def use_bounds(bounds: ReleaseBounds):
    token = _bounds.set(bounds)
    try:
        yield
    finally:
        _bounds.reset(token)


def _require_bounds() -> ReleaseBounds:
    bounds = _bounds.get()
    if bounds is None:
        raise RuntimeError("Release tools were called without a workspace bound.")
    return bounds


def _resolve_inside(path: str, roots: tuple[Path, ...]) -> Path:
    resolved = Path(path).expanduser().resolve()
    for root in roots:
        if resolved == root or root in resolved.parents:
            return resolved
    raise PermissionError(f"Path is outside the release workspace: {path}")


@tool(min_scope="READ_ONLY", idempotent=True)
def read_release_file(path: str) -> str:
    """Read one changelog or playtest file inside the allowed release workspace."""

    bounds = _require_bounds()
    resolved = _resolve_inside(path, bounds.read_roots)
    if not resolved.is_file():
        raise FileNotFoundError(path)
    return resolved.read_text(encoding="utf-8")


@tool(min_scope="READ_ONLY", idempotent=True)
def compile_release_packet(changelog_text: str, bug_log_text: str) -> str:
    """Turn changelog text and a playtest log into the release packet JSON.

    Pass both file texts unchanged. The object includes game, version, codename,
    released_on, highlights, fixes, known_issues, bugs, ship_recommendation
    (hold, ship-with-caveats, or ship), and recommendation_reason. The runtime
    may clip this return value. The final answer still needs every key, every
    changelog bullet, and every playtest bug.
    """

    text = packet_json(compile_release(changelog_text, bug_log_text))
    _compiled_packet_json["value"] = text
    return text


@tool(idempotent=False)
def write_release_files(directory: str, markdown: str, payload_json: str) -> str:
    """Write RELEASE_NOTES.md and release-packet.json. This asks for approval."""

    bounds = _require_bounds()
    resolved = Path(directory).expanduser().resolve()
    if resolved != bounds.output_dir.resolve():
        raise PermissionError(
            f"Refusing to write outside the requested output directory: {directory}"
        )
    ReleasePacket.model_validate_json(payload_json)
    resolved.mkdir(parents=True, exist_ok=True)
    notes_path = resolved / _NOTES_NAME
    packet_path = resolved / _PACKET_NAME
    notes_path.write_text(markdown, encoding="utf-8")
    packet_path.write_text(payload_json, encoding="utf-8")
    return f"Wrote {notes_path} and {packet_path}"
