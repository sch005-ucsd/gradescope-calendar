"""Command-line interface for gradecal."""

from __future__ import annotations

import os
import sys
from getpass import getpass
from typing import List, Optional

import click

from . import __version__
from .client import Assignment, GradescopeClient, GradescopeError
from .google_calendar import (
    DEFAULT_CALENDAR_NAME,
    GoogleAuthError,
    GoogleCalendarBackend,
    authorize,
    load_credentials,
)


def _credentials(email: Optional[str], password: Optional[str]):
    """Resolve Gradescope credentials: flags > env vars > interactive prompt.

    For unattended runs (cron, CI) set GRADESCOPE_EMAIL and GRADESCOPE_PASSWORD
    so nothing prompts. Passwords are never taken as a plain flag.
    """
    email = email or os.environ.get("GRADESCOPE_EMAIL")
    password = password or os.environ.get("GRADESCOPE_PASSWORD")
    if not email:
        email = click.prompt("Gradescope email")
    if not password:
        password = getpass("Gradescope password: ")
    return email, password


def _login(email, password) -> GradescopeClient:
    client = GradescopeClient()
    try:
        client.login(email, password)
    except GradescopeError as e:
        raise click.ClickException(str(e))
    return client


def _matches(course, filters: List[str]) -> bool:
    if not filters:
        return True
    hay = f"{course.name} {course.full_name} {course.term}".lower()
    return any(f.lower() in hay for f in filters)


@click.group()
@click.version_option(__version__, prog_name="gradecal")
def main() -> None:
    """Sync your Gradescope assignment due dates into Google Calendar.

    First run `gradecal auth` once to connect your Google account, then
    `gradecal sync` whenever you want to pull in new and changed deadlines.
    Schedule `gradecal sync` (cron or GitHub Actions) for a hands-off daily sync.
    """


@main.command()
def auth() -> None:
    """One-time: authorize gradecal to access your Google Calendar."""
    try:
        authorize()
    except GoogleAuthError as e:
        raise click.ClickException(str(e))
    click.echo("Authorized. Token saved. You can now run `gradecal sync`.")


@main.command(name="list")
@click.option("--email", help="Gradescope email (or set GRADESCOPE_EMAIL).")
@click.option("--password", help="Avoid; prefer GRADESCOPE_PASSWORD or the prompt.")
def list_courses(email, password) -> None:
    """List the courses gradecal can see on your Gradescope account."""
    client = _login(*_credentials(email, password))
    courses = client.get_courses()
    if not courses:
        click.echo("No courses found.")
        return
    for c in courses:
        term = f" [{c.term}]" if c.term else ""
        click.echo(f"  {c.name:<12} {c.full_name}{term}")


@main.command()
@click.option("--email", help="Gradescope email (or set GRADESCOPE_EMAIL).")
@click.option("--password", help="Avoid; prefer GRADESCOPE_PASSWORD or the prompt.")
@click.option(
    "-c", "--course", "course_filters", multiple=True,
    help="Only sync courses matching this text (repeatable).",
)
@click.option(
    "-r", "--reminder", default=1440, show_default=True,
    help="Minutes before each deadline to fire a reminder (0 = none).",
)
@click.option(
    "--calendar-name", default=DEFAULT_CALENDAR_NAME, show_default=True,
    help="Name of the Google Calendar to write to (created if missing).",
)
@click.option(
    "--timezone", default="America/Los_Angeles", show_default=True,
    help="Timezone for any deadlines parsed without an explicit offset.",
)
def sync(email, password, course_filters, reminder, calendar_name, timezone) -> None:
    """Scrape Gradescope and push due dates into Google Calendar.

    Idempotent: safe to run as often as you like. New assignments are added,
    moved deadlines are updated, and nothing is duplicated.
    """
    # Check Google auth first so an expired token fails fast, before scraping.
    try:
        creds = load_credentials()
    except GoogleAuthError as e:
        raise click.ClickException(str(e))
    backend = GoogleCalendarBackend(creds)
    calendar_id = backend.get_or_create_calendar(calendar_name, timezone)

    client = _login(*_credentials(email, password))
    courses = [c for c in client.get_courses() if _matches(c, list(course_filters))]
    if not courses:
        raise click.ClickException("No matching courses found.")

    created = updated = 0
    for c in courses:
        dated = [a for a in client.get_assignments(c) if a.due is not None]
        for a in dated:
            result = backend.upsert(calendar_id, a, reminder, timezone)
            created += result == "created"
            updated += result == "updated"
        click.echo(f"  {c.name:<12} {len(dated)} dated assignment(s)")

    click.echo(
        f"\nDone. Calendar '{calendar_name}': "
        f"{created} added, {updated} updated."
    )


if __name__ == "__main__":
    sys.exit(main())
