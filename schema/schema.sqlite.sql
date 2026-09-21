-- SSDLC Onboarding & Tracking — logical schema, SQLite dialect
-- Slice 1: inventory -> mapping -> coverage.  No SSDLC definitions, no evidence.
-- See DESIGN.md §8.1 for slice scope, §9.2 for the portability constraints.
--
-- Conventions
--   * ids are ULIDs stored as TEXT(26); never reused, never derived from a source key
--   * timestamps are ISO-8601 UTC strings ('2026-09-21T14:03:00Z')
--   * NO JSON COLUMNS (§9.2).  Open-ended tails use discriminated attribute tables.
--   * CHECK constraints carry the enums so the schema documents itself
--   * nothing is hard-deleted; absence is recorded, never inferred.  No FK declares
--     ON DELETE, so the database refuses deletion of anything referenced.
--
-- Two registries carry identity for everything else:
--
--   source   every connected system, with a stable id.  Referenced by the rows
--            that came out of it and by every sync run.  A source is CONFIGURATION,
--            not a governed entity: it is never decided about, mapped, or marked an
--            orphan, so it is deliberately not a subject.  (It also cannot be one:
--            source.id -> subject.id with subject.source_id NOT NULL is circular and
--            the first row could never be inserted.)
--   subject  every entity that can be decided about, dispositioned or audited.
--            Gives real foreign keys to decision_subject, orphan_disposition and
--            audit_event, which would otherwise be polymorphic and unenforceable.
--
-- THERE IS NO NULL SOURCE.  Rows this application owns rather than federates --
-- applications, managed entities -- belong to the seeded `internal` source at the
-- bottom of this file.  A null would be a special case every consumer has to be
-- told about; a source of kind 'internal' is queryable by the same code path as
-- ServiceNow, which matters most for skills reasoning over the API (P2).
--
-- MATERIALIZED FIELDS — the single-writer rule
--   Four fields cache a value derivable by join, because the derivation is a
--   multi-table join that every list query would otherwise run:
--       subject.display_name
--       inventory_record.scope_status / scope_decision_id / scope_stale
--       application.business_owner / criticality / lifecycle_state (+ peers)
--       application.onboarding_state
--   Each has exactly ONE writer, and that writer updates it in the SAME
--   TRANSACTION that writes the source of truth.  Nothing else writes them.
--   If a fifth such field appears, it follows the same rule or it does not ship.

PRAGMA foreign_keys = ON;


-- ============================================================================
-- 1. REGISTRIES
-- ============================================================================

-- Every entity that can be decided about, dispositioned, mapped or audited.
-- The underlying tables reference this, so registration is not optional.
CREATE TABLE subject (
    id                  TEXT PRIMARY KEY,
    subject_type        TEXT NOT NULL
                        CHECK (subject_type IN ('inventory_record','application',
                                                'managed_entity','repository','repo_scope',
                                                'build_artifact')),
    source_id           TEXT NOT NULL REFERENCES source(id),   -- never null; see header
    display_name        TEXT NOT NULL,                -- MATERIALIZED (see header)
    created_at          TEXT NOT NULL
);

CREATE INDEX idx_subject_type ON subject(subject_type);
CREATE INDEX idx_subject_source ON subject(source_id);

-- Every connected system: CMDBs, spreadsheets, code hosts, build servers.
--
-- ONE ROW PER SYSTEM *PER ROLE*, not one row per system.  A GitHub instance
-- serving both repositories and Actions builds is registered twice, under two
-- ids.  This is deliberate: CMDB scoping and repository/build mapping are done
-- by different people, and a shared row means their edits, sync cadences and
-- staleness collide.  Duplicate rows are cheap; contention between teams is not.
CREATE TABLE source (
    id                  TEXT PRIMARY KEY,
    role                TEXT NOT NULL             -- immutable once created
                        CHECK (role IN ('inventory','code','build','evidence','internal')),
    kind                TEXT NOT NULL
                        CHECK (kind IN ('internal','servicenow','bmc_helix','cherwell',
                                        'csv_list','manual_list','github','gitlab',
                                        'bitbucket','azure_devops','svn','perforce',
                                        'jenkins','github_actions','azure_pipelines',
                                        'gitlab_ci','tekton','bamboo','teamcity','other')),
    is_system           INTEGER NOT NULL DEFAULT 0 CHECK (is_system IN (0,1)),
    name                TEXT NOT NULL,
    base_url            TEXT,                     -- no stored credentials (P6)
    description         TEXT,
    expected_cadence_days INTEGER,                -- displayed as context, never an alarm
    owner_team          TEXT,                     -- who manages this registration
    -- inventory-role fields; NULL for code/build/evidence sources
    tier_selection      TEXT,                     -- which CI class/tier the user chose
    priority            INTEGER,                  -- survivorship default; lower wins
    created_by          TEXT NOT NULL,
    created_at          TEXT NOT NULL,
    last_sync_run_id    TEXT REFERENCES sync_run(id),
    -- the same system may appear under several roles, but only once per role
    UNIQUE (name, role)
);

CREATE INDEX idx_source_role ON source(role);

-- Which fields make a change MATERIAL (§5.4).  content_hash is computed over
-- exactly these, per source.  Without this table the "configurable per source"
-- promise in the design has nowhere to live.
CREATE TABLE source_decision_field (
    source_id           TEXT NOT NULL REFERENCES source(id),
    field               TEXT NOT NULL,
    PRIMARY KEY (source_id, field)
);

-- Per-source, per-field survivorship override; falls back to source.priority.
CREATE TABLE source_field_priority (
    source_id           TEXT NOT NULL REFERENCES source(id),
    field               TEXT NOT NULL,
    priority            INTEGER NOT NULL,
    PRIMARY KEY (source_id, field)
);


-- ============================================================================
-- 2. DECISIONS  (§5.5)
--    One rationale, many subjects.  Immutable once applied.
-- ============================================================================

CREATE TABLE decision_packet (
    id                  TEXT PRIMARY KEY,
    subject_type        TEXT NOT NULL
                        CHECK (subject_type IN ('inventory_record','repository',
                                                'application','build_artifact')),
    source_id           TEXT REFERENCES source(id),
    purpose             TEXT NOT NULL,            -- prose: what this packet asks
    prompt_text         TEXT NOT NULL,            -- generated prompt handed to the user
    input_snapshot_ref  TEXT,                     -- path in the packet archive
    returned_raw_ref    TEXT,                     -- verbatim return, archived
    row_count           INTEGER NOT NULL DEFAULT 0,
    status              TEXT NOT NULL DEFAULT 'open'
                        CHECK (status IN ('open','returned','applied','abandoned')),
    created_by          TEXT NOT NULL,
    created_at          TEXT NOT NULL,
    applied_by          TEXT,
    applied_at          TEXT
);

CREATE INDEX idx_decision_packet_status ON decision_packet(status);

CREATE TABLE decision (
    id                  TEXT PRIMARY KEY,
    kind                TEXT NOT NULL
                        CHECK (kind IN ('scope','mapping','decomposition','dedupe',
                                        'orphan','classification','definition_assignment')),
    -- Constrained, not free text.  A mapping's TARGET lives on the relationship
    -- row (repository_mapping.application_id, build_artifact.managed_entity_id),
    -- not here, because one rationale may cover several different targets.
    disposition         TEXT NOT NULL
                        CHECK (disposition IN ('manage','reject','defer',
                                               'mapped','unmapped','merged','split',
                                               'acknowledged','ignored','assigned')),
    rationale           TEXT NOT NULL,            -- the prose, written once
    decided_by          TEXT NOT NULL,
    decided_at          TEXT NOT NULL,
    decision_packet_id  TEXT REFERENCES decision_packet(id),
    confirmed_by_user   INTEGER NOT NULL DEFAULT 1 CHECK (confirmed_by_user IN (0,1)),
    superseded_by       TEXT REFERENCES decision(id),
    superseded_at       TEXT
);

CREATE INDEX idx_decision_kind ON decision(kind, decided_at);
CREATE INDEX idx_decision_packet ON decision(decision_packet_id);

-- Staleness lives here, per subject.  The rationale above is shared.
-- Amendment does NOT move rows: old rows are marked 'superseded' and new rows
-- inserted under the new decision, so the record that a subject was once decided
-- under the old rationale survives.
CREATE TABLE decision_subject (
    decision_id         TEXT NOT NULL REFERENCES decision(id),
    subject_id          TEXT NOT NULL REFERENCES subject(id),
    decided_against_hash TEXT,                    -- content_hash at decision time
    status              TEXT NOT NULL DEFAULT 'current'
                        CHECK (status IN ('current','stale','superseded')),
    went_stale_at       TEXT,
    PRIMARY KEY (decision_id, subject_id)
);

CREATE INDEX idx_decision_subject_subject ON decision_subject(subject_id);
CREATE INDEX idx_decision_subject_status ON decision_subject(status);


-- ============================================================================
-- 3. SYNC  (§5.2 - §5.4)
-- ============================================================================

CREATE TABLE sync_run (
    id                  TEXT PRIMARY KEY,
    source_id           TEXT NOT NULL REFERENCES source(id),   -- role comes from the source
    mode                TEXT NOT NULL
                        CHECK (mode IN ('changeset','snapshot')),
    method              TEXT NOT NULL             -- determines trust tier
                        CHECK (method IN ('api','export_file','web_ui','human_entry')),
    completeness        TEXT NOT NULL DEFAULT 'partial'
                        CHECK (completeness IN ('full','scoped','partial')),
    scope_declaration   TEXT,                     -- what this sync claims to cover
    contract_version    TEXT NOT NULL,
    skill_version       TEXT,
    actor               TEXT NOT NULL,
    started_at          TEXT NOT NULL,
    finished_at         TEXT,
    record_count        INTEGER NOT NULL DEFAULT 0,
    raw_payload_ref     TEXT,                     -- archive path; never a column blob
    status              TEXT NOT NULL DEFAULT 'running'
                        CHECK (status IN ('running','complete','failed')),
    status_detail       TEXT
);

CREATE INDEX idx_sync_run_source ON sync_run(source_id, started_at);

-- What each sync actually did, so "what changed" is answerable after the fact.
CREATE TABLE sync_op (
    id                  TEXT PRIMARY KEY,
    sync_run_id         TEXT NOT NULL REFERENCES sync_run(id),
    op                  TEXT NOT NULL
                        CHECK (op IN ('add','update','remove','unchanged')),
    record_key          TEXT NOT NULL,            -- external id at the source
    subject_id          TEXT REFERENCES subject(id),   -- resolved row, once known
    change_class        TEXT NOT NULL
                        CHECK (change_class IN ('new','material','immaterial',
                                                'unchanged','absent')),
    changed_fields      TEXT,                     -- comma-separated field names
    detail              TEXT
);

CREATE INDEX idx_sync_op_run ON sync_op(sync_run_id);
CREATE INDEX idx_sync_op_subject ON sync_op(subject_id);


-- ============================================================================
-- 4. INVENTORY  (§3.2)
-- ============================================================================

CREATE TABLE inventory_record (
    id                  TEXT PRIMARY KEY REFERENCES subject(id),
    source_id           TEXT NOT NULL REFERENCES source(id),
    external_id         TEXT NOT NULL,
    name                TEXT NOT NULL,
    description         TEXT,
    record_class        TEXT,                     -- source's own CI class
    lifecycle_state     TEXT,
    criticality         TEXT,
    business_owner      TEXT,
    engineering_owner   TEXT,
    content_hash        TEXT NOT NULL,            -- over source_decision_field only
    first_seen          TEXT NOT NULL,
    last_seen           TEXT NOT NULL,
    absent_since        TEXT,                     -- full/scoped sync, or explicit remove
    -- MATERIALIZED from decision/decision_subject (see header)
    scope_status        TEXT NOT NULL DEFAULT 'undecided'
                        CHECK (scope_status IN ('undecided','manage','reject','defer')),
    scope_decision_id   TEXT REFERENCES decision(id),
    scope_stale         INTEGER NOT NULL DEFAULT 0 CHECK (scope_stale IN (0,1)),
    UNIQUE (source_id, external_id)
);

CREATE INDEX idx_inventory_record_scope ON inventory_record(scope_status, scope_stale);
CREATE INDEX idx_inventory_record_absent ON inventory_record(absent_since);

-- The open-ended tail: typed, queryable, portable.  Not a JSON blob (§9.2).
CREATE TABLE inventory_record_attribute (
    record_id           TEXT NOT NULL REFERENCES inventory_record(id),
    key                 TEXT NOT NULL,
    value_type          TEXT NOT NULL
                        CHECK (value_type IN ('text','number','datetime','bool')),
    value_text          TEXT,
    value_number        REAL,
    value_datetime      TEXT,
    value_bool          INTEGER CHECK (value_bool IN (0,1)),
    PRIMARY KEY (record_id, key)
);


-- ============================================================================
-- 5. APPLICATION  (golden record, §3.2)
-- ============================================================================

CREATE TABLE application (
    id                  TEXT PRIMARY KEY REFERENCES subject(id),
    handle              TEXT NOT NULL UNIQUE,     -- APP-0427, never reused
    name                TEXT NOT NULL,
    description         TEXT,
    origin              TEXT NOT NULL
                        CHECK (origin IN ('federated','manual','inferred')),
    -- the fixed, small federated attribute set (P7).  MATERIALIZED from
    -- attribute_resolution (see header).
    business_owner      TEXT,
    engineering_owner   TEXT,
    criticality         TEXT,
    lifecycle_state     TEXT,
    data_flags          TEXT,                     -- comma-separated; context, not judgment
    regulatory_flags    TEXT,
    status              TEXT NOT NULL DEFAULT 'active'
                        CHECK (status IN ('active','merged','retired','orphaned')),
    -- MATERIALIZED from managed entities and mappings (see header)
    onboarding_state    TEXT NOT NULL DEFAULT 'discovered'
                        CHECK (onboarding_state IN ('discovered','in_scope','decomposed',
                                                    'bound','instrumented','observed')),
    created_by          TEXT NOT NULL,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);

CREATE INDEX idx_application_status ON application(status);
CREATE INDEX idx_application_origin ON application(origin);

-- Retired ids resolve forever (§3.2).  The absorbed row is retained with
-- status='merged', so alias_id is a real reference.
CREATE TABLE application_alias (
    alias_id            TEXT PRIMARY KEY REFERENCES application(id),
    alias_handle        TEXT NOT NULL,
    canonical_id        TEXT NOT NULL REFERENCES application(id),
    decision_id         TEXT REFERENCES decision(id),
    merged_by           TEXT NOT NULL,
    merged_at           TEXT NOT NULL
);

CREATE INDEX idx_application_alias_canonical ON application_alias(canonical_id);

CREATE TABLE application_source_link (
    application_id      TEXT NOT NULL REFERENCES application(id),
    inventory_record_id TEXT NOT NULL REFERENCES inventory_record(id),
    origin              TEXT NOT NULL
                        CHECK (origin IN ('federated','manual','dedupe')),
    decision_id         TEXT REFERENCES decision(id),
    created_by          TEXT NOT NULL,
    created_at          TEXT NOT NULL,
    PRIMARY KEY (application_id, inventory_record_id)
);

CREATE INDEX idx_app_source_link_record ON application_source_link(inventory_record_id);

-- Field-level survivorship.  Which source won each field, and why (§3.2).
CREATE TABLE attribute_resolution (
    application_id      TEXT NOT NULL REFERENCES application(id),
    field               TEXT NOT NULL,
    resolved_value      TEXT,
    winning_record_id   TEXT REFERENCES inventory_record(id),
    reason              TEXT NOT NULL
                        CHECK (reason IN ('only_source','priority','manual_override')),
    conflict            INTEGER NOT NULL DEFAULT 0 CHECK (conflict IN (0,1)),
    resolved_at         TEXT NOT NULL,
    PRIMARY KEY (application_id, field)
);

CREATE INDEX idx_attribute_resolution_conflict ON attribute_resolution(conflict);

-- Classification: the user's vocabulary, not ours (§3.5.3).
CREATE TABLE classification_label (
    id                  TEXT PRIMARY KEY,
    application_id      TEXT NOT NULL REFERENCES application(id),
    axis                TEXT NOT NULL,            -- free text: 'exposure', ...
    value               TEXT NOT NULL,            -- free text: 'internet-facing', ...
    rationale           TEXT NOT NULL,            -- required; a label alone is a guess
    decision_id         TEXT REFERENCES decision(id),
    set_by              TEXT NOT NULL,
    set_at              TEXT NOT NULL,
    UNIQUE (application_id, axis, value)
);


-- ============================================================================
-- 6. CODE  (§3.3)
-- ============================================================================

CREATE TABLE repository (
    id                  TEXT PRIMARY KEY REFERENCES subject(id),
    source_id           TEXT NOT NULL REFERENCES source(id),
    external_id         TEXT NOT NULL,
    name                TEXT NOT NULL,
    url                 TEXT,
    default_branch      TEXT,
    primary_language    TEXT,
    last_commit_at      TEXT,
    archived            INTEGER NOT NULL DEFAULT 0 CHECK (archived IN (0,1)),
    content_hash        TEXT NOT NULL,
    first_seen          TEXT NOT NULL,
    last_seen           TEXT NOT NULL,
    absent_since        TEXT,
    UNIQUE (source_id, external_id)
);

CREATE TABLE repository_attribute (
    repository_id       TEXT NOT NULL REFERENCES repository(id),
    key                 TEXT NOT NULL,
    value_type          TEXT NOT NULL
                        CHECK (value_type IN ('text','number','datetime','bool')),
    value_text          TEXT,
    value_number        REAL,
    value_datetime      TEXT,
    value_bool          INTEGER CHECK (value_bool IN (0,1)),
    PRIMARY KEY (repository_id, key)
);

-- Subtree scoping: required for SVN /trunk/project and monorepos (§3.3).
CREATE TABLE repo_scope (
    id                  TEXT PRIMARY KEY REFERENCES subject(id),
    repository_id       TEXT NOT NULL REFERENCES repository(id),
    path_prefix         TEXT NOT NULL,
    name                TEXT NOT NULL,
    created_by          TEXT NOT NULL,
    created_at          TEXT NOT NULL,
    UNIQUE (repository_id, path_prefix)
);


-- ============================================================================
-- 7. MANAGED ENTITIES AND BUILD ARTIFACTS  (§3.4)
-- ============================================================================

CREATE TABLE managed_entity (
    id                  TEXT PRIMARY KEY REFERENCES subject(id),
    application_id      TEXT NOT NULL REFERENCES application(id),
    name                TEXT NOT NULL,
    kind                TEXT
                        CHECK (kind IN ('client','server','service','library','job',
                                        'mobile','firmware','infra','whole_application')),
    rationale           TEXT NOT NULL,            -- required prose (P4)
    is_default          INTEGER NOT NULL DEFAULT 0 CHECK (is_default IN (0,1)),
    decision_id         TEXT REFERENCES decision(id),
    created_by          TEXT NOT NULL,
    created_at          TEXT NOT NULL,
    UNIQUE (application_id, name)
);

CREATE INDEX idx_managed_entity_app ON managed_entity(application_id);

-- A repository (optionally a subtree) maps to an application, and may be
-- refined to a managed entity.  Proposals live in decision_packet, not here:
-- a row exists only once a decision has been applied.
CREATE TABLE repository_mapping (
    id                  TEXT PRIMARY KEY,
    repository_id       TEXT NOT NULL REFERENCES repository(id),
    repo_scope_id       TEXT REFERENCES repo_scope(id),
    application_id      TEXT NOT NULL REFERENCES application(id),
    managed_entity_id   TEXT REFERENCES managed_entity(id),
    decision_id         TEXT REFERENCES decision(id),
    created_by          TEXT NOT NULL,
    created_at          TEXT NOT NULL
);

-- UNIQUE over a nullable column does not constrain the NULL case: in both SQLite
-- and PostgreSQL, NULL <> NULL.  Two partial indexes cover both cases properly.
CREATE UNIQUE INDEX ux_repo_mapping_scoped
    ON repository_mapping(repository_id, repo_scope_id, application_id)
    WHERE repo_scope_id IS NOT NULL;
CREATE UNIQUE INDEX ux_repo_mapping_whole
    ON repository_mapping(repository_id, application_id)
    WHERE repo_scope_id IS NULL;

CREATE INDEX idx_repository_mapping_repo ON repository_mapping(repository_id);
CREATE INDEX idx_repository_mapping_app ON repository_mapping(application_id);
CREATE INDEX idx_repository_mapping_entity ON repository_mapping(managed_entity_id);

-- Identity, not occurrence.  Exactly one governing managed entity, or an orphan.
CREATE TABLE build_artifact (
    id                  TEXT PRIMARY KEY REFERENCES subject(id),
    source_id           TEXT NOT NULL REFERENCES source(id),
    external_id         TEXT NOT NULL,
    coordinate          TEXT NOT NULL,            -- registry/group/name
    artifact_type       TEXT
                        CHECK (artifact_type IN ('container','jar','npm','nuget','wheel',
                                                 'exe','helm','apk','ipa','zip','other')),
    pipeline_ref        TEXT,
    managed_entity_id   TEXT REFERENCES managed_entity(id),   -- NULL = orphan (§7)
    mapping_decision_id TEXT REFERENCES decision(id),
    content_hash        TEXT NOT NULL,
    first_seen          TEXT NOT NULL,
    last_seen           TEXT NOT NULL,
    absent_since        TEXT,
    UNIQUE (source_id, external_id)
);

CREATE INDEX idx_build_artifact_entity ON build_artifact(managed_entity_id);


-- ============================================================================
-- 8. ORPHAN DISPOSITION  (§7)
--    Orphans themselves are computed views; this silences one without deleting it.
-- ============================================================================

CREATE TABLE orphan_disposition (
    id                  TEXT PRIMARY KEY,
    subject_id          TEXT NOT NULL UNIQUE REFERENCES subject(id),
    status              TEXT NOT NULL
                        CHECK (status IN ('acknowledged','ignored','deferred')),
    reason              TEXT NOT NULL,
    until               TEXT,
    decision_id         TEXT REFERENCES decision(id),
    set_by              TEXT NOT NULL,
    set_at              TEXT NOT NULL
);


-- ============================================================================
-- 9. AUDIT  (§6)
--    Append-only.  The lineage view is a projection of this table.
-- ============================================================================

CREATE TABLE audit_event (
    id                  TEXT PRIMARY KEY,
    at                  TEXT NOT NULL,
    actor               TEXT NOT NULL,
    action              TEXT NOT NULL,            -- created, linked, scoped, mapped...
    subject_id          TEXT REFERENCES subject(id),   -- NULL for process-only events
    source_id           TEXT REFERENCES source(id),    -- source setup and sync events
    before_text         TEXT,
    after_text          TEXT,
    rationale           TEXT,
    decision_id         TEXT REFERENCES decision(id),
    decision_packet_id  TEXT REFERENCES decision_packet(id),
    sync_run_id         TEXT REFERENCES sync_run(id)
);

CREATE INDEX idx_audit_subject ON audit_event(subject_id, at);
CREATE INDEX idx_audit_source ON audit_event(source_id);
CREATE INDEX idx_audit_at ON audit_event(at);
CREATE INDEX idx_audit_decision ON audit_event(decision_id);
CREATE INDEX idx_audit_packet ON audit_event(decision_packet_id);


-- ============================================================================
-- 10. COVERAGE VIEWS  (§7)
--     Orphans are computed, not stored.  "Unknown" is distinct from "failing".
-- ============================================================================

-- Something ships that nothing governs.  The row that earns the project.
CREATE VIEW v_orphan_build_artifact AS
SELECT  a.id AS subject_id, a.coordinate AS name, a.artifact_type,
        s.name AS source_name, a.pipeline_ref, a.last_seen,
        d.status AS disposition, d.reason AS disposition_reason
FROM    build_artifact a
JOIN    source s ON s.id = a.source_id
LEFT JOIN orphan_disposition d ON d.subject_id = a.id
WHERE   a.managed_entity_id IS NULL
  AND   a.absent_since IS NULL;

-- Code with no declared home.
CREATE VIEW v_orphan_repository AS
SELECT  r.id AS subject_id, r.name, r.url, s.name AS source_name,
        r.archived, r.last_commit_at, r.last_seen,
        d.status AS disposition, d.reason AS disposition_reason
FROM    repository r
JOIN    source s ON s.id = r.source_id
LEFT JOIN repository_mapping m ON m.repository_id = r.id
LEFT JOIN orphan_disposition d ON d.subject_id = r.id
WHERE   m.id IS NULL
  AND   r.absent_since IS NULL;

-- Inventory drift: an application whose last live source record disappeared.
CREATE VIEW v_orphan_application AS
SELECT  app.id AS subject_id, app.handle, app.name, app.origin, app.status
FROM    application app
WHERE   app.status = 'active'
  AND   app.origin = 'federated'
  AND   NOT EXISTS (
            SELECT 1
            FROM   application_source_link l
            JOIN   inventory_record ir ON ir.id = l.inventory_record_id
            WHERE  l.application_id = app.id
              AND  ir.absent_since IS NULL);

-- Detected inventory omissions: in no source at all.
CREATE VIEW v_inferred_application AS
SELECT  app.id AS subject_id, app.handle, app.name, app.created_at, app.created_by
FROM    application app
WHERE   app.origin = 'inferred'
  AND   app.status = 'active';

-- Decisions needing re-review because their subject moved.  One join to subject
-- gives the display name, instead of a six-way CASE.
CREATE VIEW v_stale_decision_subject AS
SELECT  ds.decision_id, d.kind, d.disposition, d.rationale,
        d.decided_by, d.decided_at,
        ds.subject_id, sub.subject_type, sub.display_name,
        src.name AS source_name, src.role AS source_role, ds.went_stale_at
FROM    decision_subject ds
JOIN    decision d ON d.id = ds.decision_id
JOIN    subject sub ON sub.id = ds.subject_id
JOIN    source src ON src.id = sub.source_id
WHERE   ds.status = 'stale';

-- Onboarding funnel, one row per state.
CREATE VIEW v_onboarding_funnel AS
SELECT  onboarding_state, COUNT(*) AS application_count
FROM    application
WHERE   status = 'active'
GROUP BY onboarding_state;

-- Every connected system with its roles and freshness.  Staleness is a fact we
-- display, never an alarm (§5.8).
CREATE VIEW v_source_freshness AS
SELECT  s.id AS subject_id, s.name, s.kind, s.role, s.owner_team,
        s.expected_cadence_days,
        sr.started_at AS last_sync_at, sr.actor AS last_sync_by,
        sr.method AS last_sync_method, sr.completeness AS last_sync_completeness,
        sr.record_count AS last_sync_record_count
FROM    source s
LEFT JOIN sync_run sr ON sr.id = s.last_sync_run_id;


-- ============================================================================
-- 11. SEED
--     Bootstrap rows, not sample data.  subject.source_id is NOT NULL, so this
--     row must exist before any application or managed entity can be created.
--     is_system = 1: never deletable, never synced, hidden from "connect a
--     source" flows.
-- ============================================================================

INSERT INTO source (id, role, kind, is_system, name, description,
                    created_by, created_at)
VALUES ('SYSTEM00000000000000000000', 'internal', 'internal', 1,
        'Internal Register',
        'Records this application owns rather than federates: golden-record '
        || 'applications and the managed entities declared against them. '
        || 'Applications that exist in no CMDB still belong here -- their '
        || 'origin field says manual or inferred, and application_source_link '
        || 'stays empty.',
        'system', '2026-01-01T00:00:00Z');
