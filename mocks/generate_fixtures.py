#!/usr/bin/env python3
"""
Generate simulated source-system fixtures for the SSDLC Onboarding demo.

Deterministic: same input, same output, no randomness that matters.  Standard
library only.

The data is a fictional company, Meridian Freight, and it is shaped deliberately
so that each demo scenario has something real to show.  See mocks/README.md for
the scenario list.

    python mocks/generate_fixtures.py
"""

import csv
import json
import pathlib

OUT = pathlib.Path(__file__).parent / "fixtures"
OUT.mkdir(exist_ok=True)

# ---------------------------------------------------------------------------
# Canonical truth.  No real system has this view; that is the whole problem.
#
#   sn     present in ServiceNow
#   apex   present in Apex ITAM (the second CMDB)
#   csv    present in the shadow spreadsheet
#   dis    Apex disagrees — field overrides, to exercise survivorship
# ---------------------------------------------------------------------------

A = lambda key, name, crit, life, biz, eng, desc, **kw: dict(
    key=key, name=name, criticality=crit, lifecycle=life,
    business_owner=biz, engineering_owner=eng, description=desc,
    sn=kw.get("sn", True), apex=kw.get("apex", False), csv=kw.get("csv", False),
    dis=kw.get("dis"), notes_legacy=kw.get("notes_legacy", False),
)

APPS = [
    # --- core freight platform -------------------------------------------------
    A("plm",        "PLM Suite",               "Tier 1", "Production", "H. Okafor",  "Platform Engineering",
      "Product lifecycle management for trailer and container configurations.", apex=True),
    A("billing",    "Billing Platform",        "Tier 1", "Production", "R. Castellanos", "Revenue Systems",
      "Freight invoicing, rating and settlement.", apex=True,
      dis={"criticality": "Critical", "business_owner": "R. Castellanos-Ruiz"}),
    A("quote",      "Freight Quote Engine",    "Tier 1", "Production", "R. Castellanos", "Revenue Systems",
      "Real-time rate quoting for web and partner channels.", apex=True),
    A("portal",     "Customer Portal",         "Tier 1", "Production", "M. Lindqvist", "Digital Channels",
      "Customer-facing shipment booking and tracking.", apex=True),
    A("driverapp",  "Driver Mobile App",       "Tier 1", "Production", "D. Achebe",   "Mobile Engineering",
      "iOS/Android application for drivers: dispatch, POD capture, hours of service.", apex=True),
    A("routeopt",   "Route Optimizer",         "Tier 2", "Production", "D. Achebe",   "Operations Research",
      "Daily route and load assignment optimisation.", apex=True),
    A("wms",        "Warehouse Management",    "Tier 1", "Production", "P. Nakamura", "Warehouse Systems",
      "Inventory, pick/pack and dock operations across 14 facilities.", apex=True,
      dis={"engineering_owner": "Facilities IT"}),
    A("yard",       "Yard Management",         "Tier 2", "Production", "P. Nakamura", "Warehouse Systems",
      "Trailer positioning and gate control.", apex=True),
    A("telematics", "Telematics Ingest",       "Tier 2", "Production", "D. Achebe",   "Fleet Data",
      "High-volume ingest of vehicle telemetry.", apex=True),
    A("edi",        "EDI Gateway",             "Tier 1", "Production", "S. Varga",    "Integration Services",
      "X12/EDIFACT translation for partner document exchange.", apex=True),
    A("customs",    "Customs Filing",          "Tier 1", "Production", "S. Varga",    "Integration Services",
      "Cross-border declarations and broker integration.", apex=True,
      dis={"criticality": "Tier 2"}),
    A("ratecard",   "Rate Card Service",       "Tier 2", "Production", "R. Castellanos", "Revenue Systems",
      "Contract rate storage and lookup.", apex=True),
    A("recon",      "Invoice Reconciliation",  "Tier 2", "Production", "R. Castellanos", "Revenue Systems",
      "Matches carrier invoices against expected charges."),
    A("paygw",      "Payment Gateway Adapter", "Tier 1", "Production", "R. Castellanos", "Revenue Systems",
      "Card and ACH processing integration.", apex=True),
    A("fuelcard",   "Fuel Card Integration",   "Tier 3", "Production", "L. Boateng",  "Fleet Data",
      "Fuel purchase reconciliation with card provider feeds."),

    # --- corporate -------------------------------------------------------------
    A("hrportal",   "HR Portal",               "Tier 2", "Production", "K. Sorensen", "Corporate Systems",
      "Employee self-service.", apex=True),
    A("payroll",    "Payroll Bridge",          "Tier 1", "Production", "K. Sorensen", "Corporate Systems",
      "Transfers time and earnings to the payroll provider.", apex=True),
    A("lms",        "Learning Management",     "Tier 3", "Production", "K. Sorensen", "Corporate Systems",
      "Compliance and safety training delivery."),
    A("expense",    "Expense Tool",            "Tier 3", "Production", "K. Sorensen", "Corporate Systems",
      "Expense capture and approval."),
    A("vendor",     "Vendor Portal",           "Tier 2", "Production", "T. Mbeki",    "Procurement IT",
      "Supplier onboarding and document exchange.", apex=True),
    A("procure",    "Procurement Workflow",    "Tier 3", "Production", "T. Mbeki",    "Procurement IT",
      "Purchase requisition routing."),
    A("contracts",  "Contract Repository",     "Tier 2", "Production", "T. Mbeki",    "Legal Technology",
      "Executed contract storage with clause search.", apex=True,
      dis={"business_owner": "Legal Operations", "criticality": "Tier 1"}),

    # --- fleet and compliance --------------------------------------------------
    A("assets",     "Asset Tracking",          "Tier 2", "Production", "L. Boateng",  "Fleet Data",
      "Tractor, trailer and chassis registry."),
    A("maint",      "Maintenance Scheduler",   "Tier 2", "Production", "L. Boateng",  "Fleet Data",
      "Preventive maintenance planning.", apex=True),
    A("inspect",    "Fleet Inspection",        "Tier 2", "Production", "L. Boateng",  "Fleet Data",
      "DVIR capture and defect workflow."),
    A("compliance", "Compliance Reporting",    "Tier 1", "Production", "N. Haddad",   "Risk & Compliance",
      "Regulatory reporting for hours of service and hazmat.", apex=True),
    A("safety",     "Safety Incident Tracker", "Tier 2", "Production", "N. Haddad",   "Risk & Compliance",
      "Incident intake, investigation and corrective action.", apex=True,
      dis={"engineering_owner": "Corporate Systems"}),

    # --- operations ------------------------------------------------------------
    A("dock",       "Dock Scheduler",          "Tier 2", "Production", "P. Nakamura", "Warehouse Systems",
      "Appointment booking for inbound and outbound doors."),
    A("loadplan",   "Load Planner",            "Tier 2", "Production", "D. Achebe",   "Operations Research",
      "Cube and weight optimisation for trailer loading."),
    A("tracking",   "Shipment Tracking API",   "Tier 1", "Production", "M. Lindqvist", "Digital Channels",
      "Public tracking API consumed by partners and the portal.", apex=True),
    A("notify",     "Notification Service",    "Tier 2", "Production", "M. Lindqvist", "Digital Channels",
      "Email, SMS and push fan-out."),

    # --- platform --------------------------------------------------------------
    A("idbroker",   "Identity Broker",         "Tier 1", "Production", "J. Whitfield", "Platform Engineering",
      "SSO and workforce identity federation.", apex=True),
    A("apigw",      "API Gateway",             "Tier 1", "Production", "J. Whitfield", "Platform Engineering",
      "Edge routing, throttling and authentication.", apex=True),
    A("datalake",   "Data Lake Ingest",        "Tier 2", "Production", "A. Ferreira", "Data Platform",
      "Batch and streaming ingest into the analytics lake.", apex=True),
    A("bi",         "BI Reporting",            "Tier 3", "Production", "A. Ferreira", "Data Platform",
      "Operational dashboards and scheduled reports."),
    A("forecast",   "Forecasting Models",      "Tier 3", "Production", "A. Ferreira", "Data Science",
      "Volume and capacity forecasting."),
    A("pricing",    "Pricing Analytics",       "Tier 2", "Production", "A. Ferreira", "Data Science",
      "Margin and lane profitability analysis."),
    A("sftp",       "Partner SFTP",            "Tier 2", "Production", "S. Varga",    "Integration Services",
      "Managed file transfer for partners without EDI."),
    A("imaging",    "Document Imaging",        "Tier 3", "Production", "S. Varga",    "Integration Services",
      "Scanned bill-of-lading capture and OCR."),

    # --- the legacy Notes estate: one decision, many subjects (§5.5) ------------
    A("lnclaims",   "Legacy Claims (Notes)",   "Tier 3", "Sunsetting", "N. Haddad",   "Legacy Support",
      "Lotus Notes claims intake. Replacement in flight.", apex=True, notes_legacy=True),
    A("lndispatch", "Legacy Dispatch (Notes)", "Tier 3", "Sunsetting", "D. Achebe",   "Legacy Support",
      "Lotus Notes dispatch board, read-only since 2024.", notes_legacy=True),
    A("lnrate",     "Legacy Rate Tool (Notes)","Tier 3", "Sunsetting", "R. Castellanos", "Legacy Support",
      "Lotus Notes rate calculator.", apex=True, notes_legacy=True),
    A("lnvendor",   "Legacy Vendor DB (Notes)","Tier 3", "Sunsetting", "T. Mbeki",    "Legacy Support",
      "Lotus Notes supplier directory.", notes_legacy=True),
    A("lnsafety",   "Legacy Safety Log (Notes)","Tier 3","Sunsetting", "N. Haddad",   "Legacy Support",
      "Lotus Notes incident log, archive only.", notes_legacy=True),
    A("lnfleet",    "Legacy Fleet Notes",      "Tier 3", "Sunsetting", "L. Boateng",  "Legacy Support",
      "Lotus Notes maintenance notebook.", apex=True, notes_legacy=True),
    A("lnhr",       "Legacy HR Forms (Notes)", "Tier 3", "Sunsetting", "K. Sorensen", "Legacy Support",
      "Lotus Notes HR request forms.", notes_legacy=True),
    A("lnwiki",     "Legacy Ops Wiki (Notes)", "Tier 3", "Sunsetting", "P. Nakamura", "Legacy Support",
      "Lotus Notes operations knowledge base.", notes_legacy=True),

    # --- in the shadow spreadsheet only ----------------------------------------
    A("brokerage",  "Brokerage Match",         "Tier 2", "Production", "R. Castellanos", "Revenue Systems",
      "Load-to-carrier matching for the brokerage arm. Acquired with Delta Lane Logistics; never registered.",
      sn=False, csv=True),
    A("greenmile",  "Emissions Reporting",     "Tier 3", "Production", "N. Haddad",   "Risk & Compliance",
      "Scope 1/3 emissions calculation for customer reporting. Built by the sustainability team.",
      sn=False, csv=True),
    A("quoteweb",   "Quote Widget",            "Tier 3", "Production", "M. Lindqvist", "Digital Channels",
      "Embeddable quoting widget for partner websites.",
      sn=False, csv=True),

    # --- in NOTHING: discovered from code and artifacts (§3.2 `inferred`) -------
    A("scanhub",    "ScanHub",                 None, None, None, None,
      "Barcode scanning middleware. Present in GitHub and Jenkins, in no inventory source.",
      sn=False),
    A("flowmon",    "FlowMon",                 None, None, None, None,
      "Pipeline monitoring sidecar. Present in GitHub and Jenkins, in no inventory source.",
      sn=False),
]

BY_KEY = {a["key"]: a for a in APPS}

# ---------------------------------------------------------------------------
# ServiceNow projection.  Three CI classes, so tier selection has to be a real
# choice (§3.2.1) — and ~6 infrastructure records that are noise at any tier.
# ---------------------------------------------------------------------------

SN_COARSE = [   # cmdb_ci_service — business services, one tier above applications
    ("Freight Execution",   "Everything involved in moving a load."),
    ("Revenue Management",  "Quote to cash."),
    ("Corporate Services",  "HR, finance, procurement."),
    ("Customer Experience", "Portal, tracking, notifications."),
    ("Fleet Operations",    "Assets, maintenance, compliance."),
]

SN_FINE = [     # cmdb_ci_appl — technical/deployed instances, one tier below
    ("billing-api-prod-01",  "Billing Platform"), ("billing-api-prod-02", "Billing Platform"),
    ("portal-web-prod-01",   "Customer Portal"),  ("portal-web-prod-02",  "Customer Portal"),
    ("edi-translator-prod",  "EDI Gateway"),      ("wms-core-prod",       "Warehouse Management"),
    ("apigw-edge-prod-01",   "API Gateway"),      ("apigw-edge-prod-02",  "API Gateway"),
    ("telematics-stream-01", "Telematics Ingest"),("plm-server-prod",     "PLM Suite"),
]

SN_INFRA = [    # cmdb_ci_server — never an application at any tier
    "esx-cluster-ord-01", "san-array-ord-02", "f5-ltm-dfw-01",
    "pgsql-prod-billing-01", "kafka-broker-ord-03", "ad-dc-hq-01",
]


def servicenow_records(drift=False):
    """drift=True returns the state after a later sync (§5.4 material change)."""
    rows = []
    n = 1000
    for a in APPS:
        if not a["sn"]:
            continue
        n += 1
        crit, life, biz = a["criticality"], a["lifecycle"], a["business_owner"]

        if drift:
            # Four material changes, each of a different kind, to exercise staleness.
            if a["key"] == "fuelcard":      crit = "Tier 1"          # criticality raised
            if a["key"] == "lnwiki":        life = "Retired"         # lifecycle moved
            if a["key"] == "imaging":       biz = "S. Varga-Toth"    # owner changed
            if a["key"] == "bi":            crit = "Tier 2"          # criticality raised

        rows.append({
            "sys_id": f"BSVC{n:05d}",
            "sys_class_name": "cmdb_ci_business_app",
            "name": a["name"],
            "short_description": a["description"],
            "operational_status": life,
            "u_criticality": crit,
            "owned_by": biz,
            "support_group": a["engineering_owner"],
            "u_app_key": a["key"],
        })

    n = 2000
    for name, desc in SN_COARSE:
        n += 1
        rows.append({
            "sys_id": f"BSVC{n:05d}", "sys_class_name": "cmdb_ci_service",
            "name": name, "short_description": desc,
            "operational_status": "Production", "u_criticality": "Tier 1",
            "owned_by": "Service Management", "support_group": "Service Management",
            "u_app_key": None,
        })

    n = 3000
    for host, parent in SN_FINE:
        n += 1
        rows.append({
            "sys_id": f"BSVC{n:05d}", "sys_class_name": "cmdb_ci_appl",
            "name": host, "short_description": f"Deployed instance of {parent}.",
            "operational_status": "Production", "u_criticality": "Tier 2",
            "owned_by": "Platform Engineering", "support_group": "Platform Engineering",
            "u_app_key": None,
        })

    n = 4000
    for host in SN_INFRA:
        n += 1
        rows.append({
            "sys_id": f"BSVC{n:05d}", "sys_class_name": "cmdb_ci_server",
            "name": host, "short_description": "Infrastructure CI.",
            "operational_status": "Production", "u_criticality": "Tier 2",
            "owned_by": "Infrastructure", "support_group": "Infrastructure",
            "u_app_key": None,
        })
    return rows


# ---------------------------------------------------------------------------
# Apex ITAM projection.  A different vendor with different field names, a
# partial view, and five deliberate disagreements (§3.2 survivorship).
# ---------------------------------------------------------------------------

APEX_ONLY = [
    ("Tariff Classification", "HS code assignment assistant used by the customs team."),
    ("Carrier Scorecard",     "Carrier performance scoring."),
    ("Lane Profitability",    "Duplicate of Pricing Analytics under a different name."),
    ("Depot Wi-Fi Portal",    "Guest network captive portal at depots."),
]

TIER_MAP = {"Tier 1": "Critical", "Tier 2": "High", "Tier 3": "Moderate"}


def apex_records():
    rows, n = [], 500
    for a in APPS:
        if not a["apex"]:
            continue
        n += 1
        dis = a.get("dis") or {}
        crit = dis.get("criticality", a["criticality"])
        rows.append({
            "asset_id": f"AX-{n:04d}",
            "asset_type": "Application",
            "title": a["name"],
            "summary": a["description"],
            "status": "Live" if a["lifecycle"] == "Production" else "Decommissioning",
            "tier": TIER_MAP.get(crit, crit),
            "steward": dis.get("business_owner", a["business_owner"]),
            "delivery_team": dis.get("engineering_owner", a["engineering_owner"]),
        })
    for title, summary in APEX_ONLY:
        n += 1
        rows.append({
            "asset_id": f"AX-{n:04d}", "asset_type": "Application",
            "title": title, "summary": summary, "status": "Live",
            "tier": "Moderate", "steward": "Unassigned", "delivery_team": "Unassigned",
        })
    return rows


# ---------------------------------------------------------------------------
# Repositories.  GitHub plus an SVN server whose repos hold /trunk/<project>.
# ---------------------------------------------------------------------------

GITHUB = [
    # (repo, app_key or None, language, archived, last_commit)
    ("plm-client",            "plm",        "C#",         0, "2026-09-12"),
    ("plm-server",            "plm",        "Java",       0, "2026-09-18"),
    ("plm-shared-protocol",   "plm",        "Java",       0, "2026-07-02"),
    ("billing-core",          "billing",    "Java",       0, "2026-09-19"),
    ("billing-settlement",    "billing",    "Java",       0, "2026-09-11"),
    ("billing-adjustments",   "billing",    "Java",       0, "2026-08-28"),
    ("quote-engine",          "quote",      "Go",         0, "2026-09-20"),
    ("quote-rules",           "quote",      "Go",         0, "2026-09-05"),
    ("customer-portal",       "portal",     "TypeScript", 0, "2026-09-19"),
    ("portal-bff",            "portal",     "TypeScript", 0, "2026-09-17"),
    ("driver-app-ios",        "driverapp",  "Swift",      0, "2026-09-16"),
    ("driver-app-android",    "driverapp",  "Kotlin",     0, "2026-09-16"),
    ("driver-app-sync",       "driverapp",  "Kotlin",     0, "2026-08-30"),
    ("route-optimizer",       "routeopt",   "Python",     0, "2026-09-09"),
    ("wms-core",              "wms",        "Java",       0, "2026-09-15"),
    ("wms-rf-client",         "wms",        "C#",         0, "2026-08-21"),
    ("yard-management",       "yard",       "Java",       0, "2026-07-30"),
    ("telematics-ingest",     "telematics", "Rust",       0, "2026-09-18"),
    ("telematics-decoder",    "telematics", "Rust",       0, "2026-09-02"),
    ("edi-gateway",           "edi",        "Java",       0, "2026-09-13"),
    ("edi-maps",              "edi",        "XSLT",       0, "2026-06-14"),
    ("customs-filing",        "customs",    "Java",       0, "2026-09-08"),
    ("rate-card-service",     "ratecard",   "Go",         0, "2026-09-04"),
    ("invoice-recon",         "recon",      "Python",     0, "2026-08-19"),
    ("payment-gateway-adapter","paygw",     "Java",       0, "2026-09-10"),
    ("fuel-card-sync",        "fuelcard",   "Python",     0, "2026-05-22"),
    ("hr-portal",             "hrportal",   "TypeScript", 0, "2026-08-27"),
    ("payroll-bridge",        "payroll",    "Java",       0, "2026-09-01"),
    ("expense-tool",          "expense",    "TypeScript", 0, "2026-07-16"),
    ("vendor-portal",         "vendor",     "TypeScript", 0, "2026-09-03"),
    ("contract-repository",   "contracts",  "C#",         0, "2026-08-12"),
    ("asset-tracking",        "assets",     "Java",       0, "2026-08-25"),
    ("maintenance-scheduler", "maint",      "Java",       0, "2026-09-06"),
    ("fleet-inspection",      "inspect",    "Kotlin",     0, "2026-08-14"),
    ("compliance-reporting",  "compliance", "Python",     0, "2026-09-14"),
    ("safety-incidents",      "safety",     "TypeScript", 0, "2026-08-08"),
    ("dock-scheduler",        "dock",       "Go",         0, "2026-07-24"),
    ("load-planner",          "loadplan",   "Python",     0, "2026-08-31"),
    ("shipment-tracking-api", "tracking",   "Go",         0, "2026-09-20"),
    ("notification-service",  "notify",     "Go",         0, "2026-09-07"),
    ("identity-broker",       "idbroker",   "Java",       0, "2026-09-15"),
    ("api-gateway-config",    "apigw",      "HCL",        0, "2026-09-19"),
    ("datalake-ingest",       "datalake",   "Scala",      0, "2026-09-12"),
    ("bi-reports",            "bi",         "Python",     0, "2026-06-30"),
    ("forecasting-models",    "forecast",   "Python",     0, "2026-08-05"),
    ("pricing-analytics",     "pricing",    "Python",     0, "2026-09-02"),
    ("partner-sftp-config",   "sftp",       "Shell",      0, "2026-04-11"),
    ("document-imaging",      "imaging",    "Python",     0, "2026-07-08"),
    # shadow-spreadsheet applications
    ("brokerage-match",       "brokerage",  "Python",     0, "2026-09-17"),
    ("emissions-reporting",   "greenmile",  "Python",     0, "2026-08-22"),
    ("quote-widget",          "quoteweb",   "TypeScript", 0, "2026-09-09"),
    # in NO inventory source: these become `inferred` applications
    ("scanhub",               "scanhub",    "Go",         0, "2026-09-18"),
    ("scanhub-drivers",       "scanhub",    "C",          0, "2026-08-29"),
    ("flowmon",               "flowmon",    "Go",         0, "2026-09-13"),
    # unmappable: code with no declared home (§7)
    ("meridian-terraform-modules", None,    "HCL",        0, "2026-09-16"),
    ("eng-handbook",          None,         "Markdown",   0, "2026-09-11"),
    ("hackday-2025-lanegame", None,         "JavaScript", 1, "2025-11-14"),
    ("hackday-2026-dispatchbot", None,      "Python",     1, "2026-03-02"),
    ("spike-graphql-federation", None,      "TypeScript", 1, "2025-08-19"),
    ("spike-rust-telemetry",  None,         "Rust",       1, "2026-01-27"),
    ("jenkins-shared-library", None,        "Groovy",     0, "2026-09-10"),
    ("archived-legacy-portal", None,        "PHP",        1, "2023-06-30"),
    ("dotfiles-platform-team", None,        "Shell",      0, "2026-05-04"),
    ("ml-notebooks-scratch",  None,         "Jupyter",    0, "2026-08-18"),
    ("vendor-poc-tms",        None,         "Java",       1, "2024-09-12"),
    ("interview-exercises",   None,         "Python",     0, "2026-02-14"),
]

# The monorepo: one repository, five subprojects — needs RepoScope (§3.3).
MONOREPO_SCOPES = [
    ("/services/notify",   "notify"),
    ("/services/tracking", "tracking"),
    ("/services/dock",     "dock"),
    ("/libs/common-auth",  "idbroker"),
    ("/libs/freight-model", None),
]

# SVN: three repositories, /trunk/<project> layout.  The legacy estate lives here.
SVN = [
    ("legacy-apps", [
        ("/trunk/claims",    "lnclaims"),
        ("/trunk/dispatch",  "lndispatch"),
        ("/trunk/ratetool",  "lnrate"),
        ("/trunk/vendordb",  "lnvendor"),
        ("/trunk/safetylog", "lnsafety"),
    ]),
    ("fleet-legacy", [
        ("/trunk/fleetnotes",   "lnfleet"),
        ("/trunk/inspection-v1", "inspect"),
    ]),
    ("corporate-legacy", [
        ("/trunk/hrforms",  "lnhr"),
        ("/trunk/opswiki",  "lnwiki"),
        ("/trunk/lms-v1",   "lms"),
        ("/trunk/procure-v2", "procure"),
    ]),
]


def github_repos():
    rows, n = [], 0
    for name, app_key, lang, archived, last in GITHUB:
        n += 1
        rows.append({
            "id": 7000 + n,
            "name": name,
            "full_name": f"meridian-freight/{name}",
            "html_url": f"https://github.example.com/meridian-freight/{name}",
            "default_branch": "main",
            "language": lang,
            "archived": bool(archived),
            "pushed_at": f"{last}T09:14:00Z",
            "description": (f"Part of {BY_KEY[app_key]['name']}." if app_key else ""),
            "topics": ([f"app-{app_key}"] if app_key else []),
        })
    n += 1
    rows.append({
        "id": 7000 + n, "name": "meridian-platform",
        "full_name": "meridian-freight/meridian-platform",
        "html_url": "https://github.example.com/meridian-freight/meridian-platform",
        "default_branch": "main", "language": "Go", "archived": False,
        "pushed_at": "2026-09-20T16:40:00Z",
        "description": "Monorepo: shared services and libraries.",
        "topics": ["monorepo"],
        "subprojects": [{"path": p, "hint": k} for p, k in MONOREPO_SCOPES],
    })
    return rows


def svn_repos():
    out = []
    for repo, projects in SVN:
        out.append({
            "repository": repo,
            "url": f"https://svn.example.com/svn/{repo}",
            "uuid": f"svn-{repo}",
            "paths": [
                {"path": p, "kind": "dir", "last_revision": 41000 + i * 37,
                 "last_changed": "2026-04-18T11:02:00Z",
                 "hint": BY_KEY[k]["name"] if k else None}
                for i, (p, k) in enumerate(projects)
            ],
        })
    return out


# ---------------------------------------------------------------------------
# Build artifacts.  Four ship with nothing governing them (§7).
# ---------------------------------------------------------------------------

ARTIFACTS = [
    # (coordinate, type, app_key or None, pipeline)
    ("registry.meridian/plm-client",          "container", "plm",        "plm/client-release"),
    ("com.meridian.plm:plm-server",           "jar",       "plm",        "plm/server-release"),
    ("com.meridian.plm:plm-protocol",         "jar",       "plm",        "plm/protocol-release"),
    ("com.meridian.billing:billing-core",     "jar",       "billing",    "billing/core"),
    ("com.meridian.billing:settlement",       "jar",       "billing",    "billing/settlement"),
    ("com.meridian.billing:adjustments",      "jar",       "billing",    "billing/adjustments"),
    ("registry.meridian/quote-engine",        "container", "quote",      "revenue/quote"),
    ("registry.meridian/customer-portal",     "container", "portal",     "digital/portal"),
    ("registry.meridian/portal-bff",          "container", "portal",     "digital/portal-bff"),
    ("meridian-driver-ios",                   "ipa",       "driverapp",  "mobile/ios-release"),
    ("meridian-driver-android",               "apk",       "driverapp",  "mobile/android-release"),
    ("registry.meridian/route-optimizer",     "container", "routeopt",   "or/route-opt"),
    ("com.meridian.wms:wms-core",             "jar",       "wms",        "warehouse/wms"),
    ("registry.meridian/wms-rf-client",       "container", "wms",        "warehouse/rf"),
    ("registry.meridian/telematics-ingest",   "container", "telematics", "fleet/telematics"),
    ("com.meridian.edi:edi-gateway",          "jar",       "edi",        "integration/edi"),
    ("com.meridian.customs:filing",           "jar",       "customs",    "integration/customs"),
    ("registry.meridian/rate-card",           "container", "ratecard",   "revenue/ratecard"),
    ("com.meridian.pay:gateway-adapter",      "jar",       "paygw",      "revenue/paygw"),
    ("registry.meridian/hr-portal",           "container", "hrportal",   "corp/hr"),
    ("com.meridian.hr:payroll-bridge",        "jar",       "payroll",    "corp/payroll"),
    ("registry.meridian/vendor-portal",       "container", "vendor",     "procurement/vendor"),
    ("registry.meridian/contract-repository", "container", "contracts",  "legal/contracts"),
    ("com.meridian.fleet:asset-tracking",     "jar",       "assets",     "fleet/assets"),
    ("com.meridian.fleet:maintenance",        "jar",       "maint",      "fleet/maint"),
    ("registry.meridian/compliance-reporting","container", "compliance", "risk/compliance"),
    ("registry.meridian/shipment-tracking",   "container", "tracking",   "digital/tracking"),
    ("registry.meridian/notification-service","container", "notify",     "digital/notify"),
    ("com.meridian.platform:identity-broker", "jar",       "idbroker",   "platform/idbroker"),
    ("registry.meridian/datalake-ingest",     "container", "datalake",   "data/lake-ingest"),
    ("registry.meridian/brokerage-match",     "container", "brokerage",  "revenue/brokerage"),
    ("registry.meridian/scanhub",             "container", "scanhub",    "platform/scanhub"),
    ("registry.meridian/flowmon",             "container", "flowmon",    "platform/flowmon"),
    # ungoverned: shipping, nothing owns them
    ("registry.meridian/edge-cache-shim",     "container", None,         "platform/edge-cache"),
    ("com.meridian.misc:report-exporter",     "jar",       None,         "data/report-export"),
    ("registry.meridian/partner-webhook-relay","container",None,         "integration/webhook-relay"),
    ("com.meridian.misc:legacy-batch-runner", "jar",       None,         "corp/legacy-batch"),
]


def jenkins_artifacts():
    rows, n = [], 0
    for coord, kind, app_key, pipeline in ARTIFACTS:
        n += 1
        rows.append({
            "id": f"JEN-{n:04d}",
            "coordinate": coord,
            "artifact_type": kind,
            "pipeline": pipeline,
            "last_build": f"2026-09-{10 + (n % 11):02d}T04:{(n * 7) % 60:02d}:00Z",
            "last_build_number": 400 + n * 3,
            "hint_app": BY_KEY[app_key]["name"] if app_key else None,
        })
    return rows


# ---------------------------------------------------------------------------

def write(name, obj):
    p = OUT / name
    p.write_text(json.dumps(obj, indent=2) + "\n", encoding="utf-8")
    n = len(obj) if isinstance(obj, list) else sum(len(v) for v in obj.values())
    print(f"  {p.relative_to(OUT.parent.parent)}  ({n} records)")


def main():
    print("Generating fixtures for Meridian Freight...")

    sn = servicenow_records()
    write("servicenow.json", sn)
    write("servicenow_v2.json", servicenow_records(drift=True))

    # A deliberately partial export: the first 40 rows only.  Submitting this as
    # `completeness: full` would strand the rest — that is the trap in §5.3.
    write("servicenow_partial_export.json", sn[:40])

    write("apex_itam.json", apex_records())
    write("github.json", github_repos())
    write("svn.json", svn_repos())
    write("jenkins.json", jenkins_artifacts())

    shadow = [a for a in APPS if a["csv"]]
    path = OUT / "shadow_apps.csv"
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["Application", "Owner", "Team", "Criticality", "Notes"])
        for a in shadow:
            w.writerow([a["name"], a["business_owner"], a["engineering_owner"],
                        a["criticality"], a["description"]])
    print(f"  mocks/fixtures/shadow_apps.csv  ({len(shadow)} records)")

    both = sum(1 for a in APPS if a["sn"] and a["apex"])
    print(f"\n  canonical applications      {len(APPS)}")
    print(f"  in ServiceNow               {sum(1 for a in APPS if a['sn'])}")
    print(f"  in Apex ITAM                {sum(1 for a in APPS if a['apex'])}"
          f"  (+{len(APEX_ONLY)} Apex-only records)")
    print(f"  in both (dedupe candidates) {both}")
    print(f"  disagreements between CMDBs {sum(1 for a in APPS if a.get('dis'))}")
    print(f"  shadow spreadsheet only     {sum(1 for a in APPS if a['csv'])}")
    print(f"  in no inventory source      {sum(1 for a in APPS if not a['sn'] and not a['csv'])}"
          "  -> become `inferred`")
    print(f"  legacy Notes estate         {sum(1 for a in APPS if a['notes_legacy'])}"
          "  -> one decision, many subjects")
    print(f"  unmapped repositories       {sum(1 for r in GITHUB if r[1] is None)}")
    print(f"  ungoverned build artifacts  {sum(1 for a in ARTIFACTS if a[2] is None)}")


if __name__ == "__main__":
    main()
