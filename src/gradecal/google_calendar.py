"""Google Calendar backend.

Handles the one-time OAuth flow, automatic token refresh for unattended runs,
and idempotent upserts so a daily sync never creates duplicates.

The idempotency trick: each assignment has a stable UID. We derive a
deterministic Google event id from it (a hash), then insert; if Google says the
event already exists (HTTP 409) we update it instead. Same input -> same event,
every run.
"""

from __future__ import annotations

import hashlib
import os
from datetime import timedelta
from pathlib import Path
from typing import Optional

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from .models import Assignment

SCOPES = ["https://www.googleapis.com/auth/calendar"]

CONFIG_DIR = Path(
    os.environ.get("GRADECAL_CONFIG_DIR", str(Path.home() / ".config" / "gradecal"))
)
CREDENTIALS_PATH = CONFIG_DIR / "credentials.json"
TOKEN_PATH = CONFIG_DIR / "token.json"

DEFAULT_CALENDAR_NAME = "Gradescope"
_EVENT_MINUTES = 15  # render each deadline as a short block ending at the due time


class GoogleAuthError(Exception):
    """Raised when authorization is missing, expired, or unrecoverable."""


# ------------------------------------------------------------------- auth flow
def authorize(
    credentials_path: Path = CREDENTIALS_PATH,
    token_path: Path = TOKEN_PATH,
) -> Credentials:
    """Run the interactive browser OAuth flow once and persist the token.

    Needs an OAuth *Desktop* client JSON downloaded from Google Cloud Console
    (with the Calendar API enabled). The saved token embeds the refresh token,
    client id, and secret, so later runs refresh themselves with no browser.
    """
    if not credentials_path.exists():
        raise GoogleAuthError(
            f"OAuth client file not found at {credentials_path}.\n"
            "In Google Cloud Console: enable the Calendar API, create an OAuth "
            "client of type 'Desktop app', download the JSON, and save it there."
        )
    flow = InstalledAppFlow.from_client_secrets_file(str(credentials_path), SCOPES)
    creds = flow.run_local_server(port=0)
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(creds.to_json())
    try:
        token_path.chmod(0o600)
    except OSError:
        pass
    return creds


def load_credentials(token_path: Path = TOKEN_PATH) -> Credentials:
    """Load saved credentials, refreshing the access token if needed.

    Suitable for unattended runs: it never opens a browser. If the refresh
    token has been revoked/expired it raises GoogleAuthError telling the user
    to re-run `gradecal auth`.
    """
    if not token_path.exists():
        raise GoogleAuthError(
            f"No saved Google token at {token_path}. Run `gradecal auth` first."
        )
    creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            token_path.write_text(creds.to_json())
        except Exception as e:  # google libs raise various refresh errors
            raise GoogleAuthError(
                "Could not refresh the saved Google token (it may have been "
                "revoked or expired). Run `gradecal auth` again.\n"
                f"Details: {e}"
            )

    if not creds or not creds.valid:
        raise GoogleAuthError(
            "Saved Google credentials are not valid. Run `gradecal auth` again."
        )
    return creds


# ------------------------------------------------------------------- the client
class GoogleCalendarBackend:
    def __init__(self, creds: Credentials) -> None:
        self.service = build(
            "calendar", "v3", credentials=creds, cache_discovery=False
        )

    def get_or_create_calendar(
        self, name: str = DEFAULT_CALENDAR_NAME, timezone: str = "America/Los_Angeles"
    ) -> str:
        """Return the id of the named calendar, creating it if absent.

        Using a dedicated 'Gradescope' calendar keeps these events separate from
        your personal calendar and lets you hide or delete them in one click.
        """
        page_token = None
        while True:
            resp = self.service.calendarList().list(pageToken=page_token).execute()
            for entry in resp.get("items", []):
                if entry.get("summary") == name:
                    return entry["id"]
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
        created = self.service.calendars().insert(
            body={"summary": name, "timeZone": timezone}
        ).execute()
        return created["id"]

    def upsert(
        self,
        calendar_id: str,
        assignment: Assignment,
        reminder_minutes: int,
        default_tz: str,
    ) -> str:
        """Create or update one assignment's event. Returns 'created'/'updated'."""
        body = _event_body(assignment, reminder_minutes, default_tz)
        event_id = _event_id(assignment.uid)
        try:
            self.service.events().insert(
                calendarId=calendar_id, body={**body, "id": event_id}
            ).execute()
            return "created"
        except HttpError as e:
            if getattr(e, "resp", None) is not None and e.resp.status == 409:
                self.service.events().update(
                    calendarId=calendar_id, eventId=event_id, body=body
                ).execute()
                return "updated"
            raise


# ----------------------------------------------------------------- pure helpers
def _event_id(uid: str) -> str:
    """Deterministic, valid Google event id derived from the assignment UID.

    Google requires ids in the base32hex alphabet (0-9, a-v), length 5-1024.
    A hex SHA-1 digest (chars 0-9a-f) satisfies that and never collides in
    practice.
    """
    return hashlib.sha1(uid.encode("utf-8")).hexdigest()


def _event_body(a: Assignment, reminder_minutes: int, default_tz: str) -> dict:
    assert a.due is not None  # callers filter out undated assignments
    due = a.due
    start = due - timedelta(minutes=_EVENT_MINUTES)

    if due.tzinfo is not None:
        # Real instant: the RFC3339 string carries its own UTC offset.
        start_field = {"dateTime": start.isoformat()}
        end_field = {"dateTime": due.isoformat()}
    else:
        # Wall-clock time with no offset (text-parsed fallback): tag a timezone.
        start_field = {"dateTime": start.isoformat(), "timeZone": default_tz}
        end_field = {"dateTime": due.isoformat(), "timeZone": default_tz}

    body = {
        "summary": f"Due: {a.name} ({a.course.name})",
        "description": (
            f"{a.course.name} \u2014 {a.course.full_name}\n"
            f"Assignment: {a.name}\n{a.course.url}"
        ),
        "start": start_field,
        "end": end_field,
        "source": {"title": a.course.name, "url": a.course.url},
    }
    if reminder_minutes and reminder_minutes > 0:
        body["reminders"] = {
            "useDefault": False,
            "overrides": [{"method": "popup", "minutes": reminder_minutes}],
        }
    else:
        body["reminders"] = {"useDefault": False, "overrides": []}
    return body
