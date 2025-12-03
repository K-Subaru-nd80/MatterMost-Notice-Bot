import json
import os
from datetime import datetime, timedelta, timezone
from typing import Dict, Iterable, List, Optional
from zoneinfo import ZoneInfo

import requests
from icalendar import Calendar, Event


class ConfigurationError(Exception):
    """Raised when required configuration is missing or invalid."""


def load_env_variable(name: str, required: bool = True) -> Optional[str]:
    value = os.getenv(name)
    if required and not value:
        raise ConfigurationError(f"Environment variable {name} is required")
    return value


def ensure_timezone(name: Optional[str]) -> ZoneInfo:
    if not name:
        return ZoneInfo("UTC")
    try:
        return ZoneInfo(name)
    except Exception as exc:  # ZoneInfo raises multiple exception types
        raise ConfigurationError(f"Invalid TIMEZONE value: {name}") from exc


def load_state(path: str) -> Dict[str, str]:
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError:
        return {}


def save_state(path: str, state: Dict[str, str]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def cleanup_old_state(state: Dict[str, str], threshold_days: int = 7) -> Dict[str, str]:
    """Remove entries for events that started more than threshold_days ago."""
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=threshold_days)
    cleaned = {}
    for uid, start_iso in state.items():
        try:
            start = datetime.fromisoformat(start_iso)
            # Ensure start is timezone-aware for comparison
            if start.tzinfo is None:
                start = start.replace(tzinfo=timezone.utc)
            if start >= cutoff:
                cleaned[uid] = start_iso
        except (ValueError, TypeError):
            # Keep entries with invalid timestamps for now
            cleaned[uid] = start_iso
    return cleaned


def normalize_datetime(value) -> datetime:
    """Convert iCal datetime/date to an aware UTC datetime."""
    if isinstance(value, datetime):
        dt = value
    else:
        dt = datetime.combine(value, datetime.min.time())
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def extract_events(calendar: Calendar) -> Iterable[Dict]:
    for component in calendar.walk("VEVENT"):
        if not isinstance(component, Event):
            continue
        uid = str(component.get("UID") or "")
        summary = str(component.get("SUMMARY") or "(無題)")
        dtstart = component.decoded("DTSTART")
        dtend = component.decoded("DTEND", None)
        duration = component.decoded("DURATION", None)

        start = normalize_datetime(dtstart)
        if dtend:
            end = normalize_datetime(dtend)
        elif duration:
            end = start + duration
        else:
            end = start

        description = str(component.get("DESCRIPTION") or "")
        location = str(component.get("LOCATION") or "")
        yield {
            "uid": uid,
            "summary": summary,
            "start": start,
            "end": end,
            "description": description,
            "location": location,
        }


def format_event(event: Dict, tz: ZoneInfo) -> str:
    start_local = event["start"].astimezone(tz)
    end_local = event["end"].astimezone(tz)
    start_utc = event["start"].astimezone(timezone.utc)
    end_utc = event["end"].astimezone(timezone.utc)

    details: List[str] = [f"**{event['summary']}**"]
    details.append(
        f"開始: {start_local:%Y-%m-%d %H:%M} ({tz.key}) / {start_utc:%Y-%m-%d %H:%M} (UTC)"
    )
    details.append(
        f"終了: {end_local:%Y-%m-%d %H:%M} ({tz.key}) / {end_utc:%Y-%m-%d %H:%M} (UTC)"
    )
    if event["location"]:
        details.append(f"場所: {event['location']}")
    if event["description"]:
        details.append(f"説明: {event['description']}")
    return "\n".join(f"- {line}" for line in details)


def build_message(events: List[Dict], tz: ZoneInfo, window_minutes: int) -> str:
    header = f"以下の予定が{window_minutes}分以内に開始します:\n"
    body = "\n\n".join(format_event(event, tz) for event in events)
    return header + body


def fetch_calendar(url: str) -> Calendar:
    response = requests.get(url, timeout=15)
    response.raise_for_status()
    return Calendar.from_ical(response.text)


def detect_upcoming_events(
    events: Iterable[Dict],
    window_minutes: int,
    now: datetime,
    notified_ids: Dict[str, str],
) -> List[Dict]:
    window_end = now + timedelta(minutes=window_minutes)
    filtered = []
    for event in events:
        if not event["uid"]:
            continue
        if event["uid"] in notified_ids:
            continue
        if event["start"] < now:
            continue
        if event["start"] > window_end:
            continue
        if event["end"] <= now:
            continue
        filtered.append(event)
    filtered.sort(key=lambda e: e["start"])
    return filtered


def notify_webhook(url: str, message: str) -> None:
    response = requests.post(url, json={"text": message}, timeout=10)
    response.raise_for_status()


def main() -> int:
    try:
        ical_url = load_env_variable("ICAL_URL")
        webhook_url = load_env_variable("MATTERMOST_WEBHOOK_URL")
        window_minutes_raw = load_env_variable("NOTICE_WINDOW_MINUTES")
        timezone_name = load_env_variable("TIMEZONE", required=False)
        max_events_raw = load_env_variable("MAX_EVENTS", required=False)
        state_file = load_env_variable("STATE_FILE", required=False) or "state/notifications.json"
    except ConfigurationError as exc:
        print(f"[ERROR] {exc}")
        return 1

    try:
        window_minutes = int(window_minutes_raw)
        if window_minutes <= 0:
            raise ValueError
    except ValueError:
        print("[ERROR] NOTICE_WINDOW_MINUTES must be a positive integer")
        return 1

    max_events = None
    if max_events_raw:
        try:
            parsed = int(max_events_raw)
            if parsed > 0:
                max_events = parsed
        except ValueError:
            print("[WARN] Ignoring MAX_EVENTS because it is not a number")

    tz = ensure_timezone(timezone_name)
    print(f"[INFO] Using timezone: {tz.key}")

    try:
        calendar = fetch_calendar(ical_url)
    except Exception as exc:  # network or parsing errors
        print(f"[ERROR] Failed to fetch or parse calendar: {exc}")
        return 1

    events = list(extract_events(calendar))
    print(f"[INFO] Retrieved {len(events)} events from calendar")

    now = datetime.now(timezone.utc)
    notified_state = load_state(state_file)
    original_count = len(notified_state)
    notified_state = cleanup_old_state(notified_state)
    cleaned_count = len(notified_state)
    if original_count > cleaned_count:
        print(f"[INFO] Cleaned up {original_count - cleaned_count} old notification(s) from state")
    upcoming = detect_upcoming_events(events, window_minutes, now, notified_state)
    if max_events:
        upcoming = upcoming[:max_events]

    if not upcoming:
        print("[INFO] No upcoming events to notify")
        save_state(state_file, notified_state)
        print(f"[INFO] Ensured notification state is stored at {state_file}")
        return 0

    message = build_message(upcoming, tz, window_minutes)
    print(f"[INFO] Sending notification for {len(upcoming)} event(s)")

    try:
        notify_webhook(webhook_url, message)
    except Exception as exc:
        print(f"[ERROR] Failed to send notification: {exc}")
        return 1

    for event in upcoming:
        notified_state[event["uid"]] = event["start"].isoformat()
    save_state(state_file, notified_state)
    print(f"[INFO] Saved notification state to {state_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
