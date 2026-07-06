// Vanilla JS dashboard client. Talks to the REST API (apps/api/main.py) at
// same-origin paths, so no CORS setup or build step is needed. Keeps its
// own list of drawn entities client-side purely for the preview — the
// backend is the source of truth for the actual drawing, this is just a
// coarse visualization, not a CAD-accurate renderer.

const state = { entities: [] };

async function api(path, options) {
  const response = await fetch(path, options);
  let body;
  try {
    body = await response.json();
  } catch {
    body = { detail: response.statusText };
  }
  if (!response.ok && !("success" in body)) {
    throw new Error(body.detail || `HTTP ${response.status}`);
  }
  return body;
}

function logEntry(container, ok, title, detail) {
  const el = document.createElement("div");
  el.className = `entry ${ok ? "success" : "failure"}`;
  el.innerHTML = `<div>${title}</div>${detail ? `<div class="meta">${detail}</div>` : ""}`;
  container.prepend(el);
  while (container.children.length > 25) container.removeChild(container.lastChild);
}

async function refreshHealth() {
  const statusEl = document.getElementById("status");
  try {
    const health = await api("/health");
    statusEl.textContent = `backend: ${health.backend} (${health.backend_running ? "running" : "idle"})`;
    statusEl.className = "status ok";
  } catch (err) {
    statusEl.textContent = "unreachable";
    statusEl.className = "status error";
  }
}

let toolSchemas = {};

async function loadTools() {
  const tools = await api("/tools");
  const select = document.getElementById("tool-select");
  select.innerHTML = "";
  for (const tool of tools) {
    toolSchemas[tool.name] = tool.input_schema;
    const opt = document.createElement("option");
    opt.value = tool.name;
    opt.textContent = tool.name;
    select.appendChild(opt);
  }
  select.addEventListener("change", showSchema);
  showSchema();
}

function showSchema() {
  const name = document.getElementById("tool-select").value;
  document.getElementById("tool-schema").textContent = JSON.stringify(toolSchemas[name] || {}, null, 2);
}

function handleToolResult(result, logContainer, contextLabel) {
  if (result.entity) state.entities.push(result.entity);
  if (result.success) {
    refreshPreview();
    logEntry(logContainer, true, contextLabel, result.message || "ok");
  } else {
    logEntry(logContainer, false, contextLabel, result.message || "failed");
  }
  const warnings = result.warnings || [];
  const autofixed = result.autofixed || [];
  for (const w of warnings) logEntry(logContainer, true, `warning: ${w.code}`, w.message);
  for (const f of autofixed) logEntry(logContainer, true, `autofixed: ${f.code}`, f.message);
}

function setupChat() {
  const form = document.getElementById("chat-form");
  const input = document.getElementById("chat-input");
  const log = document.getElementById("chat-log");
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const command = input.value.trim();
    if (!command) return;
    logEntry(log, true, `> ${command}`, "");
    try {
      const result = await api("/tools/process_command", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ command }),
      });
      handleToolResult(result, log, result.message || "process_command");
    } catch (err) {
      logEntry(log, false, "request failed", err.message);
    }
    input.value = "";
  });
}

function setupToolCaller() {
  const button = document.getElementById("tool-call");
  const log = document.getElementById("tool-log");
  button.addEventListener("click", async () => {
    const name = document.getElementById("tool-select").value;
    let args;
    try {
      args = JSON.parse(document.getElementById("tool-args").value || "{}");
    } catch (err) {
      logEntry(log, false, "invalid JSON arguments", err.message);
      return;
    }
    try {
      const result = await api(`/tools/${name}`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(args),
      });
      handleToolResult(result, log, name);
    } catch (err) {
      logEntry(log, false, "request failed", err.message);
    }
  });
}

function setupValidator() {
  const button = document.getElementById("validate-call");
  const log = document.getElementById("validate-log");
  button.addEventListener("click", async () => {
    let operations;
    try {
      operations = JSON.parse(document.getElementById("validate-input").value || "[]");
    } catch (err) {
      logEntry(log, false, "invalid JSON", err.message);
      return;
    }
    try {
      const result = await api("/drawings/validate", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ operations }),
      });
      logEntry(log, result.is_valid, result.is_valid ? "valid" : "invalid", `${result.issues.length} issue(s)`);
      for (const issue of result.issues) {
        logEntry(log, issue.severity !== "error", `${issue.severity}: ${issue.code}`, issue.message);
      }
    } catch (err) {
      logEntry(log, false, "request failed", err.message);
    }
  });
}

function setupSaveAndClear() {
  const log = document.getElementById("preview-log");
  document.getElementById("save-call").addEventListener("click", async () => {
    const filePath = document.getElementById("save-path").value.trim();
    if (!filePath) {
      logEntry(log, false, "save", "enter a filename first");
      return;
    }
    try {
      const result = await api("/tools/save_drawing", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ file_path: filePath }),
      });
      logEntry(log, result.success, "save", result.message);
    } catch (err) {
      logEntry(log, false, "save failed", err.message);
    }
  });
  document.getElementById("clear-preview").addEventListener("click", async () => {
    try {
      const result = await api("/drawings/clear", { method: "POST" });
      state.entities = [];
      refreshPreview();
      logEntry(log, true, "drawing history cleared", result.message);
    } catch (err) {
      logEntry(log, false, "clear failed", err.message);
    }
  });
}

function setupExportButtons() {
  const log = document.getElementById("preview-log");

  // Ask the tool endpoint first (JSON, so it can surface a "hatch was
  // skipped" warning in the log) before navigating to the download
  // endpoint — the download itself is a plain navigation, since the
  // browser needs to see the Content-Disposition header to save the file.
  async function exportAndDownload(toolName, queryFormat) {
    try {
      const result = await api(`/tools/${toolName}`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: "{}",
      });
      logEntry(log, true, `export .${queryFormat === "lisp" ? "lsp" : queryFormat}`, result.warning || "ready");
    } catch (err) {
      logEntry(log, false, "export failed", err.message);
      return;
    }
    window.location.href = `/drawings/current/export?format=${queryFormat}`;
  }

  document.getElementById("export-scr").addEventListener("click", () => exportAndDownload("export_script", "scr"));
  document.getElementById("export-lisp").addEventListener("click", () => exportAndDownload("export_lisp", "lisp"));
}

// --- Projects ----------------------------------------------------------

async function syncPreviewFromServer() {
  const current = await api("/drawings/current");
  state.entities = current.operations || [];
  refreshPreview();
}

async function loadProjects() {
  const result = await api("/projects");
  const list = document.getElementById("project-list");
  list.innerHTML = "";
  for (const project of result.projects) {
    const card = document.createElement("div");
    card.className = "project-card";
    card.innerHTML = `
      <div>
        <div>${project.name}</div>
        <div class="meta">${project.id} · ${project.revisions} revision(s)</div>
      </div>
      <div class="actions">
        <button data-action="snapshot" data-id="${project.id}" class="secondary">Snapshot</button>
        <button data-action="load" data-id="${project.id}">Load</button>
      </div>`;
    list.appendChild(card);
  }
}

function setupProjects() {
  const log = document.getElementById("project-log");

  document.getElementById("project-save").addEventListener("click", async () => {
    const nameInput = document.getElementById("project-name");
    const name = nameInput.value.trim();
    if (!name) {
      logEntry(log, false, "save as project", "enter a project name first");
      return;
    }
    try {
      const result = await api("/projects", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ name }),
      });
      logEntry(log, result.success, result.message, result.project_id || "");
      nameInput.value = "";
      await loadProjects();
    } catch (err) {
      logEntry(log, false, "request failed", err.message);
    }
  });

  document.getElementById("project-list").addEventListener("click", async (event) => {
    const button = event.target.closest("button[data-action]");
    if (!button) return;
    const { action, id } = button.dataset;
    try {
      if (action === "snapshot") {
        const result = await api(`/projects/${id}/revisions`, {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({}),
        });
        logEntry(log, result.success, result.message, "");
        await loadProjects();
      } else if (action === "load") {
        const result = await api(`/projects/${id}/load`, { method: "POST" });
        logEntry(log, result.success, result.message, "");
        await syncPreviewFromServer();
      }
    } catch (err) {
      logEntry(log, false, "request failed", err.message);
    }
  });
}

// --- SVG preview -----------------------------------------------------
// Coordinates are plotted with y negated so the drawing reads with y "up"
// (engineering convention) despite SVG's native y-down coordinate system.

const flipY = (y) => -y;

function pathForArc(cx, cy, r, startDeg, endDeg) {
  const toXY = (deg) => {
    const rad = (deg * Math.PI) / 180;
    return [cx + r * Math.cos(rad), flipY(cy + r * Math.sin(rad))];
  };
  const [sx, sy] = toXY(startDeg);
  const [ex, ey] = toXY(endDeg);
  const sweep = ((endDeg - startDeg) % 360 + 360) % 360;
  const largeArc = sweep > 180 ? 1 : 0;
  return `M ${sx} ${sy} A ${r} ${r} 0 ${largeArc} 1 ${ex} ${ey}`;
}

function svgEl(tag, attrs) {
  const el = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [key, value] of Object.entries(attrs)) el.setAttribute(key, value);
  return el;
}

function entityToSvg(entity) {
  switch (entity.type) {
    case "line":
      return svgEl("line", {
        class: "entity-line",
        x1: entity.start[0], y1: flipY(entity.start[1]),
        x2: entity.end[0], y2: flipY(entity.end[1]),
      });
    case "circle":
      return svgEl("circle", {
        class: "entity-circle",
        cx: entity.center[0], cy: flipY(entity.center[1]), r: entity.radius,
      });
    case "arc":
      return svgEl("path", {
        class: "entity-arc",
        d: pathForArc(entity.center[0], entity.center[1], entity.radius, entity.start_angle, entity.end_angle),
      });
    case "ellipse":
      return svgEl("ellipse", {
        class: "entity-ellipse",
        cx: entity.center[0], cy: flipY(entity.center[1]),
        rx: entity.major_axis, ry: entity.minor_axis,
        transform: `rotate(${-entity.rotation} ${entity.center[0]} ${flipY(entity.center[1])})`,
      });
    case "polyline": {
      const pts = entity.points.map((p) => `${p[0]},${flipY(p[1])}`).join(" ");
      return svgEl(entity.closed ? "polygon" : "polyline", { class: "entity-poly", points: pts });
    }
    case "rectangle": {
      const [x1, y1] = entity.corner1;
      const [x2, y2] = entity.corner2;
      const pts = [[x1, y1], [x2, y1], [x2, y2], [x1, y2]].map((p) => `${p[0]},${flipY(p[1])}`).join(" ");
      return svgEl("polygon", { class: "entity-poly", points: pts });
    }
    case "hatch": {
      const pts = entity.points.map((p) => `${p[0]},${flipY(p[1])}`).join(" ");
      return svgEl("polygon", { class: "entity-hatch", points: pts });
    }
    case "text":
      // textContent is set by the caller (renderPreview), which has the
      // entity in scope already.
      return svgEl("text", { class: "entity-text", x: entity.position[0], y: flipY(entity.position[1]) });
    case "dimension":
      return svgEl("line", {
        class: "entity-dimension",
        x1: entity.start[0], y1: flipY(entity.start[1]),
        x2: entity.end[0], y2: flipY(entity.end[1]),
      });
    default:
      return null;
  }
}

function computeViewBox(entities) {
  const xs = [];
  const ys = [];
  const collect = (x, y) => { xs.push(x); ys.push(flipY(y)); };
  for (const e of entities) {
    if (e.start) collect(e.start[0], e.start[1]);
    if (e.end) collect(e.end[0], e.end[1]);
    if (e.center) {
      const r = e.radius || e.major_axis || 0;
      collect(e.center[0] - r, e.center[1] - r);
      collect(e.center[0] + r, e.center[1] + r);
    }
    if (e.corner1) collect(...e.corner1);
    if (e.corner2) collect(...e.corner2);
    if (e.position) collect(...e.position);
    if (e.points) for (const p of e.points) collect(p[0], p[1]);
  }
  if (!xs.length) return [-50, -50, 100, 100];
  const pad = 10;
  const minX = Math.min(...xs) - pad;
  const minY = Math.min(...ys) - pad;
  const width = Math.max(...xs) - minX + pad;
  const height = Math.max(...ys) - minY + pad;
  return [minX, minY, width, height];
}

function renderPreview() {
  const svg = document.getElementById("canvas");
  svg.innerHTML = "";
  svg.setAttribute("viewBox", computeViewBox(state.entities).join(" "));
  for (const entity of state.entities) {
    const el = entityToSvg(entity);
    if (!el) continue;
    if (entity.type === "text") el.textContent = entity.text;
    svg.appendChild(el);
  }
}

// --- Accurate render toggle ---------------------------------------------
// Two ways to see the drawing: the coarse hand-rolled SVG above (instant,
// client-side, approximate) or a real ezdxf render fetched from the
// server (accurate, one HTTP round trip). refreshPreview() keeps whichever
// is currently selected up to date; every draw/clear/load path calls it
// instead of renderPreview() directly.

let accurateRenderMode = false;

function refreshPreview() {
  if (accurateRenderMode) {
    // cache-bust: the browser must not reuse a stale render after a new draw
    document.getElementById("rendered-img").src = `/drawings/current/render?format=svg&t=${Date.now()}`;
  } else {
    renderPreview();
  }
}

function setupRenderToggle() {
  const toggle = document.getElementById("render-toggle");
  const canvas = document.getElementById("canvas");
  const img = document.getElementById("rendered-img");
  const hint = document.getElementById("preview-hint");

  // Visibility is managed entirely via inline style.display, deliberately
  // not the `hidden` attribute: canvas is an SVGElement, whose `.hidden`
  // IDL property does not reliably reflect to the content attribute the
  // way HTMLElement's does, and clearing an inline style (style.display =
  // "") does not override a UA-stylesheet `[hidden] { display: none }`
  // rule that's still matching. Inline display always wins either way.
  img.style.display = "none";

  toggle.addEventListener("click", () => {
    accurateRenderMode = !accurateRenderMode;
    canvas.style.display = accurateRenderMode ? "none" : "";
    img.style.display = accurateRenderMode ? "" : "none";
    toggle.textContent = accurateRenderMode ? "Show coarse preview" : "Show accurate render";
    hint.textContent = accurateRenderMode
      ? "A real, CAD-accurate SVG rendered server-side (ezdxf), fetched fresh on every change."
      : "Rendered client-side from what has actually been drawn this session — "
        + "not a full CAD-accurate renderer, just enough to see the plan take shape.";
    refreshPreview();
  });
}

async function init() {
  await refreshHealth();
  await loadTools();
  await loadProjects();
  await syncPreviewFromServer();
  setupChat();
  setupToolCaller();
  setupValidator();
  setupSaveAndClear();
  setupExportButtons();
  setupProjects();
  setupRenderToggle();
}

init();
