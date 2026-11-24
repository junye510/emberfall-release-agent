"""JSON extraction around the shapes glm-4.7-flash actually returned."""

import json

import pytest
from openminion import AgentOutputValidationError

from release_agent.json_extract import extract_json_object, extract_json_objects, parse_release_packet
from release_agent.models import ReleasePacket

PACKET = {
    "game": "Emberfall",
    "version": "0.4.0",
    "codename": "Cinder Orchard",
    "released_on": "2026-10-18",
    "highlights": ["Cinder Orchard biome"],
    "fixes": ["Controller aim no longer snaps"],
    "known_issues": ["Root slam can wipe a run"],
    "bugs": [
        {
            "id": "BUG-118",
            "title": "Orchard Heart root slam hard-crashes at empty ember",
            "severity": "crash",
            "priority": 0,
            "area": "boss",
            "summary": "Empty ember gauge plus the root slam aborts to desktop.",
        }
    ],
    "ship_recommendation": "hold",
    "recommendation_reason": "Open P0 bugs block the release: BUG-118.",
}


def _body() -> str:
    return json.dumps(PACKET, indent=2)


def test_prefix_and_json_fence_matches_the_overnight_reply():
    text = "openminion: ```json\n" + _body() + "\n```"
    assert json.loads(extract_json_object(text) or "") == PACKET
    assert parse_release_packet(text) == ReleasePacket.model_validate(PACKET)


def test_bare_fence_without_a_language_tag():
    text = "```\n" + _body() + "\n```"
    assert parse_release_packet(text).ship_recommendation == "hold"
    assert parse_release_packet(text).bugs[0].id == "BUG-118"


def test_prose_before_and_after_the_object():
    text = (
        "openminion: Here is the release packet for Emberfall.\n"
        + _body()
        + "\nLet me know if you want the notes written."
    )
    parsed = parse_release_packet(text)
    assert parsed.game == "Emberfall"
    assert parsed.version == "0.4.0"
    assert [bug.priority for bug in parsed.bugs] == [0]


def test_schema_validation_stays_strict():
    text = "openminion: ```json\n" + json.dumps({"game": "Emberfall"}) + "\n```"
    with pytest.raises(AgentOutputValidationError) as raised:
        parse_release_packet(text)
    assert "ReleasePacket" in str(raised.value)
    assert raised.value.raw_text == text


def test_braces_inside_strings_do_not_end_the_object():
    packet = dict(PACKET)
    packet["highlights"] = ["Keep the {ember} lit"]
    text = "Note before.\n" + json.dumps(packet) + "\nNote after {not json}."
    assert parse_release_packet(text).highlights == ["Keep the {ember} lit"]


def test_trailing_comma_and_a_brace_inside_a_string():
    packet = dict(PACKET)
    packet["highlights"] = ["Keep the } ember, then rest"]
    broken = json.dumps(packet, indent=2)[:-1] + ",\n}"
    text = "openminion: ```json\n" + broken + "\n```"
    parsed = parse_release_packet(text)
    assert parsed.highlights == ["Keep the } ember, then rest"]
    assert parsed.ship_recommendation == "hold"
    assert parsed.bugs[0].id == "BUG-118"


def test_tilde_fence_without_a_language_tag():
    text = "~~~\n" + _body() + "\n~~~"
    assert parse_release_packet(text).version == "0.4.0"
    assert parse_release_packet(text).bugs[0].priority == 0


def test_every_top_level_object_is_returned_in_order():
    text = '{"game": "draft"}\n' + _body()
    objects = extract_json_objects(text)
    assert [json.loads(item)["game"] for item in objects] == ["draft", "Emberfall"]
