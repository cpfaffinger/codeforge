/* codeforge playground - vanilla JS, no build step, no external dependencies */
(function () {
  "use strict";
  const $ = (sel, root) => (root || document).querySelector(sel);
  const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));
  const state = { symbologies: {}, blobs: {}, lastQrUrl: null, currentSym: null, lastExample: null, limits: {} };

  // ---------------------------------------------------------------- helpers
  function toast(msg, ms) {
    const t = $("#toast");
    t.textContent = msg; t.hidden = false;
    clearTimeout(t._timer); t._timer = setTimeout(() => (t.hidden = true), ms || 1800);
  }
  function debounce(fn, ms) { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; }
  function formData(form) {
    const out = {};
    new FormData(form).forEach((v, k) => { out[k] = v; });
    $$("input[type=checkbox]", form).forEach((cb) => { if (!cb.dataset.opt) out[cb.name] = cb.checked; });
    return out;
  }
  function qs(obj) {
    const p = new URLSearchParams();
    Object.entries(obj).forEach(([k, v]) => { if (v !== "" && v !== null && v !== undefined && v !== false) p.set(k, v === true ? "true" : v); });
    return p.toString();
  }
  function absolute(path) { return location.origin + path; }
  function kb(n) { return Math.round(n / 102.4) / 10 + " kB"; }
  function showPreview(id, blob, meta) {
    const box = $("#preview-" + id);
    box.innerHTML = "";
    const url = URL.createObjectURL(blob);
    let el;
    if (blob.type === "application/pdf") { el = document.createElement("embed"); el.type = "application/pdf"; el.style.width = "100%"; el.style.height = "60vh"; }
    else { el = document.createElement("img"); el.alt = "preview"; }
    el.src = url; box.appendChild(el);
    if (meta) { const m = document.createElement("div"); m.className = "meta"; m.textContent = meta; box.appendChild(m); }
    state.blobs[id] = blob;
  }
  function showError(id, msg) { $("#preview-" + id).innerHTML = '<div class="error">' + escapeHtml(msg) + "</div>"; delete state.blobs[id]; }
  function showBusy(id, msg) { const box = $("#preview-" + id); if (!box.querySelector("img, embed")) box.innerHTML = '<div class="placeholder"><span class="spinner"></span> ' + escapeHtml(msg || "rendering ...") + "</div>"; else box.classList.add("busy"); }
  function escapeHtml(s) { return String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])); }
  async function fetchImage(id, url, meta) {
    showBusy(id);
    try {
      const r = await fetch(url);
      $("#preview-" + id).classList.remove("busy");
      if (!r.ok) {
        const ct = r.headers.get("content-type") || "";
        const body = ct.includes("json") ? (await r.json()).detail : await r.text();
        const msg = typeof body === "string" ? body : JSON.stringify(body);
        showError(id, msg);
        return { error: msg };
      }
      const blob = await r.blob();
      showPreview(id, blob, meta || (r.headers.get("content-type") + " - " + kb(blob.size)));
      return { blob };
    } catch (e) { showError(id, e.message); return { error: e.message }; }
  }
  function download(id, name) {
    const blob = state.blobs[id];
    if (!blob) { toast("nothing rendered yet"); return; }
    const ext = { "image/png": "png", "image/svg+xml": "svg", "image/jpeg": "jpg", "image/webp": "webp", "application/pdf": "pdf", "application/zip": "zip", "application/json": "json" }[blob.type] || "bin";
    const a = document.createElement("a"); a.href = URL.createObjectURL(blob); a.download = name + "." + ext; a.click();
  }
  async function copyBase64(id) {
    const blob = state.blobs[id];
    if (!blob) { toast("nothing rendered yet"); return; }
    const b64 = await new Promise((res) => { const fr = new FileReader(); fr.onload = () => res(fr.result.split(",")[1]); fr.readAsDataURL(blob); });
    await navigator.clipboard.writeText(b64); toast("base64 copied (" + b64.length + " chars)");
  }
  function parseOptions(text) {
    const out = {};
    (text || "").split(/[\s\n]+/).filter(Boolean).forEach((tok) => { const i = tok.indexOf("="); if (i > 0) out[tok.slice(0, i)] = tok.slice(i + 1); else out[tok] = true; });
    return out;
  }
  function optionString(obj) {
    return Object.entries(obj).map(([k, v]) => (v === true ? k : k + "=" + v)).join(" ");
  }

  // ------------------------------------------------------------------- tabs
  function activate(tab) {
    $$(".tab").forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
    $$(".panel").forEach((p) => p.classList.toggle("active", p.id === "panel-" + tab));
    history.replaceState(null, "", tab === "qr" ? "/" : "/?tab=" + tab);
  }
  $$(".tab").forEach((b) => b.addEventListener("click", () => activate(b.dataset.tab)));
  $$("[data-copy]").forEach((b) => b.addEventListener("click", async () => { const v = $("#" + b.dataset.copy).value; if (v) { await navigator.clipboard.writeText(v); toast("copied"); } }));
  $$("[data-download]").forEach((b) => b.addEventListener("click", () => download(b.dataset.download, b.dataset.download === "qr" ? "qrcode" : ($("#form-barcode [name=bcid]").value || "barcode"))));
  $$("[data-copyb64]").forEach((b) => b.addEventListener("click", () => copyBase64(b.dataset.copyb64)));

  // --------------------------------------------------------------------- QR
  const formQr = $("#form-qr");
  function renderQr() {
    const d = formData(formQr);
    if (!d.data) { $("#preview-qr").innerHTML = '<div class="placeholder">Enter data to render a QR code</div>'; return; }
    const params = { data: d.data, error: d.error, version: d.version, scale: d.scale, border: d.border, fg: d.fg, bg: d.bg, format: d.format, micro: d.micro };
    const url = "/api/v1/qr?" + qs(params);
    const legacy = "/qr?" + qs({ data: d.data, format: d.format.toUpperCase(), ec: { L: "LOW", M: "MEDIUM", Q: "QUARTILE", H: "HIGH" }[d.error], version: d.version, fg: d.fg !== "#000000" ? d.fg : "", bg: d.bg !== "#ffffff" ? d.bg : "", box: d.scale !== "10" ? d.scale : "", border: d.border !== "4" ? d.border : "" });
    $("#url-qr").value = absolute(url); $("#url-qr-legacy").value = absolute(legacy);
    state.lastQrUrl = url;
    fetchImage("qr", url);
    fetch("/api/v1/qr/info?" + qs({ data: d.data, error: d.error, version: d.version, micro: d.micro })).then((r) => r.json()).then((j) => {
      $("#info-qr").textContent = j.detail ? j.detail : "version " + j.version + (j.is_micro ? " (micro)" : "") + " - " + j.modules + "x" + j.modules + " modules - error level " + j.error + " - mode " + j.mode + " - " + d.data.length + " characters";
    }).catch(() => {});
  }
  formQr.addEventListener("submit", (e) => { e.preventDefault(); renderQr(); });
  formQr.addEventListener("input", debounce(renderQr, 350));

  // ---------------------------------------------------------------- barcode
  const formBc = $("#form-barcode");
  const textInput = formBc.querySelector("[name=text]");
  const rawOptions = formBc.querySelector("[name=options]");
  const symSelect = $("#sym-select"), symFilter = $("#sym-filter"), batchSelect = $("#batch-bcid");

  // ---- options panel: every option BWIPP accepts for the symbology, required ones first ----
  function optionInput(o) {
    const wrap = document.createElement("label");
    wrap.className = "opt" + (o.required ? " required" : "");
    wrap.title = (o.description || "") + (o.default !== null && o.default !== undefined ? " (default: " + o.default + ")" : "");
    let input;
    if (o.type === "bool") { input = document.createElement("input"); input.type = "checkbox"; wrap.classList.add("bool"); }
    else if (o.type === "enum") {
      input = document.createElement("select");
      const first = document.createElement("option"); first.value = ""; first.textContent = o.default !== null && o.default !== undefined ? "default (" + o.default + ")" : "default"; input.appendChild(first);
      (o.choices || []).forEach((c) => { const op = document.createElement("option"); op.value = c; op.textContent = c; input.appendChild(op); });
    } else {
      input = document.createElement("input");
      input.type = (o.type === "int" || o.type === "float") ? "number" : "text";
      if (o.type === "float") input.step = "any";
      input.placeholder = o.type === "color" ? "RRGGBB" : (o.default !== null && o.default !== undefined ? String(o.default) : (o.required ? "required" : ""));
      if (o.type === "color") { input.maxLength = 8; input.pattern = "[0-9a-fA-F]{6,8}"; }
    }
    input.dataset.opt = o.name; input.name = "opt." + o.name;
    const name = document.createElement("span"); name.className = "opt-name"; name.textContent = o.name;
    if (o.required) { const b = document.createElement("span"); b.className = "badge"; b.textContent = "required"; name.appendChild(b); }
    const desc = document.createElement("span"); desc.className = "opt-desc muted small"; desc.textContent = o.description || "";
    if (o.type === "bool") { wrap.appendChild(input); wrap.appendChild(name); wrap.appendChild(desc); }
    else { wrap.appendChild(name); wrap.appendChild(input); wrap.appendChild(desc); }
    return wrap;
  }
  function buildOptionsPanel(s) {
    const panel = $("#sym-options");
    const opts = (s && s.options) || [];
    if (!opts.length) { panel.hidden = true; return; }
    panel.hidden = false;
    const groups = { required: [], encoder: [], text: [], appearance: [], advanced: [] };
    opts.forEach((o) => { (groups[o.group] || groups.advanced).push(o); });
    $("#opt-required").hidden = !groups.required.length;
    const req = $("#opt-required-list"); req.innerHTML = ""; groups.required.forEach((o) => req.appendChild(optionInput(o)));
    ["encoder", "text", "appearance", "advanced"].forEach((g) => {
      const det = $("#opt-" + g); const list = det.querySelector(".opt-list"); list.innerHTML = "";
      groups[g].forEach((o) => list.appendChild(optionInput(o)));
      det.hidden = !groups[g].length;
      det.querySelector("[data-count]").textContent = groups[g].length ? "(" + groups[g].length + ")" : "";
      det.open = g === "encoder" && groups[g].some((o) => !["parse", "parsefnc", "raw", "dontlint", "lintreqs", "gssep"].includes(o.name)) && groups[g].length <= 6;
    });
    $("#sym-options-info").textContent = opts.length + " options from BWIPP" + (groups.required.length ? ", " + groups.required.length + " required" : "") + " - empty = encoder default";
  }
  function panelOptions() {
    const out = {};
    $$("#sym-options [data-opt]").forEach((el) => {
      const k = el.dataset.opt;
      if (el.type === "checkbox") { if (el.checked) out[k] = true; }
      else if (el.value !== "") out[k] = el.value;
    });
    return out;
  }
  function setPanelOptions(obj) {
    $$("#sym-options [data-opt]").forEach((el) => {
      const v = obj[el.dataset.opt];
      if (el.type === "checkbox") el.checked = v === true || v === "true" || v === "";
      else el.value = v === undefined || v === true ? "" : v;
    });
  }
  function applyOptionString(str) {
    // known options go into the panel, unknown ones stay raw
    const all = parseOptions(str), rest = {};
    const known = new Set($$("#sym-options [data-opt]").map((el) => el.dataset.opt));
    const panelVals = {};
    Object.entries(all).forEach(([k, v]) => { if (known.has(k)) panelVals[k] = v; else rest[k] = v; });
    setPanelOptions(panelVals);
    rawOptions.value = optionString(rest);
  }
  $("#opt-reset").addEventListener("click", () => { setPanelOptions({}); rawOptions.value = ""; validateText(); renderBarcodeDebounced(); });

  // ---- validation against the BWIPP rules (regex, length, required options) before the request goes out ----
  function validateText() {
    const s = state.currentSym; const box = $("#text-check"); const text = textInput.value;
    box.hidden = true; box.className = "check-msg"; textInput.setCustomValidity("");
    if (!s || !text) return { ok: true };
    const problems = [];
    const missing = (s.required_options || []).filter((name) => { const el = $('#sym-options [data-opt="' + name + '"]'); const raw = parseOptions(rawOptions.value); return !(raw[name] !== undefined || (el && el.value !== "" && !(el.type === "checkbox" && !el.checked))); });
    if (missing.length) problems.push("required option" + (missing.length > 1 ? "s" : "") + " not set: " + missing.join(", "));
    if (s.pattern) {
      let re = null; try { re = new RegExp(s.pattern); } catch (e) { re = null; }
      if (re && !re.test(text.replace(/\r?\n/g, ""))) {
        const bad = Array.from(new Set(text.replace(/\r?\n/g, "").split("").filter((c) => !re.test(c)))).slice(0, 8);
        const rule = (s.rules || []).find((r) => /must contain only|must start with/i.test(r)) || "allowed characters: " + s.pattern;
        problems.push(rule + (bad.length ? " - not allowed: " + bad.map((c) => JSON.stringify(c)).join(" ") : ""));
      }
    }
    const len = text.replace(/\r?\n/g, "").length;
    if (s.min_length != null && len < s.min_length) problems.push("too short: " + len + " characters, " + (s.min_length === s.max_length ? "exactly " + s.min_length : "at least " + s.min_length) + " required");
    if (s.max_length != null && len > s.max_length) problems.push("too long: " + len + " characters, at most " + s.max_length + " allowed");
    if (problems.length) {
      box.hidden = false; box.classList.add("err");
      box.innerHTML = problems.map((p) => "<div>" + escapeHtml(p) + "</div>").join("") + '<div class="small">(<a href="#" id="render-anyway">render anyway</a> - the encoder reports the exact error)</div>';
      $("#render-anyway").addEventListener("click", (e) => { e.preventDefault(); renderBarcode(true); });
      return { ok: false, problems };
    }
    if (s.pattern || s.min_length != null || s.max_length != null || (s.required_options || []).length) {
      box.hidden = false; box.classList.add("ok"); box.textContent = "format ok - " + len + " characters";
    }
    return { ok: true };
  }
  function barcodeParams() {
    const d = formData(formBc);
    const opts = Object.assign({}, panelOptions(), parseOptions(rawOptions.value));
    if (d.backgroundcolor && d.backgroundcolor.toLowerCase() !== "#ffffff" && !opts.backgroundcolor) opts.backgroundcolor = d.backgroundcolor.replace("#", "");
    return { d, opts };
  }
  async function renderBarcode(force) {
    const { d, opts } = barcodeParams();
    if (!d.bcid || !d.text) { if (!d.text) $("#preview-barcode").innerHTML = '<div class="placeholder">Enter text to render</div>'; return; }
    const check = validateText();
    const base = { bcid: d.bcid, text: d.text, scale: d.scale, rotate: d.rotate, paddingwidth: d.paddingwidth, paddingheight: d.paddingheight, format: d.format };
    const url = "/api/v1/barcode?" + qs(Object.assign({}, base, opts));
    const legacy = "/?" + qs(Object.assign({ bcid: d.bcid, text: d.text }, opts, { scale: d.scale !== "2" ? d.scale : "", rotate: d.rotate !== "N" ? d.rotate : "", paddingwidth: d.paddingwidth !== "1" ? d.paddingwidth : "", paddingheight: d.paddingheight !== "1" ? d.paddingheight : "", format: d.format !== "png" ? d.format : "" }));
    $("#url-barcode").value = absolute(url); $("#url-barcode-legacy").value = absolute(legacy);
    if (!check.ok && !force) { $("#preview-barcode").innerHTML = '<div class="placeholder">fix the data format or click "render anyway"</div>'; delete state.blobs.barcode; return; }
    const res = await fetchImage("barcode", url);
    if (res.error) {
      const box = $("#text-check"); box.hidden = false; box.className = "check-msg err";
      box.innerHTML = "<div>" + escapeHtml(res.error.replace(/^BWIPP ERROR: bwipp\.\w+ /, "")) + "</div>";
      const m = /The (\w+) option is required/.exec(res.error);
      if (m) { const el = $('#sym-options [data-opt="' + m[1] + '"]'); if (el) { $("#opt-required").hidden = false; el.focus(); } }
    }
  }
  formBc.addEventListener("submit", (e) => { e.preventDefault(); renderBarcode(); });
  const renderBarcodeDebounced = debounce(renderBarcode, 400);
  formBc.addEventListener("input", (e) => { if (e.target.id !== "sym-filter") { if (e.target === textInput) validateText(); renderBarcodeDebounced(); } });
  formBc.addEventListener("change", (e) => { if (e.target.dataset.opt) { validateText(); renderBarcodeDebounced(); } });

  // ---- symbology picker: every supported symbology, grouped by category, filterable ----
  function fillSelect(sel, list, keep) {
    const current = keep || sel.value;
    sel.innerHTML = "";
    const groups = {};
    list.forEach((s) => { (groups[s.category] = groups[s.category] || []).push(s); });
    Object.keys(groups).sort().forEach((cat) => {
      const og = document.createElement("optgroup"); og.label = cat;
      groups[cat].forEach((s) => { const o = document.createElement("option"); o.value = s.id; o.textContent = s.description + "  (" + s.id + ")" + (s.required_options && s.required_options.length ? "  *" : ""); og.appendChild(o); });
      sel.appendChild(og);
    });
    if (current && list.some((s) => s.id === current)) sel.value = current;
  }
  function symbologyList(filterText) {
    const q = (filterText || "").trim().toLowerCase();
    const all = Object.values(state.symbologies);
    if (!q) return all;
    return all.filter((s) => (s.id + " " + s.description + " " + s.category).toLowerCase().includes(q));
  }
  function showSymInfo(id) {
    const s = state.symbologies[id] || state.symbologies[(id || "").toLowerCase()];
    const box = $("#sym-info");
    if (!s) { box.hidden = true; return; }
    box.hidden = false;
    $("#sym-title").textContent = s.description;
    $("#sym-cat").textContent = s.category + "  -  id: " + s.id + "  -  " + (s.renderer === "matrix" ? "2D" : s.renderer === "maxicode" ? "MaxiCode" : "linear");
    $("#sym-wiki").href = s.wiki || "https://github.com/bwipp/postscriptbarcode/wiki";
    const parts = [];
    if (s.charset === "digits") parts.push("digits only");
    else if (s.charset === "alphanumeric") parts.push("capital letters and digits");
    else if (s.charset === "restricted") parts.push("restricted character set (see rules)");
    else parts.push("any text");
    if (s.min_length != null && s.max_length != null) parts.push(s.min_length === s.max_length ? "exactly " + s.min_length + " characters" : s.min_length + " to " + s.max_length + " characters");
    else if (s.min_length != null) parts.push("at least " + s.min_length + " characters");
    else if (s.max_length != null) parts.push("at most " + s.max_length + " characters");
    if (s.required_options && s.required_options.length) parts.push("requires option " + s.required_options.join(", "));
    $("#sym-summary").textContent = parts.join("  -  ");
    const ul = $("#sym-rules"); ul.innerHTML = "";
    const rules = s.rules && s.rules.length ? s.rules : ["No specific rules: the encoder accepts the data as given (length limits depend on the symbol size)."];
    rules.forEach((r) => { const li = document.createElement("li"); li.textContent = r; ul.appendChild(li); });
    $("#sym-rules-count").textContent = s.rules && s.rules.length ? "(" + s.rules.length + ")" : "";
    $("#sym-example").innerHTML = s.example ? "Example: <code>" + escapeHtml(s.example.length > 80 ? s.example.slice(0, 80) + "..." : s.example) + "</code>" + (s.example_options ? " with options <code>" + escapeHtml(s.example_options) + "</code>" : "") + ' <a href="#" id="use-example">use it</a>' : "";
    const use = $("#use-example"); if (use) use.addEventListener("click", (e) => { e.preventDefault(); loadExample(s); });
  }
  function loadExample(s) {
    textInput.value = s.example || "";
    rawOptions.value = "";
    setPanelOptions({});
    applyOptionString(s.example_options || "");
    state.lastExample = s.example || "";
    validateText();
    renderBarcode();
  }
  function selectSymbology(id, opts) {
    const s = state.symbologies[id];
    if (!s) return;
    const prev = state.currentSym;
    state.currentSym = s;
    showSymInfo(id);
    buildOptionsPanel(s);
    const textWasExample = !textInput.value || (prev && textInput.value === (prev.example || "") && textInput.value === state.lastExample);
    if ((opts && opts.loadExample) || textWasExample) { loadExample(s); return; }
    // keep the user's text, but drop options that only made sense for the previous symbology
    setPanelOptions({});
    rawOptions.value = "";
    validateText();
    renderBarcode();
  }
  symFilter.addEventListener("input", () => { fillSelect(symSelect, symbologyList(symFilter.value)); if (symSelect.value !== (state.currentSym || {}).id) selectSymbology(symSelect.value); });
  symSelect.addEventListener("change", () => selectSymbology(symSelect.value));
  $("#example-barcode").addEventListener("click", () => {
    let s = state.symbologies[symSelect.value];
    if (!s) { s = state.symbologies.code128 || Object.values(state.symbologies)[0]; if (s) { symFilter.value = ""; fillSelect(symSelect, symbologyList(""), s.id); state.currentSym = s; showSymInfo(s.id); buildOptionsPanel(s); } }
    if (s) loadExample(s);
  });

  // ----------------------------------------------------------------- decode
  const drop = $("#dropzone");
  const fileInput = $("#form-decode [name=file]");
  const progress = $("#decode-progress");
  let decodeTimer = null, decodeActive = false;
  function setProgress(text, pct) {
    if (!decodeActive) return;
    progress.hidden = false;
    $("#decode-progress-text").textContent = text;
    const fill = progress.querySelector(".fill");
    fill.style.width = (pct == null ? 100 : pct) + "%";
    fill.classList.toggle("indeterminate", pct == null);
  }
  function endProgress() { decodeActive = false; progress.hidden = true; clearInterval(decodeTimer); }
  function uploadAndDecode(blob) {
    // XHR instead of fetch for upload progress events
    return new Promise((resolve, reject) => {
      const fd = new FormData(); fd.append("file", blob, blob.name || "upload");
      const xhr = new XMLHttpRequest();
      xhr.open("POST", "/api/v1/decode");
      xhr.upload.onprogress = (e) => { if (e.lengthComputable) setProgress("uploading " + kb(e.loaded) + " of " + kb(e.total) + " ...", Math.round(e.loaded / e.total * 100)); };
      xhr.upload.onload = () => { const t0 = Date.now(); setProgress("decoding on the server ...", null); decodeTimer = setInterval(() => setProgress("decoding on the server ... " + ((Date.now() - t0) / 1000).toFixed(1) + " s (several passes for photos)", null), 200); };
      xhr.onload = () => { let j = null; try { j = JSON.parse(xhr.responseText); } catch (e) { j = { detail: xhr.responseText.slice(0, 300) || ("HTTP " + xhr.status) }; } resolve({ status: xhr.status, json: j }); };
      xhr.onerror = () => reject(new Error("network error - the upload did not reach the server"));
      xhr.ontimeout = () => reject(new Error("timeout - the server did not answer in time"));
      xhr.timeout = 120000;
      xhr.send(fd);
    });
  }
  async function decodeBlob(blob) {
    const box = $("#decode-results");
    box.innerHTML = "";
    const limit = state.limits.max_upload_bytes;
    if (limit && blob.size > limit) { box.innerHTML = '<div class="error">file is ' + kb(blob.size) + ", the server accepts at most " + kb(limit) + "</div>"; return; }
    if (blob.type && !/^image\//.test(blob.type)) { box.innerHTML = '<div class="error">' + escapeHtml(blob.type) + " is not an image (PNG, JPEG, GIF, BMP, WEBP, TIFF)</div>"; return; }
    showPreview("decode", blob, (blob.type || "image") + " - " + kb(blob.size));
    decodeActive = true;
    setProgress("uploading ...", 0);
    try {
      const { status, json: j } = await uploadAndDecode(blob);
      endProgress();
      if (status !== 200) { box.innerHTML = '<div class="error"><strong>decode failed (' + status + ')</strong><br>' + escapeHtml(typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail)) + "</div>"; return; }
      const meta = '<div class="muted small">' + (j.width ? j.width + "x" + j.height + " px - " : "") + j.elapsed_ms + " ms - " + j.passes + " pass" + (j.passes === 1 ? "" : "es") + "</div>";
      if (!j.count) {
        box.innerHTML = '<div class="warn"><strong>No code found.</strong>' + meta + "<ul>" + (j.hints || []).map((h) => "<li>" + escapeHtml(h) + "</li>").join("") + "</ul></div>";
        return;
      }
      box.innerHTML = '<div class="ok-box"><strong>' + j.count + " code" + (j.count > 1 ? "s" : "") + " found</strong>" + meta + "</div>" +
        '<table class="results"><tr><th>#</th><th>Type</th><th>Data</th><th></th></tr>' +
        j.symbols.map((s, i) => "<tr><td>" + (i + 1) + '</td><td>' + escapeHtml(s.type) + '<div class="muted small">' + escapeHtml(s.engine || "") + (s.found_by ? " / " + escapeHtml(s.found_by) : "") + "</div></td><td><code>" + escapeHtml(s.data) + "</code></td><td class=\"acts\"><button type=\"button\" class=\"mini\" data-copytext=\"" + i + '">copy</button> ' + (/^https?:\/\//i.test(s.data) ? '<a class="mini" href="' + escapeHtml(s.data) + '" target="_blank" rel="noopener">open</a> ' : "") + '<a class="mini" href="' + escapeHtml(genLink(s)) + '">generate</a></td></tr>').join("") + "</table>";
      $$("[data-copytext]", box).forEach((b) => b.addEventListener("click", async () => { await navigator.clipboard.writeText(j.symbols[+b.dataset.copytext].data); toast("copied"); }));
      drawDecodeOverlay(j);
    } catch (e) { endProgress(); box.innerHTML = '<div class="error">' + escapeHtml(e.message) + "</div>"; }
  }
  const DECODER_BCID = { QRCODE: "qrcode", EAN13: "ean13", EAN8: "ean8", UPCA: "upca", UPCE: "upce", CODE128: "code128", CODE39: "code39", CODE93: "code93", I25: "interleaved2of5", CODABAR: "rationalizedCodabar", DATABAR: "databaromni", DATABAR_EXP: "databarexpanded", PDF417: "pdf417", DATAMATRIX: "datamatrix" };
  function genLink(sym) {
    const bcid = DECODER_BCID[sym.type];
    if (!bcid || bcid === "qrcode") return "/?" + qs({ tab: "qr", data: sym.data });
    return "/?" + qs({ tab: "barcode", bcid, text: sym.data, options: "includetext" });
  }
  function drawDecodeOverlay(j) {
    const img = $("#preview-decode img");
    if (!img || !j.width) return;
    const draw = () => {
      const box = $("#preview-decode"); let c = box.querySelector("canvas.overlay");
      if (!c) { c = document.createElement("canvas"); c.className = "overlay"; box.appendChild(c); }
      const r = img.getBoundingClientRect(), b = box.getBoundingClientRect();
      c.width = r.width; c.height = r.height; c.style.left = (r.left - b.left) + "px"; c.style.top = (r.top - b.top) + "px";
      const ctx = c.getContext("2d"); const sx = r.width / j.width, sy = r.height / j.height;
      ctx.lineWidth = 3; ctx.strokeStyle = "#16a34a";
      j.symbols.forEach((s) => {
        if (s.polygon && s.polygon.length > 2) { ctx.beginPath(); s.polygon.forEach((p, i) => (i ? ctx.lineTo(p.x * sx, p.y * sy) : ctx.moveTo(p.x * sx, p.y * sy))); ctx.closePath(); ctx.stroke(); }
        else if (s.rect && s.rect.width) ctx.strokeRect(s.rect.left * sx, s.rect.top * sy, s.rect.width * sx, s.rect.height * sy);
      });
    };
    if (img.complete) draw(); else img.addEventListener("load", draw, { once: true });
  }
  drop.addEventListener("click", (e) => { if (!e.target.closest("label") && e.target.tagName !== "INPUT") fileInput.click(); });
  drop.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); fileInput.click(); } });
  fileInput.addEventListener("change", () => { if (fileInput.files[0]) { decodeBlob(fileInput.files[0]); fileInput.value = ""; } });
  ["dragenter", "dragover"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("over"); }));
  ["dragleave", "drop"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
  drop.addEventListener("drop", (e) => { const f = e.dataTransfer.files[0]; if (f) decodeBlob(f); });
  document.addEventListener("paste", (e) => {
    if (!$("#panel-decode").classList.contains("active")) return;
    const item = Array.from(e.clipboardData.items).find((i) => i.type.startsWith("image/"));
    if (item) { decodeBlob(item.getAsFile()); toast("decoding pasted image"); }
  });
  $("#decode-sample").addEventListener("click", async () => {
    if (!state.lastQrUrl) { toast("render a QR code first"); return; }
    const r = await fetch(state.lastQrUrl.replace(/format=[a-z]+/, "format=png"));
    decodeBlob(await r.blob());
  });
  $("#decode-api-url").textContent = absolute("/api/v1/decode");
  $$(".decode-api-url2").forEach((e) => (e.textContent = absolute("/api/v1/decode")));
  $$(".decode-api-url3").forEach((e) => (e.textContent = absolute("/api/v1/decode/base64")));

  // ------------------------------------------------------------------ batch
  const formBatch = $("#form-batch");
  formBatch.querySelector("[name=type]").addEventListener("change", (e) => { $("#batch-bcid-label").hidden = e.target.value !== "barcode"; });
  formBatch.querySelector("[name=output]").addEventListener("change", (e) => { $("#sheet-options").hidden = e.target.value !== "pdf"; });
  formBatch.querySelector("[name=items]").addEventListener("input", (e) => { const n = e.target.value.split("\n").filter((l) => l.trim()).length; $("#batch-count").textContent = n ? n + " item" + (n > 1 ? "s" : "") : ""; });
  formBatch.addEventListener("submit", async (e) => {
    e.preventDefault();
    const d = formData(formBatch);
    const lines = d.items.split("\n").map((l) => l.trim()).filter(Boolean);
    if (!lines.length) { toast("enter at least one item"); return; }
    const opts = parseOptions(d.options);
    const items = lines.map((line) => {
      const [value, caption] = line.split(/\t| \| /);
      return d.type === "qr" ? { type: "qr", data: value.trim(), caption: caption ? caption.trim() : undefined }
        : { type: "barcode", bcid: d.bcid, text: value.trim(), options: opts, caption: caption ? caption.trim() : undefined };
    });
    const body = { items, output: d.output, format: d.format, sheet: { page: d.page, columns: +d.columns, rows: +d.rows, margin_mm: +d.margin_mm, gap_mm: +d.gap_mm, captions: d.captions } };
    $("#batch-request").textContent = "POST /api/v1/batch\n" + JSON.stringify(body, null, 1).slice(0, 1500);
    const box = $("#preview-batch"); box.innerHTML = '<div class="placeholder"><span class="spinner"></span> generating ' + items.length + " codes ...</div>";
    const btn = $("#batch-submit"); btn.disabled = true;
    try {
      const r = await fetch("/api/v1/batch", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      if (!r.ok) { const j = await r.json(); showError("batch", typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail)); return; }
      const blob = await r.blob();
      state.blobs.batch = blob;
      if (d.output === "json") {
        const j = JSON.parse(await blob.text());
        box.innerHTML = '<div class="result-box"><div>' + j.succeeded + " of " + j.count + " rendered</div><div>" +
          j.items.filter((i) => i.ok).slice(0, 12).map((i) => '<img src="' + i.data_uri + '" style="max-height:90px;margin:3px">').join("") +
          '</div><button type="button" id="dl-batch">Download JSON</button>' +
          j.items.filter((i) => !i.ok).map((i) => '<div class="error">item ' + (i.index + 1) + ": " + escapeHtml(i.error) + "</div>").join("") + "</div>";
      } else if (d.output === "pdf") {
        showPreview("batch", blob, "PDF - " + kb(blob.size));
        const b = document.createElement("button"); b.type = "button"; b.id = "dl-batch"; b.textContent = "Download PDF"; b.style.position = "absolute"; b.style.top = ".6rem"; b.style.right = ".6rem"; box.appendChild(b);
      } else {
        box.innerHTML = '<div class="result-box"><div>ZIP ready - ' + kb(blob.size) + '</div><button type="button" id="dl-batch">Download ZIP</button></div>';
      }
      $("#dl-batch").addEventListener("click", () => download("batch", "codes"));
    } catch (err) { showError("batch", err.message); } finally { btn.disabled = false; }
  });

  // ------------------------------------------------------------------- init
  async function init() {
    try {
      const h = await (await fetch("/healthz")).json();
      $("#version").textContent = "v" + h.version + (h.ghostscript ? "" : " (barcodes unavailable: ghostscript missing)") + (h.bwipp ? " - BWIPP " + h.bwipp : "");
      const list = await (await fetch("/api/v1/symbologies")).json();
      const dl = $("#symbology-list");
      list.forEach((s) => { state.symbologies[s.id] = s; const o = document.createElement("option"); o.value = s.id; o.label = s.description; dl.appendChild(o); });
      fillSelect(symSelect, symbologyList(""), "code128");
      fillSelect(batchSelect, symbologyList(""), "code128");
      state.currentSym = state.symbologies[symSelect.value];
      showSymInfo(symSelect.value);
      buildOptionsPanel(state.currentSym);
    } catch (e) { /* offline docs still work */ }
    try { const l = await (await fetch("/api/v1/limits")).json(); state.limits = l; if (l.max_upload_bytes) $("#decode-limit").textContent = Math.round(l.max_upload_bytes / 1048576) + " MB"; } catch (e) { /* older server */ }
    // prefill from query string (legacy UI links used data=, imageFormat=, errorCorrection=...)
    const p = new URLSearchParams(location.search);
    const tab = p.get("tab");
    if (tab && $("#panel-" + tab)) activate(tab);
    if (p.get("data")) {
      formQr.querySelector("[name=data]").value = p.get("data");
      if (p.get("imageFormat")) formQr.querySelector("[name=format]").value = p.get("imageFormat").toLowerCase();
      if (p.get("errorCorrection")) formQr.querySelector("[name=error]").value = p.get("errorCorrection")[0].toUpperCase();
      if (p.get("version")) formQr.querySelector("[name=version]").value = p.get("version");
      if (p.get("foregroundColor")) formQr.querySelector("[name=fg]").value = p.get("foregroundColor");
      if (p.get("backgroundColor")) formQr.querySelector("[name=bg]").value = p.get("backgroundColor");
      if (p.get("boxSize")) formQr.querySelector("[name=scale]").value = p.get("boxSize");
      if (p.get("border")) formQr.querySelector("[name=border]").value = p.get("border");
      activate("qr"); renderQr();
    } else if (!tab) {
      formQr.querySelector("[name=data]").value = "https://github.com/cpfaffinger/codeforge";
      renderQr();
    }
    // prefill the barcode form (showcase and scanner link here with bcid/text/options)
    if (p.get("bcid")) {
      const want = p.get("bcid");
      const match = Object.keys(state.symbologies).find((k) => k.toLowerCase() === want.toLowerCase()) || want;
      symFilter.value = ""; fillSelect(symSelect, symbologyList(""), match);
      const s = state.symbologies[match];
      if (s) { state.currentSym = s; showSymInfo(match); buildOptionsPanel(s); }
      activate("barcode");
      if (p.get("text") || !s) {
        textInput.value = p.get("text") || "";
        rawOptions.value = "";
        applyOptionString(p.get("options") || "");
        validateText(); renderBarcode();
      } else { loadExample(s); }
    } else if (tab === "barcode" && !textInput.value) { $("#example-barcode").click(); }
  }
  init();
  if ("serviceWorker" in navigator && location.protocol === "https:") navigator.serviceWorker.register("/sw.js").catch(() => {});
})();
