# TabAtlas

TabAtlas is a local, durable library for turning large Chrome and Edge tab backlogs into saved, searchable resources that can be organized and safely closed.

## What is different in this rebuild

- Browser tabs are observations; `state-v2/tabatlas.db` is the source of truth.
- Repeated URLs keep one resource identity while each capture occurrence retains browser/profile/window/group provenance.
- Query parameters are preserved. URL fragments and default ports are removed; private pages are never fetched through guessed public endpoints.
- Resources may belong to multiple nested collections without duplication.
- User notes are first-class guidance and outrank weak metadata during organization.
- Organization supports reviewable proposals, batch application, whole-library reconsideration, reversible history, and stale-result rejection.
- Jobs persist checkpoints and completed items so interrupted analysis can resume without discarding finished work.
- Chrome/Edge use a real MV3 bridge that is OFF unless enabled. Save and close are separate effects; close requires durable save plus exact live tab/window/URL revalidation.
- The embedded agent uses the supported `codex app-server` stdio JSONL protocol. It discovers account/model state at runtime and can use ChatGPT subscription sign-in; no API key is required by TabAtlas.
- Legacy MCP servers, skill packs, AGENTS.md scaffolding, and development-agent wrappers are intentionally not part of the runtime architecture.

## Requirements

- Node.js **22.5+** (tested here with 22.16.0). The app uses Node's built-in SQLite module, which Node 22 currently labels experimental.
- Chrome or Edge for live bridge use.
- Optional: a current `codex` CLI with `app-server` support for embedded agent features. The library works without model authentication.

## Start on Windows

```powershell
cd tab-atlas
npm start
```

Open <http://127.0.0.1:8790>.

No `npm install` is required because the application has no external npm dependencies. `package-lock.json` is committed for reproducible metadata.

To use demo data without touching a real browser:

```powershell
npm run demo
npm run demo:start
```

Demo content is stored in a separate ignored `state-v2-demo/` database and is explicitly synthetic; it never shares the default real-library database in `state-v2/`.

## Pair Chrome or Edge

1. Start TabAtlas.
2. In the workspace click **Pair Chrome** or **Pair Edge** and copy the generated token.
3. Open `chrome://extensions` or `edge://extensions`, enable Developer mode, choose **Load unpacked**, and select this repository's `extension` folder.
4. Open **TabAtlas Bridge**, paste the token, choose the matching browser, and save pairing.
5. Enable the bridge. When disabled, its alarm is cleared and it neither polls TabAtlas nor queries tabs.

The bridge only talks to `127.0.0.1:8790`. Pairing credentials remain in the ignored private database and extension local storage; they are never committed.

## Daily workflow

- **Sync paired browsers** requests a bounded capture. Known canonical URLs update provenance rather than becoming duplicate library resources.
- Search/filter and browse all durable resources even when analysis is pending or evidence is limited.
- Add typed guidance from a resource detail view, or optionally dictate into the draft using the browser speech service. Dictation is never auto-saved: review the text first; TabAtlas does not claim browser speech stays local.
- Refresh bounded public evidence to obtain readable text and safe previews. Public metadata preview URLs are checked so private-network image targets are not loaded.
- **Organize visible** creates reviewable suggestions for the loaded/selected scope; **Organize all filtered** is explicitly broader. Approve, reject, or add guidance and rethink individual proposals.
- **Organize for me** is an explicit bounded delegation: it automatically applies only reversible collection changes in the visible/selected scope and records undo history.
- **Reconsider whole library** uses bounded 30-resource agent cohorts plus a compact global library summary, rather than one ever-growing prompt.
- **Find by intent** creates a temporary semantic working set when the agent is connected (lexical fallback otherwise); searching never mutates the library.
- Apply suggestions in bulk. A resource can belong to more than one nested collection. Archive, delete, remove-membership, and live-tab close remain distinct actions with reversible history for library mutations.
- Use the extension's **Save current page** action to avoid new backlog. Closing is opt-in and occurs only after the app has durably saved the page and the extension re-checks the exact live target.

## Agent connection

The runtime adapter follows the official app-server lifecycle: `initialize` → `initialized`, then `account/read`, `model/list`, `thread/start`, `turn/start`, streamed notifications, and `turn/interrupt` for cancellation. The workspace populates model and reasoning-effort controls from `model/list`; this implementation chat's model is never hardcoded into the product.

The agent runs from an isolated `state-v2/agent-context` working directory with read-only restricted sandbox settings. Page content and notes are serialized inside explicit data delimiters and are not treated as instructions. The agent only returns organization proposals; database and browser effects remain application-owned.

Live ChatGPT login uses the app-server-managed browser flow. TabAtlas never asks for, copies, stores, or exposes ChatGPT access tokens.

## Import and export

- `GET /api/export` returns a versioned `tabatlas-export` JSON snapshot containing library resources, occurrences, notes, collections/memberships, and cached evidence. Pairing credentials and runtime/account settings are excluded.
- `POST /api/import` accepts that format and merge-imports it, or accepts the documented capture fixture shape used by tests/demo.
- The rebuild uses `state-v2/` so it does not overwrite an older TabAtlas database.
- Direct migration of a legacy TabAtlas SQLite database has **not** been tested and is intentionally not automatic. Export/import is the supported boundary for this release.

## Verification

```powershell
npm run check
npm test
```

Focused acceptance coverage includes multi-browser/partial capture, preserved query identity, replay idempotency, group provenance, 1,000-resource paging/filtering, note-driven nested multi-membership organization and undo, delegated reversible changes, archive/delete recovery, temporary intent working sets, 30-item agent cohorting, checkpoint resume after process restart plus stale result rejection, malformed model output rejection, public-evidence SSRF/preview protection, current-page duplicate save and exact-target close refusal (including fragment changes), and a deterministic fake transport that exercises the real app-server adapter handshake/account/model/thread/turn event path.

See [ARCHITECTURE.md](ARCHITECTURE.md), [TEST_RESULTS.md](TEST_RESULTS.md), and [docs/HANDOFF.md](docs/HANDOFF.md).
