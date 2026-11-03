"""The provider interface.

Every job source (Adzuna today, JSearch/Jooble/The Muse tomorrow) implements
this one method. Adding a source later is a new file, never a rewrite of the
pipeline. This is the design decision worth mentioning in interviews.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from models import JobPosting


class JobSource(ABC):
    name: str = "base"

    @abstractmethod
    def fetch(
        self, query: str, location: str, max_results: int = 20
    ) -> list[JobPosting]:
        """Return normalised JobPosting objects for a search."""
        raise NotImplementedError
