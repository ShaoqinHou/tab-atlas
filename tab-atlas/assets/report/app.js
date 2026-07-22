(function loadTabAtlasModules() {
  "use strict";
  const source = document.currentScript && document.currentScript.src
    ? document.currentScript.src
    : "app.js";
  const modules = [
    "modules/state.js",
    "modules/views.js",
    "modules/gallery.js",
    "modules/notes.js",
    "modules/details.js",
    "modules/navigation.js",
    "modules/actions.js",
    "modules/workspace.js",
    "modules/sync.js",
    "modules/agent.js",
    "modules/utilities.js",
    "modules/bootstrap.js"
  ];
  for (const modulePath of modules) {
    const moduleUrl = new URL(modulePath, source).href;
    document.write(`<script src="${moduleUrl}"><\/script>`);
  }
})();
