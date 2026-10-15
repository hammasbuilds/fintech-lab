"""A hash-chained log of everything the assistant did, including everything it refused.

Each entry carries the hash of the one before it, so an entry cannot be removed or edited
without breaking every hash after it. ``verify`` reports the first index where the chain
breaks rather than a bare true/false, because "the log was tampered with" and "the log was
tampered with at entry 14" are different amounts of help.

Refusals are recorded as deliberately as executions. A log that only contains what
happened cannot answer the question that actually gets asked after a fraud: did the system
warn them?
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime

GENESIS = "0" * 64


def _digest(previous: str, index: int, kind: str, payload: dict, at: str) -> str:
    body = json.dumps(
        {"previous": previous, "index": index, "kind": kind, "payload": payload, "at": at},
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


@dataclass
class Entry:
    index: int
    kind: str
    payload: dict
    at: str
    previous: str
    digest: str

    def as_dict(self) -> dict[str, object]:
        return {
            "index": self.index,
            "kind": self.kind,
            "payload": self.payload,
            "at": self.at,
            "previous": self.previous,
            "digest": self.digest,
        }


@dataclass
class Ledger:
    entries: list[Entry] = field(default_factory=list)

    @property
    def head(self) -> str:
        return self.entries[-1].digest if self.entries else GENESIS

    def append(self, kind: str, payload: dict | None = None) -> dict[str, object]:
        body = payload or {}
        at = datetime.now(UTC).isoformat(timespec="seconds")
        index = len(self.entries)
        previous = self.head
        entry = Entry(
            index=index,
            kind=kind,
            payload=body,
            at=at,
            previous=previous,
            digest=_digest(previous, index, kind, body, at),
        )
        self.entries.append(entry)
        return entry.as_dict()

    def verify(self) -> dict[str, object]:
        """Recompute the whole chain. Names the first broken link, if there is one."""
        previous = GENESIS
        for position, entry in enumerate(self.entries):
            if entry.index != position:
                return {
                    "intact": False,
                    "broken_at": position,
                    "reason": f"entry at position {position} claims index {entry.index}",
                }
            if entry.previous != previous:
                return {
                    "intact": False,
                    "broken_at": position,
                    "reason": (
                        f"entry {position} points at {entry.previous[:12]}... but the "
                        f"entry before it hashes to {previous[:12]}..."
                    ),
                }
            expected = _digest(entry.previous, entry.index, entry.kind, entry.payload, entry.at)
            if expected != entry.digest:
                return {
                    "intact": False,
                    "broken_at": position,
                    "reason": (
                        f"entry {position} stores digest {entry.digest[:12]}... but its "
                        f"contents hash to {expected[:12]}..."
                    ),
                }
            previous = entry.digest
        return {"intact": True, "broken_at": None, "entries": len(self.entries)}

    def as_dict(self) -> dict[str, object]:
        return {
            "head": self.head,
            "entries": [entry.as_dict() for entry in self.entries],
            "verification": self.verify(),
        }
