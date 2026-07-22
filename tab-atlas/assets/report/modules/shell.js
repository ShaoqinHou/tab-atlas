// Static application shell and stable DOM references. Loaded after state.js.
const app = document.getElementById("app");
app.innerHTML = `
  <header class="topbar">
    <button id="homeBrand" class="brand" type="button" aria-label="Open TabAtlas home">
      <span class="brand-mark" aria-hidden="true">TA</span>
      <span class="brand-copy"><strong>TabAtlas</strong><small id="freshness"></small></span>
    </button>
    <label class="search-wrap">
      <span class="sr-only">Search tab library</span>
      <input id="search" class="search" type="search" placeholder="Search titles, summaries, spaces, topics, and sources" aria-label="Search tab library">
    </label>
    <div id="syncControl" class="sync-control" hidden>
      <button id="syncNow" class="sync-command" type="button">Sync now</button>
      <span class="sync-copy" role="status" aria-live="polite">
        <strong id="syncStatus" class="sync-status">Ready</strong>
        <small id="syncDetail" class="sync-detail">No browser scan has run in this workspace.</small>
      </span>
    </div>
  </header>
  <nav id="primaryNav" class="primary-nav" aria-label="Primary views" role="tablist">
    <button type="button" role="tab" data-view="home">Home</button>
    <button type="button" role="tab" data-view="library">Library</button>
    <button type="button" role="tab" data-view="review">Review <span id="navLiveCount" class="nav-count" hidden>0</span></button>
  </nav>
  <main id="screen" class="screen"></main>
  <div id="drawerHost"></div>
  <button id="agentToggle" class="agent-toggle" type="button" aria-expanded="false" aria-controls="agentPanel" hidden>Ask Codex <span id="agentPending" class="agent-pending" hidden>0</span></button>
  <aside id="agentPanel" class="agent-panel" aria-label="TabAtlas Codex assistant" hidden>
    <header class="agent-panel-head">
      <div><strong>Codex</strong><span id="agentStatus">On demand</span></div>
      <button id="agentClose" class="agent-close" type="button" aria-label="Close Codex panel">&times;</button>
    </header>
    <p id="agentScope" class="agent-scope"></p>
    <div id="agentMessages" class="agent-messages" aria-live="polite"></div>
    <form id="agentForm" class="agent-form">
      <label class="sr-only" for="agentInput">Ask Codex about this library</label>
      <textarea id="agentInput" rows="3" maxlength="32768" placeholder="Ask about this resource or library"></textarea>
      <div class="agent-form-actions">
        <button id="agentOwnership" class="secondary-command" type="button">Open in Codex</button>
        <button class="primary-command" type="submit">Send</button>
      </div>
    </form>
  </aside>
  <div id="actionStatus" class="sr-only" role="status" aria-live="polite" aria-atomic="true"></div>`;

const elements = {
  topbar: document.querySelector(".topbar"),
  homeBrand: document.getElementById("homeBrand"),
  freshness: document.getElementById("freshness"),
  search: document.getElementById("search"),
  syncControl: document.getElementById("syncControl"),
  syncNow: document.getElementById("syncNow"),
  syncStatus: document.getElementById("syncStatus"),
  syncDetail: document.getElementById("syncDetail"),
  primaryNav: document.getElementById("primaryNav"),
  navLiveCount: document.getElementById("navLiveCount"),
  screen: document.getElementById("screen"),
  drawerHost: document.getElementById("drawerHost"),
  agentToggle: document.getElementById("agentToggle"),
  agentPending: document.getElementById("agentPending"),
  agentPanel: document.getElementById("agentPanel"),
  agentClose: document.getElementById("agentClose"),
  agentStatus: document.getElementById("agentStatus"),
  agentScope: document.getElementById("agentScope"),
  agentMessages: document.getElementById("agentMessages"),
  agentForm: document.getElementById("agentForm"),
  agentInput: document.getElementById("agentInput"),
  agentOwnership: document.getElementById("agentOwnership"),
  actionStatus: document.getElementById("actionStatus")
};
