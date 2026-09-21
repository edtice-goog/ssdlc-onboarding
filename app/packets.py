"""Decision packets: the export -> decide -> diff -> apply round trip (§4).

Judgment over long lists is delegated to a Claude session, not encoded as rules
(P2).  We build the candidate list, a person reasons over it with an LLM, and
hands the result back.  One mechanism serves scope selection, repository
mapping, decomposition and dedupe.

The export is the product surface: the quality of the judgment that comes back
is bounded by what we put in the packet.
"""

import csv
import io
import json
import pathlib
import re

from .core import Refused, audit, now, one, rows, scalar, ulid
from . import registry

ARCHIVE = pathlib.Path("archive")

SCOPE_WORDS = {
    "manage": "manage", "managed": "manage", "in scope": "manage", "in-scope": "manage",
    "yes": "manage", "y": "manage", "true": "manage", "include": "manage",
    "reject": "reject", "rejected": "reject", "out of scope": "reject",
    "out-of-scope": "reject", "no": "reject", "n": "reject", "false": "reject",
    "exclude": "reject", "skip": "reject",
    "defer": "defer", "deferred": "defer", "later": "defer", "unknown": "defer",
}


# ------------------------------------------------------------------ build

def _inventory_candidates(con, source_id, flt):
    sql = ("SELECT r.id, r.external_id, r.name, r.description, r.record_class,"
           " r.lifecycle_state, r.criticality, r.business_owner, r.engineering_owner,"
           " r.content_hash, r.scope_status, r.scope_stale FROM inventory_record r"
           " WHERE r.source_id=? AND r.absent_since IS NULL")
    args = [source_id]
    if flt.get("record_class"):
        sql += " AND r.record_class=?"; args.append(flt["record_class"])
    if flt.get("scope_status"):
        sql += " AND r.scope_status=?"; args.append(flt["scope_status"])
    elif not flt.get("include_decided"):
        sql += " AND (r.scope_status='undecided' OR r.scope_stale=1)"
    out = []
    for r in rows(con, sql + " ORDER BY r.name", *args):
        cur = one(con, "SELECT d.disposition, d.rationale FROM decision_subject ds"
                       " JOIN decision d ON d.id=ds.decision_id"
                       " WHERE ds.subject_id=? AND ds.status IN ('current','stale')"
                       " ORDER BY d.decided_at DESC LIMIT 1", r["id"])
        changed = []
        if r["scope_stale"]:
            op = one(con, "SELECT changed_fields FROM sync_op WHERE subject_id=?"
                          " AND change_class='material' ORDER BY rowid DESC LIMIT 1", r["id"])
            changed = (op["changed_fields"] or "").split(",") if op and op["changed_fields"] else []
        out.append({
            "record_key": r["external_id"], "subject_id": r["id"], "name": r["name"],
            "description": r["description"], "record_class": r["record_class"],
            "lifecycle_state": r["lifecycle_state"], "criticality": r["criticality"],
            "business_owner": r["business_owner"], "engineering_owner": r["engineering_owner"],
            "current_decision": cur["disposition"] if cur else None,
            "current_rationale": cur["rationale"] if cur else None,
            "stale": bool(r["scope_stale"]), "changed_since_decision": changed,
            "content_hash": r["content_hash"],
        })
    return out


def _repository_candidates(con, source_id, flt):
    out = []
    for r in registry.list_repositories(con, source_id=source_id,
                                        mapped=False if flt.get("unmapped_only", True) else None):
        out.append({
            "record_key": r["external_id"], "subject_id": r["id"], "name": r["name"],
            "description": f"{r['primary_language'] or 'unknown language'}; "
                           f"last commit {r['last_commit_at'] or 'unknown'}"
                           + (", archived" if r["archived"] else ""),
            "record_class": "repository", "lifecycle_state": None,
            "criticality": None, "business_owner": None,
            "engineering_owner": None, "current_decision": None,
            "current_rationale": None, "stale": False, "changed_since_decision": [],
            "content_hash": r["content_hash"], "url": r["url"],
        })
    return out


def _artifact_candidates(con, source_id, flt):
    out = []
    for a in registry.list_artifacts(con, governed=False):
        if a["source_id"] != source_id:
            continue
        out.append({
            "record_key": a["external_id"], "subject_id": a["id"],
            "name": a["coordinate"],
            "description": f"{a['artifact_type'] or 'artifact'} from pipeline "
                           f"{a['pipeline_ref'] or 'unknown'}",
            "record_class": "build_artifact", "lifecycle_state": None,
            "criticality": None, "business_owner": None, "engineering_owner": None,
            "current_decision": None, "current_rationale": None,
            "stale": False, "changed_since_decision": [], "content_hash": a["content_hash"],
        })
    return out


BUILDERS = {"inventory_record": _inventory_candidates,
            "repository": _repository_candidates,
            "build_artifact": _artifact_candidates}


PROMPT_BY_TYPE = {
    "inventory_record": """You are helping decide which records from a configuration
management database need to be tracked for secure-development-lifecycle purposes.

For each row below, decide one of:

  manage   this is an application whose SSDLC we need to track
  reject   not an application, or not ours to govern
  defer    cannot tell from this information

Give a rationale for each. **Where several rows share the same reasoning, use the
same wording** - identical rationales are grouped into one recorded decision, which
is how the audit trail stays readable.""",

    "repository": """You are helping decide which application each source repository
belongs to.

For each repository below, give the application it belongs to, or `none` if it
belongs to no application we track (infrastructure, experiments, documentation,
scratch work). Use the exact application handle (APP-0001) or name where you are
confident.

Give a rationale for each, and **reuse identical wording where the reasoning is
the same** - that groups them into a single recorded decision.""",

    "build_artifact": """You are helping decide which managed entity governs each
build artifact.

Every artifact that ships should be governed by exactly one managed entity. For
each artifact below give the application handle and managed entity name, or
`none` if nothing governs it yet.""",
}

RETURN_CONTRACT = """
Return your answer as a markdown table with exactly these columns, and change
nothing in the record_key column -- it is how the decisions are matched back:

| record_key | decision | rationale |

CSV or JSON with the same three fields is equally acceptable. Rows you leave out
are treated as undecided, not as rejected.
"""


def create_packet(con, *, subject_type, purpose, created_by, source_id=None,
                  filter=None, suggested_rules=None):
    if subject_type not in BUILDERS:
        raise Refused("bad_subject_type", f"Cannot build a packet for {subject_type!r}.",
                      remedy=f"Use one of: {', '.join(BUILDERS)}.")
    candidates = BUILDERS[subject_type](con, source_id, filter or {})
    pid = ulid()
    src = one(con, "SELECT name, kind FROM source WHERE id=?", source_id) if source_id else None

    head = [PROMPT_BY_TYPE[subject_type], ""]
    if src:
        head.append(f"Source: {src['name']} ({src['kind']}).")
    head.append(f"Task: {purpose}")
    if suggested_rules:
        head += ["", "Suggested starting points (these are hints in prose, not rules "
                     "the system evaluates -- disagree with them where the data warrants):"]
        head += [f"  - {r}" for r in suggested_rules]
    head += ["", RETURN_CONTRACT.strip(), "", "---", "", _as_markdown(candidates)]
    prompt = "\n".join(head)

    ARCHIVE.mkdir(parents=True, exist_ok=True)
    snap = ARCHIVE / f"packet-{pid}-input.json"
    snap.write_text(json.dumps(candidates, indent=2), encoding="utf-8")

    con.execute("INSERT INTO decision_packet (id, subject_type, source_id, purpose,"
                " prompt_text, input_snapshot_ref, row_count, status, created_by, created_at)"
                " VALUES (?,?,?,?,?,?,?,'open',?,?)",
                (pid, subject_type, source_id, purpose, prompt, str(snap),
                 len(candidates), created_by, now()))
    audit(con, created_by, "packet.created", packet_id=pid, source_id=source_id,
          after=f"{len(candidates)} {subject_type} candidates", rationale=purpose)
    con.commit()
    return get_packet(con, pid)


def get_packet(con, pid, with_rows=True):
    p = one(con, "SELECT * FROM decision_packet WHERE id=?", pid)
    if not p:
        raise Refused("packet_not_found", f"No decision packet {pid!r}.", status=404)
    if with_rows and p["input_snapshot_ref"]:
        try:
            p["rows"] = json.loads(pathlib.Path(p["input_snapshot_ref"]).read_text(encoding="utf-8"))
        except OSError:
            p["rows"] = []
    return p


def _as_markdown(cands):
    if not cands:
        return "_(no candidates)_"
    cols = ["record_key", "name", "record_class", "lifecycle_state", "criticality",
            "business_owner", "engineering_owner", "description",
            "current_decision", "stale"]
    present = [c for c in cols
               if any(r.get(c) not in (None, "", False, []) for r in cands)]
    out = ["| " + " | ".join(present) + " |",
           "|" + "|".join("---" for _ in present) + "|"]
    for r in cands:
        cells = []
        for c in present:
            v = r.get(c)
            v = "" if v in (None, False) else ("yes" if v is True else str(v))
            cells.append(v.replace("|", "\\|").replace("\n", " ")[:160])
        out.append("| " + " | ".join(cells) + " |")
    return "\n".join(out)


def export(con, pid, fmt="markdown"):
    p = get_packet(con, pid)
    if fmt == "json":
        return json.dumps({"packet_id": pid, "purpose": p["purpose"],
                           "prompt": p["prompt_text"], "rows": p["rows"]}, indent=2)
    if fmt == "csv":
        buf = io.StringIO()
        if p["rows"]:
            keys = [k for k in p["rows"][0] if k != "subject_id"]
            w = csv.DictWriter(buf, fieldnames=keys, extrasaction="ignore")
            w.writeheader()
            for r in p["rows"]:
                w.writerow({k: r.get(k) for k in keys})
        return buf.getvalue()
    return p["prompt_text"]


# ----------------------------------------------------------------- return

def parse_return(text, valid_keys):
    """Tolerant by design (P8): JSON, CSV, a markdown table, or pasted prose.

    The only hard requirement is that record_key survives unchanged.
    """
    text = (text or "").strip()
    if not text:
        return [], []
    parsed = _try_json(text) or _try_table(text, "|") or _try_csv(text) or _try_prose(text, valid_keys)
    out, unknown = [], []
    for row in parsed or []:
        key = (row.get("record_key") or "").strip()
        if key not in valid_keys:
            if key:
                unknown.append(key)
            continue
        dec = (row.get("decision") or "").strip()
        out.append({"record_key": key, "decision": dec,
                    "rationale": (row.get("rationale") or "").strip()})
    return out, unknown


def _norm(d):
    return {(k or "").strip().lower().replace(" ", "_"): (v if v is not None else "")
            for k, v in d.items()}


def _try_json(text):
    try:
        data = json.loads(text)
    except ValueError:
        return None
    if isinstance(data, dict):
        data = data.get("decisions") or data.get("rows") or data.get("items") or []
    return [_norm(d) for d in data if isinstance(d, dict)] or None


def _try_table(text, sep):
    lines = [l.strip() for l in text.splitlines() if sep in l]
    if len(lines) < 2:
        return None
    def cells(l):
        return [c.strip() for c in l.strip().strip("|").split("|")]
    header = [h.strip().lower().replace(" ", "_") for h in cells(lines[0])]
    if "record_key" not in header:
        return None
    out = []
    for l in lines[1:]:
        c = cells(l)
        if all(set(x) <= set("-: ") for x in c):
            continue
        out.append(_norm(dict(zip(header, c))))
    return out or None


def _try_csv(text):
    try:
        r = csv.DictReader(io.StringIO(text))
        rowlist = [_norm(d) for d in r]
    except Exception:
        return None
    if rowlist and "record_key" in rowlist[0]:
        return rowlist
    return None


def _try_prose(text, valid_keys):
    """Last resort: find a known key on a line and take the rest as the answer."""
    out = []
    for line in text.splitlines():
        for k in valid_keys:
            if k in line:
                rest = line.split(k, 1)[1].strip(" :\t---")
                word = next((w for w in SCOPE_WORDS
                             if re.search(rf"\b{re.escape(w)}\b", rest, re.I)), "")
                out.append({"record_key": k,
                            "decision": SCOPE_WORDS.get(word, rest.split()[0] if rest else ""),
                            "rationale": rest})
                break
    return out or None


def submit_return(con, pid, returned_raw, actor="unknown"):
    p = get_packet(con, pid)
    ARCHIVE.mkdir(parents=True, exist_ok=True)
    path = ARCHIVE / f"packet-{pid}-return.txt"
    path.write_text(returned_raw, encoding="utf-8")
    con.execute("UPDATE decision_packet SET returned_raw_ref=?, status='returned' WHERE id=?",
                (str(path), pid))
    audit(con, actor, "packet.returned", packet_id=pid,
          after=f"{len(returned_raw)} bytes archived verbatim")
    con.commit()
    return diff(con, pid)


def _interpret(subject_type, raw):
    v = (raw or "").strip()
    if subject_type == "inventory_record":
        return SCOPE_WORDS.get(v.lower(), None)
    if v.lower() in ("none", "no", "-", "unmapped", "n/a"):
        return "unmapped"
    return v


def diff(con, pid):
    p = get_packet(con, pid)
    by_key = {r["record_key"]: r for r in p["rows"]}
    raw = ""
    if p["returned_raw_ref"]:
        try:
            raw = pathlib.Path(p["returned_raw_ref"]).read_text(encoding="utf-8")
        except OSError:
            raw = ""
    parsed, unknown = parse_return(raw, set(by_key))
    changes, groups = [], {}
    for row in parsed:
        cand = by_key[row["record_key"]]
        target = _interpret(p["subject_type"], row["decision"])
        if target is None:
            unknown.append(row["record_key"]); continue
        frm = cand.get("current_decision")
        changes.append({"record_key": row["record_key"], "name": cand["name"],
                        "from": frm, "to": target, "rationale": row["rationale"],
                        "is_change": frm != target or bool(cand.get("stale"))})
        g = groups.setdefault((target, row["rationale"]),
                              {"disposition": target, "rationale": row["rationale"],
                               "subject_count": 0})
        g["subject_count"] += 1
    decided = {c["record_key"] for c in changes}
    return {"packet_id": pid, "parsed_rows": len(parsed),
            "unrecognized_keys": sorted(set(unknown)),
            "undecided_keys": sorted(set(by_key) - decided),
            "changes": changes, "rationale_groups": list(groups.values())}


# ------------------------------------------------------------------ apply

def apply_packet(con, pid, applied_by, only_record_keys=None):
    p = get_packet(con, pid)
    d = diff(con, pid)
    by_key = {r["record_key"]: r for r in p["rows"]}
    wanted = set(only_record_keys) if only_record_keys else None

    groups: dict[tuple, list] = {}
    for c in d["changes"]:
        if wanted and c["record_key"] not in wanted:
            continue
        groups.setdefault((c["to"], c["rationale"]), []).append(c)

    kind = {"inventory_record": "scope", "repository": "mapping",
            "build_artifact": "mapping"}[p["subject_type"]]
    created, apps_created, subjects = [], 0, 0

    for (target, rationale), members in groups.items():
        disp = target if kind == "scope" else ("mapped" if target != "unmapped" else "unmapped")
        did = ulid()
        con.execute("INSERT INTO decision (id, kind, disposition, rationale, decided_by,"
                    " decided_at, decision_packet_id, confirmed_by_user)"
                    " VALUES (?,?,?,?,?,?,?,1)",
                    (did, kind, disp, rationale, applied_by, now(), pid))
        created.append(did)
        for c in members:
            cand = by_key[c["record_key"]]
            sid = cand["subject_id"]
            con.execute("UPDATE decision_subject SET status='superseded'"
                        " WHERE subject_id=? AND status IN ('current','stale')", (sid,))
            con.execute("INSERT INTO decision_subject (decision_id, subject_id,"
                        " decided_against_hash, status) VALUES (?,?,?,'current')",
                        (did, sid, cand.get("content_hash")))
            subjects += 1
            if kind == "scope":
                con.execute("UPDATE inventory_record SET scope_status=?, scope_decision_id=?,"
                            " scope_stale=0 WHERE id=?", (target, did, sid))
                if target == "manage" and not scalar(
                        con, "SELECT 1 FROM application_source_link"
                             " WHERE inventory_record_id=?", sid):
                    registry.create_application(
                        con, name=cand["name"], origin="federated",
                        created_by=applied_by, description=cand.get("description"),
                        decision_id=did, record_id=sid)
                    apps_created += 1
                audit(con, applied_by, f"record.{target}", subject_id=sid,
                      rationale=rationale, decision_id=did, packet_id=pid)
            elif p["subject_type"] == "repository":
                if target == "unmapped":
                    con.execute("INSERT INTO orphan_disposition (id, subject_id, status,"
                                " reason, decision_id, set_by, set_at)"
                                " VALUES (?,?,'ignored',?,?,?,?)"
                                " ON CONFLICT(subject_id) DO UPDATE SET status='ignored',"
                                " reason=excluded.reason, decision_id=excluded.decision_id",
                                (ulid(), sid, rationale, did, applied_by, now()))
                else:
                    try:
                        registry.map_repository(con, sid, target, applied_by, decision_id=did)
                    except Refused:
                        con.execute("UPDATE decision_subject SET status='stale',"
                                    " went_stale_at=? WHERE decision_id=? AND subject_id=?",
                                    (now(), did, sid))
            else:  # build_artifact
                if target != "unmapped":
                    ent = _find_entity(con, target)
                    if ent:
                        registry.govern_artifact(con, sid, ent, applied_by, decision_id=did)

    con.execute("UPDATE decision_packet SET status='applied', applied_by=?, applied_at=?"
                " WHERE id=?", (applied_by, now(), pid))
    audit(con, applied_by, "packet.applied", packet_id=pid,
          after=f"{len(created)} decisions over {subjects} subjects")
    con.commit()
    return {"packet_id": pid, "decisions_created": len(created),
            "subjects_affected": subjects, "applications_created": apps_created,
            "decision_ids": created}


def _find_entity(con, ref):
    r = one(con, "SELECT m.id FROM managed_entity m JOIN application a"
                 " ON a.id=m.application_id WHERE a.handle=? OR a.name=? OR m.name=?"
                 " ORDER BY m.is_default LIMIT 1", ref, ref, ref)
    return r["id"] if r else None


def apply_inline_decisions(con, source_id, decisions, actor):
    """Decisions made inside a skill session and handed back with the changeset.

    confirmed_by_user means a named person decided it there; that is the human
    review, and it applies directly (§5.2).
    """
    applied = queued = 0
    groups: dict[tuple, list] = {}
    for d in decisions:
        rec = one(con, "SELECT id, name, content_hash FROM inventory_record"
                       " WHERE source_id=? AND external_id=?", source_id, d["key"])
        if not rec:
            continue
        if not d.get("confirmed_by_user"):
            queued += 1
            continue
        target = SCOPE_WORDS.get((d.get("decision") or "").lower())
        if not target:
            queued += 1
            continue
        groups.setdefault((target, d.get("rationale", "")), []).append((rec, d))
    for (target, rationale), members in groups.items():
        did = ulid()
        con.execute("INSERT INTO decision (id, kind, disposition, rationale, decided_by,"
                    " decided_at, confirmed_by_user) VALUES (?,?,?,?,?,?,1)",
                    (did, "scope", target, rationale,
                     members[0][1].get("decided_by", actor), now()))
        for rec, d in members:
            con.execute("UPDATE decision_subject SET status='superseded'"
                        " WHERE subject_id=? AND status IN ('current','stale')", (rec["id"],))
            con.execute("INSERT INTO decision_subject (decision_id, subject_id,"
                        " decided_against_hash, status) VALUES (?,?,?,'current')",
                        (did, rec["id"], rec["content_hash"]))
            con.execute("UPDATE inventory_record SET scope_status=?, scope_decision_id=?,"
                        " scope_stale=0 WHERE id=?", (target, did, rec["id"]))
            if target == "manage" and not scalar(
                    con, "SELECT 1 FROM application_source_link WHERE inventory_record_id=?",
                    rec["id"]):
                registry.create_application(con, name=rec["name"], origin="federated",
                                            created_by=actor, decision_id=did,
                                            record_id=rec["id"])
            applied += 1
    con.commit()
    return {"decisions_applied": applied, "decisions_queued": queued}
