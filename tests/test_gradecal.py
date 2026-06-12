"""Tests that don't require network access.

We can't reach gradescope.com or Google in CI, so we test the pure pieces:
HTML parsing (against a fixture mirroring Gradescope's markup), the Google event
id derivation, and the event body builder.
"""

import re
from datetime import datetime, timezone

from gradecal.client import GradescopeClient
from gradecal.google_calendar import _event_body, _event_id
from gradecal.models import Assignment, Course

COURSE = Course(course_id="123", name="DSC 80", full_name="Data Science", term="Spring 2025")

ASSIGNMENTS_HTML = """
<table id="assignments-student-table"><tbody>
  <tr role="row">
    <th class="table--primaryLink">
      <a href="/courses/123/assignments/456/submissions">Homework 1</a>
    </th>
    <td class="submissionTimeChart">
      <time class="submissionTimeChart--releaseDate" datetime="2025-04-01T00:00:00-07:00">Apr 01</time>
      <time class="submissionTimeChart--dueDate" datetime="2025-04-08T23:59:00-07:00">Apr 08</time>
    </td>
  </tr>
  <tr role="row">
    <th><a href="/courses/123/assignments/789/submissions">Project</a></th>
    <td class="submissionTimeChart">
      <span class="submissionTimeChart--dueDate">Due Date: Jun 10 at 11:59PM</span>
    </td>
  </tr>
</tbody></table>
"""


def test_parse_assignments_with_time_tags():
    items = GradescopeClient._parse_assignments(ASSIGNMENTS_HTML, COURSE)
    assert len(items) == 2
    hw1 = items[0]
    assert hw1.name == "Homework 1"
    assert hw1.assignment_id == "456"
    assert hw1.due is not None
    assert (hw1.due.year, hw1.due.month, hw1.due.day) == (2025, 4, 8)


def test_parse_assignments_text_fallback():
    proj = GradescopeClient._parse_assignments(ASSIGNMENTS_HTML, COURSE)[1]
    assert proj.name == "Project"
    assert proj.due is not None and proj.due.month == 6 and proj.due.day == 10


def test_event_id_is_deterministic_and_valid():
    a = Assignment(COURSE, "HW1", datetime(2025, 4, 8, tzinfo=timezone.utc),
                   assignment_id="456")
    eid1 = _event_id(a.uid)
    eid2 = _event_id(a.uid)
    assert eid1 == eid2  # deterministic -> idempotent upsert
    # Google requires the base32hex alphabet (0-9, a-v), length 5-1024.
    assert re.fullmatch(r"[0-9a-v]{5,1024}", eid1)


def test_event_body_timezone_aware_vs_naive():
    aware = Assignment(COURSE, "HW1",
                       datetime(2025, 4, 8, 23, 59, tzinfo=timezone.utc),
                       assignment_id="456")
    body = _event_body(aware, reminder_minutes=60, default_tz="America/Los_Angeles")
    assert body["summary"] == "Due: HW1 (DSC 80)"
    assert "timeZone" not in body["start"]  # offset already in the datetime
    assert body["reminders"]["overrides"][0]["minutes"] == 60

    naive = Assignment(COURSE, "Proj", datetime(2025, 6, 10, 23, 59))
    nbody = _event_body(naive, reminder_minutes=0, default_tz="America/Los_Angeles")
    assert nbody["start"]["timeZone"] == "America/Los_Angeles"
    assert nbody["reminders"]["overrides"] == []  # reminders disabled
