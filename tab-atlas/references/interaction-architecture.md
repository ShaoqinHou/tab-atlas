# Interaction Architecture

## Product Model

TabAtlas is one on-demand local workspace, not a browser extension with a report
attached and not a second general-purpose chat client.

```text
Chrome / Edge extension  -> capture and verified browser mutations only
TabAtlas workspace       -> SQLite, notes, organization, progress, UI, audit
Dedicated Codex task     -> optional interpretation, proposals, and navigation
```

The extension is passive while no receiver is running. The workspace server and
its Codex app-server child exist only for an explicit interactive session. The
static HTML report remains a read-only export and offline fallback.

## Authority And Provenance

Organization decisions use this precedence:

1. A direct user lock, exclusion, or progress decision.
2. The user's active note and its stored interpretation.
3. Previously accepted stable organization.
4. A Codex inference from page evidence.
5. Metadata, source, and captured browser-group heuristics.

The original typed note or recording is immutable local evidence. Interpretation
never overwrites it. An edit creates a successor note. A failure to reach Codex
leaves the note saved and the work queued; it does not make the note disappear or
fall back to a fake inference.

One resource has one primary `Space -> Topic -> Focus` path for predictable
navigation. It may also belong to any number of Projects and Action Lists.
Action Lists model intentions such as `Must Watch`, with queue state, priority,
estimated effort, progress, and optional daily targets. They are not taxonomy.

## Codex Boundary

TabAtlas uses a dedicated persistent Codex task through the local Codex app
server. Authentication comes from the user's existing ChatGPT sign-in; TabAtlas
does not ask for or store an API key. The task can be opened in Codex with a
`codex://threads/<thread-id>` deep link.

The workspace sends bounded resource context and receives output constrained by
a JSON schema. Notes are evidence, never executable instructions. Codex does not
write SQLite or control the DOM. It returns:

- a concise interpretation;
- proposed primary and overlay memberships;
- an optional Action List item;
- an optional declarative view command.

The local workspace validates proposals. A proposal remains inert until accepted
or covered by explicit delegated authority. Applying one increments the resource
semantic revision, records before and after state, and exposes Undo. Browser tab
closure remains under the stronger capture-bound mutation protocol.

One process owns the dedicated task at a time. The workspace holds an ownership
lock across every app-server turn. An explicit handoff can proceed only while the
lock is idle; it stops the child process before exposing the task deep link.
Reclaiming restarts the child and resumes the task by its stored opaque ID. Queued
requests remain in SQLite while Codex desktop owns the task. During workspace
ownership, the child stops after two idle minutes and resumes on demand; no
heartbeat or background model turn is used.

## Interaction Model

The workspace has three primary destinations:

- **Home**: one continuation path, Action Lists, and purpose spaces.
- **Library**: all stored resources, switchable between Purpose and Source lenses.
- **Review**: new discoveries, uncertain items, duplicates, and captured tabs.

A resource inspector starts with `Your note`, followed by Codex's interpreted
meaning, memberships, next action, and collapsed source evidence. Cards show only
a note indicator; they do not repeat full note and microphone controls.

A compact agent control opens a narrow panel. Its responses may issue validated
commands such as `open_resource`, `show_collection`, or `set_filter`. The UI
announces and highlights agent navigation and provides a way back. It never asks
Codex to simulate rapid clicks.

## Voice Boundary

A recording is captured and saved locally before any processing. Playback and
deletion do not require Codex. Transcription is a separate, visible state. The
stable text-turn protocol receives only an editable transcript. Direct audio
transcription through Codex realtime is experimental and must remain an optional
adapter; failure cannot block typed notes or the rest of the workspace.

## Delivery Order

1. Text note -> stored note -> Codex interpretation -> applied organization ->
   visible result -> Undo -> restart persistence.
2. Local voice recording -> playback -> editable transcript -> the same
   interpretation pipeline.
3. Scoped agent panel -> declarative navigation and result sets.
4. Refresh-time bounded reconsideration of related resources without whole-
   library taxonomy churn.

This order is the release boundary. New special-case subsystems and broad UI
redesigns wait until the complete slice above is reliable.
