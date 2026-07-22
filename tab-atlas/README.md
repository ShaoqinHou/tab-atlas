# TabAtlas

TabAtlas is a local Codex-assisted library for deciding what to do with large
Chrome and Edge tab sets. It captures enough context to make each page
understandable, stages new pages for review, and keeps accepted resources in a
private SQLite catalog after their browser tabs close.

The mental model is simple:

```text
paired browser extension -> bounded sync -> review candidates -> durable library
                                                     |
                                                     `-> generated report/workspace
```

The database is authoritative. Browser tabs are observations, the generated
report is replaceable, and Codex suggestions are inert until accepted.

## Start

Python 3.11 or newer is required. From this directory:

```powershell
python scripts/tab_atlas.py workspace --open
```

This starts one authenticated loopback workspace. It is not a Windows service,
is not scheduled, and stops with `Ctrl+C`.

Starting the workspace begins one serialized sync against paired, enabled Chrome
and Edge extensions. The catalog remains usable while the sync waits for a
browser. Use **Sync now** to capture tabs opened later. There is no continuous
tab monitor: the extension's low-frequency loopback check only discovers a
waiting command, and it reads tabs only for that bounded command.

If a paired browser is closed or unavailable, the sync reports that state and
leaves its last trusted catalog data unchanged. TabAtlas does not launch or
remote-debug the user's normal browser profile.

## Pair Browsers

Prepare and load one unpacked extension in each browser:

```powershell
python scripts/tab_atlas.py prepare-extension
python scripts/tab_atlas.py pair --browser chrome
python scripts/tab_atlas.py pair --browser edge
```

Load `state/extension` through `chrome://extensions` and `edge://extensions`
with Developer mode enabled. Pairing is local and browser-specific.

The extension has two explicit modes:

- **OFF** clears its alarm and performs no loopback check or tab query.
- **ON** performs a low-cost loopback check. It sends no tab data unless an
  authenticated TabAtlas receiver has a command waiting.

The receiver exists only during a requested operation and binds to
`127.0.0.1`. No heartbeat uploads or background model calls are used.

## Daily Workflow

1. Start the workspace. Its initial sync stages unseen canonical resources.
2. Review **New discoveries**. Accept useful resources or dismiss unwanted ones.
3. Use Home and Library to browse by purpose, project, action, or source.
4. Add typed or local voice notes when page metadata does not express your
   intent. Ask Codex for a proposal only when interpretation is useful.
5. Use **Sync now** after opening more tabs. Known resources update provenance;
   only unseen canonical resources return to review.
6. Close browser tabs only through a separately previewed and approved mutation
   when verified closure is wanted.

Candidates are durable but are not accepted library entries. Capture never
silently promotes them. Dismissal is reversible and does not close a browser tab.

Each accepted resource has at most one primary `Space -> Topic -> Focus` path.
Projects and Action Lists are orthogonal overlays. Source, platform, owner,
channel, domain, browser, and captured group are retrieval facets, not competing
purpose taxonomies.

## Codex And Notes

Typed notes and voice recordings are private local evidence. A recording is
saved before local transcription; its audio is not sent to Codex. The user's
note takes precedence over inferred metadata.

**Ask Codex** sends bounded catalog context through the user's existing Codex
sign-in. It does not require an API key. Codex can explain, retrieve, or propose
organization and action-list changes. The workspace validates each proposal and
requires explicit acceptance before changing semantic state. Browser closure is
never an agent proposal side effect.

## Storage And Reports

Private state lives under `state/` and must remain untracked. In particular,
`state/atlas.sqlite` is the durable catalog. It retains accepted URLs,
observations, summaries, previews, notes, organization, progress, and audits.

`report/index.html` is generated from the catalog. It is a read-only offline
projection, not a second database. The authenticated workspace serves the same
catalog with review, note, proposal, and sync controls.

Generate or serve the read-only projection directly when needed:

```powershell
python scripts/tab_atlas.py report
python scripts/tab_atlas.py view --open
```

## Direct Operations

The UI is the normal path. The thin source-tree entry point exposes the same
package CLI for diagnosis and bounded automation:

```powershell
python scripts/tab_atlas.py status
python scripts/tab_atlas.py refresh --browser all
python scripts/tab_atlas.py discoveries
python scripts/tab_atlas.py inventory
python scripts/tab_atlas.py query --text "..."
```

An editable install also provides the `tabatlas` console command:

```powershell
python -m pip install -e .
tabatlas workspace --open
```

Normal runtime uses only the Python standard library. Optional local voice
transcription dependencies are documented in `requirements-voice.txt`.

## Safe Closure

Exact-duplicate cleanup and archive-all are separate authenticated protocols.
Both require a fresh capture, bounded approval, extension-side target
revalidation, a newer post-action capture, and private audit evidence.

Archive-all blocks when candidates remain. Open dismissed resources also block
by default. They may be closed only after explicit review and an approval that
uses `--include-dismissed`; they are then audited as discards and never promoted
to the accepted library. See [references/safety.md](references/safety.md).

## Development

Read [AGENTS.md](AGENTS.md) before changing code. Stable design contracts live
in [architecture.md](references/architecture.md),
[taxonomy.md](references/taxonomy.md), and [safety.md](references/safety.md).

```powershell
python -m compileall -q tabatlas scripts/tab_atlas.py
python -m unittest discover -s tests -p "test_*.py"
node --test tests/extension_protocol.test.mjs
```

Live extension tests use disposable isolated profiles only.
