# TabAtlas Workspace

The active project is [tab-atlas](tab-atlas/README.md).

TabAtlas is a local Codex-assisted library for turning large Chrome and Edge tab
sessions into durable, searchable resources. Browser tabs are capture inputs;
the private SQLite catalog is the long-term record. The browser extensions stay
passive until the on-demand workspace asks them for a bounded capture.

Start here:

```powershell
cd tab-atlas
python scripts/tab_atlas.py workspace --open
```

Project guidance lives with the code:

- [README.md](tab-atlas/README.md) explains use and the product model.
- [AGENTS.md](tab-atlas/AGENTS.md) maps responsibilities and verification.
- [SKILL.md](tab-atlas/SKILL.md) defines the Codex operating workflow.
- [references](tab-atlas/references/) defines stable architecture, taxonomy, and
  safety contracts.

There is deliberately no manually maintained project-state ledger. Use git,
tests, and the ignored local database for current facts. `legacy/`, generated
reports, captures, previews, pairings, recordings, and database files are not
authoritative instructions and must remain untracked.
