"""Render a release packet as the markdown players and the store page read."""

from release_agent.models import ReleasePacket


def render_release_notes(packet: ReleasePacket) -> str:
    """Return RELEASE_NOTES.md, including a trailing newline."""

    lines = [
        f"# {packet.game} {packet.version} — {packet.codename}",
        "",
        f"Date: {packet.released_on}" if packet.released_on else "Date: unscheduled",
        "",
        f"Ship recommendation: **{packet.ship_recommendation}**",
        "",
        packet.recommendation_reason,
        "",
        "## Highlights",
        "",
    ]
    lines.extend(_bullets(packet.highlights, "No highlights in this changelog."))
    lines.extend(["", "## Fixes", ""])
    lines.extend(_bullets(packet.fixes, "No fixes in this changelog."))
    lines.extend(["", "## Known issues", ""])
    lines.extend(_bullets(packet.known_issues, "No known issues listed in the changelog."))
    lines.extend(["", "## Prioritized bugs", ""])
    if not packet.bugs:
        lines.append("No open bugs in the playtest log.")
    else:
        lines.append("| Priority | ID | Severity | Area | Title |")
        lines.append("| --- | --- | --- | --- | --- |")
        for bug in packet.bugs:
            title = bug.title.replace("|", "\\|")
            lines.append(
                f"| P{bug.priority} | {bug.id} | {bug.severity} | {bug.area} | {title} |"
            )
        lines.append("")
        lines.append("## Bug details")
        for bug in packet.bugs:
            lines.extend(
                [
                    "",
                    f"### {bug.id} — {bug.title}",
                    "",
                    f"Priority P{bug.priority}. Severity {bug.severity}. Area: {bug.area}.",
                    "",
                    bug.summary,
                ]
            )
    lines.append("")
    return "\n".join(lines)


def _bullets(items: list[str], empty: str) -> list[str]:
    if not items:
        return [empty]
    return [f"- {item}" for item in items]
