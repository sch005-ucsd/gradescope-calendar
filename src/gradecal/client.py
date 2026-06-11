"""A minimal Gradescope client.

Gradescope has no public API, so this logs in with the user's own credentials
and scrapes the HTML of pages they already have access to. The CSS selectors in
``_parse_*`` are the brittle part: if Gradescope changes its markup, those are
the functions to update. Everything is isolated there on purpose.
"""

from __future__ import annotations

import re
from typing import List, Optional

import requests
from bs4 import BeautifulSoup
from dateutil import parser as dateparser

from .models import Assignment, Course

BASE = "https://www.gradescope.com"
LOGIN_URL = f"{BASE}/login"

# A normal-looking user agent; Gradescope serves different markup to odd clients.
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    )
}


class GradescopeError(Exception):
    """Raised for login failures or unexpected page structure."""


class GradescopeClient:
    """Logs in once, then exposes course/assignment scraping helpers."""

    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update(_HEADERS)

    # ------------------------------------------------------------------ auth
    def login(self, email: str, password: str) -> None:
        """Authenticate against Gradescope.

        Gradescope uses a Rails CSRF token: we GET the login page, pull the
        hidden ``authenticity_token``, then POST the credentials with it.
        """
        page = self.session.get(LOGIN_URL)
        token = self._csrf_token(page.text)
        if token is None:
            raise GradescopeError(
                "Could not find the login CSRF token. Gradescope's login page "
                "may have changed."
            )

        resp = self.session.post(
            LOGIN_URL,
            data={
                "utf8": "\u2713",
                "authenticity_token": token,
                "session[email]": email,
                "session[password]": password,
                "session[remember_me]": "0",
                "commit": "Log In",
                "session[remember_me_sso]": "0",
            },
            allow_redirects=True,
        )

        # On success Gradescope redirects to /account; on failure it re-renders
        # the login form (which still contains the password field).
        if "session[password]" in resp.text or resp.url.rstrip("/").endswith("/login"):
            raise GradescopeError(
                "Login failed. Double-check your email and password "
                "(and that you don't use SSO-only login)."
            )

    @staticmethod
    def _csrf_token(html: str) -> Optional[str]:
        soup = BeautifulSoup(html, "lxml")
        tag = soup.find("input", attrs={"name": "authenticity_token"})
        if tag and tag.get("value"):
            return tag["value"]
        meta = soup.find("meta", attrs={"name": "csrf-token"})
        if meta and meta.get("content"):
            return meta["content"]
        return None

    # --------------------------------------------------------------- courses
    def get_courses(self) -> List[Course]:
        """Return the courses shown on the student dashboard."""
        resp = self.session.get(f"{BASE}/account")
        return self._parse_courses(resp.text)

    @staticmethod
    def _parse_courses(html: str) -> List[Course]:
        soup = BeautifulSoup(html, "lxml")
        courses: List[Course] = []

        # The dashboard groups course cards under term headings. Each card is an
        # <a class="courseBox" href="/courses/<id>"> with a short name and a
        # longer title inside.
        for term_block in soup.select(".courseList--coursesForTerm"):
            term = ""
            # The term label is the sibling heading just before this block.
            heading = term_block.find_previous_sibling(
                class_="courseList--term"
            )
            if heading:
                term = heading.get_text(strip=True)

            for box in term_block.select("a.courseBox"):
                href = box.get("href", "")
                m = re.search(r"/courses/(\d+)", href)
                if not m:
                    continue
                short = box.select_one(".courseBox--shortname")
                full = box.select_one(".courseBox--name")
                courses.append(
                    Course(
                        course_id=m.group(1),
                        name=short.get_text(strip=True) if short else href,
                        full_name=full.get_text(strip=True) if full else "",
                        term=term,
                    )
                )

        # Fallback: some account pages render a flat list of courseBox links.
        if not courses:
            for box in soup.select("a.courseBox"):
                href = box.get("href", "")
                m = re.search(r"/courses/(\d+)", href)
                if not m:
                    continue
                short = box.select_one(".courseBox--shortname")
                full = box.select_one(".courseBox--name")
                courses.append(
                    Course(
                        course_id=m.group(1),
                        name=short.get_text(strip=True) if short else href,
                        full_name=full.get_text(strip=True) if full else "",
                        term="",
                    )
                )

        return courses

    # ----------------------------------------------------------- assignments
    def get_assignments(self, course: Course) -> List[Assignment]:
        """Scrape one course's assignment table."""
        resp = self.session.get(course.url)
        return self._parse_assignments(resp.text, course)

    @staticmethod
    def _parse_assignments(html: str, course: Course) -> List[Assignment]:
        soup = BeautifulSoup(html, "lxml")
        out: List[Assignment] = []

        table = soup.select_one("#assignments-student-table") or soup.find("table")
        if table is None:
            return out

        body = table.find("tbody") or table
        for row in body.find_all("tr"):
            name_cell = row.select_one("th") or row.find("td")
            if name_cell is None:
                continue
            name = name_cell.get_text(strip=True)
            if not name:
                continue

            link = name_cell.find("a")
            assignment_id = None
            if link and link.get("href"):
                m = re.search(r"/assignments/(\d+)", link["href"])
                if m:
                    assignment_id = m.group(1)

            released, due = GradescopeClient._extract_dates(row)
            out.append(
                Assignment(
                    course=course,
                    name=name,
                    due=due,
                    released=released,
                    assignment_id=assignment_id,
                )
            )
        return out

    @staticmethod
    def _extract_dates(row):
        """Pull release/due datetimes from a table row.

        Strategy, most reliable first:
          1. <time datetime="..."> elements (ISO 8601, timezone-aware).
          2. Elements whose class mentions release/due, parsed leniently.
        """
        released = due = None

        times = row.find_all("time")
        parsed = []
        for t in times:
            raw = t.get("datetime") or t.get_text(strip=True)
            dt = _safe_parse(raw)
            if dt:
                parsed.append(dt)
        if len(parsed) >= 2:
            released, due = parsed[0], parsed[-1]
        elif len(parsed) == 1:
            due = parsed[0]

        if due is None:
            due_el = row.find(class_=re.compile(r"dueDate", re.I))
            if due_el:
                due = _safe_parse(_clean(due_el.get_text(" ", strip=True)))
        if released is None:
            rel_el = row.find(class_=re.compile(r"releaseDate", re.I))
            if rel_el:
                released = _safe_parse(_clean(rel_el.get_text(" ", strip=True)))

        return released, due


def _clean(text: str) -> str:
    """Strip leading labels like 'Due ' or 'Late Due Date: '."""
    return re.sub(r"^(late\s+)?(due|released)(\s+date)?:?\s*", "", text, flags=re.I)


def _safe_parse(raw: Optional[str]):
    if not raw:
        return None
    try:
        return dateparser.parse(raw)
    except (ValueError, OverflowError, TypeError):
        return None
