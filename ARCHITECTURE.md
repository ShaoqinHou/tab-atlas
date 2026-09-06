# TabAtlas architecture

## Responsibility map

| Boundary | Owns |
| --- | --- |
| `public/` | Responsive workspace projection: truthful totals, filters, selection scopes, nested collections, detail/notes, proposal application, progress/error states. |
| `src/domain/` | Conservative resource identity and stable hashes. No SQL, browser, or runtime imports. |
| `src/db/` | SQLite schema, transactions, revisions, checkpoints, history, evidence/proposal persistence, import/export. |
| `src/browser/` | Pairing, bounded command queue, current-page save, exact-target close protocol, observed browser outcomes. |
| `src/evidence/` | Replaceable public evidence adapter with SSRF/private-address rejection, byte/time bounds, honest limitations. |
| `src/organization/` | Deterministic fallback and bounded agent prompt/result contracts. User notes and pinned decisions are higher-priority evidence. |
| `src/app/` | Durable job orchestration, resume/cancel/stale validation, proposal publication/application, reversible delegation, and temporary intent working sets. |
| `src/runtime/` | Codex app-server process/JSONL protocol, account/model discovery, managed login, turn lifecycle/cancellation. |
| `src/http/` | Composition-facing HTTP commands. UI and agent-triggered work use application/store invariants rather than direct SQL/browser access. |

## Durable identities

A resource, browser occurrence, note, evidence artifact, collection membership, proposal, job, job item, browser action, and agent thread/turn are distinct identities. Model memory is never the library database.

Resource canonicalization deliberately preserves path and query data. It removes only the fragment, lowercases scheme/host, and removes default ports. This avoids collapsing distinct authenticated/private/search URLs.

## Organization model

Collections are nested and many-to-many with resources. Browser groups and source/browser provenance remain occurrence metadata rather than being forced into the collection hierarchy. The deterministic organizer is a functional fallback and test oracle; the Codex adapter can return the same validated proposal contract. Applying proposals is a separate authorized command.

A proposal stores the resource revision it analyzed. Jobs additionally store an evidence fingerprint. If the user edits a note or evidence changes before publication/resume, the old item is marked stale rather than overwriting newer intent.

## Browser safety

The MV3 bridge has explicit ON/OFF state. OFF clears its alarm. ON performs a low-frequency loopback poll but reads tabs only when a bounded command is waiting.

Current-page `save + close` is a two-effect protocol:

1. extension reads current HTTP(S) tab;
2. server durably captures the resource and optional note;
3. server records a close action containing tab id, window id, and exact URL;
4. extension re-reads the live tab;
5. it closes only when all target fields still match;
6. extension reports observed success/failure/refusal, which is persisted independently of the save.

Duplicate command IDs replay the saved capture result rather than duplicating occurrences/notes. Resource identity may ignore URL fragments, but close authorization does not: the live tab id, window id, and exact browser URL string must all still match.

## Evidence safety

Public enrichment does not receive cookies or browser profile access. Readable text/preview metadata are cached as evidence; direct image resources use their already-public URL as the preview. Preview URLs extracted from remote metadata are separately checked before the UI may load them. Before network fetch, hostname resolution is rejected for loopback/private/link-local addresses. Responses are time/size bounded. YouTube currently uses public oEmbed metadata and explicitly reports that transcript/caption acquisition is not implemented by that adapter; transcript absence does not fail the library.

## Agent boundary

The app-server adapter implements the supported stdio JSONL connection and managed ChatGPT login path. Runtime availability is optional. The model list and effort choices are discovered from app-server rather than inferred from this development conversation.

Organization tasks send cohorts of at most 30 resources as delimited JSON data plus an output JSON Schema. Whole-library passes also receive only a compact global summary (counts/top hosts/current collection paths), not an ever-growing library prompt. Saved jobs retain a contract version and evidence/revision fingerprints so stale or incompatible work is not silently published. The runtime cwd is the private `state-v2/agent-context` directory with read-only restricted access. No project repository, browser profile store, arbitrary MCP server, inherited AGENTS.md, or custom skill pack is intentionally provided to the user-facing organization assistant.

## Known design limits

- Node 22 labels built-in SQLite experimental. This keeps setup dependency-free; a future migration can swap the store implementation without changing domain/browser/runtime contracts.
- Public evidence adapters are intentionally modest in this first complete release. Rich platform-specific transcript and article extraction can be added behind `PublicEvidenceService` without changing persistence ownership.
- The UI exposes text notes. Browser speech recognition/local transcription is not shipped because claiming local voice privacy without a verified transcription runtime would be misleading. Voice remains a replaceable future adapter with text fallback.

## Temporary working sets and lifecycle

Intent search returns a transient projection and never writes memberships or resource state. Agent-backed search is bounded to an 80-item prefiltered shortlist and falls back to explicit lexical matching when no runtime is connected. Archive, delete, collection removal, proposal rejection, and browser-tab close are separate commands. Archive/delete/membership changes record reversible history; deleting from the library never implies closing a live browser tab.
