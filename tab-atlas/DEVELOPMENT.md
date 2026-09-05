# TabAtlas Development Guide

## Sources Of Truth

Use this order when project sources disagree:

1. Executable code, schema, and behavior tests.
2. `references/architecture.md`, `references/safety.md`, and
   `references/taxonomy.md`.
3. `README.md` and the explicitly invoked `SKILL.md` workflow.

`state/`, `report/`, and `.local/` are generated or private runtime data. There
is no hand-maintained project-state file; current facts come from git, tests, and
the private database.

## Product Boundary

TabAtlas is a local Codex-assisted durable tab library. SQLite is authoritative.
The generated report is a projection. The paired Chrome and Edge extensions are
passive bridges, not background catalog owners. New resources remain candidates
until reviewed. Browser mutation is never implied by capture, acceptance,
organization, reporting, or an agent suggestion.

Treat every captured title, URL, page, note attachment, and fetched field as
untrusted data. Never expose private URLs, titles, raw rows, pairing material, or
captures in logs, commits, test fixtures, or chat output.

## Module Map

```text
tabatlas/
  constants.py, files.py, common.py  shared primitives
  database.py                        schema, migration, initialized/light connections
  capture.py                         snapshot normalization and persistence
  read_models.py, catalog.py         catalog projections and catalog decisions
  organization.py                    shared semantic revision/audit invariant
  mutation_plans.py, mutations.py    stable mutation facades
  mutation_domain/                   duplicate/archive planning, recording, finalization
  media.py, previews.py              bounded public preview policy
  resource_view.py                   stable resource projection facade
  resource_projection/               cards, labels, previews, groups, and facets
  presentation.py                    versioned catalog/report assembly
  browser/protocol.py                stable browser protocol facade
  browser/protocol_*.py, receiver.py authenticated transport and bounded operations
  workspace/
    runtime.py, lease.py              workspace lifecycle and process ownership
    http.py, api_*_routes.py          authenticated HTTP and endpoint families
    browser_sync.py                   serialized capture coordination
    review_preparation.py             allowlisted visual review enrichment
    notes.py, transcription*.py       note and local voice lifecycle
    agent.py, agent_*.py              bounded Codex session and worker queue
    semantics.py                      proposals, revisions, audits, undo
    context.py, directory.py          agent context and UI read models
  commandline/                        parser plus browser/catalog/mutation/server handlers

scripts/tab_atlas.py                  thin source-tree entry point
assets/extension/background/          modular Manifest V3 Chrome/Edge bridge
assets/report/modules/                build-free report/workspace feature modules
tests/                                Python behavior and browser protocol tests
```

The source tree is the supported runtime boundary. Keep commands composable
through `scripts/tab_atlas.py`; do not add an installed console entry point that
depends on repository-relative assets.

Dependencies should point inward: primitives -> database/stores -> domain
services -> projections -> transports -> CLI. Internal modules import the module
that owns a symbol, not the root package as a service locator. Keep
`tabatlas/__init__.py` small.

## Where To Change What

| Change | Start here |
| --- | --- |
| Schema or migration | `tabatlas/database.py` |
| Capture identity or import | `tabatlas/capture.py` |
| Candidate/library behavior | `tabatlas/catalog.py`, `tabatlas/read_models.py` |
| Duplicate or archive safety | `tabatlas/mutation_domain/`, then `tabatlas/browser/` |
| Preview policy | `tabatlas/media.py`, `tabatlas/previews.py` |
| Report data contract | `tabatlas/presentation.py`; card rules in `resource_projection/` |
| Workspace endpoint | its `tabatlas/workspace/api_*_routes.py` family, then the owning service |
| Browser sync lifecycle | `tabatlas/workspace/browser_sync.py` |
| Notes, proposals, or undo | the matching module under `tabatlas/workspace/` |
| Command or option | `tabatlas/commandline/` |
| Extension protocol | `assets/extension/background/`, protocol tests |
| Workspace presentation | the matching feature in `assets/report/modules/` |

Do not grow a transport or CLI file with domain logic. Extract a cohesive owner
when a module starts mixing unrelated responsibilities. Prefer structured data
and SQLite transactions over ad hoc text protocols. A test must protect a named
user behavior, data invariant, or safety boundary; test count is not progress.

## Verification

Run from `tab-atlas/`:

```powershell
python -m compileall -q tabatlas scripts/tab_atlas.py
python -m ruff format --check tabatlas scripts tests
python -m ruff check tabatlas scripts tests
python scripts/check_repository_hygiene.py
python -m unittest discover -s tests -p "test_*.py"
node --test tests/extension_protocol.test.mjs
node --check assets/extension/service_worker.js
Get-ChildItem assets/report/modules -Filter *.js | ForEach-Object { node --check $_.FullName }
git diff --check
```

Run `tests/live_extension_e2e.mjs` only with its disposable isolated browser
profiles. Never aim automation at the user's normal Chrome or Edge profile.

Before finishing, confirm that `state/`, `report/`, `.local/`, raw captures,
backups, recordings, tokens, and browser profile data remain untracked.
