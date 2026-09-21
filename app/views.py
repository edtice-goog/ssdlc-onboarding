"""Coverage, orphans, decisions, lineage (DESIGN.md §6, §7).

Three states stay distinguishable throughout: **observed-low**, **unknown**, and
**dispositioned**. A gap we have never looked at is not the same as one we looked
at and accepted.
"""

from .core import Refused, days_since, now, one, rows, scalar, ulid, audit

ORPHAN_VIEW = {
    "build_artifact": ("v_orphan_build_artifact", "something ships that nothing governs"),
    "repository": ("v_orphan_repository", "code with no declared home"),
    "application": ("v_orphan_application", "inventory drift, or retirement"),
    "inferred_application": ("v_inferred_application", "a detected inventory omission"),
}


def coverage(con):
    def c(sql, *a):
        return scalar(con, sql, *a) or 0
    apps = {
        "total": c("SELECT COUNT(*) FROM application WHERE status='active'"),
        "by_origin": {o: c("SELECT COUNT(*) FROM application WHERE status='active'"
                           " AND origin=?", o)
                      for o in ("federated", "manual", "inferred")},
        "by_onboarding_state": {r["onboarding_state"]: r["application_count"]
                                for r in rows(con, "SELECT * FROM v_onboarding_funnel")},
    }
    inv = {
        "records_total": c("SELECT COUNT(*) FROM inventory_record WHERE absent_since IS NULL"),
        "undecided": c("SELECT COUNT(*) FROM inventory_record WHERE scope_status='undecided'"
                       " AND absent_since IS NULL"),
        "managed": c("SELECT COUNT(*) FROM inventory_record WHERE scope_status='manage'"),
        "rejected": c("SELECT COUNT(*) FROM inventory_record WHERE scope_status='reject'"),
        "deferred": c("SELECT COUNT(*) FROM inventory_record WHERE scope_status='defer'"),
        "stale_decisions": c("SELECT COUNT(*) FROM decision_subject WHERE status='stale'"),
        "absent": c("SELECT COUNT(*) FROM inventory_record WHERE absent_since IS NOT NULL"),
    }
    repo_total = c("SELECT COUNT(*) FROM repository WHERE absent_since IS NULL")
    unmapped = c("SELECT COUNT(*) FROM v_orphan_repository")
    art_total = c("SELECT COUNT(*) FROM build_artifact WHERE absent_since IS NULL")
    ungoverned = c("SELECT COUNT(*) FROM v_orphan_build_artifact")
    srcs = []
    for s in rows(con, "SELECT * FROM v_source_freshness ORDER BY role, name"):
        s["staleness_days"] = days_since(s["last_sync_at"])
        srcs.append(s)
    return {
        "applications": apps,
        "inventory": inv,
        "code": {"repositories_total": repo_total,
                 "mapped": repo_total - unmapped,
                 "unmapped": unmapped,
                 "unmapped_dispositioned": c(
                     "SELECT COUNT(*) FROM v_orphan_repository WHERE disposition IS NOT NULL")},
        "build": {"artifacts_total": art_total,
                  "governed": art_total - ungoverned,
                  "ungoverned": ungoverned,
                  "ungoverned_dispositioned": c(
                      "SELECT COUNT(*) FROM v_orphan_build_artifact"
                      " WHERE disposition IS NOT NULL")},
        "sources": srcs,
    }


def orphans(con, kind, include_dispositioned=False, limit=500):
    if kind not in ORPHAN_VIEW:
        raise Refused("unknown_orphan_type", f"No orphan view {kind!r}.",
                      remedy=f"Use one of: {', '.join(ORPHAN_VIEW)}.")
    view, meaning = ORPHAN_VIEW[kind]
    out = []
    for r in rows(con, f"SELECT * FROM {view} LIMIT ?", limit):
        disp = None
        if "disposition" in r and r.get("disposition"):
            disp = {"status": r["disposition"], "reason": r.get("disposition_reason")}
        if disp and not include_dispositioned:
            continue
        out.append({
            "subject_id": r["subject_id"],
            "subject_type": kind if kind != "inferred_application" else "application",
            "display_name": r.get("name") or r.get("handle"),
            "detail": r.get("url") or r.get("pipeline_ref") or r.get("artifact_type"),
            "source_name": r.get("source_name"),
            "last_seen": r.get("last_seen") or r.get("last_commit_at"),
            "meaning": meaning,
            "disposition": disp,
        })
    return out


def dispose_orphan(con, subject_id, *, status, reason, set_by, until=None):
    if not scalar(con, "SELECT 1 FROM subject WHERE id=?", subject_id):
        raise Refused("subject_not_found", "No such subject.", subject_id=subject_id,
                      status=404)
    con.execute("INSERT INTO orphan_disposition (id, subject_id, status, reason, until,"
                " set_by, set_at) VALUES (?,?,?,?,?,?,?)"
                " ON CONFLICT(subject_id) DO UPDATE SET status=excluded.status,"
                " reason=excluded.reason, until=excluded.until, set_by=excluded.set_by,"
                " set_at=excluded.set_at",
                (ulid(), subject_id, status, reason, until, set_by, now()))
    audit(con, set_by, f"orphan.{status}", subject_id=subject_id, rationale=reason)
    con.commit()
    return one(con, "SELECT status, reason, until, set_by, set_at FROM orphan_disposition"
                    " WHERE subject_id=?", subject_id)


# -------------------------------------------------------------- decisions

def list_decisions(con, *, kind=None, decided_by=None, limit=200):
    sql, args = "SELECT * FROM decision WHERE 1=1", []
    if kind:
        sql += " AND kind=?"; args.append(kind)
    if decided_by:
        sql += " AND decided_by=?"; args.append(decided_by)
    out = []
    for d in rows(con, sql + " ORDER BY decided_at DESC LIMIT ?", *args, limit):
        d["confirmed_by_user"] = bool(d["confirmed_by_user"])
        d["subject_count"] = scalar(con, "SELECT COUNT(*) FROM decision_subject"
                                         " WHERE decision_id=?", d["id"])
        d["stale_subject_count"] = scalar(con, "SELECT COUNT(*) FROM decision_subject"
                                               " WHERE decision_id=? AND status='stale'",
                                          d["id"])
        out.append(d)
    return out


def get_decision(con, did):
    d = one(con, "SELECT * FROM decision WHERE id=?", did)
    if not d:
        raise Refused("decision_not_found", f"No decision {did!r}.", status=404)
    d["confirmed_by_user"] = bool(d["confirmed_by_user"])
    d["subjects"] = rows(con,
        "SELECT ds.subject_id, s.subject_type, s.display_name, src.name AS source_name,"
        " src.role AS source_role, ds.status, ds.decided_against_hash, ds.went_stale_at"
        " FROM decision_subject ds JOIN subject s ON s.id=ds.subject_id"
        " JOIN source src ON src.id=s.source_id WHERE ds.decision_id=?"
        " ORDER BY s.display_name", did)
    d["subject_count"] = len(d["subjects"])
    d["stale_subject_count"] = sum(1 for s in d["subjects"] if s["status"] == "stale")
    return d


def stale_subjects(con, subject_type=None, limit=200):
    sql, args = "SELECT * FROM v_stale_decision_subject", []
    if subject_type:
        sql += " WHERE subject_type=?"; args.append(subject_type)
    out = rows(con, sql + " ORDER BY went_stale_at DESC LIMIT ?", *args, limit)
    for r in out:
        op = one(con, "SELECT changed_fields FROM sync_op WHERE subject_id=?"
                      " AND change_class='material' ORDER BY rowid DESC LIMIT 1",
                 r["subject_id"])
        r["changed_fields"] = ((op["changed_fields"] or "").split(",")
                               if op and op["changed_fields"] else [])
    return out


def reaffirm(con, did, subject_ids, decided_by, note=None):
    d = get_decision(con, did)
    for sid in subject_ids:
        h = scalar(con, "SELECT content_hash FROM inventory_record WHERE id=?", sid)
        con.execute("UPDATE decision_subject SET status='current', went_stale_at=NULL,"
                    " decided_against_hash=? WHERE decision_id=? AND subject_id=?",
                    (h, did, sid))
        con.execute("UPDATE inventory_record SET scope_stale=0 WHERE id=?", (sid,))
        audit(con, decided_by, "decision.reaffirmed", subject_id=sid, decision_id=did,
              rationale=note or "Re-affirmed against current content; reasoning unchanged.")
    con.commit()
    return get_decision(con, did)


# ---------------------------------------------------------------- lineage

def lineage(con, app_ref):
    from . import registry
    app = registry.get_application(con, app_ref, detail=True)
    ids = [app["id"]] + [lk["inventory_record_id"] for lk in app["source_links"]]
    marks = ",".join("?" for _ in ids)
    events = rows(con,
        f"SELECT a.rowid AS seq, a.*, s.display_name FROM audit_event a"
        f" LEFT JOIN subject s ON s.id=a.subject_id"
        f" WHERE a.subject_id IN ({marks}) ORDER BY a.at, a.rowid", *ids)

    parts = []
    for e in events:
        d = e["at"][:10]
        act, who = e["action"], e["actor"]
        if act == "application.created":
            origin = (e["after_text"] or "").rsplit("(", 1)[-1].rstrip(")")
            if origin == "inferred":
                parts.append(f"created {d} by {who} as an inferred application"
                             f" - {e['rationale'] or 'no rationale recorded'}")
            elif origin == "manual":
                parts.append(f"entered manually {d} by {who}; in no inventory source")
            else:
                parts.append(f"created {d} by {who} from an in-scope inventory record")
        elif act == "application.source_linked":
            parts.append(f"linked to {e['after_text']} on {d}")
        elif act == "record.manage":
            pk = f" via decision packet {e['decision_packet_id'][:8]}" if e["decision_packet_id"] else ""
            parts.append(f"brought in scope {d} by {who}{pk}"
                         f" - \"{(e['rationale'] or '').strip()}\"")
        elif act in ("record.reject", "record.defer"):
            parts.append(f"{act.split('.')[1]}ed {d} by {who}"
                         f" - \"{(e['rationale'] or '').strip()}\"")
        elif act == "entity.declared":
            ent = (e["after_text"] or "").split("/")[-1].strip()
            parts.append(f"managed entity '{ent}' declared {d} by {who}")
        elif act == "repository.mapped":
            parts.append(f"repository mapped {d} by {who} ({e['after_text']})")
        elif act == "application.merged":
            parts.append(f"absorbed {e['before_text']} on {d} by {who}")
        elif act == "application.classified":
            parts.append(f"classified {e['after_text']} on {d} by {who}")
        elif act == "decision.reaffirmed":
            parts.append(f"decision re-affirmed {d} by {who}")
    narrative = f"{app['handle']} - " + ("; ".join(parts) if parts else "no recorded history") + "."
    return {"application_id": app["id"], "handle": app["handle"], "name": app["name"],
            "narrative": narrative, "events": events}


def audit_log(con, *, subject_id=None, subject_type=None, actor=None, since=None,
              decision_id=None, packet_id=None, limit=200):
    sql = ("SELECT a.*, s.subject_type, s.display_name FROM audit_event a"
           " LEFT JOIN subject s ON s.id=a.subject_id WHERE 1=1")
    args = []
    for col, val in (("a.subject_id", subject_id), ("s.subject_type", subject_type),
                     ("a.actor", actor), ("a.decision_id", decision_id),
                     ("a.decision_packet_id", packet_id)):
        if val:
            sql += f" AND {col}=?"; args.append(val)
    if since:
        sql += " AND a.at >= ?"; args.append(since)
    return rows(con, sql + " ORDER BY a.at DESC, a.rowid DESC LIMIT ?", *args, limit)
