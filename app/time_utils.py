"""Helpers for participant time-window preferences and display."""

from __future__ import annotations

from typing import Mapping


def normalize_time_text(raw_value: str | None) -> str | None:
    """Validate and normalize a HH:MM string, returning None for empty values."""
    cleaned = (raw_value or "").strip()
    if not cleaned:
        return None
    parts = cleaned.split(":")
    if len(parts) != 2 or not all(part.isdigit() for part in parts):
        raise ValueError("Use HH:MM format for time preferences.")
    hour = int(parts[0])
    minute = int(parts[1])
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        raise ValueError("Time preferences must be between 00:00 and 23:59.")
    return f"{hour:02d}:{minute:02d}"


def time_text_to_minutes(raw_value: str | None) -> int | None:
    """Convert a HH:MM string, optionally with a day offset suffix, into absolute minutes."""
    cleaned = (raw_value or "").strip()
    if not cleaned:
        return None
    day_offset = 0
    base_time = cleaned
    if " (" in cleaned and cleaned.endswith(")"):
        base_time, suffix = cleaned.split(" (", 1)
        suffix = suffix[:-1].strip()
        if suffix.endswith("d"):
            offset_text = suffix[:-1]
            if offset_text.startswith("+"):
                offset_text = offset_text[1:]
            if offset_text.lstrip("-").isdigit():
                day_offset = int(offset_text)
    normalized = normalize_time_text(base_time)
    if normalized is None:
        return None
    hour, minute = normalized.split(":")
    return (int(hour) * 60 + int(minute)) + (day_offset * 24 * 60)


def format_minutes_as_time(minutes: int | float | None) -> str | None:
    """Format a minute offset as HH:MM, preserving day overflow where needed."""
    if minutes is None:
        return None
    rounded = int(round(minutes))
    day_offset, minute_of_day = divmod(rounded, 24 * 60)
    hour, minute = divmod(minute_of_day, 60)
    suffix = ""
    if day_offset > 0:
        suffix = f" (+{day_offset}d)"
    elif day_offset < 0:
        suffix = f" ({day_offset}d)"
    return f"{hour:02d}:{minute:02d}{suffix}"


def describe_time_window(earliest: str | None, latest: str | None) -> str:
    """Describe one time window in short user-facing text."""
    if earliest and latest:
        return f"{earliest}-{latest}"
    if earliest:
        return f"after {earliest}"
    if latest:
        return f"by {latest}"
    return "flexible"


def summarize_participant_time_preferences(participant: Mapping[str, object]) -> str:
    """Return a compact summary of a participant's outbound and return preferences."""
    outbound = describe_time_window(
        participant.get("outbound_earliest_time"),
        participant.get("outbound_latest_time"),
    )
    inbound = describe_time_window(
        participant.get("return_earliest_time"),
        participant.get("return_latest_time"),
    )
    if outbound == "flexible" and inbound == "flexible":
        return "Fully flexible timing"
    return f"Outbound {outbound} | Return {inbound}"
