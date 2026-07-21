# TabAtlas

TabAtlas turns large Chrome and Edge tab sets into a durable local resource
library. Browser tabs are capture inputs, not the long-term storage layer. The
catalog keeps reopen links, provenance, summaries, previews, purpose hierarchy,
Projects, Action Lists, user notes, and audited decisions after tabs close.

## Start The Workspace

From this directory, run:

```powershell
python scripts/tab_atlas.py workspace --open
```

This starts one authenticated loopback workspace and opens it in the browser. It
is not installed as a service, is not scheduled, and stops with `Ctrl+C`. The
browser extension remains passive unless a separate capture receiver is running.

The workspace has three destinations:

- **Home** for the next decision, Action Lists, and active purpose areas;
- **Library** for Purpose and Source views of every retained resource;
- **Review** for discoveries, unplaced resources, duplicates, and captured tabs.

Open a resource to add an exact local text note, record a local voice note, edit
its transcript, update Action List progress, open the source, or remove the item
from the visible library. Notes take precedence over page metadata when Codex
proposes organization.

## Codex Integration

The **Ask Codex** panel starts a dedicated persistent Codex task only when a
request needs interpretation. It uses the existing ChatGPT sign-in from the local
Codex CLI; TabAtlas does not ask for or store an API key. Model output is a
schema-constrained proposal. Local code validates and applies it, records before
and after state, and exposes Undo. The child process stops after two idle minutes;
the opaque task ID remains available for the next request.

The workspace is the only writer while its Codex child is active. **Open in
Codex** stops that child before opening the same dedicated task in Codex desktop;
**Reclaim here** resumes workspace ownership. Voice bytes remain local. The
stable integration uses an editable text transcript rather than experimental
realtime audio.

If Codex is unavailable, notes remain saved and interpretation requests remain
queued for a later workspace session. The static `report/index.html` remains a
read-only fallback and never embeds private note text or recordings.

## Refresh Browser State

After pairing the extension in Chrome and Edge, refresh on demand with:

```powershell
python scripts/tab_atlas.py refresh --browser all
python scripts/tab_atlas.py discoveries
```

New canonical resources are staged for review. Known resources update their
observations without creating duplicates. Capture does not close, focus, move,
or regroup browser tabs. Duplicate cleanup and archive-all use separate bounded,
audited protocols described in [SKILL.md](SKILL.md) and
[references/safety.md](references/safety.md).

## Architecture

See [references/interaction-architecture.md](references/interaction-architecture.md)
for authority, agent, voice, and handoff contracts, and
[references/architecture.md](references/architecture.md) for capture, catalog,
presentation, and browser-mutation boundaries.
