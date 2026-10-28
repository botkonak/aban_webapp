/* آبان ترید — برنامه اصلی وب‌اپ */
(function () {
  "use strict";
  const A = window.Aban;

  const TF_LABEL = {
    "5m": "۵ دقیقه", "15m": "۱۵ دقیقه", "30m": "۳۰ دقیقه", "1h": "۱ ساعت",
    "4h": "۴ ساعت", "6h": "۶ ساعت", "12h": "۱۲ ساعت", "1d": "۱ روز", "1w": "۱ هفته"
  };
  const TF_MINUTES = { "5m": 5, "15m": 15, "30m": 30, "1h": 60, "4h": 240, "6h": 360, "12h": 720, "1d": 1440, "1w": 10080 };
  const SCANNER_TFS = ["1d", "5m", "15m", "30m", "1h", "4h", "6h", "12h", "1w"];
  const DEPTHS = [
    { value: 0, label: "لحظه‌ای" }, { value: 10, label: "۱۰ کندل" }, { value: 15, label: "۱۵ کندل" },
    { value: 30, label: "۳۰ کندل" }, { value: 40, label: "۴۰ کندل" }, { value: 60, label: "۶۰ کندل" }
  ];
  const SYMBOL_MODES = [{ value: "all", label: "همه نمادها" }, { value: "favorites", label: "علاقه‌مندی‌ها" }, { value: "single", label: "تک نماد" }];
  const state = { user: null, strategies: null, settings: null, unlocked: false };
  let tickerTimer = null;
  let cleanup = null;

  function setCleanup(fn) {
    if (cleanup) { try { cleanup(); } catch (e) {} }
    cleanup = fn || null;
  }
  function viewEl() { return A.$("#view"); }
  function setHTML(html) { viewEl().innerHTML = html; }
  function tfLabel(tf) { return TF_LABEL[tf] || tf; }
  function greeting() {
    const h = new Date().getHours();
    if (h < 12) return "صبح بخیر";
    if (h < 17) return "وقت بخیر";
    if (h < 21) return "عصر بخیر";
    return "شب بخیر";
  }
  function botLinkFor(user) {
    const u = user || state.user || {};
    return u.bot_url || "#";
  }
  function openSymbol(symbol, tf) {
    const params = [];
    if (tf) params.push("tf=" + encodeURIComponent(tf));
    if (readFocus(symbol)) params.push("focus=1");
    A.go("/symbol/" + encodeURIComponent(symbol) + (params.length ? "?" + params.join("&") : ""));
  }
  function storeFocus(row) {
    try { sessionStorage.setItem("aban:focus-signal", JSON.stringify(Object.assign({ _at: Date.now() }, row))); } catch (e) {}
  }
  function readFocus(symbol) {
    try {
      const raw = sessionStorage.getItem("aban:focus-signal");
      if (!raw) return null;
      const obj = JSON.parse(raw);
      if (!obj || Date.now() - (obj._at || 0) > 120000) return null;
      if (symbol && String(obj.symbol || "").toUpperCase() !== String(symbol).toUpperCase()) return null;
      return obj;
    } catch (e) { return null; }
  }
  function clearFocus() { try { sessionStorage.removeItem("aban:focus-signal"); } catch (e) {} }

  function pagerHTML(page, totalPages) {
    const p = Math.max(1, page || 1);
    const t = Math.max(1, totalPages || 1);
    if (t <= 1) return "";
    let btns = "";
    const start = Math.max(1, Math.min(p - 2, t - 4));
    const end = Math.min(t, Math.max(p + 2, 5));
    if (start > 1) btns += '<button data-page="1">۱</button>' + (start > 2 ? '<span class="tiny muted">…</span>' : "");
    for (let i = start; i <= end; i++) btns += '<button data-page="' + i + '" class="' + (i === p ? "active" : "") + '">' + A.toFa(i) + "</button>";
    if (end < t) btns += (end < t - 1 ? '<span class="tiny muted">…</span>' : "") + '<button data-page="' + t + '">' + A.toFa(t) + "</button>";
    return '<div class="pager">' +
      '<button data-page="' + (p - 1) + '" aria-label="صفحه قبل" ' + (p <= 1 ? "disabled" : "") + '>' + A.icon("chevron-right") + "</button>" +
      btns +
      '<button data-page="' + (p + 1) + '" aria-label="صفحه بعد" ' + (p >= t ? "disabled" : "") + '>' + A.icon("chevron-left") + "</button></div>";
  }
  function bindPager(root, cb) {
    if (!root) return;
    root.querySelectorAll("[data-page]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        const n = parseInt(btn.getAttribute("data-page"), 10);
        if (numValid(n)) cb(n);
      });
    });
  }
  function numValid(n) { return Number.isFinite(n) && n > 0; }

  function skeletonPage() {
    return '<div class="hello"><div><div class="skeleton sk-line w40" style="height:24px;width:220px"></div><div class="skeleton sk-line" style="width:300px"></div></div></div>' +
      '<div class="kpi-grid">' + Array(4).fill('<div class="skeleton sk-card"></div>').join("") + "</div>" +
      '<div class="grid grid-main"><div class="skeleton" style="height:380px;border-radius:18px"></div><div class="skeleton" style="height:380px;border-radius:18px"></div></div>' +
      '<div class="grid grid-low mt-3"><div class="skeleton" style="height:300px;border-radius:18px"></div><div class="skeleton" style="height:300px;border-radius:18px"></div></div>';
  }

  function errorCard(err, retryId) {
    return '<div class="card pad"><div class="empty"><div class="empty-icon">' + A.icon("alert") + "</div><b>دریافت اطلاعات انجام نشد</b><span>" +
      A.esc(err && err.message ? err.message : "خطای غیرمنتظره رخ داد.") + "</span>" +
      (retryId ? '<button class="btn btn-soft" id="' + retryId + '">' + A.icon("refresh") + " تلاش دوباره</button>" : "") + "</div></div>";
  }

  function kpiCard(o) {
    return '<div class="kpi">' +
      '<div class="kpi-top"><div><div class="kpi-label">' + A.esc(o.label) + "</div>" +
      '<div class="kpi-value">' + o.value + (o.unit ? "<small>" + A.esc(o.unit) + "</small>" : "") + "</div></div>" +
      '<div class="kpi-icon ' + (o.tone || "") + '">' + A.icon(o.icon || "activity") + "</div></div>" +
      '<div class="kpi-foot"><span class="kpi-delta ' + (o.tone2 || "flat") + '">' + (o.footIcon ? A.icon(o.footIcon) : "") + A.esc(o.foot || "") + "</span>" +
      '<div class="kpi-spark">' + (o.spark || "") + "</div></div></div>";
  }

  function fearSvg(items) {
    const list = (items || []).filter(function (x) { return x && Number(x.value) >= 0; });
    if (list.length < 2) {
      return '<div class="empty" style="padding:26px"><div class="empty-icon">' + A.icon("activity") + "</div><b>داده کافی برای نمودار نیست</b><span>پس از ثبت داده‌های شاخص، روند اینجا نمایش داده می‌شود.</span></div>";
    }
    const W = 640, H = 210, L = 34, R = 12, T = 14, B = 26;
    const t0 = list[0].time || 0, t1 = list[list.length - 1].time || 0;
    const spanT = t1 - t0 || 1;
    const pts = list.map(function (it) {
      const x = L + ((it.time || 0) - t0) / spanT * (W - L - R);
      const y = T + (100 - Math.max(0, Math.min(100, it.value))) / 100 * (H - T - B);
      return { x: x, y: y, v: it.value, t: it.time };
    });
    let d = "";
    pts.forEach(function (p, i) {
      if (!i) { d = "M" + p.x.toFixed(1) + " " + p.y.toFixed(1); return; }
      const q = pts[i - 1];
      const cx = (q.x + p.x) / 2;
      d += " C" + cx.toFixed(1) + " " + q.y.toFixed(1) + " " + cx.toFixed(1) + " " + p.y.toFixed(1) + " " + p.x.toFixed(1) + " " + p.y.toFixed(1);
    });
    const area = d + " L" + pts[pts.length - 1].x.toFixed(1) + " " + (H - B) + " L" + pts[0].x.toFixed(1) + " " + (H - B) + " Z";
    let grid = "", labels = "";
    [25, 50, 75].forEach(function (v) {
      const y = T + (100 - v) / 100 * (H - T - B);
      grid += '<line x1="' + L + '" y1="' + y.toFixed(1) + '" x2="' + (W - R) + '" y2="' + y.toFixed(1) + '" stroke="var(--chart-grid)" stroke-dasharray="3 4"/>';
      labels += '<text x="' + (L - 8) + '" y="' + (y + 3).toFixed(1) + '" text-anchor="end" fill="var(--muted-2)" font-size="9">' + A.toFa(v) + "</text>";
    });
    const dots = pts.map(function (p) {
      return '<circle cx="' + p.x.toFixed(1) + '" cy="' + p.y.toFixed(1) + '" r="2" fill="var(--primary)"><title>' + A.esc(A.fmtDate(p.t * 1000) + " — " + A.toFa(p.v)) + "</title></circle>";
    }).join("");
    const picks = [pts[0], pts[Math.floor(pts.length / 2)], pts[pts.length - 1]];
    const xlabels = picks.map(function (p, i) {
      const anchor = i === 0 ? "start" : (i === picks.length - 1 ? "end" : "middle");
      return '<text x="' + p.x.toFixed(1) + '" y="' + (H - 6) + '" text-anchor="' + anchor + '" fill="var(--muted-2)" font-size="9">' + A.esc(A.fmtDateShort(p.t * 1000)) + "</text>";
    }).join("");
    return '<svg viewBox="0 0 ' + W + " " + H + '" preserveAspectRatio="none" style="width:100%;height:100%" role="img" aria-label="روند شاخص ترس و طمع">' +
      '<defs><linearGradient id="fearGrad" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="var(--primary)" stop-opacity=".24"/><stop offset="1" stop-color="var(--primary)" stop-opacity="0"/></linearGradient></defs>' +
      grid + labels +
      '<path d="' + area + '" fill="url(#fearGrad)"/>' +
      '<path d="' + d + '" fill="none" stroke="var(--primary)" stroke-width="2.2" stroke-linecap="round" vector-effect="non-scaling-stroke"/>' +
      dots + xlabels + "</svg>";
  }

  /* ================================================================
     داشبورد
     ================================================================ */
  async function viewDashboard() {
    A.setPage("داشبورد", "نمای کلی بازار، تحلیل‌ها و اشتراک شما");
    A.markNav("dashboard");
    setHTML(skeletonPage());
    let data, market;
    try {
      const result = await Promise.all([
        A.api("/api/dashboard"),
        A.api("/api/market/status").catch(function () { return null; })
      ]);
      data = result[0];
      market = result[1];
    }
    catch (err) {
      setHTML(errorCard(err, "dashRetry"));
      const b = A.$("#dashRetry"); if (b) b.addEventListener("click", viewDashboard);
      return;
    }
    state.user = data.user || state.user;
    A.fillUser(state.user);
    const user = data.user || {};
    const stats = data.stats || {};
    const sub = A.planStatus(user.subscription);
    const fng = data.fear_greed || { value: 50, label: "خنثی" };
    const mkt = market || {};
    const mix = data.mix || { support: 0, resistance: 0 };
    const mixTotal = (mix.support || 0) + (mix.resistance || 0);
    const week = stats.signals_week || [0, 0, 0, 0, 0, 0, 0];
    const weekTotal = week.reduce(function (s, n) { return s + Number(n || 0); }, 0);
    const latest = data.latest || [];
    const searches = data.recent_searches || [];
    const premium = data.premium !== undefined ? !!data.premium : (!!user.is_admin || sub.active);
    const premiumGate = '<div class="empty"><div class="empty-icon">' + A.icon("lock") + "</div>" +
      "<b>برای مشاهده سیگنال‌های لحظه‌ای، اشتراک خود را تمدید کنید</b>" +
      "<span>با فعال‌سازی اشتراک تحلیل ساختاری، آخرین سیگنال‌ها و جدول تحلیل‌ها در دسترس شما قرار می‌گیرد.</span>" +
      '<a class="btn btn-primary" href="/subscribe" data-link>' + A.icon("wallet") + " خرید / تمدید اشتراک</a></div>";
    const fngTone = fng.value >= 55 ? "green" : (fng.value <= 45 ? "red" : "amber");

    const mktState = mkt.available ? (mkt.state || "neutral") : "";
    const mktTone = mktState === "bull" ? "green" : (mktState === "bear" ? "red" : (mktState ? "amber" : "gray"));
    const mktDesc = mktState === "bull"
      ? "میانگین RSI بالای ۵۵ و بیش از ۶۰٪ نمادها در جهت صعودی هستند؛ مومنتوم بازار مثبت است."
      : mktState === "bear"
        ? "میانگین RSI زیر ۴۵ و بیش از ۶۰٪ نمادها در جهت نزولی هستند؛ فشار فروش غالب است."
        : "شرایط بازار یکدست نیست و سیگنال‌های صعودی و نزولی هم‌زمان دیده می‌شوند.";
    const n = function (v) { return A.toFa(Math.round(Number(v || 0))); };
    const marketCard =
      '<div class="card mt-3"><div class="card-head bordered"><div><div class="card-title">' + A.icon("compass") + ' وضعیت کلی بازار</div>' +
        '<div class="card-sub">محاسبه‌شده از کندل‌های نمادهای فعال، مشابه گزارش ربات</div></div>' +
        '<div class="card-actions">' +
          (mkt.available ? '<span class="pill ' + mktTone + '">' + A.esc(mkt.state_fa || "خنثی") + "</span>" : '<span class="pill gray">در انتظار داده</span>') +
          (mkt.available && !mkt.fresh ? '<span class="pill amber">داده قدیمی</span>' : "") +
        "</div></div>" +
        '<div class="card-body">' +
          (mkt.available
            ? '<div class="market-mini">' +
                '<div class="mini-stat"><span>میانگین RSI</span><b>' + n(mkt.rsi) + "</b></div>" +
                '<div class="mini-stat"><span>میانگین ADX</span><b>' + n(mkt.adx) + "</b></div>" +
                '<div class="mini-stat"><span>نمادهای صعودی</span><b class="up">' + n(mkt.bull) + "٪</b></div>" +
                '<div class="mini-stat"><span>نمادهای نزولی</span><b class="down">' + n(mkt.bear) + "٪</b></div>" +
              "</div>" +
              '<div class="row-between mt-2"><span class="tiny muted">نمادهای بررسی‌شده: ' + n(mkt.count) +
                " • تایم‌فریم: " + A.esc(tfLabel(mkt.timeframe)) + "</span>" +
                '<span class="tiny muted">' + (mkt.at_fa ? "آخرین محاسبه: " + A.esc(mkt.at_fa) : "") + "</span></div>" +
              '<div class="hint mt-1">' + A.esc(mktDesc) + "</div>"
            : A.emptyState("compass", "وضعیت بازار هنوز آماده نیست", "پس از اجرای موتور و جمع‌آوری کندل‌ها، شاخص‌های بازار در این بخش نمایش داده می‌شوند.")) +
        "</div></div>";

    const subValue = user.is_admin ? "نامحدود" : (sub.active ? A.toFa(sub.days) : "—");
    const subFoot = user.is_admin ? "دسترسی مدیر" : (sub.active ? "روز اعتبار" : "اشتراک غیرفعال");

    let latestRows = "";
    latest.slice(0, 6).forEach(function (s) {
      latestRows += '<tr class="clickable" data-open-symbol="' + A.esc(s.symbol) + '" data-tf="' + A.esc(s.timeframe) + '">' +
        '<td><div class="row"><span class="coin' + A.coinClass(s.symbol) + '" style="width:32px;height:32px;font-size:.62rem">' + A.esc(A.coinLetters(s.symbol)) + "</span>" +
        '<div><div class="cell-main">' + A.esc(s.symbol) + '</div><div class="cell-sub">' + A.esc(s.exchange || "") + "</div></div></div></td>" +
        "<td>" + A.signalPill(s.signal_type) + "</td>" +
        '<td class="result-strategy"><div class="cell-main">' + A.esc(s.strategy || "-") + "</div></td>" +
        "<td>" + A.esc(tfLabel(s.timeframe)) + "</td>" +
        '<td><span class="pill blue">' + A.esc(A.fmtPrice(s.price)) + "</span></td>" +
        '<td class="tiny muted nowrap">' + A.esc(s.date || "-") + "</td>" +
        '<td><div class="cell-actions"><button class="icon-btn sm" data-focus="' + A.esc(s.symbol) + '" data-tf="' + A.esc(s.timeframe) + '" title="نمایش روی نمودار">' + A.icon("chart") + "</button></div></td>" +
        "</tr>";
    });

    let searchRows = "";
    searches.forEach(function (h) {
      searchRows += '<div class="list-item">' +
        '<div class="list-icon blue">' + A.icon("search") + "</div>" +
        '<div class="list-body"><div class="list-title">اسکن ' + A.esc(tfLabel(h.timeframe)) + '</div>' +
        '<div class="list-sub">' + A.toFa(h.strategy_count || 0) + " استراتژی • " + A.esc(h.created_at || "") + "</div></div>" +
        '<div class="list-side"><span class="pill ' + (h.result_count ? "green" : "gray") + '">' + A.toFa(h.result_count || 0) + " نتیجه</span>" +
        '<button class="icon-btn sm" data-rerun="' + A.esc(h.id) + '" title="اجرای مجدد">' + A.icon("refresh") + "</button></div></div>";
    });

    const donut = mixTotal > 0
      ? A.donut([{ value: mix.support, color: "var(--green)" }, { value: mix.resistance, color: "var(--red)" }], A.toFa(mixTotal), "سیگنال فعال")
      : A.donut([{ value: 1, color: "var(--surface-3)" }], "۰", "بدون سیگنال");

    setHTML(
      '<div class="hello">' +
        "<div><div class=\"eyebrow\">" + A.icon("sparkles") + " نمای امروز</div>" +
        "<h1>" + greeting() + "، " + A.esc(user.name || "کاربر آبان") + "</h1>" +
        "<p>وضعیت تحلیل‌ها، شاخص بازار و آخرین سیگنال‌های ساختاری شما.</p></div>" +
        '<div class="hello-actions">' +
          '<span class="date-chip">' + A.icon("calendar") + A.esc(A.fmtWeekday(Date.now())) + "</span>" +
          '<a class="btn btn-ghost" href="/search" data-link>' + A.icon("search") + " جستجوی نماد</a>" +
          '<a class="btn btn-primary" href="/scanner" data-link>' + A.icon("scanner") + " شروع اسکن</a>" +
        "</div>" +
      "</div>" +

      '<div class="kpi-grid">' +
        kpiCard({ label: "سیگنال امروز", value: A.toFa(stats.signals_today || 0), unit: "سیگنال", icon: "activity", tone: "green",
          foot: weekTotal ? "۷ روز: " + A.toFa(weekTotal) : "در انتظار بازار", tone2: weekTotal ? "up" : "flat", footIcon: "trend-up", spark: A.sparkline(week, { color: "var(--green)" }) }) +
        kpiCard({ label: "استراتژی فعال", value: A.toFa(stats.strategies || 0), unit: "استراتژی", icon: "layers", tone: "violet",
          foot: A.toFa(stats.categories || 0) + " دسته‌بندی", tone2: "flat", footIcon: "grid" }) +
        kpiCard({ label: "جستجوی ۲۴ ساعت", value: A.toFa(stats.searches_24h || 0), unit: "جستجو", icon: "search", tone: "cyan",
          foot: "جستجوهای شما", tone2: "flat", footIcon: "history" }) +
        kpiCard({ label: "اشتراک تحلیل ساختاری", value: subValue, unit: user.is_admin ? "" : "روز", icon: "sparkles", tone: sub.active || user.is_admin ? "green" : "amber",
          foot: subFoot, tone2: sub.active || user.is_admin ? "up" : "down", footIcon: sub.active || user.is_admin ? "check" : "alert" }) +
      "</div>" +

      marketCard +

      '<div class="grid grid-main">' +
        '<div class="card">' +
          '<div class="card-head bordered"><div><div class="card-title">' + A.icon("activity") + ' روند شاخص ترس و طمع</div>' +
          '<div class="card-sub">سنجش احساسات بازار در ۳۰ روز گذشته</div></div>' +
          '<div class="card-actions"><span class="pill ' + fngTone + '">' + A.icon("compass") + A.esc(fng.label || "خنثی") + "</span></div></div>" +
          '<div class="card-body tight">' +
            '<div class="fng-line">' +
              '<div class="fng-meta"><span class="tiny muted">شاخص فعلی</span><div class="fng-value">' + A.toFa(fng.value || 0) + "</div>" +
              '<div class="fng-bar"><span class="fng-pin" style="inset-inline-start:' + Math.max(0, Math.min(100, fng.value || 0)) + '%"></span></div>' +
              '<div class="fng-scale"><span>ترس شدید</span><span>خنثی</span><span>طمع شدید</span></div></div>' +
              A.gauge(fng.value || 0, fng.label || "خنثی") +
            "</div>" +
            '<div class="chart-box small" style="margin-top:10px">' + fearSvg(data.fear_history) + "</div>" +
          "</div>" +
          '<div class="card-foot"><span class="status-line"><span class="dot green"></span> به‌روزرسانی ساعتی از Alternative.me</span>' +
          '<a class="card-link" href="/scanner" data-link>تحلیل ساختاری ' + A.icon("chevron-left") + "</a></div>" +
        "</div>" +

        '<div class="card">' +
          '<div class="card-head bordered"><div><div class="card-title">' + A.icon("layers") + ' ترکیب سیگنال‌ها</div>' +
          '<div class="card-sub">تفکیک سیگنال‌های حمایت و مقاومت</div></div></div>' +
          '<div class="card-body">' + donut +
            '<div class="donut-legend" style="margin-top:16px">' +
              '<div class="donut-item"><i style="background:var(--green)"></i> حمایت <b>' + A.toFa(mix.support || 0) + "</b></div>" +
              '<div class="donut-item"><i style="background:var(--red)"></i> مقاومت <b>' + A.toFa(mix.resistance || 0) + "</b></div>" +
              '<div class="donut-item"><i style="background:var(--primary)"></i> کل سیگنال فعال <b>' + A.toFa(mixTotal) + "</b></div>" +
            "</div>" +
            '<div class="divider"></div>' +
            '<div class="row-between"><div><div class="tiny muted">اشتراک شما</div><div class="strong" style="font-size:.86rem;margin-top:2px">' +
              (user.is_admin ? "دسترسی نامحدود مدیر" : (sub.active ? sub.label : "اشتراک فعال نیست")) + "</div></div>" +
              (user.is_admin ? '<span class="pill violet">مدیر</span>' :
              '<a class="btn btn-sm ' + (sub.active ? "btn-ghost" : "btn-primary") + '" href="/subscribe" data-link>' + (sub.active ? "تمدید" : "خرید اشتراک") + "</a>") +
            "</div>" +
          "</div>" +
          '<div class="card-foot">' + (sub.active && !user.is_admin ? "<span>پایان: " + A.esc(sub.until) + "</span>" : "<span>برای دریافت سیگنال لحظه‌ای، اشتراک را فعال کنید.</span>") + "</div>" +
        "</div>" +
      "</div>" +

      '<div class="grid grid-low mt-3">' +
        '<div class="card">' +
          '<div class="card-head"><div><div class="card-title">' + A.icon("chart") + ' آخرین سیگنال‌های ساختاری</div>' +
          '<div class="card-sub">سیگنال‌های ثبت‌شده در تمام نمادها</div></div>' +
          '<div class="card-actions"><a class="card-link" href="/scanner" data-link>مشاهده همه ' + A.icon("chevron-left") + "</a></div></div>" +
          '<div class="card-body tight">' +
            (!premium ? premiumGate : (latest.length ?
              '<div class="table-wrap"><table class="table table-fix"><thead><tr><th>نماد</th><th>نوع</th><th>استراتژی</th><th>تایم‌فریم</th><th>قیمت</th><th>تاریخ</th><th></th></tr></thead><tbody>' + latestRows + "</tbody></table></div>"
              : A.emptyState("sparkles", "هنوز سیگنالی ثبت نشده", "برای دریافت سیگنال، یک اسکن ساختاری اجرا کنید.", '<a class="btn btn-primary btn-sm" href="/scanner" data-link>شروع اسکن</a>'))) +
          "</div>" +
        "</div>" +

        '<div class="stack">' +
          '<div class="card">' +
            '<div class="card-head"><div><div class="card-title">' + A.icon("history") + ' جستجوهای اخیر</div></div>' +
            '<div class="card-actions"><a class="card-link" href="/history" data-link>همه ' + A.icon("chevron-left") + "</a></div></div>" +
            '<div class="card-body tight">' + (searches.length ? '<div class="list">' + searchRows + "</div>" : A.emptyState("history", "تاریخچه خالی است", "جستجوهای شما پس از اجرای اسکن اینجا ذخیره می‌شوند.")) + "</div>" +
          "</div>" +
          '<div class="card">' +
            '<div class="card-head"><div><div class="card-title">' + A.icon("sparkles") + ' مسیر پیشنهادی</div></div></div>' +
            '<div class="card-body tight">' +
              '<div class="list">' +
                '<div class="list-item"><div class="list-icon green">' + A.icon("scanner") + '</div><div class="list-body"><div class="list-title">یک اسکن سریع اجرا کنید</div><div class="list-sub">تایم‌فریم ۱ ساعت و ۱۰ کندل اخیر</div></div><a class="icon-btn sm" href="/scanner" data-link>' + A.icon("chevron-left") + "</a></div>" +
                '<div class="list-item"><div class="list-icon violet">' + A.icon("search") + '</div><div class="list-body"><div class="list-title">یک نماد را دقیق بررسی کنید</div><div class="list-sub">نمودار، سه ماه تغییرات و تحلیل چند تایم‌فریمی</div></div><a class="icon-btn sm" href="/search" data-link>' + A.icon("chevron-left") + "</a></div>" +
              "</div>" +
            "</div>" +
          "</div>" +
        "</div>" +
      "</div>"
    );

    bindDashboard();
    setCleanup(null);
  }

  function bindDashboard() {
    viewEl().querySelectorAll("[data-open-symbol]").forEach(function (tr) {
      tr.addEventListener("click", function (e) {
        if (e.target.closest("[data-focus]")) return;
        openSymbol(tr.getAttribute("data-open-symbol"), tr.getAttribute("data-tf"));
      });
    });
    viewEl().querySelectorAll("[data-focus]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        openSymbol(btn.getAttribute("data-focus"), btn.getAttribute("data-tf"));
      });
    });
    viewEl().querySelectorAll("[data-rerun]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        const id = A.toEn(btn.getAttribute("data-rerun") || "").replace(/[^0-9]/g, "");
        if (id) A.go("/scanner?rerun=" + encodeURIComponent(id));
      });
    });
  }

  /* ================================================================
     اسکنر
     ================================================================ */
  async function viewScanner() {
    A.setPage("تکنیکال پیشرفته", "اسکن ساختاری نمادها با انتخاب استراتژی و صرافی");
    A.markNav("scanner");
    setHTML('<div class="card pad"><div class="loading-row"><span class="spinner"></span><span>در حال آماده‌سازی استراتژی‌ها…</span></div></div>');
    let strategies, settingsData;
    try {
      const result = await Promise.all([A.api("/api/strategies"), A.api("/api/settings")]);
      strategies = result[0]; settingsData = result[1];
    } catch (err) {
      setHTML(errorCard(err, "scanRetry"));
      const b = A.$("#scanRetry"); if (b) b.addEventListener("click", viewScanner);
      return;
    }
    state.strategies = strategies; state.settings = settingsData;
    state.user = settingsData.user || state.user;
    A.fillUser(state.user);

    const groups = (strategies.groups || []).map(function (g) {
      return { category: g.category, count: g.count, strategies: g.strategies || [] };
    });
    const allKeys = new Set();
    const keyMap = new Map();
    groups.forEach(function (g) { g.strategies.forEach(function (s) { allKeys.add(s.key); keyMap.set(s.key, s); }); });
    const saved = (settingsData.settings && settingsData.settings.scan_strategy_keys) || [];
    let selected = new Set(saved.filter(function (k) { return allKeys.has(k); }));
    const favStrategies = new Set(strategies.favorites || []);
    if (!selected.size && favStrategies.size) {
      favStrategies.forEach(function (k) { if (allKeys.has(k)) selected.add(k); });
    }
    const favSymbolList = (settingsData.favorites && settingsData.favorites.symbol) || [];
    let initialSymbolsMode = (settingsData.settings && settingsData.settings.scan_symbols_mode) || "all";
    if ((initialSymbolsMode === "favorites" && !favSymbolList.length) ||
        (initialSymbolsMode === "single" && !(settingsData.settings && settingsData.settings.scan_symbol))) {
      initialSymbolsMode = "all";
    }
    const sc = {
      groups: groups, keyMap: keyMap, favorites: favStrategies, selected: selected,
      expanded: new Set(groups.filter(function (g) { return g.strategies.some(function (s) { return selected.has(s.key); }); }).map(function (g) { return g.category; })),
      query: "", favOnly: false,
      timeframe: (settingsData.settings && settingsData.settings.scan_timeframe) || "1h",
      depth: (settingsData.settings && settingsData.settings.scan_depth) || 10,
      mode: (settingsData.settings && settingsData.settings.scan_mode) || "any",
      symbolsMode: initialSymbolsMode,
      symbol: (settingsData.settings && settingsData.settings.scan_symbol) || "",
      exchangesAll: !(settingsData.settings && settingsData.settings.exchanges_all === false),
      exchanges: new Set((settingsData.settings && settingsData.settings.scan_exchanges) || []),
      exchangeRows: settingsData.exchanges || [],
      symbols: favSymbolList,
      results: null, resultsPage: 1, lastFilters: null
    };
    if (!sc.expanded.size && groups.length) sc.expanded.add(groups[0].category);
    if (sc.exchangesAll) sc.exchangeRows.forEach(function (e) { sc.exchanges.add(e.slug); });

    renderScanner(sc);

    const params = new URLSearchParams(location.search);
    const rerun = params.get("rerun");
    if (rerun) {
      history.replaceState({}, "", "/scanner");
      runScan(sc, null, rerun);
    }
  }

  function renderScanner(sc) {
    const sub = A.planStatus(state.user && state.user.subscription);
    const strategiesCount = sc.selected.size;
    const timeframeButtons = SCANNER_TFS.map(function (tf) {
      return '<button class="seg ' + (sc.timeframe === tf ? "active" : "") + '" data-tf="' + tf + '">' + A.esc(tfLabel(tf)) + "</button>";
    }).join("");
    const depthButtons = DEPTHS.map(function (d) {
      return '<button class="chip ' + (Number(sc.depth) === d.value ? "active" : "") + '" data-depth="' + d.value + '">' + A.esc(d.label) + "</button>";
    }).join("");
    const modeButtons = [{ value: "any", label: "هر سیگنال" }, { value: "all", label: "همه با هم" }].map(function (m) {
      return '<button class="seg ' + (sc.mode === m.value ? "active" : "") + '" data-mode="' + m.value + '">' + A.esc(m.label) + "</button>";
    }).join("");
    const symbolButtons = SYMBOL_MODES.map(function (m) {
      const dis = m.value === "favorites" && !sc.symbols.length ? " disabled" : "";
      return '<button class="seg ' + (sc.symbolsMode === m.value ? "active" : "") + '" data-symbols="' + m.value + '"' + dis + ">" + A.esc(m.label) + "</button>";
    }).join("");
    const exchChips = sc.exchangeRows.map(function (e) {
      const active = sc.exchanges.has(e.slug);
      return '<button class="chip ' + (active ? "active" : "") + (sc.exchangesAll ? " disabled" : "") + '" data-exchange="' + A.esc(e.slug) + '">' +
        '<span class="dot ' + (active ? "green" : "muted") + '"></span>' + A.esc(e.name) + "</button>";
    }).join("");

    setHTML(
      '<div class="page-head">' +
        '<div><div class="eyebrow">' + A.icon("scanner") + ' موتور ساختاری</div><div class="page-title">تکنیکال پیشرفته</div>' +
        '<div class="page-sub">استراتژی‌ها را انتخاب کنید و بازار را در چند صرافی هم‌زمان اسکن کنید.</div></div>' +
        '<div class="page-actions"><a class="btn btn-ghost" href="/settings" data-link>' + A.icon("settings") + " تنظیمات اسکن</a>" +
        '<button class="btn btn-primary" id="runTop">' + A.icon("scanner") + " اجرای اسکن</button></div>" +
      "</div>" +

      (sub.active || (state.user && state.user.is_admin) ? "" :
        '<div class="notice warn mb-3">' + A.icon("lock") + '<div>برای مشاهده نتایج اسکن، اشتراک تحلیل ساختاری فعال لازم است. ' +
        '<a href="/subscribe" data-link>خرید اشتراک</a></div></div>') +

      '<div class="card filter-card">' +
        '<div class="filter-grid">' +
          '<div class="filter-block"><div class="filter-label">' + A.icon("clock") + " تایم‌فریم تحلیل</div>" +
            '<div class="segmented scroll" id="tfSeg">' + timeframeButtons + "</div></div>" +
          '<div class="filter-block"><div class="filter-label">' + A.icon("filter") + " عمق بررسی <span class=\"tiny\">تعداد کندل اخیر</span></div>" +
            '<div class="chips" id="depthChips">' + depthButtons + "</div></div>" +
          '<div class="filter-block"><div class="filter-label">' + A.icon("grid") + " دامنه نمادها</div>" +
            '<div class="segmented" id="symbolsSeg">' + symbolButtons + "</div>" +
            '<div class="mt-1" id="singleSymbolWrap" ' + (sc.symbolsMode !== "single" ? "hidden" : "") + '>' +
              '<div class="searchbox"><span>' + A.icon("search") + "</span>" +
              '<input id="singleSymbol" placeholder="نماد را جستجو کنید؛ مثل BTCUSDT" value="' + A.esc(sc.symbol) + '" autocomplete="off">' +
              "</div><div class=\"search-drop\" id=\"singleSymbolDrop\" hidden></div></div>" +
            '<div class="hint mt-1">' + A.toFa(sc.symbols.length) + " نماد در علاقه‌مندی‌های شما</div></div>" +
          '<div class="filter-block"><div class="filter-label">' + A.icon("layers") + " منطق ترکیب</div>" +
            '<div class="segmented" id="modeSeg">' + modeButtons + "</div>" +
            '<div class="hint mt-1">«هر سیگنال»: تأیید حداقل یک استراتژی. «همه با هم»: تأیید هم‌زمان همه استراتژی‌های انتخابی.</div></div>' +
          '<div class="filter-block full" style="grid-column:1/-1"><div class="row-between wrap">' +
            '<div class="filter-label" style="margin:0">' + A.icon("exchange") + " صرافی‌های من</div>" +
            '<label class="switch"><input type="checkbox" id="exchAll" ' + (sc.exchangesAll ? "checked" : "") + '><span class="switch-track"></span><span class="switch-text">فرقی نمی‌کند (همه صرافی‌ها)</span></label>' +
          "</div>" +
          '<div class="chips mt-1" id="exchChips">' + (exchChips || '<span class="tiny muted">صرافی فعالی برای نمایش وجود ندارد.</span>') + "</div>" +
          '<div class="hint mt-1">در صورت وجود نماد در چند صرافی، داده‌ها هرگز ترکیب نمی‌شوند؛ صرافی با اولویت بالاتر منبع تحلیل است.</div></div>' +
        "</div>" +
      "</div>" +

      '<div class="card strategy-section">' +
        '<div class="strategy-toolbar">' +
          '<div class="searchbox"><span>' + A.icon("search") + "</span>" +
            '<input id="strategySearch" placeholder="جستجوی استراتژی…" autocomplete="off">' +
            '<button class="clear" id="clearStrategySearch" hidden>' + A.icon("close") + "</button></div>" +
          '<span class="selected-count">انتخاب‌شده: <b id="selCount">' + A.toFa(strategiesCount) + "</b></span>" +
          '<label class="switch" style="margin-inline-start:auto"><input type="checkbox" id="favOnly"><span class="switch-track"></span><span class="switch-text">فقط علاقه‌مندی‌ها</span></label>' +
          '<button class="btn btn-sm btn-ghost" id="expandAll">' + A.icon("chevron-down") + " باز کردن همه</button>" +
        "</div>" +
        '<div class="strategy-scroll"><div class="acc" id="strategyAcc"></div></div>' +
        '<div class="strategy-foot">' +
          '<span class="tiny muted" id="strategySummary">' + A.toFa(sc.groups.length) + " دسته‌بندی • " + A.toFa(sc.keyMap.size) + " استراتژی</span>" +
          '<div class="right">' +
            '<button class="btn btn-sm btn-ghost" id="selectAllVisible">' + A.icon("check") + " انتخاب نتایج</button>" +
            '<button class="btn btn-sm btn-danger" id="clearAll">' + A.icon("trash") + " حذف همه</button>" +
            '<button class="btn btn-sm btn-soft" id="saveSelection">' + A.icon("check") + " تأیید انتخاب</button>" +
          "</div>" +
        "</div>" +
      "</div>" +

      '<div class="scan-runbar">' +
        '<div class="scan-summary" id="scanSummary"></div>' +
        '<button class="btn btn-primary btn-lg" id="runScan">' + A.icon("scanner") + " اجرای اسکن</button>" +
      "</div>" +

      '<div id="scanResults"></div>'
    );

    renderStrategyList(sc);
    updateScanSummary(sc);
    bindScanner(sc);
  }

  function signalWords(text) {
    return A.esc(text || "-")
      .replace(/مقاومت/g, '<b class="neg">مقاومت</b>')
      .replace(/حمایت/g, '<b class="pos">حمایت</b>');
  }

  function renderStrategyList(sc) {
    const acc = A.$("#strategyAcc");
    if (!acc) return;
    const q = sc.query.trim().toLowerCase();
    let html = "";
    let shownGroups = 0;
    sc.groups.forEach(function (g) {
      const items = g.strategies.filter(function (s) {
        if (sc.favOnly && !sc.favorites.has(s.key)) return false;
        if (!q) return true;
        return (s.title || "").toLowerCase().indexOf(q) >= 0 || (g.category || "").toLowerCase().indexOf(q) >= 0 || (s.strategy_type || "").toLowerCase().indexOf(q) >= 0;
      });
      if (!items.length) return;
      shownGroups++;
      const selCount = items.filter(function (s) { return sc.selected.has(s.key); }).length;
      const open = q ? true : sc.expanded.has(g.category);
      let rows = "";
      items.forEach(function (s) {
        const checked = sc.selected.has(s.key);
        const fav = sc.favorites.has(s.key);
        const guide = A.safeImage(s.guide_image);
        rows += '<div class="strategy-row" data-key="' + A.esc(s.key) + '">' +
          '<label class="check"><input type="checkbox" data-strategy="' + A.esc(s.key) + '" ' + (checked ? "checked" : "") + '>' +
          '<span class="check-box">' + A.icon("check") + "</span></label>" +
          '<div class="strategy-main"><div class="strategy-title">' + A.esc(s.title || s.key) +
            (guide ? '<button class="help-dot" data-guide="' + A.esc(s.key) + '" title="راهنمای استراتژی">' + A.icon("help") + "</button>" : "") +
          "</div>" +
          '<div class="strategy-meta">نوع استراتژی: <b>' + signalWords(s.strategy_type || g.category) + "</b> و نوع: " + signalWords(s.signal_type) +
          "</div></div>" +
          '<div class="strategy-actions"><button class="icon-btn sm ' + (fav ? "active" : "") + '" data-fav-strategy="' + A.esc(s.key) + '" title="' + (fav ? "حذف از علاقه‌مندی" : "افزودن به علاقه‌مندی") + '">' + A.icon("star") + "</button></div>" +
        "</div>";
      });
      html += '<div class="acc-item ' + (open ? "open" : "") + '" data-cat="' + A.esc(g.category) + '">' +
        '<button class="acc-head" type="button"><span class="acc-caret">' + A.icon("chevron-down") + "</span>" +
        '<span class="acc-name">' + A.esc(g.category) + "</span>" +
        '<span class="acc-count">' + A.toFa(items.length) + "</span>" +
        (selCount ? '<span class="acc-selected">' + A.toFa(selCount) + " انتخاب</span>" : "") +
        '<span class="acc-all" data-select-group="' + A.esc(g.category) + '">انتخاب همه</span></button>' +
        '<div class="acc-body">' + rows + "</div></div>";
    });
    acc.innerHTML = html || A.emptyState("search", "استراتژی پیدا نشد", sc.favOnly ? "در علاقه‌مندی‌ها استراتژی‌ای با این جستجو وجود ندارد." : "عبارت دیگری را جستجو کنید.");
    const summary = A.$("#strategySummary");
    if (summary) summary.textContent = A.toFa(shownGroups) + " دسته‌بندی نمایش داده شد";

    acc.querySelectorAll(".acc-head").forEach(function (head) {
      head.addEventListener("click", function (e) {
        if (e.target.closest("[data-select-group]")) return;
        const item = head.closest(".acc-item");
        const cat = item.getAttribute("data-cat");
        if (item.classList.contains("open")) { item.classList.remove("open"); sc.expanded.delete(cat); }
        else { item.classList.add("open"); sc.expanded.add(cat); }
      });
    });
    acc.querySelectorAll("[data-select-group]").forEach(function (btn) {
      btn.addEventListener("click", function (e) {
        e.stopPropagation();
        const cat = btn.getAttribute("data-select-group");
        const group = sc.groups.find(function (g) { return g.category === cat; });
        if (!group) return;
        const visible = group.strategies.filter(function (s) {
          if (sc.favOnly && !sc.favorites.has(s.key)) return false;
          if (!sc.query.trim()) return true;
          const q = sc.query.trim().toLowerCase();
          return (s.title || "").toLowerCase().indexOf(q) >= 0 || (group.category || "").toLowerCase().indexOf(q) >= 0 || (s.strategy_type || "").toLowerCase().indexOf(q) >= 0;
        });
        const allSelected = visible.every(function (s) { return sc.selected.has(s.key); });
        visible.forEach(function (s) { if (allSelected) sc.selected.delete(s.key); else sc.selected.add(s.key); });
        renderStrategyList(sc); updateScanSummary(sc);
      });
    });
    acc.querySelectorAll("input[data-strategy]").forEach(function (input) {
      input.addEventListener("change", function () {
        const key = input.getAttribute("data-strategy");
        if (input.checked) sc.selected.add(key); else sc.selected.delete(key);
        updateScanSummary(sc);
        updateGroupSelected(sc);
      });
    });
    acc.querySelectorAll("[data-fav-strategy]").forEach(function (btn) {
      btn.addEventListener("click", async function () {
        const key = btn.getAttribute("data-fav-strategy");
        try {
          const res = await A.api("/api/favorites", { method: "POST", body: { kind: "strategy", value: key } });
          if (res.active) sc.favorites.add(key); else sc.favorites.delete(key);
          btn.classList.toggle("active", !!res.active);
          btn.title = res.active ? "حذف از علاقه‌مندی" : "افزودن به علاقه‌مندی";
          A.toast(res.active ? "استراتژی به علاقه‌مندی‌ها اضافه شد." : "استراتژی از علاقه‌مندی‌ها حذف شد.", "success");
          if (sc.favOnly) { renderStrategyList(sc); updateScanSummary(sc); }
        } catch (err) { A.toast(err.message, "error"); }
      });
    });
    acc.querySelectorAll("[data-guide]").forEach(function (btn) {
      btn.addEventListener("click", function (e) {
        e.stopPropagation();
        const s = sc.keyMap.get(btn.getAttribute("data-guide"));
        if (s) showGuide(btn, s);
      });
    });
  }

  function updateGroupSelected(sc) {
    const acc = A.$("#strategyAcc");
    if (!acc) return;
    acc.querySelectorAll(".acc-item").forEach(function (item) {
      const cat = item.getAttribute("data-cat");
      const group = sc.groups.find(function (g) { return g.category === cat; });
      if (!group) return;
      const count = group.strategies.filter(function (s) { return sc.selected.has(s.key); }).length;
      let badge = item.querySelector(".acc-selected");
      if (count) {
        if (!badge) {
          badge = document.createElement("span");
          badge.className = "acc-selected";
          item.querySelector(".acc-head").insertBefore(badge, item.querySelector(".acc-all"));
        }
        badge.textContent = A.toFa(count) + " انتخاب";
      } else if (badge) badge.remove();
    });
    const sel = A.$("#selCount");
    if (sel) sel.textContent = A.toFa(sc.selected.size);
  }

  function updateScanSummary(sc) {
    const box = A.$("#scanSummary");
    const sel = A.$("#selCount");
    if (sel) sel.textContent = A.toFa(sc.selected.size);
    if (!box) return;
    const exCount = sc.exchangesAll ? sc.exchangeRows.length : sc.exchanges.size;
    box.innerHTML =
      "<span>تایم‌فریم: <b>" + A.esc(tfLabel(sc.timeframe)) + "</b></span>" +
      "<span>عمق: <b>" + (Number(sc.depth) === 0 ? "لحظه‌ای" : A.toFa(sc.depth) + " کندل") + "</b></span>" +
      "<span>استراتژی: <b>" + A.toFa(sc.selected.size) + "</b></span>" +
      "<span>نمادها: <b>" + (sc.symbolsMode === "all" ? "همه" : (sc.symbolsMode === "favorites" ? "علاقه‌مندی" : A.esc(sc.symbol || "تک نماد"))) + "</b></span>" +
      "<span>صرافی: <b>" + (sc.exchangesAll ? "همه" : A.toFa(exCount)) + "</b></span>";
    updateGroupSelected(sc);
  }

  function bindScanner(sc) {
    const tfSeg = A.$("#tfSeg");
    if (tfSeg) tfSeg.querySelectorAll("[data-tf]").forEach(function (b) {
      b.addEventListener("click", function () {
        sc.timeframe = b.getAttribute("data-tf");
        tfSeg.querySelectorAll("[data-tf]").forEach(function (x) { x.classList.toggle("active", x === b); });
        updateScanSummary(sc);
      });
    });
    const depthChips = A.$("#depthChips");
    if (depthChips) depthChips.querySelectorAll("[data-depth]").forEach(function (b) {
      b.addEventListener("click", function () {
        sc.depth = Number(b.getAttribute("data-depth"));
        depthChips.querySelectorAll("[data-depth]").forEach(function (x) { x.classList.toggle("active", x === b); });
        updateScanSummary(sc);
      });
    });
    const modeSeg = A.$("#modeSeg");
    if (modeSeg) modeSeg.querySelectorAll("[data-mode]").forEach(function (b) {
      b.addEventListener("click", function () {
        sc.mode = b.getAttribute("data-mode");
        modeSeg.querySelectorAll("[data-mode]").forEach(function (x) { x.classList.toggle("active", x === b); });
        updateScanSummary(sc);
      });
    });
    const symbolsSeg = A.$("#symbolsSeg");
    if (symbolsSeg) symbolsSeg.querySelectorAll("[data-symbols]").forEach(function (b) {
      b.addEventListener("click", function () {
        sc.symbolsMode = b.getAttribute("data-symbols");
        symbolsSeg.querySelectorAll("[data-symbols]").forEach(function (x) { x.classList.toggle("active", x === b); });
        const wrap = A.$("#singleSymbolWrap"); if (wrap) wrap.hidden = sc.symbolsMode !== "single";
        updateScanSummary(sc);
        if (sc.symbolsMode === "single") { const inp = A.$("#singleSymbol"); if (inp) inp.focus(); }
      });
    });
    const all = A.$("#exchAll");
    if (all) all.addEventListener("change", function () {
      sc.exchangesAll = all.checked;
      if (sc.exchangesAll) sc.exchangeRows.forEach(function (e) { sc.exchanges.add(e.slug); });
      const wrap = A.$("#exchChips");
      if (wrap) wrap.querySelectorAll("[data-exchange]").forEach(function (chip) {
        chip.classList.toggle("disabled", sc.exchangesAll);
        chip.classList.toggle("active", sc.exchangesAll || sc.exchanges.has(chip.getAttribute("data-exchange")));
      });
      updateScanSummary(sc);
    });
    const exchBox = A.$("#exchChips");
    if (exchBox) exchBox.querySelectorAll("[data-exchange]").forEach(function (chip) {
      chip.addEventListener("click", function () {
        if (sc.exchangesAll) return;
        const slug = chip.getAttribute("data-exchange");
        if (sc.exchanges.has(slug)) sc.exchanges.delete(slug); else sc.exchanges.add(slug);
        chip.classList.toggle("active", sc.exchanges.has(slug));
        updateScanSummary(sc);
      });
    });

    const search = A.$("#strategySearch"), clear = A.$("#clearStrategySearch");
    if (search) {
      search.addEventListener("input", A.debounce(function () {
        sc.query = search.value;
        if (clear) clear.hidden = !sc.query;
        renderStrategyList(sc); updateScanSummary(sc);
      }, 180));
    }
    if (clear) clear.addEventListener("click", function () {
      sc.query = ""; search.value = ""; clear.hidden = true; renderStrategyList(sc); updateScanSummary(sc);
    });
    const favOnly = A.$("#favOnly");
    if (favOnly) favOnly.addEventListener("change", function () {
      sc.favOnly = favOnly.checked;
      renderStrategyList(sc); updateScanSummary(sc);
    });
    const expandAll = A.$("#expandAll");
    if (expandAll) expandAll.addEventListener("click", function () {
      const items = A.$$("#strategyAcc .acc-item");
      const allOpen = items.every(function (i) { return i.classList.contains("open"); });
      items.forEach(function (i) { i.classList.toggle("open", !allOpen); });
      if (allOpen) sc.expanded.clear(); else sc.groups.forEach(function (g) { sc.expanded.add(g.category); });
      expandAll.innerHTML = A.icon("chevron-down") + (allOpen ? " باز کردن همه" : " بستن همه");
    });
    const selectVisible = A.$("#selectAllVisible");
    if (selectVisible) selectVisible.addEventListener("click", function () {
      const q = sc.query.trim().toLowerCase();
      sc.groups.forEach(function (g) {
        g.strategies.forEach(function (s) {
          if (sc.favOnly && !sc.favorites.has(s.key)) return;
          if (q && (s.title || "").toLowerCase().indexOf(q) < 0 && (g.category || "").toLowerCase().indexOf(q) < 0 && (s.strategy_type || "").toLowerCase().indexOf(q) < 0) return;
          sc.selected.add(s.key);
        });
      });
      renderStrategyList(sc); updateScanSummary(sc);
      A.toast("استراتژی‌های نمایش‌داده‌شده انتخاب شدند.", "success");
    });
    const clearAll = A.$("#clearAll");
    if (clearAll) clearAll.addEventListener("click", function () {
      sc.selected.clear(); renderStrategyList(sc); updateScanSummary(sc);
    });
    const saveSelection = A.$("#saveSelection");
    if (saveSelection) saveSelection.addEventListener("click", async function () {
      A.setBusy(saveSelection, true, "ذخیره…");
      try {
        await A.api("/api/settings", { method: "PUT", body: { scan_strategy_keys: Array.from(sc.selected) } });
        A.toast("انتخاب استراتژی‌ها ذخیره شد.", "success");
      } catch (err) { A.toast(err.message, "error"); }
      A.setBusy(saveSelection, false);
    });
    const runBtns = [A.$("#runScan"), A.$("#runTop")];
    runBtns.forEach(function (b) { if (b) b.addEventListener("click", function () { runScan(sc, b); }); });

    const single = A.$("#singleSymbol"), singleDrop = A.$("#singleSymbolDrop");
    if (single) {
      attachSymbolSearch(single, singleDrop, function (sym) {
        sc.symbol = sym; single.value = sym; updateScanSummary(sc);
      });
      single.addEventListener("input", function () { sc.symbol = single.value.trim().toUpperCase(); updateScanSummary(sc); });
    }
    updateScanSummary(sc);
  }

  async function saveScanSettings(sc) {
    try {
      await A.api("/api/settings", {
        method: "PUT",
        body: {
          scan_timeframe: sc.timeframe, scan_depth: Number(sc.depth), scan_mode: sc.mode,
          scan_symbols_mode: sc.symbolsMode, scan_symbol: sc.symbol,
          scan_strategy_keys: Array.from(sc.selected),
          scan_exchanges: Array.from(sc.exchanges), exchanges_all: sc.exchangesAll
        }
      });
    } catch (e) {}
  }

  function scanOverlay() {
    const el = document.createElement("div");
    el.className = "scan-progress";
    el.innerHTML = '<div class="scan-progress-card"><span class="spinner lg"></span><b>در حال اسکن بازار</b>' +
      '<p id="scanMsg">استراتژی‌های انتخابی روی نمادها بررسی می‌شوند…</p>' +
      '<div class="progress"><div class="progress-bar" id="scanBar" style="width:8%"></div></div>' +
      '<div class="elapsed" id="scanElapsed">۰ ثانیه</div>' +
      '<button class="btn btn-sm btn-ghost" id="scanCancel" style="margin-top:14px">لغو اسکن</button></div>';
    document.body.appendChild(el);
    document.body.classList.add("no-scroll");
    const msgs = [
      "دریافت کندل‌های نمادها از صرافی‌ها…",
      "محاسبه ساختار و سطوح کلیدی…",
      "بررسی هم‌زمان استراتژی‌های انتخابی…",
      "جمع‌بندی و آماده‌سازی نتایج…"
    ];
    let i = 0;
    const started = Date.now();
    const msg = el.querySelector("#scanMsg"), bar = el.querySelector("#scanBar"), elapsedEl = el.querySelector("#scanElapsed");
    const timer = setInterval(function () {
      const sec = Math.floor((Date.now() - started) / 1000);
      elapsedEl.textContent = A.fmtDuration(sec);
      msg.textContent = msgs[Math.floor(sec / 5) % msgs.length];
      const pct = Math.min(92, 8 + Math.min(80, sec * 1.6));
      bar.style.width = pct + "%";
    }, 1000);
    let cancelled = false;
    el.querySelector("#scanCancel").addEventListener("click", function () {
      cancelled = true;
      el.querySelector("#scanCancel").disabled = true;
      el.querySelector("#scanCancel").textContent = "در حال لغو…";
    });
    function close() {
      clearInterval(timer);
      el.remove();
      document.body.classList.remove("no-scroll");
    }
    return { el: el, close: close, isCancelled: function () { return cancelled; } };
  }

  async function runScan(sc, btn, rerunId) {
    if (!rerunId) {
      if (!sc.selected.size) { A.toast("حداقل یک استراتژی انتخاب کنید.", "warn"); return; }
      if (sc.symbolsMode === "single" && !sc.symbol.trim()) { A.toast("نماد مورد نظر را وارد کنید.", "warn"); return; }
      if (!sc.exchangesAll && !sc.exchanges.size) { A.toast("حداقل یک صرافی انتخاب کنید.", "warn"); return; }
    }
    const overlay = scanOverlay();
    [A.$("#runScan"), A.$("#runTop")].forEach(function (b) { if (b) b.disabled = true; });
    try {
      if (!rerunId) await saveScanSettings(sc);
      const controller = new AbortController();
      overlay.el.querySelector("#scanCancel").addEventListener("click", function () { controller.abort(); });
      let data;
      if (rerunId) {
        data = await A.api("/api/history/" + encodeURIComponent(rerunId) + "/rerun", { method: "POST", signal: controller.signal });
      } else {
        data = await A.api("/api/scanner/scan", {
          method: "POST",
          signal: controller.signal,
          body: {
            timeframe: sc.timeframe, depth: Number(sc.depth), mode: sc.mode,
            symbols_mode: sc.symbolsMode, symbol: sc.symbolsMode === "single" ? sc.symbol : "",
            strategy_keys: Array.from(sc.selected), exchanges: Array.from(sc.exchanges), exchanges_all: sc.exchangesAll
          }
        });
      }
      if (overlay.isCancelled()) { A.toast("اسکن لغو شد.", "warn"); return; }
      const bar = overlay.el.querySelector("#scanBar"); if (bar) bar.style.width = "100%";
      sc.results = data; sc.resultsPage = 1;
      renderScanResults(sc);
      const box = A.$("#scanResults");
      if (box) box.scrollIntoView({ behavior: "smooth", block: "start" });
    } catch (err) {
      if (err && (err.name === "AbortError" || overlay.isCancelled())) {
        A.toast("اسکن لغو شد.", "warn");
      } else if (err && err.status === 403) {
        renderLockedResults(sc, err.message);
      } else {
        A.toast(err.message || "اسکن انجام نشد.", "error");
      }
    } finally {
      overlay.close();
      [A.$("#runScan"), A.$("#runTop")].forEach(function (b) { if (b) b.disabled = false; });
    }
  }

  function renderLockedResults(sc, message) {
    const box = A.$("#scanResults");
    if (!box) return;
    box.innerHTML = '<div class="card results-card"><div class="card-body">' +
      '<div class="empty"><div class="empty-icon">' + A.icon("lock") + "</div><b>دسترسی به نتایج نیازمند اشتراک است</b><span>" +
      A.esc(message || "برای مشاهده سیگنال‌های لحظه‌ای باید اشتراک تحلیل ساختاری فعال داشته باشید.") + "</span>" +
      '<a class="btn btn-primary" href="/subscribe" data-link>' + A.icon("wallet") + " خرید / تمدید اشتراک</a></div></div></div>";
  }

  function renderScanResults(sc) {
    const box = A.$("#scanResults");
    if (!box || !sc.results) return;
    const result = sc.results;
    const all = result.results || [];
    const total = all.length;
    const perPage = 15;
    const pages = Math.max(1, Math.ceil(total / perPage));
    if (sc.resultsPage > pages) sc.resultsPage = pages;
    const start = (sc.resultsPage - 1) * perPage;
    const slice = all.slice(start, start + perPage);
    let rows = "";
    slice.forEach(function (r, idx) {
      const s = sc.keyMap.get(r.strategy_key);
      const guide = s ? A.safeImage(s.guide_image) : "";
      rows += '<tr data-row="' + (start + idx) + '">' +
        '<td><div class="result-symbol" data-open-symbol="' + A.esc(r.symbol) + '" data-tf="' + A.esc(r.timeframe) + '">' +
          '<span class="coin' + A.coinClass(r.symbol) + '" style="width:32px;height:32px;font-size:.6rem">' + A.esc(A.coinLetters(r.symbol)) + "</span>" +
          '<div><div class="cell-main">' + A.esc(r.symbol) + '</div><div class="cell-sub">' + A.esc(r.date || "") + "</div></div></div></td>" +
        '<td><span class="pill blue">' + A.esc(r.exchange || "-") + "</span></td>" +
        "<td>" + A.esc(tfLabel(r.timeframe)) + "</td>" +
        '<td class="result-strategy"><div class="cell-main">' + A.esc(r.strategy || "-") + "</div>" +
          (r.matches && r.matches.length ? '<div class="match-list">' + r.matches.map(function (m) { return '<span class="pill ' + (m.signal_type === "حمایت" ? "green" : "red") + '">' + A.esc(m.title) + "</span>"; }).join("") + "</div>" : "") + "</td>" +
        "<td>" + A.signalPill(r.signal_type) + "</td>" +
        "<td>" + A.directionPill(r.direction) + "</td>" +
        '<td class="num">' + A.esc(A.fmtPrice(r.price)) + "</td>" +
        '<td><div class="cell-actions">' +
          (r.id ? '<button class="icon-btn sm ' + (r.is_favorite ? "active" : "") + '" data-fav-signal="' + A.esc(r.id) + '" title="نشان کردن سیگنال">' + A.icon("heart") + "</button>" : "") +
          (guide ? '<button class="icon-btn sm" data-result-guide="' + A.esc(r.strategy_key) + '" title="راهنمای استراتژی">' + A.icon("help") + "</button>" : "") +
          '<button class="icon-btn sm" data-result-chart="' + (start + idx) + '" title="نمایش روی نمودار">' + A.icon("chart") + "</button>" +
        "</div></td></tr>";
    });

    box.innerHTML = '<div class="card results-card">' +
      '<div class="results-toolbar"><h3>' + A.icon("activity") + " نتایج اسکن</h3>" +
        '<div class="results-meta"><span>تعداد: <b>' + A.toFa(total) + "</b></span><span>زمان: <b>" + A.toFa((result.took || 0).toFixed ? (result.took).toFixed(1) : result.took) + " ثانیه</b></span>" +
        "<span>تایم‌فریم: <b>" + A.esc(tfLabel((result.filters || {}).timeframe || "")) + "</b></span>" +
        "<span>وضعیت: <b>" + A.esc((result.filters || {}).mode === "all" ? "همه با هم" : "هر سیگنال") + "</b></span></div>" +
        '<button class="btn btn-sm btn-ghost" id="rescan" style="margin-inline-start:auto">' + A.icon("refresh") + " اسکن دوباره</button></div>" +
      (total ? '<div class="table-wrap"><table class="table table-fix" style="min-width:1040px"><thead><tr><th>نماد</th><th>صرافی</th><th>تایم‌فریم</th><th>استراتژی</th><th>نوع</th><th>جهت</th><th>قیمت</th><th></th></tr></thead><tbody>' + rows + "</tbody></table></div>"
        : A.emptyState("scanner", "نتیجه‌ای پیدا نشد", "با افزایش عمق بررسی، حذف بخشی از استراتژی‌ها یا تغییر تایم‌فریم دوباره تلاش کنید.", '<button class="btn btn-soft btn-sm" id="rescanEmpty">' + A.icon("refresh") + " اسکن دوباره</button>")) +
      (total > perPage ? '<div class="scan-pagination"><span class="tiny muted">نمایش ' + A.toFa(start + 1) + " تا " + A.toFa(Math.min(start + perPage, total)) + " از " + A.toFa(total) + "</span>" + pagerHTML(sc.resultsPage, pages) + "</div>" : "") +
      "</div>";

    const rescan = A.$("#rescan") || A.$("#rescanEmpty");
    if (rescan) rescan.addEventListener("click", function () { runScan(sc, rescan); });
    const pager = box.querySelector(".scan-pagination");
    if (pager) bindPager(pager, function (p) { sc.resultsPage = p; renderScanResults(sc); box.scrollIntoView({ behavior: "smooth", block: "start" }); });
    box.querySelectorAll("[data-open-symbol]").forEach(function (el) {
      el.addEventListener("click", function () { openSymbol(el.getAttribute("data-open-symbol"), el.getAttribute("data-tf")); });
    });
    box.querySelectorAll("[data-result-chart]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        const row = all[parseInt(btn.getAttribute("data-result-chart"), 10)];
        if (!row) return;
        storeFocus(row);
        openSymbol(row.symbol, row.timeframe);
      });
    });
    box.querySelectorAll("[data-result-guide]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        const s = sc.keyMap.get(btn.getAttribute("data-result-guide"));
        if (s) showGuide(btn, s);
      });
    });
    box.querySelectorAll("[data-fav-signal]").forEach(function (btn) {
      btn.addEventListener("click", async function () {
        try {
          const res = await A.api("/api/favorites", { method: "POST", body: { kind: "signal", value: btn.getAttribute("data-fav-signal") } });
          btn.classList.toggle("active", !!res.active);
          A.toast(res.active ? "سیگنال نشان شد." : "نشان سیگنال برداشته شد.", "success");
        } catch (err) { A.toast(err.message, "error"); }
      });
    });
  }

  /* ---------- راهنمای شناور ---------- */
  let guideEl = null;
  let guideOutside = null;
  function closeGuide() {
    if (guideOutside) {
      document.removeEventListener("click", guideOutside);
      guideOutside = null;
    }
    if (guideEl) { guideEl.remove(); guideEl = null; }
  }
  function showGuide(anchor, strategy) {
    closeGuide();
    const el = document.createElement("div");
    el.className = "guide-pop";
    el.innerHTML = '<div class="guide-pop-title">' + A.icon("book") + A.esc(strategy.title || "راهنمای استراتژی") + "</div>" +
      (A.safeImage(strategy.guide_image) ? '<img src="' + A.esc(A.safeImage(strategy.guide_image)) + '" alt="' + A.esc(strategy.title || "") + '">' : "") +
      '<p>' + A.esc(strategy.description || strategy.guide_description || "برای این استراتژی توضیحی ثبت نشده است.") + "</p>";
    document.body.appendChild(el);
    const r = anchor.getBoundingClientRect();
    const w = el.offsetWidth, h = el.offsetHeight;
    let left = r.left + r.width / 2 - w / 2;
    let top = r.bottom + 8;
    if (left < 10) left = 10;
    if (left + w > window.innerWidth - 10) left = window.innerWidth - w - 10;
    if (top + h > window.innerHeight - 10) top = Math.max(10, r.top - h - 8);
    el.style.left = left + "px";
    el.style.top = top + "px";
    guideEl = el;
    setTimeout(function () {
      if (guideEl !== el) return;
      guideOutside = function (e) {
        if (!el.contains(e.target) && e.target !== anchor) closeGuide();
      };
      document.addEventListener("click", guideOutside);
    }, 0);
  }

  /* ---------- جستجوی نماد (مشترک) ---------- */
  function attachSymbolSearch(input, drop, onPick) {
    if (!input || !drop) return;
    let seq = 0;
    const run = A.debounce(async function () {
      const q = input.value.trim();
      if (!q) { drop.hidden = true; return; }
      const my = ++seq;
      drop.hidden = false;
      drop.innerHTML = '<div class="search-empty">در حال جستجو…</div>';
      try {
        const data = await A.api("/api/symbols/search?q=" + encodeURIComponent(q) + "&limit=12");
        if (my !== seq) return;
        const items = data.results || [];
        if (!items.length) { drop.innerHTML = '<div class="search-empty">نمادی پیدا نشد.</div>'; return; }
        drop.innerHTML = items.map(function (it) {
          return '<div class="search-item" data-symbol="' + A.esc(it.symbol) + '"><span class="coin' + A.coinClass(it.symbol) + '">' + A.esc(A.coinLetters(it.symbol)) + "</span>" +
            '<div class="flex-1"><div class="search-item-name">' + A.esc(it.symbol) + '</div><div class="search-item-sub">' + A.esc(it.exchange || "") + "</div></div>" + A.icon("chevron-left") + "</div>";
        }).join("");
        drop.querySelectorAll(".search-item").forEach(function (el) {
          el.addEventListener("mousedown", function (e) { e.preventDefault(); });
          el.addEventListener("click", function () {
            const sym = el.getAttribute("data-symbol");
            input.value = sym;
            if (onPick) onPick(sym);
            drop.hidden = true;
          });
        });
      } catch (err) {
        if (my !== seq) return;
        drop.innerHTML = '<div class="search-empty">' + A.esc(err.message) + "</div>";
      }
    }, 300);
    input.addEventListener("input", run);
    input.addEventListener("focus", function () { if (input.value.trim()) run(); });
    input.addEventListener("keydown", function (e) {
      if (e.key === "Enter") {
        e.preventDefault();
        const first = drop.querySelector(".search-item");
        if (first) first.click();
      }
      if (e.key === "Escape") drop.hidden = true;
    });
    symbolSearchPins = symbolSearchPins.filter(function (p) { return p.input.isConnected; });
    symbolSearchPins.push({ input: input, drop: drop });
    if (!symbolSearchDocBound) {
      symbolSearchDocBound = true;
      document.addEventListener("click", symbolSearchOutside);
    }
  }

  /* یک شنونده‌ی سراسری برای همه‌ی جستجوها؛ ورودی‌های حذف‌شده خودبه‌خود کنار گذاشته می‌شوند. */
  let symbolSearchPins = [];
  let symbolSearchDocBound = false;
  function symbolSearchOutside(e) {
    symbolSearchPins = symbolSearchPins.filter(function (p) { return p.input.isConnected && p.drop.isConnected; });
    symbolSearchPins.forEach(function (p) {
      if (!p.drop.contains(e.target) && e.target !== p.input) p.drop.hidden = true;
    });
  }

  /* ================================================================
     نمودار VibeChart و صفحه نماد
     ================================================================ */
  const LAYER_COLORS = ["#4b8ef7", "#9b7bf6", "#f2b84b", "#16b981", "#ef4d5a", "#22b8cf", "#e0715f", "#8b93a7"];
  const DEFAULT_COIN_LOGO = "/assets/icons/coin-default.svg";

  function layerId(row) {
    return String(row.strategy_key || row.strategy || "") + "@" + Math.round(Number(row.candle_ts) || 0);
  }

  function tfSeconds(tf) { return (TF_MINUTES[tf] || 60) * 60; }

  /* رنگ هر استراتژی ثابت می‌ماند تا کاربر نمودار را با همان رنگ بشناسد */
  function layerColor(row) {
    const key = String((row && (row.strategy_key || row.strategy)) || "");
    let h = 0;
    for (let i = 0; i < key.length; i++) h = (h * 131 + key.charCodeAt(i)) >>> 0;
    return LAYER_COLORS[h % LAYER_COLORS.length];
  }

  function selectedSignal(st) {
    const l = (st.layers || []).find(function (x) { return x.id === st.focusedLayer; }) || (st.layers || [])[0];
    return (l && l.signal) || null;
  }

  function layerSummary(row) {
    const typeText = row.signal_type || (String(row.strategy_type || "").indexOf("مقاومت") >= 0 ? "مقاومت" : "");
    const cls = typeText === "حمایت" ? "pos" : (typeText === "مقاومت" ? "neg" : "muted");
    return { typeText: typeText || "-", cls: cls };
  }

  function chartSettingsDefault() {
    return {
      theme: A.theme.current(),
      showRealtimePrice: true,
      showVolumePanel: true,
      disableSymbolSearch: true,
      enableDrawingTools: true
    };
  }

  function baseAssetOf(row) {
    const direct = row && (row.base_asset || row.base);
    if (direct) return String(direct).toUpperCase();
    return String((row && row.symbol) || "").replace(/USDT$/i, "").replace(/[^A-Za-z0-9]/g, "").toUpperCase();
  }

  function symbolLogoURL(base) {
    const b = String(base || "").replace(/[^A-Za-z0-9]/g, "").toUpperCase();
    return b ? "/api/assets/" + encodeURIComponent(b) + "/logo" : DEFAULT_COIN_LOGO;
  }

  function symbolLogoHTML(symbol, logoUrl) {
    const src = logoUrl || symbolLogoURL(baseAssetOf({ symbol: symbol }));
    return '<span class="coin logo' + A.coinClass(symbol) + '">' +
      '<img src="' + A.esc(src) + '" alt="' + A.esc(A.coinLetters(symbol)) + '" decoding="async" ' +
      'onerror="this.onerror=null;this.src=\'' + DEFAULT_COIN_LOGO + '\'">' +
      "</span>";
  }

  function updatePriceDisplay(st, live) {
    const el = A.$("#symLivePrice");
    const label = A.$("#symPriceLabel");
    const showLive = !st.chartSettings || st.chartSettings.showRealtimePrice !== false;
    if (label) label.textContent = showLive ? "قیمت لحظه‌ای" : "بسته‌شدن آخرین کندل";
    if (!el) return;
    if (!showLive && !Number(st.lastClose)) { el.textContent = "—"; return; }
    let value = showLive ? (live != null ? live : st.header.price) : st.lastClose;
    value = Number(value);
    if (!Number.isFinite(value) || value <= 0) value = Number(st.header.price) || 0;
    el.textContent = value > 0 ? A.fmtPrice(value) : "—";
  }

  function stopPricePolling(st) {
    if (st && st.priceTimer) {
      window.clearInterval(st.priceTimer);
      st.priceTimer = null;
    }
    if (st) st.priceSeq = (st.priceSeq || 0) + 1;
  }

  function startPricePolling(st) {
    stopPricePolling(st);
    if (!st || !st.chartSettings || st.chartSettings.showRealtimePrice === false) return;
    const seq = st.priceSeq;
    const tick = async function () {
      try {
        const data = await A.api("/api/symbol/" + encodeURIComponent(st.symbol) + "/price");
        if (seq !== st.priceSeq) return;
        const price = Number(data.price);
        if (Number.isFinite(price) && price > 0) {
          st.header.price = price;
          st.header.change_pct = Number(data.change_pct) || st.header.change_pct;
          updatePriceDisplay(st, price);
        }
      } catch (err) { /* قیمت لحظه‌ای اختیاری است */ }
    };
    st.priceTimer = window.setInterval(tick, 15000);
  }

  /* یک ردیف سیگنال را به لایه فعال نمودار تبدیل می‌کند؛ لایه قبلی جایگزین می‌شود */
  async function toggleLayer(st, row, forceOn) {
    if (!st || !row) return;
    if (st.locked) { A.toast("برای نمایش روی نمودار، اشتراک تحلیل ساختاری را فعال کنید.", "error"); return; }
    const id = layerId(row);
    const exist = st.layers.find(function (l) { return l.id === id; });
    if (exist && !forceOn) { removeLayer(st, id); return; }
    if (!row.overlay) { A.toast("این تحلیل لایه‌ی قابل نمایشی ندارد.", "error"); return; }
    st.layers = [{ id: id, signal: row, visible: true, color: layerColor(row) }];
    st.focusedLayer = id;
    st.selectedRow = id;
    const needReload = row.timeframe && row.timeframe !== st.chartTf;
    if (needReload) {
      st.chartTf = row.timeframe;
      A.$$("#chartTfSeg [data-chart-tf]").forEach(function (b) {
        b.classList.toggle("active", b.getAttribute("data-chart-tf") === st.chartTf);
      });
      await loadChart(st);
    } else if (st.chartApi) {
      st.chartApi.setLayers(st.layers);
      focusRange(st.chartApi, row);
    }
    renderLayerStrip(st);
    renderSignals(st);
    renderChartNav(st);
    const c = A.$("#clearOverlay"); if (c) c.hidden = !st.layers.length;
    const chart = A.$("#priceChart");
    if (chart && chart.scrollIntoView) chart.scrollIntoView({ behavior: "smooth", block: "center" });
  }

  function removeLayer(st, id) {
    const idx = st.layers.findIndex(function (l) { return l.id === id; });
    if (idx < 0) return;
    st.layers.splice(idx, 1);
    if (st.selectedRow === id) st.selectedRow = null;
    if (st.focusedLayer === id) st.focusedLayer = null;
    if (st.chartApi) st.chartApi.setLayers(st.layers);
    renderLayerStrip(st);
    renderSignals(st);
    const c = A.$("#clearOverlay"); if (c) c.hidden = !st.layers.length;
  }

  function clearLayers(st) {
    st.layers = [];
    st.focusedLayer = null;
    st.selectedRow = null;
    if (st.chartApi) st.chartApi.setLayers(st.layers);
    renderLayerStrip(st);
    renderSignals(st);
    const c = A.$("#clearOverlay"); if (c) c.hidden = true;
  }

  function renderLayerStrip(st) {
    const strip = A.$("#layerStrip");
    if (!strip) return;
    if (!st.layers.length) {
      strip.innerHTML = '<div class="layer-empty tiny muted">' + A.icon("layers") +
        " با انتخاب یک استراتژی از جدول تحلیل‌ها یا فهرست بالای چارت، هندسهٔ همان استراتژی روی نمودار رسم می‌شود." +
        " ترسیم‌های دستی شما جدا هستند و با تغییر لایه باقی می‌مانند.</div>";
      return;
    }
    strip.innerHTML = '<div class="layer-list">' + st.layers.map(function (l) {
      const s = l.signal || {};
      const sum = layerSummary(s);
      return '<div class="layer-chip" data-layer="' + A.esc(l.id) + '">' +
        '<span class="layer-dot" style="background:' + l.color + '"></span>' +
        '<div class="layer-text"><div class="chip-title">استراتژی فعال روی نمودار</div>' +
          '<div class="cell-main">' + A.esc(s.strategy || "-") + "</div>" +
          '<div class="cell-sub">' + A.esc(tfLabel(s.timeframe)) + " • " + A.esc(s.date || "-") + " • " +
          '<span class="' + sum.cls + '">' + A.esc(sum.typeText) + "</span> • " + A.esc(A.fmtPrice(s.price)) + "</div></div>" +
        '<div class="layer-actions">' +
          '<button class="icon-btn sm" data-layer-focus="' + A.esc(l.id) + '" title="تمرکز روی این استراتژی">' + A.icon("target") + "</button>" +
          '<button class="icon-btn sm danger" data-layer-remove="' + A.esc(l.id) + '" title="حذف لایه تحلیل">' + A.icon("close") + "</button>" +
        "</div></div>";
    }).join("") + "</div>";
    strip.querySelectorAll("[data-layer-focus]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        const id = btn.getAttribute("data-layer-focus");
        const layer = st.layers.find(function (l) { return l.id === id; });
        if (!layer || !st.chartApi) return;
        st.focusedLayer = id;
        st.selectedRow = id;
        focusRange(st.chartApi, layer.signal);
        renderSignals(st);
        const chart = A.$("#priceChart");
        if (chart) chart.scrollIntoView({ behavior: "smooth", block: "center" });
      });
    });
    strip.querySelectorAll("[data-layer-remove]").forEach(function (btn) {
      btn.addEventListener("click", function () { removeLayer(st, btn.getAttribute("data-layer-remove")); });
    });
  }

  function focusRange(api, signal) {
    if (!api || !signal) return;
    try { api.focus(signal); } catch (e) {}
  }

  /* ---------------- ناوبر بالای چارت ---------------- */

  function chartNavHTML() {
    return '<div class="chart-nav">' +
      '<div class="nav-pair">' +
        '<button class="icon-btn sm" data-chart-nav="signal-prev" title="سیگنال قبلی این نماد">' + A.icon("chevron-right") + "</button>" +
        '<div class="nav-text"><div class="nav-title">سایر سیگنال‌های این نماد</div><div class="nav-count" id="sigNavCount">—</div></div>' +
        '<button class="icon-btn sm" data-chart-nav="signal-next" title="سیگنال بعدی این نماد">' + A.icon("chevron-left") + "</button>" +
      "</div>" +
      '<div class="nav-pair">' +
        '<button class="icon-btn sm" data-chart-nav="symbol-prev" title="نماد قبلی">' + A.icon("chevron-right") + "</button>" +
        '<div class="nav-text"><div class="nav-title">سایر نمادها</div><div class="nav-count" id="symNavCount">در حال دریافت…</div></div>' +
        '<button class="icon-btn sm" data-chart-nav="symbol-next" title="نماد بعدی">' + A.icon("chevron-left") + "</button>" +
      "</div>" +
      '<div class="nav-drop" id="stratDrop">' +
        '<button class="btn btn-sm btn-ghost" id="stratDropBtn">' + A.icon("layers") + " سایر استراتژی‌های این نماد" + A.icon("chevron-down") + "</button>" +
        '<div class="drop-panel" id="stratDropPanel" hidden></div>' +
      "</div>" +
      '<div class="nav-actions">' +
        '<button class="btn btn-sm btn-ghost" id="chartUndo" title="واگرد آخرین ترسیم">واگرد</button>' +
        '<button class="btn btn-sm btn-ghost" id="chartRedo" title="ازنوی آخرین ترسیم">ازنو</button>' +
        '<button class="btn btn-sm btn-ghost" id="chartGuideBtn">' + A.icon("help") + " راهنمای استفاده</button>" +
        '<button class="icon-btn sm" id="chartFsBtn" title="نمایش تمام‌صفحه">' + A.icon("fullscreen") + "</button>" +
      "</div>" +
    "</div>";
  }

  function signalItems(st) { return (st.signals && !st.signals.locked && st.signals.results) || []; }

  function currentSignalIdx(st) {
    const items = signalItems(st);
    if (st.focusedLayer) {
      const i = items.findIndex(function (r) { return layerId(r) === st.focusedLayer; });
      if (i >= 0) return i;
    }
    if (st.sigNavIdx >= 0 && st.sigNavIdx < items.length) return st.sigNavIdx;
    return -1;
  }

  function updateUndoRedo(st) {
    const undo = A.$("#chartUndo");
    const redo = A.$("#chartRedo");
    if (undo) undo.disabled = !(st.chartApi && st.chartApi.canUndo && st.chartApi.canUndo());
    if (redo) redo.disabled = !(st.chartApi && st.chartApi.canRedo && st.chartApi.canRedo());
  }

  function renderChartNav(st) {
    const items = signalItems(st);
    const idx = currentSignalIdx(st);
    const sig = A.$("#sigNavCount");
    if (sig) {
      sig.textContent = items.length
        ? "سیگنال " + A.toFa((idx >= 0 ? idx : 0) + 1) + " از " + A.toFa(items.length)
        : "سیگنالی نیست";
    }
    const sib = st.siblings || [];
    const sym = A.$("#symNavCount");
    if (sym) {
      if (st.siblingsLocked) sym.textContent = "نیازمند اشتراک";
      else if (!sib.length) sym.textContent = st.siblingsLoaded ? "نماد سیگنال‌دیده‌ای نیست" : "در حال دریافت…";
      else {
        const i = sib.findIndex(function (s) { return String(s.symbol).toUpperCase() === String(st.symbol).toUpperCase(); });
        sym.textContent = (i >= 0 ? A.toFa(i + 1) : "۰") + " / " + A.toFa(sib.length) +
          (i >= 0 ? " — " + sib[i].symbol : "");
      }
    }
    const has = items.length > 1;
    A.$$("[data-chart-nav]").forEach(function (btn) {
      const kind = btn.getAttribute("data-chart-nav");
      const enabled = kind.indexOf("signal") === 0 ? has : (sib.length > 1);
      btn.disabled = !enabled || !!st.locked;
    });
    const drop = A.$("#stratDropBtn");
    if (drop) {
      drop.disabled = !items.length || !!st.locked;
      const sel = selectedSignal(st);
      drop.innerHTML = '<span class="layer-dot" style="background:' + (sel ? layerColor(sel) : "var(--muted)") + '"></span>' +
        (sel
          ? '<span class="drop-label"><b>' + A.esc(sel.strategy || "استراتژی") + "</b>" +
            "<small>" + A.esc(tfLabel(sel.timeframe)) + " • " + A.esc(sel.date || "-") + "</small></span>"
          : "<span>سایر استراتژی‌های این نماد</span>") +
        A.icon("chevron-down");
      drop.title = sel ? "تغییر استراتژی نمایش‌داده‌شده روی نمودار" : "انتخاب استراتژی برای نمایش روی نمودار";
    }
    updateUndoRedo(st);
    renderStratPanel(st);
  }

  async function stepSignal(st, delta) {
    const items = signalItems(st);
    if (!items.length) { A.toast("برای این نماد سیگنالی ثبت نشده است.", "info"); return; }
    const cur = currentSignalIdx(st);
    const next = cur < 0 ? (delta > 0 ? 0 : items.length - 1)
      : (cur + delta + items.length) % items.length;
    st.sigNavIdx = next;
    const row = items[next];
    st.page = Math.floor(next / st.perPage) + 1;
    await toggleLayer(st, row, true);
    renderChartNav(st);
  }

  async function loadSiblings(st) {
    st.siblings = []; st.siblingsLoaded = false; st.siblingsLocked = false;
    try {
      const data = await A.api("/api/symbols/signalled?limit=80");
      if (data.locked) st.siblingsLocked = true;
      else st.siblings = data.items || [];
    } catch (err) { /* فهرست نمادها اختیاری است */ }
    st.siblingsLoaded = true;
    renderChartNav(st);
  }

  function stepSymbol(st, delta) {
    const list = st.siblings || [];
    if (list.length < 2) { A.toast("نماد سیگنال‌دیدهٔ دیگری برای پیمایش نیست.", "info"); return; }
    let i = list.findIndex(function (s) { return String(s.symbol).toUpperCase() === String(st.symbol).toUpperCase(); });
    i = i < 0 ? (delta > 0 ? 0 : list.length - 1) : (i + delta + list.length) % list.length;
    const target = list[i];
    if (String(target.symbol).toUpperCase() === String(st.symbol).toUpperCase()) return;
    A.go("/symbol/" + encodeURIComponent(target.symbol) + "?tf=" + encodeURIComponent(target.timeframe || st.analysisTf) +
      "&ctf=" + encodeURIComponent(st.chartTf));
  }

  function strategyOptions(st) {
    const seen = new Set();
    const out = [];
    signalItems(st).forEach(function (r, i) {
      const key = r.strategy_key || r.strategy || "";
      if (!seen.has(key)) { seen.add(key); out.push({ key: key, row: r, index: i }); }
    });
    return out;
  }

  function renderStratPanel(st) {
    const panel = A.$("#stratDropPanel");
    if (!panel || panel.hidden) return;
    const opts = strategyOptions(st);
    if (!opts.length) {
      panel.innerHTML = '<div class="drop-empty">استراتژی تأییدشده‌ای روی این نماد نیست.</div>';
      return;
    }
    const sel = selectedSignal(st);
    const selKey = sel ? (sel.strategy_key || sel.strategy || "") : null;
    panel.innerHTML = '<div class="drop-title">' + A.icon("layers") +
      " انتخاب یک استراتژی برای نمایش روی نمودار • " + A.esc(st.symbol) + "</div>" +
      '<div class="drop-list">' + opts.map(function (o) {
        const sum = layerSummary(o.row);
        const on = !!selKey && String(o.key) === String(selKey);
        return '<button class="drop-item' + (on ? " active" : "") + '" data-strat-pick="' + A.esc(String(o.index)) + '">' +
          '<span class="layer-dot" style="background:' + (on ? layerColor(o.row) : "var(--border)") + '"></span>' +
          '<span class="drop-text"><b>' + A.esc(o.row.strategy || "-") + "</b>" +
            '<small>' + A.esc(tfLabel(o.row.timeframe)) + " • " + A.esc(o.row.date || "-") + " • " +
            '<span class="' + sum.cls + '">' + A.esc(sum.typeText) + "</span> • " + A.esc(A.fmtPrice(o.row.price)) + "</small></span>" +
          A.icon(on ? "check" : "chart") +
        "</button>";
      }).join("") + "</div>";
    panel.querySelectorAll("[data-strat-pick]").forEach(function (btn) {
      btn.addEventListener("click", async function () {
        const row = signalItems(st)[parseInt(btn.getAttribute("data-strat-pick"), 10)];
        closeStratPanel();
        if (!row) return;
        if (!row.overlay) { A.toast("این تحلیل لایه‌ی قابل نمایشی ندارد.", "error"); return; }
        if (sel && layerId(sel) === layerId(row)) { renderChartNav(st); return; }
        await toggleLayer(st, row, true);
        A.toast("استراتژی «" + (row.strategy || "") + "» روی نمودار نمایش داده شد.", "success");
      });
    });
  }

  function closeStratPanel() {
    const panel = A.$("#stratDropPanel");
    if (panel) panel.hidden = true;
  }

  function toggleStratPanel(st) {
    const panel = A.$("#stratDropPanel");
    if (!panel) return;
    if (!panel.hidden) { panel.hidden = true; return; }
    panel.hidden = false;
    renderStratPanel(st);
  }

  function setFullscreen(st, on) {
    const card = A.$(".chart-card");
    const box = A.$("#priceChart");
    const btn = A.$("#chartFsBtn");
    if (!card || !box) return;
    st.fullscreen = !!on;
    card.classList.toggle("fullscreen", st.fullscreen);
    if (btn) {
      btn.innerHTML = A.icon(st.fullscreen ? "fullscreen-exit" : "fullscreen");
      btn.title = st.fullscreen ? "بازگشت به اندازه عادی" : "نمایش تمام‌صفحه";
    }
    const sync = function () {
      if (!st.chartApi) return;
      try { st.chartApi.resize(); } catch (e) {}
      const focused = st.layers.find(function (l) { return l.id === st.focusedLayer; });
      if (focused) focusRange(st.chartApi, focused.signal);
    };
    setTimeout(sync, 60);
    setTimeout(sync, 260);
  }

  function showChartGuide() {
    A.openModal({ title: "راهنمای استفاده از نمودار", size: "wide", body: '<div class="guide-steps">' +
      '<div class="guide-step"><span class="guide-num">۱</span><div><b>یک استراتژی روی نمودار</b><p>با کلیک روی هر ردیف جدول تحلیل‌ها یا انتخاب از فهرست بالای چارت، خطوط، سطوح، نواحی و نشانه‌های همان استراتژی رسم می‌شود. انتخاب استراتژی دیگر جایگزین لایه قبلی می‌شود و ترسیم‌های دستی شما دست‌نخورده می‌مانند.</p></div></div>' +
      '<div class="guide-step"><span class="guide-num">۲</span><div><b>ابزارهای ترسیم</b><p>نوار عمودی سمت چپ شامل تمام ابزارهای استاندارد ترسیم (خط روند، فیبوناچی، اشکال، یادداشت، اندازه‌گیری و...) است و همیشه فعال است. ترسیم‌ها برای هر نماد و تایم‌فریم به‌صورت خودکار ذخیره می‌شوند.</p></div></div>' +
      '<div class="guide-step"><span class="guide-num">۳</span><div><b>واگرد و ازنو</b><p>برای اصلاح ترسیم‌ها از دکمه‌های «واگرد» و «ازنو» یا کلیدهای Ctrl+Z و Ctrl+Y استفاده کنید.</p></div></div>' +
      '<div class="guide-step"><span class="guide-num">۴</span><div><b>اندیکاتورهای پایه</b><p>اندیکاتورهایی مثل RSI، EMA و MACD که موتور تحلیل بر اساس آن‌ها سیگنال می‌دهد، همراه استراتژی انتخاب‌شده و به‌صورت خودکار نمایش داده می‌شوند و قابل حذف نیستند.</p></div></div>' +
      '<div class="guide-step"><span class="guide-num">۵</span><div><b>تایم‌فریم و نوع نمودار</b><p>تایم‌فریم نمودار را از نوار بالای چارت و نوع نمایش را از دکمه‌های «کندل/خطی» تغییر دهید. جستجو و تغییر نماد داخل خود چارت غیرفعال است؛ برای نماد دیگر از جستجوی بالای صفحه استفاده کنید.</p></div></div>' +
      '<div class="guide-step"><span class="guide-num">۶</span><div><b>زوم و تمام‌صفحه</b><p>با دکمه‌های بازه (۱ ماه تا همه) نمای نمودار را عوض کنید، با اسکرول زوم کنید و با دکمهٔ تمام‌صفحه نمودار را بزرگ کنید. کلید Esc از حالت تمام‌صفحه خارج می‌شود.</p></div></div>' +
      "</div>" });
  }

  async function viewSymbol(symbol) {
    const sym = String(symbol || "").toUpperCase();
    A.setPage(sym || "نماد", "نمودار قیمت و تحلیل ساختاری نماد");
    A.markNav("");
    setHTML('<div class="card pad"><div class="loading-row"><span class="spinner"></span><span>در حال دریافت اطلاعات نماد…</span></div></div>');
    const params = new URLSearchParams(location.search);
    let header, settingsData, strategiesData, chartSettings;
    try {
      const chartSettingsRequest = A.api("/api/chart-settings").catch(function () { return chartSettingsDefault(); });
      const result = await Promise.all([
        A.api("/api/symbol/" + encodeURIComponent(sym) + "/header"),
        A.api("/api/settings"),
        A.api("/api/strategies"),
        chartSettingsRequest
      ]);
      header = result[0]; settingsData = result[1]; strategiesData = result[2];
      chartSettings = result[3] || chartSettingsDefault();
    } catch (err) {
      setHTML(errorCard(err, "symRetry"));
      const b = A.$("#symRetry"); if (b) b.addEventListener("click", function () { viewSymbol(sym); });
      return;
    }
    if (!chartSettings || (chartSettings.theme !== "dark" && chartSettings.theme !== "light")) {
      chartSettings = chartSettingsDefault();
    }
    state.settings = settingsData;
    state.user = settingsData.user || state.user;
    A.fillUser(state.user);

    const keyMap = new Map();
    (strategiesData.groups || []).forEach(function (g) { (g.strategies || []).forEach(function (s) { keyMap.set(s.key, s); }); });
    const analysisTf = (params.get("tf") && TF_LABEL[params.get("tf")]) ? params.get("tf") : ((settingsData.settings && settingsData.settings.scan_timeframe) || "1d");
    const chartTfs = (header.chart_timeframes || []).map(function (t) { return t.key; });
    const reqCtf = params.get("ctf");
    const chartTf = (reqCtf && chartTfs.indexOf(reqCtf) >= 0) ? reqCtf : (chartTfs.indexOf(analysisTf) >= 0 ? analysisTf : "1d");
    if (!header.logo_url) header.logo_url = symbolLogoURL(header.base || header.symbol || sym);
    const st = {
      symbol: header.symbol || sym, header: header, keyMap: keyMap, analysisTf: analysisTf,
      chartTf: chartTf, chartType: "candles", chartApi: null,
      chartSettings: chartSettings,
      layers: [], focusedLayer: null,
      signals: null, signalsCache: {}, page: 1, perPage: 15, selectedRow: null,
      focus: params.get("focus") ? readFocus(header.symbol || sym) : null, loadingSignals: false,
      locked: !!header.locked, fullscreen: false, sigNavIdx: -1,
      siblings: [], siblingsLoaded: false, siblingsLocked: false,
      lastClose: 0, priceTimer: null, priceSeq: 0
    };
    renderSymbolPage(st);
    renderLayerStrip(st);
    bindSymbolPage(st);
    if (st.locked) showChartLock(st);
    else loadChart(st);
    loadSiblings(st);
    startPricePolling(st);
    updatePriceDisplay(st);
    loadSignals(st).then(function () {
      if (st.focus) {
        const f = st.focus;
        const found = st.signals ? (st.signals.results || []).find(function (r) {
          return r.strategy_key === f.strategy_key && Math.abs(Number(r.candle_ts) - Number(f.candle_ts || 0)) < 180;
        }) : null;
        const row = found || f;
        if (row && row.overlay) {
          toggleLayer(st, row, true).then(function () {
            A.toast("سیگنال انتخاب‌شده روی نمودار نمایش داده شد.", "success");
          });
        }
        st.focus = null; clearFocus();
      }
    });
  }

  /* اشتراک فعال، نمودار و پیمایش را باز می‌کند */
  function showChartLock(st) {
    stopPricePolling(st);
    if (st.chartApi) { try { st.chartApi.destroy(); } catch (e) {} st.chartApi = null; }
    const box = A.$("#priceChart");
    if (box) {
      box.innerHTML = '<div class="chart-lock">' + A.emptyState("lock", "نمودار تعاملی با اشتراک فعال باز می‌شود",
        "برای دیدن کندل‌ها، لایه‌های استراتژی و پیمایش نمادها، اشتراک تحلیل ساختاری را فعال کنید.",
        '<a class="btn btn-primary btn-sm" href="/subscribe" data-link>' + A.icon("wallet") + " تهیه اشتراک</a>") + "</div>";
    }
    renderChartNav(st);
    updateUndoRedo(st);
  }

  function chartColorClass(v) { return Number(v) > 0 ? "pos" : (Number(v) < 0 ? "neg" : "muted"); }

  function renderSymbolPage(st) {
    const h = st.header;
    const price = Number(h.price || 0);
    const low = Number(h.low_90d || 0), high = Number(h.high_90d || 0);
    const span = high - low || 1;
    const pos = Math.max(0, Math.min(100, ((price - low) / span) * 100));
    const chartTfs = (h.chart_timeframes && h.chart_timeframes.length)
      ? h.chart_timeframes
      : SCANNER_TFS.map(function (k) { return { key: k, label: TF_LABEL[k] || k }; });
    const analysisTfs = h.analysis_timeframes || SCANNER_TFS.map(function (k) { return { key: k, label: tfLabel(k) }; });
    const locked = !!h.locked;
    const showLive = !st.chartSettings || st.chartSettings.showRealtimePrice !== false;

    const analysisChips = analysisTfs.map(function (t) {
      return '<button class="seg ' + (st.analysisTf === t.key ? "active" : "") + '" data-analysis-tf="' + A.esc(t.key) + '">' + A.esc(TF_LABEL[t.key] || t.label) + "</button>";
    }).join("");
    const chartChips = chartTfs.map(function (t) {
      return '<button class="btn btn-sm btn-ghost ' + (st.chartTf === t.key ? "active" : "") + '" data-chart-tf="' + A.esc(t.key) + '">' + A.esc(TF_LABEL[t.key] || t.label) + "</button>";
    }).join("");

    setHTML(
      '<div class="page-head">' +
        '<div><div class="eyebrow">' + A.icon("chart") + ' صفحه نماد</div><div class="page-title">' + A.esc(h.symbol) + "</div>" +
        '<div class="page-sub">تحلیل ساختاری، نمودار قیمت و سیگنال‌های ' + A.esc(h.exchange || "") + "</div></div>" +
        '<div class="page-actions"><button class="btn btn-ghost" id="symBack">' + A.icon("arrow-left") + " بازگشت</button>" +
        '<button class="btn btn-primary" id="symRescan">' + A.icon("refresh") + " تحلیل مجدد</button></div>" +
      "</div>" +

      '<div class="card symbol-head">' +
        '<div class="symbol-id">' + symbolLogoHTML(h.symbol, h.logo_url) +
          '<div><div class="symbol-name">' + A.esc(h.symbol) + '<button class="icon-btn sm ' + (h.favorite ? "active" : "") + '" id="favSymbol" title="افزودن به علاقه‌مندی">' + A.icon("star") + "</button></div>" +
          '<div class="symbol-sub"><span class="pill blue">' + A.esc(h.exchange || "") + '</span><span>سواپ ' + A.esc(h.base || "") + '/USDT</span></div></div></div>' +
        '<div class="symbol-price-block"><div class="tiny muted" id="symPriceLabel">' + (showLive ? "قیمت لحظه‌ای" : "بسته‌شدن آخرین کندل") + '</div><div class="symbol-price" id="symLivePrice">' + (showLive ? A.fmtPrice(price) : "—") + '</div>' +
          '<div class="symbol-change ' + chartColorClass(h.change_pct) + '">' + (Number(h.change_pct) >= 0 ? A.icon("trend-up") : A.icon("trend-down")) + A.fmtPct(h.change_pct) + '<span class="tiny muted">۲۴ ساعته</span></div></div>' +
        '<div class="symbol-changes">' +
          '<div class="change-chip">۷ روزه <b class="' + chartColorClass(h.change_7d) + '">' + A.fmtPct(h.change_7d) + "</b></div>" +
          '<div class="change-chip">۳۰ روزه <b class="' + chartColorClass(h.change_30d) + '">' + A.fmtPct(h.change_30d) + "</b></div>" +
          '<div class="change-chip">۹۰ روزه <b class="' + chartColorClass(h.change_90d) + '">' + A.fmtPct(h.change_90d) + "</b></div>" +
        "</div>" +
        '<div class="symbol-actions"><a class="btn btn-sm btn-ghost" href="/search" data-link>' + A.icon("search") + " نماد دیگر</a>" +
        '<button class="btn btn-sm btn-soft" id="symScanBtn">' + A.icon("scanner") + " تحلیل ساختاری</button></div>" +
        '<div class="rangebar-card"><div class="row-between tiny muted"><span>کمترین ۹۰ روز: ' + A.fmtPrice(low) + "</span><span>بیشترین ۹۰ روز: " + A.fmtPrice(high) + "</span></div>" +
          '<div class="rangebar"><div class="rangebar-fill" style="width:' + pos + '%"></div><span class="rangebar-pin" style="inset-inline-start:' + pos + '%"></span></div>' +
          '<div class="rangebar-meta"><span>موقعیت قیمت فعلی: ' + A.toFa(pos.toFixed(0)) + "٪ از بازه</span><span>قیمت: " + A.fmtPrice(price) + "</span></div></div>" +
      "</div>" +

      '<div class="card chart-card mt-3">' +
        '<div class="card-head"><div><div class="card-title">' + A.icon("chart") + " نمودار قیمت</div>" +
          '<div class="card-sub" id="chartMeta">' + A.esc(h.symbol) + " • " + A.esc(h.exchange || "") + "</div></div>" +
          '<div class="card-actions"><div class="segmented" id="chartTypeSeg" style="height:34px">' +
            '<button class="seg ' + (st.chartType === "candles" ? "active" : "") + '" data-chart-type="candles">کندل</button>' +
            '<button class="seg ' + (st.chartType === "line" ? "active" : "") + '" data-chart-type="line">خطی</button></div></div></div>' +
        '<div class="card-body tight" style="padding-bottom:6px">' + chartNavHTML() +
          '<div class="chart-toolbar">' +
          '<div class="segmented scroll" id="chartTfSeg">' + chartChips + "</div>" +
          '<span class="tiny muted" id="zoomHint">زوم:</span>' +
          '<span id="zoomGroup">' +
          [["1m", "۱ ماه"], ["3m", "۳ ماه"], ["6m", "۶ ماه"], ["1y", "۱ سال"], ["all", "همه"]].map(function (z) {
            return '<button class="btn btn-sm btn-ghost" data-zoom="' + z[0] + '">' + z[1] + "</button>";
          }).join("") + "</span>" +
          '<button class="btn btn-sm btn-ghost" id="clearOverlay" hidden>' + A.icon("close") + " حذف لایه تحلیل</button>" +
        "</div>" +
        '<div class="layer-strip" id="layerStrip"></div></div>' +
        '<div class="chart-box large" id="priceChart" dir="ltr"><div class="loading-row"><span class="spinner"></span><span>در حال دریافت کندل‌ها…</span></div></div>' +
        '<div class="tf-section"><div class="tf-section-title">' + A.icon("clock") + " تایم‌فریم تحلیل جدول <span>با تغییر این گزینه، جدول پایین دوباره تحلیل می‌شود.</span></div>" +
          '<div class="segmented scroll" id="analysisTfSeg">' + analysisChips + "</div></div>" +
      "</div>" +

      '<div class="card mt-3 signal-table-wrap ' + (locked ? "locked" : "") + '" id="signalCard">' +
        '<div class="card-head"><div><div class="card-title">' + A.icon("activity") + " تحلیل‌های ساختاری</div>" +
          '<div class="card-sub" id="signalSub">در حال تحلیل نماد در تایم‌فریم ' + A.esc(tfLabel(st.analysisTf)) + "…</div></div>" +
          '<div class="card-actions"><span class="pill blue" id="signalCount">در حال دریافت</span>' +
          '<button class="btn btn-sm btn-ghost" id="reloadSignals">' + A.icon("refresh") + " به‌روزرسانی</button></div></div>" +
        '<div class="card-body tight" id="signalBody">' + A.loadingRow("تحلیل نماد ممکن است چند لحظه طول بکشد…") + "</div>" +
        '<div class="locked-overlay"><div class="locked-box"><div class="empty-icon">' + A.icon("lock") + "</div>" +
          "<b>برای مشاهده سیگنال‌های لحظه‌ای، اشتراک خود را تمدید کنید</b>" +
          "<p>با فعال‌سازی اشتراک تحلیل ساختاری، جدول تحلیل‌ها و نمودارهای تعاملی در دسترس شما قرار می‌گیرد.</p>" +
          '<a class="btn btn-primary" href="/subscribe" data-link>' + A.icon("wallet") + " خرید / تمدید اشتراک</a></div></div>" +
      "</div>"
    );
  }

  function bindSymbolPage(st) {
    const back = A.$("#symBack");
    if (back) back.addEventListener("click", function () { if (history.length > 1) history.back(); else A.go("/"); });
    const fav = A.$("#favSymbol");
    if (fav) fav.addEventListener("click", async function () {
      try {
        const res = await A.api("/api/favorites", { method: "POST", body: { kind: "symbol", value: st.header.symbol } });
        st.header.favorite = !!res.active;
        fav.classList.toggle("active", !!res.active);
        A.toast(res.active ? "نماد به علاقه‌مندی‌ها اضافه شد." : "نماد از علاقه‌مندی‌ها حذف شد.", "success");
      } catch (err) { A.toast(err.message, "error"); }
    });
    const rescan = A.$("#symRescan");
    if (rescan) rescan.addEventListener("click", function () { loadSignals(st, true); });
    const scanBtn = A.$("#symScanBtn");
    if (scanBtn) scanBtn.addEventListener("click", function () { A.go("/scanner"); });
    const reload = A.$("#reloadSignals");
    if (reload) reload.addEventListener("click", function () { loadSignals(st, true); });

    A.$$("#chartTypeSeg [data-chart-type]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        const type = btn.getAttribute("data-chart-type");
        if (type === st.chartType) return;
        st.chartType = type;
        A.$$("#chartTypeSeg [data-chart-type]").forEach(function (b) { b.classList.toggle("active", b === btn); });
        if (st.chartApi) st.chartApi.setChartType(type);
      });
    });

    A.$$("#chartTfSeg [data-chart-tf]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        if (st.locked) { A.toast("نمودار با اشتراک فعال باز می‌شود.", "error"); return; }
        const tf = btn.getAttribute("data-chart-tf");
        if (tf === st.chartTf) return;
        st.chartTf = tf;
        A.$$("#chartTfSeg [data-chart-tf]").forEach(function (b) { b.classList.toggle("active", b === btn); });
        loadChart(st);
      });
    });
    A.$$("#zoomGroup [data-zoom]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        applyZoom(st, btn.getAttribute("data-zoom"));
      });
    });
    const clear = A.$("#clearOverlay");
    if (clear) clear.addEventListener("click", function () {
      clearLayers(st);
      A.toast("لایه تحلیل از نمودار حذف شد. ترسیم‌های دستی شما باقی ماندند.", "success");
    });
    A.$$("#analysisTfSeg [data-analysis-tf]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        const tf = btn.getAttribute("data-analysis-tf");
        if (tf === st.analysisTf) return;
        st.analysisTf = tf;
        A.$$("#analysisTfSeg [data-analysis-tf]").forEach(function (b) { b.classList.toggle("active", b === btn); });
        loadSignals(st);
      });
    });

    A.$$("[data-chart-nav]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        const kind = btn.getAttribute("data-chart-nav");
        if (kind === "signal-prev") stepSignal(st, -1);
        else if (kind === "signal-next") stepSignal(st, 1);
        else if (kind === "symbol-prev") stepSymbol(st, -1);
        else if (kind === "symbol-next") stepSymbol(st, 1);
      });
    });
    const undo = A.$("#chartUndo");
    if (undo) undo.addEventListener("click", function () { if (st.chartApi) { st.chartApi.undo(); updateUndoRedo(st); } });
    const redo = A.$("#chartRedo");
    if (redo) redo.addEventListener("click", function () { if (st.chartApi) { st.chartApi.redo(); updateUndoRedo(st); } });
    const dropBtn = A.$("#stratDropBtn");
    if (dropBtn) dropBtn.addEventListener("click", function (e) {
      e.stopPropagation();
      toggleStratPanel(st);
    });
    const guideBtn = A.$("#chartGuideBtn");
    if (guideBtn) guideBtn.addEventListener("click", showChartGuide);
    const fsBtn = A.$("#chartFsBtn");
    if (fsBtn) fsBtn.addEventListener("click", function () { setFullscreen(st, !st.fullscreen); });
    const outside = function (e) {
      const panel = A.$("#stratDropPanel");
      if (panel && !panel.hidden && e.target.closest("#stratDrop") === null) panel.hidden = true;
    };
    document.addEventListener("click", outside);
    const keys = function (e) {
      if (e.key !== "Escape") return;
      const panel = A.$("#stratDropPanel");
      if (panel && !panel.hidden) { panel.hidden = true; return; }
      if (st.fullscreen) setFullscreen(st, false);
    };
    document.addEventListener("keydown", keys);
    renderChartNav(st);

    const resizeHandler = function () {
      if (!st.chartApi) return;
      try { st.chartApi.resize(); } catch (e) {}
    };
    window.addEventListener("resize", resizeHandler);
    setCleanup(function () {
      document.removeEventListener("click", outside);
      document.removeEventListener("keydown", keys);
      window.removeEventListener("resize", resizeHandler);
      stopPricePolling(st);
      if (st.fullscreen) {
        st.fullscreen = false;
        const card = A.$(".chart-card");
        if (card) card.classList.remove("fullscreen");
      }
      if (st.undoSub) { try { st.undoSub(); } catch (e) {} st.undoSub = null; }
      if (st.chartApi) { try { st.chartApi.destroy(); } catch (e) {} st.chartApi = null; }
      closeGuide();
    });
  }

  async function loadChart(st) {
    const box = A.$("#priceChart");
    if (!box) return;
    if (st.locked) { showChartLock(st); return; }
    if (!window.AbanVibe) { box.innerHTML = A.emptyState("chart", "نمودار در دسترس نیست", "کتابخانه نمودار بارگذاری نشد.", ""); return; }
    const seq = st.chartSeq = (st.chartSeq || 0) + 1;
    if (st.undoSub) { try { st.undoSub(); } catch (e) {} st.undoSub = null; }
    if (st.chartApi) { try { st.chartApi.destroy(); } catch (e) {} st.chartApi = null; }
    box.innerHTML = '<div class="loading-row"><span class="spinner"></span><span>در حال دریافت کندل‌ها…</span></div>';
    try {
      const data = await A.api("/api/symbol/" + encodeURIComponent(st.symbol) + "/candles?tf=" + encodeURIComponent(st.chartTf) + "&limit=900");
      if (seq !== st.chartSeq) return;
      const candles = data.candles || [];
      if (candles.length < 2) throw new Error("داده کندل کافی برای این تایم‌فریم موجود نیست.");
      st.lastClose = Number(candles[candles.length - 1][4]) || 0;
      const meta = A.$("#chartMeta");
      if (meta) meta.textContent = st.symbol + " • " + (data.exchange || st.header.exchange || "") + " • " + tfLabel(st.chartTf);
      const blockedLabels = {
        symbol: "جستجو و تغییر نماد داخل نمودار غیرفعال است؛ از جستجوی بالای صفحه استفاده کنید.",
        interval: "تایم‌فریم نمودار فقط از نوار بالای چارت تغییر می‌کند.",
        type: "نوع نمودار فقط از دکمه‌های بالای چارت تغییر می‌کند.",
        settings: "تنظیمات ظاهری نمودار توسط مدیر سامانه تعیین می‌شود.",
        dialog: "این پنجره در نمودار آبان غیرفعال است."
      };
      st.chartApi = window.AbanVibe.mount({
        host: box,
        symbol: st.symbol,
        exchange: data.exchange || st.header.exchange || "",
        logoUrl: st.header.logo_url || symbolLogoURL(st.header.base || st.symbol),
        tf: st.chartTf,
        chartType: st.chartType,
        candles: candles,
        settings: st.chartSettings,
        layers: st.layers,
        onBlocked: function (reason) { A.toast(blockedLabels[reason] || "این تغییر در نمودار غیرفعال است.", "info"); },
        onReady: function () { applyInitialView(st, seq); }
      });
      st.undoSub = st.chartApi.chart.drawings.changed.subscribe(function () { updateUndoRedo(st); });
      st.chartApi.setLayers(st.layers);
      applyInitialView(st, seq);
      const c = A.$("#clearOverlay"); if (c) c.hidden = !st.layers.length;
      updatePriceDisplay(st);
      renderChartNav(st);
      updateUndoRedo(st);
    } catch (err) {
      if (seq !== st.chartSeq) return;
      box.innerHTML = A.emptyState("chart", "نمودار در دسترس نیست", err.message || "دریافت کندل‌ها انجام نشد.", '<button class="btn btn-soft btn-sm" id="chartRetry">' + A.icon("refresh") + " تلاش دوباره</button>");
      const b = A.$("#chartRetry"); if (b) b.addEventListener("click", function () { loadChart(st); });
    }
  }

  /* نمای اولیه پس از آماده‌شدن کندل‌ها اعمال می‌شود؛ ممکن است داده چند فریم دیرتر برسد */
  function applyInitialView(st, seq) {
    let tries = 0;
    const attempt = function () {
      if (seq !== st.chartSeq || !st.chartApi || st.initialViewSeq === seq) return;
      let bars = [];
      try { bars = st.chartApi.chart.bars() || []; } catch (e) {}
      if (!bars.length) {
        if (++tries < 80) window.setTimeout(attempt, 50);
        return;
      }
      st.initialViewSeq = seq;
      const focused = st.layers.find(function (l) { return l.id === st.focusedLayer; });
      if (focused) focusRange(st.chartApi, focused.signal);
      else applyZoom(st, "6m");
      updateUndoRedo(st);
    };
    attempt();
  }

  function applyZoom(st, key) {
    if (!st.chartApi) return;
    try { st.chartApi.zoom(key); } catch (e) {}
    A.$$("#zoomGroup [data-zoom]").forEach(function (b) {
      b.classList.toggle("active", b.getAttribute("data-zoom") === key);
    });
  }

  async function loadSignals(st, force) {
    const tf = st.analysisTf;
    const seq = st.signalsSeq = (st.signalsSeq || 0) + 1;
    st.loadingSignals = true;
    const body = A.$("#signalBody");
    const sub = A.$("#signalSub");
    const count = A.$("#signalCount");
    if (body) body.innerHTML = A.loadingRow("در حال تحلیل " + st.symbol + " در تایم‌فریم " + tfLabel(tf) + "… ممکن است چند لحظه طول بکشد.");
    if (count) { count.textContent = "در حال تحلیل"; count.className = "pill amber"; }
    if (sub) sub.textContent = "تحلیل با استراتژی‌های فعال سامانه انجام می‌شود.";
    if (!force && st.signalsCache[tf]) {
      st.signals = st.signalsCache[tf];
      st.loadingSignals = false;
      renderSignals(st);
      return;
    }
    try {
      const data = await A.api("/api/symbol/" + encodeURIComponent(st.symbol) + "/signals?timeframe=" + encodeURIComponent(tf) + "&page=1&page_size=250");
      if (seq !== st.signalsSeq) return;
      st.signals = data;
      st.signalsCache[tf] = data;
      st.page = 1;
      const card = A.$("#signalCard");
      if (card) card.classList.toggle("locked", !!data.locked);
      st.locked = !!data.locked;
      if (st.locked) showChartLock(st);
      else if (!st.chartApi) loadChart(st);
      renderSignals(st);
    } catch (err) {
      if (seq !== st.signalsSeq) return;
      if (body) body.innerHTML = errorCard(err, "sigRetry");
      const b = A.$("#sigRetry"); if (b) b.addEventListener("click", function () { loadSignals(st, true); });
      if (count) { count.textContent = "خطا"; count.className = "pill red"; }
    } finally {
      if (seq === st.signalsSeq) st.loadingSignals = false;
    }
  }

  function renderSignals(st) {
    const data = st.signals;
    const body = A.$("#signalBody");
    const sub = A.$("#signalSub");
    const count = A.$("#signalCount");
    const card = A.$("#signalCard");
    if (!data || !body) return;
    if (data.locked) {
      if (card) card.classList.add("locked");
      if (count) { count.textContent = "قفل"; count.className = "pill amber"; }
      if (sub) sub.textContent = "برای مشاهده تحلیل‌ها، اشتراک تحلیل ساختاری را فعال کنید.";
      body.innerHTML = A.skeletonRows(3);
      return;
    }
    if (card) card.classList.remove("locked");
    const items = data.results || [];
    const total = data.total || items.length;
    if (count) { count.textContent = A.toFa(total) + " تحلیل"; count.className = "pill " + (total ? "green" : "gray"); }
    if (sub) sub.textContent = "تایم‌فریم " + tfLabel(st.analysisTf) + " • صرافی " + (data.exchange || st.header.exchange || "") + " • " + A.toFa(Number(data.took || 0).toFixed(1)) + " ثانیه";
    if (!items.length) {
      body.innerHTML = A.emptyState("activity", "تحلیلی یافت نشد", "در این تایم‌فریم، هیچ استراتژی فعالی در آخرین کندل‌ها تأیید نشده است. تایم‌فریم دیگری را امتحان کنید.", "");
      renderChartNav(st);
      return;
    }
    const pages = Math.max(1, Math.ceil(items.length / st.perPage));
    if (st.page > pages) st.page = pages;
    const start = (st.page - 1) * st.perPage;
    const slice = items.slice(start, start + st.perPage);
    let rows = "";
    slice.forEach(function (r, i) {
      const s = st.keyMap.get(r.strategy_key);
      const guide = s ? A.safeImage(s.guide_image) : "";
      const lid = layerId(r);
      rows += '<tr class="selectable' + (st.selectedRow === lid ? " row-active" : "") + '" data-sig-row="' + A.esc(lid) + '">' +
        '<td><div class="cell-main">' + A.esc(r.symbol) + '</div><div class="cell-sub">' + A.esc(r.exchange || "") + "</div></td>" +
        '<td class="tiny nowrap">' + A.esc(r.date || "-") + "</td>" +
        "<td>" + A.esc(tfLabel(r.timeframe)) + "</td>" +
        '<td class="result-strategy"><div class="cell-main">' + A.esc(r.strategy || "-") + "</div>" +
          (r.matches && r.matches.length ? '<div class="match-list">' + r.matches.map(function (m) { return '<span class="pill ' + (m.signal_type === "حمایت" ? "green" : "red") + '">' + A.esc(m.title) + "</span>"; }).join("") + "</div>" : "") + "</td>" +
        "<td>" + A.signalPill(r.signal_type) + "</td>" +
        "<td>" + A.directionPill(r.direction) + "</td>" +
        '<td class="num">' + A.esc(A.fmtPrice(r.price)) + "</td>" +
        '<td><div class="cell-actions">' +
          (r.id ? '<button class="icon-btn sm ' + (r.is_favorite ? "active" : "") + '" data-sig-fav="' + A.esc(r.id) + '" title="نشان کردن سیگنال">' + A.icon("heart") + "</button>" : "") +
          (guide ? '<button class="icon-btn sm" data-sig-guide="' + A.esc(r.strategy_key) + '" title="راهنما">' + A.icon("help") + "</button>" : "") +
          '<button class="icon-btn sm" data-sig-chart="' + (start + i) + '" title="نمایش روی نمودار">' + A.icon("chart") + "</button>" +
        "</div></td></tr>";
    });
    body.innerHTML = '<div class="table-wrap"><table class="table table-fix" style="min-width:960px"><thead><tr><th>نماد</th><th>تاریخ</th><th>تایم‌فریم</th><th>استراتژی</th><th>نوع</th><th>جهت</th><th>قیمت</th><th></th></tr></thead><tbody>' + rows + "</tbody></table></div>" +
      (items.length > st.perPage ? '<div class="scan-pagination"><span class="tiny muted">نمایش ' + A.toFa(start + 1) + " تا " + A.toFa(Math.min(start + st.perPage, items.length)) + " از " + A.toFa(total) + "</span>" + pagerHTML(st.page, pages) + "</div>" : "");
    body.querySelectorAll("[data-sig-chart]").forEach(function (btn) {
      btn.addEventListener("click", async function (e) {
        e.stopPropagation();
        const row = items[parseInt(btn.getAttribute("data-sig-chart"), 10)];
        if (!row) return;
        await toggleLayer(st, row, !st.layers.some(function (l) { return l.id === layerId(row); }));
        if (st.layers.some(function (l) { return l.id === layerId(row); })) {
          A.toast("لایه استراتژی «" + (row.strategy || "") + "» روی نمودار نمایش داده شد.", "success");
        } else {
          A.toast("لایه استراتژی «" + (row.strategy || "") + "» از نمودار حذف شد.", "success");
        }
      });
    });
    body.querySelectorAll("[data-sig-row]").forEach(function (tr) {
      tr.addEventListener("click", async function (e) {
        if (e.target.closest("button, a, input, label")) return;
        const id = tr.getAttribute("data-sig-row");
        const row = items.filter(function (x) { return layerId(x) === id; })[0];
        if (!row) return;
        if (!row.overlay) { A.toast("این تحلیل لایه‌ی قابل نمایشی ندارد.", "error"); return; }
        await toggleLayer(st, row);
      });
    });
    body.querySelectorAll("[data-sig-guide]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        const s = st.keyMap.get(btn.getAttribute("data-sig-guide"));
        if (s) showGuide(btn, s);
      });
    });
    body.querySelectorAll("[data-sig-fav]").forEach(function (btn) {
      btn.addEventListener("click", async function () {
        try {
          const res = await A.api("/api/favorites", { method: "POST", body: { kind: "signal", value: btn.getAttribute("data-sig-fav") } });
          btn.classList.toggle("active", !!res.active);
          A.toast(res.active ? "سیگنال نشان شد." : "نشان سیگنال برداشته شد.", "success");
        } catch (err) { A.toast(err.message, "error"); }
      });
    });
    const pager = body.querySelector(".scan-pagination");
    if (pager) bindPager(pager, function (p) {
      st.page = p; renderSignals(st);
      const card = A.$("#signalCard"); if (card) card.scrollIntoView({ behavior: "smooth", block: "start" });
    });
    renderChartNav(st);
  }

  /* ================================================================
     تاریخچه جستجو
     ================================================================ */
  async function viewHistory(page) {
    const p = Math.max(1, parseInt(page, 10) || 1);
    A.setPage("سابقه جستجو", "اسکن‌های قبلی و اجرای مجدد آن‌ها");
    A.markNav("history");
    setHTML('<div class="card pad"><div class="loading-row"><span class="spinner"></span><span>در حال دریافت تاریخچه…</span></div></div>');
    let data;
    try { data = await A.api("/api/history?page=" + p + "&page_size=10"); }
    catch (err) {
      setHTML(errorCard(err, "histRetry"));
      const b = A.$("#histRetry"); if (b) b.addEventListener("click", function () { viewHistory(p); });
      return;
    }
    const items = data.items || [];
    const total = data.total || 0;
    const pages = Math.max(1, Math.ceil(total / (data.page_size || 10)));
    let rows = "";
    items.forEach(function (h) {
      const filters = h.filters || {};
      const mode = filters.mode === "all" ? "همه با هم" : "هر سیگنال";
      const symbolsMode = h.symbols_mode === "single" ? (filters.symbol || "تک نماد") : (h.symbols_mode === "favorites" ? "علاقه‌مندی‌ها" : "همه نمادها");
      rows += '<tr>' +
        '<td><div class="cell-main">' + A.esc(h.created_at) + '</div><div class="cell-sub">#' + A.toFa(h.id) + "</div></td>" +
        "<td>" + A.esc(tfLabel(h.timeframe)) + "</td>" +
        "<td>" + (Number(h.depth) === 0 ? "لحظه‌ای" : A.toFa(h.depth) + " کندل") + "</td>" +
        "<td>" + A.esc(mode) + "</td>" +
        '<td><div class="cell-main">' + A.esc(symbolsMode) + '</div><div class="cell-sub">' + A.toFa(h.strategy_count || 0) + " استراتژی</div></td>" +
        "<td>" + A.pill(A.toFa(h.result_count || 0) + " نتیجه", h.result_count ? "green" : "gray") + "</td>" +
        '<td><div class="cell-actions">' +
          '<button class="btn btn-sm btn-soft" data-rerun="' + A.esc(h.id) + '">' + A.icon("refresh") + " اجرای مجدد</button>" +
          '<button class="icon-btn sm danger" data-del="' + A.esc(h.id) + '" title="حذف">' + A.icon("trash") + "</button>" +
        "</div></td></tr>";
    });
    setHTML(
      '<div class="page-head"><div><div class="eyebrow">' + A.icon("history") + ' آرشیو تحلیل</div><div class="page-title">سابقه جستجو</div>' +
        '<div class="page-sub">تمام اسکن‌های شما با تنظیمات کامل ذخیره می‌شوند.</div></div>' +
        '<div class="page-actions"><span class="date-chip">' + A.icon("layers") + A.toFa(total) + " جستجو</span>" +
        (items.length ? '<button class="btn btn-danger" id="clearHistory">' + A.icon("trash") + " پاک کردن تاریخچه</button>" : "") + "</div></div>" +
      '<div class="card"><div class="card-body tight">' +
        (items.length ? '<div class="table-wrap"><table class="table table-fix" style="min-width:880px"><thead><tr><th>تاریخ</th><th>تایم‌فریم</th><th>عمق</th><th>منطق</th><th>دامنه</th><th>نتیجه</th><th></th></tr></thead><tbody>' + rows + "</tbody></table></div>" +
          '<div class="history-pagination mt-2"><span class="tiny muted">صفحه ' + A.toFa(p) + " از " + A.toFa(pages) + "</span>" + pagerHTML(p, pages) + "</div>"
          : A.emptyState("history", "تاریخچه‌ای وجود ندارد", "اولین اسکن خود را اجرا کنید تا تنظیمات آن برای اجرای مجدد ذخیره شود.", '<a class="btn btn-primary btn-sm" href="/scanner" data-link>شروع اسکن</a>')) +
      "</div></div>"
    );
    A.$$("[data-rerun]").forEach(function (btn) {
      btn.addEventListener("click", function () { A.go("/scanner?rerun=" + encodeURIComponent(btn.getAttribute("data-rerun"))); });
    });
    A.$$("[data-del]").forEach(function (btn) {
      btn.addEventListener("click", async function () {
        const ok = await A.confirm({ title: "حذف جستجو", message: "این مورد از تاریخچه حذف می‌شود. امکان بازگشت وجود ندارد.", confirmText: "حذف", danger: true });
        if (!ok) return;
        try { await A.api("/api/history/" + encodeURIComponent(btn.getAttribute("data-del")), { method: "DELETE" }); A.toast("جستجو حذف شد.", "success"); viewHistory(p); }
        catch (err) { A.toast(err.message, "error"); }
      });
    });
    const clear = A.$("#clearHistory");
    if (clear) clear.addEventListener("click", async function () {
      const ok = await A.confirm({ title: "پاک کردن تاریخچه", message: "تمام جستجوهای شما حذف می‌شوند. این عملیات قابل بازگشت نیست.", confirmText: "پاک کن", danger: true });
      if (!ok) return;
      try { await A.api("/api/history", { method: "DELETE" }); A.toast("تاریخچه پاک شد.", "success"); viewHistory(1); }
      catch (err) { A.toast(err.message, "error"); }
    });
    const pg = viewEl().querySelector(".history-pagination");
    if (pg) bindPager(pg, function (n) { viewHistory(n); });
  }

  /* ================================================================
     جستجوی تک نماد
     ================================================================ */
  async function viewSearch() {
    A.setPage("جستجوی تک نماد", "نماد مورد نظر را پیدا کنید و وارد صفحه تحلیل شوید");
    A.markNav("search");
    const params = new URLSearchParams(location.search);
    const initial = params.get("q") || "";
    setHTML(
      '<div class="search-hero"><div class="eyebrow" style="justify-content:center">' + A.icon("compass") + " کاوش بازار</div>" +
        "<h1>نماد مورد نظر را پیدا کنید</h1><p>با تایپ نام نماد، سریع‌ترین مسیر به نمودار و تحلیل ساختاری را پیدا کنید.</p>" +
        '<div class="searchbox"><span>' + A.icon("search") + '</span><input id="symbolSearch" placeholder="مثلاً BTCUSDT یا ETH…" autocomplete="off" value="' + A.esc(initial) + '">' +
        '<button class="clear" id="symbolClear" ' + (initial ? "" : "hidden") + ">" + A.icon("close") + "</button></div></div>" +
      '<div class="card"><div class="card-head"><div><div class="card-title">' + A.icon("grid") + ' <span id="searchTitle">نمادهای پیشنهادی</span></div>' +
        '<div class="card-sub">دسترسی سریع به نمادهای پرکاربرد صرافی‌های فعال</div></div><div class="card-actions"><span class="pill blue" id="searchCount">در حال دریافت</span></div></div>' +
        '<div class="card-body" id="searchResults">' + A.loadingRow("در حال دریافت فهرست نمادها…") + "</div></div>"
    );
    const input = A.$("#symbolSearch"), clear = A.$("#symbolClear");
    let seq = 0;
    async function searchNow(q) {
      const my = ++seq;
      const title = A.$("#searchTitle"), count = A.$("#searchCount"), box = A.$("#searchResults");
      if (!box) return;
      if (!q) {
        if (title) title.textContent = "نمادهای پیشنهادی";
        box.innerHTML = A.loadingRow("در حال دریافت فهرست نمادها…");
        if (count) { count.textContent = "در حال دریافت"; count.className = "pill blue"; }
        try {
          const data = await A.api("/api/symbols/quick?limit=24");
          if (my !== seq) return;
          const items = data.results || [];
          if (!items.length) {
            box.innerHTML = A.emptyState("alert", "فهرست نمادها در دسترس نیست", "ارتباط سرور با صرافی‌ها برقرار نشد؛ چند لحظه بعد دوباره تلاش کنید.") +
              '<div class="form-actions"><button class="btn btn-soft" id="quickRetry">تلاش دوباره</button></div>';
            const b = A.$("#quickRetry"); if (b) b.addEventListener("click", function () { searchNow(""); });
            if (count) { count.textContent = "در دسترس نیست"; count.className = "pill amber"; }
            return;
          }
          renderTiles(items);
        } catch (err) {
          if (my !== seq) return;
          box.innerHTML = errorCard(err, "quickRetry");
          const b = A.$("#quickRetry"); if (b) b.addEventListener("click", function () { searchNow(""); });
          if (count) { count.textContent = "خطا"; count.className = "pill red"; }
        }
        return;
      }
      if (title) title.textContent = "نتایج جستجو برای «" + q + "»";
      box.innerHTML = A.loadingRow("در حال جستجوی نمادها…");
      if (count) { count.textContent = "در حال جستجو"; count.className = "pill amber"; }
      try {
        const data = await A.api("/api/symbols/search?q=" + encodeURIComponent(q) + "&limit=36");
        if (my !== seq) return;
        const items = data.results || [];
        if (count) { count.textContent = A.toFa(items.length) + " نماد"; count.className = "pill " + (items.length ? "green" : "gray"); }
        if (!items.length) { box.innerHTML = A.emptyState("search", "نمادی پیدا نشد", "املای نماد را بررسی کنید یا نام کوچک‌تری وارد کنید."); return; }
        renderTiles(items);
      } catch (err) {
        if (my !== seq) return;
        box.innerHTML = errorCard(err, "searchRetry");
        const b = A.$("#searchRetry"); if (b) b.addEventListener("click", function () { searchNow(input.value.trim()); });
        if (count) { count.textContent = "خطا"; count.className = "pill red"; }
      }
    }
    function renderTiles(items) {
      const box = A.$("#searchResults"), count = A.$("#searchCount");
      if (!box) return;
      if (count) { count.textContent = A.toFa(items.length) + " نماد"; count.className = "pill " + (items.length ? "green" : "gray"); }
      if (!items.length) { box.innerHTML = A.emptyState("search", "نمادی پیدا نشد", ""); return; }
      box.innerHTML = '<div class="quick-grid">' + items.map(function (it) {
        return '<div class="symbol-tile" data-symbol="' + A.esc(it.symbol) + '"><span class="coin' + A.coinClass(it.symbol) + '">' + A.esc(A.coinLetters(it.symbol)) + "</span>" +
          '<div class="flex-1"><div class="symbol-tile-name">' + A.esc(it.symbol) + '</div><div class="symbol-tile-sub">' + A.esc(it.exchange || "") + "</div></div>" + A.icon("chevron-left") + "</div>";
      }).join("") + "</div>";
      box.querySelectorAll("[data-symbol]").forEach(function (tile) {
        tile.addEventListener("click", function () { openSymbol(tile.getAttribute("data-symbol")); });
      });
    }
    input.addEventListener("input", A.debounce(function () {
      const q = input.value.trim();
      if (clear) clear.hidden = !q;
      const url = new URL(location.href);
      if (q) url.searchParams.set("q", q); else url.searchParams.delete("q");
      history.replaceState({}, "", url.pathname + url.search);
      searchNow(q);
    }, 280));
    if (clear) clear.addEventListener("click", function () {
      input.value = ""; clear.hidden = true;
      history.replaceState({}, "", "/search");
      input.focus(); searchNow("");
    });
    input.addEventListener("keydown", function (e) {
      if (e.key === "Enter") {
        e.preventDefault();
        const first = viewEl().querySelector("[data-symbol]");
        if (first) openSymbol(first.getAttribute("data-symbol"));
      }
    });
    searchNow(initial);
  }

  /* ================================================================
     تنظیمات
     ================================================================ */
  async function viewSettings() {
    A.setPage("تنظیمات", "اشتراک، اطلاع‌رسانی، صرافی‌ها و پیش‌فرض‌های اسکن");
    A.markNav("settings");
    setHTML('<div class="card pad"><div class="loading-row"><span class="spinner"></span><span>در حال دریافت تنظیمات…</span></div></div>');
    let data;
    try { data = await A.api("/api/settings"); }
    catch (err) {
      setHTML(errorCard(err, "setRetry"));
      const b = A.$("#setRetry"); if (b) b.addEventListener("click", viewSettings);
      return;
    }
    const s = data.settings || {};
    const user = data.user || {};
    const sub = A.planStatus(data.subscription || user.subscription);
    const exchanges = data.exchanges || [];
    const favorites = data.favorites || { strategy: [], symbol: [] };
    state.user = user; state.settings = data;
    A.fillUser(user);
    const exAll = s.exchanges_all !== false;
    const selectedEx = new Set(s.scan_exchanges || []);
    if (exAll) exchanges.forEach(function (e) { selectedEx.add(e.slug); });
    const tfButtons = SCANNER_TFS.map(function (tf) {
      return '<button class="seg ' + (s.scan_timeframe === tf ? "active" : "") + '" data-set-tf="' + tf + '">' + A.esc(tfLabel(tf)) + "</button>";
    }).join("");
    const depthChips = DEPTHS.map(function (d) {
      return '<button class="chip ' + (Number(s.scan_depth) === d.value ? "active" : "") + '" data-set-depth="' + d.value + '">' + A.esc(d.label) + "</button>";
    }).join("");
    const exchChips = exchanges.map(function (e) {
      return '<button class="chip ' + (selectedEx.has(e.slug) ? "active" : "") + (exAll ? " disabled" : "") + '" data-set-exch="' + A.esc(e.slug) + '"><span class="dot ' + (selectedEx.has(e.slug) ? "green" : "muted") + '"></span>' + A.esc(e.name) + "</button>";
    }).join("");
    const favSymbols = (favorites.symbol || []).map(function (sym) {
      return '<span class="fav-chip"><a href="/symbol/' + encodeURIComponent(sym) + '" data-link>' + A.esc(sym) + "</a><button data-unfav-symbol=\"" + A.esc(sym) + '" title="حذف">' + A.icon("close") + "</button></span>";
    }).join("");
    const favStrategies = (favorites.strategy || []).map(function (key) {
      return '<span class="fav-chip">' + A.esc(key) + '<button data-unfav-strategy="' + A.esc(key) + '" title="حذف">' + A.icon("close") + "</button></span>";
    }).join("");
    let settingsSymbolsMode = s.scan_symbols_mode || "all";
    if ((settingsSymbolsMode === "favorites" && !(favorites.symbol || []).length) ||
        (settingsSymbolsMode === "single" && !s.scan_symbol)) {
      settingsSymbolsMode = "all";
    }
    const tgLinked = Number(user.uid) > 0;

    setHTML(
      '<div class="page-head"><div><div class="eyebrow">' + A.icon("settings") + ' پیکربندی حساب</div><div class="page-title">تنظیمات</div>' +
        '<div class="page-sub">تجربه اسکن و اطلاع‌رسانی خود را شخصی‌سازی کنید.</div></div>' +
        '<div class="page-actions"><a class="btn btn-ghost" href="/scanner" data-link>' + A.icon("scanner") + ' رفتن به اسکنر</a></div></div>' +

      '<div class="settings-grid"><div class="stack">' +

        '<div class="card"><div class="card-head bordered"><div><div class="card-title">' + A.icon("user") + ' حساب کاربری</div>' +
          '<div class="card-sub">اطلاعات اتصال شما در آبان ترید</div></div></div>' +
          '<div class="card-body"><div class="row" style="gap:14px;flex-wrap:wrap">' +
            '<div class="avatar lg">' + A.esc(A.initials(user.name)) + "</div>" +
            '<div class="flex-1"><div style="font-size:1.02rem;font-weight:700">' + A.esc(user.name || "کاربر آبان") + "</div>" +
            '<div class="tiny muted">' + (user.username ? "@" + A.esc(user.username) : "کاربر وب‌اپ") + " • شناسه " + A.toFa(user.uid || 0) + "</div></div>" +
            (user.is_admin ? '<span class="pill violet">مدیر</span>' : (sub.active ? '<span class="pill green">اشتراک فعال</span>' : '<span class="pill amber">بدون اشتراک</span>')) +
          '</div><div class="divider"></div><div class="detail-grid">' +
            '<div class="detail-item"><span>شماره تلفن</span><b class="mono">' + A.esc(user.phone || "-") + "</b></div>" +
            '<div class="detail-item"><span>تاریخ عضویت</span><b>' + A.esc(user.joined_at || "-") + "</b></div>" +
            '<div class="detail-item"><span>وضعیت حساب</span><b>' + (user.verified ? "تأیید شده" : "در انتظار تأیید") + "</b></div>" +
            '<div class="detail-item"><span>دسترسی وب‌اپ</span><b>فعال</b></div>' +
          "</div></div></div>" +

        (tgLinked
          ? '<div class="card"><div class="card-head bordered"><div><div class="card-title">' + A.icon("bell") + ' اطلاع‌رسانی تلگرام</div>' +
            '<div class="card-sub">سیگنال‌های ساختاری تازه، فقط به حساب تلگرام خودِ شما ارسال می‌شوند</div></div>' +
            '<div class="card-actions"><label class="switch"><input type="checkbox" id="notifyOn" ' + (s.notify_on ? "checked" : "") + '><span class="switch-track"></span><span class="switch-text">فعال</span></label></div></div>' +
            '<div class="card-body" id="notifyFields">' +
              '<div class="form-grid">' +
                "<div class=\"field\"><label class=\"label\">راهنمای متغیرها</label><div class=\"hint\" style=\"margin-top:8px\">" + "{symbol} نام نماد، {strategy} استراتژی، {type} نوع سیگنال، {timeframe} تایم‌فریم، {date} تاریخ و {price} قیمت." + "</div></div>" +
                '<div class="field full"><label class="label">قالب پیام</label><textarea class="textarea" id="notifyTemplate" rows="5" spellcheck="false">' + A.esc(s.notify_template || "") + "</textarea></div>" +
              "</div>" +
              '<div class="form-actions"><button class="btn btn-primary" id="saveNotify">' + A.icon("check") + " ذخیره تنظیمات اطلاع‌رسانی</button></div>" +
            "</div></div>"
          : '<div class="card"><div class="card-head bordered"><div><div class="card-title">' + A.icon("bell") + ' اطلاع‌رسانی تلگرام</div>' +
            '<div class="card-sub">این حساب از طریق وب‌اپ ساخته شده و گفت‌وگوی تلگرامی ندارد</div></div></div>' +
            '<div class="card-body tight"><div class="hint">برای دریافت خودکار سیگنال‌ها در تلگرام، یک بار وارد ربات شوید و «شروع» را بزنید؛ سپس همین تنظیمات برای شما فعال می‌شود.</div>' +
            '<a class="btn btn-ghost btn-block mt-2" href="' + A.esc(botLinkFor(user)) + '" target="_blank" rel="noopener">' + A.icon("send") + " ورود به ربات</a></div></div>") +

        '<div class="card"><div class="card-head bordered"><div><div class="card-title">' + A.icon("scanner") + ' پیش‌فرض‌های اسکن</div>' +
          '<div class="card-sub">این مقادیر هنگام باز شدن اسکنر به‌صورت خودکار اعمال می‌شوند</div></div></div>' +
          '<div class="card-body">' +
            '<div class="setting-block" style="margin-top:0;padding-top:0;border:none">' +
              '<div class="filter-label">' + A.icon("clock") + ' تایم‌فریم</div><div class="segmented scroll" id="setTfSeg">' + tfButtons + "</div></div>" +
            '<div class="setting-block"><div class="filter-label">' + A.icon("filter") + ' عمق بررسی</div><div class="chips" id="setDepthChips">' + depthChips + "</div></div>" +
            '<div class="setting-block"><div class="filter-label">' + A.icon("layers") + ' منطق پیش‌فرض</div><div class="segmented" id="setModeSeg">' +
              '<button class="seg ' + (s.scan_mode !== "all" ? "active" : "") + '" data-set-mode="any">هر سیگنال</button>' +
              '<button class="seg ' + (s.scan_mode === "all" ? "active" : "") + '" data-set-mode="all">همه با هم</button></div></div>' +
            '<div class="setting-block"><div class="filter-label">' + A.icon("grid") + ' دامنه نمادها</div><div class="segmented" id="setSymbolsSeg">' +
              SYMBOL_MODES.map(function (m) {
                return '<button class="seg ' + (settingsSymbolsMode === m.value ? "active" : "") + '" data-set-symbols="' + m.value + '">' + A.esc(m.label) + "</button>";
              }).join("") + "</div>" +
              '<div class="mt-1" id="setSingleSymbolWrap" ' + (settingsSymbolsMode !== "single" ? "hidden" : "") + '>' +
                '<div class="searchbox"><span>' + A.icon("search") + "</span>" +
                '<input id="setSingleSymbol" placeholder="نماد را وارد کنید؛ مثل BTCUSDT" value="' + A.esc(s.scan_symbol || "") + '" autocomplete="off"></div>' +
              "</div></div>" +
            '<div class="setting-block"><div class="row-between wrap"><div class="filter-label" style="margin:0">' + A.icon("exchange") + " صرافی‌های من</div>" +
              '<label class="switch"><input type="checkbox" id="setExchAll" ' + (exAll ? "checked" : "") + '><span class="switch-track"></span><span class="switch-text">فرقی نمی‌کند</span></label></div>' +
              '<div class="chips mt-1" id="setExchChips">' + exchChips + "</div>" +
              '<div class="hint mt-1">اگر «فرقی نمی‌کند» روشن باشد، همه صرافی‌های فعال با اولویت پیش‌فرض بررسی می‌شوند.</div></div>' +
            '<div class="form-actions"><button class="btn btn-primary" id="saveScanDefaults">' + A.icon("check") + " ذخیره پیش‌فرض‌های اسکن</button></div>" +
          "</div></div>" +

        '<div class="card"><div class="card-head bordered"><div><div class="card-title">' + A.icon("star") + ' علاقه‌مندی‌ها</div>' +
          '<div class="card-sub">نمادها و استراتژی‌های منتخب شما</div></div></div>' +
          '<div class="card-body"><div class="setting-title">' + A.icon("chart") + " نمادها</div>" +
            (favSymbols ? '<div class="fav-chips mt-1" id="favSymbols">' + favSymbols + "</div>" : '<div class="hint">هنوز نمادی را نشان نکرده‌اید. از صفحه نماد، ستاره را بزنید.</div>') +
            '<div class="divider"></div><div class="setting-title">' + A.icon("layers") + " استراتژی‌ها</div>" +
            (favStrategies ? '<div class="fav-chips mt-1" id="favStrategies">' + favStrategies + "</div>" : '<div class="hint">هنوز استراتژی‌ای را نشان نکرده‌اید. در اسکنر، ستاره کنار استراتژی را بزنید.</div>') +
          "</div></div>" +

      "</div>" +

      '<div class="settings-side">' +
        '<div class="card"><div class="card-head"><div><div class="card-title">' + A.icon("sparkles") + ' اشتراک تحلیل ساختاری</div></div></div>' +
          '<div class="card-body tight">' +
            '<div class="mini-plan"><div class="icon-wrap">' + A.icon("sparkles") + "</div><div><b>" + (user.is_admin ? "دسترسی نامحدود" : (sub.active ? sub.label : "اشتراک فعال نیست")) + "</b>" +
              "<span>" + (sub.active && !user.is_admin ? "پایان: " + A.esc(sub.until) : (user.is_admin ? "مدیر اکوسیستم" : "برای دسترسی کامل فعال کنید")) + "</span></div></div>" +
            '<div class="plan-steps"><div class="plan-step">وارد صفحه «خرید اشتراک» شوید و شبکه پرداخت را انتخاب کنید.</div>' +
              '<div class="plan-step">مبلغ را به کیف پول اعلام‌شده واریز و رسید یا هش تراکنش را ثبت کنید.</div>' +
              '<div class="plan-step">پس از تأیید مدیر، اشتراک و دسترسی وب‌اپ فعال می‌شود.</div></div>' +
            '<a class="btn btn-primary btn-block mt-2" href="/subscribe" data-link>' + A.icon("wallet") + (sub.active ? "تمدید اشتراک" : "خرید اشتراک") + "</a>" +
            '<a class="btn btn-ghost btn-block mt-1" href="' + A.esc(botLinkFor(user)) + '" target="_blank" rel="noopener">' + A.icon("send") + " ورود به ربات</a>" +
          "</div></div>" +

        '<div class="card"><div class="card-head"><div><div class="card-title">' + A.icon("exchange") + ' صرافی‌ها</div></div></div>' +
          '<div class="card-body tight"><div class="server-grid">' + exchanges.map(function (e) {
            return '<div class="server-pill"><span class="dot green"></span><span>' + A.esc(e.name) + '</span><b class="pos">فعال</b></div>';
          }).join("") + "</div>" +
          '<div class="hint mt-2">داده هر نماد فقط از یک صرافی (با بالاترین اولویت) خوانده می‌شود و ترکیب نمی‌شود.</div></div></div>' +

        '<div class="card"><div class="card-head"><div><div class="card-title">' + A.icon("shield") + ' امنیت و نشست</div></div></div>' +
          '<div class="card-body tight"><div class="list">' +
            '<div class="list-item"><div class="list-icon green">' + A.icon("shield") + '</div><div class="list-body"><div class="list-title">نشست محافظت‌شده</div><div class="list-sub">کوکی HttpOnly با اعتبار ' + A.toFa(30) + " روز</div></div></div>" +
            '<div class="list-item"><div class="list-icon amber">' + A.icon("lock") + '</div><div class="list-body"><div class="list-title">تغییر رمز</div><div class="list-sub">از طریق ربات یا پنل مدیریت</div></div></div>' +
          "</div>" +
          '<button class="btn btn-danger btn-block mt-2" id="logoutBtn">' + A.icon("logout") + " خروج از حساب</button></div></div>" +

      "</div></div>"
    );

    const st = { tf: s.scan_timeframe || "1h", depth: Number(s.scan_depth || 10), mode: s.scan_mode || "any", symbolsMode: settingsSymbolsMode, symbol: s.scan_symbol || "", exAll: exAll, exSet: selectedEx };
    function bindSet() {
      A.$$("[data-set-tf]").forEach(function (b) {
        b.addEventListener("click", function () {
          st.tf = b.getAttribute("data-set-tf");
          A.$$("[data-set-tf]").forEach(function (x) { x.classList.toggle("active", x === b); });
        });
      });
      A.$$("[data-set-depth]").forEach(function (b) {
        b.addEventListener("click", function () {
          st.depth = Number(b.getAttribute("data-set-depth"));
          A.$$("[data-set-depth]").forEach(function (x) { x.classList.toggle("active", x === b); });
        });
      });
      A.$$("[data-set-mode]").forEach(function (b) {
        b.addEventListener("click", function () {
          st.mode = b.getAttribute("data-set-mode");
          A.$$("[data-set-mode]").forEach(function (x) { x.classList.toggle("active", x === b); });
        });
      });
      A.$$("[data-set-symbols]").forEach(function (b) {
        b.addEventListener("click", function () {
          st.symbolsMode = b.getAttribute("data-set-symbols");
          A.$$("[data-set-symbols]").forEach(function (x) { x.classList.toggle("active", x === b); });
          const wrap = A.$("#setSingleSymbolWrap"); if (wrap) wrap.hidden = st.symbolsMode !== "single";
          if (st.symbolsMode === "single") { const inp = A.$("#setSingleSymbol"); if (inp) inp.focus(); }
        });
      });
      const singleSymbol = A.$("#setSingleSymbol");
      if (singleSymbol) singleSymbol.addEventListener("input", function () { st.symbol = singleSymbol.value.trim(); });
      const all = A.$("#setExchAll");
      if (all) all.addEventListener("change", function () {
        st.exAll = all.checked;
        if (st.exAll) exchanges.forEach(function (e) { st.exSet.add(e.slug); });
        A.$$("[data-set-exch]").forEach(function (c) {
          const slug = c.getAttribute("data-set-exch");
          c.classList.toggle("disabled", st.exAll);
          c.classList.toggle("active", st.exAll || st.exSet.has(slug));
          const dot = c.querySelector(".dot");
          if (dot) dot.className = "dot " + (st.exAll || st.exSet.has(slug) ? "green" : "muted");
        });
      });
      A.$$("[data-set-exch]").forEach(function (c) {
        c.addEventListener("click", function () {
          if (st.exAll) return;
          const slug = c.getAttribute("data-set-exch");
          if (st.exSet.has(slug)) st.exSet.delete(slug); else st.exSet.add(slug);
          c.classList.toggle("active", st.exSet.has(slug));
          const dot = c.querySelector(".dot");
          if (dot) dot.className = "dot " + (st.exSet.has(slug) ? "green" : "muted");
        });
      });
    }
    bindSet();

    const notifyOn = A.$("#notifyOn");
    if (notifyOn) notifyOn.addEventListener("change", function () {
      const box = A.$("#notifyFields");
      if (box) box.style.opacity = notifyOn.checked ? "1" : ".6";
    });
    const saveNotify = A.$("#saveNotify");
    if (saveNotify) saveNotify.addEventListener("click", async function () {
      A.setBusy(saveNotify, true, "ذخیره…");
      try {
        await A.api("/api/settings", { method: "PUT", body: {
          notify_on: !!notifyOn.checked,
          notify_template: A.$("#notifyTemplate").value
        }});
        A.toast("تنظیمات اطلاع‌رسانی ذخیره شد.", "success");
      } catch (err) { A.toast(err.message, "error"); }
      A.setBusy(saveNotify, false);
    });
    const saveScan = A.$("#saveScanDefaults");
    if (saveScan) saveScan.addEventListener("click", async function () {
      if (st.symbolsMode === "single" && !st.symbol) {
        A.toast("برای حالت «تک نماد»، نام نماد را وارد کنید.", "error");
        return;
      }
      A.setBusy(saveScan, true, "ذخیره…");
      try {
        await A.api("/api/settings", { method: "PUT", body: {
          scan_timeframe: st.tf, scan_depth: st.depth, scan_mode: st.mode,
          scan_symbols_mode: st.symbolsMode, scan_symbol: st.symbol,
          scan_exchanges: Array.from(st.exSet), exchanges_all: st.exAll
        }});
        A.toast("پیش‌فرض‌های اسکن ذخیره شد.", "success");
      } catch (err) { A.toast(err.message, "error"); }
      A.setBusy(saveScan, false);
    });
    A.$$("[data-unfav-symbol]").forEach(function (b) {
      b.addEventListener("click", async function () {
        try { await A.api("/api/favorites", { method: "POST", body: { kind: "symbol", value: b.getAttribute("data-unfav-symbol") } }); viewSettings(); }
        catch (err) { A.toast(err.message, "error"); }
      });
    });
    A.$$("[data-unfav-strategy]").forEach(function (b) {
      b.addEventListener("click", async function () {
        try { await A.api("/api/favorites", { method: "POST", body: { kind: "strategy", value: b.getAttribute("data-unfav-strategy") } }); viewSettings(); }
        catch (err) { A.toast(err.message, "error"); }
      });
    });
    const logoutBtn = A.$("#logoutBtn");
    if (logoutBtn) logoutBtn.addEventListener("click", A.logout);
    setCleanup(null);
  }

  /* ================================================================
     پروفایل کاربری
     ================================================================ */
  function profileFieldHtml(field, value) {
    const type = ["email", "tel", "number"].indexOf(field.type) >= 0 ? field.type : "text";
    const mono = type === "email" || type === "tel" || type === "number";
    return '<div class="field"><label class="label" for="pf_' + A.esc(field.key) + '">' + A.esc(field.label || field.key) +
      (field.required ? ' <span style="color:var(--red)">*</span>' : "") + "</label>" +
      '<input class="input' + (mono ? " mono" : "") + '" id="pf_' + A.esc(field.key) + '" data-pfield="' + A.esc(field.key) +
      '" type="' + type + '" value="' + A.esc(value || "") + '" spellcheck="false"' +
      (type === "tel" || type === "number" ? ' inputmode="tel"' : "") + "></div>";
  }

  function normalizePhoneInput(value) {
    let v = A.toEn(String(value || "")).replace(/[\s\-()]/g, "");
    if (v.indexOf("00989") === 0) v = "+989" + v.slice(5);
    else if (v.indexOf("09") === 0) v = "+98" + v.slice(1);
    else if (v.indexOf("9") === 0 && v.length === 10) v = "+98" + v;
    return v;
  }

  async function viewProfile() {
    A.setPage("پروفایل کاربری", "اطلاعات حساب، راه‌های ارتباطی و امنیت ورود");
    A.markNav("profile");
    setHTML('<div class="card pad"><div class="loading-row"><span class="spinner"></span><span>در حال دریافت پروفایل…</span></div></div>');
    let data;
    try { data = await A.api("/api/me/profile"); }
    catch (err) {
      setHTML(errorCard(err, "profileRetry"));
      const b = A.$("#profileRetry"); if (b) b.addEventListener("click", viewProfile);
      return;
    }
    const user = state.user || {};
    const fields = (data.fields || []).filter(function (f) { return f.key !== "username" && f.key !== "password"; });
    const values = data.values || {};
    const hasPassword = data.has_password !== false;
    const fieldHtml = fields.map(function (f) { return profileFieldHtml(f, values[f.key]); }).join("");

    setHTML(
      '<div class="page-head"><div><div class="eyebrow">' + A.icon("user") + ' حساب کاربری</div><div class="page-title">پروفایل کاربری</div>' +
        '<div class="page-sub">نام کاربری شما ثابت است؛ سایر اطلاعات و رمز عبور را می‌توانید تغییر دهید.</div></div>' +
        '<div class="page-actions"><a class="btn btn-ghost" href="/settings" data-link>' + A.icon("settings") + ' تنظیمات</a>' +
        '<a class="btn btn-soft" href="/subscribe" data-link>' + A.icon("wallet") + ' خرید اشتراک</a></div></div>' +

      '<div class="settings-grid"><div class="stack">' +

        '<div class="card"><div class="card-head bordered"><div><div class="card-title">' + A.icon("user") + ' اطلاعات حساب</div>' +
          '<div class="card-sub">هویت شما در آبان ترید</div></div>' +
          (user.is_admin ? '<span class="pill violet">مدیر</span>' : '<span class="pill green">کاربر وب‌اپ</span>') + "</div>" +
          '<div class="card-body"><div class="detail-grid">' +
            '<div class="detail-item"><span>نام کاربری</span><b class="mono">' + A.esc(data.username || user.username || "-") + "</b></div>" +
            '<div class="detail-item"><span>شناسه کاربر</span><b class="mono">' + A.toFa(user.uid || data.user_id || 0) + "</b></div>" +
            '<div class="detail-item"><span>تاریخ عضویت</span><b>' + A.esc(user.joined_at || "-") + "</b></div>" +
            '<div class="detail-item"><span>وضعیت دسترسی</span><b>' + (user.subscription && user.subscription.active ? "اشتراک فعال" : "بدون اشتراک فعال") + "</b></div>" +
          "</div>" +
          '<div class="notice mt-2">' + A.icon("lock") + " نام کاربری پس از ساخت حساب قابل تغییر نیست. برای هرگونه اصلاح، با پشتیبانی در ارتباط باشید.</div></div></div>" +

        '<div class="card"><div class="card-head bordered"><div><div class="card-title">' + A.icon("edit") + ' اطلاعات تکمیلی</div>' +
          '<div class="card-sub">این اطلاعات در پروفایل شما ذخیره می‌شود</div></div></div>' +
          '<div class="card-body">' +
            (fieldHtml
              ? '<div class="form-grid" id="profileFieldBox">' + fieldHtml + "</div>" +
                '<div class="row-between mt-3"><span class="tiny muted">اطلاعات تماس برای هماهنگی و پشتیبانی استفاده می‌شود.</span>' +
                '<button class="btn btn-primary" id="saveProfileBtn">' + A.icon("check") + " ذخیره اطلاعات</button></div>"
              : '<div class="notice">' + A.icon("info") + " هنوز فیلد اطلاعاتی اضافه‌ای برای پروفایل تعریف نشده است. مدیر می‌تواند از بخش «فیلدهای ثبت‌نام» آن را اضافه کند.</div>") +
          "</div></div>" +

        '<div class="card"><div class="card-head bordered"><div><div class="card-title">' + A.icon("lock") + ' امنیت ورود</div>' +
          '<div class="card-sub">تغییر رمز عبور حساب</div></div></div>' +
          '<div class="card-body"><div class="form-grid">' +
            (hasPassword ? '<div class="field full"><label class="label" for="curPass">رمز عبور فعلی</label>' +
              '<input class="input" id="curPass" type="password" autocomplete="current-password" spellcheck="false"></div>' : "") +
            '<div class="field"><label class="label" for="newPass">رمز عبور جدید</label>' +
              '<input class="input" id="newPass" type="password" autocomplete="new-password" spellcheck="false"></div>' +
            '<div class="field"><label class="label" for="confirmPass">تکرار رمز عبور جدید</label>' +
              '<input class="input" id="confirmPass" type="password" autocomplete="new-password" spellcheck="false"></div>' +
          "</div>" +
          '<div class="row-between mt-3"><span class="tiny muted">رمز عبور باید حداقل ۸ کاراکتر باشد.</span>' +
            '<button class="btn btn-primary" id="savePasswordBtn">' + A.icon("lock") + " تغییر رمز عبور</button></div>" +
          "</div></div>" +

      '</div><div class="settings-side">' +

        '<div class="card"><div class="card-head"><div><div class="card-title">' + A.icon("help") + ' راهنمای ورود</div></div></div>' +
          '<div class="card-body tight"><div class="list">' +
            '<div class="list-item"><div class="list-icon blue">' + A.icon("user") + '</div><div class="list-body"><div class="list-title">ورود با نام کاربری</div><div class="list-sub">نام کاربری و رمز عبور دریافتی از ربات را وارد کنید.</div></div></div>' +
            '<div class="list-item"><div class="list-icon green">' + A.icon("phone") + '</div><div class="list-body"><div class="list-title">ورود با پیامک</div><div class="list-sub">اگر ورود پیامکی فعال باشد، با شماره موبایل خود وارد شوید.</div></div></div>' +
          "</div></div></div>" +

        '<div class="card"><div class="card-head"><div><div class="card-title">' + A.icon("sparkles") + ' اشتراک تحلیل ساختاری</div></div></div>' +
          '<div class="card-body tight"><div class="mini-plan"><div class="icon-wrap">' + A.icon("sparkles") + "</div><div><b>" +
            (user.is_admin ? "دسترسی نامحدود" : (user.subscription && user.subscription.active ? "اشتراک فعال" : "اشتراک فعال نیست")) + "</b><span>" +
            (user.subscription && user.subscription.until_jalali ? "پایان: " + A.esc(user.subscription.until_jalali) : "برای دسترسی کامل فعال کنید") + "</span></div></div>" +
          '<a class="btn btn-primary btn-block mt-2" href="/subscribe" data-link>' + A.icon("wallet") + " خرید / تمدید اشتراک</a></div></div>" +

      "</div></div>"
    );

    const saveProfileBtn = A.$("#saveProfileBtn");
    if (saveProfileBtn) saveProfileBtn.addEventListener("click", async function () {
      const box = A.$("#profileFieldBox");
      const inputs = box ? box.querySelectorAll("input[data-pfield]") : [];
      const payload = {};
      for (let i = 0; i < inputs.length; i += 1) {
        const el = inputs[i];
        const field = fields.filter(function (f) { return f.key === el.dataset.pfield; })[0] || { key: el.dataset.pfield };
        let value = el.value.trim();
        if (field.required && !value) { A.toast("«" + (field.label || field.key) + "» را وارد کنید.", "error"); el.focus(); return; }
        if (field.type === "email" && value && !/^[^@\s]+@[^@\s]+\.[^@\s]{2,}$/.test(value)) { A.toast("ایمیل وارد شده معتبر نیست.", "error"); el.focus(); return; }
        if (field.type === "tel" && value) {
          value = normalizePhoneInput(value);
          if (!/^\+989\d{9}$/.test(value)) { A.toast("شماره موبایل معتبر نیست.", "error"); el.focus(); return; }
        }
        payload[el.dataset.pfield] = value;
      }
      A.setBusy(saveProfileBtn, true, "ذخیره…");
      try {
        await A.api("/api/me/profile", { method: "PUT", body: { values: payload } });
        A.toast("اطلاعات پروفایل ذخیره شد.", "success");
        try {
          const session = await A.api("/api/auth/session");
          if (session && session.user) { state.user = session.user; A.fillUser(state.user); }
        } catch (e) {}
      } catch (err) { A.toast(err.message, "error"); }
      A.setBusy(saveProfileBtn, false);
    });

    const savePasswordBtn = A.$("#savePasswordBtn");
    if (savePasswordBtn) savePasswordBtn.addEventListener("click", async function () {
      const curEl = A.$("#curPass"), newEl = A.$("#newPass"), confEl = A.$("#confirmPass");
      const current = curEl ? curEl.value : "";
      const next = newEl.value;
      if (!next || next.length < 8) { A.toast("رمز عبور جدید باید حداقل ۸ کاراکتر باشد.", "error"); newEl.focus(); return; }
      if (next !== confEl.value) { A.toast("تکرار رمز عبور با رمز جدید یکسان نیست.", "error"); confEl.focus(); return; }
      A.setBusy(savePasswordBtn, true, "ذخیره…");
      try {
        await A.api("/api/me/profile", { method: "PUT", body: { current_password: current, new_password: next } });
        A.toast("رمز عبور تغییر کرد.", "success");
        newEl.value = ""; confEl.value = ""; if (curEl) curEl.value = "";
      } catch (err) { A.toast(err.message, "error"); }
      A.setBusy(savePasswordBtn, false);
    });

    setCleanup(null);
  }

  /* ================================================================
     خرید اشتراک
     ================================================================ */
  function subStatusPill(status) {
    if (status === "approved") return A.pill("تأیید شده", "green");
    if (status === "rejected") return A.pill("رد شده", "red");
    return A.pill("در انتظار بررسی", "amber");
  }

  function subMoney(value, unit) {
    const n = Number(value || 0);
    const digits = n % 1 ? (Math.abs(n) < 1 ? 6 : 2) : 0;
    return A.fmtNum(n, digits) + " " + (unit || "دلار");
  }

  function prettyCardNum(value) {
    const digits = String(value || "").replace(/\D/g, "");
    if (digits.length !== 16) return String(value || "");
    return digits.replace(/(.{4})/g, "$1 ").trim();
  }

  async function viewSubscribe() {
    A.setPage("خرید و تمدید اشتراک", "انتخاب مقصد پرداخت، ثبت رسید و پیگیری وضعیت");
    A.markNav("subscribe");
    setHTML('<div class="card pad"><div class="loading-row"><span class="spinner"></span><span>در حال دریافت وضعیت اشتراک…</span></div></div>');
    let d;
    try { d = await A.api("/api/subscription"); }
    catch (err) {
      setHTML(errorCard(err, "subRetry"));
      const b = A.$("#subRetry"); if (b) b.addEventListener("click", viewSubscribe);
      return;
    }

    const user = state.user || {};
    const unlimited = !!d.unlimited;
    const networks = d.networks || [];
    const pending = d.pending;
    const requests = d.requests || [];
    const active = !!d.active;

    const kpis =
      '<div class="kpi-grid">' +
        '<div class="kpi"><div class="kpi-top"><div><div class="kpi-label">وضعیت اشتراک</div><div class="kpi-value">' +
          (unlimited ? "نامحدود" : (active ? "فعال" : "غیرفعال")) + '</div></div><div class="kpi-icon ' + (unlimited || active ? "green" : "amber") + '">' + A.icon("shield") + "</div></div></div>" +
        '<div class="kpi"><div class="kpi-top"><div><div class="kpi-label">تاریخ پایان</div><div class="kpi-value" style="font-size:1.05rem">' +
          A.esc(unlimited ? "بدون محدودیت" : (d.until_jalali || "-")) + '</div></div><div class="kpi-icon blue">' + A.icon("calendar") + "</div></div></div>" +
        '<div class="kpi"><div class="kpi-top"><div><div class="kpi-label">روزهای باقی‌مانده</div><div class="kpi-value">' +
          (unlimited ? "∞" : A.toFa(d.days_left || 0)) + '<small>روز</small></div></div><div class="kpi-icon violet">' + A.icon("clock") + "</div></div></div>" +
        '<div class="kpi"><div class="kpi-top"><div><div class="kpi-label">مبلغ اشتراک</div><div class="kpi-value" style="font-size:1.05rem">' +
          (unlimited ? "-" : A.esc(subMoney(d.amount))) + '</div></div><div class="kpi-icon amber">' + A.icon("wallet") + "</div></div></div>" +
      "</div>";

    const discount = Number(d.discount_amount || 0) > 0 && Number(d.original_amount || 0) > Number(d.amount || 0)
      ? '<span class="pill green">' + A.icon("sparkles") + " " + A.fmtNum(Math.round((1 - d.amount / d.original_amount) * 100)) + "٪ تخفیف</span>"
      : "";

    const planCard = unlimited
      ? '<div class="card"><div class="card-head bordered"><div><div class="card-title">' + A.icon("shield") + ' دسترسی مدیریتی</div>' +
          '<div class="card-sub">مدیر اکوسیستم به همه بخش‌ها دسترسی نامحدود دارد.</div></div><span class="pill violet">مدیر</span></div></div>'
      : '<div class="card"><div class="card-head bordered"><div><div class="card-title">' + A.icon("sparkles") + ' پلن اشتراک تحلیل ساختاری</div>' +
          '<div class="card-sub">دسترسی به اسکنر، تحلیل چند تایم‌فریمی و تاریخچه جستجو</div></div>' +
          '<div class="plan-head">' + discount + "</div></div>" +
          '<div class="card-body"><div class="detail-grid">' +
            '<div class="detail-item"><span>مدت اشتراک</span><b>' + A.toFa(d.days || 0) + " روز</b></div>" +
            '<div class="detail-item"><span>مبلغ نهایی</span><b>' + A.esc(subMoney(d.amount)) + "</b></div>" +
            (discount ? '<div class="detail-item"><span>مبلغ اصلی</span><b class="muted" style="text-decoration:line-through">' + A.esc(subMoney(d.original_amount)) + "</b></div>" : "") +
            '<div class="detail-item"><span>وضعیت فعلی</span><b>' + (active ? "فعال تا " + A.esc(d.until_jalali || "-") : "غیرفعال") + "</b></div>" +
          "</div></div></div>";

    const pendingCard = pending
      ? '<div class="card"><div class="card-body"><div class="notice warn">' + A.icon("clock") +
          " درخواست شما در تاریخ " + A.esc(pending.created_at || "-") + " ثبت شده و در انتظار بررسی مدیر است. تا تعیین وضعیت، امکان ثبت درخواست جدید نیست.</div></div></div>"
      : "";

    let formCard = "";
    let payOptions = [];
    if (!unlimited && !pending) {
      if (!d.configured || (!networks.length && !(d.destinations && (d.destinations.cards || []).length) && !(d.destinations && (d.destinations.wallets || []).length))) {
        formCard = '<div class="card"><div class="card-body"><div class="notice danger">' + A.icon("alert") +
          " فروش اشتراک وب‌اپ هنوز پیکربندی نشده است. برای خرید، از ربات آبان ترید استفاده کنید یا با پشتیبانی در ارتباط باشید.</div>" +
          '<a class="btn btn-soft mt-2" href="' + A.esc(botLinkFor(user)) + '" target="_blank" rel="noopener">' + A.icon("send") + " ورود به ربات</a></div></div>";
      } else {
        payOptions = networks.map(function (n) {
          const asset = String(n.asset || "").trim();
          return {
            key: n.key, title: n.title, wallet: n.wallet || "",
            amount: n.amount, auto: !!n.auto, kind: "crypto", dest_label: "کیف پول مقصد",
            unit: asset || "",
          };
        });
        const dests = d.destinations || {};
        const planPrice = Number(d.amount || 0);
        (dests.cards || []).forEach(function (c) {
          const num = String(c.card_number || "").replace(/\D/g, "");
          if (num.length !== 16) return;
          const bank = (c.bank_name || "").trim();
          payOptions.push({
            key: "card:" + c.id,
            title: "کارت " + prettyCardNum(c.masked || num) + (bank ? " — " + bank : ""),
            wallet: prettyCardNum(num),
            amount: planPrice, auto: false, kind: "card",
            dest_label: "شماره کارت" + (bank ? " (" + bank + ")" : ""),
            shaba: (c.shaba || "").trim(),
            holder: (c.holder_name || "").trim(),
          });
        });
        (dests.wallets || []).forEach(function (w) {
          const addr = String(w.address || "").trim();
          if (!addr) return;
          const label = (w.network_label || w.network || w.chain || "کیف پول").trim();
          const asset = (w.asset || "").trim();
          const amt = Number(w.amount || 0) || planPrice;
          payOptions.push({
            key: "wallet:" + w.id,
            title: label + " — " + (asset || "ارز") + " (واریز دستی)",
            wallet: addr, amount: amt, auto: false, kind: "wallet",
            dest_label: "آدرس کیف پول " + label,
            unit: asset || "",
          });
        });
        const groups = [];
        function pushGroup(name, list) {
          if (!list.length) return;
          groups.push('<optgroup label="' + A.esc(name) + '">' + list.map(function (o, i) {
            return '<option value="' + A.esc(o.key) + '" data-amount="' + A.esc(String(o.amount || 0)) + '" data-wallet="' + A.esc(o.wallet) + '" data-auto="' + (o.auto ? "1" : "0") + '"' + (groups.length === 0 && i === 0 ? " selected" : "") + ">" + A.esc(o.title) + "</option>";
          }).join("") + "</optgroup>");
        }
        pushGroup("شبکه‌های ارز دیجیتال", payOptions.filter(function (o) { return o.kind === "crypto"; }));
        pushGroup("کارت به کارت", payOptions.filter(function (o) { return o.kind === "card"; }));
        pushGroup("کیف پول ارزی (واریز دستی)", payOptions.filter(function (o) { return o.kind === "wallet"; }));
        const options = groups.join("");
        formCard =
          '<div class="card"><div class="card-head bordered"><div><div class="card-title">' + A.icon("wallet") + ' ثبت درخواست اشتراک</div>' +
            '<div class="card-sub">مقصد پرداخت را انتخاب کنید، واریز را انجام دهید و سپس رسید یا شماره تراکنش را ثبت کنید.</div></div></div>' +
          '<div class="card-body">' +
            '<div class="form-grid">' +
              '<div class="field full"><label class="label" for="subNetwork">مقصد پرداخت</label>' +
                '<select class="select" id="subNetwork">' + options + "</select></div>" +
              '<div class="field full"><label class="label" id="subDestLabel">کیف پول مقصد</label>' +
                '<div class="copy-row"><input class="input mono" id="subWallet" readonly value="" dir="ltr">' +
                '<button class="btn btn-ghost" type="button" id="copyWalletBtn" title="کپی">' + A.icon("copy") + "</button></div></div>" +
              '<div class="field full" id="subHolderBox" hidden><label class="label">به نام</label>' +
                '<div class="copy-row"><input class="input" id="subHolder" readonly value="">' +
                '<button class="btn btn-ghost" type="button" id="copyHolderBtn" title="کپی نام گیرنده">' + A.icon("copy") + "</button></div></div>" +
              '<div class="field full" id="subShabaBox" hidden><label class="label">شبا (اختیاری)</label>' +
                '<div class="copy-row"><input class="input mono" id="subShaba" readonly value="" dir="ltr">' +
                '<button class="btn btn-ghost" type="button" id="copyShabaBtn" title="کپی شبا">' + A.icon("copy") + "</button></div></div>" +
              '<div class="field"><label class="label">مبلغ قابل پرداخت</label>' +
                '<input class="input mono" id="subExpected" readonly value=""></div>' +
              '<div class="field"><label class="label">روش ثبت</label>' +
                '<div class="segmented" id="subMethod">' +
                  '<button class="seg active" type="button" data-method="auto">تأیید خودکار با هش</button>' +
                  '<button class="seg" type="button" data-method="manual">ارسال رسید</button>' +
                "</div></div>" +
              '<div class="field full" id="subAutoBox"><label class="label" for="subHash">هش تراکنش</label>' +
                '<input class="input mono" id="subHash" placeholder="0x… یا hash" spellcheck="false" dir="ltr">' +
                '<div class="hint">تراکنش به‌صورت خودکار در بلاک‌چین بررسی می‌شود.</div></div>' +
              '<div class="field full" id="subManualBox" hidden><label class="label" for="subReceipt">تصویر رسید</label>' +
                '<input class="input" id="subReceipt" type="file" accept="image/jpeg,image/png,image/webp">' +
                '<div class="hint" id="subReceiptHint">JPG، PNG یا WebP — حداکثر ۵ مگابایت.</div></div>' +
              '<div class="field full" id="subRefBox" hidden><label class="label" for="subRef">شماره تراکنش یا کد مرجع</label>' +
                '<input class="input mono" id="subRef" placeholder="مثلاً کد پیگیری کارت‌به‌کارت" spellcheck="false" dir="ltr">' +
                '<div class="hint">اگر تصویر رسید بارگذاری نمی‌کنید، این شماره را وارد کنید.</div></div>' +
              '<div class="field full" id="subAmountBox" hidden><label class="label" id="subAmountLabel" for="subAmount">مبلغ واریز شده</label>' +
                '<input class="input mono" id="subAmount" inputmode="decimal" placeholder="مثلاً 25"></div>' +
              '<div class="field full"><label class="label" for="subNote">توضیحات (اختیاری)</label>' +
                '<textarea class="textarea" id="subNote" rows="2" placeholder="اگر توضیحی برای بررسی دارید بنویسید…"></textarea></div>' +
            "</div>" +
            '<div class="row-between mt-3"><span class="tiny muted">پس از تأیید مدیر، اشتراک شما فعال و دسترسی وب‌اپ باز می‌شود.</span>' +
              '<button class="btn btn-primary" id="subSubmitBtn">' + A.icon("send") + " ثبت درخواست</button></div>" +
          "</div></div>";
      }
    }

    const historyCard = requests.length
      ? '<div class="card"><div class="card-head bordered"><div><div class="card-title">' + A.icon("history") + ' درخواست‌های اخیر</div>' +
          '<div class="card-sub">آخرین درخواست‌های خرید و تمدید شما</div></div></div>' +
          '<div class="card-body tight"><div class="list">' + requests.map(function (r) {
            return '<div class="list-item"><div class="list-icon ' + (r.status === "approved" ? "green" : (r.status === "rejected" ? "red" : "amber")) + '">' +
              A.icon(r.status === "approved" ? "check" : (r.status === "rejected" ? "alert" : "clock")) + "</div>" +
              '<div class="list-body"><div class="list-title">درخواست #' + A.toFa(r.id) + " — " + subStatusPill(r.status) + "</div>" +
              '<div class="list-sub">' + A.esc(r.created_at || "-") + (r.decided_at ? " • بررسی: " + A.esc(r.decided_at) : "") +
              (r.reason ? " • " + A.esc(r.reason) : "") + "</div></div></div>";
          }).join("") + "</div></div></div>"
      : "";

    setHTML(
      '<div class="page-head"><div><div class="eyebrow">' + A.icon("wallet") + ' مالی</div><div class="page-title">خرید و تمدید اشتراک</div>' +
        '<div class="page-sub">پرداخت را انجام دهید و رسید یا هش تراکنش را برای بررسی ثبت کنید.</div></div></div>' +
      kpis + planCard + pendingCard + formCard + historyCard
    );

    const sel = A.$("#subNetwork");
    if (!formCard || !sel) { setCleanup(null); return; }
    const walletInput = A.$("#subWallet");
    const expectedInput = A.$("#subExpected");
    const autoBox = A.$("#subAutoBox");
    const manualBox = A.$("#subManualBox");
    const amountBox = A.$("#subAmountBox");
    const refBox = A.$("#subRefBox");
    const holderBox = A.$("#subHolderBox");
    const shabaBox = A.$("#subShabaBox");
    const destLabel = A.$("#subDestLabel");
    const receiptHint = A.$("#subReceiptHint");
    let method = "auto";

    function currentNetwork() {
      return payOptions.filter(function (o) { return o.key === sel.value; })[0] || payOptions[0] || {};
    }
    function syncNetwork() {
      const net = currentNetwork();
      const manualOnly = !net.auto;
      if (walletInput) walletInput.value = net.wallet || "";
      if (destLabel) destLabel.textContent = net.dest_label || "کیف پول مقصد";
      if (holderBox) {
        const holder = net.holder || "";
        holderBox.hidden = !holder;
        A.$("#subHolder").value = holder;
      }
      if (shabaBox) {
        const shaba = net.shaba || "";
        shabaBox.hidden = !shaba;
        A.$("#subShaba").value = shaba;
      }
      if (receiptHint) {
        receiptHint.textContent = manualOnly
          ? "JPG، PNG یا WebP — حداکثر ۵ مگابایت. رسید برای تصویر کارت‌به‌کارت اختیاری است."
          : "JPG، PNG یا WebP — حداکثر ۵ مگابایت.";
      }
      if (expectedInput) expectedInput.value = net.amount != null ? subMoney(net.amount, net.unit) : "-";
      const amountLabel = A.$("#subAmountLabel");
      const amountInput = A.$("#subAmount");
      if (amountLabel) amountLabel.textContent = "مبلغ واریز شده (" + (net.unit || "دلار") + ")";
      if (amountInput) amountInput.placeholder = "مثلاً " + A.fmtNum(net.amount || 0, net.amount && net.amount % 1 ? 2 : 0) + " " + (net.unit || "دلار");
      A.$$("#subMethod [data-method]").forEach(function (b) {
        const isAuto = b.getAttribute("data-method") === "auto";
        b.disabled = isAuto && !net.auto;
        b.classList.toggle("active", b.getAttribute("data-method") === method);
      });
      if (manualOnly && method === "auto") { method = "manual"; }
      renderMethod();
    }
    function renderMethod() {
      A.$$("#subMethod [data-method]").forEach(function (b) {
        b.classList.toggle("active", b.getAttribute("data-method") === method);
      });
      const net = currentNetwork();
      if (autoBox) autoBox.hidden = method !== "auto";
      if (manualBox) manualBox.hidden = method !== "manual";
      if (amountBox) amountBox.hidden = method !== "manual";
      if (refBox) refBox.hidden = method !== "manual" || !(net.kind === "card" || net.kind === "wallet");
    }
    function setMethod(next) {
      method = next;
      renderMethod();
    }

    if (sel) sel.addEventListener("change", syncNetwork);
    A.$$("#subMethod [data-method]").forEach(function (b) {
      b.addEventListener("click", function () {
        if (b.disabled) return;
        setMethod(b.getAttribute("data-method"));
      });
    });
    const copyWalletBtn = A.$("#copyWalletBtn");
    if (copyWalletBtn) copyWalletBtn.addEventListener("click", function () {
      A.copy(walletInput.value);
    });
    const copyHolderBtn = A.$("#copyHolderBtn");
    if (copyHolderBtn) copyHolderBtn.addEventListener("click", function () {
      A.copy(A.$("#subHolder").value);
    });
    const copyShabaBtn = A.$("#copyShabaBtn");
    if (copyShabaBtn) copyShabaBtn.addEventListener("click", function () {
      A.copy(A.$("#subShaba").value);
    });
    syncNetwork();

    const submitBtn = A.$("#subSubmitBtn");
    if (submitBtn) submitBtn.addEventListener("click", async function () {
      const net = currentNetwork();
      const form = new FormData();
      form.append("network_key", sel.value);
      form.append("note", A.$("#subNote").value.trim());
      if (method === "auto") {
        const hash = A.$("#subHash").value.trim();
        if (!hash) { A.toast("هش تراکنش را وارد کنید.", "error"); A.$("#subHash").focus(); return; }
        form.append("tx_hash", hash);
      } else {
        const fileEl = A.$("#subReceipt");
        const refEl = A.$("#subRef");
        const ref = refEl ? refEl.value.trim() : "";
        const destOnly = net.kind === "card" || net.kind === "wallet";
        const hasFile = !!(fileEl.files && fileEl.files[0]);
        if (!hasFile && !(destOnly && ref)) {
          A.toast(destOnly ? "تصویر رسید یا شماره تراکنش را وارد کنید." : "تصویر رسید را انتخاب کنید.", "error");
          (hasFile ? refEl : fileEl).focus();
          return;
        }
        if (hasFile) {
          const file = fileEl.files[0];
          if (file.size > 5 * 1024 * 1024) { A.toast("حجم رسید باید کمتر از ۵ مگابایت باشد.", "error"); return; }
          form.append("receipt", file);
        }
        if (destOnly && ref) form.append("tx_hash", ref);
        form.append("amount", A.$("#subAmount").value.trim());
        form.append("text", A.$("#subNote").value.trim());
      }
      A.setBusy(submitBtn, true, "در حال ثبت…");
      try {
        const res = await A.api("/api/subscription/request", { method: "POST", body: form });
        A.toast((res && res.message) || "درخواست ثبت شد.", "success");
        setTimeout(viewSubscribe, 900);
      } catch (err) {
        A.toast(err.message, "error");
        A.setBusy(submitBtn, false);
      }
    });

    setCleanup(null);
  }

  /* ================================================================
     جستجوی بالای صفحه
     ================================================================ */
  function setupTopSearch() {
    const input = A.$("#topSearchInput"), drop = A.$("#topSearchDrop");
    if (!input || !drop) return;
    let seq = 0;
    const run = A.debounce(async function () {
      const q = input.value.trim();
      if (!q) { drop.hidden = true; return; }
      const my = ++seq;
      drop.hidden = false;
      drop.innerHTML = '<div class="search-empty">در حال جستجو…</div>';
      try {
        const data = await A.api("/api/symbols/search?q=" + encodeURIComponent(q) + "&limit=8");
        if (my !== seq) return;
        const items = data.results || [];
        if (!items.length) { drop.innerHTML = '<div class="search-empty">نمادی پیدا نشد.</div>'; return; }
        drop.innerHTML = items.map(function (it) {
          return '<div class="search-item" data-symbol="' + A.esc(it.symbol) + '"><span class="coin' + A.coinClass(it.symbol) + '">' + A.esc(A.coinLetters(it.symbol)) + "</span>" +
            '<div class="flex-1"><div class="search-item-name">' + A.esc(it.symbol) + '</div><div class="search-item-sub">' + A.esc(it.exchange || "") + "</div></div>" + A.icon("chevron-left") + "</div>";
        }).join("");
        drop.querySelectorAll("[data-symbol]").forEach(function (el) {
          el.addEventListener("mousedown", function (e) { e.preventDefault(); });
          el.addEventListener("click", function () {
            drop.hidden = true;
            input.value = "";
            openSymbol(el.getAttribute("data-symbol"));
          });
        });
      } catch (err) {
        if (my !== seq) return;
        drop.innerHTML = '<div class="search-empty">' + A.esc(err.message) + "</div>";
      }
    }, 320);
    input.addEventListener("input", run);
    input.addEventListener("keydown", function (e) {
      if (e.key === "Enter") {
        e.preventDefault();
        const first = drop.querySelector("[data-symbol]");
        if (first) first.click();
        else if (input.value.trim()) A.go("/search?q=" + encodeURIComponent(input.value.trim()));
      }
      if (e.key === "Escape") drop.hidden = true;
    });
    document.addEventListener("click", function (e) {
      if (!drop.contains(e.target) && e.target !== input) drop.hidden = true;
    });
  }

  /* ================================================================
     نوار قیمت زندهٔ بازار
     ================================================================ */
  function tickerPrice(v) {
    const n = Number(v) || 0;
    if (!n) return "—";
    const digits = n >= 1000 ? 2 : (n >= 10 ? 3 : (n >= 0.1 ? 4 : 6));
    return n.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
  }

  function tickerSymbolText(item) {
    const raw = String(item.label || item.symbol || "");
    if (item.label) return raw;
    return /USDT$/.test(raw) ? raw.replace(/USDT$/, "/USDT") : raw;
  }

  function tickerItemHTML(item, showChange) {
    const pct = Number(item.pct) || 0;
    const cls = pct > 0 ? "up" : (pct < 0 ? "down" : "flat");
    return '<span class="ticker-item">' +
      '<span class="ticker-sym">' + A.esc(tickerSymbolText(item)) + "</span>" +
      '<span class="ticker-price">' + tickerPrice(item.price) + "</span>" +
      (showChange
        ? '<span class="ticker-chg ' + cls + '">' + (pct >= 0 ? "▲" : "▼") + " " +
          Math.abs(pct).toFixed(2) + "%</span>"
        : "") +
      "</span>";
  }

  async function loadTicker() {
    const bar = A.$("#tickerBar");
    const track = A.$("#tickerTrack");
    if (!bar || !track) return;
    let d;
    try { d = await A.api("/api/ticker", { redirectOn401: false }); }
    catch (err) { return; }
    const items = ((d && d.items) || []).filter(function (i) { return Number(i.price) > 0; });
    if (!d || !d.enabled || !items.length) { bar.hidden = true; return; }
    const one = items.map(function (i) { return tickerItemHTML(i, d.show_change); }).join("");
    let block = one;
    /* عرض نوار مخفی صفر است؛ پیش از اندازه‌گیری نمایش داده می‌شود تا تکرار لازم درست حساب شود. */
    bar.hidden = false;
    track.innerHTML = block + block;
    for (let guard = 0; guard < 6 && track.scrollWidth / 2 < bar.clientWidth; guard++) {
      block += one;
      track.innerHTML = block + block;
    }
    // تغییر طول انیمیشن از نو شروعش می‌کند؛ فقط در عوض واقعی سرعت بنویس تا حرکت پیوسته بماند.
    const speed = Math.max(8, Number(d.speed) || 48) + "s";
    if (track.dataset.speed !== speed) {
      track.dataset.speed = speed;
      track.style.setProperty("--ticker-speed", speed);
    }
    const src = A.$("#tickerSource");
    if (src) src.textContent = items[0].exchange ? String(items[0].exchange).toUpperCase() : "";
  }

  /* ================================================================
     مسیریابی
     ================================================================ */
  const OPEN_VIEWS = { "/subscribe": 1, "/settings/profile": 1 };

  async function refreshSubscription() {
    try {
      const d = await A.api("/api/auth/session", { redirectOn401: false });
      if (!d || !d.authenticated || !d.user) return false;
      state.user = d.user;
      A.fillUser(state.user);
      return !!state.user.is_admin || !!A.planStatus(state.user.subscription).active;
    } catch (err) {
      return false;
    }
  }

  async function route() {
    const path = location.pathname.replace(/\/+$/, "") || "/";
    closeGuide();
    window.scrollTo({ top: 0 });
    if (!OPEN_VIEWS[path] && !state.unlocked) {
      const sub = A.planStatus((state.user || {}).subscription);
      if (!(state.user || {}).is_admin && !sub.active) {
        const granted = await refreshSubscription();
        if (!granted) { A.go("/subscribe", true); return; }
      }
      state.unlocked = true;
    }
    if (path === "/" || path === "") return runView(viewDashboard);
    if (path === "/scanner") return runView(viewScanner);
    if (path === "/history") return runView(viewHistory, 1);
    if (path === "/search") return runView(viewSearch);
    if (path === "/settings") return runView(viewSettings);
    if (path === "/settings/profile") return runView(viewProfile);
    if (path === "/subscribe") return runView(viewSubscribe);
    const m = path.match(/^\/symbol\/([^/]+)$/);
    if (m) return runView(viewSymbol, decodeURIComponent(m[1]));
    return runView(viewDashboard);
  }
  function runView(fn) {
    setCleanup(null);
    const args = Array.prototype.slice.call(arguments, 1);
    try {
      const p = fn.apply(null, args);
      if (p && p.catch) p.catch(function (err) {
        setHTML(errorCard(err, "routeRetry"));
        const b = A.$("#routeRetry"); if (b) b.addEventListener("click", route);
      });
    } catch (err) {
      setHTML(errorCard(err, "routeRetry"));
      const b = A.$("#routeRetry"); if (b) b.addEventListener("click", route);
    }
  }

  async function boot() {
    A.setupShell();
    setupTopSearch();
    window.addEventListener("popstate", route);
    let data;
    try {
      data = await A.api("/api/auth/session", { redirectOn401: false });
    } catch (err) {
      setHTML(errorCard(err, "bootRetry"));
      const b = A.$("#bootRetry"); if (b) b.addEventListener("click", boot);
      return;
    }
    if (!data || !data.authenticated) {
      location.replace("/login");
      return;
    }
    state.user = data.user;
    A.fillUser(state.user);
    loadTicker();
    if (!tickerTimer) tickerTimer = setInterval(loadTicker, 30000);
    const path = location.pathname.replace(/\/+$/, "") || "/";
    const sub = A.planStatus(state.user && state.user.subscription);
    state.unlocked = !!state.user.is_admin || !!sub.active;
    if (!state.unlocked && path !== "/subscribe" && path !== "/settings/profile") {
      location.replace("/subscribe");
      return;
    }
    route();
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();

  window.AbanApp = { route: route, state: state, showGuide: showGuide };
})();
