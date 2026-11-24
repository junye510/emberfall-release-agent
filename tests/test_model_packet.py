"""Model replies that drift from ReleasePacket still review the compiled packet.

The fixture is the stderr from a live glm-4.7-flash turn. Extraction succeeds.
Strict validation fails because ship_recommendation and recommendation_reason
are absent. The workflow must not store that object: it drops the changelog
and would be a ship decision with no recorded reason. The packet shown for
approval is the one compiled from the changelog and playtest log.
"""

import json
from pathlib import Path

import pytest
from openminion import AgentOutputValidationError

from release_agent.compile import compile_release
from release_agent.json_extract import extract_json_object, parse_release_packet
from release_agent.models import packet_json
from release_agent.workflow import packet_from_model_reply

ROOT = Path(__file__).resolve().parents[1]
CHANGELOG = (ROOT / "examples" / "emberfall" / "CHANGELOG.md").read_text(encoding="utf-8")
BUGS = (ROOT / "examples" / "emberfall" / "playtest-log.md").read_text(encoding="utf-8")
GLM_STDERR = (ROOT / "tests" / "fixtures" / "glm-4.7-flash-stderr.txt").read_text(encoding="utf-8")
CANON = compile_release(CHANGELOG, BUGS)


def _adopt(text: str):
    return packet_from_model_reply(text, changelog_text=CHANGELOG, bug_log_text=BUGS)


def _fenced(body: str) -> str:
    return "openminion: ```json\n" + body + "\n```"


def test_glm_stderr_extracts_and_still_fails_the_strict_schema():
    extracted = json.loads(extract_json_object(GLM_STDERR) or "")
    assert extracted["game"] == "Emberfall 0.4.0-rc.3 Changelog"
    assert extracted["version"] == "unversioned"
    assert extracted["highlights"] == []
    assert extracted["fixes"] == []
    assert extracted["known_issues"] == []
    assert [bug["id"] for bug in extracted["bugs"]] == [
        "BUG-118",
        "BUG-121",
        "BUG-124",
        "BUG-126",
    ]
    assert "ship_recommendation" not in extracted
    assert "recommendation_reason" not in extracted
    with pytest.raises(AgentOutputValidationError) as raised:
        parse_release_packet(GLM_STDERR)
    message = str(raised.value)
    assert "ship_recommendation" in message
    assert "recommendation_reason" in message
    assert raised.value.raw_text == GLM_STDERR


def test_glm_stderr_log_keeps_changelog_facts_and_the_blocker(capsys):
    packet = _adopt(GLM_STDERR)
    captured = capsys.readouterr()

    assert packet == CANON
    assert packet.game == "Emberfall"
    assert packet.version == "0.4.0"
    assert packet.codename == "Cinder Orchard"
    assert packet.released_on == "2026-10-18"
    assert any("Cinder Orchard biome" in item for item in packet.highlights)
    assert packet.fixes
    assert packet.known_issues
    assert [bug.id for bug in packet.bugs] == ["BUG-118", "BUG-121", "BUG-124", "BUG-126"]
    assert packet.bugs[0].priority == 0
    assert packet.bugs[0].summary.startswith("Empty ember gauge plus the root slam")
    assert packet.ship_recommendation == "hold"
    assert "BUG-118" in packet.recommendation_reason
    assert "unversioned" not in packet.model_dump_json()
    assert "0.4.0-rc.3 Changelog" not in packet.model_dump_json()
    assert "compiled from the changelog and playtest log" in captured.err
    assert "ship_recommendation" in captured.err
    assert "recommendation_reason" in captured.err


def test_exact_fenced_packet_matches_without_a_warning(capsys):
    packet = _adopt("openminion: ```json\n" + packet_json(CANON) + "```")
    assert packet == CANON
    assert capsys.readouterr().err == ""


def test_reordered_bugs_still_match(capsys):
    payload = CANON.model_dump()
    payload["bugs"] = list(reversed(payload["bugs"]))
    packet = _adopt(json.dumps(payload))
    assert packet == CANON
    assert [bug.id for bug in packet.bugs] == ["BUG-118", "BUG-121", "BUG-124", "BUG-126"]
    assert capsys.readouterr().err == ""


def test_trailing_comma_and_tilde_fence_match_the_compiled_packet(capsys):
    broken = packet_json(CANON).rstrip()[:-1] + ",\n}"
    packet = _adopt("~~~\n" + broken + "\n~~~")
    assert packet == CANON
    assert capsys.readouterr().err == ""


def test_dropped_blocker_is_restored_from_the_playtest_log(capsys):
    payload = CANON.model_dump()
    payload["bugs"] = [bug for bug in payload["bugs"] if bug["id"] != "BUG-118"]
    payload["ship_recommendation"] = "ship-with-caveats"
    payload["recommendation_reason"] = "No crash bugs."
    packet = _adopt(_fenced(json.dumps(payload)))
    assert packet == CANON
    assert packet.ship_recommendation == "hold"
    assert packet.bugs[0].id == "BUG-118"
    assert "compiled from the changelog and playtest log" in capsys.readouterr().err


def test_invented_highlight_and_bug_are_not_kept(capsys):
    payload = CANON.model_dump()
    payload["highlights"] = ["Invented bonus biome", *payload["highlights"]]
    payload["bugs"].append(
        {
            "id": "BUG-999",
            "title": "Invented extractor crash",
            "severity": "crash",
            "priority": 0,
            "area": "boss",
            "summary": "This bug is not in the playtest log.",
        }
    )
    packet = _adopt(_fenced(json.dumps(payload)))
    rendered = packet.model_dump_json()
    assert packet == CANON
    assert "Invented bonus biome" not in rendered
    assert "BUG-999" not in rendered
    assert "compiled from the changelog and playtest log" in capsys.readouterr().err


@pytest.mark.parametrize(
    "body",
    [
        {
            "game": "Emberfall 0.4.0-rc.3 Changelog",
            "version": "unversioned",
            "codename": "",
            "released_on": "",
            "highlights": [],
            "fixes": [],
            "known_issues": [],
            "bugs": [
                {
                    "bug_id": "BUG-118",
                    "name": "Orchard Heart root slam hard-crashes at empty ember",
                    "sev": "crash",
                    "priority": "P0",
                    "component": "boss",
                    "details": "Empty ember gauge plus the root slam aborts to desktop.",
                }
            ],
        },
        {
            "gameTitle": "Emberfall",
            "releaseVersion": "0.4.0",
            "codeName": "Cinder Orchard",
            "releasedOn": "2026-10-18",
            "added": ["Cinder Orchard biome"],
            "bugFixes": [],
            "knownIssues": [],
            "openBugs": [
                {
                    "bugId": "BUG-121",
                    "title": "Ember gust",
                    "severity": "major",
                    "rank": "P1",
                    "area": "movement",
                    "description": "A gust shoves the player.",
                }
            ],
            "shipRecommendation": "ship",
            "reason": "Looks fine.",
        },
        {
            "game": "Emberfall",
            "version": "0.4.0",
            "bugs": {
                "BUG-118": {
                    "title": "Orchard Heart root slam hard-crashes at empty ember",
                    "severity": "crash",
                    "priority": 0,
                    "area": "boss",
                    "summary": "Different structure than a bug list.",
                }
            },
        },
        {
            "game": "Emberfall",
            "version": "unversioned",
            "highlights": [],
            "bugs": [
                "BUG-118 (crash): Orchard Heart root slam",
                "BUG-126: Pause menu quit row says Surival",
            ],
        },
    ],
)
def test_schema_drift_keeps_the_compiled_facts(body, capsys):
    packet = _adopt(_fenced(json.dumps(body)))
    assert packet == CANON
    assert packet.ship_recommendation == "hold"
    assert [bug.id for bug in packet.bugs] == ["BUG-118", "BUG-121", "BUG-124", "BUG-126"]
    assert "compiled from the changelog and playtest log" in capsys.readouterr().err


def test_partial_object_is_not_stored_as_the_packet(capsys):
    packet = _adopt('openminion: ```json\n{"game": "Emberfall"}\n```')
    assert packet == CANON
    assert packet.version == "0.4.0"
    assert len(packet.bugs) == 4
    assert "compiled from the changelog and playtest log" in capsys.readouterr().err


def test_a_later_exact_object_matches_without_a_warning(capsys):
    text = '{"game": "draft only"}\n' + packet_json(CANON)
    packet = _adopt(text)
    assert packet == CANON
    assert capsys.readouterr().err == ""


def test_prose_without_a_json_object_is_still_an_error(capsys):
    with pytest.raises(AgentOutputValidationError) as raised:
        _adopt("openminion: Emberfall 0.4.0 looks ready to ship.")
    assert "JSON object" in str(raised.value)
    assert "compiled from the changelog and playtest log" not in capsys.readouterr().err
