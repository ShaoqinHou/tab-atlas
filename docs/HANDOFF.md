# Handoff and local qualification

## Verified in the delivery sandbox

- Node syntax checks and required-file checks.
- Full Node test suite: 14/14 focused tests passing on Node 22.16.0 in the delivery sandbox.
- SQLite persistence and 1,000-resource bounded listing/filtering.
- Multi-browser fixture with tab groups, repeated URLs, distinct query URLs, and partial browser failure.
- Replay-idempotent bridge capture/current-page save.
- Multi-membership organization driven by an explicit user note; reversible membership undo; note survives rerun path.
- Durable job pause/resume, 30-resource agent cohorting, reuse of completed results, real store restart recovery, and stale rejection after a newer note edit.
- Exact browser close refusal when query or URL fragment changes; durable save remains intact.
- Explicit delegated organization with nested collection paths, reversible apply/undo, archive/delete recovery, and non-mutating intent working sets.
- Public-evidence private-host/redirect protection and rejection of private-network preview image URLs.
- Controlled fake `codex app-server` transport covering initialize/initialized, account/read, model/list, thread/start, turn/start, streamed delta, turn/completed, and process exit handling.
- Local HTTP server smoke check for dashboard/resources.

## Still requires the coordinator's Windows/browser/account environment

1. `node --version` should be 22.5+; run `npm run check && npm test`.
2. Run `npm run demo && npm run demo:start`, open `http://127.0.0.1:8790`, and inspect at a wide desktop viewport and a narrow/mobile-sized viewport. Confirm cards, selection scopes, nested collection navigation, detail dialog, and load-more behavior preserve useful orientation.
3. Load `extension/` unpacked in current Chrome and Edge. Pair each browser and verify OFF performs no polling/tab query; ON handles a capture with real browser groups.
4. Save the current page with and without close-after-save. Change/navigate the tab before a queued close and confirm it is left open with a refused action; confirm a matching target closes only that tab.
5. Ensure the installed `codex` CLI exposes `codex app-server`. Open the workspace and check runtime status. If signed out, use the managed ChatGPT login flow; verify `account/read` and `model/list` reflect the user's subscription and available effort levels.
6. Run one real organization task using the agent adapter. Compare the proposal to deterministic fallback and confirm no repository/global instruction files are loaded into the bounded agent context.
7. Exercise public evidence on representative article, YouTube, image, and X URLs from the user's network. Confirm unavailable/private content is reported as limited instead of guessed.

## Not claimed as tested

- Importing an old/legacy SQLite database directly.
- Live Chrome/Edge process behavior in the delivery sandbox.
- Live ChatGPT subscription authentication/model generation in the delivery sandbox.
- Platform transcript APIs beyond the shipped public metadata adapter.
- Voice transcription. No browser-cloud speech service is silently enabled.
