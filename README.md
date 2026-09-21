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

Design settled; first build slice in progress.

| Artifact | State |
|---|---|
| [DESIGN.md](DESIGN.md) | Complete — 12 sections, 40+ recorded decisions |
| [schema/](schema/) | Slice 1 — 24 tables, 6 views, SQLite + PostgreSQL |
| [api/openapi.yaml](api/openapi.yaml) | Slice 1 — 35 paths, 42 operations, 40 schemas |
| [mocks/](mocks/) | Simulated ServiceNow, Apex ITAM, GitHub, SVN, Jenkins, spreadsheet |
| Application | Not started |

**Slice 1 is inventory → mapping → coverage, on simulated data.** No SSDLC definitions, no
evidence collection, no credentials. The point is to make the problem and the proposed
solution arguable by people who will never read the design document, while changing course
is still cheap.

---

## Try it

```bash
python mocks/generate_fixtures.py     # deterministic fixtures
python mocks/serve.py                 # http://127.0.0.1:8900
curl localhost:8900/                  # index of every endpoint
```

```bash
python -c "import sqlite3,pathlib; sqlite3.connect('demo.db').executescript(pathlib.Path('schema/schema.sqlite.sql').read_text())"
```

[`mocks/README.md`](mocks/README.md) walks ten demonstrable scenarios against the fixture
data — tier selection, cross-CMDB disagreement, detected inventory omissions, shared
decision rationale, staleness after drift, the partial-export trap, SVN subtree scoping,
unmapped repositories, ungoverned artifacts, and decomposition.

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

---

## Layout

```
DESIGN.md              the design and its decision log
schema/
  schema.sqlite.sql    reference implementation dialect
  schema.postgres.sql  generated; differences documented in its header
api/
  openapi.yaml         the API contract
mocks/
  generate_fixtures.py canonical data and its projections into each source
  serve.py             simulated ServiceNow, Apex ITAM, GitHub, SVN, Jenkins
  fixtures/            generated, committed, reproducible
  README.md            the ten demo scenarios
```

The schema and the API contract are the durable artifacts. The UI is comparatively cheap to
build and cheap to rebuild — which is why the API is specified to support a UI we did not
write, and why there are no JSON columns for someone else to reverse-engineer.
