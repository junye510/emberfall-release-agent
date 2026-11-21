"""Tool sandbox tests. These call the decorated functions directly."""

from pathlib import Path

import pytest

from release_agent.tools import ReleaseBounds, read_release_file, use_bounds, write_release_files
from release_agent.compile import compile_release
from release_agent.models import packet_json
from release_agent.render import render_release_notes

ROOT = Path(__file__).resolve().parents[1]
CHANGELOG = ROOT / "examples" / "emberfall" / "CHANGELOG.md"
BUGS = ROOT / "examples" / "emberfall" / "playtest-log.md"


def test_read_allows_workspace_files_and_rejects_escape(tmp_path: Path):
    bounds = ReleaseBounds(read_roots=(CHANGELOG.parent,), output_dir=tmp_path)
    with use_bounds(bounds):
        text = read_release_file(str(CHANGELOG))
        assert "Cinder Orchard" in text
        with pytest.raises(PermissionError):
            read_release_file("/etc/passwd")
        with pytest.raises(PermissionError):
            read_release_file(str(CHANGELOG.parent.parent / "README.md"))


def test_write_is_limited_to_the_output_directory(tmp_path: Path):
    packet = compile_release(
        CHANGELOG.read_text(encoding="utf-8"),
        BUGS.read_text(encoding="utf-8"),
    )
    notes = render_release_notes(packet)
    payload = packet_json(packet)
    other = tmp_path / "elsewhere"
    other.mkdir()
    bounds = ReleaseBounds(read_roots=(CHANGELOG.parent,), output_dir=tmp_path / "out")
    with use_bounds(bounds):
        with pytest.raises(PermissionError):
            write_release_files(str(other), notes, payload)
        message = write_release_files(str(bounds.output_dir), notes, payload)
    assert "RELEASE_NOTES.md" in message
    assert (bounds.output_dir / "RELEASE_NOTES.md").read_text(encoding="utf-8") == notes
    assert (bounds.output_dir / "release-packet.json").read_text(encoding="utf-8") == payload


def test_write_rejects_invalid_packet_json(tmp_path: Path):
    bounds = ReleaseBounds(read_roots=(tmp_path,), output_dir=tmp_path)
    with use_bounds(bounds):
        with pytest.raises(Exception):
            write_release_files(str(tmp_path), "# notes\n", "not-json")
    assert not (tmp_path / "RELEASE_NOTES.md").exists()
