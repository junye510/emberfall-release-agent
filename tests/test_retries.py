"""One-shot retries for rate limits and non-JSON final answers."""

import os
from pathlib import Path
import subprocess
import sys

import pytest
from openminion import AgentOutputValidationError, ProviderError

from release_agent.runtime_config import (
    ECHO_DEFAULT_TIMEOUT_SECONDS,
    HOSTED_DEFAULT_TIMEOUT_SECONDS,
    agents_config_path,
    resolve_settings,
)
from release_agent.workflow import (
    RateLimitedError,
    StructuredOutputError,
    run_turn_with_retry,
)


def test_hosted_timeout_defaults_to_300_seconds(monkeypatch):
    monkeypatch.delenv("OPENMINION_TIMEOUT_SECONDS", raising=False)
    echo = resolve_settings(provider="echo", approval="yes")
    hosted = resolve_settings(provider="zai", approval="yes")
    assert echo.timeout_seconds == ECHO_DEFAULT_TIMEOUT_SECONDS == 120
    assert hosted.timeout_seconds == HOSTED_DEFAULT_TIMEOUT_SECONDS == 300
    monkeypatch.setenv("OPENMINION_TIMEOUT_SECONDS", "45")
    assert resolve_settings(provider="zai", approval="yes").timeout_seconds == 45


def test_validation_error_retries_once_with_json_instruction():
    calls: list[str] = []

    def run(message: str) -> str:
        calls.append(message)
        if len(calls) == 1:
            raise AgentOutputValidationError(
                "not json",
                raw_text="openminion: Emberfall 0.4.0 looks ready to ship.",
            )
        return "ok"

    assert run_turn_with_retry(run, "stage: draft", json_retry=True) == "ok"
    assert len(calls) == 2
    assert "Reply only with JSON" in calls[1]
    assert "ship_recommendation" in calls[1]
    assert "recommendation_reason" in calls[1]
    assert calls[1].startswith("stage: draft")


def test_second_validation_failure_explains_the_prose_reply():
    def run(message: str) -> str:
        raise AgentOutputValidationError(
            "not json",
            raw_text="openminion: still prose",
        )

    with pytest.raises(StructuredOutputError) as raised:
        run_turn_with_retry(run, "stage: draft", json_retry=True)
    assert "openminion:" in str(raised.value)
    assert raised.value.raw_text == "openminion: still prose"


def test_rate_limit_retries_once_then_raises_a_clear_error():
    calls = {"n": 0}

    def run(message: str) -> str:
        calls["n"] += 1
        raise ProviderError("您的账户已达到速率限制", code="RATE_LIMITED")

    with pytest.raises(RateLimitedError) as raised:
        run_turn_with_retry(run, "stage: draft", json_retry=True)
    assert calls["n"] == 2
    assert raised.value.code == "RATE_LIMITED"
    assert "1302" in str(raised.value)
    assert "1305" in str(raised.value)


def test_other_provider_errors_are_not_retried():
    calls = {"n": 0}

    def run(message: str) -> str:
        calls["n"] += 1
        raise ProviderError("missing key", code="PROVIDER_ERROR")

    with pytest.raises(ProviderError) as raised:
        run_turn_with_retry(run, "stage: draft", json_retry=True)
    assert calls["n"] == 1
    assert raised.value.code == "PROVIDER_ERROR"


def test_setup_writes_agents_json_under_openminion_home(tmp_path: Path):
    home = tmp_path / "home"
    data_root = tmp_path / "other-data-root"
    env = os.environ.copy()
    env["OPENMINION_HOME"] = str(home)
    env["OPENMINION_DATA_ROOT"] = str(data_root)
    env["ZAI_API_KEY"] = "unset"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "openminion",
            "setup",
            "--provider",
            "zai",
            "--model",
            "glm-4.7-flash",
            "--no-chat",
        ],
        cwd=tmp_path,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    config = agents_config_path(home)
    assert config.is_file()
    assert not (data_root / "agents.json").exists()
