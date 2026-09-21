"""The demo workflow, as discrete resumable steps.

Demo scaffolding, not product.  Kept in its own module and exposed by the API
only when the server is started with --demo, so it cannot be mistaken for part
of the contract.

Each step declares:

    run(con)   perform it, return facts worth showing
    done(con)  whether it has already happened -- derived from the data itself,
               so there is no progress table and no state to get out of sync.
               Re-running the demo against a partly-populated database works.

`seed.py` runs every step in order and prints a narrative; the UI runs them one
at a time so an audience can watch the application fill up.
"""

import csv
import json
import pathlib

from app import ingest, packets, registry, views
from app.core import one, rows, scalar

FX = pathlib.Path(__file__).parent.parent / "mocks" / "fixtures"
JOE, SARAH, PAT = "joe@meridian.example", "sarah@meridian.example", "pat@meridian.example"

NOTES_RATIONALE = "Lotus Notes estate, replacement in flight, out of scope until retirement"
TIER3_RATIONALE = ("Tier 3 utility application, below the SSDLC threshold agreed with "
                   "security leadership")


def load(n):
    return json.loads((FX / n).read_text(encoding="utf-8"))


def src(con, name):
    return one(con, "SELECT * FROM source WHERE name=?", name)


def payload(ops, actor, method="api", **kw):
    return {"contract_version": "1.0", "skill_version": "onboarding-skill/0.3",
            "actor": actor, "method": method, "ops": ops, **kw}


# ------------------------------------------------------------ op builders

def sn_ops(rows_, ci_class):
    return [{"op": "add", "key": r["sys_id"], "name": r["name"],
             "description": r["short_description"], "record_class": r["sys_class_name"],
             "lifecycle_state": r["operational_status"], "criticality": r["u_criticality"],
             "business_owner": r["owned_by"], "engineering_owner": r["support_group"],
             "attributes": [{"key": "sys_class_name", "value_type": "text",
                             "value_text": r["sys_class_name"]}]}
            for r in rows_ if r["sys_class_name"] == ci_class]


def apex_ops(rows_):
    tier = {"Critical": "Tier 1", "High": "Tier 2", "Moderate": "Tier 3"}
    return [{"op": "add", "key": r["asset_id"], "name": r["title"],
             "description": r["summary"], "record_class": r["asset_type"],
             "lifecycle_state": "Production" if r["status"] == "Live" else "Sunsetting",
             "criticality": tier.get(r["tier"], r["tier"]),
             "business_owner": r["steward"], "engineering_owner": r["delivery_team"]}
            for r in rows_]


def gh_ops(rows_):
    return [{"op": "add", "key": str(r["id"]), "name": r["name"], "url": r["html_url"],
             "default_branch": r["default_branch"], "primary_language": r["language"],
             "last_commit_at": r["pushed_at"], "archived": r["archived"]}
            for r in rows_]


# ------------------------------------------------------------------ steps

def run_connect_servicenow(con):
    counts = {}
    for r in load("servicenow.json"):
        counts[r["sys_class_name"]] = counts.get(r["sys_class_name"], 0) + 1
    s = ingest.create_source(con, role="inventory", kind="servicenow", name="ServiceNow",
                             created_by=JOE, base_url="https://sn.meridian.example",
                             tier_selection="cmdb_ci_business_app", priority=10,
                             owner_team="Service Management", expected_cadence_days=30)
    return {"headline": "ServiceNow registered, tier cmdb_ci_business_app",
            "facts": [f"{k}: {v} records" + ("   <- selected" if k == "cmdb_ci_business_app" else "")
                      for k, v in sorted(counts.items())],
            "note": "Nothing in the data says which class means \"application\". "
                    "joe@ decided, and the decision is recorded on the source.",
            "link": "sources"}


def run_ingest_servicenow(con):
    s = src(con, "ServiceNow")
    r = ingest.ingest(con, s["id"], payload(sn_ops(load("servicenow.json"),
                                                   "cmdb_ci_business_app"), JOE),
                      mode="changeset")
    return {"headline": f"{r['counts']['new']} records ingested",
            "facts": [f"21 records at the other three tiers were never sent",
                      f"sync run {r['sync_run_id'][:12]}, method api, mode changeset"],
            "note": "Tier selection happened at the source, which is why no "
                    "infrastructure CI is in scope.",
            "link": "sources"}


def run_build_packet(con):
    s = src(con, "ServiceNow")
    pk = packets.create_packet(
        con, subject_type="inventory_record", source_id=s["id"], created_by=JOE,
        purpose="Which ServiceNow business applications need SSDLC management?",
        suggested_rules=["Production business applications are usually in scope.",
                         "The Lotus Notes estate is being retired; confirm before including it."])
    return {"headline": f"packet built: {pk['row_count']} candidates",
            "facts": [f"prompt is {len(pk['prompt_text'])} characters, ready to paste",
                      "suggested rules are prose hints, not rules we evaluate"],
            "note": "The export is the product surface. What comes back is bounded "
                    "by what we put in.",
            "link": "packets"}


def _chat_answer(pk):
    """What comes back from the chat session.

    Identical wording where the reasoning is identical -- that is what collapses
    47 rows into a handful of recorded decisions.
    """
    lines = ["| record_key | decision | rationale |", "|---|---|---|"]
    for r in pk["rows"]:
        if "Notes" in r["name"]:
            lines.append(f"| {r['record_key']} | reject | {NOTES_RATIONALE} |")
        elif r["criticality"] == "Tier 3":
            lines.append(f"| {r['record_key']} | reject | {TIER3_RATIONALE} |")
        else:
            lines.append(f"| {r['record_key']} | manage | Production business application, "
                         f"{r['criticality']}, owned by {r['engineering_owner']} |")
    return "\n".join(lines)


def run_return_packet(con):
    pk = one(con, "SELECT * FROM decision_packet WHERE status='open'"
                  " ORDER BY created_at LIMIT 1")
    full = packets.get_packet(con, pk["id"])
    d = packets.submit_return(con, pk["id"], _chat_answer(full), actor=JOE)
    groups = sorted(d["rationale_groups"], key=lambda g: -g["subject_count"])
    return {"headline": f"{d['parsed_rows']} decisions returned, "
                        f"{len(d['rationale_groups'])} distinct rationales",
            "facts": [f"{g['disposition']} x{g['subject_count']}  {g['rationale'][:64]}"
                      for g in groups[:5]],
            "note": f"{d['parsed_rows']} rows become {len(d['rationale_groups'])} decisions, "
                    f"not {d['parsed_rows']}. Rows left out come back undecided, never rejected.",
            "link": "packets"}


def run_apply_packet(con):
    pk = one(con, "SELECT * FROM decision_packet WHERE status='returned'"
                  " ORDER BY created_at LIMIT 1")
    res = packets.apply_packet(con, pk["id"], applied_by=JOE)
    return {"headline": f"{res['decisions_created']} decisions applied over "
                        f"{res['subjects_affected']} subjects",
            "facts": [f"{res['applications_created']} applications created, each with a "
                      f"default managed entity",
                      "every application has a stable ID that outlives its CMDB record"],
            "link": "applications"}


def run_second_cmdb(con):
    s = ingest.create_source(con, role="inventory", kind="bmc_helix", name="Apex ITAM",
                             created_by=JOE, tier_selection="Application", priority=20,
                             owner_team="IT Asset Management", expected_cadence_days=30)
    ingest.ingest(con, s["id"], payload(apex_ops(load("apex_itam.json")), JOE),
                  mode="changeset")
    matched = 0
    for rec in ingest.known_inventory(con, s["id"]):
        app = one(con, "SELECT id FROM application WHERE name=? AND status='active'",
                  rec["name"])
        if not app:
            continue
        r = one(con, "SELECT id FROM inventory_record WHERE source_id=? AND external_id=?",
                s["id"], rec["external_id"])
        con.execute("UPDATE inventory_record SET scope_status='manage' WHERE id=?", (r["id"],))
        registry.link_record(con, app["id"], r["id"], JOE, origin="dedupe")
        matched += 1
    con.commit()
    conflicts = rows(con, "SELECT a.handle, a.name, r.field FROM attribute_resolution r"
                          " JOIN application a ON a.id=r.application_id WHERE r.conflict=1"
                          " ORDER BY a.handle")
    return {"headline": f"{matched} records matched an existing application; "
                        f"{len(conflicts)} field conflicts",
            "facts": [f"{c['name']} - {c['field'].replace('_',' ')}" for c in conflicts],
            "note": "Two CMDBs, different keys, different field names, different "
                    "vocabularies. Conflicts surface rather than being silently resolved.",
            "link": "applications"}


def run_spreadsheet(con):
    shadow = list(csv.DictReader(
        (FX / "shadow_apps.csv").read_text(encoding="utf-8").splitlines()))
    s = ingest.create_source(con, role="inventory", kind="csv_list",
                             name="Shadow application spreadsheet", created_by=JOE,
                             tier_selection="whole sheet", priority=50,
                             owner_team="Security Engineering")
    ops = [{"op": "add", "key": f"SHEET-{i:03d}", "name": r["Application"],
            "description": r["Notes"], "record_class": "spreadsheet_row",
            "lifecycle_state": "Production", "criticality": r["Criticality"],
            "business_owner": r["Owner"], "engineering_owner": r["Team"]}
           for i, r in enumerate(shadow, 1)]
    ingest.ingest(con, s["id"], payload(ops, JOE, method="export_file"), mode="changeset")
    names = []
    for op in ops:
        rec = one(con, "SELECT id, name FROM inventory_record WHERE source_id=? AND external_id=?",
                  s["id"], op["key"])
        con.execute("UPDATE inventory_record SET scope_status='manage' WHERE id=?", (rec["id"],))
        registry.create_application(con, name=rec["name"], origin="federated",
                                    created_by=JOE, record_id=rec["id"])
        names.append(rec["name"])
    con.commit()
    return {"headline": f"{len(names)} applications that are in neither CMDB",
            "facts": names,
            "note": "A spreadsheet is just another source with role=inventory. Same "
                    "ingestion path, same decisions, same kind of ID.",
            "link": "sources"}


def run_code(con):
    gh = ingest.create_source(con, role="code", kind="github", name="GitHub Enterprise",
                              created_by=SARAH, base_url="https://github.example.com",
                              owner_team="Developer Platform")
    ingest.ingest(con, gh["id"], payload(gh_ops(load("github.json")), SARAH),
                  mode="changeset")
    hints = {r["name"]: r for r in load("github.json")}
    mapped = 0
    for repo in registry.list_repositories(con, source_id=gh["id"]):
        desc = hints.get(repo["name"], {}).get("description", "")
        if not desc.startswith("Part of "):
            continue
        app = one(con, "SELECT id FROM application WHERE name=? AND status='active'",
                  desc[len("Part of "):].rstrip("."))
        if app:
            registry.map_repository(con, repo["id"], app["id"], SARAH)
            mapped += 1
    con.commit()
    total = scalar(con, "SELECT COUNT(*) FROM repository WHERE source_id=?", gh["id"])
    return {"headline": f"{total} repositories ingested, {mapped} mapped",
            "facts": [f"{total - mapped} have no declared home yet",
                      "GitHub is registered code-role only; Actions would be a second "
                      "registration owned by Release Engineering"],
            "link": "repositories"}


def run_svn(con):
    s = ingest.create_source(con, role="code", kind="svn", name="Subversion",
                             created_by=SARAH, owner_team="Legacy Support")
    ops = [{"op": "add", "key": r["uuid"], "name": r["repository"], "url": r["url"],
            "default_branch": "trunk", "archived": False} for r in load("svn.json")]
    ingest.ingest(con, s["id"], payload(ops, SARAH), mode="changeset")
    n = 0
    for repo_fx in load("svn.json"):
        repo = one(con, "SELECT id FROM repository WHERE source_id=? AND external_id=?",
                   s["id"], repo_fx["uuid"])
        for path in repo_fx["paths"]:
            sc = registry.add_scope(con, repo["id"], path_prefix=path["path"],
                                    name=path["hint"] or path["path"], created_by=SARAH)
            n += 1
            if path["hint"]:
                app = one(con, "SELECT id FROM application WHERE name=? AND status='active'",
                          path["hint"])
                if app:
                    registry.map_repository(con, repo["id"], app["id"], SARAH,
                                            repo_scope_id=sc["id"])
    con.commit()
    return {"headline": f"3 SVN repositories, {n} subtrees declared",
            "facts": ["legacy-apps alone holds five projects under /trunk"],
            "note": "Without subtree scopes the finest available grain is the "
                    "repository, which maps to five applications at once and is useless.",
            "link": "repositories"}


def run_build(con):
    s = ingest.create_source(con, role="build", kind="jenkins", name="Jenkins",
                             created_by=PAT, owner_team="Release Engineering")
    jen = load("jenkins.json")
    ops = [{"op": "add", "key": r["id"], "coordinate": r["coordinate"],
            "artifact_type": r["artifact_type"], "pipeline_ref": r["pipeline"]} for r in jen]
    ingest.ingest(con, s["id"], payload(ops, PAT), mode="changeset")
    hint = {a["id"]: a["hint_app"] for a in jen}
    governed = 0
    for art in registry.list_artifacts(con, governed=False):
        target = hint.get(art["external_id"])
        if not target:
            continue
        ent = one(con, "SELECT m.id FROM managed_entity m JOIN application a"
                       " ON a.id=m.application_id WHERE a.name=? AND a.status='active'"
                       " ORDER BY m.is_default DESC LIMIT 1", target)
        if ent:
            registry.govern_artifact(con, art["id"], ent["id"], PAT)
            governed += 1
    con.commit()
    un = scalar(con, "SELECT COUNT(*) FROM v_orphan_build_artifact")
    return {"headline": f"{len(ops)} artifacts ingested, {governed} governed",
            "facts": [o["display_name"] for o in views.orphans(con, "build_artifact")],
            "note": f"{un} ship with nothing governing them. Every one has a recent "
                    f"build. This is the row that earns the project.",
            "link": "artifacts"}


def run_omissions(con):
    made = []
    for name, pipeline, repos in (("ScanHub", "platform/scanhub",
                                   ["scanhub", "scanhub-drivers"]),
                                  ("FlowMon", "platform/flowmon", ["flowmon"])):
        app_id = registry.create_application(
            con, name=name, origin="inferred", created_by=SARAH,
            rationale=f"Ships from Jenkins pipeline {pipeline} and has source in GitHub, "
                      f"but is in no inventory source. Raised with Service Management "
                      f"for CMDB registration.")
        for rn in repos:
            r = one(con, "SELECT id FROM repository WHERE name=?", rn)
            if r:
                registry.map_repository(con, r["id"], app_id, SARAH)
        ent = one(con, "SELECT id FROM managed_entity WHERE application_id=?", app_id)
        art = one(con, "SELECT id FROM build_artifact WHERE pipeline_ref=?", pipeline)
        if art and ent:
            registry.govern_artifact(con, art["id"], ent["id"], SARAH)
        con.commit()
        made.append(registry.get_application(con, app_id))
    return {"headline": f"{len(made)} applications created with origin=inferred",
            "facts": [f"{a['handle']} {a['name']} - {a['repository_count']} repositories"
                      for a in made],
            "note": "In no CMDB and no spreadsheet, but they ship. They get a real ID "
                    "anyway, and the list of them is a report for the CMDB owners.",
            "link": "orphans/inferred_application"}


def run_decompose(con):
    plm = one(con, "SELECT id, handle FROM application WHERE name='PLM Suite'")
    arts = {a["coordinate"]: a["id"] for a in registry.list_artifacts(con)}
    registry.create_entity(
        con, plm["id"], name="PLM Client", kind="client", created_by=SARAH,
        rationale="Desktop client, separate release train and separate signing process "
                  "from the server.",
        build_artifact_ids=[v for k, v in arts.items() if "plm-client" in k])
    registry.create_entity(
        con, plm["id"], name="PLM Server", kind="server", created_by=SARAH,
        rationale="Server and the shared protocol jar are built and released together "
                  "from the same pipeline.",
        build_artifact_ids=[v for k, v in arts.items()
                            if "plm-server" in k or "plm-protocol" in k])
    ents = registry.list_entities(con, plm["id"])
    return {"headline": "PLM Suite decomposed into client and server",
            "facts": [f"{e['name']} - {e['artifact_count']} artifacts - {e['rationale'][:52]}"
                      for e in ents],
            "note": "Whether the SSDLC runs against a jar or its parent does not "
                    "matter. What matters is that the boundary is written down.",
            "link": f"application/{plm['id']}"}


def run_drift(con):
    s = src(con, "ServiceNow")
    r = ingest.ingest(con, s["id"],
                      payload(sn_ops(load("servicenow_v2.json"), "cmdb_ci_business_app"), JOE),
                      mode="changeset")
    stale = views.stale_subjects(con)
    return {"headline": f"{r['counts']['material']} material changes, "
                        f"{len(stale)} decisions now stale",
            "facts": [f"{s_['display_name']} - was {s_['disposition']} - changed "
                      f"{', '.join(s_['changed_fields']) or 'unknown'}" for s_ in stale],
            "note": "Not invalidated - stale. A person decided this and the thing they "
                    "decided about has moved. Fuel Card Integration was rejected as low "
                    "criticality and is now Tier 1. The other seven Notes applications "
                    "under the same rationale are untouched.",
            "link": "decisions"}


def run_partial(con):
    s = src(con, "ServiceNow")
    before = scalar(con, "SELECT COUNT(*) FROM inventory_record WHERE source_id=?"
                         " AND absent_since IS NULL", s["id"])
    ops = sn_ops(load("servicenow_partial_export.json"), "cmdb_ci_business_app")
    r = ingest.ingest(con, s["id"], {"contract_version": "1.0", "actor": JOE,
                                     "method": "export_file", "records": ops},
                      mode="snapshot")
    after = scalar(con, "SELECT COUNT(*) FROM inventory_record WHERE source_id=?"
                        " AND absent_since IS NULL", s["id"])
    return {"headline": f"filtered export of {len(ops)} rows: nothing marked absent",
            "facts": [f"live records before {before}, after {after}",
                      r["warnings"][0] if r["warnings"] else ""],
            "note": "Submitted without a completeness claim, so it defaults to partial "
                    "and may never mark absences. Claimed as full, this would have "
                    "stranded 28 records and orphaned their applications.",
            "link": "sources"}


# ------------------------------------------------------------------ table

STEPS = [
    dict(key="connect", title="Connect a CMDB",
         blurb="Register ServiceNow and choose which CI class represents an application. "
               "Tier selection is configuration, not inference.",
         run=run_connect_servicenow,
         done=lambda con: bool(src(con, "ServiceNow"))),

    dict(key="ingest", title="Ingest",
         blurb="Pull the selected tier. The companion skill does the retrieving; we "
               "hold no credentials.",
         run=run_ingest_servicenow,
         done=lambda con: scalar(con, "SELECT COUNT(*) FROM inventory_record") > 0),

    dict(key="packet", title="Build a decision packet",
         blurb="47 records, and someone has to say which ones matter. We build the "
               "candidate list and generate a prompt rather than writing rules.",
         run=run_build_packet,
         done=lambda con: scalar(con, "SELECT COUNT(*) FROM decision_packet") > 0),

    dict(key="return", title="Return the decided list",
         blurb="What comes back from the chat session, parsed and diffed before "
               "anything is applied.",
         run=run_return_packet,
         done=lambda con: scalar(con, "SELECT COUNT(*) FROM decision_packet"
                                      " WHERE status IN ('returned','applied')") > 0),

    dict(key="apply", title="Apply",
         blurb="One decision per distinct rationale, each over many subjects. "
               "Applications get stable IDs that outlive their CMDB records.",
         run=run_apply_packet,
         done=lambda con: scalar(con, "SELECT COUNT(*) FROM application") > 0),

    dict(key="second_cmdb", title="Add a second CMDB",
         blurb="Most organisations have more than one, and they disagree.",
         run=run_second_cmdb,
         done=lambda con: bool(src(con, "Apex ITAM"))),

    dict(key="spreadsheet", title="Add the spreadsheet",
         blurb="Some applications are in no CMDB at all. A spreadsheet is just another "
               "inventory source.",
         run=run_spreadsheet,
         done=lambda con: bool(src(con, "Shadow application spreadsheet"))),

    dict(key="code", title="Connect source control",
         blurb="Ingest repositories and map them to applications.",
         run=run_code,
         done=lambda con: bool(src(con, "GitHub Enterprise"))),

    dict(key="svn", title="Subversion and subtrees",
         blurb="One repository holding five projects needs subtree scoping to be "
               "mappable at all.",
         run=run_svn,
         done=lambda con: bool(src(con, "Subversion"))),

    dict(key="build", title="Connect the build system",
         blurb="Ingest what actually ships, and find out what nothing governs.",
         run=run_build,
         done=lambda con: bool(src(con, "Jenkins"))),

    dict(key="omissions", title="Record the omissions",
         blurb="Two things ship, have source, and appear in no inventory anywhere.",
         run=run_omissions,
         done=lambda con: scalar(con, "SELECT COUNT(*) FROM application"
                                      " WHERE origin='inferred'") > 0),

    dict(key="decompose", title="Decompose an application",
         blurb="Draw the SSDLC boundary and write down why it falls there.",
         run=run_decompose,
         done=lambda con: scalar(con, "SELECT COUNT(*) FROM managed_entity"
                                      " WHERE is_default=0") > 0),

    dict(key="drift", title="Re-sync, and watch decisions go stale",
         blurb="The CMDB moved. Decisions are not invalidated by that - they are "
               "marked for re-review.",
         run=run_drift,
         done=lambda con: scalar(con, "SELECT COUNT(*) FROM decision_subject"
                                      " WHERE status='stale'") > 0),

    dict(key="partial", title="The partial-export trap",
         blurb="Someone hands over a filtered export. Failing safe here matters more "
               "than convenience.",
         run=run_partial,
         done=lambda con: scalar(con, "SELECT COUNT(*) FROM sync_run"
                                      " WHERE mode='snapshot'") > 0),
]

BY_KEY = {s["key"]: s for s in STEPS}


def state(con):
    out, blocked = [], False
    for i, s in enumerate(STEPS, 1):
        try:
            is_done = bool(s["done"](con))
        except Exception:                                    # noqa: BLE001
            is_done = False
        out.append({"n": i, "key": s["key"], "title": s["title"], "blurb": s["blurb"],
                    "done": is_done, "next": False})
    nxt = next((s for s in out if not s["done"]), None)
    if nxt:
        nxt["next"] = True
    return out


def run(con, key):
    step = BY_KEY.get(key)
    if not step:
        from app.core import Refused
        raise Refused("unknown_step", f"No demo step {key!r}.",
                      remedy=f"One of: {', '.join(BY_KEY)}")
    if step["done"](con):
        return {"key": key, "already_done": True,
                "headline": "Already done.", "facts": []}
    result = step["run"](con)
    con.commit()
    result.update(key=key, title=step["title"], already_done=False)
    return result
