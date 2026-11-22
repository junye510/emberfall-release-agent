"""Pull a release packet out of a model reply without loosening the schema.

GLM, through the OpenMinion profile prefix, often returns a valid object
wrapped as ``openminion: ```json ... `````. The SDK's ``output_type`` check
rejects that whole string. This module strips that wrapping, then validates
the first balanced JSON object with ``ReleasePacket`` unchanged.
"""

from __future__ import annotations

import json
import re

from pydantic import ValidationError

from openminion import AgentOutputValidationError

from release_agent.models import ReleasePacket

_PREFIX_RE = re.compile(r"^\s*openminion:\s*", re.IGNORECASE)
_FENCE_RE = re.compile(r"```(?:json)?[^\n]*\n(.*?)```", re.IGNORECASE | re.DOTALL)


def extract_json_object(text: str) -> str | None:
    """Return the first balanced top-level JSON object in ``text``.

    Tolerates an optional leading ``openminion:`` prefix, ```json or bare
    ``` fences, and prose around the object. Braces inside JSON strings do
    not end the object. Returns None when no JSON object is present.
    """

    stripped = _PREFIX_RE.sub("", text, count=1)
    candidates = [stripped]
    candidates.extend(match.group(1) for match in _FENCE_RE.finditer(stripped))
    # A fence with no newline after the language tag still wraps the object.
    candidates.append(re.sub(r"```(?:json)?", "", stripped, flags=re.IGNORECASE))
    for candidate in candidates:
        found = _first_json_object(candidate)
        if found is not None:
            return found
    return None


def parse_release_packet(text: str) -> ReleasePacket:
    """Validate the first JSON object in ``text`` as a ``ReleasePacket``."""

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


def _first_json_object(text: str) -> str | None:
    decoder = json.JSONDecoder()
    for index, character in enumerate(text):
        if character != "{":
            continue
        try:
            value, end = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return text[index : index + end]
    return None
