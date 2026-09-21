# SSDLC Onboarding & Tracking

Establish, for every application an organization is responsible for, a documented and
continuously-observed answer to three questions:

1. What do we have? (applications, code, build outputs)
2. What SSDLC is supposed to be running against each of them?
3. What is the current state of evidence that it is actually running — **including evidence
   that action is being taken** on what it finds?

A system of record and measurement. It records what exists, what was decided, who decided
it, and what evidence has been observed. It does not render verdicts, and it does not make
the decisions — those belong to expert practitioners. See [DESIGN.md §1.1](DESIGN.md) for
the explicit non-goals.

Companion to [`ssdlc-mythos`](../ssdlc-mythos), which supplies the thesis: the two-loop
model, the eight-phase reference frame, the stage-cost placement gradient, the tool-property
interface, and the classification axes.

---

## Status

Design settled. The first build slice is working end to end on simulated data.

| Artifact | State |
|---|---|
| [DESIGN.md](DESIGN.md) | Complete — 12 sections, 60+ recorded decisions |
| [schema/](schema/) | 24 tables, 7 views, SQLite reference + PostgreSQL |
| [api/openapi.yaml](api/openapi.yaml) | 32 paths, 37 operations, 36 schemas |
| [mocks/](mocks/) | Simulated ServiceNow, Apex ITAM, GitHub, SVN, Jenkins, spreadsheet |
| [app/](app/) | Working — API, domain logic and UI over the whole slice |
| [demo/](demo/) | End-to-end walkthrough against the simulated sources |

**Slice 1 is inventory → mapping → coverage, on simulated data.** No SSDLC definitions, no
evidence collection, no credentials. The point is to make the problem and the proposed
solution arguable by people who will never read the design document, while changing course
is still cheap.

---

## Try it

No install step — standard library only.

```bash
python -m demo.seed --db demo.db
```

Walks the whole slice against the simulated sources and prints a narrative: register
sources → ingest → decision packet → decide → diff → apply → map → subtree scopes →
govern artifacts → detected omissions → decompose → re-sync with drift → the
partial-export trap → coverage → lineage.

```bash
python -m app.serve --db demo.db      # http://127.0.0.1:8080
```

To drive the simulated back ends over HTTP instead of reading their fixtures:

```bash
python mocks/serve.py                 # http://127.0.0.1:8900 — curl / for the index
```

What the demo ends with, on 52 fictional applications at a logistics operator:

| | |
|---|---|
| applications | 37 — 35 federated, **2 inferred** (in no CMDB at all) |
| inventory records | 81 across three sources — 59 managed, 15 rejected, 7 undecided, **4 stale** |
| field conflicts between the two CMDBs | 7, surfaced rather than silently resolved |
| repositories | 70 — 50 mapped, 20 with no declared home |
| build artifacts | 37 — 33 governed, **4 shipping with nothing governing them** |

[`mocks/README.md`](mocks/README.md) explains the ten scenarios the fixture data is shaped
around, and why each one is there.

---

## The shape of it

```
InventorySource (CMDB | lightweight list)
      |-- InventoryRecord ---+
                             +--> Application (golden record, stable ID)
InventorySource (CMDB #2) ---+            |
      |-- InventoryRecord                 | decomposes into (1..N, default 1)
                                          v
CodeSystem (github | svn | ...)     ManagedEntity  --1:1--> SsdlcProcessInstance
      |-- Repository ------------------>  |                        |
            |-- RepoScope (subtree)       |                        +-- StepInstance
                                          | 0..N                         |
BuildSystem ---------------------> BuildArtifact                         +- EvidenceBinding
                                          |                              +- EvidenceRecord*
                                          +-- Build (occurrence)
```

A few load-bearing choices, each argued in DESIGN.md:

**The application ID is ours.** Organizations have more than one CMDB and some applications
are in none of them, so a golden record with a stable opaque ID is unavoidable. We federate
exactly one entity type and six attributes — not a CMDB. Applications discovered in no
inventory source are recorded as `inferred`, and that list is a report handed back to the
CMDB owners.

**The managed entity is the governed unit, not the build artifact.** Whether an SSDLC runs
against a JAR or its parent does not matter; what matters is that the boundary is documented
with a written rationale and the coverage is measurable.

**Evidence is state on a ladder, not a verdict.** L0 declared → L1 defined → L2 executed →
L3 produced → **L4 dispositioned** → **L5 closed**, with trust tier carried as a separate
axis. L4 and L5 are the point: they are where "action is being taken" lives. There is no
`compliant` boolean in the schema.

**We ask tools questions; we do not warehouse findings.** An SLA policy declared in the
organization's own SSDLC renders into a question the tool can answer — *"any component with
CVSS ≥ 8.0 known for more than 48 hours?"* — and we store the answer with a capped sample of
references. No `Finding` table.

**Judgment over long lists goes to a Claude session, not a rules engine.** We export a
candidate list, a person reasons about it with an LLM, and hands back decisions we diff and
apply. One mechanism serves scope selection, repo mapping, decomposition and dedupe.

**We hold no inventory credentials.** Retrieval is done by a companion skill in the user's
own session, by API token, CSV export, browser automation, or typing — all recorded and
distinguished by trust tier. Evidence collection *may* use narrowly-scoped read-only tokens,
because fetching a scan report involves no judgment. Inventory does.

**Code the happy path; infer the rest.** Rare paths are made recoverable by emitting rich,
self-describing context rather than by writing code that almost never runs. Retiring a source
writes an artifact of everything it reparented; there is no restore routine, because the
artifact plus the API is enough for an agent to reconstruct from.

---

## Layout

```
DESIGN.md              the design and its decision log
schema/
  schema.sqlite.sql    reference implementation dialect
  schema.postgres.sql  generated; differences documented in its header
api/
  openapi.yaml         the API contract
app/
  core.py              ids, database, subject registry, audit
  ingest.py            sources, sync, change classification, staleness
  registry.py          applications, survivorship, entities, mappings
  packets.py           the export / decide / diff / apply round trip
  views.py             coverage, orphans, decisions, lineage
  api.py               HTTP routing over the domain modules
  ui/index.html        single-file UI, no build step
mocks/
  generate_fixtures.py canonical data and its projections into each source
  serve.py             simulated ServiceNow, Apex ITAM, GitHub, SVN, Jenkins
  fixtures/            generated, committed, reproducible
  README.md            the ten demo scenarios
demo/
  seed.py              the end-to-end walkthrough
```

The schema and the API contract are the durable artifacts. The UI is comparatively cheap to
build and cheap to rebuild — which is why the API is specified to support a UI we did not
write, and why there are no JSON columns for someone else to reverse-engineer.
