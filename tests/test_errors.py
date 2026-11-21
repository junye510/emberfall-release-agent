"""ProviderError, TurnTimeoutError, and structured-output rejection."""

from pathlib import Path

from openminion import Agent, AgentOutputValidationError, ProviderError, TurnTimeoutError

from release_agent.cli import main
from release_agent.models import ReleasePacket
from release_agent.runtime_config import prepare_runtime_home, resolve_settings

ROOT = Path(__file__).resolve().parents[1]
CHANGELOG = ROOT / "examples" / "emberfall" / "CHANGELOG.md"
BUGS = ROOT / "examples" / "emberfall" / "playtest-log.md"


def test_missing_openai_key_is_provider_error(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    code = main(
        [
            "--changelog",
            str(CHANGELOG),
            "--bugs",
            str(BUGS),
            "--out",
            str(tmp_path / "out"),
            "--approval",
            "yes",
            "--provider",
            "openai",
            "--home",
            str(tmp_path / "home"),
            "--session-id",
            "provider-error",
            "--timeout",
            "30",
            "--env-file",
            str(tmp_path / "missing.env"),
        ]
    )
    captured = capsys.readouterr()
    assert code == 3
    assert "provider failed: PROVIDER_ERROR:" in captured.err
    assert "OPENAI_API_KEY" in captured.err
    assert not (tmp_path / "out" / "RELEASE_NOTES.md").exists()


def test_turn_timeout_and_invalid_structured_output(tmp_path: Path):
    home = tmp_path / "home"
    config_path = prepare_runtime_home(home, resolve_settings(provider="echo", approval="yes"))
    with Agent(
        instructions="Return JSON matching the schema.",
        output_type=ReleasePacket,
        session_id="errors",
        config_path=str(config_path),
    ) as agent:
        try:
            agent.run("this is not json", timeout_seconds=30)
            raised = None
        except AgentOutputValidationError as exc:
            raised = exc
        assert raised is not None
        assert "not json" in raised.raw_text
        try:
            agent.run("hello", timeout_seconds=0.05, session_id="errors-timeout")
            timed_out = False
        except TurnTimeoutError as exc:
            timed_out = True
            assert "timed out" in str(exc).lower()
        assert timed_out


def test_cli_timeout_exit_code(tmp_path: Path, capsys):
    code = main(
        [
            "--changelog",
            str(CHANGELOG),
            "--bugs",
            str(BUGS),
            "--out",
            str(tmp_path / "out"),
            "--approval",
            "yes",
            "--provider",
            "echo",
            "--home",
            str(tmp_path / "home"),
            "--session-id",
            "cli-timeout",
            "--timeout",
            "0.05",
            "--env-file",
            str(tmp_path / "missing.env"),
        ]
    )
    captured = capsys.readouterr()
    assert code == 4, captured.out + captured.err
    assert "turn timed out:" in captured.err


def test_provider_error_type_is_the_public_class():
    assert issubclass(ProviderError, Exception)
    assert issubclass(TurnTimeoutError, Exception)
