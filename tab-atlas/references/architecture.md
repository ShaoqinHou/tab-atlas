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
- `ON`: create one 30-second alarm. On each alarm, ask the loopback receiver whether a capture is pending. Query windows, tabs, and tab groups only after an authenticated capture command.
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
- Never open a browser window or mutate a tab.
- Do not expose a generic health endpoint. An older local exporter used one as
  its signal to collect tabs, so the authenticated command endpoint is the only
  capture rendezvous.

## Catalog

Keep raw browser observations distinct from deduplicated resources.

- A capture preserves browser, window, group, ordering, title, exact URL, and state.
- A resource represents a canonical URL across captures and browsers.
- Collections express projects, themes, or workflows.
- Brief, detail, why-kept, and next-action fields support progressive disclosure.
- Tasks represent work derived from resources; they do not mutate browser state.

The latest trusted capture per browser defines the current inventory. Older
captures remain provenance and recovery evidence. Experimental closed-browser
results are stored as candidates and cannot replace current inventory until a
separate review promotes them.

## Presentation

Generate a self-contained, read-only HTML report. It must support scanning first and detail on demand. Do not require a long-running app server, build chain, or account.

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
