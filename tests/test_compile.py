"""Parser tests for the Emberfall sample packet."""

from pathlib import Path

from release_agent.compile import compile_release, priority_for_severity
from release_agent.models import packet_json
from release_agent.render import render_release_notes

ROOT = Path(__file__).resolve().parents[1]
CHANGELOG = (ROOT / "examples" / "emberfall" / "CHANGELOG.md").read_text(encoding="utf-8")
BUGS = (ROOT / "examples" / "emberfall" / "playtest-log.md").read_text(encoding="utf-8")


def test_sample_packet_prioritizes_the_crash_and_holds():
    packet = compile_release(CHANGELOG, BUGS)

    assert packet.game == "Emberfall"
    assert packet.version == "0.4.0"
    assert packet.codename == "Cinder Orchard"
    assert packet.released_on == "2026-10-18"
    assert len(packet.highlights) == 5
    assert any("Cinder Orchard biome" in item for item in packet.highlights)
    assert len(packet.fixes) == 3
    assert len(packet.known_issues) == 2
    assert [bug.id for bug in packet.bugs] == [
        "BUG-118",
        "BUG-121",
        "BUG-124",
        "BUG-126",
    ]
    assert [bug.priority for bug in packet.bugs] == [0, 1, 2, 3]
    assert packet.ship_recommendation == "hold"
    assert "BUG-118" in packet.recommendation_reason
    assert packet_json(packet).strip().startswith("{")


def test_priority_words():
    assert priority_for_severity("crash") == 0
    assert priority_for_severity("Major") == 1
    assert priority_for_severity("cosmetic") == 3
    assert priority_for_severity("mystery") == 2


def test_release_notes_include_the_hold_and_the_crash():
    notes = render_release_notes(compile_release(CHANGELOG, BUGS))
    assert "Ship recommendation: **hold**" in notes
    assert "BUG-118" in notes
    assert "Orchard Heart root slam hard-crashes" in notes
    assert notes.endswith("\n")
