"""Deterministic changelog and playtest parsers.

The offline completer and a real model both call this through the
``compile_release_packet`` tool. Keeping the rules in code makes the sample
run repeatable when no hosted model is configured.
"""

from __future__ import annotations

import re

from release_agent.models import BugReport, ReleasePacket, ShipRecommendation

_VERSION_RE = re.compile(
    r"^##\s+\[(?P<version>[^\]]+)\]\s*-\s*(?P<date>\d{4}-\d{2}-\d{2})\s*[—–-]\s*(?P<codename>.+?)\s*$"
)
_BUG_HEADING_RE = re.compile(r"^##\s+(BUG-\d+)\s*$", re.IGNORECASE)
_FIELD_RE = re.compile(r"^[-*]\s+([A-Za-z][\w -]*):\s*(.*)$")
_BULLET_RE = re.compile(r"^[-*]\s+(.*\S)\s*$")

_PRIORITY = {
    "crash": 0,
    "blocker": 0,
    "critical": 0,
    "p0": 0,
    "major": 1,
    "high": 1,
    "p1": 1,
    "minor": 2,
    "medium": 2,
    "p2": 2,
    "trivial": 3,
    "cosmetic": 3,
    "low": 3,
    "p3": 3,
}
_SECTION_NAMES = {
    "added": "added",
    "changed": "changed",
    "fixed": "fixed",
    "known issues": "known",
    "known issue": "known",
}


def priority_for_severity(severity: str) -> int:
    """Map a playtest severity word onto P0–P3. Unknown severities land on P2."""

    return _PRIORITY.get(severity.strip().lower(), 2)


def compile_release(changelog_text: str, bug_log_text: str) -> ReleasePacket:
    """Build a release packet from the two source documents."""

    version = _parse_changelog(changelog_text)
    bugs = _parse_bugs(bug_log_text)
    recommendation, reason = _recommend(bugs)
    return ReleasePacket(
        game=version["game"],
        version=version["version"],
        codename=version["codename"],
        released_on=version["released_on"],
        highlights=[*version["added"], *version["changed"]],
        fixes=version["fixed"],
        known_issues=version["known"],
        bugs=bugs,
        ship_recommendation=recommendation,
        recommendation_reason=reason,
    )


def _parse_changelog(text: str) -> dict[str, object]:
    game = "Emberfall"
    version = "unversioned"
    released_on = ""
    codename = ""
    sections: dict[str, list[str]] = {
        "added": [],
        "changed": [],
        "fixed": [],
        "known": [],
    }
    current: str | None = None
    in_version = False
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith("# ") and not line.startswith("## "):
            game = line[2:].strip() or game
            continue
        version_match = _VERSION_RE.match(line)
        if version_match:
            if in_version:
                break
            in_version = True
            version = version_match.group("version").strip()
            released_on = version_match.group("date").strip()
            codename = version_match.group("codename").strip()
            current = None
            continue
        if not in_version:
            continue
        if line.startswith("### "):
            name = line[4:].strip().lower()
            current = _SECTION_NAMES.get(name)
            continue
        if current is None:
            continue
        bullet = _BULLET_RE.match(line)
        if bullet:
            sections[current].append(bullet.group(1).strip())
    return {
        "game": game,
        "version": version,
        "released_on": released_on,
        "codename": codename,
        "added": sections["added"],
        "changed": sections["changed"],
        "fixed": sections["fixed"],
        "known": sections["known"],
    }


def _parse_bugs(text: str) -> list[BugReport]:
    bugs: list[BugReport] = []
    current_id = ""
    fields: dict[str, str] = {}

    def flush() -> None:
        if not current_id:
            return
        title = fields.get("title", "").strip() or current_id
        severity = fields.get("severity", "").strip() or "unknown"
        area = fields.get("area", "").strip() or "general"
        summary = fields.get("summary", "").strip() or title
        bugs.append(
            BugReport(
                id=current_id.upper(),
                title=title,
                severity=severity,
                priority=priority_for_severity(severity),
                area=area,
                summary=summary,
            )
        )

    for raw_line in text.splitlines():
        line = raw_line.strip()
        heading = _BUG_HEADING_RE.match(line)
        if heading:
            flush()
            current_id = heading.group(1)
            fields = {}
            continue
        if not current_id:
            continue
        field = _FIELD_RE.match(line)
        if field:
            fields[field.group(1).strip().lower()] = field.group(2).strip()
    flush()
    bugs.sort(key=lambda bug: (bug.priority, bug.id))
    return bugs


def _recommend(bugs: list[BugReport]) -> tuple[ShipRecommendation, str]:
    by_priority = {0: [], 1: [], 2: [], 3: []}
    for bug in bugs:
        by_priority[bug.priority].append(bug.id)
    if by_priority[0]:
        listed = ", ".join(by_priority[0])
        return (
            "hold",
            f"Open P0 bugs block the release: {listed}.",
        )
    if by_priority[1]:
        listed = ", ".join(by_priority[1])
        return (
            "ship-with-caveats",
            f"No open P0 bugs. Ship only with the P1 bugs listed as known issues: {listed}.",
        )
    if bugs:
        return ("ship", "No open P0 or P1 bugs. Remaining notes are minor or cosmetic.")
    return ("ship", "The playtest log has no open bugs.")
