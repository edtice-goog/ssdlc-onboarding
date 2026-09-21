# SSDLC Onboarding & Tracking — Design

**Status:** Draft, in discussion
**Last updated:** 2026-09-21
**Companion project:** `C:\Data\ssdlc-mythos` (thesis, two-loop model, eight-phase frame, placement gradient, tool properties, classification axes)

---

## 1. Purpose

Establish, for every application an organization is responsible for, a documented and continuously-observed answer to three questions:

1. What do we have? (applications, code, build outputs)
2. What SSDLC is supposed to be running against each of them?
3. What is the current state of evidence that it is actually running — including evidence that action is being taken on what it finds?

The system is a **system of record and measurement**. It records what exists, what was decided, who decided it, and what evidence has been observed. It does not render verdicts.

The decisions are made by expert practitioners. Our job is to record them and confirm they are implemented.

### 1.1 What this is not

Written down because each of these is a plausible-sounding feature request that would change what the product is.

- **Not a decision-making tool.** We do not classify applications, design SSDLCs, recommend controls, or walk anyone through those choices. The user classifies their own applications and authors their own SSDLC — ideally against the properties in `ssdlc-mythos` or a set they have adapted — and hands us the result. We record the decision and measure against it.
- **Not a CMDB.** One federated entity type, a fixed small attribute set (P7).
- **Not a findings warehouse.** We ask the tools questions and store the answers (§3.6.1). The tools remain the system of record for findings.
- **Not a scanner, orchestrator, or gate.** We observe controls; we do not run them and we do not enforce them.
- **Not an anti-malfeasance system.** Detecting whether someone is gaming their own metrics is an organizational management problem (P1). Our data would support such analysis; building it is somebody else's project.

Several of these are well served by **companion skills** rather than application features — see §11.

---

## 2. Design principles

**P1 — We record state, not judgment.**
Evidence is stored as observed state on a ladder (§3.6). There is no `compliant` boolean anywhere in the schema. Target vs. attained is displayed side by side; the reader judges. Anti-gaming detection is explicitly out of scope — it is an organizational management problem, not a software one. We store enough fact and provenance that someone *could* build such analysis on our data later.

**P2 — Judgment work is delegated to conversational AI, not encoded as rules.**
Where a decision requires judgment over a large list (which CMDB records matter, which repo belongs to which application), we do not build a rules engine. We produce a candidate list, the user reasons about it in a Claude chat session, and hands the result back. See §4. Consequences: no rules DSL to build or debug; no credentials or data-egress policy for us to own; the user controls exactly what is shared.

**P3 — Provenance is the deliverable.**
Every application ID, mapping, scope decision and evidence record carries an auditable lineage: who, when, from what input, on what basis. The lineage view (§6) is a first-class feature, not a debug log.

**P4 — Boundaries are documented human decisions, not derived facts.**
Where the SSDLC boundary falls (which build outputs are governed together) is declared by a person with a written rationale. We do not infer it.

**P5 — Nothing blocks onboarding.**
Every step has a working default. Applications get a default managed entity; managed entities may have no build artifacts; unknown states are recordable rather than erroneous. Refinement is always optional and always later.

**P6 — Inventory is skill-mediated and never automated; evidence retrieval may be.**
The asymmetry is principled rather than arbitrary: **inventory requires judgment, evidence retrieval does not.** Deciding whether `BSVC00123` is an application we should manage is a judgment call that will be inferred wrongly if automated. Fetching a scan report is mechanical.

So:

- **Inventory and mapping** — performed by a companion Claude skill in the user's own session, under the user's own access, by whatever means they have (§5.1). We store no inventory credentials. A human is in the loop every time, including in the UI.
- **Evidence** — may use narrowly-scoped, read-only credentials held by the application (§5.6), because there is no judgment to preserve. Least privilege is documented per collector and enforced by asking for only that scope.

The application owns the data model, the decisions, the diff and the audit trail. The skill owns getting data out of the world where judgment is involved.

**P8 — Code the happy path; infer the rest.**
Conventional applications spend a large share of their code on paths that almost never run: restore routines, rollback, exhaustive error recovery, edge-case reconciliation. That code is expensive to write, rarely exercised, and frequently wrong the one time it matters.

We code the common path and make the uncommon one **recoverable by inference** instead. Two obligations follow, and they are not optional — they are what earns the right to skip the code:

1. **Emit enough context.** Destructive or unusual operations write rich, self-describing artifacts — plain text or JSON that a person or an LLM can act on *without reading our source*. Failures say what was attempted, against what, with what result, in prose.
2. **Prefer uniform models to special cases.** A special case is a thing every consumer must be told about. The no-null-source decision (§3.0) is an instance: one query path an LLM can reason about beats a documented exception it has to learn.

Worked example: removing a source writes an artifact recording everything that was reparented (§3.0.2). We do not write a restore routine. If a catastrophe needs undoing, the artifact plus the API is enough for an agent to reconstruct it — and that path is rare enough that inference at the time beats code maintained forever against the day it is needed.

This is a sibling of P2. P2 delegates *judgment*; P8 delegates *rare paths*. Both trade code we would have to maintain for context we have to emit, which is the cheaper obligation.

**P7 — We federate one entity type, not a CMDB.**
We own a fixed, small attribute set for applications (§3.2). No infrastructure CIs, no dependency graphs, no change records, no discovery. If we find ourselves adding fields, that is the signal we are drifting.

---

## 3. Domain model

### 3.0 Two registries

Everything else hangs off two registry tables. They exist because the alternative —
`(subject_type, subject_id)` pairs with no foreign key — is unenforceable, and this is a
system whose entire value proposition is provenance (P3).

**`source`** — every connected system, with a stable id: CMDBs, spreadsheets, code hosts,
build servers. Referenced by every row that came out of it and by every sync run.

**One registration per system *per role*.** A GitHub instance serving both repositories and
Actions builds is registered twice, under two ids. This is not an oversight. CMDB scoping
and repository/build mapping are done by different people, and a shared registration means
their edits, sync cadences and staleness collide. Duplicate rows are cheap; contention
between teams is not. `role` is immutable, and `name` is unique within a role rather than
globally.

**`subject`** — every entity that can be decided about, dispositioned, mapped or audited:
sources, inventory records, applications, managed entities, repositories, repo scopes,
build artifacts. Each underlying table's primary key is a foreign key *to* `subject`, so
registration is not optional.

This buys three things:

- `decision_subject`, `orphan_disposition` and `audit_event` get real foreign keys instead
  of unenforceable polymorphic pairs
- one join path for rendering a subject's name, instead of a six-way `CASE` repeated across
  the decision, stale-subject, lineage and four orphan views
- every subject resolves to a source through one join, with no special cases

**There is no null source.** Rows this application owns rather than federates —
applications, managed entities — belong to a seeded **Internal Register** source
(`role: internal`, `is_system: true`, undeletable, never synced). A null would be a special
case every consumer has to be told about; a source of kind `internal` is queryable by the
same code path as ServiceNow, which matters most for a skill reasoning over the API (P2)
rather than for us.

**A source is configuration, not a governed entity**, so it is deliberately *not* a subject:
nothing makes a scope decision about a source, maps it, or marks it an orphan. That is also
forced — `source.id → subject.id` with `subject.source_id` NOT NULL is circular, and the
first row could never be inserted. Audit events about source setup carry `audit_event.source_id`
instead.

### 3.0.2 Unlinking a source

Removing a source does not remove what came from it. Its subjects are **reparented to the
Internal Register**: an inventory record from a decommissioned ServiceNow instance becomes
an internally-maintained record, a repository from a retired Git host becomes an
internally-maintained repository. Application IDs, `application_source_link` rows, mappings,
decisions and audit history all survive untouched.

This is a one-line rule with no special cases, and it is only possible *because* there is no
null source. With nullable `source_id` we would have had to invent semantics for "used to
come from somewhere"; with the Internal Register, reparenting is an `UPDATE`.

Two rules around it:

- **We do not delete data without confirmation.** Removing a source is not a way to delete
  its entities. Anyone who wants the entities gone removes them first, deliberately, and
  that is its own decision with its own rationale.
- **Source removal writes an artifact** — a plain text/JSON file recording every reparented
  subject with its id, display name, former source, and former external id, plus the source
  configuration itself. It exists so a catastrophe is recoverable.

We do not write a restore routine (P8). The artifact plus the API is enough for an agent to
reconstruct the prior state, and that path is rare enough that inference at the time beats
code maintained forever against the day it is needed.

### 3.0.1 Materialized fields and the single-writer rule

Four fields cache a value derivable by join, because the derivation is a multi-table join
every list query would otherwise run:

| Field | True source |
|---|---|
| `subject.display_name` | the underlying row |
| `inventory_record.scope_status` / `scope_decision_id` / `scope_stale` | `decision` + `decision_subject` |
| `application.business_owner` / `criticality` / `lifecycle_state` and peers | `attribute_resolution` |
| `application.onboarding_state` | managed entities and mappings |

Each has **exactly one writer**, and that writer updates it in the **same transaction** that
writes the source of truth. Nothing else writes them. If a fifth such field appears it
follows the same rule or it does not ship. The rule is stated in the schema header, not just
here, because the person who adds the fifth one will be reading the schema.

### 3.1 The spine

```
                      subject  (registry: everything decidable / auditable)
                         ^  ^  ^  ^  ^  ^
                         |  |  |  |  |  |
source [role=inventory] -+  |  |  |  |  +- build_artifact
      |-- InventoryRecord --+  |  |  |          ^
                             +--> Application (golden record, stable ID)
source [role=inventory] -----+            |
      |-- InventoryRecord                 | decomposes into (1..N, default 1)
                                          v
source [role=code] ---------------> ManagedEntity --1:1--> SsdlcProcessInstance
      |-- Repository ------------------>  |                        |
            |-- RepoScope (subtree)       |                        +-- StepInstance
                                          | 0..N                         |
source [role=build] --------------> BuildArtifact                        +- EvidenceBinding
                                          |                              +- EvidenceRecord*
                                          +-- Build (occurrence)
```

Everything below `subject` in that diagram is also *in* `subject` — the registry is what
gives `decision_subject`, `orphan_disposition` and `audit_event` real foreign keys (§3.0).

### 3.2 Inventory and application federation

We are, unavoidably, a **CMDB federation** for one entity type. The cost is bounded by owning only these attributes:

| Field | Purpose |
|---|---|
| name, aliases, description | identity and search |
| business owner, engineering owner/team | routing |
| criticality / tier | prioritization, onboarding order |
| lifecycle state | dev / production / sunsetting / retired |
| data & regulatory flags | context the user classifies against (§3.5.3) |

Everything else the source carries is retained as typed attributes — `InventoryRecordAttribute {record_id, key, value_type, value_text, value_number, value_datetime, value_bool}` — displayed and searchable but not promoted into the model (§9.2). The verbatim source payload lives in the sync archive (`SyncRun.raw_payload_ref`), not in a column.

**Application ID.** Opaque immutable PK (ULID) plus a short human handle (`APP-0427`). Never reused, never derived from a CMDB key. All downstream references use the PK, so a CMDB record being re-keyed or deleted does not orphan accumulated evidence.

**Origins.** An Application is created one of three ways:

- `federated` — from an in-scope CMDB/list record
- `manual` — entered by a person; in no inventory source
- `inferred` — created from an unattached repository or shipping artifact

`inferred` applications are **detected inventory omissions**. The list of them is a report handed back to the CMDB owners, and it is one of the system's primary outputs.

**Federation mechanics.**

- `ApplicationSourceLink {application_id, inventory_record_id, origin, decided_by, ...}` — many records to one application. No `contains`/`part_of` relation: the user chooses which CI tier to ingest at source setup (§3.2.1), so records are already at application granularity. Genuine cross-source granularity mismatches are resolved by merge/split.
- Field-level survivorship: ordered source priority per field, plus per-application manual override. The resolved value is materialized on `Application`; `AttributeResolution` records which source won each field. Inter-CMDB conflicts surface as visible rows.
- Records never hard-delete: `last_seen`, `absent_since`. An application whose last source record disappears becomes `orphaned` and goes to review — retired, or source error?
- Merge is non-destructive: `ApplicationAlias {alias_id -> canonical_id}`; retired IDs resolve forever. Split moves managed entities and mappings, leaving an audit event.
- Cross-source dedupe (`same_as`) uses the same decision-packet flow as every other judgment call.

#### 3.2.1 Source setup

`connect` → `choose which CI classes/tiers to ingest` → `ingest all records at that tier` → `decide which are in scope for SSDLC management`

Tier selection is user configuration. We do not infer which tier represents an "application." Ingested-but-not-in-scope is a retained state, not a deletion; if a record is later brought into scope its full history is already present.

The in-scope decision is made via a decision packet (§4).

### 3.3 Code sources

- Code hosts are `source` rows with `role = code` (§3.0); no stored credentials (P6)
- `Repository {source_id, external_id, name, url, default_branch, archived, last_commit_at, primary_language}` plus `RepositoryAttribute` for the source-specific tail (§9.2)
- `RepoScope {repository_id, path_prefix, name}` — optional subtree. Required for SVN (`/trunk/projectA`) and monorepos, where the repository is not the finest useful grain.

Repositories (or scopes) map to managed entities. The AI first pass may propose repo → Application coarsely; the refinement is repo/scope → ManagedEntity.

### 3.4 Managed entities and build artifacts

**ManagedEntity is the governed unit.** It carries exactly one SSDLC process instance. The build artifact is not the governed unit — it is an evidence anchor.

```
Application  1 --> N  ManagedEntity  1 --1  SsdlcProcessInstance
                          |
                          +--> 0..N  BuildArtifact --> N Build
```

Worked example:

```
foo.app (APP-0427)
+-- ManagedEntity "foo.app core"     -> SSDLC instance #1
|     rationale: "built and released together from the monorepo"
|     artifacts: foo.jar, bar.jar
+-- ManagedEntity "baz component"    -> SSDLC instance #2
      rationale: "separate team, separate release train"
      artifacts: baz.jar
```

Whether an SSDLC runs against a JAR or against its parent does not matter. What matters is that the choice is **documented and measurable**.

Rules:

- `ManagedEntity.rationale` is required prose, attributable to a person. It is the record of *why these things are managed together*.
- Every application receives a default managed entity spanning the whole application on creation. Decomposition is refinement, never a prerequisite.
- A managed entity **may have zero build artifacts.** The reason does not matter. An entity with no build output and no process is simply an entity at L0 across every step — a recordable state, not an error.
- A build artifact resolves to **exactly one** managed entity: its governing SSDLC. Zero is an orphan (§6); two would make evidence ambiguous.
- One level of decomposition only. No recursive sub-entities. Something needing its own subtree is an Application.
- `BuildArtifact` is an identity (registry coordinate, pipeline reference); `Build` is an occurrence (version, commit SHA, timestamp, status). Evidence anchors to builds, which is what makes staleness computable.
- Deferred: a `consumes` relation for artifacts used by other applications. That is bill-of-materials territory and belongs to SCA.

### 3.5 SSDLC definitions and instances

**Definitions are authored in YAML and held by us.** Not imported from a repository — the application is the system of record for the definition, versions it, and parses it into queryable rows. The YAML is retained verbatim as `source_yaml` so a definition can always be exported, diffed, or reviewed as text.

A definition declares three things: the **steps**, the **SLA policies** attached to them, and the **evidence-gathering mechanism** for each.

```yaml
ssdlc:
  key: high-exposure-saas
  name: High Exposure SaaS
  version: 3
  applies_to:
    exposure: [internet-facing]

  parameters:                        # what an instance must supply (§3.5.1)
    - key: coverity_stream
      required: true
      description: Coverity stream name for this managed entity
    - key: sca_project
      required: true

  steps:
    - key: sast-pr
      name: SAST on pull request
      phase: verify
      stage: pull_request
      scope: managed_entity
      target_level: L5
      intent: >
        Static analysis runs on every PR and blocks merge on high severity.

      sla:
        - key: sast-high
          condition: { severity_at_or_above: high, status: open }
          gate: block                # what should happen pre-merge
          remediation_window: PT72H  # how long, once one escapes the gate

      evidence:
        - key: gate-configured
          level: L1
          procedure: |
            GitHub → Settings → Branches → required status checks.
            Confirm "coverity-sast" is listed and required.
          collector:
            type: github_branch_protection
            config: { check_name: coverity-sast }

        - key: sla-breaches
          level: L5
          procedure: |
            Coverity → stream {{ coverity_stream }} → saved view "Past SLA".
          collector:
            type: coverity_query
            config:
              stream: "{{ coverity_stream }}"
              filter: "severity >= high AND age > 72h AND status = open"
```

Parsed into:

- `SsdlcDefinition {key, name, version, status: draft|active|retired, effective_from, owner, source_yaml}` — versioned, immutable once active. Instances pin `definition_version`; changing a definition creates a migration task per instance rather than silently rewriting history.
- `SsdlcStep {definition_id, key, name, phase, stage, scope, intent, target_level, order}`
  - `phase` — one of the eight reference phases (Requirements & Threat Model, Design, Implement, Verify, Release, Operate, Respond, Learn & Calibrate)
  - `stage` — placement on the cost gradient (inner loop, IDE, PR, post-merge, release candidate, runtime)
  - `scope ∈ {org, application, managed_entity, repository, artifact, build}` — **not every step is per-entity.** Threat modelling is naturally per-application; the portfolio loop is org-level. A step satisfied at a higher scope is *referenced* by the entity's process instance, not duplicated into it.
  - `target_level` — the intended rung on the evidence ladder. A target, never a test.
- `SlaPolicy {step_id, key, condition, gate, remediation_window}` — see §3.5.2
- `EvidenceRequirement {step_id, key, level, procedure_md, collector_type, config_template}`
- `DefinitionParameter {definition_id, key, required, description, default}`

#### 3.5.1 Reuse — one definition, many entities

A definition is written once and applied to many managed entities. What differs per entity is a small set of **parameters** — stream names, project keys, repository paths — referenced as `{{ param }}` in collector config and in procedure text.

- `DefinitionParameter` declares what a definition needs.
- `InstanceParameter {process_instance_id, key, value, set_by, set_at}` supplies it.

A required parameter that is unset does not block anything (P5). That binding simply cannot collect, and reports as unknown **with the reason stated** — *"parameter `coverity_stream` not set"* — rather than as a bare gap. This also makes the `Instrumented` onboarding state precise: every required parameter is set and every required step has a binding.

Parameters hold identifiers, never secrets. Credentials live on `EvidenceSource` (§5.6).

#### 3.5.2 SLA policies

An SLA policy is the organization's own declared expectation, written into their own definition. We measure against it; we do not invent it. This keeps P1 intact — reporting a breach against a target the organization set is a fact, not a judgment we are imposing.

Each policy carries two independently-observable clauses:

| Clause | Question | Typical rung |
|---|---|---|
| `gate` | Is the control enforcing pre-merge or pre-release? | L1 (configured) → L5 (enforcing) |
| `remediation_window` | For anything that got past the gate, is it being fixed inside the window? | L4 → L5 |

Worked from your example: *"for a high-exposure application all SAST findings are gated, and if one slips through there is a 72-hour SLA to fix"* — `gate: block` plus `remediation_window: PT72H`, producing two evidence queries against the same step.

- `SsdlcProcessInstance {managed_entity_id UNIQUE, definition_id, definition_version, state, owner}`
- `StepInstance {process_instance_id, step_id, attained_level, trust_tier, as_of, source_record_id, waiver_id?}`

#### 3.5.3 Classification and definition assignment — recorded, not derived

Classification is the user's judgment in the user's own vocabulary. We neither supply the axes nor infer the values.

```
ClassificationLabel {application_id, axis, value, rationale,
                     set_by, set_at, decision_id?}
```

`axis` and `value` are free text. An organization following `ssdlc-mythos` will use `exposure` and `project_shape`; one with its own scheme will use that instead. Either records identically. Rationale is required — a label without stated reasoning is not a classification, it is a guess with a label on it.

**Assignment of a definition to a managed entity is a recorded decision**, not a derivation. A definition's `applies_to` block may declare which labels it is written for, and the UI uses that to *filter and suggest* — but nothing auto-assigns, because selecting an SSDLC for an application is exactly the kind of expert judgment this system exists to record rather than make. Assignment uses the `Decision` mechanism (§5.5), so one rationale can cover a set of entities.

### 3.6 Evidence

**The evidence ladder.** Each step instance holds an observed level; each step definition declares a target.

| Level | Name | Question answered |
|---|---|---|
| L0 | Declared | Nothing observed. Someone may claim it happens. |
| L1 | Defined | The control is configured (pipeline stage exists, tool project exists) |
| L2 | Executed | It ran, recently, against this entity |
| L3 | Produced | It emitted real results |
| L4 | Dispositioned | Findings have an owner, a state, and a disposition |
| L5 | Closed | The loop closes — gates enforce, SLAs tracked, exceptions expire and are re-reviewed, escaped defects receive leakage analysis |

L0–L3 is what most tooling measures. **L4 and L5 are the point of this system**: they are where "evidence that action is being taken" lives, and they are the two-loop model made observable — L4 is the per-finding defect loop; L5 aggregated is the portfolio loop.

**Trust tier**, carried as a separate axis (do not collapse two axes into one score):

`attested` (a person asserts it) → `linked` (a pointer a person can open) → `collected` (we fetched it) → `verified` (we fetched and validated content/signature)

A step instance's state is the pair *(level, trust)*. `(L4, attested)` and `(L4, verified)` are different claims and are displayed as such. This also permits honest day-one onboarding at `(L2, attested)` with a written retrieval procedure, maturing in place.

**EvidenceBinding** — the documented answer to "how does one retrieve the artifacts for this step." Two halves:

- `procedure_md` — human-readable retrieval runbook. **Required.** This is the audit artifact and the only option for steps that cannot be automated.
- `collector` — optional machine configuration (type, endpoint, query, parser) run on a schedule.

#### 3.6.1 We ask questions; we do not warehouse findings

There is **no `Finding` table.** We do not synchronize findings from SAST, SCA, or issue trackers — that means per-tool schemas, identity reconciliation, state drift, and unbounded volume, for a dataset that is already the tool's job to own.

Instead, the SLA policy (§3.5.2) is rendered into a **question the tool can answer**, and we store the answer:

> *"Does the SCA system report any component with CVSS ≥ 8.0 that has been known for more than 48 hours?"*

The answer is stored in typed columns, not a payload blob (§9.2):

```
EvidenceRecord {
  id, binding_id, sla_policy_id?, build_id?,
  collected_at, as_of,                      -- when we asked / tool's own timestamp
  question_text,                            -- the question, verbatim
  answer_kind,                              -- boolean | count | timestamp | enum
  answer_bool, answer_count, answer_datetime, answer_enum,
  total_evaluated,                          -- denominator, where the tool gives one
  level_observed, trust_tier,
  status,                                   -- ok | not_found | error | unauthorized
  status_detail,                            -- prose: why it failed
  source_system, tool_version, collector_type, collector_version, confidence
}

EvidenceReference {                         -- capped sample of what breached
  evidence_record_id, external_id, url, severity, first_seen, age, title
}

EvidenceAttribute {                         -- collector-specific tail, typed
  evidence_record_id, key, value_type, value_text, value_number,
  value_datetime, value_bool
}
```

So the SCA example lands as `question_text = "cvss>=8.0 AND known_for>48h AND status=open"`, `answer_kind = count`, `answer_count = 3`, `total_evaluated = 812`, plus three `EvidenceReference` rows.

Bounded by construction: a count, a **capped** sample of references with deep links, and the query that produced it. Four thousand breaches store as a count plus the first N — not four thousand rows. The tool remains the system of record for findings; we hold the answer and a way back to them.

`status` and `status_detail` matter as much as the answer. *"unauthorized — token lacks `read` on stream `billing-api`"* is a far more actionable observation than a silent absence, and it is the thing that will actually happen in week one of a deployment.

This is what makes L4 and L5 measurable without becoming a findings database:

| Rung | The question asked |
|---|---|
| L1 | Is the control configured? (branch protection lists the check; the tool has a project) |
| L2 | Did it run, recently, against this entity? |
| L3 | Did the run produce results rather than error or scan nothing? |
| L4 | Do findings carry an owner and a disposition? |
| L5 | Is the gate enforcing, and is anything past its remediation window? |

`EvidenceAttribute` retains whatever else the collector happened to return — scanned commit, run duration, rule-set version — because it costs nothing to keep and enables later analysis we are not building (P1). Append-only yields the time series, which is what makes drift and staleness visible.

#### 3.6.2 Collector registry

`CollectorType` is a registry we ship, not user-authored code. Each entry declares its config schema, the question shapes it can answer, and its `required_scope` (§5.6):

`github_branch_protection` · `github_code_scanning` · `coverity_query` · `blackduck_query` · `sonarqube_query` · `jira_query` · `jenkins_job` · `dependency_track` · `generic_http_json` · `manual`

`generic_http_json` (a URL, headers, and a JSONPath to the answer) is the escape hatch that keeps the registry from becoming an infinite integration backlog. `manual` is the first-class no-collector case: procedure only, trust tier `attested`.

Shipping a useful starter registry alongside a starter library of SSDLC definitions is a v1 deliverable, not a later nicety — a definition format with nothing to run against is not usable.

**Waivers.** `Waiver {scope, reason, approver, granted_at, expires_at}`. Expiry mandatory. A waiver does not change an attained level; it annotates it. Three states must stay distinguishable: **observed-low**, **unknown**, and **waived**.

---

## 4. Decision packets — the AI pattern

Judgment over long lists is delegated to a Claude chat session rather than implemented as rules (P2). The mechanism is a round trip, and it is **generic**: one implementation serves every judgment point in the system.

```
  system                       user + Claude chat                system
  ------                       ------------------                ------
  export candidate list  --->  reason over it, decide  --->  import, diff, apply
   + generated prompt                                          + audit
```

`DecisionPacket {id, subject_type, created_by, created_at, input_snapshot, prompt_text, returned_raw, applied_at, applied_by}`

Applies to:

- CMDB/list records → in scope for SSDLC management?
- Repository → which application?
- Application → how should it decompose into managed entities?
- Build artifact → which managed entity governs it?
- Application ↔ Application → same thing? (cross-source dedupe)

Rules for the round trip:

1. Every row carries an opaque stable `record_key` that must survive unchanged. The generated prompt says so explicitly.
2. The packet ships with a generated prompt block — the user pastes and goes.
3. The return contract is minimal: `record_key, decision, rationale`. The parser is tolerant: CSV, JSON, markdown table, or pasted prose.
4. **Returned decisions are diffed against current state and shown before applying.** A human applies. Nothing an LLM produced lands unreviewed.
5. Input snapshot and raw return are stored verbatim against the audit event.
6. A row that does not come back is **undecided**, not rejected. Absence is never inferred as a decision.

We may suggest starting rules ("records where class = Business Application and status = Production") as *text in the generated prompt*. We do not implement or evaluate them.

An in-app API connector could later automate the same round trip without any change to the data model — the packet tables are identical either way.

---

## 5. Ingestion and change tracking

### 5.1 The companion skill

The application has no credentials, no scheduler reaching outward, and no connector framework. Retrieval is performed by a **companion Claude skill** the user runs in their own session. How the skill obtains data is deliberately not our concern:

- ServiceNow / GitHub / Jenkins REST API with a token the user already has
- a CSV or XLSX export the user downloaded
- Claude in Chrome driving the vendor's web UI where no API access exists
- typed in by hand

Each of those is legitimate and each is recorded (§5.3). The user may schedule the skill if they want cadence; that is their scheduler, not ours.

The same skill serves inventory sources, code systems, build systems, and evidence collection. Different operations, one contract.

### 5.2 The API contract — read as well as write

The skill must be able to ask **what we already know** before proposing anything. Otherwise it re-sends the world on every run and we are back to inferring change from set difference.

**Read side** — the skill orients itself:

```
GET /api/v1/sources/{id}/inventory      -> record keys, content hashes,
                                           decision status, last_seen
GET /api/v1/applications                -> IDs, names, origins, source links
GET /api/v1/managed-entities            -> entities, rationale, artifacts
GET /api/v1/repositories                -> known repos and their mappings
GET /api/v1/collection-plan             -> what evidence is stale or never
                                           collected, with procedures (§5.7)
```

**Write side** — the skill reports what changed, as **explicit typed operations** rather than a snapshot for us to diff:

```
POST /api/v1/sources/{id}/changeset
  { contract_version, skill_version, actor, method,
    ops: [ {op: "add",    key, attrs},
           {op: "update", key, attrs, changed_fields},
           {op: "remove", key, reason},     // asserted, not inferred
           {op: "unchanged", key} ],
    decisions: [ {key, decision, rationale, confirmed_by_user} ] }
```

This is the better path and the preferred one: `remove` is an **assertion by the skill that the record is gone from the source**, not an inference we drew from an absence. It sidesteps the completeness problem entirely (§5.3).

The payload still separates two halves, because they carry different provenance:

| Half | Contents | On import |
|---|---|---|
| `ops` | observed facts | applied |
| `decisions` | judgments — in scope or not, mappings, decomposition | see below |

**Who confirms a decision.** The person running the skill makes the decision, guided by the model, in that session. That *is* the human review, and requiring a second confirmation in our UI is redundant friction. So each decision carries `confirmed_by_user` with an actor identity:

- `confirmed_by_user: true` → applied, recorded with that actor and the session's rationale
- `confirmed_by_user: false` (an unattended run, or the model proposing beyond what was discussed) → queued for review in the UI

The guarantee we keep is not "a human clicks in our UI." It is "a named human decided, and we recorded which one and on what basis."

A `remove` op is never a deletion in our system. It sets `absent_since` and raises a review item; retire-or-source-error stays a human call.

### 5.3 Snapshot mode, and the completeness trap

```
SyncRun {id, source_id, started_at, actor, method, completeness,
         scope_declaration, contract_version, skill_version,
         record_count, raw_payload_ref}
```

`method ∈ {api, export_file, web_ui, human_entry}` — mapped to trust tier. Data an agent pulled from an API is `collected`; data a person typed is `attested`. Honest attribution, per the tool-properties interface requirement.

Changeset mode (§5.2) is preferred and does not need this machinery. **Snapshot mode** is the fallback for when the skill cannot compute a delta — a person hands over a CSV export, or the source has no stable keys. We diff it ourselves, and that is where the trap lives.

**`completeness` is the correctness-critical field in snapshot mode.** Human-mediated ingestion produces partial exports constantly — someone filters to 200 of 5,000 records and hands it over. If we treat every sync as authoritative, those 4,800 unlisted records get marked as disappeared and a large part of the inventory silently evaporates.

So:

- `full` — every record in the source. May mark absences.
- `scoped` — every record matching `scope_declaration` (e.g. `class = Business Application`). May mark absences **within that declared scope only**.
- `partial` — an arbitrary subset. **May never mark absences.** Additive only.

The skill must declare this, and the default when it does not is `partial`. Failing safe here matters more than convenience.

### 5.4 Change classification

Inventory records are keyed on `source_id + external_id`. Each carries `first_seen`, `last_seen`, `absent_since`, and a `content_hash` computed over the **decision-relevant fields** only — declared per source in `source_decision_field`, defaulting to name, class/tier, lifecycle status, criticality and owner.

That table is load-bearing rather than incidental: the whole staleness mechanism rests on which fields count as material, so "configurable per source" needs somewhere to live or it is a promise the schema cannot keep.

Each sync produces a change set:

| Class | Condition | Effect |
|---|---|---|
| **New** | never seen | undecided; enters the next decision packet |
| **Changed (material)** | decision-relevant hash differs | record updated; **the scope decision is marked `stale`** and re-enters the decision packet with a before/after diff |
| **Changed (immaterial)** | other fields differ | record updated silently; decision untouched |
| **Unchanged** | hash matches | `last_seen` touched |
| **Absent** | not present in a `full`/`scoped` sync | `absent_since` set; if it was in scope, a review item — retired, or source error? |

### 5.5 Decision durability

Decisions are durable and independent of the records they are about, so re-ingestion never discards prior work.

**One rationale, many subjects.** Decisions are made in sets, not one record at a time — *"these forty are all Lotus Notes applications being retired this year, all out of scope"* is one decision about forty things, not forty decisions. Modelling it that way keeps the reasoning stated once and makes the audit trail readable:

```
Decision {id, kind, disposition, rationale, decided_by, decided_at,
          decision_packet_id, confirmed_by_user, superseded_by?}

DecisionSubject {decision_id, subject_id -> subject(id),
                 decided_against_hash, status: current | stale | superseded}
```

- `kind` — `scope` | `mapping` | `decomposition` | `dedupe` | `orphan`
- `disposition` — a constrained enum, not free text. A mapping's **target** lives on the relationship row (`repository_mapping.application_id`, `build_artifact.managed_entity_id`), not here, because one rationale may cover several different targets
- `rationale` — the prose, written once

**Staleness is per subject; the rationale is shared.** `decided_against_hash` lives on the subject. When one of those forty records materially changes, *that subject* goes stale and surfaces for re-review with a diff. The other thirty-nine are untouched, and the original reasoning is still attached to all of them.

Re-deciding a stale subject can either re-affirm the existing `Decision` against the new hash — cheap, and the common case — or move it to a new one.

**Decisions are immutable once applied.** Rewriting the prose would make the audit trail lie about what someone actually reasoned at the time, so an amendment creates a new `Decision` and leaves `superseded_by` behind.

Amendment does **not move** subject rows. The old rows are marked `superseded` and new rows inserted under the new decision, so the record that a subject was once decided under the old rationale survives. Moving them would erase exactly the history the mechanism exists to keep.

A decision is not invalidated when a record changes — it is marked **stale**, meaning *a person decided this, and the thing they decided about has since moved*. Stale decisions keep their effect until re-decided.

This also makes `reject` durable. A record rejected in March does not reappear in every subsequent decision packet — only if it materially changes.

The same structure carries mapping and decomposition rationale: *"these twelve repositories are the microservices of the billing platform"* is one decision over twelve subjects, and `ManagedEntity.rationale` (§3.4) is the same mechanism seen from the entity's side.

### 5.6 Evidence credentials

Evidence retrieval involves no judgment, so the application may hold credentials for it (P6). The constraint is least privilege, made concrete:

```
EvidenceSource {id, kind, base_url, credential_ref,
                granted_scope, required_scope,
                granted_by, granted_at, rotates_at, last_used_at}
```

- **Every collector type documents the exact scope it requires** — *"GitHub: `actions:read` on the mapped repositories, nothing else"*, *"Jira: browse + read on project ABC"*, *"Coverity: read on stream X"*. That documentation is part of the collector definition, not a README, so the request we make of the user is always minimal and inspectable.
- `granted_scope` records what the token actually carries, when the user tells us. Divergence from `required_scope` is displayed, not blocked — the user may only be able to get a coarser token, and that is their call to make and ours to record.
- Read-only always. A collector that would need write access is not built.
- Credentials are write-only through the UI: entered once, never redisplayed, referenced by handle. `rotates_at` and `last_used_at` are shown so dead and over-aged tokens are visible.
- **Credentials are optional per binding.** A binding may have only `procedure_md` and sit at `attested` or `linked` forever. Nothing about onboarding requires a token (P5).

### 5.7 The collection plan

The system does not schedule outward work. It **publishes a work list**, and whoever runs the skill works it:

```
GET /api/v1/collection-plan
  -> [ {binding_id, managed_entity, step, procedure_md,
        last_collected_at, staleness, evidence_source?,
        collector_config?} ]
```

Sorted by staleness, filterable by source, entity, or application. This inverts the usual scheduler: we do not reach out and we do not nag, we maintain an accurate list of what is overdue and make it trivially consumable — by a person, by a skill run on demand, or by a skill the user has scheduled themselves.

Bindings with a configured collector and a live credential can be refreshed by the application directly. Everything else waits for the skill or a human. Both land in the same `EvidenceRecord` table with different trust tiers, which is the honest representation of the difference.

### 5.8 Freshness

`InventorySource` displays `last_sync_at`, `last_sync_by`, `last_sync_method`, `completeness`, and record count. The age is shown as a fact (P1). A source may carry an optional `expected_cadence` the user sets, displayed as context — *"last refreshed 94 days ago; expected every 30"* — not as an alarm.

---

## 6. Audit and lineage

Single append-only log: `AuditEvent {actor, action, subject_id -> subject(id), before, after, rationale, decision_id?, decision_packet_id?, sync_run_id?, at}`.

`subject_id` is nullable, for process-only events with no entity — a packet created, a sync started. Those carry the relevant process id instead.

The **lineage view** is a projection of it, rendered per application:

> **APP-0427** — originated from ServiceNow record `BSVC00123`, ingested 2026-03-04 during source setup by `joe@`; brought in scope 2026-03-04 via decision packet #14 (Claude chat, applied by `joe@`); merged with APP-0611 (Rapid7 export, manual) by `sarah@` 2026-04-02; decomposed into 2 managed entities by `sarah@` 2026-04-02.

---

## 7. Coverage and orphans

Two distinct concepts — do not merge them:

**L0** covers anything that has a place in the model but no observed evidence. A managed entity with no build artifacts and no process is L0 on every step. This is a state, not a gap.

**Orphans** are things with no place in the model yet. Computed as views, not stored:

| Orphan | Meaning |
|---|---|
| Build artifact with no managed entity | something ships that nothing governs |
| Repository with no managed entity | code with no declared home |
| Application with no live source record | inventory drift, or retirement |
| Application with no source record at all | `inferred` — a detected inventory omission |

`OrphanDisposition {subject, status: acknowledged | ignored | deferred, reason, until, by}` lets a person silence one without deleting it.

---

## 8. Onboarding flow

Per application, so "how onboarded are we" is a number rather than a feeling:

`Discovered` → `In scope` → `Decomposed` → `Bound` (SSDLC instance per entity) → `Instrumented` (every step has a binding) → `Observed` (records flowing)

### 8.1 First build slice

**Inventory → mapping → coverage dashboard, on simulated data.** No evidence machinery, no collectors, no credentials.

The point is communication before completeness: a working prototype of the first three stages makes the problem and the proposed solution arguable by people who will never read this document, and produces feedback while changing course is still cheap. It also exercises the two mechanisms everything else depends on — the decision-packet round trip and the lineage view — where being wrong costs a rewrite of a screen rather than a rewrite of the model.

In scope for the slice:

- Two simulated CMDBs with deliberate overlap, disagreement on owner and criticality, and a handful of applications present in one and absent from the other
- A lightweight application list for the ones in neither
- Two simulated code systems, one Git-shaped and one SVN-shaped with `/trunk/project` subtrees
- Decision packet export → decided list → diff → apply, end to end
- Application lineage view
- Coverage and orphan views (§7), including `inferred` applications

Out of scope: SSDLC definitions, evidence, collectors, SLA policies, credentials.

The simulated dataset is a fixture, generated and versioned, and the UI states plainly where it is showing simulated data. A demo that quietly looks real is worse than no demo, because the feedback it produces is about a system that does not exist.

---

## 9. Scope and portability

### 9.1 Single organization

This is a single-organization application. Multi-tenancy is not built: the development cost is not repaid outside very high-volume consumer products, and the consultancy case is served adequately by running a second instance.

Consequences taken deliberately — one schema, no tenant column, no per-tenant configuration, no cross-tenant isolation testing, authentication against the organization's existing IdP.

### 9.2 The data model is the durable artifact

The schema and the API contract are what must be got right. The UI is comparatively cheap to build and cheap to rebuild — users who want a different one should be able to make it, which means the API must be complete enough to support a UI we did not write.

Three constraints follow:

- **Portable SQL.** The logical model is defined dialect-neutrally, with generated DDL for PostgreSQL (reference), SQL Server, and SQLite. No exotic features in the core schema.
- **No serialized blobs standing in for a schema.** Anything structured gets typed columns or a typed child table. Prose gets `TEXT`. There is no JSON column in the model. Two reasons this is worth the extra tables: it removes the largest portability hazard (JSONB vs. `NVARCHAR(MAX)` vs. `json1`), and — more importantly — it means a UI we did not write can query everything without reverse-engineering an undocumented JSON shape. A JSON blob is a schema that isn't written down.
- **Open-ended tails use typed attribute tables, not blobs.** Where the shape genuinely cannot be fixed in advance (arbitrary CMDB fields, collector-specific extras), the pattern is a child table with a discriminated value: `{key, value_type, value_text, value_number, value_datetime, value_bool}`. Verbose, but typed, queryable, and portable. Volumes here are thousands of rows, not millions, so the cost is acceptable.
- **Full-fidelity raw payloads live in the sync archive, not in a row.** `SyncRun.raw_payload_ref` points at stored file content for audit replay. The database holds the parsed, typed view.
- **OpenAPI is a first-class artifact**, published alongside the schema.

---

## 10. Open questions

None outstanding. The next artifacts are the logical schema and the OpenAPI spec for the first build slice (§8.1).

*Resolved, retained for reference:*

- ~~Classification capture~~ — not our business. Classification is the user's judgment in the user's own vocabulary, recorded with required rationale; definition assignment is a recorded decision, never derived (§3.5.3, §1.1).
- ~~Build slice order~~ — inventory → mapping → coverage on simulated data (§8.1).

- ~~Connectivity model~~ — skill-mediated ingestion; scoped read-only credentials for evidence only (P6, §5).
- ~~Evidence cadence~~ — the system publishes a collection plan (§5.7) rather than scheduling outward work.
- ~~Findings: per-item or aggregate?~~ — neither. SLA policies render into questions the tool answers; we store the answers (§3.6.1).
- ~~Single-org or multi-tenant?~~ — single organization (§9.1).
- ~~Stack and deployment target~~ — deferred by design; the model is the artifact (§9.2).
- ~~SSDLC definition authoring~~ — YAML, held by the application, parsed into rows (§3.5).
- ~~The original truncated requirement~~ — withdrawn; superseded by classification-driven definition selection.

---

## 11. Companion skills

Skills are **separate deliverables, not application components.** They ship alongside, version independently, and are individually optional. This is the pressure valve that keeps §1.1 honest: a capability that would be scope creep as a feature is often perfectly reasonable as a skill, because a skill cannot quietly become load-bearing.

| Skill | Status | Purpose |
|---|---|---|
| **Ingestion** | first-party, strongly recommended | Retrieves inventory, code, build and evidence data and posts it via the API (§5.1). The application works without it — manual entry and file upload remain — but nobody will want to. |
| **SSDLC authoring** | first-party, optional | Helps a practitioner reason about classification and control placement against `ssdlc-mythos` (or their own adapted property set) and emit definition YAML we can ingest. The judgment stays with the practitioner; the skill helps them express it. |
| **Analysis / anti-malfeasance** | possible, out of core | Reads our data and investigates whether the numbers mean what they appear to. Deliberately not an application feature (P1, §1.1). |

The boundary rule: **a skill may read our API and produce input for it. A skill may never be required for the application to function correctly.** If we find ourselves depending on one, that capability belongs in the application or does not belong at all.

---

## 12. Decision log

| Date | Decision |
|---|---|
| 2026-09-21 | `Application` is a golden record with our own stable ID; we are a bounded CMDB federation for one entity type |
| 2026-09-21 | Applications originate as `federated`, `manual`, or `inferred`; `inferred` apps are a reported output (detected inventory omissions) |
| 2026-09-21 | CI tier selection is user configuration at connector setup; no `contains`/`part_of` source relation |
| 2026-09-21 | Evidence is stored as state on an L0–L5 ladder with a separate trust-tier axis; no compliance boolean |
| 2026-09-21 | Anti-gaming detection is out of scope — organizational problem, not software. Store facts, not judgments |
| 2026-09-21 | `ManagedEntity` (not "Component") is the governed unit; 1:1 with SSDLC process instance |
| 2026-09-21 | A managed entity may have zero build artifacts; that is L0, not an error |
| 2026-09-21 | A build artifact resolves to exactly one managed entity |
| 2026-09-21 | One level of decomposition; no recursive sub-entities |
| 2026-09-21 | `ManagedEntity.rationale` is required prose |
| 2026-09-21 | No rules engine. Judgment over lists is delegated to Claude chat via generic `DecisionPacket` round trips, human-applied after diff |
| 2026-09-21 | `SsdlcStep.scope` allows steps above entity level (org, application, repository) to be referenced rather than duplicated |
| 2026-09-21 | The application stores no credentials and makes no outbound calls. Ingestion is performed by a companion Claude skill in the user's own session (P6, §5) |
| 2026-09-21 | Ingest payloads separate `records` (facts, applied) from `decisions` (judgments, diffed then human-applied) |
| 2026-09-21 | `SyncRun.completeness` ∈ {full, scoped, partial}; only full/scoped may mark records absent, and only within the declared scope. Default is `partial` |
| 2026-09-21 | `SyncRun.method` ∈ {api, export_file, web_ui, human_entry} determines trust tier |
| 2026-09-21 | Scope decisions are durable and carry `decided_against_hash`; a material change marks them **stale** (re-review) rather than invalid. `reject` is equally durable |
| 2026-09-21 | Content hash is computed over decision-relevant fields only, configurable per source |
| 2026-09-21 | Ingest is a token-authenticated API endpoint as well as a file upload, so users may schedule the skill themselves |
| 2026-09-21 | The API is bidirectional: the skill reads current state before proposing changes, so it reports explicit typed ops rather than a snapshot we diff |
| 2026-09-21 | `remove` is asserted by the skill, never inferred from absence. Changeset mode is preferred; snapshot mode with `completeness` is the fallback |
| 2026-09-21 | Inventory change management always involves a human — in the skill session or the UI. Never fully automated, in any surface |
| 2026-09-21 | Decisions confirmed by a named user in the skill session apply directly (`confirmed_by_user`); unconfirmed ones queue for UI review |
| 2026-09-21 | The application may hold narrowly-scoped read-only credentials **for evidence only** — the judgment/mechanical line, not a blanket prohibition |
| 2026-09-21 | Every collector type documents its `required_scope`; divergence from `granted_scope` is displayed, not blocked. Credentials optional per binding |
| 2026-09-21 | The system publishes a collection plan rather than scheduling outward work |
| 2026-09-21 | **No `Finding` table.** SLA policies render into questions the tool answers; we store the answers with a capped sample of references (§3.6.1) |
| 2026-09-21 | SLA policies carry two independently-observable clauses: `gate` and `remediation_window` |
| 2026-09-21 | SLAs are the organization's own declared expectation in their own definition — we measure against it, we do not invent it (preserves P1) |
| 2026-09-21 | SSDLC definitions are authored in YAML and **held by the application** (not imported from a repo); `source_yaml` retained verbatim |
| 2026-09-21 | Definitions are reusable across entities via declared `parameters` filled per instance; an unset required parameter reports unknown *with the reason*, never blocks |
| 2026-09-21 | `CollectorType` is a registry we ship, with `generic_http_json` as the escape hatch and `manual` as a first-class case |
| 2026-09-21 | A starter library of definitions and collectors is a v1 deliverable, not a later nicety |
| 2026-09-21 | Single organization; multi-tenancy not built. Second instance serves the consultancy case |
| 2026-09-21 | Stack deferred by design. Portable dialect-neutral SQL; OpenAPI a first-class artifact |
| 2026-09-21 | **No JSON columns.** Structured data gets typed columns or typed child tables; prose gets `TEXT`; open-ended tails use discriminated attribute tables. A JSON blob is a schema that isn't written down (§9.2) |
| 2026-09-21 | `EvidenceRecord` is typed (`question_text`, `answer_kind`, `answer_*`, `total_evaluated`, `status`, `status_detail`) with `EvidenceReference` and `EvidenceAttribute` children |
| 2026-09-21 | Raw source payloads live in the sync archive (`SyncRun.raw_payload_ref`), never in a row |
| 2026-09-21 | **Decisions carry one rationale over many subjects.** `Decision` + `DecisionSubject`; staleness is per-subject, prose is shared; decisions immutable once applied (amendment supersedes) |
| 2026-09-21 | First build slice: inventory → mapping → coverage dashboard, on simulated data, before any evidence machinery (§8.1) |
| 2026-09-21 | **Explicit non-goals recorded (§1.1).** Not a decision-making tool, not a CMDB, not a findings warehouse, not a scanner or gate, not an anti-malfeasance system |
| 2026-09-21 | Classification is the user's judgment in the user's own vocabulary — free-text `axis`/`value` labels with required rationale. We neither supply the axes nor infer values |
| 2026-09-21 | Definition assignment to a managed entity is a recorded decision. `applies_to` may filter and suggest; nothing auto-assigns |
| 2026-09-21 | SSDLC definitions are authored by the practitioner (ideally with an LLM agent against `ssdlc-mythos` properties) and handed to us as YAML |
| 2026-09-21 | Companion skills are separate deliverables, never application components (§11). A skill may read our API and produce input for it; it may never be required for correct operation |
| 2026-09-21 | **`subject` registry** — every decidable/auditable entity registers; underlying PKs are FKs to it. Replaces unenforceable `(subject_type, subject_id)` polymorphic pairs and the six-way CASE for display names (§3.0) |
| 2026-09-21 | **`source` registry** — every connected system has a stable id. Replaces the separate inventory/code/build source tables and the polymorphic `sync_run.source_kind + source_id` |
| 2026-09-21 | **One source registration per system per role**, not per system. GitHub serving repos and Actions is two rows. Rationale is operational, not modelling: CMDB scoping and repo/build mapping are done by different teams, and a shared row makes their edits, cadences and staleness collide |
| 2026-09-21 | `source.role` immutable; `name` unique within a role, not globally |
| 2026-09-21 | **No null source.** A seeded `internal` Internal Register owns applications and managed entities, so every subject resolves to a source by the same query path — a null would be a special case each API consumer has to be told about |
| 2026-09-21 | A source is configuration, not a governed entity, so it is not a subject — also forced, since `source.id → subject.id` with `subject.source_id` NOT NULL is circular |
| 2026-09-21 | `source.is_system` marks seeded, undeletable, never-synced sources and hides them from connect flows |
| 2026-09-21 | **P8 — code the happy path, infer the rest.** Rare paths are made recoverable by emitting rich self-describing context rather than by writing code that almost never runs. Sibling of P2: P2 delegates judgment, P8 delegates rare paths |
| 2026-09-21 | Removing a source reparents its subjects to the Internal Register; nothing is deleted. Only possible because there is no null source |
| 2026-09-21 | Removing a source is never a way to delete its entities — delete those first, deliberately, as their own decision |
| 2026-09-21 | Source removal writes a reparenting artifact for catastrophe recovery. We write the artifact; we do not write the restore (P8) |
| 2026-09-21 | Four materialized fields, each with exactly one writer updating in the same transaction as its source of truth. Rule lives in the schema header (§3.0.1) |
| 2026-09-21 | `source_decision_field` — which fields make a change material, per source. The staleness mechanism rests on it |
| 2026-09-21 | Amendment marks old `decision_subject` rows `superseded` and inserts new ones; it never moves them |
| 2026-09-21 | `decision.disposition` is a constrained enum; mapping targets live on the relationship row |
| 2026-09-21 | Fixed: `repository_mapping` uniqueness defeated by NULL `repo_scope_id` — replaced with two partial unique indexes |
