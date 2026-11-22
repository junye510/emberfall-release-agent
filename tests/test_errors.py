"""ProviderError, TurnTimeoutError, and structured-output rejection."""

import json
from pathlib import Path

from openminion import Agent, AgentOutputValidationError, ProviderError, TurnTimeoutError

from release_agent.cli import main
from release_agent.models import ReleasePacket
from release_agent.runtime_config import (
    DEEPSEEK_ALT_MODEL,
    DEEPSEEK_API_KEY_ENV,
    DEEPSEEK_BASE_URL,
    DEEPSEEK_DEFAULT_MODEL,
    ZAI_API_KEY_ENV,
    ZAI_BASE_URL,
    ZAI_BASE_URL_ENV,
    ZAI_CHINA_BASE_URL,
    ZAI_DEFAULT_MODEL,
    ZAI_PAID_PRESET_MODEL,
    prepare_runtime_home,
    resolve_settings,
)

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


def test_zai_free_model_is_the_recommended_preset(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.delenv(ZAI_API_KEY_ENV, raising=False)
    monkeypatch.delenv("OPENMINION_BASE_URL", raising=False)
    monkeypatch.delenv(ZAI_BASE_URL_ENV, raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-should-not-be-used")
    home = tmp_path / "home"
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
            "zai",
            "--home",
            str(home),
            "--session-id",
            "zai-missing-key",
            "--timeout",
            "30",
            "--env-file",
            str(tmp_path / "missing.env"),
        ]
    )
    captured = capsys.readouterr()
    assert code == 3, captured.out + captured.err
    assert "provider failed: PROVIDER_ERROR:" in captured.err
    assert ZAI_API_KEY_ENV in captured.err
    assert not (tmp_path / "out" / "RELEASE_NOTES.md").exists()

    config = json.loads((home / ".openminion" / "agents.json").read_text(encoding="utf-8"))
    openai = config["providers"]["openai"]
    assert openai["model"] == ZAI_DEFAULT_MODEL
    assert openai["model"] != ZAI_PAID_PRESET_MODEL
    assert openai["base_url"] == ZAI_BASE_URL
    assert openai["api_key_env"] == ZAI_API_KEY_ENV
    assert not str(openai.get("api_key") or "").strip()
    assert config["agents"]["openminion"]["provider"] == "openai"
    connection = config["agents"]["openminion"]["model_connections"]["zai"]
    assert connection["provider"] == "openai"
    assert ZAI_DEFAULT_MODEL in connection["models"]
    assert ZAI_PAID_PRESET_MODEL not in connection["models"]
    assert "unset" not in json.dumps(config)

    monkeypatch.setenv(ZAI_BASE_URL_ENV, ZAI_CHINA_BASE_URL)
    china = resolve_settings(provider="zai", approval="yes")
    assert china.model == ZAI_DEFAULT_MODEL
    assert china.base_url == ZAI_CHINA_BASE_URL
    china_home = tmp_path / "home-china"
    prepare_runtime_home(china_home, china)
    china_config = json.loads(
        (china_home / ".openminion" / "agents.json").read_text(encoding="utf-8")
    )
    assert china_config["providers"]["openai"]["base_url"] == ZAI_CHINA_BASE_URL
    assert china_config["providers"]["openai"]["model"] == ZAI_DEFAULT_MODEL

    monkeypatch.setenv("OPENMINION_BASE_URL", ZAI_BASE_URL)
    overridden = resolve_settings(provider="zai", approval="yes")
    assert overridden.base_url == ZAI_BASE_URL


def test_deepseek_is_the_real_model_preset(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.delenv(DEEPSEEK_API_KEY_ENV, raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-should-not-be-used")
    home = tmp_path / "home"
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
            "deepseek",
            "--home",
            str(home),
            "--session-id",
            "deepseek-missing-key",
            "--timeout",
            "30",
            "--env-file",
            str(tmp_path / "missing.env"),
        ]
    )
    captured = capsys.readouterr()
    assert code == 3, captured.out + captured.err
    assert "provider failed: PROVIDER_ERROR:" in captured.err
    assert DEEPSEEK_API_KEY_ENV in captured.err
    assert not (tmp_path / "out" / "RELEASE_NOTES.md").exists()

    config = json.loads((home / ".openminion" / "agents.json").read_text(encoding="utf-8"))
    openai = config["providers"]["openai"]
    assert openai["model"] == DEEPSEEK_DEFAULT_MODEL
    assert openai["base_url"] == DEEPSEEK_BASE_URL
    assert openai["api_key_env"] == DEEPSEEK_API_KEY_ENV
    assert not str(openai.get("api_key") or "").strip()
    assert config["agents"]["openminion"]["provider"] == "openai"
    connection = config["agents"]["openminion"]["model_connections"]["deepseek"]
    assert connection["provider"] == "openai"
    assert DEEPSEEK_DEFAULT_MODEL in connection["models"]
    assert "unset" not in json.dumps(config)

    settings = resolve_settings(provider="deepseek", model=DEEPSEEK_ALT_MODEL, approval="yes")
    alt_home = tmp_path / "home-pro"
    prepare_runtime_home(alt_home, settings)
    alt = json.loads((alt_home / ".openminion" / "agents.json").read_text(encoding="utf-8"))
    assert alt["providers"]["openai"]["model"] == DEEPSEEK_ALT_MODEL
    assert alt["providers"]["openai"]["api_key_env"] == DEEPSEEK_API_KEY_ENV


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
