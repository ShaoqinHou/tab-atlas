# Architecture

## Product Boundary

The user operates TabAtlas by talking to Codex. The workspace supplies local,
deterministic capabilities; it does not embed a model runtime.

```text
Chrome / Edge extension
        |
        | authenticated loopback command
        v
one-shot receiver -> raw capture -> SQLite catalog
                                      |-- staged discoveries
                                      |-- durable accepted library
                                      |-- bounded Codex batches
                                      `-- static local report
```

The agent-operated cycle is:

```text
refresh -> review discoveries -> accept or dismiss -> enrich and reconsider
        -> report -> optionally run a verified archive
```

## Browser Extension And Receiver

Use one Manifest V3 extension package in Chrome and Edge.

- `OFF`: clear the polling alarm; do not query tabs or contact the receiver.
- `ON`: use one low-frequency alarm to check the loopback receiver. Read windows,
  tabs, and groups only after an authenticated bounded command is available.
- Register no tab, window, or group change listeners.
- Keep permissions limited to alarms, storage, tabs, tab groups, and loopback.

The receiver is not scheduled and does not start with Windows. A CLI operation
owns its lifetime. Bind only to `127.0.0.1`, validate size and schema, and use
nonce-bound HMAC proofs for receiver identity, commands, snapshots, results, and
cleanup. Write raw snapshots atomically before importing them.

Ordinary capture does not focus, navigate, group, move, or close tabs. It works
with paired extensions in browsers that are already running. Automatic launch of
normal browser profiles is not part of the implemented capture path.

## Catalog State

Keep three concepts separate:

1. A live observation preserves browser, window, group, order, title, exact URL,
   and tab state for a specific capture.
2. A staged discovery is a newly observed canonical resource awaiting accept or
   dismiss.
3. An accepted resource is a durable library record that survives tab closure.

Canonical identity prevents a known resource from being staged again. A later
capture updates its observations and provenance. Dismissed resources remain
outside the accepted library and remain visible to safety checks while open.

Semantic organization uses **Space -> Topic -> Focus**. A Project is a
cross-cutting overlay and does not replace this hierarchy. Codex should
periodically reconsider the accepted library as a whole when new accepted
resources reveal a better grouping; stable high-confidence memberships should
not churn without evidence.

## Presentation

Generate a self-contained local HTML report. It supports discovery review,
purpose-first navigation, hierarchy filters, Project overlays, global search,
and progressive resource detail. Resource lists append in bounded batches through
infinite scrolling so large libraries remain responsive. Browser groups preserve
working context and tab order but remain secondary filters.

Presentation evidence has three layers:

1. Deterministic local signals for source, format, intent, duplicates, groups,
   and safe previews.
2. Codex annotations for concise summaries, decision context, and next actions.
3. Selective source inspection when the first two layers cannot support a useful
   decision.

Cache only allowlisted public preview media during explicit enrichment. Do not
crawl arbitrary URLs, access authenticated pages, or load remote media when the
report opens.

## Mutation Protocols

Exact-duplicate cleanup and archive-all are distinct one-shot protocols. Each
requires its own bounded approval, fresh capture, authenticated target plan,
extension-side revalidation, post-action capture, and private audit.

Archive-all adds stronger durability and completeness checks:

1. Capture the requested running browsers immediately before planning and bind
   the plan to the exact capture IDs returned by that receiver run. Receiver
   arrival order, rather than the browser's wall clock, defines current state.
2. Block if any live resource is pending review or was dismissed.
3. Verify catalog integrity, raw capture evidence, accepted resource records, and
   an integrity-checked private database backup.
4. Bind every target to its tab ID, exact URL hash, window, and group.
5. Create one temporary pinned inactive extension control tab per affected browser.
6. Revalidate each target in the extension immediately before closing it.
7. Capture again and require a real newer trusted capture in which every target
   is both reported closed and absent.
8. Remove the control tab before submitting its authenticated cleanup result, and
   retain the actual outcome in ignored audit evidence.

The accepted library remains available after successful closure. A failure stops
the protocol and preserves its evidence; it does not silently claim completion.

## Test Boundary

Browser end-to-end tests use bundled Chromium or isolated disposable Chrome and
Edge profiles with test data. They must not read, mutate, launch, or close the
user's normal browser profiles. Production captures use the installed, paired
extension only when the user asks the agent to operate TabAtlas.
