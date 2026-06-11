"""Simple data models for the objects we scrape from Gradescope."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass(frozen=True)
class Course:
    """A Gradescope course the user is enrolled in."""

    course_id: str
    name: str  # e.g. "DSC 80"
    full_name: str  # e.g. "Practice and Application of Data Science"
    term: str  # e.g. "Spring 2025"

    @property
    def url(self) -> str:
        return f"https://www.gradescope.com/courses/{self.course_id}"


@dataclass(frozen=True)
class Assignment:
    """A single assignment with a due date."""

    course: Course
    name: str
    due: Optional[datetime]  # timezone-aware when we can parse it; None if undated
    released: Optional[datetime] = None
    assignment_id: Optional[str] = None

    @property
    def uid(self) -> str:
        """A stable identifier so re-importing updates instead of duplicating."""
        aid = self.assignment_id or self.name.replace(" ", "-").lower()
        return f"{self.course.course_id}-{aid}@gradecal"
