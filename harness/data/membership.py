"""Point-in-time index membership rebuilt from free change-history lists.

Start from today's constituents and undo changes newest-first. Free lists are incomplete
(names added and removed between list snapshots, ticker renames), so the result is an
approximation used for universe selection only, never a claim of an unbiased universe.
"""

import csv
import io
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class MembershipChange:
    on: date
    added: frozenset[str]
    removed: frozenset[str]


def _tickers(cell: str | None) -> frozenset[str]:
    return frozenset(t.strip().upper() for t in (cell or "").split(";") if t.strip())


def parse_changes_csv(text: str) -> tuple[MembershipChange, ...]:
    lines = [ln for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
    rows = csv.DictReader(io.StringIO("\n".join(lines)))
    changes = [
        MembershipChange(
            on=date.fromisoformat(row["date"].strip()),
            added=_tickers(row.get("added")),
            removed=_tickers(row.get("removed")),
        )
        for row in rows
    ]
    return tuple(sorted(changes, key=lambda c: c.on))


@dataclass(frozen=True)
class IndexHistory:
    current: frozenset[str]
    changes: Sequence[MembershipChange]

    def members_on(self, on: date) -> frozenset[str]:
        members = set(self.current)
        for change in sorted(self.changes, key=lambda c: c.on, reverse=True):
            if change.on > on:
                members -= change.added
                members |= change.removed
        return frozenset(members)


class PointInTimeMembership:
    def __init__(self, histories: Mapping[str, IndexHistory]) -> None:
        self._histories = dict(histories)

    def membership(self, index: str, on: date) -> frozenset[str]:
        """Unknown indexes return an empty set, matching the in-memory fake."""
        history = self._histories.get(index)
        return history.members_on(on) if history else frozenset()

    def fingerprint(self) -> dict[tuple[str, date], frozenset[str]]:
        """Stable view of the underlying lists, for content-hashing into a snapshot."""
        out: dict[tuple[str, date], frozenset[str]] = {}
        for index, history in self._histories.items():
            out[(f"{index}/current", date.min)] = history.current
            for change in history.changes:
                out[(f"{index}/added", change.on)] = change.added
                out[(f"{index}/removed", change.on)] = change.removed
        return out
