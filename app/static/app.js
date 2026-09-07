/* codeforge playground - vanilla JS, no build step, no external dependencies */
(function () {
  "use strict";
  const $ = (sel, root) => (root || document).querySelector(sel);
  const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));
  const state = { symbologies: {}, blobs: {}, lastQrUrl: null };

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
    $$("input[type=checkbox]", form).forEach((cb) => { out[cb.name] = cb.checked; });
    return out;
  }
  function qs(obj) {
    const p = new URLSearchParams();
    Object.entries(obj).forEach(([k, v]) => { if (v !== "" && v !== null && v !== undefined && v !== false) p.set(k, v === true ? "true" : v); });
    return p.toString();
  }
  function absolute(path) { return location.origin + path; }
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
  function escapeHtml(s) { return String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])); }
  async function fetchImage(id, url, meta) {
    try {
      const r = await fetch(url);
      if (!r.ok) {
        const ct = r.headers.get("content-type") || "";
        const body = ct.includes("json") ? (await r.json()).detail : await r.text();
        showError(id, typeof body === "string" ? body : JSON.stringify(body));
        return null;
      }
      const blob = await r.blob();
      showPreview(id, blob, meta || (r.headers.get("content-type") + " - " + Math.round(blob.size / 102.4) / 10 + " kB"));
      return blob;
    } catch (e) { showError(id, e.message); return null; }
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
    if (!d.data) return;
    const params = { data: d.data, error: d.error, version: d.version, scale: d.scale, border: d.border, fg: d.fg, bg: d.bg, format: d.format, micro: d.micro };
    const url = "/api/v1/qr?" + qs(params);
    const legacy = "/qr?" + qs({ data: d.data, format: d.format.toUpperCase(), ec: { L: "LOW", M: "MEDIUM", Q: "QUARTILE", H: "HIGH" }[d.error], version: d.version, fg: d.fg !== "#000000" ? d.fg : "", bg: d.bg !== "#ffffff" ? d.bg : "", box: d.scale !== "10" ? d.scale : "", border: d.border !== "4" ? d.border : "" });
    $("#url-qr").value = absolute(url); $("#url-qr-legacy").value = absolute(legacy);
    state.lastQrUrl = url;
    fetchImage("qr", url);
    fetch("/api/v1/qr/info?" + qs({ data: d.data, error: d.error, version: d.version, micro: d.micro })).then((r) => r.json()).then((j) => {
      $("#info-qr").textContent = j.detail ? j.detail : "version " + j.version + (j.is_micro ? " (micro)" : "") + " - " + j.modules + "x" + j.modules + " modules - error level " + j.error + " - mode " + j.mode;
    }).catch(() => {});
  }
  formQr.addEventListener("submit", (e) => { e.preventDefault(); renderQr(); });
  formQr.addEventListener("input", debounce(renderQr, 350));

  // ---------------------------------------------------------------- barcode
  const formBc = $("#form-barcode");
  function barcodeParams() {
    const d = formData(formBc);
    const opts = parseOptions(d.options);
    if (d.backgroundcolor && d.backgroundcolor.toLowerCase() !== "#ffffff") opts.backgroundcolor = d.backgroundcolor.replace("#", "");
    return { d, opts };
  }
  function renderBarcode() {
    const { d, opts } = barcodeParams();
    if (!d.bcid || !d.text) return;
    const base = { bcid: d.bcid, text: d.text, scale: d.scale, rotate: d.rotate, paddingwidth: d.paddingwidth, paddingheight: d.paddingheight, format: d.format };
    const url = "/api/v1/barcode?" + qs(Object.assign({}, base, opts));
    const legacy = "/?" + qs(Object.assign({ bcid: d.bcid, text: d.text }, opts, { scale: d.scale !== "2" ? d.scale : "", rotate: d.rotate !== "N" ? d.rotate : "", paddingwidth: d.paddingwidth !== "1" ? d.paddingwidth : "", paddingheight: d.paddingheight !== "1" ? d.paddingheight : "", format: d.format !== "png" ? d.format : "" }));
    $("#url-barcode").value = absolute(url); $("#url-barcode-legacy").value = absolute(legacy);
    fetchImage("barcode", url);
  }
  formBc.addEventListener("submit", (e) => { e.preventDefault(); renderBarcode(); });
  const renderBarcodeDebounced = debounce(renderBarcode, 400);
  formBc.addEventListener("input", (e) => { if (e.target.id !== "sym-filter") renderBarcodeDebounced(); });
  // ---- symbology picker: every supported symbology, grouped by category, filterable ----
  const symSelect = $("#sym-select"), symFilter = $("#sym-filter"), batchSelect = $("#batch-bcid");
  function fillSelect(sel, list, keep) {
    const current = keep || sel.value;
    sel.innerHTML = "";
    const groups = {};
    list.forEach((s) => { (groups[s.category] = groups[s.category] || []).push(s); });
    Object.keys(groups).sort().forEach((cat) => {
      const og = document.createElement("optgroup"); og.label = cat;
      groups[cat].forEach((s) => { const o = document.createElement("option"); o.value = s.id; o.textContent = s.description + "  (" + s.id + ")"; og.appendChild(o); });
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
    $("#sym-cat").textContent = s.category + "  -  id: " + s.id;
    $("#sym-wiki").href = s.wiki || "https://github.com/bwipp/postscriptbarcode/wiki";
    const parts = [];
    if (s.charset === "digits") parts.push("digits only");
    else if (s.charset === "alphanumeric") parts.push("alphanumeric");
    else parts.push("any text (see rules)");
    if (s.min_length != null && s.max_length != null) parts.push(s.min_length === s.max_length ? "exactly " + s.min_length + " characters" : s.min_length + " to " + s.max_length + " characters");
    else if (s.min_length != null) parts.push("at least " + s.min_length + " characters");
    else if (s.max_length != null) parts.push("at most " + s.max_length + " characters");
    $("#sym-summary").textContent = parts.join("  -  ");
    const ul = $("#sym-rules"); ul.innerHTML = "";
    (s.rules && s.rules.length ? s.rules : ["No specific rules: the encoder accepts the data as given (length limits depend on the symbol size)."]).forEach((r) => { const li = document.createElement("li"); li.textContent = r; ul.appendChild(li); });
    $("#sym-example").innerHTML = s.example ? "Example: <code>" + escapeHtml(s.example) + "</code>" + (s.example_options ? " with options <code>" + escapeHtml(s.example_options) + "</code>" : "") : "";
  }
  symFilter.addEventListener("input", () => { fillSelect(symSelect, symbologyList(symFilter.value)); showSymInfo(symSelect.value); });
  symSelect.addEventListener("change", () => { showSymInfo(symSelect.value); renderBarcode(); });
  $("#example-barcode").addEventListener("click", () => {
    let s = state.symbologies[symSelect.value];
    if (!s) { s = state.symbologies.code128 || Object.values(state.symbologies)[0]; if (s) { symFilter.value = ""; fillSelect(symSelect, symbologyList(""), s.id); } }
    if (!s) return;
    formBc.querySelector("[name=text]").value = s.example || "";
    formBc.querySelector("[name=options]").value = s.example_options || "";
    showSymInfo(s.id);
    renderBarcode();
  });

  // ----------------------------------------------------------------- decode
  const drop = $("#dropzone");
  const fileInput = $("#form-decode [name=file]");
  async function decodeBlob(blob) {
    const box = $("#decode-results");
    box.innerHTML = '<div class="muted">decoding ...</div>';
    showPreview("decode", blob, blob.type + " - " + Math.round(blob.size / 1024) + " kB");
    const fd = new FormData(); fd.append("file", blob, "upload");
    try {
      const r = await fetch("/api/v1/decode", { method: "POST", body: fd });
      const j = await r.json();
      if (!r.ok) { box.innerHTML = '<div class="error">' + escapeHtml(j.detail) + "</div>"; return; }
      if (!j.count) { box.innerHTML = '<div class="muted">no code found</div>'; return; }
      box.innerHTML = '<table class="results"><tr><th>#</th><th>Type</th><th>Data</th></tr>' + j.symbols.map((s, i) => "<tr><td>" + (i + 1) + "</td><td>" + escapeHtml(s.type) + "</td><td><code>" + escapeHtml(s.data) + "</code></td></tr>").join("") + "</table>";
    } catch (e) { box.innerHTML = '<div class="error">' + escapeHtml(e.message) + "</div>"; }
  }
  drop.addEventListener("click", (e) => { if (e.target.tagName !== "INPUT") fileInput.click(); });
  drop.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") fileInput.click(); });
  fileInput.addEventListener("change", () => { if (fileInput.files[0]) decodeBlob(fileInput.files[0]); });
  ["dragenter", "dragover"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("over"); }));
  ["dragleave", "drop"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
  drop.addEventListener("drop", (e) => { const f = e.dataTransfer.files[0]; if (f) decodeBlob(f); });
  document.addEventListener("paste", (e) => {
    if (!$("#panel-decode").classList.contains("active")) return;
    const item = Array.from(e.clipboardData.items).find((i) => i.type.startsWith("image/"));
    if (item) decodeBlob(item.getAsFile());
  });
  $("#decode-sample").addEventListener("click", async () => {
    if (!state.lastQrUrl) { toast("render a QR code first"); return; }
    const r = await fetch(state.lastQrUrl.replace(/format=[a-z]+/, "format=png"));
    decodeBlob(await r.blob());
  });

  // ------------------------------------------------------------------ batch
  const formBatch = $("#form-batch");
  formBatch.querySelector("[name=type]").addEventListener("change", (e) => { $("#batch-bcid-label").hidden = e.target.value !== "barcode"; });
  formBatch.querySelector("[name=output]").addEventListener("change", (e) => { $("#sheet-options").hidden = e.target.value !== "pdf"; });
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
    const box = $("#preview-batch"); box.innerHTML = '<div class="placeholder">generating ...</div>';
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
        showPreview("batch", blob, "PDF - " + Math.round(blob.size / 1024) + " kB");
        const b = document.createElement("button"); b.type = "button"; b.id = "dl-batch"; b.textContent = "Download PDF"; b.style.position = "absolute"; b.style.top = ".6rem"; b.style.right = ".6rem"; box.appendChild(b);
      } else {
        box.innerHTML = '<div class="result-box"><div>ZIP ready - ' + Math.round(blob.size / 1024) + ' kB</div><button type="button" id="dl-batch">Download ZIP</button></div>';
      }
      $("#dl-batch").addEventListener("click", () => download("batch", "codes"));
    } catch (err) { showError("batch", err.message); }
  });

  // ------------------------------------------------------------------- init
  async function init() {
    try {
      const h = await (await fetch("/healthz")).json();
      $("#version").textContent = "v" + h.version + (h.ghostscript ? "" : " (barcodes unavailable: ghostscript missing)");
      const list = await (await fetch("/api/v1/symbologies")).json();
      const dl = $("#symbology-list");
      list.forEach((s) => { state.symbologies[s.id] = s; const o = document.createElement("option"); o.value = s.id; o.label = s.description; dl.appendChild(o); });
      fillSelect(symSelect, symbologyList(""), "code128");
      fillSelect(batchSelect, symbologyList(""), "code128");
      showSymInfo(symSelect.value);
    } catch (e) { /* offline docs still work */ }
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
      formBc.querySelector("[name=text]").value = p.get("text") || "";
      formBc.querySelector("[name=options]").value = p.get("options") || "";
      showSymInfo(match);
      activate("barcode"); renderBarcode();
    } else if (tab === "barcode" && !formBc.querySelector("[name=text]").value) { $("#example-barcode").click(); }
  }
  init();
})();
