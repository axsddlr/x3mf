/* x3mf web interface */
(() => {
  "use strict";

  const state = {
    source: null,
    kit: null,
    sourceExample: null,
    kitExample: null,
    sourceInfo: null,
    busy: false,
    startedAt: 0,
    timer: null,
  };

  const $ = (id) => document.getElementById(id);
  const status = $("status");
  const statusDot = status.querySelector(".status__dot");
  const statusText = status.querySelector(".status__text");
  const statusTime = $("status-time");
  const result = $("result");
  const runButton = $("run");
  const sourceChips = $("source-chips");

  /* ------------------------------------------------------------- helpers */

  function el(tag, props = {}, ...children) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(props)) {
      if (value === undefined || value === null || value === false) continue;
      if (key === "text") node.textContent = value;
      else if (key === "class") node.className = value;
      else if (key === "dataset") Object.assign(node.dataset, value);
      else node.setAttribute(key, value === true ? "" : String(value));
    }
    for (const child of children) {
      if (child === null || child === undefined || child === false) continue;
      node.append(child.nodeType ? child : document.createTextNode(String(child)));
    }
    return node;
  }

  function setStatus(kind, text) {
    statusDot.dataset.kind = kind;
    statusText.textContent = text;
  }

  function fmtBytes(bytes) {
    if (!Number.isFinite(bytes) || bytes <= 0) return "";
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} kB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  }

  function startTimer() {
    state.startedAt = performance.now();
    statusTime.textContent = "0.0 s";
    stopTimer();
    state.timer = setInterval(() => {
      const seconds = (performance.now() - state.startedAt) / 1000;
      statusTime.textContent = `${seconds.toFixed(1)} s`;
    }, 100);
  }

  function stopTimer() {
    if (state.timer) clearInterval(state.timer);
    state.timer = null;
  }

  function finishTimer() {
    stopTimer();
    if (state.startedAt) {
      const seconds = (performance.now() - state.startedAt) / 1000;
      statusTime.textContent = `${seconds.toFixed(2)} s`;
    }
  }

  function saveBlob(id, filename) {
    const link = el("a", { href: `/api/download/${id}`, download: filename, class: "button", text: "Download" });
    return link;
  }

  /* -------------------------------------------------------------- inputs */

  function wireDrop(zone) {
    const kind = zone.dataset.drop;
    const input = zone.querySelector('input[type="file"]');
    const title = zone.querySelector(".drop__title");
    const meta = zone.querySelector(".drop__meta");
    input.addEventListener("change", () => {
      if (input.files && input.files[0]) accept(kind, input.files[0], zone, title, meta);
    });

    for (const type of ["dragenter", "dragover"]) {
      zone.addEventListener(type, (event) => {
        event.preventDefault();
        zone.classList.add("is-over");
      });
    }
    for (const type of ["dragleave", "dragend"]) {
      zone.addEventListener(type, () => zone.classList.remove("is-over"));
    }
    zone.addEventListener("drop", (event) => {
      event.preventDefault();
      zone.classList.remove("is-over");
      const file = event.dataTransfer?.files?.[0];
      if (file) accept(kind, file, zone, title, meta);
    });
  }

  function setFilled(kind, name, metaText) {
    const zone = document.querySelector(`[data-drop="${kind}"]`);
    zone.classList.add("is-filled");
    zone.querySelector(".drop__title").textContent = name;
    zone.querySelector(".drop__meta").textContent = metaText;
    const actions = $(`${kind}-actions`);
    if (actions) actions.hidden = false;
    if (kind === "source" && $("samples")) $("samples").hidden = true;
  }

  function clearFile(kind) {
    state[kind] = null;
    state[kind + "Example"] = null;
    const zone = document.querySelector(`[data-drop="${kind}"]`);
    zone.classList.remove("is-filled");
    zone.querySelector(".drop__title").textContent = PLACEHOLDERS[kind].title;
    zone.querySelector(".drop__meta").textContent = PLACEHOLDERS[kind].meta;
    const actionsRow = $(`${kind}-actions`);
    if (actionsRow) actionsRow.hidden = true;
    const input = zone.querySelector('input[type="file"]');
    if (input) input.value = "";

    if (kind === "source") {
      state.sourceInfo = null;
      state.sourceError = false;
      sourceChips.hidden = true;
      sourceChips.replaceChildren();
      result.replaceChildren(idleContent());
      setStatus("idle", "Waiting for a project");
      statusTime.textContent = "";
      if ($("samples")) $("samples").hidden = false;
    } else {
      state.kitInfo = null;
      $("kit-note").textContent = "Machine geometry, limits and preset names will come from this file, not from the source project.";
      $("kit-note").classList.remove("note--warn");
    }
    applyDestinationMode();
  }

  function accept(kind, file, zone, title, meta) {
    state[kind] = file;
    state[kind + "Example"] = null;
    const input = zone.querySelector('input[type="file"]');
    if (input) input.value = "";
    setFilled(
      kind,
      file.name,
      `${fmtBytes(file.size)} · ${kind === "source" ? "drop another file to replace" : "destination profile"}`
    );

    if (kind === "source") {
      state.sourceInfo = null;
      state.sourceError = false;
      sourceChips.hidden = true;
      sourceChips.replaceChildren();
      result.replaceChildren(idleContent());
      setStatus("idle", "Inspecting source…");
      inspectSource();
    } else {
      state.kitInfo = null;
      inspectKit();
    }
    updateRunButton();
  }

  function hasSource() {
    return Boolean(state.source || state.sourceExample);
  }

  function destinationMode() {
    const checked = document.querySelector('input[name="dest"]:checked');
    return checked ? checked.value : "kit";
  }

  function hasKit() {
    return Boolean(state.kit || state.kitExample);
  }

  function canRun() {
    if (!hasSource()) return false;
    if (state.sourceError) return false;  // a file we could not read cannot translate
    // "Another printer" means the machine is unknown without the file.
    return destinationMode() === "same" || hasKit();
  }

  function updateRunButton() {
    runButton.disabled = !canRun() || state.busy;
    runButton.querySelector("span").textContent = state.busy ? "Translating…" : "Translate project";

    const gate = $("gate");
    if (state.busy) {
      gate.hidden = true;
    } else if (!hasSource()) {
      gate.hidden = true;
    } else if (state.sourceError) {
      gate.hidden = false;
      gate.textContent = "That file could not be read. Choose another .3mf.";
    } else if (!canRun()) {
      gate.hidden = false;
      gate.textContent = "Add your printer's .3mf above, or switch to “Same printer”.";
    } else {
      gate.hidden = true;
    }
  }

  function applyDestinationMode() {
    const mode = destinationMode();
    document.querySelectorAll(".mode").forEach((card) => {
      card.classList.toggle("is-active", card.querySelector("input").checked);
    });
    $("kit-area").hidden = mode === "same";

    const note = $("kit-note");
    if (mode === "same") {
      note.classList.add("note--warn");
      note.textContent =
        "The machine settings already inside the project will be kept. Only correct when the " +
        "destination is the same physical printer, otherwise the file targets the wrong bed." +
        (hasKit() ? " Your file is kept if you switch back." : "");
    } else {
      note.classList.remove("note--warn");
      note.textContent =
        "Machine geometry, limits and preset names will come from this file, not from the source project.";
    }
    updateRunButton();

    // Keep the readiness line honest, but never overwrite a finished report.
    const current = statusText.textContent;
    if (hasSource() && (current.startsWith("Project read") || current.startsWith("Waiting"))) {
      const ready = mode === "same" || hasKit();
      setStatus(
        "idle",
        ready
          ? "Project read. Ready to translate."
          : "Project read. Add your printer's .3mf to continue."
      );
      statusTime.textContent = "";
    }
  }

  /* ------------------------------------------------------------- requests */

  async function post(path, form) {
    const response = await fetch(path, { method: "POST", body: form });
    const text = await response.text();
    let payload;
    try {
      payload = JSON.parse(text);
    } catch {
      throw new Error(`server returned ${response.status}: ${text.slice(0, 200)}`);
    }
    return { response, payload };
  }

  async function inspectSource() {
    if (!hasSource()) return;
    const form = new FormData();
    if (state.source) form.append("source", state.source, state.source.name);
    else form.append("source_example", state.sourceExample);
    try {
      const { response, payload } = await post("/api/inspect", form);
      if (!response.ok || !payload.ok) throw new Error(payload.error || "could not read the project");
      state.sourceInfo = payload.source;
      state.sourceError = false;
      const { errors } = renderSourceChips(payload.source, payload.issues);
      const ready = destinationMode() === "same" || hasKit();
      if (errors) {
        setStatus(
          "warn",
          `${errors} error${errors === 1 ? "" : "s"} in the source. Translate anyway, or choose another file.`
        );
      } else {
        setStatus(
          "idle",
          ready
            ? "Project read. Ready to translate."
            : "Project read. Add your printer's .3mf to continue."
        );
      }
      updateRunButton();
    } catch (error) {
      if (isNetworkError(error)) {
        setStatus("bad", "Cannot reach the translator");
        result.replaceChildren(offlineBox());
        return;
      }
      state.sourceError = true;
      setStatus("bad", "Could not read that file");
      result.replaceChildren(alertBox(error.message));
      updateRunButton();
    }
  }

  async function inspectKit() {
    if (!state.kit) return;
    const form = new FormData();
    form.append("source", state.kit, state.kit.name);
    try {
      const { payload } = await post("/api/inspect", form);
      if (!payload.ok) throw new Error(payload.error || "could not read the kit");
      state.kitInfo = payload.source;
      const machine = payload.source.machine;
      const note = $("kit-note");
      note.textContent = machine
        ? `Found ${machine.model || "an unknown printer"} · ${machine.variant ? `nozzle ${machine.variant}` : "nozzle unknown"} · ` +
          `${machine.extruders} extruder(s) · ${machine.settings_keys} resolved settings — its geometry and preset names will be used.`
        : "That file has no project settings, so it cannot describe a printer. Save a project from the destination slicer instead.";
      note.classList.toggle("note--warn", !machine);
      if (machine) updateRunButton();
    } catch (error) {
      if (isNetworkError(error)) {
        setStatus("bad", "Cannot reach the translator");
        result.replaceChildren(offlineBox());
        return;
      }
      setStatus("bad", "Could not read the kit");
      result.replaceChildren(alertBox(error.message));
    }
  }

  function options() {
    const number = (id) => {
      const value = $(id).value.trim();
      const parsed = Number.parseInt(value, 10);
      return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
    };
    return {
      to: $("opt-to").value || null,
      filaments: number("opt-filaments"),
      extruders: number("opt-extruders"),
      drop_aux: $("opt-drop-aux").checked,
      keep_unknown: $("opt-keep-unknown").checked,
      force: $("opt-force").checked,
    };
  }

  async function translate() {
    if (!canRun() || state.busy) return;
    state.busy = true;
    updateRunButton();
    setStatus("busy", "Translating…");
    startTimer();
    result.replaceChildren(el("p", { class: "empty__foot", text: "Rewriting model, settings and layer metadata…" }));

    const withKit = destinationMode() === "kit";
    const form = new FormData();
    if (state.source) form.append("source", state.source, state.source.name);
    else form.append("source_example", state.sourceExample);
    if (withKit && state.kit) form.append("kit", state.kit, state.kit.name);
    else if (withKit && state.kitExample) form.append("kit_example", state.kitExample);
    form.append("options", JSON.stringify(options()));

    try {
      const { response, payload } = await post("/api/translate", form);
      finishTimer();
      if (response.status === 422 && payload.status === "refused") {
        renderResult(payload, "refused");
      } else if (response.status === 422) {
        setStatus("bad", "Could not translate this project");
        result.replaceChildren(alertBox(payload.error || "the translator refused the input"));
      } else if (!response.ok) {
        throw new Error(payload.error || `server returned ${response.status}`);
      } else {
        renderResult(payload, "ok");
      }
    } catch (error) {
      finishTimer();
      if (isNetworkError(error)) {
        setStatus("bad", "Cannot reach the translator");
        result.replaceChildren(offlineBox());
        return;
      }
      setStatus("bad", "Translation failed");
      result.replaceChildren(alertBox(error.message));
    } finally {
      state.busy = false;
      updateRunButton();
    }
  }

  /* ------------------------------------------------------------- rendering */

  function alertBox(message, headline = "Nothing was written.") {
    return el("div", { class: "alert", role: "alert" },
      el("span", { text: "▲", "aria-hidden": "true" }),
      el("div", {},
        el("strong", { text: headline + " " }),
        el("span", { text: message })
      )
    );
  }

  // A fetch that never got an answer is not a problem with the user's file; the
  // browser only says "Failed to fetch", which used to be shown as if the
  // project were unreadable. Distinguish the two so the remedy is obvious.
  function isNetworkError(error) {
    if (error instanceof TypeError) return true;
    return /failed to fetch|networkerror|load failed|fetch failed/i.test(String(error && error.message));
  }

  function offlineBox() {
    return alertBox(
      "The page lost contact with the translator, so nothing was sent. If you closed the " +
        "terminal, start it again with python -m x3mf web and reload this page.",
      "The translator is not responding."
    );
  }

  function chip(label, value, kind) {
    return el("span", { class: `chip${kind ? ` chip--${kind}` : ""}` }, `${label} `, el("b", { text: value }));
  }

  function renderSourceChips(info, issues) {
    const chips = [];
    const dialect = info.dialect || {};
    chips.push(chip("dialect", dialect.key || "unknown"));
    if (info.machine) chips.push(chip("printer", info.machine.model || "?"));
    if (info.layout) {
      chips.push(chip("plates", String(info.layout.plates)));
      chips.push(chip("objects", String(info.layout.objects)));
      chips.push(chip("parts", String(info.layout.parts)));
    }
    if (info.filaments) chips.push(chip("filaments", String(info.filaments.length)));
    if (info.machine) chips.push(chip("settings", String(info.machine.settings_keys)));

    const found = issues || [];
    const errors = found.filter((issue) => issue.level === "error").length;
    const warnings = found.filter((issue) => issue.level === "warning").length;

    if (errors || warnings) {
      const label = errors
        ? `${errors} error${errors === 1 ? "" : "s"}`
        : `${warnings} warning${warnings === 1 ? "" : "s"}`;
      const toggle = el("button", {
        type: "button",
        class: `chip chip--button chip--${errors ? "bad" : "warn"}`,
        "aria-expanded": "false",
      }, `${label} `, el("b", { text: "show" }));

      const panel = el("div", { class: "issues-panel", hidden: true },
        ...found.map((issue) => el("div", { class: "issue", dataset: { level: issue.level } },
          el("span", { class: "issue__code", text: issue.code }),
          el("span", { class: "issue__msg", text: issue.message })
        ))
      );

      toggle.addEventListener("click", () => {
        const opening = panel.hidden;
        panel.hidden = !opening;
        toggle.setAttribute("aria-expanded", String(opening));
        toggle.querySelector("b").textContent = opening ? "hide" : "show";
      });
      chips.push(toggle, panel);
    }

    sourceChips.replaceChildren(...chips);
    sourceChips.hidden = false;
    return { errors, warnings };
  }

  function verdictBlock(verdict) {
    if (!verdict) return null;
    return el("section", { class: "verdict", dataset: { tone: verdict.tone || "ok" } },
      el("p", { class: "verdict__headline", text: verdict.headline }),
      verdict.detail ? el("p", { class: "verdict__detail", text: verdict.detail }) : null,
      verdict.facts && verdict.facts.length
        ? el("ul", { class: "verdict__facts" }, ...verdict.facts.map((fact) => el("li", { text: fact })))
        : null
    );
  }

  function fold(title, items, tone) {
    if (!items || !items.length) return null;
    return el("details", { class: "fold" },
      el("summary", {},
        el("span", { text: title }),
        el("span", { class: "count", text: String(items.length) })
      ),
      el("ul", { class: `list list--${tone || "none"}` },
        ...items.map((item) => el("li", { text: item })))
    );
  }

  function group(title, items, tone) {
    if (!items || !items.length) return null;
    return el("section", { class: "section" },
      el("div", { class: "section__head" },
        el("h3", { text: title }),
        el("span", { class: "count", text: String(items.length) })
      ),
      el("ul", { class: `list list--${tone || "none"}` },
        ...items.map((item) => el("li", { text: item })))
    );
  }

  function factsFor(data) {
    const target = data.target || {};
    const facts = [];
    const fact = (term, value) => el("div", { class: "fact" },
      el("dt", { text: term }), el("dd", { text: value || "—" }));

    if (target.machine) facts.push(fact("printer", `${target.machine}${target.variant ? ` · ${target.variant}` : ""}`));
    if (target.process) facts.push(fact("process", target.process));
    if (typeof target.plates === "number") facts.push(fact("plates", String(target.plates)));
    if (target.filaments && target.filaments.length) {
      facts.push(el("div", { class: "fact" },
        el("dt", { text: "filaments" }),
        el("dd", {},
          el("div", { class: "swatches" },
            ...target.filaments.map((filament) => el("span", {
              class: "swatch",
              title: filament.preset || "",
              "aria-label": `filament ${filament.index}: ${filament.material || "unknown material"}, ` +
                `${filament.colour || "no colour"}, preset ${filament.preset || "none"}`,
            },
              el("i", { style: `background:${filament.colour || "#999"}` }),
              `${filament.index} · ${filament.material || "?"}`
            ))
          )
        )
      ));
    }
    return facts.length ? el("dl", { class: "facts" }, ...facts) : null;
  }

  function issuesBlock(issues) {
    if (!issues || !issues.length) return null;
    return el("section", { class: "section" },
      el("div", { class: "section__head" },
        el("h3", { text: "Validation" }),
        el("span", { class: "count", text: String(issues.length) })
      ),
      el("div", {},
        ...issues.map((issue) => el("div", { class: "issue", dataset: { level: issue.level } },
          el("span", { class: "issue__code", text: issue.code }),
          el("span", { class: "issue__msg", text: issue.message })
        ))
      )
    );
  }

  function renderResult(data, statusKind) {
    const report = data.report || {};
    const download = data.download;
    const verdict = data.verdict;
    const nodes = [];

    if (statusKind === "refused") {
      const errors = (data.issues || []).filter((issue) => issue.level === "error").length;
      setStatus("bad", `Refused. ${errors} invariant error${errors === 1 ? "" : "s"}.`);
      nodes.push(alertBox(
        "The translated project failed its own format checks, so no download was produced. " +
        "The findings below say what would break; force the write in Options if you want the file anyway."
      ));
    } else if (verdict) {
      setStatus(verdict.tone === "ok" ? "ok" : "warn",
        verdict.tone === "ok" ? "Done. Nothing dropped or flagged." : "Done. Check the notes below.");
    } else {
      setStatus("warn", "Done.");
    }

    nodes.push(verdictBlock(verdict));

    if (download) {
      nodes.push(el("section", { class: "section" },
        el("div", { class: "download" },
          el("div", { class: "download__file" },
            el("span", { class: "download__name", text: download.filename }),
            el("span", { class: "download__size", text: fmtBytes(download.bytes) })
          ),
          saveBlob(download.id, download.filename)
        )
      ));
    }

    const facts = factsFor(data);
    if (facts) nodes.push(el("section", { class: "section" },
      el("div", { class: "section__head" }, el("h3", { text: "Where it will print" })), facts));

    nodes.push(issuesBlock(data.issues));
    nodes.push(group("Dropped", report.dropped, "warn"));
    nodes.push(group("Warnings", report.warnings, "warn"));
    nodes.push(fold("Carried with adjustment", report.adjusted, "none"));
    nodes.push(fold("Carried", report.carried, "ok"));

    result.replaceChildren(...nodes.filter(Boolean));
  }

  function idleContent() {
    return el("div", { class: "empty" },
      el("h3", { text: "What this does, precisely" }),
      el("ol", { class: "steps" },
        ...[
          ["Objects, plates and parts", " keep their ids, transforms and roles, including modifier and support-blocker volumes."],
          ["Filaments keep their colour and material", "; each slot is pointed at a preset the destination actually has."],
          ["Layer metadata is preserved byte-for-byte", "; only object ids and filament indices are remapped when slots collapse."],
          ["Printer geometry, nozzle and machine limits come from your printer file", ", and the output is checked against them before it is written."],
        ].map(([lead, rest]) => el("li", {}, el("strong", { text: lead }), rest))
      ),
      el("p", { class: "empty__foot", text: "Start with a source project, then say where it will print: another machine needs your printer's file, the same machine needs nothing." })
    );
  }

  /* ---------------------------------------------------------------- wiring */

  async function loadSample() {
    const response = await fetch("/api/examples");
    const payload = await response.json();
    const samples = Object.fromEntries((payload.samples || []).map((s) => [s.role, s]));
    if (!samples.source) return;

    document.querySelector('input[name="dest"][value="kit"]').checked = true;
    applyDestinationMode();
    state.source = null;
    state.kit = null;
    state.sourceExample = "source";
    state.kitExample = samples.kit ? "kit" : null;

    setFilled("source", samples.source.name, `${fmtBytes(samples.source.bytes)} · bundled sample`);

    if (samples.kit) {
      setFilled("kit", samples.kit.name, `${fmtBytes(samples.kit.bytes)} · bundled sample`);
      $("kit-note").textContent = "Bundled kit: a Creality Ender-3 V3 SE project from OrcaSlicer. Its geometry and preset names will be used.";
    }
    result.replaceChildren(idleContent());
    setStatus("idle", "Inspecting bundled sample…");
    updateRunButton();
    await inspectSource();
  }

  async function initSamples() {
    try {
      const response = await fetch("/api/examples");
      const payload = await response.json();
      if (payload.samples && payload.samples.some((s) => s.role === "source")) {
        if ($("samples")) $("samples").hidden = false;
      }
    } catch {
      /* samples are optional */
    }
  }

  $("load-sample").addEventListener("click", () => {
    loadSample().catch((error) => {
      setStatus("bad", "Could not load the sample");
      result.replaceChildren(alertBox(error.message));
    });
  });
  initSamples();

  const PLACEHOLDERS = {};
  for (const kind of ["source", "kit"]) {
    const zone = document.querySelector(`[data-drop="${kind}"]`);
    PLACEHOLDERS[kind] = {
      title: zone.querySelector(".drop__title").textContent,
      meta: zone.querySelector(".drop__meta").textContent,
    };
  }
  $("source-remove").addEventListener("click", () => clearFile("source"));
  $("kit-remove").addEventListener("click", () => clearFile("kit"));

  document.querySelectorAll('input[name="dest"]').forEach((radio) => {
    radio.addEventListener("change", applyDestinationMode);
  });
  applyDestinationMode();

  document.querySelectorAll("[data-drop]").forEach(wireDrop);
  runButton.addEventListener("click", translate);
  document.addEventListener("keydown", (event) => {
    // Enter is a page-level shortcut only when nothing interactive has focus,
    // otherwise it must keep doing what the focused control says it does.
    const interactive = event.target.closest("a, button, summary, label, input, select, textarea, [role='button']");
    if (event.key === "Enter" && !interactive && !runButton.disabled) {
      event.preventDefault();
      translate();
    }
  });
})();
