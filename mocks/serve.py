#!/usr/bin/env python3
"""
Simulated source systems for the SSDLC Onboarding demo.

Standard library only — no install step.  Serves four fake back ends that behave
enough like the real things (field names, pagination style, auth header) that the
ingestion path is exercised honestly rather than hand-waved:

    ServiceNow   /servicenow/api/now/table/<class>    sysparm_limit / sysparm_offset
    Apex ITAM    /apex/v2/assets                      page / page_size
    GitHub       /github/api/v3/orgs/<org>/repos      page / per_page + Link header
    Subversion   /svn/ , /svn/<repo>                  path listing
    Jenkins      /jenkins/api/artifacts               plain list
    Shadow list  /files/shadow_apps.csv               a spreadsheet someone emailed

Usage:
    python mocks/serve.py [--port 8900]

Then, for example:
    curl localhost:8900/servicenow/api/now/table/cmdb_ci_business_app?sysparm_limit=5
    curl localhost:8900/servicenow/api/now/table/cmdb_ci_business_app?state=v2
"""

import argparse
import csv
import io
import json
import pathlib
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

FIXTURES = pathlib.Path(__file__).parent / "fixtures"

# Any non-empty bearer token is accepted; the point is that the shape is right,
# not that we are testing auth.  Requests with no token get a 401 so the skill's
# credential handling is exercised.
REQUIRE_AUTH = {"/servicenow", "/apex", "/github", "/jenkins"}


def load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class Handler(BaseHTTPRequestHandler):
    server_version = "MeridianMock/0.1"

    # ---------------------------------------------------------------- helpers

    def _json(self, obj, status=200, headers=None):
        body = json.dumps(obj, indent=2).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _text(self, body, status=200, ctype="text/plain; charset=utf-8"):
        raw = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _authed(self, path):
        if not any(path.startswith(p) for p in REQUIRE_AUTH):
            return True
        if self.headers.get("Authorization"):
            return True
        self._json({"error": "missing Authorization header",
                    "hint": "any non-empty bearer token is accepted by the mock"}, 401)
        return False

    def log_message(self, fmt, *args):
        print(f"  {self.command} {self.path}")

    # ------------------------------------------------------------------ routes

    def do_GET(self):
        u = urlparse(self.path)
        path, q = u.path.rstrip("/") or "/", parse_qs(u.query)

        if not self._authed(path):
            return

        if path == "/":
            return self._text(INDEX, ctype="text/plain; charset=utf-8")

        # --- ServiceNow -----------------------------------------------------
        m = re.fullmatch(r"/servicenow/api/now/table/(\w+)", path)
        if m:
            ci_class = m.group(1)
            state = (q.get("state") or ["v1"])[0]
            rows = load("servicenow_v2.json" if state == "v2" else "servicenow.json")
            rows = [r for r in rows if r["sys_class_name"] == ci_class]
            limit = int((q.get("sysparm_limit") or [100])[0])
            offset = int((q.get("sysparm_offset") or [0])[0])
            page = rows[offset:offset + limit]
            return self._json({"result": page},
                              headers={"X-Total-Count": str(len(rows))})

        if path == "/servicenow/api/now/table":
            rows = load("servicenow.json")
            classes = sorted({r["sys_class_name"] for r in rows})
            return self._json({"result": [
                {"sys_class_name": c,
                 "count": sum(1 for r in rows if r["sys_class_name"] == c)}
                for c in classes]})

        # A genuinely partial export, for demonstrating the completeness trap.
        if path == "/servicenow/export/partial":
            return self._json({"result": load("servicenow_partial_export.json"),
                               "note": "Filtered export. 40 of 68 records."})

        # --- Apex ITAM ------------------------------------------------------
        if path == "/apex/v2/assets":
            rows = load("apex_itam.json")
            if "type" in q:
                rows = [r for r in rows if r["asset_type"] == q["type"][0]]
            size = int((q.get("page_size") or [100])[0])
            page_no = int((q.get("page") or [1])[0])
            start = (page_no - 1) * size
            chunk = rows[start:start + size]
            return self._json({
                "data": chunk,
                "page": {"number": page_no, "size": size, "total": len(rows),
                         "pages": max(1, -(-len(rows) // size))},
            })

        # --- GitHub ---------------------------------------------------------
        m = re.fullmatch(r"/github/api/v3/orgs/([\w-]+)/repos", path)
        if m:
            rows = load("github.json")
            per = int((q.get("per_page") or [30])[0])
            page_no = int((q.get("page") or [1])[0])
            start = (page_no - 1) * per
            chunk = rows[start:start + per]
            headers = {}
            if start + per < len(rows):
                headers["Link"] = (f'<{self.path.split("?")[0]}?page={page_no + 1}'
                                   f'&per_page={per}>; rel="next"')
            return self._json(chunk, headers=headers)

        m = re.fullmatch(r"/github/api/v3/repos/([\w-]+)/([\w.-]+)", path)
        if m:
            name = m.group(2)
            for r in load("github.json"):
                if r["name"] == name:
                    return self._json(r)
            return self._json({"message": "Not Found"}, 404)

        # --- Subversion -----------------------------------------------------
        if path == "/svn":
            return self._json([{"repository": r["repository"], "url": r["url"],
                                "path_count": len(r["paths"])} for r in load("svn.json")])

        m = re.fullmatch(r"/svn/([\w-]+)", path)
        if m:
            for r in load("svn.json"):
                if r["repository"] == m.group(1):
                    return self._json(r)
            return self._json({"error": "no such repository"}, 404)

        # --- Jenkins --------------------------------------------------------
        if path == "/jenkins/api/artifacts":
            return self._json({"artifacts": load("jenkins.json")})

        # --- the spreadsheet someone emailed --------------------------------
        if path == "/files/shadow_apps.csv":
            return self._text((FIXTURES / "shadow_apps.csv").read_text(encoding="utf-8"),
                              ctype="text/csv; charset=utf-8")

        return self._json({"error": "no such endpoint", "path": path,
                           "hint": "GET / for the index"}, 404)


INDEX = """
Meridian Freight — simulated source systems
===========================================
Fixtures for the SSDLC Onboarding demo.  Nothing here is real.

ServiceNow  (two CMDBs, deliberately disagreeing)
  GET /servicenow/api/now/table                       list CI classes and counts
  GET /servicenow/api/now/table/cmdb_ci_business_app  the applications
  GET /servicenow/api/now/table/cmdb_ci_service       business services (coarser tier)
  GET /servicenow/api/now/table/cmdb_ci_appl          deployed instances (finer tier)
  GET /servicenow/api/now/table/cmdb_ci_server        infrastructure (never in scope)
      ?sysparm_limit= &sysparm_offset=
      ?state=v2                                       later state: 4 material changes
  GET /servicenow/export/partial                      40 of 68 rows -- the completeness trap

Apex ITAM
  GET /apex/v2/assets?type=Application&page=&page_size=

GitHub
  GET /github/api/v3/orgs/meridian-freight/repos?page=&per_page=
  GET /github/api/v3/repos/meridian-freight/<name>

Subversion
  GET /svn                                            repositories
  GET /svn/<repo>                                     /trunk/<project> paths

Jenkins
  GET /jenkins/api/artifacts

Shadow spreadsheet
  GET /files/shadow_apps.csv

All endpoints except /files require any non-empty Authorization header.
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8900)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()

    missing = [f for f in ("servicenow.json", "apex_itam.json", "github.json",
                           "svn.json", "jenkins.json", "shadow_apps.csv")
               if not (FIXTURES / f).exists()]
    if missing:
        raise SystemExit(f"Missing fixtures: {missing}\n"
                         f"Run:  python mocks/generate_fixtures.py")

    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Meridian Freight mock sources on http://{args.host}:{args.port}/")
    print("Ctrl-C to stop.\n")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")


if __name__ == "__main__":
    main()
