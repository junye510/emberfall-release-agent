"""Build an isolated OpenMinion home for one release run."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import subprocess
import sys

SUPPORTED_PROVIDERS = (
    "echo",
    "zai",
    "deepseek",
    "openai",
    "anthropic",
    "claude",
    "openrouter",
    "cerebras",
    "groq",
    "ollama",
    "cortensor",
)

# OpenAI-compatible setup presets. `config init --provider` does not accept them.
# Z.ai's preset default model is the paid glm-5.2, so the free model is explicit.
ZAI_PROVIDER = "zai"
ZAI_DEFAULT_MODEL = "glm-4.7-flash"
ZAI_PAID_PRESET_MODEL = "glm-5.2"
ZAI_BASE_URL = "https://api.z.ai/api/paas/v4/"
ZAI_CHINA_BASE_URL = "https://open.bigmodel.cn/api/paas/v4/"
ZAI_API_KEY_ENV = "ZAI_API_KEY"
ZAI_BASE_URL_ENV = "ZAI_BASE_URL"

DEEPSEEK_PROVIDER = "deepseek"
DEEPSEEK_DEFAULT_MODEL = "deepseek-v4-flash"
DEEPSEEK_ALT_MODEL = "deepseek-v4-pro"
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_API_KEY_ENV = "DEEPSEEK_API_KEY"

# Echo turns are local. Hosted free tiers, especially glm-4.7-flash, can spend
# the first attempt inside provider retries and still need room for tools.
ECHO_DEFAULT_TIMEOUT_SECONDS = 120.0
HOSTED_DEFAULT_TIMEOUT_SECONDS = 300.0


@dataclass(frozen=True)
class _SetupPreset:
    provider: str
    default_model: str
    base_url: str
    api_key_env: str


_SETUP_PRESETS = {
    ZAI_PROVIDER: _SetupPreset(
        ZAI_PROVIDER, ZAI_DEFAULT_MODEL, ZAI_BASE_URL, ZAI_API_KEY_ENV
    ),
    DEEPSEEK_PROVIDER: _SetupPreset(
        DEEPSEEK_PROVIDER,
        DEEPSEEK_DEFAULT_MODEL,
        DEEPSEEK_BASE_URL,
        DEEPSEEK_API_KEY_ENV,
    ),
}

# config init stores Claude settings on the anthropic block.
_PROVIDER_BLOCK = {
    "claude": "anthropic",
    "anthropic": "anthropic",
    "openai": "openai",
    "openrouter": "openrouter",
    "cerebras": "cerebras",
    "groq": "groq",
    "ollama": "ollama",
    "cortensor": "cortensor",
}


@dataclass(frozen=True)
class RuntimeSettings:
    provider: str
    model: str
    base_url: str
    timeout_seconds: float
    approval: str
    warnings: tuple[str, ...]


def load_dotenv(path: Path) -> None:
    """Load KEY=VALUE lines. Existing environment variables win."""

    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def resolve_settings(
    *,
    provider: str | None = None,
    model: str | None = None,
    base_url: str | None = None,
    timeout_seconds: float | None = None,
    approval: str | None = None,
) -> RuntimeSettings:
    selected = (provider if provider is not None else os.environ.get("OPENMINION_PROVIDER", "echo"))
    selected = selected.strip().lower() or "echo"
    if selected not in SUPPORTED_PROVIDERS:
        supported = ", ".join(SUPPORTED_PROVIDERS)
        raise ValueError(
            f"Unknown OPENMINION_PROVIDER {selected!r}. Supported: {supported}."
        )
    selected_model = model if model is not None else os.environ.get("OPENMINION_MODEL", "")
    selected_model = selected_model.strip()
    selected_base = _resolve_base_url(selected, base_url)
    if timeout_seconds is None:
        raw_timeout = os.environ.get("OPENMINION_TIMEOUT_SECONDS", "").strip()
        if raw_timeout:
            try:
                timeout_seconds = float(raw_timeout)
            except ValueError as exc:
                raise ValueError(
                    f"OPENMINION_TIMEOUT_SECONDS must be a number, got {raw_timeout!r}."
                ) from exc
        elif selected == "echo":
            timeout_seconds = ECHO_DEFAULT_TIMEOUT_SECONDS
        else:
            timeout_seconds = HOSTED_DEFAULT_TIMEOUT_SECONDS
    if timeout_seconds <= 0:
        raise ValueError("Timeout must be greater than zero.")
    selected_approval = (
        approval
        if approval is not None
        else os.environ.get("RELEASE_AGENT_APPROVAL", "ask")
    )
    selected_approval = selected_approval.strip().lower() or "ask"
    if selected_approval not in {"ask", "yes", "no"}:
        raise ValueError("Approval must be ask, yes, or no.")
    warnings: list[str] = []
    preset = _SETUP_PRESETS.get(selected)
    if preset is not None and not selected_model:
        selected_model = preset.default_model
    if selected == "echo" and selected_model:
        warnings.append(
            "OPENMINION_MODEL is ignored for the echo provider. "
            "OpenMinion raises ConfigError: Runtime model overrides are not "
            "supported for the echo provider."
        )
        selected_model = ""
    return RuntimeSettings(
        provider=selected,
        model=selected_model,
        base_url=selected_base,
        timeout_seconds=timeout_seconds,
        approval=selected_approval,
        warnings=tuple(warnings),
    )


def agents_config_path(home: Path) -> Path:
    """Path ``openminion setup`` actually writes.

    Setup stores ``agents.json`` at ``$OPENMINION_HOME/.openminion/agents.json``.
    ``OPENMINION_DATA_ROOT`` does not choose that file. Keep the two variables
    pointed at the same home so doctor and the CLI read one config.
    """

    return home.expanduser().resolve() / ".openminion" / "agents.json"


def prepare_runtime_home(home: Path, settings: RuntimeSettings) -> Path:
    """Create agents.json for one isolated OpenMinion home."""

    home = home.expanduser().resolve()
    home.mkdir(parents=True, exist_ok=True)
    data_root = home / ".openminion"
    env = os.environ.copy()
    env["OPENMINION_HOME"] = str(home)
    # Align state with the directory setup uses for agents.json. A different
    # DATA_ROOT would not move the config file.
    env["OPENMINION_DATA_ROOT"] = str(data_root)
    if settings.provider in _SETUP_PRESETS:
        return _prepare_setup_preset(home, data_root, env, settings)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "openminion",
            "config",
            "init",
            "--provider",
            settings.provider,
            "--force",
        ],
        cwd=str(home),
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "config init failed").strip()
        raise RuntimeError(detail)
    config_path = data_root / "agents.json"
    _apply_provider_overrides(config_path, settings)
    return config_path


def _resolve_base_url(provider: str, explicit: str | None) -> str:
    """CLI flag, then OPENMINION_BASE_URL, then ZAI_BASE_URL for Z.ai."""

    if explicit is not None:
        return explicit.strip()
    general = os.environ.get("OPENMINION_BASE_URL", "").strip()
    if general:
        return general
    if provider == ZAI_PROVIDER:
        return os.environ.get(ZAI_BASE_URL_ENV, "").strip()
    return ""


def _prepare_setup_preset(
    home: Path,
    data_root: Path,
    env: dict[str, str],
    settings: RuntimeSettings,
) -> Path:
    """Write a setup-catalog preset such as Z.ai or DeepSeek.

    Non-interactive setup refuses to save the preset unless its API-key env var
    is present. When the parent process has no key, the child setup gets a
    placeholder so the profile can still be written. The placeholder is not
    stored: ``api_key`` stays empty and ``api_key_env`` stays the preset name.
    The following ``Agent.run`` then raises ``ProviderError`` from the real env.
    """

    preset = _SETUP_PRESETS[settings.provider]
    if not env.get(preset.api_key_env, "").strip():
        env[preset.api_key_env] = "unset"
    command = [
        sys.executable,
        "-m",
        "openminion",
        "setup",
        "--provider",
        preset.provider,
        "--model",
        settings.model or preset.default_model,
        "--no-chat",
    ]
    if settings.base_url:
        command.extend(["--base-url", settings.base_url])
    result = subprocess.run(
        command,
        cwd=str(home),
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or f"{preset.provider} setup failed").strip()
        raise RuntimeError(detail)
    config_path = data_root / "agents.json"
    _scrub_placeholder_key(config_path)
    return config_path


def _scrub_placeholder_key(config_path: Path) -> None:
    """Drop a setup placeholder if one was persisted. Keys stay in the environment."""

    payload = json.loads(config_path.read_text(encoding="utf-8"))
    changed = False
    providers = payload.get("providers")
    if isinstance(providers, dict):
        block = providers.get("openai")
        if isinstance(block, dict) and block.get("api_key") == "unset":
            block["api_key"] = ""
            changed = True
    if changed:
        config_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _apply_provider_overrides(config_path: Path, settings: RuntimeSettings) -> None:
    block_name = _PROVIDER_BLOCK.get(settings.provider)
    if block_name is None or (not settings.model and not settings.base_url):
        return
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    providers = payload.setdefault("providers", {})
    block = providers.setdefault(block_name, {})
    if settings.model:
        block["model"] = settings.model
    if settings.base_url:
        block["base_url"] = settings.base_url
    config_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
