# Architecture

## Product Boundary

TabAtlas is one on-demand local workspace backed by a private SQLite catalog.
It is not a hosted service, a continuously running browser monitor, or a second
general-purpose chat client.

```text
Chrome / Edge extension
        |
        | authenticated bounded command
        v
loopback receiver -> capture -> SQLite catalog -> workspace
                                      |              |
                                      |              `-> reviewed writes
                                      `-> generated read-only report

Codex task <-> bounded context and inert proposals <-> workspace
```

Responsibilities are separate:

- The extension observes tabs and executes separately approved browser
  mutations.
- The workspace owns durable state, review, notes, organization, progress, and
  audits.
- Codex interprets bounded evidence and proposes actions; it is not the database
  and cannot mutate browser state through a suggestion.
- The generated report is a replaceable projection and offline fallback.

## Runtime Lifecycle

`workspace` starts one authenticated loopback server and automatically requests
one serialized sync from paired, enabled Chrome and Edge extensions. The current
catalog remains available while that sync is starting, waiting, importing, and
preparing allowlisted public review previews, and republishing. **Sync now**
invokes the same coordinator for tabs opened later. Terminal status retains the
per-browser tab counts, candidate count, preview result, and completion time.

Only one sync may run at a time. Concurrent requests join or report the current
job instead of starting competing receivers. Status exposes aggregate progress
and browser outcomes, never private tab content.

Sync performs deterministic capture and allowlisted preview preparation. It does
not invoke Codex. Semantic batch preparation is a separate, explicit active-task
operation; it may annotate candidates for review but cannot silently accept,
dismiss, or close them.

This is event-on-demand, not continuous collection. Workspace startup and an
explicit Sync now action are the events. The extension alarm merely checks for a
waiting loopback command; it does not stream browser state. A closed or
unavailable browser leaves its last trusted catalog observations intact.

The workspace, receiver, and optional Codex child are not scheduled and do not
start with Windows. They stop with the interactive session. TabAtlas does not
launch, copy, or remote-debug the user's normal browser profile.

Browser access uses a durable random credential stored in the private catalog.
One bootstrap navigation sets a persistent HttpOnly, host-only, SameSite cookie
in that browser. Bare unauthenticated navigation reveals no catalog data, and an
explicit access rotation revokes all previously authorized browser cookies.

## Extension And Receiver

One Manifest V3 extension package is loaded separately in Chrome and Edge.

- `OFF`: clear the alarm; perform no loopback check and no tab query.
- `ON`: perform one low-frequency loopback check. Query windows, groups, and
  tabs only after an authenticated bounded command is available.
- Register no tab, window, or group change listeners for continuous capture.

The receiver binds only to `127.0.0.1`, validates payload shape and size, and
uses nonce-bound proofs for pairing, command, snapshot, result, and cleanup
messages. Raw evidence is written atomically before import.

Read-only capture must not focus, navigate, move, regroup, or close tabs. Pairing
authorizes communication, not mutation. Mutation requires its own fresh plan and
approval.

## Catalog Model

SQLite is the sole durable authority. Keep these concepts distinct:

1. A **capture** records browser, window, group, order, title, exact URL, and tab
   state at one trusted observation.
2. A **candidate** is a previously unseen canonical resource awaiting review.
3. An **accepted resource** is a durable library entry that survives tab closure.
4. A **dismissed resource** is a reviewed rejection retained for recovery,
   deduplication, and close safety but excluded from normal library queries.

Canonical identity prevents repeated discoveries. A later observation of a
known resource updates provenance and live context without creating another
semantic record. Capture never silently promotes a candidate. Accept, Dismiss,
Restore, and Remove are catalog decisions and do not mutate browser tabs.

The report and workspace read models are derived from one versioned catalog
snapshot. Regeneration may replace the report atomically; it must never become a
second source of truth.

## Organization And Authority

One resource has at most one primary purpose path:

```text
Space -> optional Topic -> optional Focus
```

Projects and Action Lists are orthogonal many-to-many overlays. Source,
platform, owner, channel, domain, browser, and captured group are facets. They do
not replace the purpose hierarchy.

Organization evidence uses this precedence:

1. Direct user locks, exclusions, and progress decisions.
2. The user's active note and stored interpretation.
3. Previously accepted stable organization.
4. Codex inference from bounded page evidence.
5. Metadata, source, and browser-group heuristics.

The complete accepted library may be reconsidered after new acceptance reveals
a clearer grouping. Stable high-confidence memberships should not churn without
new evidence. Weakly evidenced resources remain in Inbox.

## Notes, Voice, And Codex

The original typed note or recording is immutable local evidence. Editing
creates a successor. Voice is saved before processing, transcribed by a
short-lived local worker, and remains available if transcription fails. Audio is
not sent to Codex.

The user's note outranks metadata. Saving a note does not start a model turn.
After an explicit review request, the workspace sends a bounded transcript and
resource context to a dedicated Codex task using the user's existing sign-in.
No API key is collected by TabAtlas.

Codex output is schema-constrained and may contain:

- a concise interpretation;
- one proposed primary hierarchy path;
- optional Project or Action List changes;
- an optional declarative navigation command.

The workspace validates the result. A proposal remains inert until accepted,
must match the current semantic revision, records before and after state, and is
undoable. A changed note or transcript makes an older proposal stale. Agent
navigation is declarative and visible; Codex does not control the DOM or simulate
rapid clicks.

The workspace holds single ownership of its dedicated Codex task. Explicit
handoff to Codex desktop stops the local child first; reclaim resumes workspace
ownership. Queued requests stay in SQLite. No heartbeat or background model turn
is used.

## Presentation

The workspace has three primary destinations:

- **Home** for continuation, Projects, Action Lists, and purpose Spaces.
- **Library** for Purpose and Source lenses over durable resources.
- **Review** for candidates, uncertain resources, duplicates, dismissed items,
  and currently captured tabs.

Cards show the smallest useful decision set. The inspector reveals notes,
interpretation, hierarchy, actions, provenance, and detailed evidence on demand.
Lists render in bounded batches and append on scroll.

Preview acquisition is explicit and allowlisted. Public image adapters may cache
decision-bearing thumbnails or posters during a requested Sync. A failed preview
does not invalidate an authenticated browser capture and enters a 24-hour retry
backoff so unchanged syncs do not repeatedly wait on the same unavailable media.
Remote video is loaded only for the one resource being interacted with and is
never persisted. Private, authenticated, internal, local, and weakly evidenced
pages remain metadata-only unless an appropriate local capture is deliberately
registered.

## Browser Mutations

Exact-duplicate cleanup and archive-all are separate protocols. Approval for one
never authorizes the other. Each requires:

1. A fresh trusted capture bound to the requested browsers.
2. A deterministic target plan and bounded approval.
3. Extension-side revalidation of tab ID, exact URL hash, window, and group.
4. Authenticated per-target results.
5. A real newer post-action capture proving planned closures are absent.
6. Private retained audit evidence.

Archive-all additionally requires zero candidates, catalog integrity, durable
accepted records, raw evidence, and an integrity-checked private database backup.
Dismissed live resources block closure by default. They can be included only
after explicit review with `--include-dismissed` and an approval whose scope
names retained and discarded targets. They remain dismissed and are audited as
discards.

A failure preserves evidence and reports the operation incomplete. Process exit,
browser disappearance, or stale inventory is not proof of closure.

## Implementation Boundaries

The package dependency direction is:

```text
primitives -> database/stores -> domain services -> read models
           -> workspace/report transports -> command line
```

Domain invariants belong in cohesive services, not HTTP handlers, CLI branches,
or the root package. SQLite writes use transactions. External contracts use
structured JSON with explicit validation. Generated files and private runtime
state are not source modules.

See `DEVELOPMENT.md` for the concrete module map and change locations.

## Test Boundary

Unit and integration tests use temporary databases. Browser end-to-end tests use
bundled Chromium or isolated disposable Chrome and Edge profiles. Tests must not
read, launch, automate, or mutate the user's normal browser profiles.

Tests should protect named behavior, persistence, protocol, privacy, and
mutation-safety invariants. Historical test counts and release milestones are
not architecture.
