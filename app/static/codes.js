/* codeforge: shared helpers of the live scanner (/scan) and the file decoder (/decode).
   Format names from BarcodeDetector, zxing-wasm, ZXing JS and the server API, the code groups (all / qr / 2d / 1d),
   the in-browser zxing-wasm decoder and a small history store in localStorage. */
window.CF = (function () {
  "use strict";
  const $ = (s, root) => (root || document).querySelector(s);

  // ---- format names -> codeforge bcid (for "generate with codeforge") ----
  const BCID = {
    qr_code: "qrcode", QR_CODE: "qrcode", QRCODE: "qrcode", QRCode: "qrcode", "QR Code": "qrcode",
    MicroQRCode: "microqrcode", MICROQR: "microqrcode", "Micro QR Code": "microqrcode", rMQRCode: "rectangularmicroqrcode", RMQRCode: "rectangularmicroqrcode", RMQR: "rectangularmicroqrcode",
    ean_13: "ean13", EAN_13: "ean13", EAN13: "ean13", "EAN-13": "ean13", ean_8: "ean8", EAN_8: "ean8", EAN8: "ean8", "EAN-8": "ean8",
    upc_a: "upca", UPC_A: "upca", UPCA: "upca", "UPC-A": "upca", upc_e: "upce", UPC_E: "upce", UPCE: "upce", "UPC-E": "upce",
    code_128: "code128", CODE_128: "code128", CODE128: "code128", Code128: "code128", "Code 128": "code128",
    code_39: "code39", CODE_39: "code39", CODE39: "code39", Code39: "code39", "Code 39": "code39",
    code_93: "code93", CODE_93: "code93", CODE93: "code93", Code93: "code93", "Code 93": "code93",
    itf: "interleaved2of5", ITF: "interleaved2of5", I25: "interleaved2of5", "ITF-14": "itf14", ITF14: "itf14",
    codabar: "rationalizedCodabar", CODABAR: "rationalizedCodabar", Codabar: "rationalizedCodabar",
    data_matrix: "datamatrix", DATA_MATRIX: "datamatrix", DATAMATRIX: "datamatrix", DataMatrix: "datamatrix", "Data Matrix": "datamatrix",
    pdf417: "pdf417", PDF_417: "pdf417", PDF417: "pdf417", MicroPDF417: "micropdf417", MICROPDF417: "micropdf417",
    aztec: "azteccode", AZTEC: "azteccode", Aztec: "azteccode", MAXICODE: "maxicode", MaxiCode: "maxicode",
    RSS_14: "databaromni", DATABAR: "databaromni", DataBar: "databaromni", RSS_EXPANDED: "databarexpanded", DATABAR_EXP: "databarexpanded",
    DataBarExpanded: "databarexpanded", DataBarExp: "databarexpanded", DataBarLimited: "databarlimited", DataBarLtd: "databarlimited", DATABAR_LTD: "databarlimited",
    PZN: "pzn", CODE32: "code32", TELEPEN: "telepen", ISBN: "isbn", ISBN13: "isbn",
  };
  function niceFormat(f) { return String(f || "?").replace(/([a-z])([A-Z])/g, "$1 $2").toUpperCase().replace(/_/g, " "); }
  function esc(s) { return String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])); }
  function genLink(item) {
    const bcid = BCID[item.format] || "";
    if (!bcid || bcid === "qrcode") return "/?" + new URLSearchParams({ tab: "qr", data: item.text }).toString();
    return "/?" + new URLSearchParams({ tab: "barcode", bcid, text: item.text, options: "includetext" }).toString();
  }
  function toast(msg) { const t = $("#toast"); if (!t) return; t.textContent = msg; t.hidden = false; clearTimeout(t._t); t._t = setTimeout(() => (t.hidden = true), 1800); }
  async function copy(text) { try { await navigator.clipboard.writeText(text); toast("copied"); } catch (e) { toast("copy failed - select the text instead"); } }
  function pref(key, def) { try { const v = localStorage.getItem(key); return v === null ? def : v; } catch (e) { return def; } }
  function setPref(key, v) { try { localStorage.setItem(key, v); } catch (e) { /* private mode */ } }
  function fmtMs(ms) { return ms >= 60000 ? Math.floor(ms / 60000) + " min " + Math.round((ms % 60000) / 1000) + " s" : ms >= 1000 ? (ms / 1000).toFixed(1) + " s" : Math.round(ms) + " ms"; }
  function fmtSize(b) { return b >= 1048576 ? (b / 1048576).toFixed(1) + " MB" : Math.max(1, Math.round(b / 1024)) + " kB"; }

  // ---- code groups: all, qr, 2d, 1d ----
  const norm = (f) => String(f || "").toUpperCase().replace(/[^A-Z0-9]/g, "");
  const QR = new Set(["QRCODE", "MICROQR", "MICROQRCODE", "RMQR", "RMQRCODE", "QRCODEMODEL1", "QRCODEMODEL2"]);
  const MATRIX = new Set([...QR, "DATAMATRIX", "AZTEC", "AZTECCODE", "AZTECRUNE", "PDF417", "COMPACTPDF417", "MICROPDF417", "MAXICODE", "SQCODE"]);
  const GROUPS = {
    all: "all codes (QR, 2D and 1D)",
    qr: "QR codes only",
    "2d": "2D codes (QR, DataMatrix, Aztec, PDF417 ...)",
    "1d": "1D barcodes (EAN, UPC, Code 128, ITF ...)",
  };
  function matches(format, group) {
    if (!group || group === "all") return true;
    const n = norm(format);
    if (group === "qr") return QR.has(n);
    if (group === "2d") return MATRIX.has(n);
    if (group === "1d") return !MATRIX.has(n);
    return true;
  }
  function fillGroupSelect(sel, value) {
    sel.innerHTML = Object.entries(GROUPS).map(([k, v]) => '<option value="' + k + '">' + esc(v) + "</option>").join("");
    sel.value = GROUPS[value] ? value : "all";
  }

  // ---- zxing-wasm (zxing-cpp compiled to WebAssembly), vendored in /static/vendor ----
  const WASM_FORMATS = { qr: ["QRCode", "MicroQRCode", "RMQRCode"], "2d": ["AllMatrix"], "1d": ["AllLinear"] };
  let wasmReady = false, wasmLoading = null;
  function wasmAvailable() { return !!(window.ZXingWASM && window.ZXingWASM.readBarcodes && typeof WebAssembly === "object"); }
  function ensureWasm() {
    if (wasmReady) return Promise.resolve(true);
    if (!wasmAvailable()) return Promise.resolve(false);
    if (!wasmLoading) {
      wasmLoading = ZXingWASM.prepareZXingModule({ overrides: { locateFile: (p, prefix) => (p.endsWith(".wasm") ? "/static/vendor/zxing_reader.wasm" : prefix + p) }, fireImmediately: true })
        .then(() => (wasmReady = true))
        .catch((e) => { wasmLoading = null; throw e; });
    }
    return wasmLoading;
  }
  // input: Blob/File (PNG, JPEG, GIF, BMP ...) or ImageData; returns the valid results of the wanted group
  async function wasmRead(input, group, extra) {
    if (!(await ensureWasm())) return null;
    const opts = Object.assign({ tryHarder: true, tryRotate: true, tryInvert: true, tryDownscale: true, maxNumberOfSymbols: 8 }, extra || {});
    if (WASM_FORMATS[group]) opts.formats = WASM_FORMATS[group];
    let res;
    try { res = await ZXingWASM.readBarcodes(input, opts); }
    catch (e) { if (!opts.formats) throw e; delete opts.formats; res = await ZXingWASM.readBarcodes(input, opts); }
    return res.filter((r) => r.isValid && r.text && matches(r.format, group));
  }

  // ---- history (localStorage, newest first, duplicates merged) ----
  function History(key, max) {
    const h = {
      key, max,
      load() { try { return JSON.parse(localStorage.getItem(key) || "[]"); } catch (e) { return []; } },
      save(list) { try { localStorage.setItem(key, JSON.stringify(list.slice(0, max))); } catch (e) { /* private mode / quota */ } },
      add(text, format, extra) {
        const now = Date.now(), list = h.load();
        const idx = list.findIndex((s) => s.text === text && s.format === format);
        let item;
        if (idx >= 0) { item = list.splice(idx, 1)[0]; item.count = (item.count || 1) + 1; item.last = now; }
        else item = { text, format, first: now, last: now, count: 1 };
        Object.assign(item, extra || {});
        list.unshift(item); h.save(list);
        return item;
      },
      remove(i) { const l = h.load(); l.splice(i, 1); h.save(l); return l; },
      clear() { h.save([]); },
    };
    return h;
  }
  function actionsHtml(item, idx) {
    const isUrl = /^https?:\/\//i.test(item.text);
    return '<a class="primary" href="' + esc(genLink(item)) + '">Generate with codeforge</a>' +
      '<button type="button" data-copy="' + idx + '">Copy</button>' + (isUrl ? '<a href="' + esc(item.text) + '" target="_blank" rel="noopener">Open link</a>' : "") +
      (idx >= 0 ? '<button type="button" data-del="' + idx + '">Delete</button>' : "");
  }
  // codes: [{text, format}], wires the copy buttons
  function codeListHtml(codes) {
    return codes.map((c, i) => '<div class="code"><div class="meta"><span class="fmt">' + esc(niceFormat(c.format)) + "</span>" + (c.via ? "<span>" + esc(c.via) + "</span>" : "") + '</div><div class="raw">' + esc(c.text) + '</div><div class="acts">' + actionsHtml(c, -1).replace('data-copy="-1"', 'data-copy-code="' + i + '"') + "</div></div>").join("");
  }
  function wireCodeList(box, codes) { box.querySelectorAll("[data-copy-code]").forEach((b) => b.addEventListener("click", () => copy(codes[+b.dataset.copyCode].text))); }
  function renderHistory(store, box, info, highlightKey, emptyText) {
    const list = store.load();
    box.innerHTML = "";
    if (info) info.textContent = list.length ? list.length + " / " + store.max : "";
    if (!list.length) { box.innerHTML = '<div class="empty">' + esc(emptyText || "Nothing yet.") + "</div>"; return; }
    list.forEach((item, i) => {
      const el = document.createElement("div");
      el.className = "scan" + (highlightKey === item.format + "|" + item.text ? " new" : "");
      el.innerHTML = '<div class="raw">' + esc(item.text) + "</div>" +
        '<div class="meta"><span class="fmt">' + esc(niceFormat(item.format)) + "</span><span>" + new Date(item.last).toLocaleString() + "</span>" +
        (item.count > 1 ? "<span>seen " + item.count + "x</span>" : "") + (item.source ? "<span>" + esc(item.source) + "</span>" : "") + (item.file ? "<span>" + esc(item.file) + "</span>" : "") + "</div>" +
        '<div class="acts">' + actionsHtml(item, i) + "</div>";
      box.appendChild(el);
    });
    box.querySelectorAll("[data-copy]").forEach((b) => b.addEventListener("click", () => copy(store.load()[+b.dataset.copy].text)));
    box.querySelectorAll("[data-del]").forEach((b) => b.addEventListener("click", () => { store.remove(+b.dataset.del); renderHistory(store, box, info, null, emptyText); }));
  }

  // ---- CSV export ----
  function csv(rows, header) {
    const cell = (v) => { const s = v == null ? "" : String(v); return /[",\n\r;]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s; };
    return [header, ...rows].map((r) => r.map(cell).join(",")).join("\r\n") + "\r\n";
  }
  function download(name, text, type) {
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob(["﻿" + text], { type: type || "text/csv;charset=utf-8" }));
    a.download = name; document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 2000);
  }
  function historyCsv(store) {
    return csv(store.load().map((s) => [s.text, niceFormat(s.format), s.count || 1, new Date(s.first).toISOString(), new Date(s.last).toISOString(), s.source || "", s.file || ""]),
      ["text", "format", "count", "first_seen", "last_seen", "source", "file"]);
  }

  // ---- PWA ----
  function registerServiceWorker() { if ("serviceWorker" in navigator && location.protocol === "https:") navigator.serviceWorker.register("/sw.js").catch(() => {}); }

  return { $, BCID, niceFormat, esc, genLink, toast, copy, pref, setPref, fmtMs, fmtSize, GROUPS, matches, fillGroupSelect, WASM_FORMATS, wasmAvailable, ensureWasm, wasmRead,
    isWasmReady: () => wasmReady, History, actionsHtml, codeListHtml, wireCodeList, renderHistory, csv, download, historyCsv, registerServiceWorker };
})();
