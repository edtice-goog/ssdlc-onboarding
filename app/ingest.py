"""Sources, sync runs, change classification, staleness.

The ingestion half of the contract (DESIGN.md §5).  Two paths:

    changeset  explicit typed ops from a skill that read our state first.
               `remove` is asserted, never inferred.  Preferred.
    snapshot   a list we diff ourselves.  `completeness` decides whether
               absence may be acted on, and defaults to `partial`.
"""

from .core import (DEFAULT_DECISION_FIELDS, INTERNAL_SOURCE, Refused, audit,
                   content_hash, now, one, register_subject, rename_subject,
                   reparent_subjects, rows, scalar, ulid)

SUBJECT_FOR_ROLE = {"inventory": "inventory_record",
                    "code": "repository",
                    "build": "build_artifact"}


# ---------------------------------------------------------------- sources

def create_source(con, *, role, kind, name, created_by, base_url=None,
                  description=None, owner_team=None, tier_selection=None,
                  priority=100, expected_cadence_days=None, decision_fields=None):
    if role == "internal":
        raise Refused("internal_source_reserved",
                      "The internal role is reserved for the seeded Internal Register.",
                      detail=f"Attempted to create a source named {name!r} with role 'internal'.",
                      remedy="Use role inventory, code, build or evidence.")
    if scalar(con, "SELECT 1 FROM source WHERE name=? AND role=?", name, role):
        raise Refused("source_exists",
                      f"A {role} source named {name!r} is already registered.",
                      remedy="Use a different name, or the existing registration.",
                      status=409)
    sid = ulid()
    con.execute(
        "INSERT INTO source (id, role, kind, is_system, name, base_url, description,"
        " expected_cadence_days, owner_team, tier_selection, priority, created_by,"
        " created_at) VALUES (?,?,?,0,?,?,?,?,?,?,?,?,?)",
        (sid, role, kind, name, base_url, description, expected_cadence_days,
         owner_team, tier_selection, priority if role == "inventory" else None,
         created_by, now()))
    for f in (decision_fields or (DEFAULT_DECISION_FIELDS if role == "inventory" else ())):
        con.execute("INSERT INTO source_decision_field VALUES (?,?)", (sid, f))
    audit(con, created_by, "source.registered", source_id=sid,
          after=f"{name} ({role}/{kind})",
          rationale=f"tier_selection={tier_selection!r}" if tier_selection else None)
    con.commit()
    return get_source(con, sid)


def decision_fields(con, source_id):
    f = [r["field"] for r in rows(con,
         "SELECT field FROM source_decision_field WHERE source_id=?", source_id)]
    return f or list(DEFAULT_DECISION_FIELDS)


def get_source(con, source_id):
    s = one(con, "SELECT * FROM source WHERE id=?", source_id)
    if not s:
        raise Refused("source_not_found", f"No source with id {source_id!r}.",
                      source_id=source_id, status=404)
    last = one(con, "SELECT * FROM sync_run WHERE id=?", s["last_sync_run_id"]) \
        if s["last_sync_run_id"] else None
    subject_type = SUBJECT_FOR_ROLE.get(s["role"])
    s["record_count"] = scalar(
        con, "SELECT COUNT(*) FROM subject WHERE source_id=? AND subject_type=?",
        source_id, subject_type) if subject_type else scalar(
        con, "SELECT COUNT(*) FROM subject WHERE source_id=?", source_id)
    from .core import days_since
    s["last_sync_at"] = last["started_at"] if last else None
    s["last_sync_by"] = last["actor"] if last else None
    s["last_sync_method"] = last["method"] if last else None
    s["last_sync_completeness"] = last["completeness"] if last else None
    s["staleness_days"] = days_since(s["last_sync_at"])
    s["decision_fields"] = decision_fields(con, source_id) if s["role"] == "inventory" else []
    return s


def list_sources(con, role=None, kind=None, owner_team=None):
    sql, args = "SELECT id FROM source WHERE 1=1", []
    for col, val in (("role", role), ("kind", kind), ("owner_team", owner_team)):
        if val:
            sql += f" AND {col}=?"; args.append(val)
    return [get_source(con, r["id"]) for r in rows(con, sql + " ORDER BY role, name", *args)]


def retire_source(con, source_id, *, rationale, retired_by, artifact_dir="artifacts"):
    """Reparent everything to the Internal Register.  Nothing is deleted (§3.0.2)."""
    import json, pathlib
    s = get_source(con, source_id)
    if s["is_system"]:
        raise Refused("system_source", "The Internal Register cannot be retired.",
                      detail="It holds every row this application owns rather than federates.",
                      source_id=source_id, status=409)

    moved = reparent_subjects(con, source_id)
    # UNIQUE(source_id, external_id) would collide if two retired sources both
    # used the same key, so qualify the key on the way across.
    for m in moved:
        if m["subject_type"] == "inventory_record":
            con.execute("UPDATE inventory_record SET source_id=?,"
                        " external_id = ? || ':' || external_id WHERE id=?",
                        (INTERNAL_SOURCE, s["name"], m["id"]))
        elif m["subject_type"] == "repository":
            con.execute("UPDATE repository SET source_id=?,"
                        " external_id = ? || ':' || external_id WHERE id=?",
                        (INTERNAL_SOURCE, s["name"], m["id"]))
        elif m["subject_type"] == "build_artifact":
            con.execute("UPDATE build_artifact SET source_id=?,"
                        " external_id = ? || ':' || external_id WHERE id=?",
                        (INTERNAL_SOURCE, s["name"], m["id"]))

    did = ulid()
    con.execute("INSERT INTO decision (id, kind, disposition, rationale, decided_by,"
                " decided_at, confirmed_by_user) VALUES (?,?,?,?,?,?,1)",
                (did, "orphan", "acknowledged", rationale, retired_by, now()))
    for m in moved:
        con.execute("INSERT INTO decision_subject (decision_id, subject_id) VALUES (?,?)",
                    (did, m["id"]))

    # The artifact.  We write this; we do not write the restore (P8).
    d = pathlib.Path(artifact_dir); d.mkdir(parents=True, exist_ok=True)
    path = d / f"source-retired-{source_id}.json"
    path.write_text(json.dumps({
        "retired_at": now(), "retired_by": retired_by, "rationale": rationale,
        "source": {k: s[k] for k in ("id", "role", "kind", "name", "base_url",
                                     "tier_selection", "priority", "owner_team",
                                     "expected_cadence_days")},
        "decision_fields": s["decision_fields"],
        "reparented_to": INTERNAL_SOURCE,
        "note": "Every subject below was reparented to the Internal Register. "
                "Nothing was deleted. External ids were prefixed with the source "
                "name to avoid key collisions. This file plus the API is intended "
                "to be sufficient to reconstruct the prior state by inference; "
                "there is no restore routine.",
        "subjects": moved,
    }, indent=2), encoding="utf-8")

    con.execute("DELETE FROM source_decision_field WHERE source_id=?", (source_id,))
    con.execute("DELETE FROM source_field_priority WHERE source_id=?", (source_id,))
    con.execute("UPDATE sync_run SET source_id=? WHERE source_id=?",
                (INTERNAL_SOURCE, source_id))
    con.execute("UPDATE decision_packet SET source_id=NULL WHERE source_id=?", (source_id,))
    con.execute("UPDATE audit_event SET source_id=NULL WHERE source_id=?", (source_id,))
    con.execute("DELETE FROM source WHERE id=?", (source_id,))
    audit(con, retired_by, "source.retired", rationale=rationale, decision_id=did,
          before=f"{s['name']} ({s['role']})",
          after=f"{len(moved)} subjects reparented to Internal Register")
    con.commit()

    by_type: dict[str, int] = {}
    for m in moved:
        by_type[m["subject_type"]] = by_type.get(m["subject_type"], 0) + 1
    return {"source_id": source_id, "reparented_count": len(moved),
            "reparented_by_type": by_type, "artifact_ref": str(path),
            "decision_id": did}


# ------------------------------------------------------------ orientation

def known_inventory(con, source_id, *, scope_status=None, include_absent=False,
                    limit=500):
    sql = ("SELECT r.external_id, r.name, r.content_hash, r.scope_status,"
           " r.scope_stale, r.last_seen, r.absent_since,"
           " (SELECT l.application_id FROM application_source_link l"
           "   WHERE l.inventory_record_id = r.id) AS application_id"
           " FROM inventory_record r WHERE r.source_id = ?")
    args = [source_id]
    if not include_absent:
        sql += " AND r.absent_since IS NULL"
    if scope_status:
        sql += " AND r.scope_status = ?"; args.append(scope_status)
    out = rows(con, sql + " ORDER BY r.name LIMIT ?", *args, limit)
    for r in out:
        r["scope_stale"] = bool(r["scope_stale"])
    return out


# ------------------------------------------------------------------ sync

def _classify(existing, incoming_hash, incoming, fields):
    if existing is None:
        return "new"
    if existing["content_hash"] != incoming_hash:
        return "material"
    for k, v in incoming.items():
        if k in ("external_id",) or k in fields:
            continue
        if k in existing and (existing[k] or None) != (v or None):
            return "immaterial"
    return "unchanged"


def _mark_stale(con, subject_id, changed):
    """A material change does not invalidate a decision; it marks it stale."""
    n = con.execute(
        "UPDATE decision_subject SET status='stale', went_stale_at=?"
        " WHERE subject_id=? AND status='current'", (now(), subject_id)).rowcount
    if n:
        con.execute("UPDATE inventory_record SET scope_stale=1 WHERE id=?", (subject_id,))
    return n


def _upsert_inventory(con, source, op, sync_run_id, fields):
    key = op["key"]
    vals = {f: op.get(f) for f in
            ("name", "description", "record_class", "lifecycle_state",
             "criticality", "business_owner", "engineering_owner")}
    h = content_hash(vals, fields)
    existing = one(con, "SELECT * FROM inventory_record WHERE source_id=? AND external_id=?",
                   source["id"], key)
    cls = _classify(existing, h, vals, fields)
    t = now()

    if existing is None:
        rid = ulid()
        register_subject(con, rid, "inventory_record", source["id"], vals["name"] or key)
        con.execute(
            "INSERT INTO inventory_record (id, source_id, external_id, name, description,"
            " record_class, lifecycle_state, criticality, business_owner,"
            " engineering_owner, content_hash, first_seen, last_seen)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (rid, source["id"], key, vals["name"], vals["description"], vals["record_class"],
             vals["lifecycle_state"], vals["criticality"], vals["business_owner"],
             vals["engineering_owner"], h, t, t))
    else:
        rid = existing["id"]
        con.execute(
            "UPDATE inventory_record SET name=?, description=?, record_class=?,"
            " lifecycle_state=?, criticality=?, business_owner=?, engineering_owner=?,"
            " content_hash=?, last_seen=?, absent_since=NULL WHERE id=?",
            (vals["name"], vals["description"], vals["record_class"], vals["lifecycle_state"],
             vals["criticality"], vals["business_owner"], vals["engineering_owner"],
             h, t, rid))
        rename_subject(con, rid, vals["name"] or key)
        if cls == "material":
            _mark_stale(con, rid, [f for f in fields
                                   if (existing[f] or None) != (vals.get(f) or None)])

    for a in op.get("attributes", []):
        con.execute(
            "INSERT INTO inventory_record_attribute (record_id, key, value_type,"
            " value_text, value_number, value_datetime, value_bool)"
            " VALUES (?,?,?,?,?,?,?)"
            " ON CONFLICT(record_id, key) DO UPDATE SET value_type=excluded.value_type,"
            " value_text=excluded.value_text, value_number=excluded.value_number,"
            " value_datetime=excluded.value_datetime, value_bool=excluded.value_bool",
            (rid, a["key"], a.get("value_type", "text"), a.get("value_text"),
             a.get("value_number"), a.get("value_datetime"), a.get("value_bool")))
    return rid, cls


def _upsert_repository(con, source, op, sync_run_id, fields):
    key = op["key"]
    vals = {"name": op.get("name"), "url": op.get("url"),
            "default_branch": op.get("default_branch"),
            "primary_language": op.get("primary_language"),
            "last_commit_at": op.get("last_commit_at"),
            "archived": 1 if op.get("archived") else 0}
    h = content_hash({k: str(v) for k, v in vals.items()}, vals.keys())
    existing = one(con, "SELECT * FROM repository WHERE source_id=? AND external_id=?",
                   source["id"], key)
    t = now()
    if existing is None:
        rid = ulid()
        register_subject(con, rid, "repository", source["id"], vals["name"] or key)
        con.execute(
            "INSERT INTO repository (id, source_id, external_id, name, url, default_branch,"
            " primary_language, last_commit_at, archived, content_hash, first_seen, last_seen)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (rid, source["id"], key, vals["name"], vals["url"], vals["default_branch"],
             vals["primary_language"], vals["last_commit_at"], vals["archived"], h, t, t))
        return rid, "new"
    rid = existing["id"]
    cls = "unchanged" if existing["content_hash"] == h else "material"
    con.execute(
        "UPDATE repository SET name=?, url=?, default_branch=?, primary_language=?,"
        " last_commit_at=?, archived=?, content_hash=?, last_seen=?, absent_since=NULL"
        " WHERE id=?",
        (vals["name"], vals["url"], vals["default_branch"], vals["primary_language"],
         vals["last_commit_at"], vals["archived"], h, t, rid))
    rename_subject(con, rid, vals["name"] or key)
    return rid, cls


def _upsert_artifact(con, source, op, sync_run_id, fields):
    key = op["key"]
    vals = {"coordinate": op.get("coordinate") or op.get("name"),
            "artifact_type": op.get("artifact_type"),
            "pipeline_ref": op.get("pipeline_ref")}
    h = content_hash({k: str(v) for k, v in vals.items()}, vals.keys())
    existing = one(con, "SELECT * FROM build_artifact WHERE source_id=? AND external_id=?",
                   source["id"], key)
    t = now()
    if existing is None:
        aid = ulid()
        register_subject(con, aid, "build_artifact", source["id"], vals["coordinate"] or key)
        con.execute(
            "INSERT INTO build_artifact (id, source_id, external_id, coordinate,"
            " artifact_type, pipeline_ref, content_hash, first_seen, last_seen)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (aid, source["id"], key, vals["coordinate"], vals["artifact_type"],
             vals["pipeline_ref"], h, t, t))
        return aid, "new"
    aid = existing["id"]
    cls = "unchanged" if existing["content_hash"] == h else "material"
    con.execute("UPDATE build_artifact SET coordinate=?, artifact_type=?, pipeline_ref=?,"
                " content_hash=?, last_seen=?, absent_since=NULL WHERE id=?",
                (vals["coordinate"], vals["artifact_type"], vals["pipeline_ref"], h, t, aid))
    rename_subject(con, aid, vals["coordinate"] or key)
    return aid, cls


UPSERT = {"inventory": _upsert_inventory, "code": _upsert_repository,
          "build": _upsert_artifact}
TABLE = {"inventory": "inventory_record", "code": "repository", "build": "build_artifact"}


def _mark_absent(con, source, keys_present, scope_declaration):
    """Only ever called for full/scoped syncs (§5.3)."""
    table = TABLE[source["role"]]
    absent = []
    for r in rows(con, f"SELECT id, external_id, name FROM {table}"
                       f" WHERE source_id=? AND absent_since IS NULL", source["id"]):
        if r["external_id"] in keys_present:
            continue
        con.execute(f"UPDATE {table} SET absent_since=? WHERE id=?", (now(), r["id"]))
        absent.append(r)
    return absent


def ingest(con, source_id, payload, *, mode):
    source = get_source(con, source_id)
    if source["is_system"]:
        raise Refused("system_source", "The Internal Register does not accept sync.",
                      detail="It holds rows this application owns rather than federates.",
                      source_id=source_id, status=409)
    role = source["role"]
    if role not in UPSERT:
        raise Refused("role_not_ingestable",
                      f"A source with role {role!r} does not accept {mode} ingestion.",
                      source_id=source_id)

    completeness = payload.get("completeness", "partial") if mode == "snapshot" else "partial"
    if completeness not in ("full", "scoped", "partial"):
        raise Refused("bad_completeness", f"Unknown completeness {completeness!r}.",
                      remedy="Use full, scoped or partial. Omitting it means partial.")
    ops = payload.get("ops") if mode == "changeset" else payload.get("records")
    if ops is None:
        raise Refused("missing_ops",
                      "No operations in the payload.",
                      detail=f"Expected {'ops' if mode == 'changeset' else 'records'}.",
                      source_id=source_id)

    run_id = ulid()
    con.execute(
        "INSERT INTO sync_run (id, source_id, mode, method, completeness,"
        " scope_declaration, contract_version, skill_version, actor, started_at,"
        " record_count, raw_payload_ref, status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,'running')",
        (run_id, source_id, mode, payload.get("method", "human_entry"), completeness,
         payload.get("scope_declaration"), payload.get("contract_version", "1.0"),
         payload.get("skill_version"), payload.get("actor", "unknown"), now(),
         len(ops), payload.get("raw_payload_ref")))

    fields = decision_fields(con, source_id)
    counts = {"new": 0, "material": 0, "immaterial": 0, "unchanged": 0, "absent": 0}
    warnings, keys_present = [], set()
    upsert = UPSERT[role]
    table = TABLE[role]

    for op in ops:
        kind = op.get("op", "add" if mode == "snapshot" else "add")
        key = op["key"]
        keys_present.add(key)
        if kind == "remove":
            ex = one(con, f"SELECT id FROM {table} WHERE source_id=? AND external_id=?",
                     source_id, key)
            if ex:
                con.execute(f"UPDATE {table} SET absent_since=? WHERE id=?", (now(), ex["id"]))
                counts["absent"] += 1
                con.execute("INSERT INTO sync_op (id, sync_run_id, op, record_key,"
                            " subject_id, change_class, detail) VALUES (?,?,?,?,?,?,?)",
                            (ulid(), run_id, "remove", key, ex["id"], "absent",
                             op.get("reason")))
            continue
        if kind == "unchanged":
            counts["unchanged"] += 1
            continue
        sid, cls = upsert(con, source, op, run_id, fields)
        counts[cls] = counts.get(cls, 0) + 1
        con.execute("INSERT INTO sync_op (id, sync_run_id, op, record_key, subject_id,"
                    " change_class, changed_fields) VALUES (?,?,?,?,?,?,?)",
                    (ulid(), run_id, kind, key, sid, cls,
                     ",".join(op.get("changed_fields", [])) or None))

    if mode == "snapshot" and completeness in ("full", "scoped"):
        absent = _mark_absent(con, source, keys_present, payload.get("scope_declaration"))
        counts["absent"] += len(absent)
        for r in absent:
            con.execute("INSERT INTO sync_op (id, sync_run_id, op, record_key, subject_id,"
                        " change_class, detail) VALUES (?,?,?,?,?,?,?)",
                        (ulid(), run_id, "remove", r["external_id"], r["id"], "absent",
                         f"not present in {completeness} sync"))
    elif mode == "snapshot":
        warnings.append(
            "completeness=partial: records not present in this payload were left "
            "untouched. Nothing was marked absent.")

    con.execute("UPDATE sync_run SET finished_at=?, status='complete' WHERE id=?",
                (now(), run_id))
    con.execute("UPDATE source SET last_sync_run_id=? WHERE id=?", (run_id, source_id))
    audit(con, payload.get("actor", "unknown"), f"source.synced.{mode}",
          source_id=source_id, sync_run_id=run_id,
          after=f"{counts['new']} new, {counts['material']} material, "
                f"{counts['absent']} absent")
    con.commit()

    result = {"sync_run_id": run_id, "mode": mode, "completeness": completeness,
              "counts": counts, "warnings": warnings,
              "decisions_applied": 0, "decisions_queued": 0}

    inline = payload.get("decisions") or []
    if inline:
        from .packets import apply_inline_decisions
        result.update(apply_inline_decisions(con, source_id, inline,
                                             payload.get("actor", "unknown")))
    return result
