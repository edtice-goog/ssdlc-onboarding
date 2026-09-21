"""HTTP routing over the domain modules.

Implements api/openapi.yaml.  Standard library only: no framework, so the spec
is hand-maintained and is the contract rather than a generated artifact.
"""

import json
import mimetypes
import pathlib
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, unquote

from . import ingest, packets, registry, views
from .core import Refused, connect, one, rows

UI = pathlib.Path(__file__).parent / "ui"
PREFIX = "/api/v1"


def _lim(q, default=500):
    try:
        return max(1, min(1000, int(q.get("limit", [default])[0])))
    except (ValueError, TypeError):
        return default


def _b(v, default=None):
    if v is None:
        return default
    return str(v).lower() in ("1", "true", "yes")


class Router:
    def __init__(self):
        self.routes = []

    def add(self, method, pattern, fn):
        rx = re.compile("^" + re.sub(r"\{(\w+)\}", r"(?P<\1>[^/]+)", pattern) + "$")
        self.routes.append((method, rx, fn))

    def match(self, method, path):
        for m, rx, fn in self.routes:
            if m != method:
                continue
            hit = rx.match(path)
            if hit:
                return fn, {k: unquote(v) for k, v in hit.groupdict().items()}
        return None, None


R = Router()


def route(method, pattern):
    def deco(fn):
        R.add(method, pattern, fn)
        return fn
    return deco


# ----------------------------------------------------------------- sources

@route("GET", "/sources")
def _list_sources(con, q, body, **_):
    return ingest.list_sources(con, role=q.get("role", [None])[0],
                               kind=q.get("kind", [None])[0],
                               owner_team=q.get("owner_team", [None])[0])


@route("POST", "/sources")
def _create_source(con, q, body, **_):
    return ingest.create_source(con, **body), 201


@route("GET", "/sources/{sourceId}")
def _get_source(con, q, body, sourceId, **_):
    return ingest.get_source(con, sourceId)


@route("POST", "/sources/{sourceId}/retire")
def _retire(con, q, body, sourceId, **_):
    return ingest.retire_source(con, sourceId, rationale=body["rationale"],
                                retired_by=body["retired_by"])


@route("GET", "/sources/{sourceId}/inventory")
def _known(con, q, body, sourceId, **_):
    items = ingest.known_inventory(
        con, sourceId, scope_status=q.get("scope_status", [None])[0],
        include_absent=_b(q.get("include_absent", [None])[0], False),
        limit=int(q.get("limit", [500])[0]))
    return {"items": items, "next_cursor": None}


@route("POST", "/sources/{sourceId}/changeset")
def _changeset(con, q, body, sourceId, **_):
    return ingest.ingest(con, sourceId, body, mode="changeset"), 202


@route("POST", "/sources/{sourceId}/snapshot")
def _snapshot(con, q, body, sourceId, **_):
    return ingest.ingest(con, sourceId, body, mode="snapshot"), 202


# -------------------------------------------------------- code and build

@route("GET", "/repositories")
def _repos(con, q, body, **_):
    return {"items": registry.list_repositories(
        con, source_id=q.get("source_id", [None])[0],
        mapped=_b(q.get("mapped", [None])[0]),
        archived=_b(q.get("archived", [None])[0]),
        q=q.get("q", [None])[0], limit=_lim(q)), "next_cursor": None}


@route("GET", "/repositories/{repositoryId}")
def _repo(con, q, body, repositoryId, **_):
    r = one(con, "SELECT r.*, s.name AS source_name FROM repository r"
                 " JOIN source s ON s.id=r.source_id WHERE r.id=?", repositoryId)
    if not r:
        raise Refused("repository_not_found", "No such repository.",
                      subject_id=repositoryId, status=404)
    r["archived"] = bool(r["archived"])
    r["scopes"] = rows(con, "SELECT * FROM repo_scope WHERE repository_id=?", repositoryId)
    r["mappings"] = rows(con,
        "SELECT m.application_id, a.handle AS application_handle, m.managed_entity_id,"
        " e.name AS managed_entity_name, m.repo_scope_id, m.decision_id"
        " FROM repository_mapping m JOIN application a ON a.id=m.application_id"
        " LEFT JOIN managed_entity e ON e.id=m.managed_entity_id"
        " WHERE m.repository_id=?", repositoryId)
    r["mapped"] = bool(r["mappings"])
    r["attributes"] = rows(con, "SELECT key, value_type, value_text, value_number,"
                                " value_datetime, value_bool FROM repository_attribute"
                                " WHERE repository_id=?", repositoryId)
    return r


@route("POST", "/repositories/{repositoryId}/scopes")
def _scope(con, q, body, repositoryId, **_):
    return registry.add_scope(con, repositoryId, path_prefix=body["path_prefix"],
                              name=body["name"],
                              created_by=body.get("created_by", "api")), 201


@route("GET", "/build-artifacts")
def _artifacts(con, q, body, **_):
    return {"items": registry.list_artifacts(
        con, governed=_b(q.get("governed", [None])[0]),
        managed_entity_id=q.get("managed_entity_id", [None])[0],
        limit=_lim(q)), "next_cursor": None}


# ------------------------------------------------------------ applications

@route("GET", "/applications")
def _apps(con, q, body, **_):
    return {"items": registry.list_applications(
        con, origin=q.get("origin", [None])[0],
        status=q.get("status", ["active"])[0],
        onboarding_state=q.get("onboarding_state", [None])[0],
        q=q.get("q", [None])[0], limit=_lim(q)), "next_cursor": None}


@route("POST", "/applications")
def _create_app(con, q, body, **_):
    app_id = registry.create_application(
        con, name=body["name"], origin=body["origin"],
        created_by=body["created_by"], description=body.get("description"),
        rationale=body.get("rationale"))
    con.commit()
    return registry.get_application(con, app_id), 201


@route("GET", "/applications/{applicationId}")
def _app(con, q, body, applicationId, **_):
    return registry.get_application(con, applicationId, detail=True)


@route("GET", "/applications/{applicationId}/lineage")
def _lineage(con, q, body, applicationId, **_):
    return views.lineage(con, applicationId)


@route("POST", "/applications/{applicationId}/merge")
def _merge(con, q, body, applicationId, **_):
    return registry.merge_applications(con, applicationId, body["absorb_application_id"],
                                       rationale=body["rationale"],
                                       decided_by=body["decided_by"])


@route("GET", "/applications/{applicationId}/managed-entities")
def _entities(con, q, body, applicationId, **_):
    app = registry.get_application(con, applicationId)
    return registry.list_entities(con, app["id"])


@route("POST", "/applications/{applicationId}/managed-entities")
def _entity(con, q, body, applicationId, **_):
    return registry.create_entity(
        con, applicationId, name=body["name"], rationale=body.get("rationale", ""),
        created_by=body["created_by"], kind=body.get("kind"),
        build_artifact_ids=body.get("build_artifact_ids"),
        repository_ids=body.get("repository_ids")), 201


@route("GET", "/applications/{applicationId}/classification")
def _get_class(con, q, body, applicationId, **_):
    app = registry.get_application(con, applicationId)
    return rows(con, "SELECT id, axis, value, rationale, set_by, set_at"
                     " FROM classification_label WHERE application_id=?", app["id"])


@route("POST", "/applications/{applicationId}/classification")
def _set_class(con, q, body, applicationId, **_):
    return registry.classify(con, applicationId, axis=body["axis"], value=body["value"],
                             rationale=body["rationale"], set_by=body["set_by"]), 201


# ---------------------------------------------------------------- packets

@route("GET", "/decision-packets")
def _packets(con, q, body, **_):
    sql, args = "SELECT * FROM decision_packet WHERE 1=1", []
    if q.get("status"):
        sql += " AND status=?"; args.append(q["status"][0])
    return rows(con, sql + " ORDER BY created_at DESC", *args)


@route("POST", "/decision-packets")
def _packet(con, q, body, **_):
    return packets.create_packet(
        con, subject_type=body["subject_type"], purpose=body["purpose"],
        created_by=body["created_by"], source_id=body.get("source_id"),
        filter=body.get("filter"), suggested_rules=body.get("suggested_rules")), 201


@route("GET", "/decision-packets/{packetId}")
def _packet_get(con, q, body, packetId, **_):
    return packets.get_packet(con, packetId)


@route("GET", "/decision-packets/{packetId}/export")
def _packet_export(con, q, body, packetId, **_):
    fmt = q.get("format", ["markdown"])[0]
    ctype = {"markdown": "text/markdown", "csv": "text/csv",
             "json": "application/json"}.get(fmt, "text/plain")
    return ("__raw__", packets.export(con, packetId, fmt), ctype)


@route("POST", "/decision-packets/{packetId}/return")
def _packet_return(con, q, body, packetId, **_):
    return packets.submit_return(con, packetId, body["returned_raw"],
                                 body.get("actor", "api"))


@route("GET", "/decision-packets/{packetId}/diff")
def _packet_diff(con, q, body, packetId, **_):
    return packets.diff(con, packetId)


@route("POST", "/decision-packets/{packetId}/apply")
def _packet_apply(con, q, body, packetId, **_):
    return packets.apply_packet(con, packetId, body["applied_by"],
                                body.get("only_record_keys"))


# -------------------------------------------------------------- decisions

@route("GET", "/decisions")
def _decisions(con, q, body, **_):
    return {"items": views.list_decisions(con, kind=q.get("kind", [None])[0],
                                          decided_by=q.get("decided_by", [None])[0],
                                          limit=_lim(q, 200)), "next_cursor": None}


@route("GET", "/decisions/stale-subjects")
def _stale(con, q, body, **_):
    return views.stale_subjects(con, subject_type=q.get("subject_type", [None])[0],
                                limit=_lim(q, 200))


@route("GET", "/decisions/{decisionId}")
def _decision(con, q, body, decisionId, **_):
    return views.get_decision(con, decisionId)


@route("POST", "/decisions/{decisionId}/reaffirm")
def _reaffirm(con, q, body, decisionId, **_):
    return views.reaffirm(con, decisionId, body["subject_ids"], body["decided_by"],
                          body.get("note"))


# --------------------------------------------------------- coverage, audit

@route("GET", "/coverage/summary")
def _coverage(con, q, body, **_):
    return views.coverage(con)


@route("GET", "/coverage/orphans/{orphanType}")
def _orphans(con, q, body, orphanType, **_):
    return {"items": views.orphans(
        con, orphanType,
        include_dispositioned=_b(q.get("include_dispositioned", [None])[0], False),
        limit=_lim(q)), "next_cursor": None}


@route("PUT", "/coverage/orphans/{orphanType}/{subjectId}/disposition")
def _dispose(con, q, body, orphanType, subjectId, **_):
    return views.dispose_orphan(con, subjectId, status=body["status"],
                                reason=body["reason"], set_by=body["set_by"],
                                until=body.get("until"))


@route("GET", "/audit")
def _audit(con, q, body, **_):
    return {"items": views.audit_log(
        con, subject_id=q.get("subject_id", [None])[0],
        subject_type=q.get("subject_type", [None])[0],
        actor=q.get("actor", [None])[0], since=q.get("since", [None])[0],
        decision_id=q.get("decision_id", [None])[0],
        packet_id=q.get("decision_packet_id", [None])[0],
        limit=_lim(q, 200)), "next_cursor": None}


@route("GET", "/sync-runs")
def _syncs(con, q, body, **_):
    sql = ("SELECT r.*, s.name AS source_name, s.role AS source_role FROM sync_run r"
           " LEFT JOIN source s ON s.id=r.source_id WHERE 1=1")
    args = []
    if q.get("source_id"):
        sql += " AND r.source_id=?"; args.append(q["source_id"][0])
    if q.get("role"):
        sql += " AND s.role=?"; args.append(q["role"][0])
    return rows(con, sql + " ORDER BY r.started_at DESC LIMIT 100", *args)


@route("GET", "/sync-runs/{syncRunId}")
def _sync(con, q, body, syncRunId, **_):
    r = one(con, "SELECT r.*, s.name AS source_name, s.role AS source_role FROM sync_run r"
                 " LEFT JOIN source s ON s.id=r.source_id WHERE r.id=?", syncRunId)
    if not r:
        raise Refused("sync_run_not_found", "No such sync run.", status=404)
    r["ops"] = rows(con, "SELECT op, record_key, change_class, changed_fields, detail"
                         " FROM sync_op WHERE sync_run_id=? ORDER BY rowid", syncRunId)
    return r


# ------------------------------------------------------------------ server

class Handler(BaseHTTPRequestHandler):
    server_version = "SSDLCOnboarding/0.1"
    db_path = "ssdlc.db"

    def _send(self, obj, status=200, ctype="application/json"):
        body = obj if isinstance(obj, bytes) else \
            (obj.encode() if isinstance(obj, str) else json.dumps(obj, indent=2).encode())
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _static(self, path):
        rel = "index.html" if path in ("/", "") else path.lstrip("/")
        f = (UI / rel).resolve()
        if not str(f).startswith(str(UI.resolve())) or not f.is_file():
            return self._send({"code": "not_found", "message": "No such page."}, 404)
        ctype = mimetypes.guess_type(str(f))[0] or "application/octet-stream"
        self._send(f.read_bytes(), 200, ctype)

    def _handle(self, method):
        u = urlparse(self.path)
        if not u.path.startswith(PREFIX):
            if method == "GET":
                return self._static(u.path)
            return self._send({"code": "not_found", "message": "No such endpoint."}, 404)
        path = u.path[len(PREFIX):] or "/"
        fn, params = R.match(method, path)
        if not fn:
            return self._send({"code": "not_found", "message": f"No route {method} {path}.",
                               "remedy": "See api/openapi.yaml for the contract."}, 404)
        body = {}
        n = int(self.headers.get("Content-Length") or 0)
        if n:
            raw = self.rfile.read(n).decode()
            try:
                body = json.loads(raw)
            except ValueError:
                return self._send({"code": "bad_json", "message": "Body is not valid JSON.",
                                   "detail": raw[:200]}, 400)
        con = connect(self.db_path)
        try:
            result = fn(con, parse_qs(u.query), body, **params)
            status = 200
            if isinstance(result, tuple) and len(result) == 3 and result[0] == "__raw__":
                return self._send(result[1], 200, result[2])
            if isinstance(result, tuple):
                result, status = result
            self._send(result, status)
        except Refused as e:
            self._send(e.payload(), e.status)
        except KeyError as e:
            self._send({"code": "missing_field", "message": f"Required field {e} missing.",
                        "remedy": "See api/openapi.yaml for the request shape."}, 400)
        except Exception as e:                               # noqa: BLE001
            self._send({"code": "internal_error", "message": str(e),
                        "detail": f"{type(e).__name__} handling {method} {path}"}, 500)
        finally:
            con.close()

    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")

    def do_PUT(self):
        self._handle("PUT")

    def log_message(self, fmt, *args):
        if self.path.startswith(PREFIX):
            print(f"  {self.command} {self.path}")


def serve(host="127.0.0.1", port=8080, db="ssdlc.db"):
    Handler.db_path = db
    connect(db).close()
    srv = ThreadingHTTPServer((host, port), Handler)
    print(f"SSDLC Onboarding on http://{host}:{port}/   (db: {db})")
    print(f"API under {PREFIX}; contract in api/openapi.yaml")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
