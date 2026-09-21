# Simulated source systems

Fixtures and mock back ends for the first build slice (DESIGN.md §8.1). Standard library
only — no install step.

```bash
python mocks/generate_fixtures.py     # deterministic; regenerates fixtures/
python mocks/serve.py --port 8900     # serves them over HTTP
curl localhost:8900/                  # index of every endpoint
```

Everything except `/files` requires an `Authorization` header. Any non-empty bearer token
is accepted — the point is that the shape is right, not that we are testing auth.

---

## The company

**Meridian Freight**, a logistics operator. 52 canonical applications, none of which any
single system has a complete view of. That gap is the thing the product exists to close,
so the fixtures are built from a canonical truth that is then *projected* into each source
with realistic omissions and disagreements.

| | |
|---|---|
| canonical applications | 52 |
| in ServiceNow | 47 |
| in Apex ITAM | 27 (+4 Apex-only records) |
| in both — dedupe candidates | 27 |
| disagreeing between CMDBs | 5 |
| in the shadow spreadsheet only | 3 |
| **in no inventory source at all** | **2** |
| legacy Notes estate | 8 |
| GitHub repositories | 67 (12 unmapped) |
| SVN projects across 3 repos | 11 |
| build artifacts | 37 (**4 ungoverned**) |

---

## Scenarios the data supports

Each row is something you can actually demonstrate, not a hypothetical.

### 1. Tier selection is user configuration (§3.2.1)

ServiceNow carries four CI classes:

| Class | Count | What it is |
|---|---|---|
| `cmdb_ci_service` | 5 | Business services — one tier *above* applications |
| `cmdb_ci_business_app` | 47 | The applications |
| `cmdb_ci_appl` | 10 | Deployed instances — one tier *below* |
| `cmdb_ci_server` | 6 | Infrastructure — never an application at any tier |

Nothing in the data tells you which one means "application." Someone has to decide, and
that decision gets recorded. Pick the wrong tier and you get 10 rows for `billing-api-prod-01`
and `billing-api-prod-02` instead of one for Billing Platform.

### 2. Federation and survivorship (§3.2)

27 applications appear in both CMDBs under different keys (`BSVC01002` / `AX-0502`),
different field names (`u_criticality` / `tier`, `owned_by` / `steward`) and different
vocabularies (`Tier 1` / `Critical`). Five disagree on substance:

| Application | Disagreement |
|---|---|
| Billing Platform | criticality, and the owner's name is spelled differently |
| Warehouse Management | engineering owner |
| Customs Filing | criticality — Tier 1 vs Tier 2 |
| Contract Repository | business owner **and** criticality |
| Safety Incident Tracker | engineering owner |

Apex also carries "Lane Profitability," which is Pricing Analytics under another name — a
dedupe candidate that name-matching alone will not catch.

### 3. Detected inventory omissions (§3.2, `inferred`)

**ScanHub** and **FlowMon** are in no CMDB and no spreadsheet. They exist only as
repositories (`scanhub`, `scanhub-drivers`, `flowmon`) and shipping containers
(`registry.meridian/scanhub`, `registry.meridian/flowmon`).

They must acquire an application ID anyway. The list of `inferred` applications is a
report you hand back to the CMDB owners — the product turns the ugly part of federation
into an output.

### 4. One rationale, many subjects (§5.5)

Eight Lotus Notes applications, all `Sunsetting`, all owned by Legacy Support. They are a
single decision — *"Notes estate, replacement in flight, out of scope until retirement"* —
over eight subjects, not eight decisions. Demonstrates why rationale is deduplicated.

### 5. Material change and staleness (§5.4, §5.5)

`?state=v2` returns a later state of ServiceNow with four material changes, each of a
different kind:

| Application | Change |
|---|---|
| Fuel Card Integration | criticality Tier 3 → **Tier 1** |
| BI Reporting | criticality Tier 3 → Tier 2 |
| Document Imaging | owner `S. Varga` → `S. Varga-Toth` |
| Legacy Ops Wiki (Notes) | lifecycle Sunsetting → **Retired** |

Each marks its decision subject **stale**, not invalid. Fuel Card Integration is the
interesting one: rejected as low-criticality, now Tier 1 — the reasoning needs revisiting.
Document Imaging is the counter-case: a name correction that changes nothing, and
re-affirming should be one click.

The other seven Notes applications stay untouched while Legacy Ops Wiki goes stale — the
point of per-subject staleness with shared prose.

### 6. The completeness trap (§5.3)

`GET /servicenow/export/partial` returns 40 of 68 rows, exactly as a filtered export would.

Submit it as `completeness: full` and 28 records are marked absent, applications go
`orphaned`, and the review queue fills with damage. Submit it as `partial` — the default —
and nothing is marked absent. This is why the field is required and why omitting it fails
safe.

### 7. Subtree scoping (§3.3)

Three SVN repositories in `/trunk/<project>` layout, 11 projects. `legacy-apps` alone holds
five. Without `RepoScope` the finest available grain is "legacy-apps," which maps to five
applications at once and is useless.

`meridian-platform` is the Git-shaped version of the same problem: one repository, five
subprojects, four of which belong to different applications and one (`/libs/freight-model`)
to none.

### 8. Code with no declared home (§7)

12 unmapped repositories, of realistically varied kinds: Terraform modules, the engineering
handbook, four hack-day and spike projects, a shared Jenkins library, dotfiles, scratch
notebooks, an archived PHP portal, a vendor PoC, interview exercises.

Most should be dispositioned `ignored` rather than mapped. The demo point is that
dismissing them is a *recorded decision with a reason*, not a delete.

### 9. Something ships that nothing governs (§7)

Four build artifacts with no managed entity:

- `registry.meridian/edge-cache-shim`
- `com.meridian.misc:report-exporter`
- `registry.meridian/partner-webhook-relay`
- `com.meridian.misc:legacy-batch-runner`

All four have recent builds. This is the row that earns the project.

### 10. Decomposition (§3.4)

**PLM Suite** — `plm-client` (C#), `plm-server` (Java), `plm-shared-protocol` (Java),
shipping three artifacts. The natural split is client / server, with the protocol jar
belonging to one of them. Someone has to decide, and write down why.

**Billing Platform** — `billing-core`, `billing-settlement`, `billing-adjustments`,
shipping three jars. The `foo.app` case from the design discussion: maybe all three are one
managed entity; maybe settlement has its own release train and belongs on its own. Either
is correct as long as it is documented.

---

## Endpoints

| System | Endpoint | Pagination |
|---|---|---|
| ServiceNow | `/servicenow/api/now/table/<class>` | `sysparm_limit`, `sysparm_offset` |
| | `/servicenow/api/now/table` | class listing with counts |
| | `?state=v2` | the drifted state |
| | `/servicenow/export/partial` | 40 of 68 rows |
| Apex ITAM | `/apex/v2/assets?type=Application` | `page`, `page_size` |
| GitHub | `/github/api/v3/orgs/meridian-freight/repos` | `page`, `per_page`, `Link` header |
| | `/github/api/v3/repos/meridian-freight/<name>` | |
| Subversion | `/svn`, `/svn/<repo>` | none |
| Jenkins | `/jenkins/api/artifacts` | none |
| Spreadsheet | `/files/shadow_apps.csv` | none — no auth required |

Each back end deliberately uses its own vendor's field names and pagination idiom. Making
the ingestion path cope with that variation is most of the real work, and a mock that
normalised it away would hide exactly the problem worth demonstrating.

---

## Regenerating

`generate_fixtures.py` holds the canonical application list and the projection rules. It is
deterministic — editing the `APPS` table and re-running gives a reproducible dataset, and
the fixture JSON is meant to be committed so a demo is repeatable.
