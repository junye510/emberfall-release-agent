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
    "openai",
    "anthropic",
    "claude",
    "openrouter",
    "cerebras",
    "groq",
    "ollama",
    "cortensor",
)

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
    selected_base = (
        base_url if base_url is not None else os.environ.get("OPENMINION_BASE_URL", "")
    )
    selected_base = selected_base.strip()
    if timeout_seconds is None:
        raw_timeout = os.environ.get("OPENMINION_TIMEOUT_SECONDS", "120").strip()
        try:
            timeout_seconds = float(raw_timeout)
        except ValueError as exc:
            raise ValueError(
                f"OPENMINION_TIMEOUT_SECONDS must be a number, got {raw_timeout!r}."
            ) from exc
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


def prepare_runtime_home(home: Path, settings: RuntimeSettings) -> Path:
    """Create agents.json with ``openminion config init`` and apply model overrides."""

    home.mkdir(parents=True, exist_ok=True)
    data_root = home / ".openminion"
    env = os.environ.copy()
    env["OPENMINION_HOME"] = str(home)
    env["OPENMINION_DATA_ROOT"] = str(data_root)
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
