"""Applications, federation and survivorship, managed entities, mappings.

The golden-record half of the model (DESIGN.md §3.2, §3.4).  Applications and
managed entities belong to the Internal Register: we own them rather than
federate them, and there is no null source.
"""

from .core import (FEDERATED_FIELDS, INTERNAL_SOURCE, Refused, audit, now, one,
                   register_subject, rename_subject, rows, scalar, ulid)


def next_handle(con) -> str:
    n = scalar(con, "SELECT COUNT(*) FROM application") + 1
    while scalar(con, "SELECT 1 FROM application WHERE handle=?", f"APP-{n:04d}"):
        n += 1
    return f"APP-{n:04d}"


def resolve(con, app_id):
    """Follow a merge alias.  Retired ids resolve forever (§3.2)."""
    a = one(con, "SELECT canonical_id FROM application_alias WHERE alias_id=?", app_id)
    return (a["canonical_id"], app_id) if a else (app_id, None)


def _by_handle(con, ref):
    if ref.startswith("APP-"):
        r = one(con, "SELECT id FROM application WHERE handle=?", ref)
        if r:
            return r["id"]
        r = one(con, "SELECT alias_id FROM application_alias WHERE alias_handle=?", ref)
        if r:
            return r["alias_id"]
    return ref


# ------------------------------------------------------------ survivorship

def resolve_attributes(con, app_id):
    """Field-level survivorship across linked source records.

    MATERIALIZED (single writer): writes attribute_resolution and the cached
    columns on application in the same transaction.
    """
    links = rows(con,
                 "SELECT l.inventory_record_id AS rid, r.source_id, s.name AS source_name,"
                 " s.priority, r.business_owner, r.engineering_owner, r.criticality,"
                 " r.lifecycle_state"
                 " FROM application_source_link l"
                 " JOIN inventory_record r ON r.id = l.inventory_record_id"
                 " JOIN source s ON s.id = r.source_id"
                 " WHERE l.application_id = ? AND r.absent_since IS NULL", app_id)
    updates = {}
    for field in FEDERATED_FIELDS:
        candidates = []
        for lk in links:
            if lk[field] is None:
                continue
            override = scalar(con, "SELECT priority FROM source_field_priority"
                                   " WHERE source_id=? AND field=?", lk["source_id"], field)
            candidates.append((override if override is not None
                               else (lk["priority"] if lk["priority"] is not None else 100),
                               lk["source_name"], lk["rid"], lk[field]))
        con.execute("DELETE FROM attribute_resolution WHERE application_id=? AND field=?",
                    (app_id, field))
        if not candidates:
            continue
        candidates.sort(key=lambda c: (c[0], c[1]))
        prio, sname, rid, value = candidates[0]
        distinct = {c[3] for c in candidates}
        reason = "only_source" if len(candidates) == 1 else "priority"
        con.execute("INSERT INTO attribute_resolution (application_id, field, resolved_value,"
                    " winning_record_id, reason, conflict, resolved_at) VALUES (?,?,?,?,?,?,?)",
                    (app_id, field, value, rid, reason, 1 if len(distinct) > 1 else 0, now()))
        updates[field] = value
    if updates:
        sets = ", ".join(f"{f}=?" for f in updates)
        con.execute(f"UPDATE application SET {sets}, updated_at=? WHERE id=?",
                    (*updates.values(), now(), app_id))
    return updates


def attribute_detail(con, app_id):
    out = []
    for r in rows(con, "SELECT * FROM attribute_resolution WHERE application_id=?", app_id):
        competing = rows(con,
            "SELECT s.name AS source_name, ir." + r["field"] + " AS value"
            " FROM application_source_link l"
            " JOIN inventory_record ir ON ir.id = l.inventory_record_id"
            " JOIN source s ON s.id = ir.source_id"
            " WHERE l.application_id=? AND ir.absent_since IS NULL", app_id)
        win = one(con, "SELECT s.name FROM inventory_record ir JOIN source s"
                       " ON s.id=ir.source_id WHERE ir.id=?", r["winning_record_id"])
        out.append({"field": r["field"], "resolved_value": r["resolved_value"],
                    "winning_source_name": win["name"] if win else None,
                    "reason": r["reason"], "conflict": bool(r["conflict"]),
                    "competing_values": [c for c in competing if c["value"] is not None]})
    return out


# ------------------------------------------------------------ applications

def create_application(con, *, name, origin, created_by, description=None,
                       rationale=None, decision_id=None, record_id=None):
    if origin == "inferred" and not rationale:
        raise Refused("rationale_required",
                      "An inferred application needs a rationale.",
                      detail="It exists in no inventory source, so how it was discovered "
                             "is the only provenance it has.",
                      remedy="Supply rationale, e.g. 'found shipping from Jenkins "
                             "pipeline platform/scanhub with no CMDB record'.")
    app_id, handle = ulid(), next_handle(con)
    register_subject(con, app_id, "application", INTERNAL_SOURCE, f"{handle} {name}")
    t = now()
    con.execute("INSERT INTO application (id, handle, name, description, origin, status,"
                " onboarding_state, created_by, created_at, updated_at)"
                " VALUES (?,?,?,?,?,'active',?,?,?,?)",
                (app_id, handle, name, description, origin,
                 "in_scope" if origin == "federated" else "discovered",
                 created_by, t, t))
    # Every application gets a default managed entity: decomposition is a
    # refinement, never a prerequisite (P5).
    me_id = ulid()
    register_subject(con, me_id, "managed_entity", INTERNAL_SOURCE,
                     f"{name} (whole application)")
    con.execute("INSERT INTO managed_entity (id, application_id, name, kind, rationale,"
                " is_default, created_by, created_at) VALUES (?,?,?,?,?,1,?,?)",
                (me_id, app_id, name, "whole_application",
                 "Default entity spanning the whole application, created automatically. "
                 "Replace or subdivide when the real boundary is decided.",
                 created_by, t))
    audit(con, created_by, "application.created", subject_id=app_id,
          after=f"{handle} {name} ({origin})", rationale=rationale,
          decision_id=decision_id)
    if record_id:
        link_record(con, app_id, record_id, created_by, decision_id=decision_id)
    return app_id


def link_record(con, app_id, record_id, actor, *, origin="federated", decision_id=None):
    if scalar(con, "SELECT 1 FROM application_source_link WHERE application_id=?"
                   " AND inventory_record_id=?", app_id, record_id):
        return
    con.execute("INSERT INTO application_source_link (application_id, inventory_record_id,"
                " origin, decision_id, created_by, created_at) VALUES (?,?,?,?,?,?)",
                (app_id, record_id, origin, decision_id, actor, now()))
    rec = one(con, "SELECT r.name, s.name AS source_name FROM inventory_record r"
                   " JOIN source s ON s.id=r.source_id WHERE r.id=?", record_id)
    audit(con, actor, "application.source_linked", subject_id=app_id,
          after=f"{rec['source_name']}: {rec['name']}", decision_id=decision_id)
    resolve_attributes(con, app_id)


def get_application(con, ref, detail=False):
    app_id = _by_handle(con, ref)
    canonical, resolved_from = resolve(con, app_id)
    a = one(con, "SELECT * FROM application WHERE id=?", canonical)
    if not a:
        raise Refused("application_not_found", f"No application {ref!r}.",
                      subject_id=ref, status=404)
    a["source_count"] = scalar(con, "SELECT COUNT(*) FROM application_source_link"
                                    " WHERE application_id=?", canonical)
    a["managed_entity_count"] = scalar(con, "SELECT COUNT(*) FROM managed_entity"
                                            " WHERE application_id=?", canonical)
    a["repository_count"] = scalar(con, "SELECT COUNT(*) FROM repository_mapping"
                                        " WHERE application_id=?", canonical)
    a["has_attribute_conflict"] = bool(scalar(
        con, "SELECT COUNT(*) FROM attribute_resolution WHERE application_id=? AND conflict=1",
        canonical))
    if not detail:
        return a
    a["resolved_from"] = resolved_from
    a["source_links"] = rows(con,
        "SELECT l.inventory_record_id, r.source_id, s.name AS source_name,"
        " r.external_id, r.absent_since FROM application_source_link l"
        " JOIN inventory_record r ON r.id=l.inventory_record_id"
        " JOIN source s ON s.id=r.source_id WHERE l.application_id=?", canonical)
    a["attribute_resolution"] = attribute_detail(con, canonical)
    a["managed_entities"] = list_entities(con, canonical)
    a["classification"] = rows(con, "SELECT id, axis, value, rationale, set_by, set_at"
                                    " FROM classification_label WHERE application_id=?",
                               canonical)
    a["aliases"] = rows(con, "SELECT alias_id, alias_handle, merged_at"
                             " FROM application_alias WHERE canonical_id=?", canonical)
    return a


def list_applications(con, *, origin=None, status="active", onboarding_state=None,
                      q=None, limit=500):
    sql, args = "SELECT id FROM application WHERE 1=1", []
    if status:
        sql += " AND status=?"; args.append(status)
    if origin:
        sql += " AND origin=?"; args.append(origin)
    if onboarding_state:
        sql += " AND onboarding_state=?"; args.append(onboarding_state)
    if q:
        sql += " AND (name LIKE ? OR handle LIKE ?)"; args += [f"%{q}%", f"%{q}%"]
    return [get_application(con, r["id"])
            for r in rows(con, sql + " ORDER BY handle LIMIT ?", *args, limit)]


def merge_applications(con, keep_ref, absorb_ref, *, rationale, decided_by):
    keep = get_application(con, keep_ref)
    absorb = get_application(con, absorb_ref)
    if keep["id"] == absorb["id"]:
        raise Refused("merge_self", "An application cannot be merged into itself.",
                      subject_id=keep["id"])
    did = ulid()
    con.execute("INSERT INTO decision (id, kind, disposition, rationale, decided_by,"
                " decided_at, confirmed_by_user) VALUES (?,?,'merged',?,?,?,1)",
                (did, "dedupe", rationale, decided_by, now()))
    for s in (keep["id"], absorb["id"]):
        con.execute("INSERT INTO decision_subject (decision_id, subject_id) VALUES (?,?)",
                    (did, s))
    for lk in rows(con, "SELECT inventory_record_id, origin FROM application_source_link"
                        " WHERE application_id=?", absorb["id"]):
        con.execute("DELETE FROM application_source_link WHERE application_id=?"
                    " AND inventory_record_id=?", (absorb["id"], lk["inventory_record_id"]))
        link_record(con, keep["id"], lk["inventory_record_id"], decided_by,
                    origin="dedupe", decision_id=did)
    con.execute("UPDATE repository_mapping SET application_id=? WHERE application_id=?",
                (keep["id"], absorb["id"]))
    con.execute("UPDATE managed_entity SET application_id=? WHERE application_id=?"
                " AND is_default=0", (keep["id"], absorb["id"]))
    con.execute("UPDATE application SET status='merged', updated_at=? WHERE id=?",
                (now(), absorb["id"]))
    con.execute("INSERT INTO application_alias (alias_id, alias_handle, canonical_id,"
                " decision_id, merged_by, merged_at) VALUES (?,?,?,?,?,?)",
                (absorb["id"], absorb["handle"], keep["id"], did, decided_by, now()))
    rename_subject(con, absorb["id"], f"{absorb['handle']} {absorb['name']} (merged)")
    audit(con, decided_by, "application.merged", subject_id=keep["id"],
          before=f"{absorb['handle']} {absorb['name']}",
          after=f"merged into {keep['handle']}", rationale=rationale, decision_id=did)
    resolve_attributes(con, keep["id"])
    con.commit()
    return get_application(con, keep["id"], detail=True)


def classify(con, app_ref, *, axis, value, rationale, set_by):
    app = get_application(con, app_ref)
    cid = ulid()
    con.execute("INSERT INTO classification_label (id, application_id, axis, value,"
                " rationale, set_by, set_at) VALUES (?,?,?,?,?,?,?)"
                " ON CONFLICT(application_id, axis, value) DO UPDATE SET"
                " rationale=excluded.rationale, set_by=excluded.set_by,"
                " set_at=excluded.set_at",
                (cid, app["id"], axis, value, rationale, set_by, now()))
    audit(con, set_by, "application.classified", subject_id=app["id"],
          after=f"{axis} = {value}", rationale=rationale)
    con.commit()
    return one(con, "SELECT id, axis, value, rationale, set_by, set_at"
                    " FROM classification_label WHERE application_id=? AND axis=? AND value=?",
               app["id"], axis, value)


# -------------------------------------------------------- managed entities

def list_entities(con, app_id):
    out = rows(con, "SELECT * FROM managed_entity WHERE application_id=? ORDER BY is_default"
                    " DESC, name", app_id)
    for e in out:
        e["is_default"] = bool(e["is_default"])
        e["artifact_count"] = scalar(con, "SELECT COUNT(*) FROM build_artifact"
                                          " WHERE managed_entity_id=?", e["id"])
        e["repository_count"] = scalar(con, "SELECT COUNT(*) FROM repository_mapping"
                                            " WHERE managed_entity_id=?", e["id"])
    return out


def create_entity(con, app_ref, *, name, rationale, created_by, kind=None,
                  build_artifact_ids=None, repository_ids=None, decision_id=None):
    app = get_application(con, app_ref)
    if not rationale or not rationale.strip():
        raise Refused("rationale_required",
                      "A managed entity needs a written rationale.",
                      detail="It is the record of why these things are managed together, "
                             "and it is the only thing that makes the boundary auditable.",
                      subject_id=app["id"],
                      remedy="Supply rationale, e.g. 'built and released together "
                             "from the monorepo'.")
    eid = ulid()
    register_subject(con, eid, "managed_entity", INTERNAL_SOURCE, f"{app['name']} / {name}")
    con.execute("INSERT INTO managed_entity (id, application_id, name, kind, rationale,"
                " is_default, decision_id, created_by, created_at) VALUES (?,?,?,?,?,0,?,?,?)",
                (eid, app["id"], name, kind, rationale, decision_id, created_by, now()))
    for aid in (build_artifact_ids or []):
        con.execute("UPDATE build_artifact SET managed_entity_id=?, mapping_decision_id=?"
                    " WHERE id=?", (eid, decision_id, aid))
    for rid in (repository_ids or []):
        con.execute("UPDATE repository_mapping SET managed_entity_id=? WHERE repository_id=?"
                    " AND application_id=?", (eid, rid, app["id"]))
    audit(con, created_by, "entity.declared", subject_id=eid,
          after=f"{app['handle']} / {name}", rationale=rationale, decision_id=decision_id)
    _refresh_onboarding(con, app["id"])
    con.commit()
    return one(con, "SELECT * FROM managed_entity WHERE id=?", eid)


def _refresh_onboarding(con, app_id):
    """MATERIALIZED: application.onboarding_state.  Single writer."""
    non_default = scalar(con, "SELECT COUNT(*) FROM managed_entity WHERE application_id=?"
                              " AND is_default=0", app_id)
    mapped = scalar(con, "SELECT COUNT(*) FROM repository_mapping WHERE application_id=?", app_id)
    linked = scalar(con, "SELECT COUNT(*) FROM application_source_link"
                         " WHERE application_id=?", app_id)
    state = "discovered"
    if linked or mapped:
        state = "in_scope"
    if non_default:
        state = "decomposed"
    con.execute("UPDATE application SET onboarding_state=?, updated_at=? WHERE id=?",
                (state, now(), app_id))


# ------------------------------------------------------------- mapping

def map_repository(con, repository_id, app_ref, actor, *, repo_scope_id=None,
                   managed_entity_id=None, decision_id=None):
    app = get_application(con, app_ref)
    existing = one(con, "SELECT id FROM repository_mapping WHERE repository_id=?"
                        " AND application_id=? AND (repo_scope_id IS ? OR repo_scope_id=?)",
                   repository_id, app["id"], repo_scope_id, repo_scope_id)
    if existing:
        return existing["id"]
    mid = ulid()
    con.execute("INSERT INTO repository_mapping (id, repository_id, repo_scope_id,"
                " application_id, managed_entity_id, decision_id, created_by, created_at)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (mid, repository_id, repo_scope_id, app["id"], managed_entity_id,
                 decision_id, actor, now()))
    repo = one(con, "SELECT name FROM repository WHERE id=?", repository_id)
    audit(con, actor, "repository.mapped", subject_id=repository_id,
          after=f"{repo['name']} -> {app['handle']} {app['name']}", decision_id=decision_id)
    _refresh_onboarding(con, app["id"])
    return mid


def govern_artifact(con, artifact_id, managed_entity_id, actor, *, decision_id=None):
    art = one(con, "SELECT coordinate FROM build_artifact WHERE id=?", artifact_id)
    ent = one(con, "SELECT m.name, a.handle FROM managed_entity m"
                   " JOIN application a ON a.id=m.application_id WHERE m.id=?",
              managed_entity_id)
    if not art or not ent:
        raise Refused("not_found", "Artifact or managed entity not found.",
                      subject_id=artifact_id, status=404)
    con.execute("UPDATE build_artifact SET managed_entity_id=?, mapping_decision_id=?"
                " WHERE id=?", (managed_entity_id, decision_id, artifact_id))
    audit(con, actor, "artifact.governed", subject_id=artifact_id,
          after=f"{art['coordinate']} -> {ent['handle']} / {ent['name']}",
          decision_id=decision_id)


def add_scope(con, repository_id, *, path_prefix, name, created_by):
    repo = one(con, "SELECT source_id, name FROM repository WHERE id=?", repository_id)
    if not repo:
        raise Refused("repository_not_found", "No such repository.",
                      subject_id=repository_id, status=404)
    sid = ulid()
    register_subject(con, sid, "repo_scope", repo["source_id"],
                     f"{repo['name']}{path_prefix}")
    con.execute("INSERT INTO repo_scope (id, repository_id, path_prefix, name, created_by,"
                " created_at) VALUES (?,?,?,?,?,?)",
                (sid, repository_id, path_prefix, name, created_by, now()))
    audit(con, created_by, "repository.scoped", subject_id=repository_id,
          after=f"{repo['name']}{path_prefix} ({name})")
    con.commit()
    return one(con, "SELECT * FROM repo_scope WHERE id=?", sid)


def list_repositories(con, *, source_id=None, mapped=None, archived=None, q=None, limit=500):
    sql = ("SELECT r.*, s.name AS source_name,"
           " (SELECT COUNT(*) FROM repository_mapping m WHERE m.repository_id=r.id) AS nmap,"
           " (SELECT COUNT(*) FROM repo_scope sc WHERE sc.repository_id=r.id) AS scope_count"
           " FROM repository r JOIN source s ON s.id=r.source_id WHERE 1=1")
    args = []
    if source_id:
        sql += " AND r.source_id=?"; args.append(source_id)
    if archived is not None:
        sql += " AND r.archived=?"; args.append(1 if archived else 0)
    if q:
        sql += " AND r.name LIKE ?"; args.append(f"%{q}%")
    out = []
    for r in rows(con, sql + " ORDER BY r.name LIMIT ?", *args, limit):
        r["mapped"] = r.pop("nmap") > 0
        r["archived"] = bool(r["archived"])
        if mapped is not None and r["mapped"] != mapped:
            continue
        link = one(con, "SELECT a.id, a.handle FROM repository_mapping m"
                        " JOIN application a ON a.id=m.application_id"
                        " WHERE m.repository_id=? LIMIT 1", r["id"])
        r["application_id"] = link["id"] if link else None
        r["application_handle"] = link["handle"] if link else None
        out.append(r)
    return out


def list_artifacts(con, *, governed=None, managed_entity_id=None, limit=500):
    sql = ("SELECT a.*, s.name AS source_name, m.name AS managed_entity_name,"
           " app.handle AS application_handle FROM build_artifact a"
           " JOIN source s ON s.id=a.source_id"
           " LEFT JOIN managed_entity m ON m.id=a.managed_entity_id"
           " LEFT JOIN application app ON app.id=m.application_id WHERE 1=1")
    args = []
    if managed_entity_id:
        sql += " AND a.managed_entity_id=?"; args.append(managed_entity_id)
    if governed is True:
        sql += " AND a.managed_entity_id IS NOT NULL"
    elif governed is False:
        sql += " AND a.managed_entity_id IS NULL"
    out = rows(con, sql + " ORDER BY a.coordinate LIMIT ?", *args, limit)
    for a in out:
        a["governed"] = a["managed_entity_id"] is not None
    return out
