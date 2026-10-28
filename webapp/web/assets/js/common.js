/* آبان ترید — ابزارهای مشترک رابط کاربری */
(function (window, document) {
  "use strict";

  const SPRITE = "/assets/icons/sprite.svg";
  const FA_DIGITS = "۰۱۲۳۴۵۶۷۸۹";
  const EN_DIGITS = "0123456789";

  function $(sel, root) { return (root || document).querySelector(sel); }
  function $$(sel, root) { return Array.from((root || document).querySelectorAll(sel)); }

  function esc(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  function icon(name, cls) {
    return '<svg class="icon ' + (cls || "") + '" aria-hidden="true"><use href="' + SPRITE + "#i-" + name + '"></use></svg>';
  }

  function toFa(value) {
    return String(value == null ? "" : value).replace(/[0-9]/g, function (d) { return FA_DIGITS[+d]; });
  }
  function toEn(value) {
    return String(value == null ? "" : value).replace(/[۰-۹]/g, function (d) { return EN_DIGITS[FA_DIGITS.indexOf(d)]; })
      .replace(/[٠-٩]/g, function (d) { return EN_DIGITS["٠١٢٣٤٥٦٧٨٩".indexOf(d)]; });
  }
  function parseNum(value) {
    const n = Number(toEn(String(value == null ? "" : value)).replace(/,/g, "").trim());
    return Number.isFinite(n) ? n : 0;
  }

  function fmtNum(value, digits) {
    const n = Number(value);
    if (!Number.isFinite(n)) return toFa("0");
    const d = digits == null ? (Number.isInteger(n) ? 0 : 2) : digits;
    try {
      return toFa(new Intl.NumberFormat("en-US", { maximumFractionDigits: d, minimumFractionDigits: 0 }).format(n));
    } catch (e) {
      return toFa(n.toFixed(d));
    }
  }

  function fmtPrice(value) {
    const n = Number(value || 0);
    if (!Number.isFinite(n)) return toFa("0");
    const abs = Math.abs(n);
    let d;
    if (abs >= 1000) d = 2;
    else if (abs >= 100) d = 2;
    else if (abs >= 1) d = 4;
    else if (abs >= 0.01) d = 5;
    else if (abs >= 0.0001) d = 7;
    else d = 9;
    let out = n.toLocaleString("en-US", { maximumFractionDigits: d, minimumFractionDigits: 0 });
    return toFa(out);
  }

  function fmtCompact(value) {
    const n = Number(value || 0);
    const abs = Math.abs(n);
    if (abs >= 1e9) return toFa((n / 1e9).toFixed(abs >= 1e10 ? 0 : 1)) + " میلیارد";
    if (abs >= 1e6) return toFa((n / 1e6).toFixed(abs >= 1e7 ? 0 : 1)) + " میلیون";
    if (abs >= 1e4) return toFa((n / 1e3).toFixed(0)) + " هزار";
    return fmtNum(n, 0);
  }

  function fmtBytes(value) {
    const n = Math.max(0, Number(value || 0));
    if (n >= 1024 * 1024 * 1024) return toFa((n / (1024 * 1024 * 1024)).toFixed(1)) + " گیگابایت";
    if (n >= 1024 * 1024) return toFa((n / (1024 * 1024)).toFixed(1)) + " مگابایت";
    if (n >= 1024) return toFa((n / 1024).toFixed(1)) + " کیلوبایت";
    return toFa(Math.round(n)) + " بایت";
  }

  function fmtPct(value, digits) {
    const n = Number(value || 0);
    const d = digits == null ? 2 : digits;
    const sign = n > 0 ? "+" : (n < 0 ? "−" : "");
    return sign + toFa(Math.abs(n).toFixed(d)) + "٪";
  }
  function pctClass(value) {
    const n = Number(value || 0);
    return n > 0 ? "pos" : (n < 0 ? "neg" : "muted");
  }

  function toDate(value) {
    if (value instanceof Date) return isNaN(value.getTime()) ? null : value;
    if (typeof value === "number") return new Date(value < 1e12 ? value * 1000 : value);
    if (typeof value === "string") {
      const t = value.trim();
      if (!t || t === "-") return null;
      if (/^\d+(\.\d+)?$/.test(t)) return toDate(Number(t));
      const iso = t.indexOf("T") >= 0 || t.endsWith("Z") ? t : t.replace(" ", "T") + "Z";
      const d = new Date(iso);
      return isNaN(d.getTime()) ? null : d;
    }
    return null;
  }

  const _fmtCache = {};
  function faDate(value, opts) {
    const d = toDate(value);
    if (!d) return "-";
    const key = JSON.stringify(opts || {});
    if (!_fmtCache[key]) {
      _fmtCache[key] = new Intl.DateTimeFormat("fa-IR-u-ca-persian", Object.assign({ timeZone: "Asia/Tehran", calendar: "persian" }, opts || {}));
    }
    try { return _fmtCache[key].format(d); } catch (e) { return "-"; }
  }
  function fmtDate(value) { return faDate(value, { year: "numeric", month: "long", day: "numeric" }); }
  function fmtDateShort(value) { return faDate(value, { month: "2-digit", day: "2-digit" }); }
  function fmtTime(value) { return faDate(value, { hour: "2-digit", minute: "2-digit", hour12: false }); }
  function fmtDateTime(value) { return faDate(value, { year: "numeric", month: "long", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false }); }
  function fmtWeekday(value) { return faDate(value, { weekday: "long", day: "numeric", month: "long", year: "numeric" }); }

  function timeAgo(value) {
    const d = toDate(value);
    if (!d) return "-";
    const sec = Math.max(0, Math.floor((Date.now() - d.getTime()) / 1000));
    if (sec < 60) return "همین حالا";
    const min = Math.floor(sec / 60);
    if (min < 60) return toFa(min) + " دقیقه پیش";
    const hour = Math.floor(min / 60);
    if (hour < 24) return toFa(hour) + " ساعت پیش";
    const day = Math.floor(hour / 24);
    if (day < 30) return toFa(day) + " روز پیش";
    return fmtDate(d);
  }

  function initials(name) {
    const parts = String(name || "").trim().split(/\s+/).filter(Boolean);
    if (!parts.length) return "آ";
    if (parts.length === 1) return parts[0].slice(0, 2);
    return (parts[0][0] || "") + (parts[1][0] || "");
  }

  function coinClass(symbol) {
    const s = String(symbol || "").toUpperCase().replace(/[^A-Z]/g, "");
    const base = ["BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "ADA", "AVAX", "LINK", "TON"].find(function (x) { return s.startsWith(x); });
    return base ? " " + base.toLowerCase() : "";
  }
  function coinLetters(symbol) {
    const s = String(symbol || "").toUpperCase().replace(/USDT$/, "").replace(/[^A-Z0-9]/g, "");
    return (s || "?").slice(0, 3);
  }

  /* ---------- theme ---------- */
  function currentTheme() { return document.documentElement.getAttribute("data-theme") === "dark" ? "dark" : "light"; }
  function syncThemeIcon() {
    const dark = currentTheme() === "dark";
    $$("#themeIcon use, .theme-icon use").forEach(function (u) {
      u.setAttribute("href", SPRITE + "#i-" + (dark ? "sun" : "moon"));
    });
    const meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.setAttribute("content", dark ? "#0a0f19" : "#f4f6fb");
  }
  function setTheme(theme) {
    document.documentElement.setAttribute("data-theme", theme === "dark" ? "dark" : "light");
    try { localStorage.setItem("aban-theme", theme); } catch (e) {}
    syncThemeIcon();
    document.dispatchEvent(new CustomEvent("aban:theme", { detail: { theme: theme } }));
  }
  function toggleTheme() { setTheme(currentTheme() === "dark" ? "light" : "dark"); }

  /* ---------- toast ---------- */
  function toast(message, type, ms) {
    const root = $("#toasts");
    if (!root) return;
    const kind = type === "error" ? "error" : (type === "success" ? "success" : (type === "warn" ? "warn" : "info"));
    const el = document.createElement("div");
    el.className = "toast " + kind;
    el.setAttribute("role", "status");
    const ic = kind === "success" ? "check" : (kind === "error" ? "alert" : (kind === "warn" ? "alert" : "info"));
    el.innerHTML = icon(ic) + "<div>" + esc(message) + "</div>";
    root.appendChild(el);
    setTimeout(function () {
      el.classList.add("leaving");
      setTimeout(function () { el.remove(); }, 220);
    }, ms || (kind === "error" ? 5200 : 3600));
  }

  /* ---------- api ---------- */
  function isLoginPage() { return /\/login\/?$/.test(location.pathname); }
  function isAdminPage() { return /^\/admin(\/|$)/.test(location.pathname); }

  async function api(path, options) {
    const opts = Object.assign({ method: "GET", redirectOn401: true }, options || {});
    const init = { method: opts.method, credentials: "same-origin", headers: Object.assign({}, opts.headers || {}) };
    if (opts.signal) init.signal = opts.signal;
    if (opts.body !== undefined && !(opts.body instanceof FormData)) {
      init.headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(opts.body);
    } else if (opts.body instanceof FormData) {
      init.body = opts.body;
    }
    let res;
    try {
      res = await fetch(path, init);
    } catch (e) {
      if (e && e.name === "AbortError") {
        const aborted = new Error("عملیات لغو شد.");
        aborted.name = "AbortError";
        throw aborted;
      }
      throw new Error("ارتباط با سرور برقرار نشد. اتصال اینترنت را بررسی کنید.");
    }
    let data = null;
    const ct = res.headers.get("content-type") || "";
    if (ct.indexOf("application/json") >= 0) {
      try { data = await res.json(); } catch (e) { data = null; }
    }
    if (!res.ok) {
      if (res.status === 401 && opts.redirectOn401 && !isLoginPage()) {
        location.href = isAdminPage() ? "/login?admin=1" : "/login";
        throw new Error("نشست شما منقضی شده است.");
      }
      const message = (data && (data.error || data.detail)) || "درخواست انجام نشد. دوباره تلاش کنید.";
      const err = new Error(typeof message === "string" ? message : "درخواست نامعتبر است.");
      err.status = res.status;
      err.data = data;
      throw err;
    }
    return data;
  }

  async function logout() {
    try { await api("/api/auth/logout", { method: "POST", redirectOn401: false }); } catch (e) {}
    location.href = "/login";
  }

  /* ---------- modal ---------- */
  let _modalCleanup = null;
  function closeModal() {
    const root = $("#modalRoot");
    if (!root) return;
    if (_modalCleanup) { try { _modalCleanup(); } catch (e) {} _modalCleanup = null; }
    root.classList.remove("on");
    root.innerHTML = "";
    document.body.classList.remove("no-scroll");
  }

  function openModal(config) {
    const root = $("#modalRoot");
    if (!root) return null;
    const cfg = config || {};
    const size = cfg.size === "wide" ? "wide" : (cfg.size === "narrow" ? "narrow" : "");
    const cardClass = cfg.drawer ? "drawer-card" : ("modal-card " + size);
    const head = cfg.title ? (
      '<div class="' + (cfg.drawer ? "drawer-head" : "modal-head") + '">' +
        '<div class="flex-1"><div class="' + (cfg.drawer ? "modal-title" : "modal-title") + '">' + cfg.title + "</div>" +
        (cfg.sub ? '<div class="modal-sub">' + cfg.sub + "</div>" : "") + "</div>" +
        '<button class="icon-btn modal-close" data-modal-close aria-label="بستن">' + icon("close") + "</button>" +
      "</div>"
    ) : "";
    const bodyClass = cfg.drawer ? "drawer-body" : "modal-body";
    const foot = cfg.footer ? '<div class="' + (cfg.drawer ? "drawer-foot" : "modal-foot") + '">' + cfg.footer + "</div>" : "";
    root.innerHTML =
      '<div class="modal-backdrop" data-modal-close></div>' +
      '<div class="' + cardClass + '" role="dialog" aria-modal="true">' + head +
        '<div class="' + bodyClass + '">' + (cfg.body || "") + "</div>" + foot +
      "</div>";
    root.classList.add("on");
    document.body.classList.add("no-scroll");

    function onKey(e) {
      if (e.key === "Escape") closeModal();
    }
    document.addEventListener("keydown", onKey);
    root.querySelectorAll("[data-modal-close]").forEach(function (b) {
      b.addEventListener("click", closeModal);
    });
    const result = {
      root: root,
      card: root.querySelector(".modal-card, .drawer-card"),
      body: root.querySelector("." + bodyClass),
      close: closeModal
    };
    _modalCleanup = function () {
      document.removeEventListener("keydown", onKey);
      if (typeof cfg.onClose === "function") { try { cfg.onClose(result); } catch (e) {} }
    };
    if (typeof cfg.onMount === "function") cfg.onMount(result);
    return result;
  }

  function confirmDialog(config) {
    const cfg = config || {};
    return new Promise(function (resolve) {
      let done = false;
      const m = openModal({
        size: "narrow",
        title: cfg.title || "تأیید عملیات",
        body: '<div class="stack"><div class="notice ' + (cfg.danger ? "danger" : "") + '">' + icon(cfg.danger ? "alert" : "info") +
          "<div>" + esc(cfg.message || "آیا از انجام این عملیات مطمئن هستید؟") + "</div></div></div>",
        footer:
          '<button class="btn btn-ghost" data-cancel>' + esc(cfg.cancelText || "انصراف") + "</button>" +
          '<button class="btn ' + (cfg.danger ? "btn-danger" : "btn-primary") + '" data-ok>' + esc(cfg.confirmText || "تأیید") + "</button>"
      });
      if (m) {
        const card = m.root.querySelector(".modal-card");
        card.querySelector("[data-ok]").addEventListener("click", function () { done = true; resolve(true); closeModal(); });
        card.querySelector("[data-cancel]").addEventListener("click", function () { done = true; resolve(false); closeModal(); });
      }
      const obs = new MutationObserver(function () {
        if (!document.querySelector("#modalRoot.on") && !done) { done = true; resolve(false); obs.disconnect(); }
      });
      const mr = $("#modalRoot");
      if (mr) obs.observe(mr, { attributes: true, attributeFilter: ["class"] });
    });
  }

  function promptDialog(config) {
    const cfg = config || {};
    return new Promise(function (resolve) {
      const field = cfg.multiline
        ? '<textarea class="textarea" id="promptInput" placeholder="' + esc(cfg.placeholder || "") + '">' + esc(cfg.value || "") + "</textarea>"
        : '<input class="input" id="promptInput" placeholder="' + esc(cfg.placeholder || "") + '" value="' + esc(cfg.value || "") + '">';
      let settled = false;
      const m = openModal({
        size: "narrow",
        title: cfg.title || "ورود اطلاعات",
        body: '<div class="field"><label class="label">' + esc(cfg.label || "") + "</label>" + field + "</div>",
        footer: '<button class="btn btn-ghost" data-cancel>' + esc(cfg.cancelText || "انصراف") + "</button>" +
          '<button class="btn btn-primary" data-ok>' + esc(cfg.confirmText || "ثبت") + "</button>",
        onMount: function (m2) {
          const input = m2.body.querySelector("#promptInput");
          input.focus();
          m2.root.querySelector("[data-ok]").addEventListener("click", function () {
            settled = true;
            resolve(input.value);
            closeModal();
          });
          m2.root.querySelector("[data-cancel]").addEventListener("click", function () {
            settled = true;
            resolve(null);
            closeModal();
          });
          input.addEventListener("keydown", function (e) {
            if (e.key === "Enter" && !cfg.multiline) {
              e.preventDefault();
              m2.root.querySelector("[data-ok]").click();
            }
          });
        }
      });
      const obs = new MutationObserver(function () {
        if (!document.querySelector("#modalRoot.on") && !settled) { settled = true; resolve(null); obs.disconnect(); }
      });
      const mr = $("#modalRoot");
      if (mr) obs.observe(mr, { attributes: true, attributeFilter: ["class"] });
      if (!m) resolve(null);
    });
  }

  function copyText(text) {
    const value = String(text == null ? "" : text);
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(value).then(function () { toast("کپی شد.", "success", 1800); }).catch(function () { fallback(); });
    } else fallback();
    function fallback() {
      const ta = document.createElement("textarea");
      ta.value = value;
      ta.setAttribute("readonly", "");
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.select();
      try { document.execCommand("copy"); toast("کپی شد.", "success", 1800); } catch (e) { toast("کپی انجام نشد.", "error"); }
      ta.remove();
    }
  }

  function debounce(fn, wait) {
    let t;
    return function () {
      const args = arguments, self = this;
      clearTimeout(t);
      t = setTimeout(function () { fn.apply(self, args); }, wait || 260);
    };
  }

  function setBusy(btn, busy, label) {
    if (!btn) return;
    if (busy) {
      if (!btn.dataset.original) btn.dataset.original = btn.innerHTML;
      btn.disabled = true;
      btn.innerHTML = '<span class="spinner white"></span><span>' + esc(label || "لطفاً صبر کنید…") + "</span>";
    } else {
      btn.disabled = false;
      if (btn.dataset.original) { btn.innerHTML = btn.dataset.original; delete btn.dataset.original; }
    }
  }

  /* ---------- svg mini charts ---------- */
  let _gradId = 0;
  function sparkline(values, options) {
    const o = options || {};
    const data = (values || []).map(function (v) { return Number(v) || 0; });
    const w = 120, h = 34, pad = 2;
    if (!data.length) data.push(0);
    const min = Math.min.apply(null, data), max = Math.max.apply(null, data);
    const span = max - min || 1;
    const step = data.length > 1 ? (w - pad * 2) / (data.length - 1) : 0;
    const pts = data.map(function (v, i) {
      return [pad + i * step, h - pad - ((v - min) / span) * (h - pad * 2)];
    });
    let d = "";
    pts.forEach(function (p, i) {
      if (!i) { d += "M" + p[0].toFixed(1) + " " + p[1].toFixed(1); return; }
      const prev = pts[i - 1];
      const cx = (prev[0] + p[0]) / 2;
      d += " C" + cx.toFixed(1) + " " + prev[1].toFixed(1) + " " + cx.toFixed(1) + " " + p[1].toFixed(1) + " " + p[0].toFixed(1) + " " + p[1].toFixed(1);
    });
    const color = o.color || "var(--primary)";
    const id = "sg" + (++_gradId);
    const area = d + " L" + (w - pad) + " " + h + " L" + pad + " " + h + " Z";
    return '<svg viewBox="0 0 ' + w + " " + h + '" preserveAspectRatio="none" aria-hidden="true">' +
      "<defs><linearGradient id=\"" + id + '" x1="0" y1="0" x2="0" y2="1">' +
      '<stop offset="0%" stop-color="' + color + '" stop-opacity=".26"/>' +
      '<stop offset="100%" stop-color="' + color + '" stop-opacity="0"/></linearGradient></defs>' +
      '<path d="' + area + '" fill="url(#' + id + ')"/>' +
      '<path d="' + d + '" fill="none" stroke="' + color + '" stroke-width="1.8" stroke-linecap="round" vector-effect="non-scaling-stroke"/>' +
      '<circle cx="' + pts[pts.length - 1][0].toFixed(1) + '" cy="' + pts[pts.length - 1][1].toFixed(1) + '" r="2.1" fill="' + color + '"/>' +
      "</svg>";
  }

  function gaugeSvg(value, label) {
    const v = Math.max(0, Math.min(100, Number(value) || 0));
    const len = Math.PI * 50;
    const dash = (len * v / 100).toFixed(2);
    const angle = (v / 100) * 180 - 90;
    return '<div class="gauge" role="img" aria-label="شاخص ترس و طمع: ' + v + '">' +
      '<svg viewBox="0 0 120 74">' +
      '<defs><linearGradient id="gaugeGrad" x1="0" y1="0" x2="1" y2="0">' +
      '<stop offset="0" stop-color="#ef4d5a"/><stop offset=".5" stop-color="#f2b84b"/><stop offset="1" stop-color="#16b981"/>' +
      "</linearGradient></defs>" +
      '<path d="M10 64 A50 50 0 0 1 110 64" fill="none" stroke="var(--surface-3)" stroke-width="10" stroke-linecap="round"/>' +
      '<path d="M10 64 A50 50 0 0 1 110 64" fill="none" stroke="url(#gaugeGrad)" stroke-width="10" stroke-linecap="round" stroke-dasharray="' + dash + " 999\"/>" +
      '<line x1="60" y1="64" x2="60" y2="23" stroke="var(--text)" stroke-width="2.4" stroke-linecap="round" transform="rotate(' + angle.toFixed(1) + ' 60 64)"/>' +
      '<circle cx="60" cy="64" r="4.2" fill="var(--surface)" stroke="var(--text)" stroke-width="2"/>' +
      "</svg>" +
      '<div class="gauge-val">' + toFa(Math.round(v)) + "</div>" +
      '<div class="gauge-lab">' + esc(label || "") + "</div></div>";
  }

  function donutSvg(parts, centerValue, centerLabel) {
    const items = (parts || []).filter(function (p) { return Number(p.value) > 0; });
    const total = items.reduce(function (s, p) { return s + Number(p.value); }, 0) || 1;
    const r = 54, c = 2 * Math.PI * r;
    let offset = 0;
    let circles = "";
    items.forEach(function (p) {
      const frac = Number(p.value) / total;
      const dash = Math.max(0, frac * c - 3);
      circles += '<circle cx="75" cy="75" r="' + r + '" fill="none" stroke="' + p.color + '" stroke-width="14" stroke-linecap="round" ' +
        'stroke-dasharray="' + dash.toFixed(2) + " " + (c - dash).toFixed(2) + '" stroke-dashoffset="' + (-offset).toFixed(2) + '"/>';
      offset += frac * c;
    });
    return '<div class="donut"><svg viewBox="0 0 150 150">' +
      '<circle cx="75" cy="75" r="' + r + '" fill="none" stroke="var(--surface-3)" stroke-width="14"/>' +
      circles + "</svg>" +
      '<div class="donut-center"><div><b>' + esc(centerValue == null ? "" : centerValue) + "</b><span>" + esc(centerLabel || "") + "</span></div></div></div>";
  }

  /* ---------- small renderers ---------- */
  function pill(text, tone) {
    return '<span class="pill ' + (tone || "") + '">' + esc(text) + "</span>";
  }
  function signalPill(type) {
    const t = String(type || "");
    if (t === "حمایت") return '<span class="pill green">حمایت</span>';
    if (t === "مقاومت") return '<span class="pill red">مقاومت</span>';
    return '<span class="pill gray">' + esc(t || "-") + "</span>";
  }
  function directionPill(dir) {
    const d = String(dir || "").toUpperCase();
    if (d === "LONG") return '<span class="pill green">لانگ</span>';
    if (d === "SHORT") return '<span class="pill red">شورت</span>';
    return '<span class="pill gray">' + esc(d || "-") + "</span>";
  }
  function statusPill(status) {
    const map = {
      approved: ["تأیید شده", "green"], pending: ["در انتظار", "amber"], rejected: ["رد شده", "red"],
      suspended: ["معلق", "violet"], open: ["باز", "blue"], closed: ["بسته", "gray"]
    };
    const m = map[status] || [status || "-", "gray"];
    return '<span class="pill ' + m[1] + '">' + esc(m[0]) + "</span>";
  }
  function emptyState(iconName, title, text, action) {
    return '<div class="empty"><div class="empty-icon">' + icon(iconName || "info") + "</div><b>" + esc(title || "موردی یافت نشد") +
      "</b><span>" + esc(text || "") + "</span>" + (action || "") + "</div>";
  }
  function loadingRow(text) {
    return '<div class="loading-row"><span class="spinner"></span><span>' + esc(text || "در حال دریافت اطلاعات…") + "</span></div>";
  }
  function skeletonRows(n) {
    let out = "";
    for (let i = 0; i < (n || 4); i++) out += '<div class="list-item"><div class="skeleton" style="width:38px;height:38px;border-radius:10px"></div><div class="flex-1"><div class="skeleton sk-line w60"></div><div class="skeleton sk-line w40" style="margin-bottom:0"></div></div></div>';
    return out;
  }

  function fmtDuration(seconds) {
    const s = Math.max(0, Math.floor(Number(seconds) || 0));
    if (s < 60) return toFa(s) + " ثانیه";
    const m = Math.floor(s / 60), r = s % 60;
    return toFa(m) + " دقیقه" + (r ? " و " + toFa(r) + " ثانیه" : "");
  }

  function daysLabel(days) {
    const d = Math.max(0, Math.floor(Number(days) || 0));
    if (!d) return "بدون اشتراک فعال";
    if (d === 1) return "۱ روز باقی‌مانده";
    return toFa(d) + " روز باقی‌مانده";
  }

  function planStatus(sub) {
    const s = sub || {};
    if (s.unlimited) return { active: true, label: "دسترسی نامحدود مدیر", days: 0, until: "-" };
    const active = !!s.active;
    return { active: active, label: active ? daysLabel(s.days_left) : "اشتراک فعال نیست", days: s.days_left || 0, until: s.until_jalali || "-" };
  }

  function safeImage(url) {
    const u = String(url || "").trim();
    if (!u) return "";
    if (u.charAt(0) === "/" || /^https?:\/\//i.test(u)) return u;
    return "";
  }

  /* ---------- shell ---------- */
  function bindTheme() {
    $$("#themeBtn").forEach(function (btn) {
      btn.addEventListener("click", toggleTheme);
    });
    syncThemeIcon();
  }

  function bindMobileNav() {
    const btn = $("#menuBtn"), side = $("#sidebar"), scrim = $("#scrim");
    if (!btn || !side) return;
    function close() { side.classList.remove("open"); if (scrim) scrim.classList.remove("on"); }
    btn.addEventListener("click", function () {
      side.classList.toggle("open");
      if (scrim) scrim.classList.toggle("on", side.classList.contains("open"));
    });
    if (scrim) scrim.addEventListener("click", close);
    $$(".nav-link", side).forEach(function (a) { a.addEventListener("click", close); });
  }

  function bindLogout() {
    $$("#sideLogout").forEach(function (btn) { btn.addEventListener("click", logout); });
  }

  function fillUser(user) {
    if (!user) return;
    const name = user.name || "کاربر آبان";
    const sub = user.is_admin ? "مدیر اکوسیستم" : (user.username ? "@" + user.username : (user.phone || "کاربر آبان ترید"));
    const targets = [
      ["#sideAvatar", initials(name)], ["#topAvatar", initials(name)],
      ["#sideUserName", name], ["#topUserName", name],
      ["#sideUserSub", sub], ["#topUserSub", sub]
    ];
    targets.forEach(function (pair) {
      const el = $(pair[0]);
      if (el) el.textContent = pair[1];
    });
    const adminLink = $("#adminLink");
    if (adminLink && user.is_admin) adminLink.hidden = false;
    const bot = $("#botLink");
    if (bot && user.bot_url) bot.href = user.bot_url;
    const sideSubText = $("#sideSubText");
    const sideBtn = $("#sideSubBtn");
    const sideBar = $("#sideSubBar");
    const s = planStatus(user.subscription);
    if (sideSubText) {
      if (user.is_admin) sideSubText.textContent = "دسترسی کامل مدیریتی به تمام بخش‌ها.";
      else if (s.active) sideSubText.textContent = s.label + " — پایان: " + s.until;
      else sideSubText.textContent = "برای مشاهده سیگنال‌های لحظه‌ای، اشتراک خود را فعال کنید.";
    }
    if (sideBtn) {
      sideBtn.textContent = s.active ? "تمدید اشتراک" : "خرید اشتراک";
      sideBtn.href = "/subscribe";
    }
    if (sideBar) {
      const pct = user.is_admin ? 100 : Math.min(100, Math.round((s.days / 30) * 100));
      sideBar.style.width = pct + "%";
      sideBar.parentElement.className = "progress " + (s.active ? "green" : "amber");
    }
  }

  function setupShell() {
    bindTheme();
    bindMobileNav();
    bindLogout();
  }

  function setPage(title, sub) {
    const t = $("#pageTitle"), s = $("#pageSub");
    if (t) t.textContent = title || "";
    if (s) s.textContent = sub || "";
    const site = document.documentElement.getAttribute("data-site-title") || "آبان ترید";
    document.title = (title ? title + " | " : "") + site;
  }

  function markNav(name) {
    $$(".nav-link[data-nav]").forEach(function (a) {
      a.classList.toggle("active", a.getAttribute("data-nav") === name);
    });
  }

  function go(path, replace) {
    if (replace) history.replaceState({}, "", path);
    else history.pushState({}, "", path);
    window.dispatchEvent(new PopStateEvent("popstate"));
  }

  /* global links (SPA) */
  document.addEventListener("click", function (e) {
    const a = e.target.closest("a[data-link]");
    if (!a) return;
    if (a.target === "_blank" || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    const href = a.getAttribute("href") || "";
    if (!href || href.charAt(0) !== "/") return;
    e.preventDefault();
    if (href === location.pathname + location.search) return;
    go(href);
    window.scrollTo({ top: 0, behavior: "smooth" });
  });

  /* ---------- برندینگ عمومی و حالت تعمیر ---------- */
  const brand = { name: "آبان ترید", title: "آبان ترید", login: {} };
  let maintenanceShown = false;

  function applyBrandText(key, value) {
    if (!value) return;
    $$('[data-brand="' + key + '"]').forEach(function (el) { el.textContent = value; });
  }

  function maintText(text) {
    return text || "پلتفرم در حال بروزرسانی است؛ لطفاً در ساعات آینده وارد شوید.";
  }

  function showMaintenance(text) {
    let bar = document.querySelector(".maintenance-bar");
    if (!bar) {
      bar = document.createElement("div");
      bar.className = "maintenance-bar";
      bar.setAttribute("role", "status");
      document.body.insertBefore(bar, document.body.firstChild);
      document.body.classList.add("has-maintenance");
    }
    bar.innerHTML = icon("alert") + "<span>" + esc(maintText(text)) + "</span>";
    maintenanceShown = true;
  }

  function hideMaintenance() {
    const bar = document.querySelector(".maintenance-bar");
    if (bar) bar.remove();
    document.body.classList.remove("has-maintenance");
    maintenanceShown = false;
  }

  async function applyPublicConfig() {
    let cfg;
    try {
      cfg = await api("/api/public/config", { redirectOn401: false });
    } catch (e) {
      return;
    }
    if (!cfg) return;
    brand.name = (cfg.site_name || brand.name).trim() || "آبان ترید";
    brand.title = (cfg.site_title || brand.name).trim() || brand.name;
    document.documentElement.setAttribute("data-site-title", brand.title);
    applyBrandText("name", brand.name);
    applyBrandText("slogan", cfg.site_slogan || "");
    if (cfg.logo) {
      $$(".brand-mark").forEach(function (el) {
        el.innerHTML = '<img src="' + esc(cfg.logo) + '" alt="' + esc(brand.name) + '" loading="eager">';
      });
    }
    const login = cfg.login || {};
    brand.login = login;
    applyBrandText("login-title", login.title || "");
    applyBrandText("login-subtitle", login.subtitle || "");
    applyBrandText("login-heading", login.heading || "");
    applyBrandText("login-note", login.note || "");
    applyBrandText("login-button", login.button || "");
    applyBrandText("login-footer", login.footer || "");
    $$("#botLink").forEach(function (a) { if (cfg.bot_url) a.href = cfg.bot_url; });
    const title = $("#pageTitle");
    document.title = (title && title.textContent ? title.textContent + " | " : "") + brand.title;
    if (cfg.maintenance && cfg.maintenance.on) showMaintenance(cfg.maintenance.text);
    else hideMaintenance();
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", applyPublicConfig);
  else applyPublicConfig();

  window.Aban = {
    $: $, $$: $$, esc: esc, icon: icon, toFa: toFa, toEn: toEn, parseNum: parseNum,
    fmtNum: fmtNum, fmtPrice: fmtPrice, fmtCompact: fmtCompact, fmtBytes: fmtBytes, fmtPct: fmtPct, pctClass: pctClass,
    fmtDate: fmtDate, fmtDateShort: fmtDateShort, fmtTime: fmtTime, fmtDateTime: fmtDateTime, fmtWeekday: fmtWeekday,
    timeAgo: timeAgo, initials: initials, coinClass: coinClass, coinLetters: coinLetters,
    theme: { current: currentTheme, set: setTheme, toggle: toggleTheme, sync: syncThemeIcon },
    toast: toast, api: api, logout: logout, openModal: openModal, closeModal: closeModal,
    confirm: confirmDialog, prompt: promptDialog, copy: copyText, debounce: debounce, setBusy: setBusy,
    sparkline: sparkline, gauge: gaugeSvg, donut: donutSvg,
    pill: pill, signalPill: signalPill, directionPill: directionPill, statusPill: statusPill,
    emptyState: emptyState, loadingRow: loadingRow, skeletonRows: skeletonRows,
    fmtDuration: fmtDuration, daysLabel: daysLabel, planStatus: planStatus, safeImage: safeImage,
    setupShell: setupShell, fillUser: fillUser, setPage: setPage, markNav: markNav, go: go,
    isAdminPage: isAdminPage, applyPublicConfig: applyPublicConfig, brand: brand
  };
})(window, document);
