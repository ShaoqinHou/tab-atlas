# Architecture

## Product Boundary

The user operates TabAtlas by talking to Codex. The workspace supplies deterministic local capabilities:

```text
Chrome / Edge extension
        |
        | authenticated loopback capture
        v
one-shot Python receiver -> raw snapshot -> SQLite catalog
                                             |
                                             +-> bounded Codex batches
                                             +-> static local HTML report
```

There is no embedded model runtime. The current Codex agent performs semantic work and writes structured results through the CLI.

## Browser Extension

Use one Manifest V3 package in Chrome and Edge.

- `OFF`: clear the polling alarm. Do not query tabs or perform network requests.
- `ON`: create one 30-second alarm. On each alarm, ask the loopback receiver whether a bounded command is pending. Query windows, tabs, and tab groups only after an authenticated capture command. A mutation command is accepted only for the exact-duplicate protocol below.
- Register no tab, window, or group change listeners. Hundreds of tabs must not create background event churn.
- Request only `alarms`, `storage`, `tabs`, and `tabGroups`, plus loopback host access.
- Use a fixed public manifest key so unpacked installs keep a stable extension ID.

The receiver cannot wake a fully dormant extension directly. The 30-second alarm is the smallest browser-supported automatic rendezvous without a persistent native process or WebSocket heartbeat.

## Receiver

The receiver is not scheduled and does not start with Windows. `capture` or `pair` owns its complete lifetime.

- Bind only to `127.0.0.1`.
- Pair with a random 256-bit token, then retain only its SHA-256 verifier in
  SQLite and extension storage. Every command and snapshot uses nonce-bound HMAC
  proofs; no reusable token or verifier crosses the loopback socket.
- Require the receiver to prove the same pairing key before the extension reads
  tabs. A process that merely occupies port `9786` cannot solicit a snapshot.
- Limit request size and validate every payload.
- Write raw snapshots atomically before importing them.
- Stop after all requested browsers respond or the timeout expires.
- Never open or focus a browser window. The ordinary capture receiver never mutates a tab.
- Do not expose a generic health endpoint. An older local exporter used one as
  its signal to collect tabs, so the authenticated command endpoint is the only
  capture rendezvous.

## Catalog

Keep raw browser observations distinct from deduplicated resources.

- A capture preserves browser, window, group, ordering, title, exact URL, and state.
- A resource represents a canonical URL across captures and browsers.
- One `space` expresses the user's primary purpose, up to two `topic` collections
  refine it, and `project` collections overlay active work without replacing purpose.
- Brief, detail, why-kept, and next-action fields support progressive disclosure.
- Tasks represent work derived from resources; they do not mutate browser state.

The latest trusted capture per browser defines the current inventory. Older
captures remain provenance and recovery evidence. Experimental closed-browser
results are stored as candidates and cannot replace current inventory until a
separate review promotes them.

## Presentation

Generate a self-contained HTML decision report. It queues local decisions but does
not directly mutate browser state. It must support scanning first and detail on
demand without a long-running app server, build chain, or account.

The default view is a decision overview, not a complete resource list. Purpose
spaces are the primary navigation, topics refine a selected purpose, and browser
groups remain contextual filters with stable IDs and preserved tab order. A new
space resets filters that no longer apply; an explicitly selected browser group
then narrows the visible resources inside that space.

Presentation metadata has three layers:

1. Deterministic local signals for source, format, intent, duplicates, groups,
   and safe generated previews.
2. Codex annotations for concise descriptions, decision context, and next steps.
3. Selective source inspection only when the first two layers cannot support a
   concrete decision.

Cache known public video thumbnails into private local state during an explicit
`enrich` run, validate their origin and media type, then copy them into the static
report. Do not load remote thumbnails when the report opens. Do not crawl arbitrary
tab URLs or authenticated pages. Local decision controls store only resource IDs
and proposed statuses and export an annotation-compatible JSON file.

## Exact Duplicate Mutation

Exact duplicate cleanup is a separate one-shot receiver workflow:

1. Capture the requested browsers immediately before planning.
2. Plan only byte-identical HTTPS URLs in the same browser, window, and group.
3. Retain one keeper and exclude active, pinned, audible, highlighted, file,
   browser-internal, local HTTP, cross-window, and cross-group tabs.
4. Bind tab IDs and URL hashes to an authenticated receiver command.
5. Revalidate the target URL, keeper URL, protection state, window, and group in
   the extension immediately before each close.
6. Submit per-tab outcomes through an authenticated result message, capture again,
   and retain an ignored append-only audit containing plan and post-capture IDs.

Canonical URL matches are retrieval hints only and are never mutation evidence.

## Deferred Boundary

Closed-browser recovery is not ordinary capture. The production fallback is the
last trusted capture, with its per-browser age shown clearly. Never launch a
normal profile, headless or otherwise: browser startup can alter session state,
and modern Chrome refuses remote debugging against its default data directory.

Any experimental recovery must require the browser to be fully stopped, copy
only allowlisted session artifacts into immutable evidence, verify source hashes
before and after, restore through a second disposable writable derivative with
external networking blocked, and store the result as a non-authoritative
candidate. Encrypted or incompatible session data fails back to the trusted
capture; it never broadens into copying cookies, history, passwords, storage,
extensions, or encryption keys.
