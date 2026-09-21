"""Identifiers, database access, subject registration, audit.

Standard library only.  Everything else in the application builds on this.
"""

import os
import pathlib
import secrets
import sqlite3
import time
from datetime import datetime, timezone

SCHEMA = pathlib.Path(__file__).parent.parent / "schema" / "schema.sqlite.sql"
INTERNAL_SOURCE = "SYSTEM00000000000000000000"
CONTRACT_VERSION = "1.0"

# Default decision-relevant fields for an inventory source (§5.4).  A source may
# declare its own set in source_decision_field.
DEFAULT_DECISION_FIELDS = (
    "name", "record_class", "lifecycle_state",
    "criticality", "business_owner", "engineering_owner",
)

# The federated attribute set we own (P7).  Nothing else is promoted.
FEDERATED_FIELDS = (
    "business_owner", "engineering_owner", "criticality", "lifecycle_state",
)

_B32 = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"          # Crockford, no I L O U


def ulid() -> str:
    """26-character, time-ordered, URL-safe.  Sorts by creation."""
    ms = int(time.time() * 1000)
    out = []
    for _ in range(10):
        out.append(_B32[ms & 31]); ms >>= 5
    ts = "".join(reversed(out))
    rand = "".join(_B32[secrets.randbelow(32)] for _ in range(16))
    return ts + rand


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def days_since(ts: str | None) -> int | None:
    if not ts:
        return None
    try:
        t = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return max(0, (datetime.now(timezone.utc) - t).days)


# ---------------------------------------------------------------- database

def connect(path: str) -> sqlite3.Connection:
    fresh = path == ":memory:" or not os.path.exists(path)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    if fresh:
        con.executescript(SCHEMA.read_text(encoding="utf-8"))
        con.commit()
    return con


def rows(con, sql, *args) -> list[dict]:
    return [dict(r) for r in con.execute(sql, args)]


def one(con, sql, *args) -> dict | None:
    r = con.execute(sql, args).fetchone()
    return dict(r) if r else None


def scalar(con, sql, *args):
    r = con.execute(sql, args).fetchone()
    return r[0] if r else None


class Refused(Exception):
    """A request we will not perform.

    Carries enough context to act on without reading our source (P8): what was
    attempted, against what, and what would make it succeed.
    """

    def __init__(self, code, message, detail=None, remedy=None,
                 subject_id=None, source_id=None, status=400):
        super().__init__(message)
        self.code, self.message, self.detail = code, message, detail
        self.remedy, self.subject_id, self.source_id = remedy, subject_id, source_id
        self.status = status

    def payload(self) -> dict:
        return {k: v for k, v in {
            "code": self.code, "message": self.message, "detail": self.detail,
            "remedy": self.remedy, "subject_id": self.subject_id,
            "source_id": self.source_id,
        }.items() if v is not None}


# ------------------------------------------------------- subject registry

def register_subject(con, subject_id, subject_type, source_id, display_name):
    """Every governed entity registers here first.  There is no null source."""
    con.execute(
        "INSERT INTO subject (id, subject_type, source_id, display_name, created_at)"
        " VALUES (?,?,?,?,?)",
        (subject_id, subject_type, source_id, display_name, now()))


def rename_subject(con, subject_id, display_name):
    """MATERIALIZED field: single writer, same transaction as the source row."""
    con.execute("UPDATE subject SET display_name = ? WHERE id = ?",
                (display_name, subject_id))


def reparent_subjects(con, from_source, to_source=INTERNAL_SOURCE) -> list[dict]:
    moved = rows(con, "SELECT id, subject_type, display_name FROM subject"
                      " WHERE source_id = ?", from_source)
    con.execute("UPDATE subject SET source_id = ? WHERE source_id = ?",
                (to_source, from_source))
    return moved


# ------------------------------------------------------------------ audit

def audit(con, actor, action, *, subject_id=None, source_id=None,
          before=None, after=None, rationale=None,
          decision_id=None, packet_id=None, sync_run_id=None):
    con.execute(
        "INSERT INTO audit_event (id, at, actor, action, subject_id, source_id,"
        " before_text, after_text, rationale, decision_id, decision_packet_id,"
        " sync_run_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (ulid(), now(), actor, action, subject_id, source_id,
         before, after, rationale, decision_id, packet_id, sync_run_id))


def content_hash(values: dict, fields) -> str:
    import hashlib
    h = hashlib.sha256()
    for f in sorted(fields):
        h.update(f.encode()); h.update(b"\x00")
        h.update((values.get(f) or "").encode()); h.update(b"\x1e")
    return h.hexdigest()[:32]
