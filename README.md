# TabAtlas

TabAtlas is a local, agent-operated workspace for turning large Chrome and
Microsoft Edge tab sessions into a durable, searchable resource library. The
user talks to Codex; this repository supplies deterministic browser capture,
SQLite storage, organization, reporting, and audited close operations.

It is intentionally not a hosted app, embedded model runner, or background
Windows service.

## What It Does

- Captures tabs, windows, ordering, and browser tab groups without activating or
  navigating content tabs.
- Stages previously unseen canonical URLs as discoveries. Existing URLs update
  live context without creating duplicate library entries.
- Keeps accepted resources after their browser tabs close, including exact URLs,
  provenance, concise summaries, previews, and last-known browser context.
- Organizes the complete accepted library as Space -> Topic -> Focus, with
  optional cross-cutting Project collections.
- Generates a local decision workspace with preview crops, brief and detailed
  views, search, browser-group filters, discovery review, duplicate review, open
  tab review, and batched infinite scrolling.
- Can close exact duplicate extras or every reviewed captured tab through
  separate authenticated, backed-up, post-verified operations.

## Layout

```text
PROJECT_STATE.md       Canonical direction and current checkpoint
tab-atlas/SKILL.md     Agent operating workflow
tab-atlas/scripts/     Python standard-library CLI, catalog, and receiver
tab-atlas/assets/      Manifest V3 extension and static report source
tab-atlas/tests/       Focused behavior, protocol, and isolated-browser tests
tab-atlas/state/       Private local database, captures, and previews (ignored)
tab-atlas/report/      Generated private report (ignored)
legacy/                Quarantined prior implementation (ignored)
```

## Use Through Codex

The normal interface is conversation. Useful requests are:

- `Refresh TabAtlas`
- `Show me the new discoveries`
- `Accept all new discoveries`
- `Reconsider the library organization`
- `Open the TabAtlas report`
- `Close the exact duplicates`
- `Archive my captured tabs`

Codex translates those requests into the bounded workflow documented in
`tab-atlas/SKILL.md`. New discoveries are reviewed before acceptance. After
acceptance, Codex enriches and reconsiders them together with the existing
library rather than classifying the new batch in isolation.

## Direct Commands

Python 3.11 or newer is sufficient for normal operation.

```powershell
cd tab-atlas
python scripts/tab_atlas.py status
python scripts/tab_atlas.py refresh --browser all
python scripts/tab_atlas.py discoveries
python scripts/tab_atlas.py accept --all
python scripts/tab_atlas.py enrich
python scripts/tab_atlas.py report
```

Selected discoveries can be accepted or dismissed by opaque resource ID.
Dismissal is reversible:

```powershell
python scripts/tab_atlas.py accept --resource-id <id>
python scripts/tab_atlas.py dismiss --resource-id <id>
python scripts/tab_atlas.py discoveries --state dismissed
python scripts/tab_atlas.py accept --resource-id <id>
```

The generated report is `tab-atlas/report/index.html`.

## Browser Extension

Prepare the stable unpacked directory once:

```powershell
python scripts/tab_atlas.py prepare-extension
```

Load `tab-atlas/state/extension` as an unpacked extension in Chrome and Edge,
then pair each browser once:

```powershell
python scripts/tab_atlas.py pair --browser chrome
python scripts/tab_atlas.py pair --browser edge
```

The extension has two explicit states:

- **OFF**: no alarm, loopback request, or tab query.
- **ON**: one low-cost 30-second alarm checks the fixed loopback receiver. Tabs
  are read only when a one-shot authenticated command is waiting.

The receiver records the protocol version reported by each authenticated worker.
Older paired workers remain usable for read-only capture, but duplicate cleanup
and archive commands require protocol 4 and fail before mutation with a precise
reload instruction. After `prepare-extension` changes the unpacked source, click
**Reload** once on TabAtlas Bridge in `chrome://extensions` and
`edge://extensions`; restarting the browsers does not reliably refresh an
already registered unpacked worker. The popup's **Build** row shows the code the
browser actually loaded, for example `v0.4.1 / protocol 4`. If **Reload** leaves
an older value active, toggle the TabAtlas Bridge extension-manager card off and
on; do not confuse that manager control with the popup's passive-mode switch.

The receiver is started on demand, binds only to `127.0.0.1`, and exits after the
requested operation. Nothing is scheduled with Windows. A closed browser keeps
its last trusted inventory; TabAtlas does not secretly launch or automate the
normal browser profile.

## Decision Workspace

- **Home** provides one next action and the six purpose Spaces.
- **Spaces** drill into Topic and Focus levels, with format, browser, and captured
  group filters.
- **Review** separates new discoveries, ambiguous accepted resources, exact
  duplicates, and currently open tabs.
- Resource cards show the smallest useful decision set: visual evidence where
  available, cleaned title, concise brief, topic/focus signal, next action, and
  open or stored state. The inspector exposes provenance and detail on demand.
- Discovery cards receive agent-written decision summaries and safe public video
  thumbnails before acceptance, so review is not limited to raw tab titles.
- The first 30 matching cards render immediately. More are appended as the user
  scrolls, without a manual Show more control.

Report commands download privacy-safe action requests. They do not mutate the
database or browser directly; Codex validates and executes the corresponding CLI
operation.

## Safe Close Operations

Exact duplicate cleanup retains one exact HTTPS URL in the same browser, window,
and tab group. It excludes protected or changed tabs and proves the keeper
remains after closure.

```powershell
python scripts/tab_atlas.py dedupe --browser all
python scripts/tab_atlas.py dedupe --browser all --execute --approval "<bounded scope>"
```

Archive-all always blocks on pending discoveries. By default it also blocks on
dismissed live resources. After the user explicitly reviews those dismissals,
`--include-dismissed` closes them as audited discards while accepted resources
remain in the durable library. The command verifies raw evidence and catalog
integrity, creates an integrity-checked private backup, revalidates every tab
immediately before closure, captures again, and requires every planned tab to be
both reported closed and absent. A temporary extension-owned control tab keeps
verification alive and is removed through a separate authenticated result.
Any pre-existing TabAtlas popup tab is bound to the same fresh plan as an
operational target, so the workflow does not leave its own setup page behind.

```powershell
python scripts/tab_atlas.py archive-tabs --browser all
python scripts/tab_atlas.py archive-tabs --browser all --execute --approval "<bounded scope>"
python scripts/tab_atlas.py archive-tabs --browser all --include-dismissed --execute --approval "<scope naming retained and discarded counts>"
```

## Verification

```powershell
cd tab-atlas
python -m unittest discover -s tests -p "test_*.py"
node --test tests/extension_protocol.test.mjs
node tests/live_extension_e2e.mjs all archive
node tests/live_extension_e2e.mjs all dedupe
python -m py_compile scripts/tab_atlas.py scripts/tab_atlas_core.py scripts/tab_atlas_receiver.py
node --check assets/extension/service_worker.js
node --check assets/report/app.js
```

The live browser tests use disposable isolated profiles. Private snapshots, the
SQLite database, generated reports, previews, pairing material, and quarantined
legacy tree remain untracked.
