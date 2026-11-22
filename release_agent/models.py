"""Emberfall release assistant models."""

from typing import Literal

from pydantic import BaseModel, Field

ShipRecommendation = Literal["hold", "ship-with-caveats", "ship"]


class BugReport(BaseModel):
    """One open playtest bug, already prioritized."""

    id: str
    title: str
    severity: str
    priority: int = Field(ge=0, le=3)
    area: str
    summary: str


class ReleasePacket(BaseModel):
    """Structured release summary the rest of the tool writes to disk."""

    game: str
    version: str
    codename: str
    released_on: str
    highlights: list[str]
    fixes: list[str]
    known_issues: list[str]
    bugs: list[BugReport]
    ship_recommendation: ShipRecommendation
    recommendation_reason: str


def packet_json(packet: ReleasePacket) -> str:
    """Canonical JSON written to release-packet.json and shown at approval time."""

    return packet.model_dump_json(indent=2) + "\n"
