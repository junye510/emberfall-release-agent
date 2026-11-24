"""Pull JSON objects out of a model reply without loosening the schema.

GLM, through the OpenMinion profile prefix, often returns an object wrapped as
``openminion: ```json ... ``` ``. The SDK's ``output_type`` check rejects that
whole string. This module strips that wrapping. ``ReleasePacket`` validation
stays strict: a missing key is still a validation error. Deciding what to show
the human when the object is not the compiled packet lives in the workflow.
"""

from __future__ import annotations

import json
import re

from pydantic import ValidationError

from openminion import AgentOutputValidationError

from release_agent.models import ReleasePacket

_PREFIX_RE = re.compile(r"^\s*openminion:\s*", re.IGNORECASE)
_FENCE_RE = re.compile(
    r"(?:```|~~~)(?:json)?[^\n]*\n(.*?)(?:```|~~~)",
    re.IGNORECASE | re.DOTALL,
)
_FENCE_MARK_RE = re.compile(r"(?:```|~~~)(?:json)?", re.IGNORECASE)


def extract_json_object(text: str) -> str | None:
    """Return the first balanced top-level JSON object in ``text``.

    Tolerates an optional leading ``openminion:`` prefix, ```json or bare
    ``` fences, ``~~~`` fences, trailing commas, and prose around the object.
    Braces inside JSON strings do not end the object. Returns None when no
    JSON object is present.
    """

    objects = extract_json_objects(text)
    if not objects:
        return None
    return objects[0]


def extract_json_objects(text: str) -> list[str]:
    """Return every balanced top-level JSON object in ``text``, in order."""

    stripped = _PREFIX_RE.sub("", text, count=1)
    candidates = [stripped]
    candidates.extend(match.group(1) for match in _FENCE_RE.finditer(stripped))
    candidates.append(_FENCE_MARK_RE.sub("", stripped))
    found: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        for raw in _json_objects_in(candidate):
            try:
                key = json.dumps(json.loads(raw), sort_keys=True, ensure_ascii=True)
            except json.JSONDecodeError:
                continue
            if key in seen:
                continue
            seen.add(key)
            found.append(raw)
    return found


def parse_release_packet(text: str) -> ReleasePacket:
    """Validate the first JSON object in ``text`` as a ``ReleasePacket``.

    Schema rules are unchanged. A fenced object that omits
    ``ship_recommendation`` or renames bug fields still raises
    ``AgentOutputValidationError``.
    """

    extracted = extract_json_object(text)
    if extracted is None:
        raise AgentOutputValidationError(
            "Reply did not contain a JSON object.",
            raw_text=text,
        )
    try:
        return ReleasePacket.model_validate_json(extracted)
    except ValidationError as exc:
        raise AgentOutputValidationError(
            f"Reply did not validate against ReleasePacket: {exc}",
            raw_text=text,
            validation_error=exc,
        ) from exc


def _json_objects_in(text: str) -> list[str]:
    found: list[str] = []
    index = 0
    while index < len(text):
        start = text.find("{", index)
        if start < 0:
            break
        end = _object_end(text, start)
        if end is None:
            index = start + 1
            continue
        decoded = _decode_dict(text[start:end])
        if decoded is None:
            index = end
            continue
        found.append(decoded)
        index = end
    return found


def _decode_dict(segment: str) -> str | None:
    parsed = _raw_decode(segment)
    if parsed is not None:
        value, consumed = parsed
        if isinstance(value, dict):
            return segment[:consumed]
        return None
    relaxed = _strip_trailing_commas(segment)
    if relaxed == segment:
        return None
    parsed = _raw_decode(relaxed)
    if parsed is None:
        return None
    value, consumed = parsed
    if relaxed[consumed:].strip():
        return None
    if not isinstance(value, dict):
        return None
    return json.dumps(value)


def _raw_decode(segment: str) -> tuple[object, int] | None:
    try:
        value, consumed = json.JSONDecoder().raw_decode(segment)
    except json.JSONDecodeError:
        return None
    return value, consumed


def _object_end(text: str, start: int) -> int | None:
    """Index just after the object that begins at ``start``."""

    depth = 0
    in_string = False
    escape = False
    for index in range(start, len(text)):
        character = text[index]
        if in_string:
            if escape:
                escape = False
            elif character == "\\":
                escape = True
            elif character == '"':
                in_string = False
            continue
        if character == '"':
            in_string = True
            continue
        if character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                return index + 1
    return None


def _strip_trailing_commas(text: str) -> str:
    """Remove commas that sit immediately before a ``}`` or ``]`` outside strings."""

    pieces: list[str] = []
    in_string = False
    escape = False
    index = 0
    while index < len(text):
        character = text[index]
        if in_string:
            pieces.append(character)
            if escape:
                escape = False
            elif character == "\\":
                escape = True
            elif character == '"':
                in_string = False
            index += 1
            continue
        if character == '"':
            in_string = True
            pieces.append(character)
            index += 1
            continue
        if character == ",":
            look = index + 1
            while look < len(text) and text[look] in " \t\r\n":
                look += 1
            if look < len(text) and text[look] in "}]":
                index += 1
                continue
        pieces.append(character)
        index += 1
    return "".join(pieces)
