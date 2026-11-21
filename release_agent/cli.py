"""Command line for the Emberfall release assistant."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
from uuid import uuid4

from openminion import AgentOutputValidationError, ProviderError, TurnTimeoutError

from release_agent.runtime_config import load_dotenv, resolve_settings
from release_agent.workflow import run_release


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="release_agent",
        description=(
            "Read an Emberfall changelog and playtest log, summarize the release, "
            "and write the notes after a human approves."
        ),
    )
    parser.add_argument("--changelog", required=True, type=Path, help="Changelog markdown file")
    parser.add_argument("--bugs", required=True, type=Path, help="Playtest or bug-log markdown file")
    parser.add_argument("--out", required=True, type=Path, help="Directory for the release files")
    parser.add_argument(
        "--approval",
        choices=("ask", "yes", "no"),
        default=None,
        help="ask prompts before writing. yes and no are unattended. Defaults to RELEASE_AGENT_APPROVAL or ask.",
    )
    parser.add_argument(
        "--provider",
        default=None,
        help="OpenMinion provider. Defaults to OPENMINION_PROVIDER or echo.",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Model id for a real provider. Ignored for echo. Defaults to OPENMINION_MODEL.",
    )
    parser.add_argument(
        "--base-url",
        default=None,
        help="Provider base URL override. Defaults to OPENMINION_BASE_URL.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=None,
        help="Turn deadline in seconds. Defaults to OPENMINION_TIMEOUT_SECONDS or 120.",
    )
    parser.add_argument(
        "--session-id",
        default="",
        help="Stable OpenMinion session id. Defaults to a fresh id per invocation.",
    )
    parser.add_argument(
        "--home",
        type=Path,
        default=Path(".openminion-home"),
        help="Isolated OPENMINION_HOME for config and session storage.",
    )
    parser.add_argument(
        "--draft-only",
        action="store_true",
        help="Print the packet and skip the approval-gated write.",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=Path(".env"),
        help="Optional dotenv file. Existing environment variables win.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    load_dotenv(args.env_file)
    try:
        settings = resolve_settings(
            provider=args.provider,
            model=args.model,
            base_url=args.base_url,
            timeout_seconds=args.timeout,
            approval=args.approval,
        )
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 1
    for warning in settings.warnings:
        print(f"warning: {warning}", file=sys.stderr)
    session_id = args.session_id.strip() or f"emberfall-{uuid4().hex}"
    try:
        outcome = run_release(
            changelog=args.changelog,
            bugs=args.bugs,
            output_dir=args.out,
            home=args.home,
            session_id=session_id,
            settings=settings,
            draft_only=args.draft_only,
        )
    except FileNotFoundError as exc:
        print(exc, file=sys.stderr)
        return 1
    except ProviderError as exc:
        print(f"provider failed: {exc.code}: {exc.message}", file=sys.stderr)
        if exc.details:
            print(f"provider details: {exc.details}", file=sys.stderr)
        return 3
    except TurnTimeoutError as exc:
        print(f"turn timed out: {exc}", file=sys.stderr)
        return 4
    except AgentOutputValidationError as exc:
        print("structured output failed validation.", file=sys.stderr)
        print(exc.raw_text, file=sys.stderr)
        return 5
    except Exception as exc:  # noqa: BLE001
        print(f"release agent failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    _print_outcome(outcome, draft_only=args.draft_only)
    if args.draft_only:
        return 0
    if outcome.approved is False or not outcome.wrote_files:
        return 2
    return 0


def _print_outcome(outcome, *, draft_only: bool) -> None:
    print(outcome.notes_markdown)
    print(f"provider={outcome.provider}")
    if outcome.reported_provider or outcome.reported_model:
        print(
            "sdk_report="
            f"provider:{outcome.reported_provider or 'unknown'} "
            f"model:{outcome.reported_model or 'unknown'}"
        )
    print(f"session_id={outcome.session_id}")
    print(f"draft_thread_id={outcome.draft_thread_id or ''}")
    if not draft_only:
        print(f"publish_thread_id={outcome.publish_thread_id or ''}")
        print(f"approved={outcome.approved}")
        print(f"publish_text={outcome.publish_text}")
    if outcome.wrote_files:
        print(f"wrote={outcome.output_dir / 'RELEASE_NOTES.md'}")
        print(f"wrote={outcome.output_dir / 'release-packet.json'}")
    elif not draft_only:
        print("wrote=none")


if __name__ == "__main__":
    raise SystemExit(main())
