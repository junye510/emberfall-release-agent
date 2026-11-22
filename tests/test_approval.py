"""End-to-end echo runs: structured output plus approve and deny."""

import json
from pathlib import Path

from release_agent.cli import main
from release_agent.compile import compile_release

ROOT = Path(__file__).resolve().parents[1]
CHANGELOG = ROOT / "examples" / "emberfall" / "CHANGELOG.md"
BUGS = ROOT / "examples" / "emberfall" / "playtest-log.md"


def _run(tmp_path: Path, approval: str, out_name: str) -> int:
    return main(
        [
            "--changelog",
            str(CHANGELOG),
            "--bugs",
            str(BUGS),
            "--out",
            str(tmp_path / out_name),
            "--approval",
            approval,
            "--provider",
            "echo",
            "--home",
            str(tmp_path / f"home-{approval}"),
            "--session-id",
            f"emberfall-{approval}",
            "--timeout",
            "90",
            "--env-file",
            str(tmp_path / "missing.env"),
        ]
    )


def test_approval_yes_writes_the_structured_packet(tmp_path: Path, capsys):
    code = _run(tmp_path, "yes", "out")
    captured = capsys.readouterr()
    assert code == 0, captured.err

    notes_path = tmp_path / "out" / "RELEASE_NOTES.md"
    packet_path = tmp_path / "out" / "release-packet.json"
    assert notes_path.is_file()
    assert packet_path.is_file()

    expected = compile_release(
        CHANGELOG.read_text(encoding="utf-8"),
        BUGS.read_text(encoding="utf-8"),
    )
    written = json.loads(packet_path.read_text(encoding="utf-8"))
    assert written == expected.model_dump()
    assert "Ship recommendation: **hold**" in notes_path.read_text(encoding="utf-8")
    assert "Decision: allow" in captured.out
    assert "tool: write_release_files" in captured.out
    assert "session_id=emberfall-yes" in captured.out
    assert "draft_thread_id=emberfall-yes:" in captured.out
    assert "publish_thread_id=" in captured.out
    assert "approved=True" in captured.out
    assert "Wrote release files" in captured.out


def test_approval_no_writes_nothing(tmp_path: Path, capsys):
    code = _run(tmp_path, "no", "out")
    captured = capsys.readouterr()
    assert code == 2, captured.err
    assert not (tmp_path / "out" / "RELEASE_NOTES.md").exists()
    assert not (tmp_path / "out" / "release-packet.json").exists()
    assert "Decision: deny" in captured.out
    assert "approved=False" in captured.out
    assert "Ship recommendation: **hold**" in captured.out
    assert "Confirmation denied. The action was not run." in captured.out
    assert "wrote=none" in captured.out
