import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from icalendar import Calendar

sys.path.append(str(Path(__file__).resolve().parents[1]))

from src import notifier


class DummyResponse:
    def __init__(self, content: bytes):
        self.content = content

    def raise_for_status(self) -> None:  # pragma: no cover - placeholder for parity with requests.Response
        return None


def test_fetch_calendar_uses_binary(monkeypatch):
    calendar = Calendar()
    calendar.add("PRODID", "-//Example Corp//CalDAV Client//EN")
    calendar.add("VERSION", "2.0")

    event = notifier.Event()
    event.add("UID", "1234")
    event.add("SUMMARY", "Test event")
    event.add("DTSTART", datetime(2024, 1, 1, tzinfo=timezone.utc))
    calendar.add_component(event)

    ical_bytes = calendar.to_ical()

    def fake_get(url: str, timeout: int) -> DummyResponse:  # type: ignore[unused-arg]
        return DummyResponse(ical_bytes)

    monkeypatch.setattr(notifier.requests, "get", fake_get)

    parsed = notifier.fetch_calendar("http://example.com/calendar.ics")
    events = list(notifier.extract_events(parsed))

    assert len(events) == 1
    assert events[0]["summary"] == "Test event"


def test_detect_upcoming_events_filters_and_sorts():
    now = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)
    events = [
        {"uid": "1", "start": now + timedelta(minutes=10), "end": now + timedelta(minutes=20)},
        {"uid": "2", "start": now - timedelta(minutes=5), "end": now + timedelta(minutes=5)},
        {"uid": "3", "start": now + timedelta(minutes=30), "end": now + timedelta(minutes=40)},
        {"uid": "4", "start": now + timedelta(minutes=15), "end": now + timedelta(minutes=25)},
    ]

    upcoming = notifier.detect_upcoming_events(events, window_minutes=20, now=now, notified_ids={"1": ""})

    assert [event["uid"] for event in upcoming] == ["4"]


def test_build_message_formats_timezone():
    tz = notifier.ZoneInfo("UTC")
    event = {
        "summary": "Planning meeting",
        "start": datetime(2024, 1, 1, 9, 0, tzinfo=timezone.utc),
        "end": datetime(2024, 1, 1, 10, 0, tzinfo=timezone.utc),
        "location": "Online",
        "description": "Quarterly planning",
        "uid": "abc",
    }

    message = notifier.build_message([event], tz, window_minutes=60)

    assert "UTC" in message
    assert "Planning meeting" in message
