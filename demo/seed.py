#!/usr/bin/env python3
"""Drive the whole slice-1 round trip against the simulated sources.

    python -m demo.seed [--db demo.db]

Reads the fixtures the way a companion skill would after pulling them, builds
the changeset payloads that skill would POST, and walks: register sources ->
ingest -> decision packet -> decide -> diff -> apply -> map -> decompose ->
re-sync with drift -> coverage.

Prints a narrative rather than a log, because the point of the demo is that the
story is legible to someone who has not read the design.
"""

import argparse
import json
import pathlib
import sys

if hasattr(sys.stdout, "reconfigure"):          # Windows consoles default to cp1252
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from app import ingest, packets, registry, views                # noqa: E402
from app.core import connect, one, rows, scalar                 # noqa: E402

FX = pathlib.Path(__file__).parent.parent / "mocks" / "fixtures"
JOE, SARAH, PAT = "joe@meridian.example", "sarah@meridian.example", "pat@meridian.example"


def load(n):
    return json.loads((FX / n).read_text(encoding="utf-8"))


def say(msg=""):
    print(msg)


def head(n, title):
    print(f"\n{'-' * 72}\n{n}. {title}\n{'-' * 72}")


# ------------------------------------------------------------------ ingest

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


def svn_ops(rows_):
    return [{"op": "add", "key": r["uuid"], "name": r["repository"], "url": r["url"],
             "default_branch": "trunk", "primary_language": None,
             "last_commit_at": None, "archived": False} for r in rows_]


def jenkins_ops(rows_):
    return [{"op": "add", "key": r["id"], "coordinate": r["coordinate"],
             "artifact_type": r["artifact_type"], "pipeline_ref": r["pipeline"]}
            for r in rows_]


def payload(ops, actor, method="api", **kw):
    return {"contract_version": "1.0", "skill_version": "onboarding-skill/0.3",
            "actor": actor, "method": method, "ops": ops, **kw}


# ------------------------------------------------------------------- main

def main(db_path):
    p = pathlib.Path(db_path)
    if p.exists():
        p.unlink()
    con = connect(db_path)

    head(1, "Register sources")
    say("Tier selection is user configuration. ServiceNow carries four CI classes and")
    say("nothing in the data says which one means \"application\" - someone decides.\n")
    sn_all = load("servicenow.json")
    classes = {}
    for r in sn_all:
        classes[r["sys_class_name"]] = classes.get(r["sys_class_name"], 0) + 1
    for k, v in sorted(classes.items()):
        mark = "  <- joe@ selected this tier" if k == "cmdb_ci_business_app" else ""
        say(f"    {k:<26} {v:>3}{mark}")

    sn = ingest.create_source(con, role="inventory", kind="servicenow", name="ServiceNow",
                              created_by=JOE, base_url="https://sn.meridian.example",
                              tier_selection="cmdb_ci_business_app", priority=10,
                              owner_team="Service Management", expected_cadence_days=30)
    apex = ingest.create_source(con, role="inventory", kind="bmc_helix", name="Apex ITAM",
                                created_by=JOE, tier_selection="Application", priority=20,
                                owner_team="IT Asset Management", expected_cadence_days=30)
    gh = ingest.create_source(con, role="code", kind="github", name="GitHub Enterprise",
                              created_by=SARAH, base_url="https://github.example.com",
                              owner_team="Developer Platform")
    svn = ingest.create_source(con, role="code", kind="svn", name="Subversion",
                               created_by=SARAH, owner_team="Legacy Support")
    jen = ingest.create_source(con, role="build", kind="jenkins", name="Jenkins",
                               created_by=PAT, owner_team="Release Engineering")
    say(f"\n    5 sources registered. Note GitHub is code-role only; if Actions were in")
    say(f"    use it would be a second registration owned by Release Engineering.")

    head(2, "Ingest")
    for src, ops, who in (
            (sn, sn_ops(sn_all, "cmdb_ci_business_app"), JOE),
            (apex, apex_ops(load("apex_itam.json")), JOE),
            (gh, gh_ops(load("github.json")), SARAH),
            (svn, svn_ops(load("svn.json")), SARAH),
            (jen, jenkins_ops(load("jenkins.json")), PAT)):
        r = ingest.ingest(con, src["id"], payload(ops, who), mode="changeset")
        say(f"    {src['name']:<20} {r['counts']['new']:>3} new")

    say("\n    The 21 ServiceNow records at other tiers were never sent. Tier selection")
    say("    happened at the source, which is why no infrastructure CI is in scope.")

    head(3, "The lightweight list - applications in no CMDB")
    import csv as _csv
    shadow = list(_csv.DictReader(
        (FX / "shadow_apps.csv").read_text(encoding="utf-8").splitlines()))
    sheet = ingest.create_source(con, role="inventory", kind="csv_list",
                                 name="Shadow application spreadsheet", created_by=JOE,
                                 tier_selection="whole sheet", priority=50,
                                 owner_team="Security Engineering")
    ops = [{"op": "add", "key": f"SHEET-{i:03d}", "name": r["Application"],
            "description": r["Notes"], "record_class": "spreadsheet_row",
            "lifecycle_state": "Production", "criticality": r["Criticality"],
            "business_owner": r["Owner"], "engineering_owner": r["Team"]}
           for i, r in enumerate(shadow, 1)]
    ingest.ingest(con, sheet["id"], payload(ops, JOE, method="export_file"),
                  mode="changeset")
    for op in ops:
        rec = one(con, "SELECT id, name, content_hash FROM inventory_record"
                       " WHERE source_id=? AND external_id=?", sheet["id"], op["key"])
        con.execute("UPDATE inventory_record SET scope_status='manage' WHERE id=?",
                    (rec["id"],))
        registry.create_application(con, name=rec["name"], origin="federated",
                                    created_by=JOE, record_id=rec["id"])
    con.commit()
    say(f"    {len(ops)} applications that exist in neither CMDB, tracked anyway")
    say("    Same ingestion path, same decisions, same IDs. A spreadsheet is just")
    say("    another source with role=inventory.")

    head(4, "Decision packet - which records need SSDLC management?")
    pk = packets.create_packet(
        con, subject_type="inventory_record", source_id=sn["id"], created_by=JOE,
        purpose="Which ServiceNow business applications need SSDLC management?",
        suggested_rules=[
            "Production business applications are usually in scope.",
            "The Lotus Notes estate is being retired; confirm before including it.",
        ])
    say(f"    packet {pk['id'][:12]}  {pk['row_count']} candidates")
    say(f"    prompt is {len(pk['prompt_text'])} chars, ready to paste into a chat session")

    # What comes back from that chat session.  Note the shared wording on the
    # Notes estate: identical rationales collapse into one recorded decision.
    NOTES = ("Lotus Notes estate, replacement in flight, out of scope until retirement")
    lines = ["| record_key | decision | rationale |", "|---|---|---|"]
    for r in pk["rows"]:
        if "(Notes)" in r["name"] or "Notes" in r["name"]:
            lines.append(f"| {r['record_key']} | reject | {NOTES} |")
        elif r["criticality"] == "Tier 3":
            lines.append(f"| {r['record_key']} | reject | "
                         f"Tier 3 utility application, below the SSDLC threshold agreed "
                         f"with security leadership |")
        else:
            lines.append(f"| {r['record_key']} | manage | "
                         f"Production business application, {r['criticality']}, "
                         f"owned by {r['engineering_owner']} |")
    packets.submit_return(con, pk["id"], "\n".join(lines), actor=JOE)

    d = packets.diff(con, pk["id"])
    say(f"\n    returned and parsed: {d['parsed_rows']} rows, "
        f"{len(d['undecided_keys'])} left undecided")
    say(f"    {len(d['rationale_groups'])} distinct rationales -> "
        f"{len(d['rationale_groups'])} decisions, not {d['parsed_rows']}:")
    for g in sorted(d["rationale_groups"], key=lambda g: -g["subject_count"])[:4]:
        say(f"      {g['disposition']:<7} x{g['subject_count']:<3} {g['rationale'][:58]}")

    res = packets.apply_packet(con, pk["id"], applied_by=JOE)
    say(f"\n    applied: {res['decisions_created']} decisions over "
        f"{res['subjects_affected']} subjects, {res['applications_created']} applications created")

    head(5, "Second CMDB - federation and disagreement")
    apex_recs = ingest.known_inventory(con, apex["id"])
    matched = 0
    for rec in apex_recs:
        app = one(con, "SELECT id FROM application WHERE name=? AND status='active'",
                  rec["name"])
        if not app:
            continue
        r = one(con, "SELECT id, content_hash FROM inventory_record WHERE source_id=?"
                     " AND external_id=?", apex["id"], rec["external_id"])
        con.execute("UPDATE inventory_record SET scope_status='manage' WHERE id=?", (r["id"],))
        registry.link_record(con, app["id"], r["id"], JOE, origin="dedupe")
        matched += 1
    con.commit()
    say(f"    {matched} Apex records matched an existing application and were linked.")

    conflicts = rows(con,
        "SELECT a.handle, a.name, r.field, r.resolved_value, s.name AS winner"
        " FROM attribute_resolution r JOIN application a ON a.id=r.application_id"
        " LEFT JOIN inventory_record ir ON ir.id=r.winning_record_id"
        " LEFT JOIN source s ON s.id=ir.source_id"
        " WHERE r.conflict=1 ORDER BY a.handle, r.field")
    say(f"    {len(conflicts)} field-level conflicts, surfaced rather than silently resolved:")
    for c in conflicts[:6]:
        say(f"      {c['handle']} {c['name']:<24} {c['field']:<18} "
            f"-> {str(c['resolved_value'])[:22]:<22} ({c['winner']} wins on priority)")

    head(6, "Map repositories")
    hints = {r["name"]: r for r in load("github.json")}
    mapped = unmapped = 0
    for repo in registry.list_repositories(con, source_id=gh["id"]):
        topics = hints.get(repo["name"], {}).get("topics", [])
        key = next((t[4:] for t in topics if t.startswith("app-")), None)
        target = None
        if key:
            desc = hints[repo["name"]].get("description", "")
            if desc.startswith("Part of "):
                target = desc[len("Part of "):].rstrip(".")
        if target:
            app = one(con, "SELECT id FROM application WHERE name=? AND status='active'", target)
            if app:
                registry.map_repository(con, repo["id"], app["id"], SARAH)
                mapped += 1
                continue
        unmapped += 1
    con.commit()
    say(f"    {mapped} repositories mapped, {unmapped} left unmapped")

    head(7, "SVN subtrees - the repository is not the finest useful grain")
    for svn_repo in load("svn.json"):
        repo = one(con, "SELECT id FROM repository WHERE source_id=? AND external_id=?",
                   svn["id"], svn_repo["uuid"])
        for path_entry in svn_repo["paths"]:
            sc = registry.add_scope(con, repo["id"], path_prefix=path_entry["path"],
                                    name=path_entry["hint"] or path_entry["path"],
                                    created_by=SARAH)
            if path_entry["hint"]:
                app = one(con, "SELECT id FROM application WHERE name=? AND status='active'",
                          path_entry["hint"])
                if app:
                    registry.map_repository(con, repo["id"], app["id"], SARAH,
                                            repo_scope_id=sc["id"])
    con.commit()
    n_scope = scalar(con, "SELECT COUNT(*) FROM repo_scope")
    say(f"    {n_scope} subtrees declared across 3 SVN repositories")
    say("    Without scopes the finest grain is \"legacy-apps\", which maps to five")
    say("    applications at once and is useless.")

    head(8, "Govern the artifacts")
    hint_by_key = {a["id"]: a["hint_app"] for a in load("jenkins.json")}
    governed = 0
    for art in registry.list_artifacts(con, governed=False):
        target = hint_by_key.get(art["external_id"])
        if not target:
            continue
        ent = one(con, "SELECT m.id FROM managed_entity m JOIN application a"
                       " ON a.id=m.application_id WHERE a.name=? AND a.status='active'"
                       " ORDER BY m.is_default DESC LIMIT 1", target)
        if ent:
            registry.govern_artifact(con, art["id"], ent["id"], PAT)
            governed += 1
    con.commit()
    say(f"    {governed} artifacts assigned to a managed entity")
    say(f"    {scalar(con, 'SELECT COUNT(*) FROM v_orphan_build_artifact')} still governed "
        f"by nothing")

    head(9, "Detected inventory omissions")
    say("    Two artifacts ship and have repositories, but appear in no CMDB and no")
    say("    spreadsheet. They need an application ID anyway.")
    say()
    for name, pipeline, repos in (
            ("ScanHub", "platform/scanhub", ["scanhub", "scanhub-drivers"]),
            ("FlowMon", "platform/flowmon", ["flowmon"])):
        app_id = registry.create_application(
            con, name=name, origin="inferred", created_by=SARAH,
            rationale=f"Ships from Jenkins pipeline {pipeline} and has source in GitHub, "
                      f"but is in no inventory source. Raised with Service Management "
                      f"for CMDB registration.")
        for rn in repos:
            r = one(con, "SELECT id FROM repository WHERE name=? AND source_id=?", rn, gh["id"])
            if r:
                registry.map_repository(con, r["id"], app_id, SARAH)
        ent = one(con, "SELECT id FROM managed_entity WHERE application_id=?", app_id)
        art = one(con, "SELECT id FROM build_artifact WHERE pipeline_ref=?", pipeline)
        if art and ent:
            registry.govern_artifact(con, art["id"], ent["id"], SARAH)
        con.commit()
        a = registry.get_application(con, app_id)
        say(f"    {a['handle']} {a['name']:<10} {a['repository_count']} repos, "
            f"origin=inferred - a report for the CMDB owners")

    head(10, "Decompose - the boundary is a documented decision")
    plm = one(con, "SELECT id, handle FROM application WHERE name='PLM Suite'")
    if plm:
        arts = {a["coordinate"]: a["id"] for a in registry.list_artifacts(con)}
        registry.create_entity(
            con, plm["id"], name="PLM Client", kind="client", created_by=SARAH,
            rationale="Desktop client, separate release train and separate signing "
                      "process from the server.",
            build_artifact_ids=[arts[k] for k in arts if "plm-client" in k])
        registry.create_entity(
            con, plm["id"], name="PLM Server", kind="server", created_by=SARAH,
            rationale="Server and the shared protocol jar are built and released "
                      "together from the same pipeline.",
            build_artifact_ids=[arts[k] for k in arts
                                if "plm-server" in k or "plm-protocol" in k])
        say("    foo.app in the design discussion: whether the SSDLC runs against the")
        say("    jar or its parent does not matter, as long as it is written down.")
        say()
        for e in registry.list_entities(con, plm["id"]):
            say(f"    {e['name']:<14} {e['artifact_count']} artifacts  {e['rationale'][:46]}")

    head(11, "Re-sync with drift - decisions go stale, not invalid")
    r = ingest.ingest(con, sn["id"],
                      payload(sn_ops(load("servicenow_v2.json"), "cmdb_ci_business_app"),
                              JOE), mode="changeset")
    say(f"    {r['counts']['material']} material changes, "
        f"{r['counts']['unchanged'] + r['counts']['immaterial']} unchanged")
    for s in views.stale_subjects(con):
        say(f"      {s['display_name']:<28} was {s['disposition']:<7} "
            f"- {s['rationale'][:44]}")
    say("\n    Each keeps its effect until re-decided. The other records under the same")
    say("    rationale are untouched - staleness is per subject, the prose is shared.")

    head(12, "The partial-export trap")
    before = scalar(con, "SELECT COUNT(*) FROM inventory_record WHERE source_id=?"
                         " AND absent_since IS NULL", sn["id"])
    part = load("servicenow_partial_export.json")
    ops = sn_ops(part, "cmdb_ci_business_app")
    r = ingest.ingest(con, sn["id"],
                      {"contract_version": "1.0", "actor": JOE, "method": "export_file",
                       "records": ops}, mode="snapshot")
    after = scalar(con, "SELECT COUNT(*) FROM inventory_record WHERE source_id=?"
                        " AND absent_since IS NULL", sn["id"])
    say(f"    filtered export of {len(ops)} records submitted without a completeness claim")
    say(f"    live records before {before}, after {after}  - nothing marked absent")
    say(f"    {r['warnings'][0]}")

    head(13, "Coverage")
    cov = views.coverage(con)
    a, i = cov["applications"], cov["inventory"]
    say(f"    applications   {a['total']:>3}   "
        f"federated {a['by_origin']['federated']}, inferred {a['by_origin']['inferred']}")
    say(f"    inventory      {i['records_total']:>3}   managed {i['managed']}, "
        f"rejected {i['rejected']}, undecided {i['undecided']}, stale {i['stale_decisions']}")
    say(f"    repositories   {cov['code']['repositories_total']:>3}   "
        f"mapped {cov['code']['mapped']}, unmapped {cov['code']['unmapped']}")
    say(f"    artifacts      {cov['build']['artifacts_total']:>3}   "
        f"governed {cov['build']['governed']}, ungoverned {cov['build']['ungoverned']}")

    say("\n    Something ships that nothing governs:")
    for o in views.orphans(con, "build_artifact")[:6]:
        say(f"      {o['display_name']:<44} {o['detail'] or ''}")

    head(14, "Lineage")
    app = one(con, "SELECT id FROM application WHERE name='Billing Platform'")
    if app:
        say("    " + views.lineage(con, app["id"])["narrative"])

    con.close()
    say(f"\n{'-' * 72}")
    say(f"Database written to {db_path}.  Start the UI with:")
    say(f"    python -m app.serve --db {db_path}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="demo.db")
    main(ap.parse_args().db)
