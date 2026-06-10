"""Google Calendar sync (optional). The app is the source of truth.

Approved leaves are *pushed* as all-day events; externally-created events are
*pulled* in read-only. All ``google-*`` imports are lazy so the package only
needs to be installed where sync is actually used — tests and manual-only
deployments never import it.

One-time setup for a personal/company calendar (OAuth2):
  1. Google Cloud Console → enable "Google Calendar API".
  2. OAuth consent screen → External → add your account as a Test user.
  3. Credentials → OAuth client ID → Web application; redirect URI
     ``<host>/vacations/google/callback``.
  4. Put GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET in the environment.
  5. Visit /vacations/google/connect once to grant access (stores a refresh
     token in ``google_calendar_accounts``).
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

from flask import current_app

from app.vacations.models import GoogleCalendarAccount, LeaveEntry, LeaveKind

log = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/calendar"]

# Map a leave kind to a Google event colorId (calendar palette) for at-a-glance
# colour coding in the calendar UI. None → calendar default.
_KIND_COLOR = {
    LeaveKind.ANNUAL: "10",     # green
    LeaveKind.ON_DEMAND: "10",
    LeaveKind.SICK: "11",       # red
    LeaveKind.UNPAID: "8",      # graphite
}


def is_configured() -> bool:
    """True when OAuth client credentials are present in config."""
    return bool(
        current_app.config.get("GOOGLE_CLIENT_ID")
        and current_app.config.get("GOOGLE_CLIENT_SECRET")
    )


def _client_config(redirect_uri: str) -> dict:
    return {
        "web": {
            "client_id": current_app.config["GOOGLE_CLIENT_ID"],
            "client_secret": current_app.config["GOOGLE_CLIENT_SECRET"],
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [redirect_uri],
        }
    }


# --- OAuth flow -------------------------------------------------------------

def authorization_url(redirect_uri: str) -> tuple[str, str]:
    """Return (auth_url, state) to redirect the user to Google's consent page."""
    from google_auth_oauthlib.flow import Flow

    flow = Flow.from_client_config(
        _client_config(redirect_uri), scopes=SCOPES, redirect_uri=redirect_uri
    )
    return flow.authorization_url(
        access_type="offline", include_granted_scopes="true", prompt="consent"
    )


def exchange_code(code: str, redirect_uri: str, calendar_id: str = "primary") -> dict:
    """Exchange an OAuth ``code`` for credentials. Returns a token dict."""
    from google_auth_oauthlib.flow import Flow

    flow = Flow.from_client_config(
        _client_config(redirect_uri), scopes=SCOPES, redirect_uri=redirect_uri
    )
    flow.fetch_token(code=code)
    creds = flow.credentials
    return _creds_to_dict(creds)


def _creds_to_dict(creds) -> dict:
    return {
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "scopes": list(creds.scopes or SCOPES),
    }


def _credentials(account: GoogleCalendarAccount):
    """Build live google Credentials from the stored token, refreshing if stale."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    creds = Credentials(**account.token)
    if not creds.valid and creds.refresh_token:
        creds.refresh(Request())
        account.token = _creds_to_dict(creds)  # persist rotated token (caller commits)
    return creds


def _service(account: GoogleCalendarAccount):
    from googleapiclient.discovery import build

    return build("calendar", "v3", credentials=_credentials(account), cache_discovery=False)


def account_email(account: GoogleCalendarAccount) -> str | None:
    """Fetch the calendar's owner email (used to label the connection)."""
    try:
        svc = _service(account)
        cal = svc.calendarList().get(calendarId=account.calendar_id).execute()
        return cal.get("id") or cal.get("summary")
    except Exception:  # noqa: BLE001 — labelling is best-effort
        log.warning("Could not fetch Google calendar label", exc_info=True)
        return None


# --- Push (app → Google) ----------------------------------------------------

def _event_body(entry: LeaveEntry) -> dict:
    # All-day Google events use exclusive end dates → +1 day on our inclusive end.
    summary = {
        LeaveKind.ANNUAL: "Urlop wypoczynkowy",
        LeaveKind.ON_DEMAND: "Urlop na żądanie",
        LeaveKind.SICK: "L4 / Zwolnienie lekarskie",
        LeaveKind.UNPAID: "Urlop bezpłatny",
        LeaveKind.OTHER: "Nieobecność",
    }.get(entry.kind, "Nieobecność")
    name = entry.driver.full_name if entry.driver else ""
    body = {
        "summary": f"{summary} — {name}".strip(" —"),
        "description": entry.note or "",
        "start": {"date": entry.start_date.isoformat()},
        "end": {"date": (entry.end_date + timedelta(days=1)).isoformat()},
        "transparency": "transparent",
        "extendedProperties": {
            "private": {
                "driver_uuid": str(entry.driver_uuid),
                "leave_uuid": str(entry.uuid),
                "kind": entry.kind.value,
            }
        },
    }
    if (color := _KIND_COLOR.get(entry.kind)) is not None:
        body["colorId"] = color
    return body


def push_event(account: GoogleCalendarAccount, entry: LeaveEntry) -> dict:
    """Create or update the calendar event for ``entry``. Returns {id, etag}."""
    svc = _service(account)
    body = _event_body(entry)
    if entry.google_event_id:
        ev = svc.events().update(
            calendarId=account.calendar_id, eventId=entry.google_event_id, body=body
        ).execute()
    else:
        ev = svc.events().insert(calendarId=account.calendar_id, body=body).execute()
    return {"id": ev.get("id"), "etag": ev.get("etag")}


def delete_event(account: GoogleCalendarAccount, event_id: str) -> None:
    svc = _service(account)
    try:
        svc.events().delete(calendarId=account.calendar_id, eventId=event_id).execute()
    except Exception:  # noqa: BLE001 — already gone is fine
        log.warning("Google event delete failed for %s", event_id, exc_info=True)


# --- Pull (Google → app, read-only import) ----------------------------------

def classify_kind(summary: str) -> LeaveKind:
    """Best-effort kind from an event title (for externally-created events)."""
    s = (summary or "").lower()
    if any(w in s for w in ("l4", "chor", "sick", "больн", "lekar")):
        return LeaveKind.SICK
    if "żąda" in s or "zada" in s or "demand" in s:
        return LeaveKind.ON_DEMAND
    if any(w in s for w in ("urlop", "vacation", "leave", "отпуск", "wypocz")):
        return LeaveKind.ANNUAL
    return LeaveKind.OTHER


def _parse_all_day(value: dict) -> date | None:
    if "date" in value:
        return date.fromisoformat(value["date"])
    if "dateTime" in value:
        return datetime.fromisoformat(value["dateTime"].replace("Z", "+00:00")).date()
    return None


def pull_events(
    account: GoogleCalendarAccount, time_min: date, time_max: date
) -> list[dict]:
    """List events in [time_min, time_max] normalised for upsert.

    Each dict: id, etag, summary, start_date, end_date (inclusive),
    driver_uuid (from our extendedProperties, if present), kind, raw.
    """
    svc = _service(account)
    items: list[dict] = []
    page_token = None
    while True:
        resp = svc.events().list(
            calendarId=account.calendar_id,
            timeMin=datetime.combine(time_min, datetime.min.time()).isoformat() + "Z",
            timeMax=datetime.combine(time_max, datetime.min.time()).isoformat() + "Z",
            singleEvents=True,
            showDeleted=False,
            pageToken=page_token,
            maxResults=2500,
        ).execute()
        for ev in resp.get("items", []):
            start = _parse_all_day(ev.get("start", {}))
            end = _parse_all_day(ev.get("end", {}))
            if start is None or end is None:
                continue
            # Google all-day end is exclusive → back to our inclusive end.
            inclusive_end = end - timedelta(days=1) if "date" in ev.get("end", {}) else end
            props = (ev.get("extendedProperties", {}) or {}).get("private", {}) or {}
            kind_val = props.get("kind")
            try:
                kind = LeaveKind(kind_val) if kind_val else classify_kind(ev.get("summary", ""))
            except ValueError:
                kind = classify_kind(ev.get("summary", ""))
            items.append(
                {
                    "id": ev.get("id"),
                    "etag": ev.get("etag"),
                    "summary": ev.get("summary", ""),
                    "start_date": start,
                    "end_date": max(inclusive_end, start),
                    "driver_uuid": props.get("driver_uuid"),
                    "kind": kind,
                    "raw": ev,
                }
            )
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return items
