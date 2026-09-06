# TabAtlas delivery verification

Verification date: 2026-09-06 (sandbox environment)

## Environment

- Node.js: 22.16.0
- npm: 10.9.2
- Git: 2.47.3
- SQLite: Node built-in `node:sqlite` (Node 22 emits its experimental-feature warning)
- Local `codex` executable: not installed in this sandbox
- Chromium binary: present, but headless screenshot invocations did not terminate successfully in this container, so viewport screenshots are not claimed

## Commands run

```text
npm run check
npm test
```

Final result: **14 tests passed, 0 failed**.

Focused behaviors covered:

1. delegated nested organization, reversible membership apply/undo, archive/delete recovery;
2. temporary intent working set uses confirmed notes and does not mutate library state;
3. versioned export/import preserves resource identity, occurrences, browser/group provenance, notes, and memberships;
4. current-page duplicate save is idempotent and stale/exact close targets are protected, including URL fragment changes;
5. multi-browser partial capture preserves query identity, group provenance, duplicate occurrences, and replay idempotency;
6. public evidence rejects private hosts and revalidates redirects;
7. public evidence drops private-network preview URLs from page metadata;
8. job pause/resume retains completed units and rejects stale work after user edits;
9. agent organization is bounded into cohorts of 30;
10. a running durable job becomes resumable after a real SQLite store close/reopen;
11. user guidance outranks weak title metadata and supports multiple memberships;
12. 1,000-resource filtering/paging returns correct totals and bounded pages;
13. malformed model output is rejected rather than partially applied;
14. controlled fake `codex app-server` transport exercises initialize/initialized, account/model discovery, thread/turn lifecycle, streamed agent output, and completion.

## HTTP smoke checks

A local server was started against synthetic state and `/api/dashboard`, `/api/resources`, and `/api/runtime/status` were exercised. A 240-item demo returned a bounded 60-item first page with the correct total. With no `codex` executable installed, runtime status returned a typed `spawn_failed` offline state while the ordinary library server remained usable.

## Not live-validated here

- Chrome and Edge unpacked-extension installation, actual tab-group capture, and actual live close behavior on the user's Windows profiles.
- ChatGPT/Codex subscription sign-in and a real model turn; only the real adapter with deterministic transport was tested.
- Wide/narrow visual browser screenshots: Chromium exists in the sandbox, but its headless screenshot process hung in this container. Responsive CSS and data behavior are implemented, but final viewport qualification remains a local Windows/browser check.
- Direct migration of an old TabAtlas SQLite database. The supported boundary is versioned JSON export/import.
- Platform-specific transcript acquisition beyond the shipped public metadata/readable-text adapters.
