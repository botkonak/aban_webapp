/* آبان ترید — پنل مدیریت */
(function () {
  "use strict";
  const A = window.Aban;

  const TF_LABEL = {
    "5m": "۵ دقیقه", "15m": "۱۵ دقیقه", "30m": "۳۰ دقیقه", "1h": "۱ ساعت",
    "4h": "۴ ساعت", "6h": "۶ ساعت", "12h": "۱۲ ساعت", "1d": "۱ روز", "1w": "۱ هفته"
  };
  const PAGE_SIZE = 25;
  const PAY_SIZE = 20;
  const STRAT_SIZE = 30;
  const CLAIM_SIZE = 10;

  const state = {
    user: null,
    users: { q: "", sub: "all", access: "all", page: 1, total: 0, items: [] },
    payments: { service: "all", status: "all", page: 1, total: 0, items: [], statuses: [] },
    strategies: { q: "", category: "all", page: 1, total: 0, items: [], categories: [] },
    exchanges: { items: [] },
    message: { tab: "single", user: null, text: "", job: null, timer: null, items: [] },
    settings: null,
    support: { status: "open", q: "", page: 1, total: 0, items: [], current: 0 },
    plans: { tab: "bot", data: null, dest: { cards: [], wallets: [] }, section: "", claims: { plan: "", page: 1, total: 0, items: [] } },
    seo: { section: "" },
    branding: null,
    maintenance: null,
    apikeys: null,
    backups: { page: 1, total: 0, items: [], sections: [], auto: {}, keep: 0 },
    security: null,
    content: { postsPage: 1, postsQ: "", postsStatus: "all", postsCategory: 0, categories: [] },
    media: { page: 1, q: "", total: 0, items: [] },
    webusers: { page: 1, q: "", total: 0, items: [] },
    registration: null,
    sec: {
      tab: "overview", eventsPage: 1, auditPage: 1, evSeverity: "all", evResolved: "all", evQ: "", auditQ: "",
      ipQ: "", overview: null, ipItems: [], userItems: []
    }
  };

  let viewToken = 0;
  let cleanup = null;

  function viewEl() { return A.$("#view"); }
  function beginView() {
    viewToken += 1;
    const token = viewToken;
    if (cleanup) { try { cleanup(); } catch (e) {} cleanup = null; }
    stopJobPolling();
    return token;
  }
  function alive(token) { return token === viewToken; }
  function setCleanup(fn) { cleanup = fn || null; }
  function clip(text, len) {
    const s = String(text == null ? "" : text);
    return s.length > len ? s.slice(0, len - 1) + "…" : s;
  }
  function money(value) { return A.fmtNum(value, 2); }
  function unitOf(p) { return (p && p.asset) ? p.asset : "دلار"; }
  function loadingCard(text) {
    return '<div class="card pad">' + A.loadingRow(text) + "</div>";
  }
  function errorCard(message, retryAttr) {
    return '<div class="card pad"><div class="empty"><div class="empty-icon">' + A.icon("alert") +
      "</div><b>دریافت اطلاعات انجام نشد</b><span>" + A.esc(message || "خطای ارتباط با سرور") + "</span>" +
      (retryAttr || "") + "</div></div>";
  }
  function pagerHTML(page, totalPages) {
    const p = Math.max(1, page || 1);
    const t = Math.max(1, totalPages || 1);
    if (t <= 1) return "";
    let out = '<button data-act="page" data-page="' + A.toFa(Math.max(1, p - 1)) + '"' + (p <= 1 ? " disabled" : "") + ">" + A.icon("chevron-right") + "</button>";
    let start = Math.max(1, p - 2);
    let end = Math.min(t, start + 4);
    start = Math.max(1, end - 4);
    if (start > 1) out += '<button data-act="page" data-page="۱">۱</button>' + (start > 2 ? '<span class="tiny muted">…</span>' : "");
    for (let i = start; i <= end; i++) {
      out += '<button data-act="page" data-page="' + A.toFa(i) + '"' + (i === p ? ' class="active"' : "") + ">" + A.toFa(i) + "</button>";
    }
    if (end < t) out += (end < t - 1 ? '<span class="tiny muted">…</span>' : "") + '<button data-act="page" data-page="' + A.toFa(t) + '">' + A.toFa(t) + "</button>";
    out += '<button data-act="page" data-page="' + A.toFa(Math.min(t, p + 1)) + '"' + (p >= t ? " disabled" : "") + ">" + A.icon("chevron-left") + "</button>";
    return '<div class="pager">' + out + "</div>";
  }
  function pageNumber(value) {
    const n = parseInt(A.toEn(String(value || "")).replace(/\D/g, ""), 10);
    return Number.isFinite(n) && n > 0 ? n : 1;
  }
  function subCell(sub) {
    const s = sub || {};
    if (s.active) {
      return '<span class="pill green">' + A.toFa(s.days_left || 0) + " روز</span>" +
        '<div class="cell-sub">تا ' + A.esc(s.until_jalali || "-") + "</div>";
    }
    return '<span class="pill gray">غیرفعال</span>';
  }
  function accessCell(user) {
    if (user.access_enabled && user.has_password) return '<span class="pill green">فعال</span>';
    if (user.access_enabled) return '<span class="pill amber">بدون رمز</span>';
    return '<span class="pill gray">غیرفعال</span>';
  }
  function setUsersBadge(stats) {
    const badge = A.$("#navUsersBadge");
    if (!badge) return;
    const s = stats || {};
    const n = Number(s.pending_requests != null ? s.pending_requests : s.pending_payments) || 0;
    if (n > 0) { badge.textContent = A.toFa(n); badge.hidden = false; }
    else { badge.textContent = ""; badge.hidden = true; }
  }
  const SEO_TAB_KEYS = {
    "seo-web": "site", "web": "site", "site": "site",
    "seo-telegram": "telegram", "telegram": "telegram",
    "seo-technical": "tech", "technical": "tech", "tech": "tech"
  };
  const PLANS_SECTIONS = {
    plans: "#planMainForm",
    networks: "#planNetworksCard",
    destinations: "#planDestinationsCard",
    discounts: "#planDiscountForm",
    free: "#planFreeForm"
  };
  function applyDeepLink() {
    const name = routeName();
    const params = new URLSearchParams(location.search || "");
    const tab = (params.get("tab") || "").trim();
    if (tab) {
      if (name === "seo" && SEO_TAB_KEYS[tab]) state.seo.section = SEO_TAB_KEYS[tab];
      else if (name === "plans" && PLANS_SECTIONS[tab]) state.plans.section = tab;
      else if (name === "security-center") {
        const keys = secTabItems().map(function (t) { return t[0]; });
        if (keys.indexOf(tab) >= 0) state.sec.tab = tab;
      }
    }
    const status = (params.get("status") || "").trim();
    if (name === "payments" && status) state.payments.status = status;
  }
  function markNavDeep(metaNav) {
    const here = location.pathname + (location.search || "");
    const links = A.$$(".nav-link[data-nav]");
    let chosen = links.filter(function (a) { return (a.getAttribute("href") || "") === here; });
    if (!chosen.length) {
      chosen = links.filter(function (a) {
        const href = a.getAttribute("href") || "";
        return href === location.pathname && href.indexOf("?") < 0;
      });
    }
    if (!chosen.length) {
      chosen = links.filter(function (a) { return a.getAttribute("data-nav") === metaNav; }).slice(0, 1);
    }
    links.forEach(function (a) { a.classList.toggle("active", chosen.indexOf(a) >= 0); });
  }
  function targetLabel(type) {
    if (type === "single") return "تک کاربر";
    if (type === "bulk") return "همه کاربران";
    if (type === "targeted") return "ارسال هدفمند";
    return type || "-";
  }
  function paymentRowActions(p) {
    const ic = A.icon;
    let out = '<div class="cell-actions">';
    out += '<button class="icon-btn sm" data-act="payment-view" data-pid="' + p.id + '" title="جزئیات پرداخت">' + ic("eye") + "</button>";
    if (p.status === "pending" || p.status === "suspended") {
      out += '<button class="icon-btn sm" data-act="payment-approve" data-pid="' + p.id + '" title="تأیید">' + ic("check") + "</button>";
      out += '<button class="icon-btn sm danger" data-act="payment-reject" data-pid="' + p.id + '" title="رد">' + ic("close") + "</button>";
    }
    out += "</div>";
    return out;
  }
  function refreshCurrent() {
    const name = routeName();
    const meta = PAGES[name] || PAGES.overview;
    const token = beginView();
    A.setPage(meta.title, meta.sub);
    markNavDeep(meta.nav);
    meta.render(token);
  }

  /* ------------------------------------------------------------------
     نمای کلی
  ------------------------------------------------------------------ */
  async function renderOverview(token) {
    const root = viewEl();
    root.innerHTML = '<div class="hello"><div><div class="skeleton sk-line" style="height:24px;width:220px"></div>' +
      '<div class="skeleton sk-line" style="width:320px"></div></div></div>' +
      '<div class="kpi-grid">' + Array(4).fill('<div class="skeleton sk-card"></div>').join("") + "</div>" +
      '<div class="grid grid-main"><div class="skeleton" style="height:360px;border-radius:18px"></div>' +
      '<div class="skeleton" style="height:360px;border-radius:18px"></div></div>';
    let data;
    try {
      data = await A.api("/api/admin/overview", {
        headers: { "X-Requested-With": "webapp" }
      });
    } catch (e) {
      if (!alive(token)) return;
      root.innerHTML = errorCard(e.message, '<button class="btn btn-soft" data-act="refresh">' + A.icon("refresh") + " تلاش دوباره</button>");
      return;
    }
    if (!alive(token)) return;

    const stats = data.stats || {};
    const exchanges = data.exchanges || [];
    const payments = data.recent_payments || [];
    const users = data.recent_users || [];
    const messages = data.messages || [];
    const totalRev = Number(stats.revenue || 0);
    const activeEx = exchanges.filter(function (e) { return Number(e.is_active) === 1; }).length;
    setUsersBadge(stats);
    const side = A.$("#adminSideStatus");
    if (side) side.textContent = A.toFa(stats.web_users || 0) + " کاربر وب‌اپ • " + A.toFa(activeEx) + " صرافی فعال";

    const donut = A.donut([
      { value: totalRev, color: "var(--primary)" }
    ], A.fmtNum(totalRev, 2), "جمع مبالغ");

    let payRows = payments.map(function (p) {
      return '<tr class="clickable" data-act="payment-view" data-pid="' + p.id + '">' +
        '<td><div class="cell-main">#' + A.toFa(p.id) + '</div><div class="cell-sub">کاربر ' + A.toFa(p.user_id) + "</div></td>" +
        '<td><span class="pill blue">' + A.esc(money(p.amount)) + " " + A.esc(unitOf(p)) + "</span></td>" +
        '<td>' + A.statusPill(p.status) + "</td>" +
        '<td class="tiny muted nowrap">' + A.esc(p.created_at || "-") + "</td>" +
        '<td><div class="cell-actions"><button class="icon-btn sm" data-act="payment-view" data-pid="' + p.id + '" title="جزئیات">' + A.icon("eye") + "</button></div></td></tr>";
    }).join("");

    const recentUsers = users.slice(0, 6).map(function (u) {
      return '<div class="list-item"><div class="avatar">' + A.esc(A.initials(u.name)) + "</div>" +
        '<div class="list-body"><div class="list-title">' + A.esc(u.name) + '</div><div class="list-sub">' +
        A.esc(u.phone || (u.username ? "@" + u.username : "کاربر آبان")) + "</div></div>" +
        '<div class="list-side">' + (u.webapp_sub && u.webapp_sub.active
          ? '<span class="pill green">' + A.toFa(u.webapp_sub.days_left) + " روز</span>"
          : '<span class="pill gray">بدون اشتراک</span>') +
        '<button class="icon-btn sm" data-act="user-open" data-uid="' + u.user_id + '" title="پروفایل">' + A.icon("eye") + "</button></div></div>";
    }).join("");

    const messageRows = messages.slice(0, 5).map(function (m) {
      return '<div class="list-item"><div class="list-icon blue">' + A.icon("send") + "</div>" +
        '<div class="list-body"><div class="list-title">' + A.esc(clip(m.text, 70)) + "</div>" +
        '<div class="list-sub">' + A.esc(targetLabel(m.target_type)) + " • " + A.timeAgo(m.created_at) + "</div></div>" +
        '<div class="list-side"><span class="pill green">' + A.toFa(m.sent || 0) + " ارسال</span>" +
        (Number(m.failed || 0) ? '<span class="pill red">' + A.toFa(m.failed) + " خطا</span>" : "") + "</div></div>";
    }).join("");

    const exchangeRows = exchanges.map(function (ex) {
      return '<div class="list-item"><div class="list-icon ' + (Number(ex.is_active) === 1 ? "green" : "") + '">' + A.icon("exchange") + "</div>" +
        '<div class="list-body"><div class="list-title">' + A.esc(ex.name) + '</div><div class="list-sub mono">' + A.esc(ex.slug) + "</div></div>" +
        '<div class="list-side">' + (Number(ex.is_active) === 1 ? '<span class="pill green">فعال</span>' : '<span class="pill gray">غیرفعال</span>') +
        (Number(ex.is_visible) === 1 ? "" : '<span class="pill amber">پنهان</span>') + "</div></div>";
    }).join("");

    root.innerHTML =
      '<div class="hello"><div><div class="eyebrow">' + A.icon("shield") + " مرکز کنترل</div>" +
      "<h1>داشبورد مدیریت</h1><p>نمای زنده از کاربران، اشتراک‌ها، پرداخت‌ها و وضعیت سامانه.</p></div>" +
      '<div class="hello-actions"><span class="date-chip">' + A.icon("calendar") + A.esc(A.fmtWeekday(Date.now())) + "</span>" +
      '<button class="btn btn-soft" data-act="refresh">' + A.icon("refresh") + " به‌روزرسانی</button>" +
      '<a class="btn btn-primary" href="/">' + A.icon("external") + " نمای کاربری</a></div></div>" +

      '<div class="kpi-grid">' +
        '<div class="kpi"><div class="kpi-top"><div><div class="kpi-label">کاربران وب‌اپ</div><div class="kpi-value">' + A.fmtNum(stats.web_users || 0) + '</div></div><div class="kpi-icon blue">' + A.icon("users") + "</div></div>" +
          '<div class="kpi-foot"><span class="tiny muted">از ' + A.fmtNum(stats.total_users || 0) + " کاربر کل</span></div></div>" +
        '<div class="kpi"><div class="kpi-top"><div><div class="kpi-label">اشتراک فعال</div><div class="kpi-value">' + A.fmtNum(stats.web_active || 0) + '</div></div><div class="kpi-icon green">' + A.icon("check") + "</div></div>" +
          '<div class="kpi-foot"><span class="tiny muted">از ' + A.fmtNum(stats.web_users || 0) + " کاربر وب‌اپ</span></div></div>" +
        '<div class="kpi"><div class="kpi-top"><div><div class="kpi-label">درآمد تأییدشده</div><div class="kpi-value">' + A.fmtNum(stats.revenue || 0, 2) + '<small>جمع مبالغ</small></div></div><div class="kpi-icon violet">' + A.icon("wallet") + "</div></div>" +
          '<div class="kpi-foot"><span class="tiny muted">فقط اشتراک تحلیل ساختاری</span></div></div>' +
        '<div class="kpi"><div class="kpi-top"><div><div class="kpi-label">در انتظار بررسی</div><div class="kpi-value">' + A.fmtNum(stats.pending_payments || 0) + '</div></div><div class="kpi-icon amber">' + A.icon("clock") + "</div></div>" +
          '<div class="kpi-foot"><span class="tiny muted">' + A.fmtNum(stats.pending_requests || 0) + " درخواست اشتراک</span></div></div>" +
      "</div>" +

      '<div class="grid grid-main">' +
        '<div class="card"><div class="card-head bordered"><div><div class="card-title">' + A.icon("wallet") + " آخرین پرداخت‌ها</div>" +
          '<div class="card-sub">آخرین تراکنش‌های ثبت‌شده وب‌اپ</div></div><div class="card-actions">' +
          '<a class="card-link" href="/admin/payments" data-admin-link>مشاهده همه ' + A.icon("chevron-left") + "</a></div></div>" +
          '<div class="card-body tight">' + (payRows
            ? '<div class="table-wrap"><table class="table table-narrow"><thead><tr><th>شناسه</th><th>مبلغ</th><th>وضعیت</th><th>تاریخ</th><th></th></tr></thead><tbody>' + payRows + "</tbody></table></div>"
            : A.emptyState("wallet", "پرداختی ثبت نشده", "پس از ثبت اولین پرداخت، اینجا نمایش داده می‌شود.")) + "</div>" +
          '<div class="card-foot"><span>' + A.toFa(data.payments_total || 0) + " پرداخت ثبت‌شده</span>" +
          '<a class="card-link" href="/admin/payments" data-admin-link>مدیریت پرداخت‌ها ' + A.icon("chevron-left") + "</a></div></div>" +

        '<div class="stack">' +
          '<div class="card"><div class="card-head"><div><div class="card-title">' + A.icon("chart") + " درآمد اشتراک وب‌اپ</div>" +
            '<div class="card-sub">مجموع پرداخت‌های تأییدشده تحلیل ساختاری</div></div></div><div class="card-body">' +
            '<div class="donut-wrap">' + donut + '<div class="donut-legend">' +
              '<div class="donut-item"><i style="background:var(--primary)"></i> درآمد تأییدشده <b>' + A.fmtNum(stats.revenue || 0, 2) + "</b></div>" +
            "</div></div></div></div>" +
          '<div class="card"><div class="card-head"><div><div class="card-title">' + A.icon("exchange") + " وضعیت صرافی‌ها</div></div>" +
            '<div class="card-actions"><a class="card-link" href="/admin/exchanges" data-admin-link>مدیریت ' + A.icon("chevron-left") + "</a></div></div>" +
            '<div class="card-body tight">' + (exchangeRows ? '<div class="list">' + exchangeRows + "</div>" : A.emptyState("exchange", "صرافی‌ای ثبت نشده", "از بخش صرافی‌ها یک منبع داده اضافه کنید.")) + "</div></div>" +
        "</div>" +
      "</div>" +

      '<div class="grid grid-low mt-3">' +
        '<div class="card"><div class="card-head"><div><div class="card-title">' + A.icon("users") + " کاربران تازه</div>" +
          '<div class="card-sub">آخرین کاربران عضو شده</div></div><div class="card-actions">' +
          '<a class="card-link" href="/admin/users" data-admin-link>همه کاربران ' + A.icon("chevron-left") + "</a></div></div>" +
          '<div class="card-body tight">' + (recentUsers ? '<div class="list">' + recentUsers + "</div>" : A.emptyState("users", "کاربری ثبت نشده", "کاربران پس از شروع ربات اینجا نمایش داده می‌شوند.")) + "</div></div>" +

        '<div class="stack">' +
          '<div class="card"><div class="card-head"><div><div class="card-title">' + A.icon("message") + " آخرین پیام‌ها</div></div>" +
            '<div class="card-actions"><a class="card-link" href="/admin/messages" data-admin-link>ارسال پیام ' + A.icon("chevron-left") + "</a></div></div>" +
            '<div class="card-body tight">' + (messageRows ? '<div class="list">' + messageRows + "</div>" : A.emptyState("message", "پیامی ارسال نشده", "از بخش ارسال پیام، اطلاع‌رسانی کنید.")) + "</div></div>" +
          '<div class="card"><div class="card-head"><div><div class="card-title">' + A.icon("activity") + " شاخص‌های سامانه</div></div></div>" +
            '<div class="card-body"><div class="stat-mini">' +
              '<div class="stat-mini-item"><span>استراتژی فعال</span><b>' + A.fmtNum(stats.strategies || 0) + "</b></div>" +
              '<div class="stat-mini-item"><span>دسته‌بندی</span><b>' + A.fmtNum(stats.categories || 0) + "</b></div>" +
              '<div class="stat-mini-item"><span>جستجوی ۲۴ ساعت</span><b>' + A.fmtNum(stats.searches_24h || 0) + "</b></div>" +
              '<div class="stat-mini-item"><span>صرافی فعال</span><b>' + A.fmtNum(activeEx) + "</b></div>" +
            "</div></div></div>" +
        "</div>" +
      "</div>";
  }

  /* ------------------------------------------------------------------
     کاربران
  ------------------------------------------------------------------ */
  async function renderUsers(token) {
    const root = viewEl();
    root.innerHTML =
      '<div class="page-head"><div><div class="eyebrow">' + A.icon("users") + " مدیریت کاربران</div>" +
      '<h1 class="page-title">کاربران</h1><p class="page-sub">جستجو، بررسی اشتراک و مدیریت دسترسی کاربران.</p></div>' +
      '<div class="page-actions"><button class="btn btn-soft" data-act="refresh">' + A.icon("refresh") + " به‌روزرسانی</button></div></div>" +
      '<div class="card"><div class="results-toolbar">' +
        '<div class="searchbox" style="flex:1;min-width:220px;max-width:380px">' + A.icon("search") +
          '<input id="adminUserQ" placeholder="نام، شماره، یوزرنیم یا شناسه کاربر" value="' + A.esc(state.users.q) + '" autocomplete="off"></div>' +
        '<select class="select" id="adminUserSub" style="width:auto;min-width:170px">' +
          '<option value="all"' + (state.users.sub === "all" ? " selected" : "") + ">همه اشتراک‌ها</option>" +
          '<option value="vip"' + (state.users.sub === "vip" ? " selected" : "") + ">اشتراک فعال ربات</option>" +
          '<option value="expired"' + (state.users.sub === "expired" ? " selected" : "") + ">منقضی ربات</option>" +
          '<option value="none"' + (state.users.sub === "none" ? " selected" : "") + ">بدون اشتراک ربات</option>" +
          '<option value="web_vip"' + (state.users.sub === "web_vip" ? " selected" : "") + ">اشتراک فعال وب‌اپ</option>" +
          '<option value="web_expired"' + (state.users.sub === "web_expired" ? " selected" : "") + ">منقضی وب‌اپ</option>" +
        "</select>" +
        '<select class="select" id="adminUserAccess" style="width:auto;min-width:150px">' +
          '<option value="all"' + (state.users.access === "all" ? " selected" : "") + ">همه دسترسی‌ها</option>" +
          '<option value="on"' + (state.users.access === "on" ? " selected" : "") + ">دسترسی فعال</option>" +
          '<option value="off"' + (state.users.access === "off" ? " selected" : "") + ">بدون دسترسی</option>" +
        "</select>" +
      '</div><div class="card-body tight" id="usersTableBox">' + A.loadingRow("در حال دریافت کاربران…") + "</div>" +
      '<div class="card-foot"><span id="usersCount" class="tiny muted"></span><div id="usersPager"></div></div></div>';

    const q = A.$("#adminUserQ");
    const sub = A.$("#adminUserSub");
    const access = A.$("#adminUserAccess");
    if (q) q.addEventListener("input", A.debounce(function () {
      state.users.q = q.value.trim();
      state.users.page = 1;
      loadUsersTable(token);
    }, 320));
    if (sub) sub.addEventListener("change", function () { state.users.sub = sub.value; state.users.page = 1; loadUsersTable(token); });
    if (access) access.addEventListener("change", function () { state.users.access = access.value; state.users.page = 1; loadUsersTable(token); });
    setCleanup(function () {
      if (q) q.oninput = null;
    });
    await loadUsersTable(token);
  }

  async function loadUsersTable(token) {
    const box = A.$("#usersTableBox");
    if (!box) return;
    box.innerHTML = A.loadingRow("در حال دریافت کاربران…");
    let data;
    try {
      data = await A.api("/api/admin/users?q=" + encodeURIComponent(state.users.q) +
        "&sub=" + encodeURIComponent(state.users.sub) +
        "&access=" + encodeURIComponent(state.users.access) +
        "&page=" + state.users.page + "&page_size=" + PAGE_SIZE);
    } catch (e) {
      if (!alive(token)) return;
      box.innerHTML = A.emptyState("alert", "دریافت کاربران انجام نشد", e.message);
      return;
    }
    if (!alive(token) || !A.$("#usersTableBox")) return;
    state.users.items = data.items || [];
    state.users.total = data.total || 0;
    const items = state.users.items;
    if (!items.length) {
      box.innerHTML = A.emptyState("users", "کاربری یافت نشد", "فیلترها را تغییر دهید یا عبارت دیگری جستجو کنید.");
    } else {
      const rows = items.map(function (u) {
        return '<tr class="clickable" data-act="user-open" data-uid="' + u.user_id + '">' +
          '<td><div class="row"><div class="avatar sm">' + A.esc(A.initials(u.name)) + "</div>" +
            '<div><div class="cell-main">' + A.esc(u.name) + '</div><div class="cell-sub">' +
            A.esc(u.phone || (u.username ? "@" + u.username : "کاربر " + A.toFa(u.user_id))) + "</div></div></div></td>" +
          '<td class="tiny muted nowrap">' + A.esc(u.joined_at || "-") + "</td>" +
          '<td class="num">' + A.toFa(u.user_id) + "</td>" +
          "<td>" + subCell(u.bot_sub) + "</td>" +
          "<td>" + subCell(u.webapp_sub) + "</td>" +
          "<td>" + accessCell(u) + "</td>" +
          '<td><div class="cell-actions">' +
            '<button class="icon-btn sm" data-act="user-open" data-uid="' + u.user_id + '" title="پروفایل">' + A.icon("eye") + "</button>" +
            '<button class="icon-btn sm" data-act="user-message" data-uid="' + u.user_id + '" data-name="' + A.esc(u.name) + '" title="ارسال پیام">' + A.icon("message") + "</button>" +
            '<button class="icon-btn sm danger" data-act="user-delete" data-uid="' + u.user_id + '" data-name="' + A.esc(u.name) + '" title="حذف کاربر">' + A.icon("trash") + "</button>" +
          "</div></td></tr>";
      }).join("");
      box.innerHTML = '<div class="table-wrap"><table class="table table-fix"><thead><tr>' +
        "<th>کاربر</th><th>عضویت</th><th>شناسه</th><th>اشتراک ربات</th><th>اشتراک وب‌اپ</th><th>دسترسی</th><th></th>" +
        "</tr></thead><tbody>" + rows + "</tbody></table></div>";
    }
    const count = A.$("#usersCount");
    const pager = A.$("#usersPager");
    const pages = Math.ceil(state.users.total / PAGE_SIZE);
    if (count) count.textContent = A.toFa(state.users.total) + " کاربر";
    if (pager) pager.innerHTML = pagerHTML(state.users.page, pages);
  }

  async function openUser(uid, token) {
    token = token || viewToken;
    let user;
    try {
      user = await A.api("/api/admin/users/" + encodeURIComponent(uid));
    } catch (e) {
      A.toast(e.message, "error");
      return;
    }
    if (!alive(token)) return;
    const m = A.openModal({
      drawer: true,
      size: "wide",
      title: A.esc(user.name || "پروفایل کاربر"),
      sub: "شناسه " + A.toFa(user.user_id) + " • " + A.esc(user.phone || (user.username ? "@" + user.username : "بدون شماره")),
      body: userDetailBody(user),
      footer:
        '<button class="btn btn-ghost" data-modal-close>بستن</button>' +
        '<button class="btn btn-soft" data-act="user-message" data-uid="' + uid + '" data-name="' + A.esc(user.name) + '">' + A.icon("send") + " پیام</button>" +
        '<button class="btn btn-primary" data-act="user-password" data-uid="' + uid + '">' + A.icon("lock") + " صدور رمز</button>" +
        '<button class="btn btn-danger" data-act="user-delete" data-uid="' + uid + '" data-name="' + A.esc(user.name) + '">' + A.icon("trash") + " حذف</button>"
    });
    if (!m) return;
    // access switch change is handled globally
  }

  function userDetailBody(u) {
    const bot = u.bot_sub || {};
    const web = u.webapp_sub || {};
    const payments = u.payments || [];
    const searches = u.searches || [];
    const favorites = u.favorites || [];
    const payRows = payments.map(function (p) {
      const hash = String(p.tx_hash || "");
      const shortHash = hash.length > 14 ? hash.slice(0, 6) + "…" + hash.slice(-4) : hash;
      return '<tr><td class="tiny muted nowrap">' + A.esc(p.created_at || "-") + "</td>" +
        "<td>" + A.esc(p.service_fa || (p.service_type === "webapp" ? "تحلیل ساختاری" : "ربات")) + "</td>" +
        '<td class="num">' + A.esc(money(p.amount)) + " " + A.esc(unitOf(p)) + "</td>" +
        "<td>" + A.esc(p.method_fa || "-") + "</td>" +
        '<td class="mono tiny" title="' + A.esc(hash) + '">' + (hash ? A.esc(shortHash) : "-") + "</td>" +
        '<td><span class="pill ' + (p.status === "approved" ? "green" : (p.status === "rejected" ? "red" : "amber")) + '">' + A.esc(p.status_fa || p.status) + "</span></td></tr>";
    }).join("");
    const searchRows = searches.slice(0, 6).map(function (s) {
      return '<div class="list-item"><div class="list-icon blue">' + A.icon("search") + "</div>" +
        '<div class="list-body"><div class="list-title">اسکن ' + A.esc(TF_LABEL[s.timeframe] || s.timeframe || "-") + "</div>" +
        '<div class="list-sub">' + A.esc(s.created_at || "") + " • " + A.toFa(s.strategy_count || 0) + " استراتژی</div></div>" +
        '<div class="list-side"><span class="pill ' + (s.result_count ? "green" : "gray") + '">' + A.toFa(s.result_count || 0) + " نتیجه</span></div></div>";
    }).join("");
    const favRows = favorites.length ? favorites.map(function (f) {
      const kind = f.kind === "strategy" ? "استراتژی" : (f.kind === "signal" ? "سیگنال" : "نماد");
      return '<span class="fav-chip">' + A.esc(kind) + ": " + A.esc(f.value) + "</span>";
    }).join("") : '<span class="tiny muted">موردی ثبت نشده است.</span>';

    function subCard(title, icon, sub) {
      const active = !!sub.active;
      return '<div class="sub-card' + (active ? " active" : "") + '">' +
        '<div class="sub-top">' + A.icon(icon) + A.esc(title) + "</div>" +
        '<div class="sub-meta">' +
          "وضعیت: " + (active ? "فعال" : "غیرفعال") + "<br>" +
          "شروع: " + A.esc(sub.start || "-") + "<br>" +
          "پایان: " + A.esc(sub.until_jalali || "-") + "<br>" +
          "باقی‌مانده: " + A.toFa(sub.days_left || 0) + " روز<br>" +
          "پرداخت موفق: " + A.toFa(sub.payments || 0) + " • " + A.esc(money(sub.amount || 0)) +
        "</div></div>";
    }

    return '<div class="row wrap" style="gap:12px;margin-bottom:16px">' +
        '<div class="avatar lg violet">' + A.esc(A.initials(u.name)) + "</div>" +
        '<div class="flex-1"><div class="row wrap">' +
          '<span class="pill ' + (u.access_enabled ? "green" : "gray") + '">' + (u.access_enabled ? "دسترسی فعال" : "دسترسی غیرفعال") + "</span>" +
          (u.has_password ? '<span class="pill blue">رمز صادر شده</span>' : '<span class="pill amber">بدون رمز</span>') +
          (u.verified ? '<span class="pill cyan">تأیید شده</span>' : "") +
        "</div></div>" +
        '<label class="switch"><input type="checkbox" data-user-access="' + u.user_id + '"' + (u.access_enabled ? " checked" : "") + '>' +
          '<span class="switch-track"></span><span class="switch-text">دسترسی وب‌اپ</span></label>' +
      "</div>" +

      '<div class="detail-grid">' +
        '<div class="detail-item"><span>شناسه کاربر</span><b>' + A.toFa(u.user_id) + "</b></div>" +
        '<div class="detail-item"><span>شماره تماس</span><b class="mono">' + A.esc(u.phone || "-") + "</b></div>" +
        '<div class="detail-item"><span>نام کاربری</span><b class="mono">' + A.esc(u.username ? "@" + u.username : "-") + "</b></div>" +
        '<div class="detail-item"><span>تاریخ عضویت</span><b>' + A.esc(u.joined_at || "-") + "</b></div>" +
        '<div class="detail-item"><span>آخرین ورود</span><b>' + A.esc(u.last_login || "-") + "</b></div>" +
        '<div class="detail-item"><span>صدور رمز</span><b>' + A.esc(u.password_issued_at || "-") + "</b></div>" +
      "</div>" +

      '<div class="detail-section"><div class="detail-section-title">' + A.icon("layers") + " وضعیت اشتراک‌ها</div>" +
        '<div class="sub-cards">' + subCard("اشتراک ربات", "send", bot) + subCard("اشتراک وب‌اپ", "wallet", web) + "</div>" +
        '<div class="form-actions" style="justify-content:flex-start">' +
          '<button class="btn btn-sm btn-success" data-act="user-sub-add" data-uid="' + u.user_id + '">' + A.icon("plus") + " افزودن روز</button>" +
          '<button class="btn btn-sm btn-ghost" data-act="user-sub-reduce" data-uid="' + u.user_id + '">' + A.icon("minus") + " کاهش روز</button>" +
          '<button class="btn btn-sm btn-danger" data-act="user-sub-remove" data-uid="' + u.user_id + '">' + A.icon("trash") + " حذف اشتراک</button>" +
        "</div></div>" +

      '<div class="detail-section"><div class="detail-section-title">' + A.icon("wallet") + " تاریخچه تراکنش‌ها</div>" +
        (payRows ? '<div class="table-wrap"><table class="table compact table-narrow"><thead><tr><th>تاریخ</th><th>نوع سرویس</th><th>مبلغ</th><th>روش پرداخت</th><th>هش تراکنش</th><th>وضعیت</th></tr></thead><tbody>' + payRows + "</tbody></table></div>"
          : '<div class="notice">' + A.icon("info") + "<div>پرداختی برای این کاربر ثبت نشده است.</div></div>") + "</div>" +

      '<div class="detail-section"><div class="detail-section-title">' + A.icon("history") + " جستجوهای اخیر</div>" +
        (searchRows ? '<div class="list">' + searchRows + "</div>" : '<div class="tiny muted">تاریخچه‌ای وجود ندارد.</div>') + "</div>" +

      '<div class="detail-section"><div class="detail-section-title">' + A.icon("star") + " علاقه‌مندی‌ها</div>" +
        '<div class="fav-chips">' + favRows + "</div></div>";
  }

  async function openUserPassword(uid) {
    const value = await A.prompt({
      title: "صدور رمز ورود",
      label: "مدت اشتراک همراه رمز (روز) — صفر یعنی بدون تغییر",
      value: "0",
      placeholder: "مثلاً ۳۰",
      confirmText: "صدور رمز"
    });
    if (value === null) return;
    const days = Math.max(0, Math.min(3650, parseInt(A.toEn(value).replace(/\D/g, ""), 10) || 0));
    try {
      A.toast("در حال صدور رمز…", "info", 1600);
      const res = await A.api("/api/admin/users/" + uid + "/password", { method: "POST", body: { days: days } });
      showCredential(res, uid);
    } catch (e) {
      A.toast(e.message, "error");
    }
  }

  function showCredential(res, uid) {
    const m = A.openModal({
      size: "narrow",
      title: "رمز ورود جدید",
      sub: "رمز را در اختیار کاربر قرار دهید.",
      body: '<div class="stack">' +
        '<div class="notice ' + (res.sent ? "" : "warn") + '">' + A.icon(res.sent ? "check" : "alert") + "<div>" + A.esc(res.message || "") + "</div></div>" +
        '<div class="credential"><span>' + A.icon("lock") + '</span><code id="newPasswordCode">' + A.esc(res.password || "") + "</code>" +
          '<button class="icon-btn sm" data-copy-password title="کپی رمز">' + A.icon("copy") + "</button></div>" +
        (res.until_jalali && res.until_jalali !== "-" ? '<div class="kv"><span>پایان اشتراک</span><b>' + A.esc(res.until_jalali) + "</b></div>" : "") +
        '<div class="notice">' + A.icon("info") + "<div>برای امنیت، این رمز فقط همین یک بار نمایش داده می‌شود.</div></div></div>",
      footer: '<button class="btn btn-ghost" data-modal-close>بستن</button>' +
        '<button class="btn btn-primary" data-act="user-open" data-uid="' + uid + '">' + A.icon("user") + " مشاهده پروفایل</button>",
      onMount: function (modal) {
        const btn = modal.root.querySelector("[data-copy-password]");
        if (btn) btn.addEventListener("click", function () {
          const code = modal.root.querySelector("#newPasswordCode");
          A.copy(code ? code.textContent : "");
        });
      }
    });
    return m;
  }

  async function userSubscription(uid, action) {
    if (action === "remove") {
      const ok = await A.confirm({
        title: "حذف اشتراک وب‌اپ",
        message: "اشتراک تحلیل ساختاری این کاربر حذف می‌شود. این عملیات قابل بازگشت نیست.",
        danger: true,
        confirmText: "حذف اشتراک"
      });
      if (!ok) return;
    } else {
      const value = await A.prompt({
        title: action === "add" ? "افزودن روز به اشتراک" : "کاهش روز از اشتراک",
        label: "تعداد روز",
        value: "30",
        placeholder: "مثلاً ۳۰",
        confirmText: action === "add" ? "افزودن" : "کاهش"
      });
      if (value === null) return;
      const days = parseInt(A.toEn(value).replace(/\D/g, ""), 10);
      if (!days || days < 1) { A.toast("تعداد روز را درست وارد کنید.", "error"); return; }
      try {
        const res = await A.api("/api/admin/users/" + uid + "/subscription", { method: "POST", body: { action: action, days: days } });
        if (res.password) showCredential(res, uid);
        else {
          A.toast(res.message || "اشتراک به‌روزرسانی شد.", "success");
          openUser(uid);
        }
      } catch (e) {
        A.toast(e.message, "error");
      }
      return;
    }
    try {
      const res = await A.api("/api/admin/users/" + uid + "/subscription", { method: "POST", body: { action: action } });
      A.toast(res.message || "اشتراک حذف شد.", "success");
      openUser(uid);
    } catch (e) {
      A.toast(e.message, "error");
    }
  }

  async function sendUserMessage(uid, name) {
    const text = await A.prompt({
      title: "ارسال پیام",
      label: "متن پیام برای " + (name || "کاربر"),
      placeholder: "متن پیام را بنویسید…",
      multiline: true,
      confirmText: "ارسال پیام"
    });
    if (text === null) return;
    if (!text.trim()) { A.toast("متن پیام خالی است.", "error"); return; }
    try {
      const res = await A.api("/api/admin/users/" + uid + "/message", { method: "POST", body: { text: text.trim() } });
      A.toast(res.message || "پیام ارسال شد.", res.ok ? "success" : "error");
    } catch (e) {
      A.toast(e.message, "error");
    }
  }

  async function deleteUser(uid, name) {
    const ok = await A.confirm({
      title: "حذف کامل کاربر",
      message: "کاربر «" + (name || "انتخاب‌شده") + "» و تمام اطلاعات وب‌اپ او حذف می‌شود. این عملیات قابل بازگشت نیست.",
      danger: true,
      confirmText: "حذف کاربر"
    });
    if (!ok) return;
    try {
      await A.api("/api/admin/users/" + uid, { method: "DELETE" });
      A.toast("کاربر حذف شد.", "success");
      A.closeModal();
      if (routeName() === "users") loadUsersTable(viewToken);
    } catch (e) {
      A.toast(e.message, "error");
    }
  }

  /* ------------------------------------------------------------------
     پرداخت‌ها
  ------------------------------------------------------------------ */
  const PAY_STATUS_FALLBACK = [
    { key: "pending", label: "در انتظار" },
    { key: "approved", label: "تأیید شده" },
    { key: "rejected", label: "رد شده" },
    { key: "suspended", label: "معلق" }
  ];
  function payStatusOptionsHTML() {
    const list = (state.payments.statuses && state.payments.statuses.length) ? state.payments.statuses : PAY_STATUS_FALLBACK;
    let out = '<option value="all"' + (state.payments.status === "all" ? " selected" : "") + ">همه وضعیت‌ها</option>";
    list.forEach(function (st) {
      if (!st || !st.key) return;
      out += '<option value="' + A.esc(st.key) + '"' + (state.payments.status === st.key ? " selected" : "") + ">" + A.esc(st.label || st.key) + "</option>";
    });
    return out;
  }
  async function renderPayments(token) {
    const root = viewEl();
    const services = [
      { key: "all", label: "همه سرویس‌ها" },
      { key: "webapp", label: "تحلیل ساختاری" },
      { key: "bot", label: "اشتراک ربات" }
    ];
    root.innerHTML =
      '<div class="page-head"><div><div class="eyebrow">' + A.icon("wallet") + " مالی</div>" +
      '<h1 class="page-title">پرداخت‌ها و اشتراک‌ها</h1><p class="page-sub">بررسی تراکنش‌های ربات و وب‌اپ، تأیید رسیدها و ثبت پرداخت دستی.</p></div>' +
      '<div class="page-actions"><button class="btn btn-soft" data-act="refresh">' + A.icon("refresh") + " به‌روزرسانی</button>" +
      '<button class="btn btn-primary" data-act="payment-manual">' + A.icon("plus") + " ثبت پرداخت دستی</button></div></div>" +
      '<div class="card"><div class="results-toolbar">' +
        '<div class="segmented" id="payServiceTabs">' + services.map(function (s) {
          return '<button class="seg' + (state.payments.service === s.key ? " active" : "") + '" data-act="pay-service" data-service="' + s.key + '">' + A.esc(s.label) + "</button>";
        }).join("") + "</div>" +
        '<select class="select" id="payStatus" style="width:auto;min-width:160px">' + payStatusOptionsHTML() + "</select>" +
      '</div><div class="card-body tight" id="paymentsTableBox">' + A.loadingRow("در حال دریافت پرداخت‌ها…") + "</div>" +
      '<div class="card-foot"><span id="paymentsCount" class="tiny muted"></span><div id="paymentsPager"></div></div></div>';

    const status = A.$("#payStatus");
    if (status) status.addEventListener("change", function () {
      state.payments.status = status.value;
      state.payments.page = 1;
      loadPaymentsTable(token);
    });
    await loadPaymentsTable(token);
  }

  async function loadPaymentsTable(token) {
    const box = A.$("#paymentsTableBox");
    if (!box) return;
    box.innerHTML = A.loadingRow("در حال دریافت پرداخت‌ها…");
    let data;
    try {
      data = await A.api("/api/admin/payments?service=" + encodeURIComponent(state.payments.service) +
        "&status=" + encodeURIComponent(state.payments.status) +
        "&page=" + state.payments.page + "&page_size=" + PAY_SIZE);
    } catch (e) {
      if (!alive(token)) return;
      box.innerHTML = A.emptyState("alert", "دریافت پرداخت‌ها انجام نشد", e.message);
      return;
    }
    if (!alive(token) || !A.$("#paymentsTableBox")) return;
    state.payments.items = data.items || [];
    state.payments.total = data.total || 0;
    state.payments.statuses = data.statuses || [];
    const statusSel = A.$("#payStatus");
    if (statusSel) {
      const statusOptions = payStatusOptionsHTML();
      if (statusSel.innerHTML !== statusOptions) {
        statusSel.innerHTML = statusOptions;
      }
      statusSel.value = state.payments.status;
      if (statusSel.value !== state.payments.status) {
        state.payments.status = "all";
        statusSel.value = "all";
      }
    }
    if (!state.payments.items.length) {
      box.innerHTML = A.emptyState("wallet", "پرداختی یافت نشد", "برای این فیلتر تراکنشی ثبت نشده است.");
    } else {
      const rows = state.payments.items.map(function (p) {
        return '<tr><td><div class="cell-main">#' + A.toFa(p.id) + '</div><div class="cell-sub">' + A.esc(p.service_fa || p.service_type) + "</div></td>" +
          '<td><button class="btn-link" data-act="user-open" data-uid="' + p.user_id + '">' + A.toFa(p.user_id) + "</button></td>" +
          '<td><div class="cell-main num">' + A.esc(money(p.amount)) + " " + A.esc(unitOf(p)) + "</div>" +
            (p.expected_amount && Number(p.expected_amount) !== Number(p.amount) ? '<div class="cell-sub">مورد انتظار: ' + A.esc(money(p.expected_amount)) + " " + A.esc(unitOf(p)) + "</div>" : "") + "</td>" +
          "<td>" + A.statusPill(p.status) + "</td>" +
          '<td class="tiny">' + A.esc(p.method_fa || p.method) + (p.network ? '<div class="cell-sub mono">' + A.esc(p.network) + "</div>" : "") + "</td>" +
          '<td class="tiny muted nowrap">' + A.esc(p.created_at || "-") + "</td>" +
          "<td>" + paymentRowActions(p) + "</td></tr>";
      }).join("");
      box.innerHTML = '<div class="table-wrap"><table class="table table-fix"><thead><tr>' +
        "<th>شناسه</th><th>کاربر</th><th>مبلغ</th><th>وضعیت</th><th>روش</th><th>تاریخ</th><th></th>" +
        "</tr></thead><tbody>" + rows + "</tbody></table></div>";
    }
    const count = A.$("#paymentsCount");
    const pager = A.$("#paymentsPager");
    if (count) count.textContent = A.toFa(state.payments.total) + " تراکنش";
    if (pager) pager.innerHTML = pagerHTML(state.payments.page, Math.ceil(state.payments.total / PAY_SIZE));
  }

  async function openPayment(pid) {
    let p = state.payments.items.find(function (x) { return Number(x.id) === Number(pid); });
    if (!p) {
      try { const res = await A.api("/api/admin/payments/" + encodeURIComponent(pid)); p = res.item; }
      catch (e) { p = null; }
    }
    if (!p) { A.toast("اطلاعات پرداخت پیدا نشد.", "error"); return; }
    let req = null;
    if (p.request_id) {
      try { req = await A.api("/api/admin/requests/" + p.request_id); } catch (e) { req = null; }
    }
    const media = req && req.has_media
      ? (req.media_kind === "p"
        ? '<div class="receipt-media"><img src="/api/admin/requests/' + p.request_id + '/media" alt="تصویر رسید" loading="lazy"></div>'
        : '<div class="receipt-box"><a class="btn btn-sm btn-soft" href="/api/admin/requests/' + p.request_id + '/media" target="_blank" rel="noopener">' + A.icon("download") + " دریافت فایل رسید</a></div>")
      : "";
    const body = '<div class="stack">' +
      '<div class="row wrap"><span class="pill blue">#' + A.toFa(p.id) + "</span>" + A.statusPill(p.status) +
        '<span class="pill gray">' + A.esc(p.service_fa || p.service_type) + "</span>" +
        '<span class="pill violet">' + A.esc(p.method_fa || p.method) + "</span></div>" +
      '<div class="detail-grid">' +
        '<div class="detail-item"><span>مبلغ</span><b>' + A.esc(money(p.amount)) + " " + A.esc(unitOf(p)) + "</b></div>" +
        '<div class="detail-item"><span>مبلغ مورد انتظار</span><b>' + A.esc(money(p.expected_amount || 0)) + " " + A.esc(unitOf(p)) + "</b></div>" +
        '<div class="detail-item"><span>شناسه کاربر</span><b>' + A.toFa(p.user_id) + "</b></div>" +
        '<div class="detail-item"><span>تاریخ ثبت</span><b>' + A.esc(p.created_at || "-") + "</b></div>" +
        (p.tx_hash ? '<div class="detail-item" style="grid-column:1/-1"><span>هش تراکنش</span><b class="mono">' + A.esc(p.tx_hash) + "</b></div>" : "") +
        (p.sender ? '<div class="detail-item"><span>فرستنده</span><b class="mono">' + A.esc(p.sender) + "</b></div>" : "") +
        (p.receiver ? '<div class="detail-item"><span>گیرنده</span><b class="mono">' + A.esc(p.receiver) + "</b></div>" : "") +
        (p.note ? '<div class="detail-item" style="grid-column:1/-1"><span>یادداشت مدیر</span><b>' + A.esc(p.note) + "</b></div>" : "") +
      "</div>" +
      (req && req.text ? '<div class="receipt-box"><div class="tiny muted mb-1">متن رسید</div><p>' + A.esc(req.text) + "</p>" + media + "</div>" : media) +
      (req ? '<div class="kv"><span>وضعیت درخواست</span><b>' + A.statusPill(req.status) + "</b></div>" : "") +
    "</div>";
    let footer = '<button class="btn btn-ghost" data-modal-close>بستن</button>' +
      '<button class="btn btn-soft" data-act="user-open" data-uid="' + p.user_id + '">' + A.icon("user") + " پروفایل کاربر</button>";
    if (p.status === "pending" || p.status === "suspended") {
      footer += '<button class="btn btn-danger" data-act="payment-reject" data-pid="' + p.id + '">' + A.icon("close") + " رد</button>" +
        '<button class="btn btn-primary" data-act="payment-approve" data-pid="' + p.id + '">' + A.icon("check") + " تأیید</button>";
    } else if (p.status === "approved") {
      footer += '<button class="btn btn-danger" data-act="payment-suspend" data-pid="' + p.id + '">' + A.icon("lock") + " تعلیق</button>";
    }
    A.openModal({ size: "wide", title: "جزئیات پرداخت", sub: "بررسی تراکنش و رسید ارسال‌شده", body: body, footer: footer });
  }

  async function paymentActionModal(pid, action) {
    let p = state.payments.items.find(function (x) { return Number(x.id) === Number(pid); }) || null;
    if (action === "approve") {
      try {
        const res = await A.api("/api/admin/payments/" + encodeURIComponent(pid));
        if (res && res.item) p = res.item;
      } catch (e) { /* از اطلاعات جدول استفاده می‌شود */ }
    }
    const labels = { approve: "تأیید پرداخت", reject: "رد پرداخت", suspend: "تعلیق پرداخت" };
    const messages = {
      approve: "با تأیید پرداخت، اشتراک کاربر فعال یا تمدید می‌شود.",
      reject: "پرداخت رد می‌شود و به کاربر اطلاع داده می‌شود.",
      suspend: "پرداخت معلق می‌شود و دسترسی مرتبط با آن بررسی خواهد شد."
    };
    const manual = p && p.method !== "bsc" && p.method !== "auto";
    const isCard = !!(p && p.is_card);
    const networks = (p && p.networks) || [];
    let txFields = "";
    if (action === "approve" && manual) {
      const options = networks.map(function (n) {
        return '<option value="' + A.esc(n.key) + '"' + ((p.network && p.network === n.key) ? " selected" : "") + ">" + A.esc(n.title) + "</option>";
      }).join("");
      txFields =
        '<div class="field"><label class="label">' + (isCard
          ? "شماره پیگیری بانکی (اختیاری)"
          : 'هش تراکنش (TxID) <span class="req">*</span>') + "</label>" +
          '<input class="input textarea-ltr" id="approveTx" dir="ltr" placeholder="' + (isCard ? "شماره پیگیری تراکنش بانکی" : "0x… یا hash تراکنش") + '" value="' + A.esc(p.tx_hash || "") + '"></div>' +
        '<div class="form-grid">' +
          '<div class="field"><label class="label">مبلغ واریزی ' + (isCard ? "(دلار)" : "(واحد همان شبکه)") + "</label>" +
            '<input class="input" id="approveAmount" type="number" min="0" step="0.000001" value="' + A.esc(String(p.amount || p.expected_amount || 0)) + '"></div>' +
          (isCard ? '<div class="field"><label class="label">روش</label><input class="input" value="کارت‌به‌کارت" disabled></div>'
            : '<div class="field"><label class="label">شبکه</label><select class="select" id="approveNetwork">' +
              (options || '<option value="">شبکه‌ای ثبت نشده</option>') + "</select></div>") +
        "</div>" +
        '<div class="hint">' + (isCard
          ? "برای کارت‌به‌کارت، مبلغ دریافتی را وارد کنید؛ شماره پیگیری در سوابق ثبت می‌شود."
          : "هش تأییدشده در سوابق ذخیره می‌شود و تأیید دوباره‌ی همان هش ممکن نیست.") + "</div>";
    }
    const m = A.openModal({
      size: "narrow",
      title: labels[action] || "عملیات پرداخت",
      body: '<div class="stack"><div class="notice ' + (action === "approve" ? "" : "warn") + '">' + A.icon(action === "approve" ? "check" : "alert") +
        "<div>" + messages[action] + "</div></div>" + txFields +
        '<div class="field"><label class="label">یادداشت (اختیاری)</label>' +
        '<textarea class="textarea" id="paymentNote" placeholder="توضیح برای ثبت در سوابق…"></textarea></div></div>',
      footer: '<button class="btn btn-ghost" data-modal-close>انصراف</button>' +
        '<button class="btn ' + (action === "approve" ? "btn-primary" : "btn-danger") + '" data-confirm-action>' +
        (action === "approve" ? A.icon("check") + " تأیید" : (action === "reject" ? A.icon("close") + " رد" : A.icon("lock") + " تعلیق")) + "</button>",
      onMount: function (modal) {
        modal.root.querySelector("[data-confirm-action]").addEventListener("click", async function () {
          const btn = this;
          const noteEl = modal.root.querySelector("#paymentNote");
          const txEl = modal.root.querySelector("#approveTx");
          const amountEl = modal.root.querySelector("#approveAmount");
          const networkEl = modal.root.querySelector("#approveNetwork");
          if (action === "approve" && manual && !isCard) {
            if (!txEl || !txEl.value.trim()) {
              A.toast("هش تراکنش (TxID) را وارد کنید.", "error");
              if (txEl) txEl.focus();
              return;
            }
            if (!networkEl || !networkEl.value) {
              A.toast("شبکه‌ی پرداخت را انتخاب کنید.", "error");
              return;
            }
          }
          A.setBusy(btn, true, "در حال ثبت…");
          try {
            const body = { action: action, note: noteEl ? noteEl.value.trim() : "" };
            if (action === "approve" && manual) {
              body.tx_hash = txEl ? txEl.value.trim() : "";
              body.amount = amountEl && amountEl.value !== "" ? Number(amountEl.value) : null;
              if (!isCard && networkEl) body.network = networkEl.value;
            }
            const res = await A.api("/api/admin/payments/" + pid + "/action", { method: "POST", body: body });
            A.toast(res.message || "وضعیت پرداخت به‌روزرسانی شد.", "success");
            A.closeModal();
            refreshSideStatus();
            if (routeName() === "payments") loadPaymentsTable(viewToken);
          } catch (e) {
            A.setBusy(btn, false);
            A.toast(e.message, "error");
          }
        });
      }
    });
    return m;
  }

  async function openManualPayment() {
    let data;
    try {
      data = await A.api("/api/admin/plans");
    } catch (e) {
      A.toast(e.message, "error");
      return;
    }
    const plans = data.plans || [];
    function planBy(key) { return plans.find(function (p) { return p.plan === key; }) || plans[0] || { networks: [], amount: 0 }; }
    function netOptions(plan, selected) {
      return (plan.networks || []).map(function (n) {
        return '<option value="' + A.esc(n.network_key) + '"' + (n.network_key === selected ? " selected" : "") + ">" + A.esc(n.title) + "</option>";
      }).join("");
    }
    const first = planBy("webapp");
    A.openModal({
      size: "",
      title: "ثبت پرداخت دستی",
      sub: "پرداخت تأییدشده به اشتراک کاربر اضافه می‌شود و هش تراکنش در سوابق باقی می‌ماند.",
      body: '<div class="stack"><div class="form-grid">' +
        '<div class="field"><label class="label">شناسه کاربر <span class="req">*</span></label><input class="input" id="manualUid" type="number" min="1" placeholder="مثلاً 123456789"></div>' +
        '<div class="field"><label class="label">نوع اشتراک</label><select class="select" id="manualPlan">' +
          plans.map(function (p) { return '<option value="' + A.esc(p.plan) + '"' + (p.plan === "webapp" ? " selected" : "") + ">" + A.esc(p.label) + "</option>"; }).join("") +
        "</select></div>" +
        '<div class="field"><label class="label">مبلغ واریزی (واحد همان شبکه) <span class="req">*</span></label><input class="input" id="manualAmount" type="number" min="0" step="0.01" value="' + A.esc(String(first.amount || 0)) + '"></div>' +
        '<div class="field"><label class="label">شبکه <span class="req">*</span></label><select class="select" id="manualNetwork">' + netOptions(first, "") + "</select></div>" +
        '<div class="field full"><label class="label">هش تراکنش (TxID) <span class="req">*</span></label><input class="input textarea-ltr" id="manualTx" dir="ltr" placeholder="0x… / hash"></div>' +
        '<div class="field"><label class="label">آدرس فرستنده (اختیاری)</label><input class="input textarea-ltr" id="manualSender" dir="ltr" placeholder="آدرس کیف پول فرستنده"></div>' +
        '<div class="field"><label class="label">یادداشت</label><input class="input" id="manualNote" placeholder="توضیح کوتاه"></div>' +
      "</div></div>",
      footer: '<button class="btn btn-ghost" data-modal-close>انصراف</button>' +
        '<button class="btn btn-primary" data-manual-submit>' + A.icon("check") + " تأیید و فعال‌سازی</button>",
      onMount: function (modal) {
        const planEl = modal.root.querySelector("#manualPlan");
        const amountEl = modal.root.querySelector("#manualAmount");
        const netEl = modal.root.querySelector("#manualNetwork");
        planEl.addEventListener("change", function () {
          const p = planBy(planEl.value);
          amountEl.value = p.amount || 0;
          netEl.innerHTML = netOptions(p, "");
        });
        modal.root.querySelector("[data-manual-submit]").addEventListener("click", async function () {
          const btn = this;
          const uid = Number(modal.root.querySelector("#manualUid").value || 0);
          const tx = (modal.root.querySelector("#manualTx").value || "").trim();
          const network = netEl.value || "";
          if (!uid) { A.toast("شناسه کاربر را وارد کنید.", "error"); return; }
          if (!tx) { A.toast("هش تراکنش را وارد کنید.", "error"); return; }
          if (!network) { A.toast("شبکه را انتخاب کنید.", "error"); return; }
          A.setBusy(btn, true, "در حال ثبت…");
          try {
            const res = await A.api("/api/admin/payments/manual", {
              method: "POST",
              body: {
                user_id: uid,
                plan: planEl.value,
                amount: amountEl.value === "" ? 0 : Number(amountEl.value),
                tx_hash: tx,
                network: network,
                sender: (modal.root.querySelector("#manualSender").value || "").trim(),
                note: (modal.root.querySelector("#manualNote").value || "").trim()
              }
            });
            A.toast(res.message || "پرداخت دستی ثبت شد.", "success");
            A.closeModal();
            if (routeName() === "payments") loadPaymentsTable(viewToken);
          } catch (e) {
            A.setBusy(btn, false);
            A.toast(e.message, "error");
          }
        });
      }
    });
  }

  /* ------------------------------------------------------------------
     استراتژی‌ها
  ------------------------------------------------------------------ */
  async function renderStrategies(token) {
    const root = viewEl();
    root.innerHTML =
      '<div class="page-head"><div><div class="eyebrow">' + A.icon("layers") + " کاتالوگ تحلیل</div>" +
      '<h1 class="page-title">استراتژی‌ها و راهنما</h1><p class="page-sub">فعال‌سازی استراتژی‌ها و مدیریت تصویر و توضیح راهنما.</p></div>' +
      '<div class="page-actions"><button class="btn btn-soft" data-act="refresh">' + A.icon("refresh") + " به‌روزرسانی</button></div></div>" +
      '<div class="card"><div class="results-toolbar">' +
        '<div class="searchbox" style="flex:1;min-width:220px;max-width:360px">' + A.icon("search") +
          '<input id="stratQ" placeholder="جستجوی عنوان یا کلید استراتژی" value="' + A.esc(state.strategies.q) + '" autocomplete="off"></div>' +
        '<select class="select" id="stratCategory" style="width:auto;min-width:190px"><option value="all">همه دسته‌بندی‌ها</option></select>' +
        '<span class="pill green" id="stratActiveCount">—</span>' +
      '</div><div class="card-body tight" id="strategiesTableBox">' + A.loadingRow("در حال دریافت استراتژی‌ها…") + "</div>" +
      '<div class="card-foot"><span id="strategiesCount" class="tiny muted"></span><div id="strategiesPager"></div></div></div>';

    const q = A.$("#stratQ");
    const cat = A.$("#stratCategory");
    if (q) q.addEventListener("input", A.debounce(function () {
      state.strategies.q = q.value.trim();
      state.strategies.page = 1;
      loadStrategiesTable(token);
    }, 320));
    if (cat) cat.addEventListener("change", function () {
      state.strategies.category = cat.value;
      state.strategies.page = 1;
      loadStrategiesTable(token);
    });
    await loadStrategiesTable(token);
  }

  async function loadStrategiesTable(token) {
    const box = A.$("#strategiesTableBox");
    if (!box) return;
    box.innerHTML = A.loadingRow("در حال دریافت استراتژی‌ها…");
    let data;
    try {
      data = await A.api("/api/admin/strategies?q=" + encodeURIComponent(state.strategies.q) +
        "&category=" + encodeURIComponent(state.strategies.category) +
        "&page=" + state.strategies.page + "&page_size=" + STRAT_SIZE);
    } catch (e) {
      if (!alive(token)) return;
      box.innerHTML = A.emptyState("alert", "دریافت استراتژی‌ها انجام نشد", e.message);
      return;
    }
    if (!alive(token) || !A.$("#strategiesTableBox")) return;
    state.strategies.items = data.items || [];
    state.strategies.total = data.total || 0;
    state.strategies.categories = data.categories || [];
    const catSel = A.$("#stratCategory");
    if (catSel && catSel.options.length <= 1) {
      state.strategies.categories.forEach(function (c) {
        const opt = document.createElement("option");
        opt.value = c;
        opt.textContent = c;
        catSel.appendChild(opt);
      });
      catSel.value = state.strategies.category;
    }
    const activeCount = A.$("#stratActiveCount");
    if (activeCount) activeCount.textContent = A.toFa(data.active_count || 0) + " استراتژی فعال";

    if (!state.strategies.items.length) {
      box.innerHTML = A.emptyState("layers", "استراتژی‌ای یافت نشد", "عبارت جستجو یا دسته‌بندی را تغییر دهید.");
    } else {
      const rows = state.strategies.items.map(function (s) {
        const img = s.guide_image_path
          ? '<a href="' + A.esc(A.safeImage(s.guide_image_path)) + '" target="_blank" rel="noopener" title="مشاهده راهنما">' +
            '<img src="' + A.esc(A.safeImage(s.guide_image_path)) + '" alt="راهنما" style="width:52px;height:34px;object-fit:cover;border-radius:7px;border:1px solid var(--border)"></a>'
          : '<span class="tiny muted">بدون تصویر</span>';
        return "<tr>" +
          '<td><div class="cell-main">' + A.esc(s.title) + '</div><div class="cell-sub mono">' + A.esc(s.key) + "</div></td>" +
          '<td class="tiny">' + A.esc(s.category) + "<br><span class=\"muted\">" + A.esc(s.strategy_type) + "</span></td>" +
          "<td>" + A.signalPill(s.signal_type) + "</td>" +
          "<td>" + img + "</td>" +
          '<td><label class="switch"><input type="checkbox" data-strategy-toggle="' + A.esc(s.key) + '"' + (s.active ? " checked" : "") + '><span class="switch-track"></span></label></td>' +
          '<td><div class="cell-actions">' +
            '<button class="icon-btn sm" data-act="strategy-guide" data-key="' + A.esc(s.key) + '" title="ویرایش راهنما">' + A.icon("edit") + "</button>" +
            (s.guide_image_path ? '<button class="icon-btn sm danger" data-act="strategy-guide-delete" data-key="' + A.esc(s.key) + '" data-title="' + A.esc(s.title) + '" title="حذف راهنما">' + A.icon("trash") + "</button>" : "") +
          "</div></td></tr>";
      }).join("");
      box.innerHTML = '<div class="table-wrap"><table class="table table-fix"><thead><tr>' +
        "<th>استراتژی</th><th>دسته‌بندی</th><th>سیگنال</th><th>راهنما</th><th>فعال</th><th></th>" +
        "</tr></thead><tbody>" + rows + "</tbody></table></div>";
    }
    const count = A.$("#strategiesCount");
    const pager = A.$("#strategiesPager");
    if (count) count.textContent = A.toFa(state.strategies.total) + " استراتژی";
    if (pager) pager.innerHTML = pagerHTML(state.strategies.page, Math.ceil(state.strategies.total / STRAT_SIZE));
  }

  function openGuide(key) {
    const item = state.strategies.items.find(function (s) { return s.key === key; });
    if (!item) { A.toast("استراتژی پیدا نشد.", "error"); return; }
    const existing = A.safeImage(item.guide_image_path || "");
    const body = '<div class="stack">' +
      '<div class="notice">' + A.icon("info") + "<div>راهنمای هر استراتژی در وب‌اپ برای کاربران نمایش داده می‌شود. تصویر اختیاری است.</div></div>" +
      '<form id="guideForm" class="form-grid">' +
        '<div class="field full"><label class="label">عنوان راهنما</label>' +
          '<input class="input" name="title" maxlength="200" value="' + A.esc(item.guide_title || item.title) + '"></div>' +
        '<div class="field full"><label class="label">توضیحات راهنما</label>' +
          '<textarea class="textarea" name="description" maxlength="2000" placeholder="روش استفاده و نکات این استراتژی…">' + A.esc(item.guide_description || item.description || "") + "</textarea></div>" +
        '<div class="field"><label class="label">عرض تصویر</label><input class="input" name="width" type="number" min="120" max="1200" value="' + (item.guide_image_width || 420) + '"></div>' +
        '<div class="field"><label class="label">ارتفاع تصویر</label><input class="input" name="height" type="number" min="90" max="1200" value="' + (item.guide_image_height || 300) + '"></div>' +
        '<div class="field full"><label class="label">تصویر راهنما</label>' +
          '<div class="upload-drop" id="guideDrop">' + A.icon("image") + "<b>انتخاب یا رها کردن تصویر</b><span>PNG، JPG، GIF یا WebP — حداکثر ۵ مگابایت</span>" +
          '<input type="file" id="guideFile" accept="image/png,image/jpeg,image/gif,image/webp" hidden></div>' +
          '<div class="upload-preview" id="guidePreview"' + (existing ? "" : " hidden") + '><img id="guidePreviewImg" src="' + A.esc(existing) + '" alt="پیش‌نمایش راهنما"></div>' +
        "</div>" +
      "</form></div>";
    const footer = (existing ? '<button class="btn btn-danger" data-delete-guide>' + A.icon("trash") + " حذف راهنما</button>" : "") +
      '<button class="btn btn-ghost" data-modal-close>انصراف</button>' +
      '<button class="btn btn-primary" data-save-guide>' + A.icon("check") + " ذخیره راهنما</button>";
    A.openModal({
      size: "wide",
      title: "راهنمای استراتژی",
      sub: A.esc(item.title),
      body: body,
      footer: footer,
      onMount: function (modal) {
        let picked = null;
        const drop = modal.root.querySelector("#guideDrop");
        const file = modal.root.querySelector("#guideFile");
        const preview = modal.root.querySelector("#guidePreview");
        const img = modal.root.querySelector("#guidePreviewImg");
        function choose(f) {
          if (!f) return;
          const okTypes = ["image/png", "image/jpeg", "image/gif", "image/webp"];
          if (okTypes.indexOf(f.type) < 0) { A.toast("فرمت تصویر مجاز نیست.", "error"); return; }
          if (f.size > 5 * 1024 * 1024) { A.toast("حجم تصویر باید کمتر از ۵ مگابایت باشد.", "error"); return; }
          picked = f;
          img.src = URL.createObjectURL(f);
          preview.hidden = false;
        }
        drop.addEventListener("click", function () { file.click(); });
        file.addEventListener("change", function () { choose(file.files && file.files[0]); });
        drop.addEventListener("dragover", function (e) { e.preventDefault(); drop.style.borderColor = "var(--primary)"; });
        drop.addEventListener("dragleave", function () { drop.style.borderColor = ""; });
        drop.addEventListener("drop", function (e) {
          e.preventDefault();
          drop.style.borderColor = "";
          choose(e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0]);
        });
        const saveBtn = modal.root.querySelector("[data-save-guide]");
        async function save() {
          const form = modal.root.querySelector("#guideForm");
          const fd = new FormData();
          fd.append("title", form.title.value.trim());
          fd.append("description", form.description.value.trim());
          fd.append("width", form.width.value || "420");
          fd.append("height", form.height.value || "300");
          if (picked) fd.append("image", picked, picked.name);
          A.setBusy(saveBtn, true, "در حال ذخیره…");
          try {
            await A.api("/api/admin/strategies/" + encodeURIComponent(key) + "/guide", { method: "POST", body: fd });
            A.toast("راهنمای استراتژی ذخیره شد.", "success");
            A.closeModal();
            loadStrategiesTable(viewToken);
          } catch (e) {
            A.setBusy(saveBtn, false);
            A.toast(e.message, "error");
          }
        }
        saveBtn.addEventListener("click", save);
        modal.root.querySelector("#guideForm").addEventListener("submit", function (e) { e.preventDefault(); save(); });
        const delBtn = modal.root.querySelector("[data-delete-guide]");
        if (delBtn) delBtn.addEventListener("click", function () { deleteGuide(key, item.title); });
      }
    });
  }

  async function deleteGuide(key, title) {
    const ok = await A.confirm({
      title: "حذف راهنما",
      message: "راهنمای استراتژی «" + (title || key) + "» و تصویر آن حذف می‌شود.",
      danger: true,
      confirmText: "حذف راهنما"
    });
    if (!ok) return;
    try {
      await A.api("/api/admin/strategies/" + encodeURIComponent(key) + "/guide", { method: "DELETE" });
      A.toast("راهنما حذف شد.", "success");
      if (routeName() === "strategies") loadStrategiesTable(viewToken);
    } catch (e) {
      A.toast(e.message, "error");
    }
  }

  /* ------------------------------------------------------------------
     صرافی‌ها
  ------------------------------------------------------------------ */
  async function renderExchanges(token) {
    const root = viewEl();
    root.innerHTML =
      '<div class="page-head"><div><div class="eyebrow">' + A.icon("exchange") + " منابع داده</div>" +
      '<h1 class="page-title">صرافی‌ها</h1><p class="page-sub">اولویت، فعال‌بودن و نمایش صرافی‌ها در تحلیل ساختاری.</p></div>' +
      '<div class="page-actions"><button class="btn btn-soft" data-act="exchange-sync">' + A.icon("refresh") + " همگام‌سازی نمادها</button>" +
      '<button class="btn btn-primary" data-act="exchange-add">' + A.icon("plus") + " افزودن صرافی</button></div></div>" +
      '<div class="card"><div class="card-body tight" id="exchangesTableBox">' + A.loadingRow("در حال دریافت صرافی‌ها…") + "</div></div>";

    let data;
    try {
      data = await A.api("/api/admin/exchanges");
    } catch (e) {
      if (!alive(token)) return;
      const box = A.$("#exchangesTableBox");
      if (box) box.innerHTML = A.emptyState("alert", "دریافت صرافی‌ها انجام نشد", e.message);
      return;
    }
    if (!alive(token) || !A.$("#exchangesTableBox")) return;
    state.exchanges.items = data.items || [];
    const items = state.exchanges.items;
    const box = A.$("#exchangesTableBox");
    if (!items.length) {
      box.innerHTML = A.emptyState("exchange", "صرافی‌ای ثبت نشده", "برای شروع تحلیل، یک صرافی اضافه کنید.",
        '<button class="btn btn-primary btn-sm" data-act="exchange-add">' + A.icon("plus") + " افزودن صرافی</button>");
      return;
    }
    const rows = items.map(function (ex) {
      return "<tr>" +
        '<td><div class="cell-main">' + A.esc(ex.name) + '</div><div class="cell-sub mono">' + A.esc(ex.slug) + "</div></td>" +
        '<td class="mono tiny">' + A.esc(ex.ccxt_id || "-") + "</td>" +
        '<td class="tiny muted"><span class="mono">' + A.esc(clip(ex.api_url || "-", 44)) + "</span></td>" +
        '<td class="num">' + A.toFa(ex.priority || 100) + "</td>" +
        '<td><label class="switch"><input type="checkbox" data-exchange-flag="' + ex.id + '" data-field="is_active"' + (Number(ex.is_active) === 1 ? " checked" : "") + '><span class="switch-track"></span></label></td>' +
        '<td><label class="switch"><input type="checkbox" data-exchange-flag="' + ex.id + '" data-field="is_visible"' + (Number(ex.is_visible) === 1 ? " checked" : "") + '><span class="switch-track"></span></label></td>' +
        '<td><div class="cell-actions">' +
          '<button class="icon-btn sm" data-act="exchange-edit" data-id="' + ex.id + '" title="ویرایش">' + A.icon("edit") + "</button>" +
          '<button class="icon-btn sm danger" data-act="exchange-delete" data-id="' + ex.id + '" data-name="' + A.esc(ex.name) + '" title="حذف">' + A.icon("trash") + "</button>" +
        "</div></td></tr>";
    }).join("");
    box.innerHTML = '<div class="table-wrap"><table class="table table-fix"><thead><tr>' +
      "<th>صرافی</th><th>شناسه CCXT</th><th>آدرس API</th><th>اولویت</th><th>فعال</th><th>نمایش</th><th></th>" +
      "</tr></thead><tbody>" + rows + "</tbody></table></div>" +
      '<div class="notice mt-2">' + A.icon("info") + "<div>صرافی فعال در تحلیل و اسکن شرکت می‌کند. صرافی پنهان در فهرست کاربران نمایش داده نمی‌شود. اولویت کمتر یعنی منبع اصلی نماد.</div></div>";
  }

  function openExchangeForm(id) {
    const ex = id ? state.exchanges.items.find(function (x) { return Number(x.id) === Number(id); }) : null;
    const value = ex || { name: "", slug: "", ccxt_id: "", api_url: "", priority: 100, is_active: 1, is_visible: 1 };
    const body = '<form id="exchangeForm" class="form-grid">' +
      '<div class="field"><label class="label">نام نمایشی <span class="req">*</span></label>' +
        '<input class="input" name="name" maxlength="64" value="' + A.esc(value.name) + '" placeholder="مثلاً Binance"></div>' +
      '<div class="field"><label class="label">نامک (slug) <span class="req">*</span></label>' +
        '<input class="input textarea-ltr" name="slug" maxlength="40" value="' + A.esc(value.slug) + '" placeholder="binance"></div>' +
      '<div class="field"><label class="label">شناسه CCXT</label>' +
        '<input class="input textarea-ltr" name="ccxt_id" maxlength="40" value="' + A.esc(value.ccxt_id || "") + '" placeholder="binance"></div>' +
      '<div class="field"><label class="label">اولویت</label>' +
        '<input class="input" name="priority" type="number" min="1" max="9999" value="' + (value.priority || 100) + '"></div>' +
      '<div class="field full"><label class="label">آدرس API</label>' +
        '<input class="input textarea-ltr" name="api_url" maxlength="300" value="' + A.esc(value.api_url || "") + '" placeholder="https://fapi.binance.com" dir="ltr"></div>' +
      '<div class="field"><label class="switch"><input type="checkbox" name="is_active"' + (Number(value.is_active) === 1 ? " checked" : "") + '><span class="switch-track"></span><span class="switch-text">فعال در تحلیل</span></label></div>' +
      '<div class="field"><label class="switch"><input type="checkbox" name="is_visible"' + (Number(value.is_visible) === 1 ? " checked" : "") + '><span class="switch-track"></span><span class="switch-text">نمایش به کاربران</span></label></div>' +
      '<div class="field full"><div class="hint">برای صرافی‌های سازگار با CCXT شناسه را وارد کنید. صرافی بدون شناسه CCXT باید API سفارشی داشته باشد.</div></div>' +
    "</form>";
    A.openModal({
      size: "wide",
      title: id ? "ویرایش صرافی" : "افزودن صرافی",
      sub: "تنظیمات منبع داده و اولویت پردازش",
      body: body,
      footer: '<button class="btn btn-ghost" data-modal-close>انصراف</button>' +
        '<button class="btn btn-primary" data-save-exchange>' + A.icon("check") + " ذخیره</button>",
      onMount: function (modal) {
        const btn = modal.root.querySelector("[data-save-exchange]");
        async function save() {
          const form = modal.root.querySelector("#exchangeForm");
          const payload = {
            name: form.name.value.trim(),
            slug: form.slug.value.trim().toLowerCase(),
            ccxt_id: form.ccxt_id.value.trim(),
            api_url: form.api_url.value.trim(),
            priority: parseInt(form.priority.value, 10) || 100,
            is_active: form.is_active.checked,
            is_visible: form.is_visible.checked
          };
          if (!payload.name || !payload.slug) { A.toast("نام و نامک صرافی الزامی است.", "error"); return; }
          A.setBusy(btn, true, "در حال ذخیره…");
          try {
            if (id) {
              await A.api("/api/admin/exchanges/" + id, { method: "PUT", body: payload });
            } else {
              await A.api("/api/admin/exchanges", { method: "POST", body: payload });
            }
            A.toast("اطلاعات صرافی ذخیره شد.", "success");
            A.closeModal();
            refreshCurrent();
          } catch (e) {
            A.setBusy(btn, false);
            A.toast(e.message, "error");
          }
        }
        btn.addEventListener("click", save);
        modal.root.querySelector("#exchangeForm").addEventListener("submit", function (e) { e.preventDefault(); save(); });
      }
    });
  }

  async function syncExchanges() {
    const ok = await A.confirm({
      title: "همگام‌سازی صرافی‌ها",
      message: "فهرست نمادهای صرافی‌های فعال دریافت و به‌روزرسانی می‌شود. این عملیات ممکن است کمی زمان ببرد.",
      confirmText: "شروع همگام‌سازی"
    });
    if (!ok) return;
    A.toast("همگام‌سازی آغاز شد؛ لطفاً صبر کنید…", "info", 5000);
    try {
      const res = await A.api("/api/admin/exchanges/sync", { method: "POST" });
      const errors = res.errors || {};
      const errorCount = Array.isArray(errors) ? errors.length : Object.keys(errors).length;
      if (errorCount) A.toast("همگام‌سازی با خطا در " + A.toFa(errorCount) + " صرافی پایان یافت.", "warn", 6000);
      else A.toast("همگام‌سازی صرافی‌ها کامل شد.", "success");
      refreshCurrent();
    } catch (e) {
      A.toast(e.message, "error");
    }
  }

  async function deleteExchange(id, name) {
    const ok = await A.confirm({
      title: "حذف صرافی",
      message: "صرافی «" + (name || id) + "» حذف می‌شود و نمادهای آن از تحلیل کنار می‌روند.",
      danger: true,
      confirmText: "حذف صرافی"
    });
    if (!ok) return;
    try {
      await A.api("/api/admin/exchanges/" + id, { method: "DELETE" });
      A.toast("صرافی حذف شد.", "success");
      refreshCurrent();
    } catch (e) {
      A.toast(e.message, "error");
    }
  }

  /* ------------------------------------------------------------------
     پیام‌ها
  ------------------------------------------------------------------ */
  async function renderMessages(token) {
    const root = viewEl();
    root.innerHTML =
      '<div class="page-head"><div><div class="eyebrow">' + A.icon("message") + " ارتباط با کاربران</div>" +
      '<h1 class="page-title">ارسال پیام</h1><p class="page-sub">پیام تک‌نفره، هدفمند یا گروهی برای کاربران وب‌اپ.</p></div>' +
      '<div class="page-actions"><button class="btn btn-soft" data-act="refresh">' + A.icon("refresh") + " به‌روزرسانی</button></div></div>" +
      '<div class="grid grid-3-2">' +
        '<div class="card" id="msgComposerCard">' + composerHTML() + "</div>" +
        '<div class="card"><div class="card-head bordered"><div><div class="card-title">' + A.icon("history") +
          " تاریخچه ارسال</div><div class=\"card-sub\">آخرین پیام‌های ارسال‌شده</div></div></div>" +
          '<div class="card-body tight" id="msgHistoryBox">' + A.loadingRow("در حال دریافت تاریخچه…") + "</div></div>" +
      "</div>";
    bindComposer(token);
    await loadMessageHistory(token);
  }

  function composerHTML() {
    const tab = state.message.tab;
    let targetBox = "";
    if (tab === "single") {
      targetBox = '<div class="setting-block"><div class="setting-title">' + A.icon("user") + " مخاطب پیام</div>" +
        '<div class="setting-desc">کاربر را جستجو و انتخاب کنید.</div>' +
        '<div class="searchbox">' + A.icon("search") + '<input id="msgUserSearch" placeholder="نام، شماره یا شناسه کاربر" autocomplete="off"></div>' +
        '<div id="msgSelected" style="margin-top:9px"' + (state.message.user ? "" : " hidden") + '>' +
          (state.message.user ? selectedUserHTML() : "") + "</div>" +
        '<div id="msgUserResults" class="list" style="margin-top:10px"></div></div>';
    } else if (tab === "targeted") {
      targetBox = '<div class="setting-block"><div class="setting-title">' + A.icon("filter") + " شرایط دریافت‌کنندگان</div>" +
        '<div class="setting-desc">پیام برای کاربران دارای رمز ورود و مطابق شرایط ارسال می‌شود.</div>' +
        '<div class="form-grid">' +
          '<div class="field"><label class="label">وضعیت اشتراک</label><select class="select" id="msgSubFilter">' +
            '<option value="all">همه کاربران</option>' +
            '<option value="active">اشتراک فعال</option>' +
            '<option value="expired">اشتراک منقضی</option>' +
            '<option value="none">بدون اشتراک</option>' +
          "</select></div>" +
          '<div class="field"><label class="label">عضویت در چند روز اخیر</label>' +
            '<input class="input" id="msgJoinedDays" type="number" min="0" max="365" placeholder="۰ = بدون محدودیت"></div>' +
        "</div></div>";
    } else {
      targetBox = '<div class="notice">' + A.icon("users") + "<div>این پیام برای تمام کاربرانی که رمز ورود وب‌اپ دارند ارسال می‌شود. ارسال به‌صورت گروهی و با صف انجام می‌گیرد.</div></div>";
    }
    return '<div class="card-head bordered"><div><div class="card-title">' + A.icon("send") + " نوشتن پیام</div>" +
      '<div class="card-sub">متن پیام بدون قالب‌بندی HTML ارسال می‌شود.</div></div></div>' +
      '<div class="card-body"><div class="admin-tabs mb-2">' +
        '<button class="' + (tab === "single" ? "active" : "") + '" data-act="msg-tab" data-tab="single">تک کاربر</button>' +
        '<button class="' + (tab === "targeted" ? "active" : "") + '" data-act="msg-tab" data-tab="targeted">هدفمند</button>' +
        '<button class="' + (tab === "bulk" ? "active" : "") + '" data-act="msg-tab" data-tab="bulk">گروهی</button>' +
      "</div>" + targetBox +
      '<div class="setting-block"><div class="setting-title">' + A.icon("message") + " متن پیام</div>" +
        '<div class="field"><textarea class="textarea" id="msgText" maxlength="4000" placeholder="متن پیام را بنویسید…">' + A.esc(state.message.text) + "</textarea>" +
        '<div class="hint" id="msgCharCount">' + A.toFa(String(state.message.text || "").length) + " از ۴۰۰۰ کاراکتر</div></div>" +
        '<div class="form-actions" style="justify-content:flex-start">' +
          '<button class="btn btn-primary" id="msgSend">' + A.icon("send") + " ارسال پیام</button>" +
        "</div></div>" +
      '<div id="msgJob" hidden>' + jobHTML(state.message.job) + "</div></div>";
  }

  function selectedUserHTML() {
    const u = state.message.user;
    if (!u) return "";
    return '<div class="fav-chip" style="padding:7px 10px">' + A.icon("user") + "<span>" + A.esc(u.name) + "</span>" +
      '<span class="muted tiny">#' + A.toFa(u.uid) + "</span>" +
      '<button data-act="msg-user-clear" title="حذف انتخاب">' + A.icon("close") + "</button></div>";
  }

  function jobHTML(job) {
    if (!job) return "";
    const statuses = { pending: "در انتظار", running: "در حال ارسال", done: "پایان یافته", cancelled: "لغو شده", failed: "خطا" };
    const pct = Math.max(0, Math.min(100, Number(job.progress || 0)));
    return '<div class="card-body" style="border-top:1px solid var(--border)">' +
      '<div class="row-between mb-1"><div class="strong">' + A.icon("activity") + " ارسال گروهی</div>" +
        '<span class="pill ' + (job.status === "done" ? "green" : (job.status === "failed" ? "red" : (job.status === "cancelled" ? "gray" : "blue"))) + '">' +
        A.esc(statuses[job.status] || job.status) + "</span></div>" +
      '<div class="progress' + (job.status === "done" ? " green" : "") + '"><div class="progress-bar" style="width:' + pct + '%"></div></div>' +
      '<div class="progress-meta"><span>' + A.toFa(job.sent || 0) + " ارسال موفق • " + A.toFa(job.failed || 0) + " خطا</span><span>" + A.toFa(pct) + "٪</span></div>" +
      '<div class="row-between mt-2"><span class="tiny muted">مجموع گیرندگان: ' + A.toFa(job.total || 0) + "</span>" +
        (job.status === "running" || job.status === "pending"
          ? '<button class="btn btn-sm btn-danger" data-act="msg-cancel" data-job="' + A.esc(job.id) + '">لغو ارسال</button>' : "") +
      "</div></div>";
  }

  function bindComposer(token) {
    if (A.$("#msgUserSearch")) {
      const input = A.$("#msgUserSearch");
      input.addEventListener("input", A.debounce(function () {
        loadMessageUsers(input.value.trim());
      }, 280));
      if (!state.message.user) loadMessageUsers("");
    }
    if (A.$("#msgUserResults")) bindMessageUserResults();
    if (A.$("#msgSend")) {
      const textarea = A.$("#msgText");
      const counter = A.$("#msgCharCount");
      if (textarea && counter) {
        textarea.addEventListener("input", function () {
          state.message.text = textarea.value;
          counter.textContent = A.toFa(textarea.value.length) + " از ۴۰۰۰ کاراکتر";
        });
      }
      A.$("#msgSend").addEventListener("click", function () {
        sendMessage(this, token);
      });
    }
    if (state.message.job && (state.message.job.status === "running" || state.message.job.status === "pending")) {
      startJobPolling(state.message.job.id);
    }
  }

  function bindMessageUserResults() {
    const box = A.$("#msgUserResults");
    if (!box) return;
    box.addEventListener("click", function (e) {
      const btn = e.target.closest("[data-pick-user]");
      if (!btn) return;
      state.message.user = { uid: Number(btn.getAttribute("data-pick-user")), name: btn.getAttribute("data-pick-name") || "" };
      const selected = A.$("#msgSelected");
      if (selected) { selected.hidden = false; selected.innerHTML = selectedUserHTML(); }
      Array.from(box.querySelectorAll("[data-pick-user]")).forEach(function (el) {
        el.classList.toggle("active", el === btn);
      });
    });
  }

  async function loadMessageUsers(query) {
    const box = A.$("#msgUserResults");
    if (!box) return;
    box.innerHTML = A.loadingRow("در حال جستجوی کاربران…");
    try {
      const data = await A.api("/api/admin/users?q=" + encodeURIComponent(query) + "&page=1&page_size=8");
      if (!A.$("#msgUserResults")) return;
      const items = data.items || [];
      if (!items.length) {
        box.innerHTML = '<div class="tiny muted">کاربری پیدا نشد.</div>';
        return;
      }
      box.innerHTML = items.map(function (u) {
        return '<div class="list-item" data-pick-user="' + u.user_id + '" data-pick-name="' + A.esc(u.name) + '" style="cursor:pointer">' +
          '<div class="avatar sm">' + A.esc(A.initials(u.name)) + "</div>" +
          '<div class="list-body"><div class="list-title">' + A.esc(u.name) + '</div><div class="list-sub">' +
          A.esc(u.phone || (u.username ? "@" + u.username : "شناسه " + A.toFa(u.user_id))) + "</div></div>" +
          '<div class="list-side">' + (u.webapp_sub && u.webapp_sub.active
            ? '<span class="pill green">' + A.toFa(u.webapp_sub.days_left) + " روز</span>"
            : '<span class="pill gray">بدون اشتراک</span>') + "</div></div>";
      }).join("");
      bindMessageUserResults();
      if (state.message.user) {
        Array.from(box.querySelectorAll("[data-pick-user]")).forEach(function (el) {
          el.classList.toggle("active", Number(el.getAttribute("data-pick-user")) === state.message.user.uid);
        });
      }
    } catch (e) {
      if (A.$("#msgUserResults")) box.innerHTML = '<div class="tiny muted">' + A.esc(e.message) + "</div>";
    }
  }

  async function sendMessage(btn, token) {
    const textarea = A.$("#msgText");
    const text = textarea ? textarea.value.trim() : "";
    if (!text) { A.toast("متن پیام را وارد کنید.", "error"); return; }
    const tab = state.message.tab;
    let body = { mode: tab, text: text };
    if (tab === "single") {
      if (!state.message.user) { A.toast("یک کاربر را انتخاب کنید.", "error"); return; }
      body.user_id = state.message.user.uid;
    } else if (tab === "targeted") {
      const sub = A.$("#msgSubFilter");
      const joined = A.$("#msgJoinedDays");
      body.sub_filter = sub ? sub.value : "all";
      body.joined_days = joined ? Math.max(0, Math.min(365, parseInt(A.toEn(joined.value).replace(/\D/g, ""), 10) || 0)) : 0;
    } else {
      const ok = await A.confirm({
        title: "ارسال گروهی",
        message: "این پیام برای همه کاربران دارای رمز ورود ارسال می‌شود. ادامه می‌دهید؟",
        confirmText: "ارسال گروهی"
      });
      if (!ok) return;
    }
    A.setBusy(btn, true, "در حال ارسال…");
    try {
      const res = await A.api("/api/admin/messages", { method: "POST", body: body });
      A.setBusy(btn, false);
      if (res.job_id) {
        state.message.job = {
          id: res.job_id, status: "pending", total: res.total || 0,
          sent: 0, failed: 0, progress: 0
        };
        const jobBox = A.$("#msgJob");
        if (jobBox) { jobBox.hidden = false; jobBox.innerHTML = jobHTML(state.message.job); }
        if (textarea) { textarea.value = ""; state.message.text = ""; }
        startJobPolling(res.job_id);
        A.toast("ارسال گروهی آغاز شد.", "info");
      } else {
        A.toast(res.message || "پیام ارسال شد.", "success");
        if (textarea) { textarea.value = ""; state.message.text = ""; }
        const counter = A.$("#msgCharCount");
        if (counter) counter.textContent = "۰ از ۴۰۰۰ کاراکتر";
        loadMessageHistory(token);
      }
    } catch (e) {
      A.setBusy(btn, false);
      A.toast(e.message, "error");
    }
  }

  function stopJobPolling() {
    if (state.message.timer) {
      clearInterval(state.message.timer);
      state.message.timer = null;
    }
  }

  function startJobPolling(jobId) {
    stopJobPolling();
    const tick = async function () {
      try {
        const job = await A.api("/api/admin/messages/jobs/" + encodeURIComponent(jobId), { redirectOn401: false });
        state.message.job = job;
        const box = A.$("#msgJob");
        if (!box) { stopJobPolling(); return; }
        box.hidden = false;
        box.innerHTML = jobHTML(job);
        if (job.status === "done" || job.status === "cancelled" || job.status === "failed") {
          stopJobPolling();
          loadMessageHistory();
          if (job.status === "done") A.toast("ارسال گروهی با موفقیت پایان یافت.", "success");
          else if (job.status === "cancelled") A.toast("ارسال گروهی لغو شد.", "warn");
          else A.toast("ارسال گروهی با خطا متوقف شد.", "error");
        }
      } catch (e) {
        stopJobPolling();
      }
    };
    tick();
    state.message.timer = setInterval(tick, 1300);
  }

  async function cancelJob(jobId) {
    const ok = await A.confirm({
      title: "لغو ارسال گروهی",
      message: "ارسال پیام‌های باقی‌مانده متوقف می‌شود.",
      danger: true,
      confirmText: "لغو ارسال"
    });
    if (!ok) return;
    try {
      await A.api("/api/admin/messages/jobs/" + encodeURIComponent(jobId) + "/cancel", { method: "POST" });
      A.toast("درخواست لغو ثبت شد.", "info");
      startJobPolling(jobId);
    } catch (e) {
      A.toast(e.message, "error");
    }
  }

  async function loadMessageHistory(token) {
    const box = A.$("#msgHistoryBox");
    if (!box) return;
    box.innerHTML = A.loadingRow("در حال دریافت تاریخچه…");
    let data;
    try {
      data = await A.api("/api/admin/messages");
    } catch (e) {
      if (box) box.innerHTML = '<div class="tiny muted">' + A.esc(e.message) + "</div>";
      return;
    }
    if (!A.$("#msgHistoryBox")) return;
    const items = data.items || [];
    state.message.items = items;
    if (!items.length) {
      box.innerHTML = A.emptyState("message", "تاریخچه خالی است", "پس از ارسال اولین پیام، اینجا نمایش داده می‌شود.");
      return;
    }
    box.innerHTML = '<div class="list">' + items.map(function (m) {
      return '<div class="list-item"><div class="list-icon ' + (Number(m.failed || 0) ? "amber" : "green") + '">' + A.icon("send") + "</div>" +
        '<div class="list-body"><div class="list-title">' + A.esc(clip(m.text, 90)) + "</div>" +
        '<div class="list-sub">' + A.esc(targetLabel(m.target_type)) + " • " + A.esc(m.created_at_fa || A.timeAgo(m.created_at)) + "</div></div>" +
        '<div class="list-side"><span class="pill green">' + A.toFa(m.sent || 0) + " موفق</span>" +
        (Number(m.failed || 0) ? '<span class="pill red">' + A.toFa(m.failed) + " خطا</span>" : "") + "</div></div>";
    }).join("") + "</div>";
  }

  /* ------------------------------------------------------------------
     تنظیمات
  ------------------------------------------------------------------ */
  async function renderSettings(token) {
    const root = viewEl();
    root.innerHTML = loadingCard("در حال دریافت تنظیمات…");
    let data, chart;
    try {
      [data, chart] = await Promise.all([
        A.api("/api/admin/settings"),
        A.api("/api/admin/chart-settings")
      ]);
    } catch (e) {
      if (!alive(token)) return;
      root.innerHTML = errorCard(e.message, '<button class="btn btn-soft" data-act="refresh">' + A.icon("refresh") + " تلاش دوباره</button>");
      return;
    }
    if (!alive(token)) return;
    state.settings = data;
    const s = data.settings || {};
    const site = data.site || "";
    const tfOptions = Object.keys(TF_LABEL).map(function (k) {
      return '<option value="' + k + '"' + (s.web_default_timeframe === k ? " selected" : "") + ">" + TF_LABEL[k] + "</option>";
    }).join("");
    root.innerHTML =
      '<div class="page-head"><div><div class="eyebrow">' + A.icon("settings") + " پیکربندی</div>" +
      '<h1 class="page-title">تنظیمات سامانه</h1><p class="page-sub">پیام‌های اطلاع‌رسانی و تنظیمات پیش‌فرض تحلیل.</p></div></div>' +
      '<div class="settings-grid">' +
        '<form class="card" id="adminSettingsForm"><div class="card-head bordered"><div><div class="card-title">' + A.icon("bell") + " پیام‌ها و تحلیل</div>" +
          '<div class="card-sub">قالب اطلاع‌رسانی رویدادهای وب‌اپ و تایم‌فریم پیش‌فرض اسکن.</div></div></div>' +
          '<div class="card-body">' +
          '<div class="form-grid">' +
            '<div class="field full"><label class="label">تایم‌فریم پیش‌فرض</label><select class="select" name="web_default_timeframe">' + tfOptions + "</select></div>" +
          "</div>" +
          '<div class="setting-block"><div class="setting-title">' + A.icon("bell") + " قالب پیام اطلاع‌رسانی</div>" +
            '<div class="setting-desc">برای اطلاع‌رسانی رویدادهای وب‌اپ به مدیر ربات استفاده می‌شود.</div>' +
            '<textarea class="textarea textarea-ltr" name="web_notify_template" rows="6" dir="rtl">' + A.esc(s.web_notify_template || "") + "</textarea>" +
            '<div class="row" style="justify-content:flex-end"><button type="button" class="btn btn-sm btn-ghost" data-act="settings-reset" data-field="web_notify_template">بازگردانی پیش‌فرض</button></div></div>' +
          '<div class="form-actions"><button type="submit" class="btn btn-primary">' + A.icon("check") + " ذخیره تنظیمات</button></div>" +
        "</div></form>" +

        '<div class="settings-side">' +
          '<form class="card" id="chartSettingsForm"><div class="card-head bordered"><div><div class="card-title">' + A.icon("settings") + " نمایش نمودار</div>" +
            '<div class="card-sub">تنظیمات سراسری نمودار برای همه کاربران؛ ذخیره مستقل از تنظیمات سامانه.</div></div></div>' +
            '<div class="card-body"><div class="form-grid">' +
              '<div class="field full"><label class="label" for="chartTheme">پوسته نمودار</label>' +
                '<select class="select" id="chartTheme" name="theme" aria-describedby="chartThemeHint">' +
                  '<option value="dark"' + (chart.theme === "dark" ? " selected" : "") + '>تیره</option>' +
                  '<option value="light"' + (chart.theme === "light" ? " selected" : "") + '>روشن</option></select>' +
                '<div class="hint" id="chartThemeHint">پوسته تیره یا روشن نمودار مستقل از پوسته سایت است.</div></div>' +
              '<div class="field full"><label class="switch"><input type="checkbox" name="showRealtimePrice" aria-describedby="chartRealtimeHint"' + (chart.showRealtimePrice ? " checked" : "") + '>' +
                '<span class="switch-track"></span><span class="switch-text">نمایش قیمت لحظه‌ای در سربرگ نمودار</span></label>' +
                '<div class="hint" id="chartRealtimeHint">این گزینه فقط نمایش قیمت لحظه‌ای سربرگ را تغییر می‌دهد؛ داده‌های باز، بالا، پایین و بسته (OHLC) کندل‌ها تغییری نمی‌کنند.</div></div>' +
              '<div class="field full"><label class="switch"><input type="checkbox" name="showVolumePanel" aria-describedby="chartVolumeHint"' + (chart.showVolumePanel ? " checked" : "") + '>' +
                '<span class="switch-track"></span><span class="switch-text">نمایش پنل حجم معاملات</span></label>' +
                '<div class="hint" id="chartVolumeHint">پنل حجم معاملات زیر نمودار نمایش داده شود.</div></div>' +
            '</div><div class="form-actions"><button type="submit" class="btn btn-primary">' + A.icon("check") + " ذخیره تنظیمات نمودار</button></div></div></form>" +
          '<div class="card"><div class="card-head"><div><div class="card-title">' + A.icon("link") + " آدرس وب‌اپ</div></div></div>" +
            '<div class="card-body"><div class="copy-row">' +
              '<input class="input" id="siteAddress" readonly value="' + A.esc(site || "تنظیم نشده") + '">' +
              (site ? '<button class="icon-btn sm" data-act="copy-site" title="کپی">' + A.icon("copy") + "</button>" : "") +
            "</div><div class=\"hint mt-1\">" + (site ? "این آدرس در پیام‌های ربات و لینک ورود استفاده می‌شود." : "متغیر WEBAPP_PUBLIC_HOST را در محیط اجرا تنظیم کنید.") + "</div></div></div>" +
          '<div class="card"><div class="card-head"><div><div class="card-title">' + A.icon("grid") + " بخش‌های مدیریتی</div></div></div>" +
            '<div class="card-body tight"><div class="list">' +
              '<a class="list-item" href="/admin/branding" data-admin-link><div class="list-icon violet">' + A.icon("image") + '</div><div class="list-body"><div class="list-title">برندینگ و ظاهر سایت</div><div class="list-sub">نام، شعار، لوگو و متون ورود</div></div>' + A.icon("chevron-left") + "</a>" +
              '<a class="list-item" href="/admin/plans" data-admin-link><div class="list-icon green">' + A.icon("star") + '</div><div class="list-body"><div class="list-title">پلن‌ها و شبکه‌های پرداخت</div><div class="list-sub">قیمت، مدت، تخفیف و اشتراک رایگان</div></div>' + A.icon("chevron-left") + "</a>" +
              '<a class="list-item" href="/admin/api-keys" data-admin-link><div class="list-icon amber">' + A.icon("lock") + '</div><div class="list-body"><div class="list-title">کلیدهای API</div><div class="list-sub">NodeReal، Tronscan، TonAPI و Arbiscan</div></div>' + A.icon("chevron-left") + "</a>" +
              '<a class="list-item" href="/admin/security" data-admin-link><div class="list-icon blue">' + A.icon("shield") + '</div><div class="list-body"><div class="list-title">امنیت و محدودیت‌ها</div><div class="list-sub">محدودیت ربات و وب‌اپ به‌صورت جداگانه</div></div>' + A.icon("chevron-left") + "</a>" +
              '<a class="list-item" href="/admin/backups" data-admin-link><div class="list-icon cyan">' + A.icon("download") + '</div><div class="list-body"><div class="list-title">بکاپ و بازیابی</div><div class="list-sub">بکاپ کامل یا بخشی با تاریخچه</div></div>' + A.icon("chevron-left") + "</a>" +
            "</div></div></div>" +
          '<div class="card"><div class="card-head"><div><div class="card-title">' + A.icon("shield") + " نکات امنیتی</div></div></div>" +
            '<div class="card-body"><div class="stack tiny muted">' +
            "<div>• ورود مدیر فقط از لینک اختصاصی ربات انجام می‌شود.</div>" +
            "<div>• رمز کاربران به‌صورت هش‌شده ذخیره می‌شود و قابل بازیابی نیست.</div>" +
            "<div>• کلیدهای API فقط از پنل مدیریت قابل ویرایش هستند.</div>" +
            "</div></div></div>" +
        "</div>" +
      "</div>";

    const form = A.$("#adminSettingsForm");
    if (form) {
      form.addEventListener("submit", async function (e) {
        e.preventDefault();
        const btn = e.target.querySelector('button[type="submit"]');
        A.setBusy(btn, true, "در حال ذخیره…");
        const payload = {
          web_default_timeframe: form.web_default_timeframe.value,
          web_notify_template: form.web_notify_template.value.trim()
        };
        try {
          const res = await A.api("/api/admin/settings", { method: "PUT", body: payload });
          state.settings = res;
          A.toast("تنظیمات سامانه ذخیره شد.", "success");
        } catch (err) {
          A.toast(err.message, "error");
        } finally {
          A.setBusy(btn, false);
        }
      });
    }

    const chartForm = A.$("#chartSettingsForm");
    if (chartForm) {
      chartForm.addEventListener("submit", async function (e) {
        e.preventDefault();
        const btn = chartForm.querySelector('button[type="submit"]');
        if (btn.disabled) return;
        const payload = {
          theme: chartForm.elements.namedItem("theme").value,
          showRealtimePrice: chartForm.elements.namedItem("showRealtimePrice").checked,
          showVolumePanel: chartForm.elements.namedItem("showVolumePanel").checked
        };
        A.setBusy(btn, true, "در حال ذخیره…");
        try {
          await A.api("/api/admin/chart-settings", { method: "PUT", body: payload });
          A.toast("تنظیمات نمودار ذخیره شد.", "success");
        } catch (err) {
          A.toast(err.message, "error");
        } finally {
          A.setBusy(btn, false);
        }
      });
    }
  }

  /* ------------------------------------------------------------------
     برندینگ
  ------------------------------------------------------------------ */
  async function renderBranding(token) {
    const root = viewEl();
    root.innerHTML = loadingCard("در حال دریافت تنظیمات ظاهر…");
    let data;
    try {
      data = await A.api("/api/admin/branding");
    } catch (e) {
      if (!alive(token)) return;
      root.innerHTML = errorCard(e.message, '<button class="btn btn-soft" data-act="refresh">' + A.icon("refresh") + " تلاش دوباره</button>");
      return;
    }
    if (!alive(token)) return;
    state.branding = data;
    const b = data.branding || data;
    const login = b.login || {};
    const logo = b.logo || "";
    root.innerHTML =
      '<div class="page-head"><div><div class="eyebrow">' + A.icon("image") + " ظاهر سایت</div>" +
      '<h1 class="page-title">برندینگ و متون</h1><p class="page-sub">نام سایت، شعار، لوگو و متن‌های صفحه ورود را مدیریت کنید.</p></div>' +
      '<div class="page-actions"><a class="btn btn-soft" href="/" target="_blank" rel="noopener">' + A.icon("external") + " پیش‌نمایش سایت</a></div></div>" +
      '<div class="grid grid-main">' +
        '<form class="card" id="brandingForm"><div class="card-head bordered"><div><div class="card-title">' + A.icon("brand") + " هویت برند</div>" +
          '<div class="card-sub">این مقادیر در نوار کناری، عنوان صفحات و پیام‌های ربات استفاده می‌شوند.</div></div></div>' +
          '<div class="card-body"><div class="form-grid">' +
            '<div class="field"><label class="label">نام سایت <span class="req">*</span></label><input class="input" name="site_name" maxlength="60" value="' + A.esc(b.site_name || "") + '"></div>' +
            '<div class="field"><label class="label">شعار سایت</label><input class="input" name="site_slogan" maxlength="120" value="' + A.esc(b.site_slogan || "") + '"></div>' +
            '<div class="field full"><label class="label">عنوان مرورگر</label><input class="input" name="site_title" maxlength="120" value="' + A.esc(b.site_title || "") + '"></div>' +
          "</div></div>" +
          '<div class="card-body" style="border-top:1px solid var(--border)"><div class="setting-title">' + A.icon("image") + " لوگوی سایت</div>" +
            '<div class="setting-desc">حداکثر ۳ مگابایت؛ PNG، JPG، WebP یا GIF. با حذف لوگو، آیکون پیش‌فرض نمایش داده می‌شود.</div>' +
            '<div class="logo-preview">' +
              '<span class="logo-box" id="brandLogoBox">' + (logo ? '<img src="' + A.esc(logo) + '" alt="لوگو">' : A.icon("brand")) + "</span>" +
              '<div class="logo-actions"><input class="file-input" id="brandLogoInput" type="file" accept="image/png,image/jpeg,image/webp,image/gif">' +
                '<button type="button" class="btn btn-soft" data-act="brand-logo-pick">' + A.icon("image") + " انتخاب تصویر</button>" +
                (logo ? '<button type="button" class="btn btn-danger" data-act="brand-logo-delete">' + A.icon("trash") + " حذف لوگو</button>" : "") +
              "</div>" +
            "</div>" +
          "</div>" +
          '<div class="card-foot"><span class="tiny muted">آخرین تغییر: پس از ذخیره بلافاصله اعمال می‌شود.</span>' +
            '<button type="submit" class="btn btn-primary btn-sm">' + A.icon("check") + " ذخیره برندینگ</button></div>" +
        "</form>" +

        '<form class="card" id="loginTextForm"><div class="card-head bordered"><div><div class="card-title">' + A.icon("lock") + " متون صفحه ورود</div>" +
          '<div class="card-sub">متن‌های صفحه ورود کاربران و مدیر را سفارشی کنید.</div></div></div>' +
          '<div class="card-body"><div class="form-grid">' +
            '<div class="field full"><label class="label">عنوان اصلی</label><input class="input" name="login_title" maxlength="200" value="' + A.esc(login.title || "") + '"></div>' +
            '<div class="field full"><label class="label">زیرعنوان</label><textarea class="textarea" name="login_subtitle" maxlength="400" rows="2">' + A.esc(login.subtitle || "") + "</textarea></div>" +
            '<div class="field"><label class="label">عنوان فرم ورود</label><input class="input" name="login_heading" maxlength="80" value="' + A.esc(login.heading || "") + '"></div>' +
            '<div class="field"><label class="label">متن دکمه ورود</label><input class="input" name="login_button" maxlength="40" value="' + A.esc(login.button || "") + '"></div>' +
            '<div class="field full"><label class="label">توضیح زیر فرم</label><textarea class="textarea" name="login_note" maxlength="300" rows="2">' + A.esc(login.note || "") + "</textarea></div>" +
            '<div class="field"><label class="label">متن خطای ورود</label><input class="input" name="login_error" maxlength="200" value="' + A.esc(login.error || "") + '" placeholder="در صورت خالی بودن، پیام پیش‌فرض نمایش داده می‌شود"></div>' +
            '<div class="field"><label class="label">متن پایین صفحه</label><input class="input" name="login_footer" maxlength="300" value="' + A.esc(login.footer || "") + '"></div>' +
          "</div></div>" +
          '<div class="card-foot"><span class="tiny muted">برای پیش‌نمایش، صفحه ورود را در پنجره جدید باز کنید.</span>' +
            '<button type="submit" class="btn btn-primary btn-sm">' + A.icon("check") + " ذخیره متون ورود</button></div>" +
        "</form>" +
      "</div>";

    const brandForm = A.$("#brandingForm");
    if (brandForm) brandForm.addEventListener("submit", async function (e) {
      e.preventDefault();
      const btn = e.target.querySelector('button[type="submit"]');
      A.setBusy(btn, true, "در حال ذخیره…");
      try {
        await A.api("/api/admin/branding", {
          method: "PUT",
          body: {
            site_name: brandForm.site_name.value.trim(),
            site_slogan: brandForm.site_slogan.value.trim(),
            site_title: brandForm.site_title.value.trim()
          }
        });
        await A.applyPublicConfig();
        A.toast("برندینگ سایت ذخیره شد.", "success");
      } catch (err) {
        A.toast(err.message, "error");
      } finally {
        A.setBusy(btn, false);
      }
    });

    const loginForm = A.$("#loginTextForm");
    if (loginForm) loginForm.addEventListener("submit", async function (e) {
      e.preventDefault();
      const btn = e.target.querySelector('button[type="submit"]');
      A.setBusy(btn, true, "در حال ذخیره…");
      try {
        await A.api("/api/admin/branding", {
          method: "PUT",
          body: {
            login_title: loginForm.login_title.value.trim(),
            login_subtitle: loginForm.login_subtitle.value.trim(),
            login_heading: loginForm.login_heading.value.trim(),
            login_note: loginForm.login_note.value.trim(),
            login_button: loginForm.login_button.value.trim(),
            login_error: loginForm.login_error.value.trim(),
            login_footer: loginForm.login_footer.value.trim()
          }
        });
        A.toast("متون صفحه ورود ذخیره شد.", "success");
      } catch (err) {
        A.toast(err.message, "error");
      } finally {
        A.setBusy(btn, false);
      }
    });

    const file = A.$("#brandLogoInput");
    if (file) file.addEventListener("change", async function () {
      const f = file.files && file.files[0];
      if (!f) return;
      const fd = new FormData();
      fd.append("image", f);
      try {
        A.toast("در حال بارگذاری لوگو…", "info", 2000);
        await A.api("/api/admin/branding/logo", { method: "POST", body: fd });
        A.toast("لوگو بارگذاری شد.", "success");
        refreshCurrent();
        A.applyPublicConfig();
      } catch (err) {
        A.toast(err.message, "error");
      } finally {
        file.value = "";
      }
    });
  }

  /* ------------------------------------------------------------------
     حالت تعمیر
  ------------------------------------------------------------------ */
  async function renderMaintenance(token) {
    const root = viewEl();
    root.innerHTML = loadingCard("در حال دریافت وضعیت…");
    let data;
    try {
      data = await A.api("/api/admin/maintenance");
    } catch (e) {
      if (!alive(token)) return;
      root.innerHTML = errorCard(e.message, '<button class="btn btn-soft" data-act="refresh">' + A.icon("refresh") + " تلاش دوباره</button>");
      return;
    }
    if (!alive(token)) return;
    state.maintenance = data;
    root.innerHTML =
      '<div class="page-head"><div><div class="eyebrow">' + A.icon("alert") + " وضعیت سایت</div>" +
      '<h1 class="page-title">حالت تعمیر و نگهداری</h1><p class="page-sub">در حالت تعمیر، سایت در دسترس می‌ماند و یک نوار هشدار بالای همه صفحات نمایش داده می‌شود.</p></div></div>' +
      '<div class="settings-grid">' +
        '<form class="card" id="maintForm"><div class="card-head bordered"><div><div class="card-title">' + A.icon("alert") + " تنظیم نوار هشدار</div>" +
          '<div class="card-sub">متن هشدار برای کاربران و مدیران نمایش داده می‌شود.</div></div></div>' +
          '<div class="card-body"><div class="stack">' +
            '<label class="switch"><input type="checkbox" id="maintOn"' + (data.on ? " checked" : "") + '><span class="switch-track"></span>' +
              '<span class="switch-text">فعال بودن حالت تعمیر</span></label>' +
            '<div class="field"><label class="label">متن هشدار</label><textarea class="textarea" id="maintText" rows="4" maxlength="500">' + A.esc(data.text || "") + "</textarea></div>" +
            '<div class="notice warn">' + A.icon("info") + "<div>با فعال بودن این حالت، خرید، ورود و سایر بخش‌ها مسدود نمی‌شوند؛ فقط هشدار نمایش داده می‌شود.</div></div>" +
          "</div></div>" +
          '<div class="card-foot"><span class="tiny muted">' + (data.at_fa ? "فعال‌شده از: " + A.esc(data.at_fa) : "هنوز فعال نشده است.") + "</span>" +
            '<button type="submit" class="btn btn-primary btn-sm">' + A.icon("check") + " ذخیره وضعیت</button></div>" +
        "</form>" +
        '<div class="settings-side">' +
          '<div class="card"><div class="card-head"><div><div class="card-title">' + A.icon("eye") + " پیش‌نمایش</div></div></div>" +
            '<div class="card-body"><div class="maintenance-bar" style="position:static;border-radius:12px;border:1px solid var(--border)">' +
              A.icon("alert") + "<span>" + A.esc(data.text || "پلتفرم در حال بروزرسانی است؛ لطفاً در ساعات آینده وارد شوید.") + "</span></div>" +
              '<div class="hint mt-2">این نوار در بالای تمام صفحات کاربری و مدیریتی نمایش داده می‌شود.</div></div></div>' +
          '<div class="card"><div class="card-head"><div><div class="card-title">' + A.icon("info") + " راهنما</div></div></div>" +
            '<div class="card-body"><div class="stack tiny muted">' +
              "<div>• برای اطلاع‌رسانی به کاربران، متن واضح و کوتاه بنویسید.</div>" +
              "<div>• پس از پایان تعمیرات، حالت تعمیر را خاموش کنید.</div>" +
              "<div>• تغییرات بلافاصله روی همه صفحات اعمال می‌شود.</div>" +
            "</div></div></div>" +
        "</div>" +
      "</div>";

    const form = A.$("#maintForm");
    if (form) form.addEventListener("submit", async function (e) {
      e.preventDefault();
      const btn = e.target.querySelector('button[type="submit"]');
      A.setBusy(btn, true, "در حال ذخیره…");
      try {
        const res = await A.api("/api/admin/maintenance", {
          method: "PUT",
          body: { on: !!A.$("#maintOn").checked, text: A.$("#maintText").value.trim() }
        });
        state.maintenance = res;
        await A.applyPublicConfig();
        A.toast(res.message || "وضعیت ذخیره شد.", "success");
        refreshCurrent();
      } catch (err) {
        A.toast(err.message, "error");
        A.setBusy(btn, false);
      }
    });
  }

  /* ------------------------------------------------------------------
     کلیدهای API
  ------------------------------------------------------------------ */
  async function renderApiKeys(token) {
    const root = viewEl();
    root.innerHTML = loadingCard("در حال دریافت کلیدها…");
    let data;
    try {
      data = await A.api("/api/admin/api-keys");
    } catch (e) {
      if (!alive(token)) return;
      root.innerHTML = errorCard(e.message, '<button class="btn btn-soft" data-act="refresh">' + A.icon("refresh") + " تلاش دوباره</button>");
      return;
    }
    if (!alive(token)) return;
    state.apikeys = data;
    const rows = (data.keys || []).map(function (k) {
      return '<div class="key-row">' +
        '<div class="grow"><div class="cell-main">' + A.esc(k.label) + '</div>' +
          '<div class="key-status">' + (k.present ? "ثبت‌شده: " + A.esc(k.masked) : "ثبت نشده") + "</div></div>" +
        '<input class="input" data-key="' + A.esc(k.key) + '" dir="ltr" placeholder="کلید جدید را وارد کنید" autocomplete="off">' +
        (k.present ? '<button class="btn btn-sm btn-danger" data-act="api-key-clear" data-key="' + A.esc(k.key) + '">' + A.icon("trash") + " پاک کردن</button>" : "") +
      "</div>";
    }).join("");
    root.innerHTML =
      '<div class="page-head"><div><div class="eyebrow">' + A.icon("lock") + " تأیید پرداخت</div>" +
      '<h1 class="page-title">کلیدهای API</h1><p class="page-sub">کلیدهای NodeReal، Tronscan، TonAPI و Arbiscan برای بررسی خودکار تراکنش‌ها.</p></div></div>' +
      '<div class="settings-grid">' +
        '<div class="card"><div class="card-head bordered"><div><div class="card-title">' + A.icon("hash") + " کلیدهای سرویس‌ها</div>" +
          '<div class="card-sub">کلیدها به‌صورت ماسک‌شده نمایش داده می‌شوند و در پاسخ‌های عمومی قرار نمی‌گیرند.</div></div></div>' +
          '<div class="card-body" id="apiKeysBox">' + (rows || A.emptyState("lock", "کلیدی ثبت نشده", "برای فعال‌سازی تأیید خودکار، کلیدهای سرویس را وارد کنید.")) + "</div>" +
          '<div class="card-foot"><span class="tiny muted">NodeReal برای BSC، Tronscan برای TRON، TonAPI برای TON و Arbiscan برای Arbitrum استفاده می‌شود.</span>' +
            '<button class="btn btn-primary btn-sm" data-act="api-keys-save">' + A.icon("check") + " ذخیره کلیدها</button></div>" +
        "</div>" +
        '<div class="settings-side">' +
          '<div class="card"><div class="card-head"><div><div class="card-title">' + A.icon("shield") + " امنیت کلیدها</div></div></div>" +
            '<div class="card-body"><div class="stack tiny muted">' +
              "<div>• کلیدها فقط در پایگاه‌داده سرور ذخیره می‌شوند.</div>" +
              "<div>• این مقادیر هرگز در خروجی API عمومی نمایش داده نمی‌شوند.</div>" +
              "<div>• برای پاک‌کردن یک کلید، از دکمه پاک کردن همان ردیف استفاده کنید.</div>" +
              "<div>• پس از تغییر کلیدها، صحت تأیید خودکار را بررسی کنید.</div>" +
            "</div></div></div>" +
          '<div class="card"><div class="card-head"><div><div class="card-title">' + A.icon("info") + " وضعیت</div></div></div>" +
            '<div class="card-body"><div class="stat-mini">' +
              '<div class="stat-mini-item"><span>کلیدهای ثبت‌شده</span><b>' + A.toFa((data.keys || []).filter(function (k) { return k.present; }).length) + "</b></div>" +
              '<div class="stat-mini-item"><span>کل سرویس‌ها</span><b>' + A.toFa((data.keys || []).length) + "</b></div>" +
            "</div></div></div>" +
        "</div>" +
      "</div>";
  }

  async function saveApiKeys() {
    const inputs = A.$$("#apiKeysBox input[data-key]");
    const body = {};
    inputs.forEach(function (el) {
      const value = (el.value || "").trim();
      if (value && value.indexOf("•") < 0) body[el.getAttribute("data-key")] = value;
    });
    if (!Object.keys(body).length) {
      A.toast("کلید جدیدی وارد نشده است.", "info");
      return;
    }
    try {
      const res = await A.api("/api/admin/api-keys", { method: "PUT", body: body });
      state.apikeys = res;
      A.toast(res.message || "کلیدها ذخیره شد.", "success");
      refreshCurrent();
    } catch (e) {
      A.toast(e.message, "error");
    }
  }

  async function clearApiKey(key) {
    const ok = await A.confirm({
      title: "پاک کردن کلید API",
      message: "این کلید از سرور حذف می‌شود و تأیید خودکار مربوط به آن سرویس غیرفعال خواهد شد.",
      danger: true,
      confirmText: "پاک کردن"
    });
    if (!ok) return;
    const body = {};
    body[key] = "";
    try {
      const res = await A.api("/api/admin/api-keys", { method: "PUT", body: body });
      state.apikeys = res;
      A.toast("کلید پاک شد.", "success");
      refreshCurrent();
    } catch (e) {
      A.toast(e.message, "error");
    }
  }

  /* ------------------------------------------------------------------
     پشتیبانی
  ------------------------------------------------------------------ */
  async function renderSupport(token) {
    const root = viewEl();
    root.innerHTML =
      '<div class="page-head"><div><div class="eyebrow">' + A.icon("message") + " ارتباط با کاربران</div>" +
      '<h1 class="page-title">پشتیبانی</h1><p class="page-sub">مکالمات کاربران، تاریخچه پیام‌ها، فایل‌ها و پاسخ‌دهی از پنل مدیریت.</p></div>' +
      '<div class="page-actions"><button class="btn btn-soft" data-act="support-refresh">' + A.icon("refresh") + " به‌روزرسانی</button></div></div>" +
      '<div class="support-layout">' +
        '<div class="card"><div class="results-toolbar">' +
          '<select class="select" id="supportStatus" style="width:auto;min-width:140px">' +
            '<option value="open"' + (state.support.status === "open" ? " selected" : "") + ">باز</option>" +
            '<option value="closed"' + (state.support.status === "closed" ? " selected" : "") + ">بسته</option>" +
            '<option value="all"' + (state.support.status === "all" ? " selected" : "") + ">همه</option>" +
          "</select>" +
          '<div class="searchbox" style="flex:1;min-width:140px">' + A.icon("search") +
            '<input id="supportQ" placeholder="جستجو…" value="' + A.esc(state.support.q) + '" autocomplete="off"></div>' +
        '</div><div class="card-body tight"><div id="supportList" class="support-list">' + A.loadingRow("در حال دریافت مکالمات…") + "</div>" +
        '<div class="card-foot"><span id="supportCount" class="tiny muted"></span><div id="supportPager"></div></div></div></div>' +
        '<div class="card support-pane" id="supportPane">' + A.emptyState("message", "یک مکالمه را انتخاب کنید", "برای مشاهده تاریخچه و پاسخ، روی مکالمه کلیک کنید.") + "</div>" +
      "</div>";

    const status = A.$("#supportStatus");
    if (status) status.addEventListener("change", function () {
      state.support.status = status.value;
      state.support.page = 1;
      loadSupportList(token);
    });
    const q = A.$("#supportQ");
    if (q) {
      let timer = null;
      q.addEventListener("input", function () {
        clearTimeout(timer);
        timer = setTimeout(function () {
          state.support.q = q.value.trim();
          state.support.page = 1;
          loadSupportList(token);
        }, 300);
      });
    }
    await loadSupportList(token);
  }

  async function loadSupportList(token) {
    const box = A.$("#supportList");
    if (!box) return;
    let data;
    try {
      data = await A.api("/api/admin/support?status=" + encodeURIComponent(state.support.status) +
        "&q=" + encodeURIComponent(state.support.q) +
        "&page=" + state.support.page + "&page_size=20");
    } catch (e) {
      if (!alive(token)) return;
      box.innerHTML = A.emptyState("alert", "دریافت مکالمات انجام نشد", e.message);
      return;
    }
    if (!alive(token) || !A.$("#supportList")) return;
    state.support.items = data.items || [];
    state.support.total = data.total || 0;
    if (!state.support.items.length) {
      box.innerHTML = A.emptyState("message", "مکالمه‌ای یافت نشد", "با این فیلتر مکالمه‌ای ثبت نشده است.");
    } else {
      box.innerHTML = '<div class="list">' + state.support.items.map(function (t) {
        const u = t.user || {};
        const active = Number(state.support.current) === Number(t.id);
        return '<button class="support-ticket' + (active ? " active" : "") + '" data-act="support-open" data-tid="' + t.id + '">' +
          '<span class="avatar">' + A.esc(A.initials(u.name || "-")) + "</span>" +
          '<span class="grow"><span class="name">' + A.esc(u.name || "-") +
            (t.status === "open" ? '<span class="pill green">باز</span>' : '<span class="pill gray">بسته</span>') + "</span>" +
            '<span class="preview">' + A.esc(t.last_line || "بدون پیام") + "</span>" +
            '<span class="preview">' + A.esc(t.last_at_fa || t.created_at_fa || "") + "</span></span></button>";
      }).join("") + "</div>";
    }
    const count = A.$("#supportCount");
    const pager = A.$("#supportPager");
    if (count) count.textContent = A.toFa(state.support.total) + " مکالمه";
    if (pager) pager.innerHTML = pagerHTML(state.support.page, Math.ceil(state.support.total / 20));
    if (state.support.current) await openSupportTicket(state.support.current, true);
  }

  async function openSupportTicket(tid, silent) {
    state.support.current = Number(tid) || 0;
    A.$$(".support-ticket").forEach(function (b) {
      b.classList.toggle("active", Number(b.getAttribute("data-tid")) === Number(tid));
    });
    const pane = A.$("#supportPane");
    if (!pane) return;
    pane.innerHTML = loadingCard("در حال دریافت مکالمه…");
    let data;
    try {
      data = await A.api("/api/admin/support/" + encodeURIComponent(tid));
    } catch (e) {
      pane.innerHTML = errorCard(e.message, '<button class="btn btn-soft" data-act="support-refresh">' + A.icon("refresh") + " تلاش دوباره</button>");
      return;
    }
    if (!A.$("#supportPane")) return;
    const t = data.ticket || {};
    const u = data.user || {};
    const msgs = data.messages || [];
    let thread = "";
    msgs.forEach(function (m) {
      const admin = (m.role || m.sender || "") === "admin";
      let media = "";
      if (m.media_url) {
        media = (m.message_type === "photo")
          ? '<img src="' + A.esc(m.media_url) + '" alt="پیوست" loading="lazy">'
          : '<a class="btn btn-sm btn-soft mt-1" href="' + A.esc(m.media_url) + '" target="_blank" rel="noopener">' + A.icon("download") + " دریافت فایل</a>";
      }
      thread += '<div class="support-msg ' + (admin ? "admin" : "user") + '">' + A.esc(m.text || "") + media +
        '<div class="meta"><span>' + (admin ? "مدیریت" : A.esc(u.name || "کاربر")) + "</span><span>" + A.esc(m.created_at_fa || "") + "</span></div></div>";
    });
    pane.innerHTML =
      '<div class="card-head bordered"><div><div class="card-title">' + A.icon("user") + " " + A.esc(u.name || ("کاربر " + A.toFa(t.user_id || ""))) + "</div>" +
        '<div class="card-sub">' + A.esc(u.phone || (u.username ? "@" + u.username : "بدون شناسه")) +
        " • " + (t.status === "open" ? "باز" : "بسته") + " • " + A.esc(t.created_at_fa || "") + "</div></div>" +
        '<div class="card-actions">' +
          '<button class="btn btn-sm btn-soft" data-act="support-status" data-tid="' + t.id + '" data-status="' + (t.status === "open" ? "closed" : "open") + '">' +
            (t.status === "open" ? A.icon("lock") + " بستن" : A.icon("check") + " بازکردن") + "</button>" +
          '<button class="btn btn-sm btn-ghost" data-act="user-open" data-uid="' + t.user_id + '">' + A.icon("eye") + " پروفایل</button>" +
        "</div></div>" +
      '<div class="card-body">' +
        '<div class="detail-grid" style="margin-bottom:12px">' +
          '<div class="detail-item"><span>اشتراک ربات</span><b>' + A.esc(u.bot_until_fa || "-") + "</b></div>" +
          '<div class="detail-item"><span>اشتراک وب‌اپ</span><b>' + A.esc(u.web_until_fa || "-") + "</b></div>" +
        "</div>" +
        '<div class="support-thread" id="supportThread">' + (thread || A.emptyState("message", "پیامی ثبت نشده", "این مکالمه هنوز پیامی ندارد.")) + "</div>" +
        '<div class="support-reply"><div class="field"><label class="label">پاسخ به کاربر</label>' +
          '<textarea class="textarea" id="supportReplyText" rows="3" placeholder="پاسخ شما در تلگرام برای کاربر ارسال می‌شود…"></textarea></div>' +
          '<div class="row-between"><label class="check"><input type="checkbox" id="supportReopen"' + (t.status === "open" ? " checked" : "") + '><span class="check-box">' + A.icon("check") + '</span><span class="check-text">مکالمه باز بماند</span></label>' +
          '<button class="btn btn-primary" data-act="support-send" data-tid="' + t.id + '">' + A.icon("send") + " ارسال پاسخ</button></div>" +
        "</div>" +
      "</div>";
    const threadEl = A.$("#supportThread");
    if (threadEl) threadEl.scrollTop = threadEl.scrollHeight;
    if (silent) {
      /* به‌روزرسانی بی‌صدا پس از تازه‌سازی لیست */
    }
  }

  async function sendSupportReply(tid) {
    const textEl = A.$("#supportReplyText");
    const reopenEl = A.$("#supportReopen");
    const text = textEl ? textEl.value.trim() : "";
    if (!text) {
      A.toast("متن پاسخ را وارد کنید.", "error");
      if (textEl) textEl.focus();
      return;
    }
    try {
      const res = await A.api("/api/admin/support/" + encodeURIComponent(tid) + "/reply", {
        method: "POST",
        body: { text: text, reopen: !!(reopenEl && reopenEl.checked) }
      });
      A.toast(res.message || "پاسخ ارسال شد.", "success");
      await openSupportTicket(tid, true);
      await loadSupportList(viewToken);
    } catch (e) {
      A.toast(e.message, "error");
    }
  }

  async function toggleSupportStatus(tid, status) {
    try {
      const res = await A.api("/api/admin/support/" + encodeURIComponent(tid) + "/status", {
        method: "POST", body: { status: status }
      });
      A.toast(res.message || "وضعیت تغییر کرد.", "success");
      await openSupportTicket(tid, true);
      await loadSupportList(viewToken);
    } catch (e) {
      A.toast(e.message, "error");
    }
  }

  /* ------------------------------------------------------------------
     پلن‌ها، شبکه‌ها، تخفیف و اشتراک رایگان
  ------------------------------------------------------------------ */
  async function renderPlans(token) {
    const root = viewEl();
    root.innerHTML = loadingCard("در حال دریافت پلن‌ها…");
    let data;
    try {
      data = await A.api("/api/admin/plans");
    } catch (e) {
      if (!alive(token)) return;
      root.innerHTML = errorCard(e.message, '<button class="btn btn-soft" data-act="refresh">' + A.icon("refresh") + " تلاش دوباره</button>");
      return;
    }
    if (!alive(token)) return;
    state.plans.data = data;
    await loadPlanDestinations();
    if (!alive(token)) return;
    renderPlanPanel(token);
  }

  async function loadPlanDestinations() {
    try {
      state.plans.dest = await A.api("/api/admin/payment-destinations");
    } catch (e) {
      state.plans.dest = { cards: [], wallets: [] };
    }
  }

  function renderPlanPanel(token) {
    const root = viewEl();
    const data = state.plans.data;
    if (!data) return;
    const plans = data.plans || [];
    const plan = plans.find(function (p) { return p.plan === state.plans.tab; }) || plans[0];
    if (!plan) { root.innerHTML = A.emptyState("alert", "پلنی پیدا نشد", "تنظیمات پلن‌ها در دسترس نیست."); return; }
    const features = data.free_features || [];
    const strategies = data.strategies || [];
    const featureSet = new Set(plan.free.features || []);
    const strategySet = new Set(plan.free.strategies || []);
    const isBot = plan.plan === "bot";
    const netCards = (plan.networks || []).map(function (n) {
      return '<div class="net-card' + (n.enabled ? "" : " off") + '" data-net="' + A.esc(n.network_key) + '">' +
        '<div class="net-top"><span class="coin' + A.coinClass(n.asset) + '">' + A.esc(n.asset) + "</span>" +
          '<div class="grow"><div class="net-title">' + A.esc(n.title) + '</div><div class="net-sub mono">' + A.esc(n.wallet || "کیف پول ثبت نشده") + "</div></div>" +
          '<label class="switch"><input type="checkbox" data-net-enabled' + (n.enabled ? " checked" : "") + '><span class="switch-track"></span></label></div>' +
        '<div class="field"><label class="label">آدرس کیف پول</label><input class="input textarea-ltr" data-net-wallet dir="ltr" value="' + A.esc(n.wallet || "") + '" placeholder="wallet address"></div>' +
        '<div class="field"><label class="label">مبلغ (واحد همان دارایی)</label><input class="input" data-net-amount type="number" min="0" step="0.000001" value="' + A.esc(String(n.amount || 0)) + '"></div>' +
        '<div class="net-foot"><a class="btn btn-sm btn-ghost" data-act="plan-net-save" data-plan="' + plan.plan + '" data-key="' + A.esc(n.network_key) + '">' + A.icon("check") + " ذخیره شبکه</a>" +
          (n.enabled ? '<span class="pill green">فعال</span>' : '<span class="pill gray">غیرفعال</span>') + "</div>" +
      "</div>";
    }).join("");

    let freeExtra = "";
    if (isBot) {
      freeExtra = '<div class="field full"><label class="label">امکانات ربات در دوره رایگان</label>' +
        '<div class="chips">' + features.map(function (f) {
          return '<label class="check"><input type="checkbox" data-free-feature value="' + A.esc(f.key) + '"' + (featureSet.has(f.key) ? " checked" : "") + '><span class="check-box">' + A.icon("check") + '</span><span class="check-text">' + A.esc(f.label) + "</span></label>";
        }).join("") + "</div>" +
        '<div class="hint">اگر هیچ موردی انتخاب نشود، اشتراک رایگان ربات هیچ امکان اضافه‌ای نمی‌دهد.</div></div>';
    } else {
      freeExtra = '<div class="field full"><label class="label">استراتژی‌های قابل استفاده در دوره رایگان</label>' +
        '<div class="picker-tools"><div class="searchbox">' + A.icon("search") + '<input id="freeStratSearch" placeholder="جستجوی استراتژی…"></div>' +
          '<button type="button" class="btn btn-sm btn-ghost" data-act="free-pick-all">همه</button>' +
          '<button type="button" class="btn btn-sm btn-ghost" data-act="free-pick-none">هیچ‌کدام</button></div>' +
        '<div class="picker" id="freeStrategyPicker">' + strategies.map(function (s) {
          return '<label class="check" data-search="' + A.esc((s.title + " " + s.key + " " + s.category).toLowerCase()) + '"><input type="checkbox" data-free-strategy value="' + A.esc(s.key) + '"' + (strategySet.has(s.key) ? " checked" : "") + '><span class="check-box">' + A.icon("check") + '</span><span class="check-text">' + A.esc(s.title) + "</span></label>";
        }).join("") + "</div>" +
        '<div class="hint">فقط استراتژی‌های انتخاب‌شده در دوره رایگان وب‌اپ در دسترس خواهند بود.</div></div>';
    }

    root.innerHTML =
      '<div class="page-head"><div><div class="eyebrow">' + A.icon("star") + " اشتراک‌ها</div>" +
      '<h1 class="page-title">پلن‌ها و پرداخت</h1><p class="page-sub">قیمت، مدت، شبکه‌ها، تخفیف و اشتراک رایگان هر دو پلن را مدیریت کنید.</p></div>' +
      '<div class="page-actions"><button class="btn btn-soft" data-act="refresh">' + A.icon("refresh") + " به‌روزرسانی</button></div></div>" +
      '<div class="plan-head mb-2"><div class="segmented">' + plans.map(function (p) {
        return '<button class="seg' + (p.plan === plan.plan ? " active" : "") + '" data-act="plan-tab" data-plan="' + p.plan + '">' + A.esc(p.label) + "</button>";
      }).join("") + "</div>" +
      (plan.enabled ? '<span class="pill green">فعال</span>' : '<span class="pill gray">غیرفعال</span>') +
      (plan.configured ? '<span class="pill blue">آماده خرید</span>' : '<span class="pill amber">نیازمند تکمیل تنظیمات</span>') + "</div>" +

      '<div class="grid grid-main">' +
        '<div class="stack">' +
          '<form class="card" id="planMainForm" data-plan="' + plan.plan + '"><div class="card-head bordered"><div><div class="card-title">' + A.icon("star") + " تنظیمات پلن</div>" +
            '<div class="card-sub">قیمت پیش‌فرض، مدت، تمدید و قالب پیام خرید.</div></div></div>' +
            '<div class="card-body"><div class="form-grid">' +
              '<div class="field"><label class="label">روش پرداخت</label><select class="select" name="mode">' +
                '<option value="off"' + (plan.mode === "off" ? " selected" : "") + ">غیرفعال</option>" +
                '<option value="manual"' + (plan.mode === "manual" ? " selected" : "") + ">دستی (رسید)</option>" +
                '<option value="auto"' + (plan.mode === "auto" ? " selected" : "") + ">خودکار (تأیید بلاک‌چین)</option>" +
              "</select></div>" +
              '<div class="field"><label class="label">مبلغ پیش‌فرض (دلار)</label><input class="input" name="amount" type="number" min="0" step="0.01" value="' + A.esc(String(plan.amount || 0)) + '"></div>' +
              '<div class="field"><label class="label">مدت اشتراک (روز)</label><input class="input" name="days" type="number" min="1" max="3650" value="' + A.esc(String(plan.days || 30)) + '"></div>' +
              '<div class="field"><label class="label">روزهای تمدید</label><input class="input" name="renew_days" type="number" min="0" max="365" value="' + A.esc(String(plan.renew_days || 0)) + '"></div>' +
              '<div class="field full"><label class="label">قالب پیام خرید / تمدید</label><textarea class="textarea textarea-ltr" name="template" rows="5" dir="rtl">' + A.esc(plan.template || "") + "</textarea>" +
                '<div class="hint">می‌توانید از {amount}، {wallet}، {days}، {site} و {network} استفاده کنید.</div></div>' +
            "</div></div>" +
            '<div class="card-foot"><span class="tiny muted">تغییرات پس از ذخیره در ربات و وب‌اپ اعمال می‌شود.</span>' +
              '<button type="submit" class="btn btn-primary btn-sm">' + A.icon("check") + " ذخیره پلن</button></div>" +
          "</form>" +

          '<div class="card" id="planNetworksCard"><div class="card-head bordered"><div><div class="card-title">' + A.icon("wallet") + " شبکه‌های پرداخت</div>" +
            '<div class="card-sub">فقط شبکه‌های فعال در ربات و صفحه خرید نمایش داده می‌شوند.</div></div></div>' +
            '<div class="card-body"><div class="net-grid">' + netCards + "</div>" +
            '<div class="hint mt-2">برای فعال‌سازی هر شبکه، آدرس کیف پول و مبلغ بزرگ‌تر از صفر الزامی است.</div></div></div>' +

          '<div class="card" id="planDestinationsCard"><div class="card-head bordered"><div><div class="card-title">' + A.icon("hash") + " مقصدهای پرداخت (کارت و کیف‌پول)</div>" +
            '<div class="card-sub">شماره کارت‌ها و کیف‌پول‌های دستی که در صفحه خرید ربات و وب‌اپ برای واریز نمایش داده می‌شوند.</div></div></div>' +
            '<div class="card-body"><div class="net-grid" id="planDestinationsGrid">' + destRowsHtml(plan.plan) + "</div>" +
            '<div class="hint mt-2">شماره کارت با کنترل‌رقم Luhn و شبا با کنترل‌رقم IBAN بررسی می‌شود؛ ثبت شبا اختیاری است. فقط مقصدهای فعال در صفحه خرید دیده می‌شوند و واریز به آن‌ها همیشه با بررسی دستی رسید تأیید می‌شود.</div></div></div>' +
        "</div>" +

        '<div class="stack">' +
          '<form class="card" id="planDiscountForm" data-plan="' + plan.plan + '"><div class="card-head bordered"><div><div class="card-title">' + A.icon("sparkles") + " تخفیف</div>" +
            '<div class="card-sub">درصد تخفیف و بازه زمانی اعمال آن.</div></div></div>' +
            '<div class="card-body"><div class="stack">' +
              '<label class="switch"><input type="checkbox" id="discountOn"' + (plan.discount.on ? " checked" : "") + '><span class="switch-track"></span><span class="switch-text">فعال بودن تخفیف</span></label>' +
              '<div class="form-grid">' +
                '<div class="field"><label class="label">درصد تخفیف</label><input class="input" name="pct" type="number" min="0" max="100" step="0.1" value="' + A.esc(String(plan.discount.pct || 0)) + '"></div>' +
                '<div class="field"><label class="label">مدت (روز)</label><input class="input" name="days" type="number" min="1" max="365" value="' + A.esc(String(plan.discount.days || 1)) + '"></div>' +
              "</div>" +
              '<label class="check"><input type="checkbox" id="discountRestart"><span class="check-box">' + A.icon("check") + '</span><span class="check-text">شروع دوباره بازه تخفیف از حالا</span></label>' +
              '<div class="hint">' + (plan.discount.active ? "تخفیف همین حالا فعال است." : (plan.discount.until ? "بازه تخفیف تعریف شده است." : "تخفیفی فعال نیست.")) + "</div>" +
            "</div></div>" +
            '<div class="card-foot"><span class="tiny muted">درصد تخفیف روی مبلغ همه شبکه‌های این پلن اعمال می‌شود.</span>' +
              '<button type="submit" class="btn btn-primary btn-sm">' + A.icon("check") + " ذخیره تخفیف</button></div>" +
          "</form>" +

          '<form class="card" id="planFreeForm" data-plan="' + plan.plan + '"><div class="card-head bordered"><div><div class="card-title">' + A.icon("heart") + " اشتراک رایگان</div>" +
            '<div class="card-sub">فعال‌سازی دوره آزمایشی و انتخاب امکانات قابل استفاده.</div></div></div>' +
            '<div class="card-body"><div class="stack">' +
              '<label class="switch"><input type="checkbox" id="freeOn"' + (plan.free.on ? " checked" : "") + '><span class="switch-track"></span><span class="switch-text">فعال بودن اشتراک رایگان</span></label>' +
              '<div class="field"><label class="label">مدت دوره (روز)</label><input class="input" name="free_days" type="number" min="1" max="3650" value="' + A.esc(String(plan.free.days || 7)) + '"></div>' +
              freeExtra +
            "</div></div>" +
            '<div class="card-foot"><span class="tiny muted">هر کاربر فقط یک بار می‌تواند اشتراک رایگان این پلن را فعال کند.</span>' +
              '<button type="submit" class="btn btn-primary btn-sm">' + A.icon("check") + " ذخیره اشتراک رایگان</button></div>" +
          "</form>" +
        "</div>" +
      "</div>" +

      '<div class="card mt-3"><div class="card-head"><div><div class="card-title">' + A.icon("history") + " استفاده‌کنندگان از اشتراک رایگان</div>" +
        '<div class="card-sub">آخرین فعال‌سازی‌های دوره رایگان این پلن.</div></div><div class="card-actions"><span id="planClaimsCount" class="tiny muted"></span></div></div>' +
        '<div class="card-body tight" id="planClaimsBox">' + A.loadingRow("در حال دریافت…") + "</div>" +
        '<div class="card-foot"><span class="tiny muted">پس از پایان دوره، دسترسی قبلی کاربر بازگردانده می‌شود.</span><div id="planClaimsPager"></div></div></div>';

    bindPlanForms(plan);
    loadPlanClaims(plan.plan, token);
    focusPlansSection();
  }

  function focusPlansSection() {
    const tab = state.plans.section;
    if (!tab) return;
    state.plans.section = "";
    const sel = PLANS_SECTIONS[tab];
    if (!sel) return;
    setTimeout(function () {
      const target = A.$(sel);
      const view = A.$("#view");
      if (!target || !view || !view.contains(target)) return;
      target.scrollIntoView({ behavior: "smooth", block: "center" });
      target.style.boxShadow = "0 0 0 2px var(--primary)";
      setTimeout(function () {
        if (target.isConnected) target.style.boxShadow = "";
      }, 2000);
    }, 150);
  }

  function bindPlanForms(plan) {
    const mainForm = A.$("#planMainForm");
    if (mainForm) mainForm.addEventListener("submit", async function (e) {
      e.preventDefault();
      const btn = e.target.querySelector('button[type="submit"]');
      A.setBusy(btn, true, "در حال ذخیره…");
      try {
        await A.api("/api/admin/plans/" + encodeURIComponent(plan.plan), {
          method: "PUT",
          body: {
            mode: mainForm.mode.value,
            amount: mainForm.amount.value === "" ? 0 : Number(mainForm.amount.value),
            days: Number(mainForm.days.value || 30),
            renew_days: Number(mainForm.renew_days.value || 0),
            template: mainForm.template.value
          }
        });
        A.toast("تنظیمات پلن ذخیره شد.", "success");
        await reloadPlans();
      } catch (err) {
        A.toast(err.message, "error");
        A.setBusy(btn, false);
      }
    });

    const discountForm = A.$("#planDiscountForm");
    if (discountForm) discountForm.addEventListener("submit", async function (e) {
      e.preventDefault();
      const btn = e.target.querySelector('button[type="submit"]');
      A.setBusy(btn, true, "در حال ذخیره…");
      try {
        await A.api("/api/admin/plans/" + encodeURIComponent(plan.plan) + "/discount", {
          method: "PUT",
          body: {
            enabled: !!A.$("#discountOn").checked,
            pct: Number(discountForm.pct.value || 0),
            days: Number(discountForm.days.value || 1),
            restart: !!(A.$("#discountRestart") && A.$("#discountRestart").checked)
          }
        });
        A.toast("تنظیمات تخفیف ذخیره شد.", "success");
        await reloadPlans();
      } catch (err) {
        A.toast(err.message, "error");
        A.setBusy(btn, false);
      }
    });

    const freeForm = A.$("#planFreeForm");
    if (freeForm) freeForm.addEventListener("submit", async function (e) {
      e.preventDefault();
      const btn = e.target.querySelector('button[type="submit"]');
      const body = {
        free_on: !!A.$("#freeOn").checked,
        free_days: Number(freeForm.free_days.value || 7)
      };
      if (plan.plan === "bot") {
        body.free_features = A.$$("[data-free-feature]:checked").map(function (x) { return x.value; });
      } else {
        body.free_strategies = A.$$("[data-free-strategy]:checked").map(function (x) { return x.value; });
      }
      A.setBusy(btn, true, "در حال ذخیره…");
      try {
        await A.api("/api/admin/plans/" + encodeURIComponent(plan.plan), { method: "PUT", body: body });
        A.toast("تنظیمات اشتراک رایگان ذخیره شد.", "success");
        await reloadPlans();
      } catch (err) {
        A.toast(err.message, "error");
        A.setBusy(btn, false);
      }
    });

    const search = A.$("#freeStratSearch");
    if (search) search.addEventListener("input", function () {
      const q = search.value.trim().toLowerCase();
      A.$$("#freeStrategyPicker [data-search]").forEach(function (el) {
        el.style.display = !q || (el.getAttribute("data-search") || "").indexOf(q) >= 0 ? "" : "none";
      });
    });
  }

  async function reloadPlans() {
    try {
      state.plans.data = await A.api("/api/admin/plans");
      await loadPlanDestinations();
      renderPlanPanel(viewToken);
    } catch (e) {
      A.toast(e.message, "error");
    }
  }

  /* ---------------- مقصدهای پرداخت (کارت بانکی + کیف‌پول دستی) ---------------- */
  const WALLET_CHAINS = [
    ["tron", "TRON (TRC-20)"], ["bsc", "BSC (BEP-20)"], ["arbitrum", "Arbitrum"], ["ton", "TON"], ["other", "سایر"]
  ];

  function destField(label, key, value, opts) {
    const o = opts || {};
    return '<div class="field"><label class="label">' + label + "</label>" +
      '<input class="input' + (o.ltr ? " textarea-ltr" : "") + '" data-dest-' + key +
      (o.type ? ' type="' + o.type + '"' : "") + ' dir="' + (o.ltr ? "ltr" : "rtl") + '" value="' + A.esc(value == null ? "" : value) + '"' +
      (o.placeholder ? ' placeholder="' + A.esc(o.placeholder) + '"' : "") + (o.extra || "") + "></div>";
  }

  function destCardRow(c) {
    const isNew = !c.id;
    return '<div class="net-card' + (c.is_active || isNew ? "" : " off") + '" data-dest="card:' + (c.id || 0) + '">' +
      '<div class="net-top"><span class="pill blue">کارت بانکی</span>' +
        '<div class="grow"><div class="net-title">' + A.esc(c.bank_name || "بانک") + '</div>' +
        '<div class="net-sub mono" dir="ltr">' + A.esc(c.card_number || c.masked || "شماره کارت وارد نشده") + "</div></div>" +
        '<label class="switch"><input type="checkbox" data-dest-active' + (c.is_active || isNew ? " checked" : "") + '><span class="switch-track"></span></label></div>' +
      '<div class="form-grid">' +
        destField("شماره کارت (۱۶ رقم)", "number", c.card_number, { ltr: true, placeholder: "6037 7000 0000 0000" }) +
        destField("به نام", "holder", c.holder_name) +
        destField("نام بانک", "bank", c.bank_name) +
        destField("کد بانک", "bankcode", c.bank_code, { ltr: true, placeholder: "013" }) +
        destField("شبا (اختیاری)", "shaba", c.shaba, { ltr: true, placeholder: "IR…" }) +
        destField("یادداشت داخلی", "note", c.note) +
      "</div>" +
      '<div class="net-foot">' +
        '<button type="button" class="btn btn-sm btn-ghost" data-act="dest-save" data-kind="card" data-id="' + (c.id || 0) + '">' +
          A.icon(isNew ? "plus" : "check") + (isNew ? " افزودن کارت" : " ذخیره کارت") + "</button>" +
        (isNew ? "" : '<button type="button" class="btn btn-sm btn-danger" data-act="dest-delete" data-kind="card" data-id="' + c.id + '">' + A.icon("trash") + " حذف</button>") +
        (isNew ? "" : (c.is_active ? '<span class="pill green">فعال</span>' : '<span class="pill gray">غیرفعال</span>')) +
      "</div></div>";
  }

  function destWalletRow(w) {
    const isNew = !w.id;
    const chain = w.chain || "tron";
    const options = WALLET_CHAINS.map(function (o) {
      return '<option value="' + o[0] + '"' + (chain === o[0] ? " selected" : "") + ">" + o[1] + "</option>";
    }).join("");
    return '<div class="net-card' + (w.is_active || isNew ? "" : " off") + '" data-dest="wallet:' + (w.id || 0) + '">' +
      '<div class="net-top"><span class="pill violet">کیف‌پول</span>' +
        '<div class="grow"><div class="net-title">' + A.esc(w.network_label || w.network || "شبکه") + "</div>" +
        '<div class="net-sub mono" dir="ltr">' + A.esc(w.address || "آدرس وارد نشده") + "</div></div>" +
        '<label class="switch"><input type="checkbox" data-dest-active' + (w.is_active || isNew ? " checked" : "") + '><span class="switch-track"></span></label></div>' +
      '<div class="form-grid">' +
        '<div class="field"><label class="label">زنجیره</label><select class="select" data-dest-chain>' + options + "</select></div>" +
        destField("نام نمایشی شبکه", "label", w.network_label, { placeholder: "TRON · TRC-20" }) +
        destField("دارایی", "asset", w.asset || "USDT", { ltr: true, placeholder: "USDT" }) +
        destField("آدرس کیف پول", "address", w.address, { ltr: true, placeholder: "T…" }) +
        destField("مبلغ (واحد همان دارایی)", "amount", w.amount == null ? "0" : String(w.amount), { ltr: true, type: "number", extra: ' min="0" step="0.000001"' }) +
        destField("یادداشت داخلی", "note", w.note) +
      "</div>" +
      '<div class="net-foot">' +
        '<button type="button" class="btn btn-sm btn-ghost" data-act="dest-save" data-kind="wallet" data-id="' + (w.id || 0) + '">' +
          A.icon(isNew ? "plus" : "check") + (isNew ? " افزودن کیف‌پول" : " ذخیره کیف‌پول") + "</button>" +
        (isNew ? "" : '<button type="button" class="btn btn-sm btn-danger" data-act="dest-delete" data-kind="wallet" data-id="' + w.id + '">' + A.icon("trash") + " حذف</button>") +
        (isNew ? "" : (w.is_active ? '<span class="pill green">فعال</span>' : '<span class="pill gray">غیرفعال</span>')) +
      "</div></div>";
  }

  function destRowsHtml(planKey) {
    const dest = state.plans.dest || { cards: [], wallets: [] };
    const cards = (dest.cards || []).filter(function (c) { return c.plan === planKey; }).map(destCardRow);
    const wallets = (dest.wallets || []).filter(function (w) { return w.plan === planKey; }).map(destWalletRow);
    cards.push(destCardRow({ id: 0, is_active: true }));
    wallets.push(destWalletRow({ id: 0, is_active: true, asset: "USDT", chain: "tron" }));
    return cards.join("") + wallets.join("");
  }

  async function refreshDestGrid(planKey) {
    await loadPlanDestinations();
    const box = A.$("#planDestinationsGrid");
    if (box) box.innerHTML = destRowsHtml(planKey);
  }

  function destPayload(root, kind) {
    const get = function (key) {
      const n = root.querySelector("[data-dest-" + key + "]");
      return n ? String(n.value).trim() : "";
    };
    const active = root.querySelector("[data-dest-active]");
    if (kind === "card") {
      return {
        card_number: get("number"), holder_name: get("holder"), bank_name: get("bank"),
        bank_code: get("bankcode"), shaba: get("shaba"), note: get("note"),
        is_active: !!(active && active.checked)
      };
    }
    const chain = get("chain");
    return {
      chain: chain, network: chain, network_label: get("label"), asset: get("asset"),
      address: get("address"), amount: Number(get("amount") || 0) || 0, note: get("note"),
      is_active: !!(active && active.checked)
    };
  }

  async function savePlanDestination(kind, id) {
    const plan = state.plans.tab || "bot";
    const root = document.querySelector('[data-dest="' + kind + ":" + id + '"]');
    if (!root) return;
    const btn = root.querySelector('[data-act="dest-save"]');
    A.setBusy(btn, true, "در حال ذخیره…");
    const body = destPayload(root, kind);
    body.plan = plan;
    try {
      await A.api(id ? ("/api/admin/" + kind + "s/" + id) : ("/api/admin/" + kind + "s"), {
        method: id ? "PUT" : "POST", body: body
      });
      A.toast(id ? "مقصد پرداخت ذخیره شد." : "مقصد پرداخت افزوده شد.", "success");
      await refreshDestGrid(plan);
    } catch (e) {
      A.toast(e.message, "error");
      if (btn) A.setBusy(btn, false);
    }
  }

  async function deletePlanDestination(kind, id) {
    const plan = state.plans.tab || "bot";
    if (!id) return;
    if (!window.confirm("این مقصد پرداخت حذف شود؟")) return;
    try {
      await A.api("/api/admin/" + kind + "s/" + id, { method: "DELETE" });
      A.toast("مقصد پرداخت حذف شد.", "success");
      await refreshDestGrid(plan);
    } catch (e) {
      A.toast(e.message, "error");
    }
  }

  async function savePlanNetwork(plan, key) {
    const card = document.querySelector('[data-net="' + key + '"]');
    if (!card) return;
    const wallet = card.querySelector("[data-net-wallet]");
    const amount = card.querySelector("[data-net-amount]");
    const enabled = card.querySelector("[data-net-enabled]");
    try {
      await A.api("/api/admin/plans/" + encodeURIComponent(plan) + "/networks/" + encodeURIComponent(key), {
        method: "PUT",
        body: {
          wallet: wallet ? wallet.value.trim() : "",
          amount: amount && amount.value !== "" ? Number(amount.value) : 0,
          enabled: !!(enabled && enabled.checked)
        }
      });
      A.toast("تنظیمات شبکه ذخیره شد.", "success");
      await reloadPlans();
    } catch (e) {
      A.toast(e.message, "error");
    }
  }

  async function loadPlanClaims(plan, token) {
    const box = A.$("#planClaimsBox");
    if (!box) return;
    if (state.plans.claims.plan !== plan) {
      state.plans.claims.plan = plan;
      state.plans.claims.page = 1;
    }
    const page = Math.max(1, Number(state.plans.claims.page || 1));
    let data;
    try {
      data = await A.api("/api/admin/free-claims?plan=" + encodeURIComponent(plan) + "&page=" + page + "&page_size=" + CLAIM_SIZE);
    } catch (e) {
      if (!alive(token)) return;
      box.innerHTML = A.emptyState("alert", "دریافت اطلاعات انجام نشد", e.message);
      return;
    }
    if (!alive(token) || !A.$("#planClaimsBox")) return;
    state.plans.claims = Object.assign({}, data, { plan: plan, page: page });
    const items = data.items || [];
    box.innerHTML = items.length ? '<div class="claim-list">' + items.map(function (c) {
      return '<div class="claim-item"><div class="list-icon ' + (c.active ? "green" : "amber") + '">' + A.icon(c.active ? "check" : "clock") + "</div>" +
        '<div class="grow"><b>' + A.esc(c.name) + '</b><div class="cell-sub">کاربر ' + A.toFa(c.user_id) + " • " + (c.plan === "bot" ? "ربات" : "تحلیل ساختاری") + "</div></div>" +
        '<div class="cell-sub">' + A.esc(c.starts_at_fa) + " — " + A.esc(c.ends_at_fa) + "</div>" +
        (c.active ? '<span class="pill green">فعال</span>' : (c.restored ? '<span class="pill gray">بازگردانی‌شده</span>' : '<span class="pill amber">پایان‌یافته</span>')) + "</div>";
    }).join("") + "</div>" : A.emptyState("heart", "هنوز استفاده‌ای ثبت نشده", "با فعال‌سازی اشتراک رایگان، کاربران در این جدول نمایش داده می‌شوند.");
    const count = A.$("#planClaimsCount");
    if (count) count.textContent = A.toFa(data.total || 0) + " استفاده";
    const pager = A.$("#planClaimsPager");
    if (pager) pager.innerHTML = pagerHTML(page, Math.ceil((data.total || 0) / CLAIM_SIZE));
  }

  /* ------------------------------------------------------------------
     بکاپ و بازیابی
  ------------------------------------------------------------------ */
  async function renderBackups(token) {
    const root = viewEl();
    root.innerHTML =
      '<div class="page-head"><div><div class="eyebrow">' + A.icon("download") + " نگهداری داده</div>" +
      '<h1 class="page-title">بکاپ و بازیابی</h1><p class="page-sub">ساخت بکاپ کامل یا بخشی، تاریخچه، دانلود، بازیابی و بارگذاری فایل.</p></div>' +
      '<div class="page-actions"><button class="btn btn-soft" data-act="backups-refresh">' + A.icon("refresh") + " به‌روزرسانی</button></div></div>" +
      '<div class="grid grid-main">' +
        '<div class="card"><div class="card-head bordered"><div><div class="card-title">' + A.icon("download") + " ساخت بکاپ</div>" +
          '<div class="card-sub">برای بکاپ کامل هیچ بخشی انتخاب نکنید. برای بکاپ بخشی، بخش‌های موردنظر را علامت بزنید.</div></div></div>' +
          '<div class="card-body"><div id="backupSections" class="chips">' + A.loadingRow("در حال دریافت بخش‌ها…") + "</div>" +
            '<div class="row-between mt-2"><span class="tiny muted">فایل بکاپ به‌صورت JSON در سرور ذخیره می‌شود.</span>' +
            '<button class="btn btn-primary" data-act="backup-create">' + A.icon("plus") + " ساخت بکاپ</button></div></div></div>" +
        '<div class="card"><div class="card-head bordered"><div><div class="card-title">' + A.icon("arrow-up-left") + " بازیابی از فایل</div>" +
          '<div class="card-sub">فایل بکاپ آبان ترید را بارگذاری کنید؛ داده‌های فعلی جایگزین می‌شوند.</div></div></div>' +
          '<div class="card-body"><input class="input" id="backupUpload" type="file" accept="application/json,.json">' +
            '<div class="notice warn mt-2">' + A.icon("alert") + "<div>بازیابی، داده‌های فعلی بخش‌های موجود در فایل را بازنویسی می‌کند. پیش از بازیابی، بکاپ تازه بگیرید.</div></div></div>" +
          '<div class="card-foot"><span class="tiny muted">حداکثر حجم مجاز توسط سرور کنترل می‌شود.</span>' +
            '<button class="btn btn-danger btn-sm" data-act="backup-upload">' + A.icon("download") + " بارگذاری و بازیابی</button></div></div>" +
      "</div>" +
      '<div class="card mt-3"><div class="card-head"><div><div class="card-title">' + A.icon("history") + " تاریخچه بکاپ‌ها</div></div>" +
        '<div class="card-actions"><span id="backupAutoInfo" class="tiny muted"></span></div></div>' +
        '<div class="card-body tight" id="backupTableBox">' + A.loadingRow("در حال دریافت تاریخچه…") + "</div>" +
        '<div class="card-foot"><span id="backupCount" class="tiny muted"></span><div id="backupPager"></div></div></div>';
    await loadBackups(token);
  }

  async function loadBackups(token) {
    const box = A.$("#backupTableBox");
    if (!box) return;
    let data;
    try {
      data = await A.api("/api/admin/backups?page=" + state.backups.page + "&page_size=20");
    } catch (e) {
      if (!alive(token)) return;
      box.innerHTML = A.emptyState("alert", "دریافت بکاپ‌ها انجام نشد", e.message);
      return;
    }
    if (!alive(token) || !A.$("#backupTableBox")) return;
    state.backups.items = data.items || [];
    state.backups.total = data.total || 0;
    state.backups.sections = data.sections || [];
    state.backups.auto = data.auto || {};
    state.backups.keep = data.keep || 0;
    const secBox = A.$("#backupSections");
    if (secBox) {
      secBox.innerHTML = state.backups.sections.map(function (s) {
        return '<label class="check"><input type="checkbox" data-backup-section value="' + A.esc(s.key) + '"><span class="check-box">' + A.icon("check") + '</span><span class="check-text">' + A.esc(s.label) + " <span class=\"tiny muted\">(" + A.toFa(s.count) + " جدول)</span></span></label>";
      }).join("");
    }
    const autoInfo = A.$("#backupAutoInfo");
    if (autoInfo) autoInfo.textContent = "نگهداری: " + A.toFa(state.backups.keep || 0) + " فایل" + (state.backups.auto && state.backups.auto.on ? " • خودکار هر " + A.toFa(state.backups.auto.days || 0) + " روز" : "");
    if (!state.backups.items.length) {
      box.innerHTML = A.emptyState("download", "هنوز بکاپی ساخته نشده", "برای شروع، یک بکاپ کامل بسازید.");
    } else {
      box.innerHTML = '<div class="table-wrap"><table class="table table-fix"><thead><tr>' +
        "<th>شناسه</th><th>فایل</th><th>نوع</th><th>حجم</th><th>وضعیت</th><th>تاریخ</th><th></th></tr></thead><tbody>" +
        state.backups.items.map(function (r) {
          return "<tr><td>#" + A.toFa(r.id) + "</td><td><div class=\"cell-main mono\" style=\"direction:ltr\">" + A.esc(r.filename) + "</div>" +
            '<div class="cell-sub">' + A.toFa((r.tables || []).length) + " جدول</div></td>" +
            '<td class="tiny">' + (r.kind === "full" ? "کامل" : "بخشی") + "</td>" +
            '<td class="tiny">' + A.fmtBytes(r.size || 0) + "</td>" +
            '<td><span class="pill ' + (r.status === "ready" ? "green" : "amber") + '">' + (r.status === "ready" ? "آماده" : A.esc(r.status || "-")) + "</span></td>" +
            '<td class="tiny muted nowrap">' + A.esc(r.created_at_fa || "-") + "</td>" +
            '<td><div class="backup-actions">' +
              '<a class="icon-btn sm" href="/api/admin/backups/' + r.id + '/download" title="دانلود">' + A.icon("download") + "</a>" +
              '<button class="icon-btn sm" data-act="backup-restore" data-bid="' + r.id + '" title="بازیابی">' + A.icon("refresh") + "</button>" +
              '<button class="icon-btn sm danger" data-act="backup-delete" data-bid="' + r.id + '" title="حذف">' + A.icon("trash") + "</button>" +
            "</div></td></tr>";
        }).join("") + "</tbody></table></div>";
    }
    const count = A.$("#backupCount");
    const pager = A.$("#backupPager");
    if (count) count.textContent = A.toFa(state.backups.total) + " بکاپ";
    if (pager) pager.innerHTML = pagerHTML(state.backups.page, Math.ceil(state.backups.total / 20));
  }

  async function createBackup() {
    const sections = A.$$("[data-backup-section]:checked").map(function (x) { return x.value; });
    const ok = await A.confirm({
      title: sections.length ? "ساخت بکاپ بخشی" : "ساخت بکاپ کامل",
      message: sections.length
        ? "بکاپ از " + sections.length + " بخش انتخاب‌شده ساخته می‌شود."
        : "از تمام اطلاعات سامانه بکاپ کامل ساخته می‌شود.",
      confirmText: "ساخت بکاپ"
    });
    if (!ok) return;
    try {
      const res = await A.api("/api/admin/backups", { method: "POST", body: { sections: sections } });
      A.toast(res.message || "بکاپ ساخته شد.", "success");
      await loadBackups(viewToken);
    } catch (e) {
      A.toast(e.message, "error");
    }
  }

  async function restoreBackup(bid) {
    const ok = await A.confirm({
      title: "بازیابی بکاپ",
      message: "داده‌های فعلی با محتوای این بکاپ جایگزین می‌شود. این عملیات قابل بازگشت نیست.",
      danger: true,
      confirmText: "بازیابی"
    });
    if (!ok) return;
    try {
      const res = await A.api("/api/admin/backups/" + bid + "/restore", { method: "POST" });
      A.toast(res.message || "بازیابی انجام شد.", "success");
      await loadBackups(viewToken);
    } catch (e) {
      A.toast(e.message, "error");
    }
  }

  async function deleteBackup(bid) {
    const ok = await A.confirm({
      title: "حذف بکاپ",
      message: "فایل و رکورد این بکاپ حذف می‌شود.",
      danger: true,
      confirmText: "حذف"
    });
    if (!ok) return;
    try {
      await A.api("/api/admin/backups/" + bid, { method: "DELETE" });
      A.toast("بکاپ حذف شد.", "success");
      await loadBackups(viewToken);
    } catch (e) {
      A.toast(e.message, "error");
    }
  }

  async function uploadBackup() {
    const input = A.$("#backupUpload");
    const f = input && input.files && input.files[0];
    if (!f) { A.toast("فایل بکاپ را انتخاب کنید.", "error"); return; }
    const ok = await A.confirm({
      title: "بارگذاری و بازیابی",
      message: "فایل انتخاب‌شده بررسی و در صورت معتبر بودن، داده‌های آن بازیابی می‌شود.",
      danger: true,
      confirmText: "بارگذاری و بازیابی"
    });
    if (!ok) return;
    const fd = new FormData();
    fd.append("file", f);
    try {
      const res = await A.api("/api/admin/backups/upload", { method: "POST", body: fd });
      A.toast(res.message || "بازیابی انجام شد.", "success");
      if (input) input.value = "";
      await loadBackups(viewToken);
    } catch (e) {
      A.toast(e.message, "error");
    }
  }

  /* ------------------------------------------------------------------
     امنیت
  ------------------------------------------------------------------ */
  async function renderSecurity(token) {
    const root = viewEl();
    root.innerHTML = loadingCard("در حال دریافت تنظیمات امنیتی…");
    let data;
    try {
      data = await A.api("/api/admin/security");
    } catch (e) {
      if (!alive(token)) return;
      root.innerHTML = errorCard(e.message, '<button class="btn btn-soft" data-act="refresh">' + A.icon("refresh") + " تلاش دوباره</button>");
      return;
    }
    if (!alive(token)) return;
    state.security = data;
    const bot = data.bot || {};
    const web = data.web || {};
    const stats = data.stats || {};
    const events = data.events || [];
    root.innerHTML =
      '<div class="page-head"><div><div class="eyebrow">' + A.icon("shield") + " حفاظت سامانه</div>" +
      '<h1 class="page-title">امنیت و محدودیت‌ها</h1><p class="page-sub">محدودیت‌های ربات و وب‌اپ جداگانه تنظیم می‌شوند و رویدادهای امنیتی ثبت می‌گردند.</p></div>' +
      '<div class="page-actions"><button class="btn btn-soft" data-act="security-refresh">' + A.icon("refresh") + " به‌روزرسانی</button></div></div>" +
      '<div class="settings-grid">' +
        '<form class="card" id="securityForm"><div class="card-head bordered"><div><div class="card-title">' + A.icon("shield") + " محدودیت درخواست‌ها</div>" +
          '<div class="card-sub">پنجره زمانی بر حسب ثانیه و مدت مسدودسازی نیز بر حسب ثانیه است.</div></div></div>' +
          '<div class="card-body">' +
            '<div class="setting-title">' + A.icon("send") + " ربات تلگرام</div>" +
            '<div class="form-grid three">' +
              '<div class="field"><label class="label">حداکثر درخواست</label><input class="input" name="rl_max" type="number" min="1" value="' + A.esc(String(bot.rl_max || 0)) + '"></div>' +
              '<div class="field"><label class="label">پنجره (ثانیه)</label><input class="input" name="rl_window" type="number" min="1" value="' + A.esc(String(bot.rl_window || 0)) + '"></div>' +
              '<div class="field"><label class="label">مدت مسدودی (ثانیه)</label><input class="input" name="rl_block" type="number" min="0" value="' + A.esc(String(bot.rl_block || 0)) + '"></div>' +
              '<div class="field"><label class="label">سقف سراسری</label><input class="input" name="rl_global" type="number" min="1" value="' + A.esc(String(bot.rl_global || 0)) + '"></div>' +
              '<div class="field"><label class="label">حداکثر پیام تکراری</label><input class="input" name="as_max" type="number" min="1" value="' + A.esc(String(bot.as_max || 0)) + '"></div>' +
              '<div class="field"><label class="label">پنجره ضد تکرار (ثانیه)</label><input class="input" name="as_window" type="number" min="1" value="' + A.esc(String(bot.as_window || 0)) + '"></div>' +
              '<div class="field"><label class="label">مسدودی ضد تکرار (ثانیه)</label><input class="input" name="as_block" type="number" min="0" value="' + A.esc(String(bot.as_block || 0)) + '"></div>' +
            "</div>" +
            '<div class="setting-block"><div class="setting-title">' + A.icon("shield") + " وب‌اپ</div>" +
              '<div class="setting-desc">این محدودیت‌ها فقط روی درخواست‌های وب‌اپ اعمال می‌شوند و با محدودیت ربات مستقل هستند.</div>' +
              '<div class="form-grid three">' +
                '<div class="field"><label class="label">حداکثر درخواست</label><input class="input" name="wsec_max" type="number" min="1" value="' + A.esc(String(web.max || 0)) + '"></div>' +
                '<div class="field"><label class="label">پنجره (ثانیه)</label><input class="input" name="wsec_window" type="number" min="1" value="' + A.esc(String(web.window || 0)) + '"></div>' +
                '<div class="field"><label class="label">مدت مسدودی (ثانیه)</label><input class="input" name="wsec_block" type="number" min="1" value="' + A.esc(String(web.block || 0)) + '"></div>' +
              "</div>" +
              '<div class="row wrap mt-2"><label class="switch"><input type="checkbox" id="wsecOn"' + (web.on ? " checked" : "") + '><span class="switch-track"></span><span class="switch-text">فعال بودن محدودیت وب‌اپ</span></label>' +
              '<label class="switch"><input type="checkbox" id="wsecNotify"' + (web.notify ? " checked" : "") + '><span class="switch-track"></span><span class="switch-text">اطلاع به مدیر ربات</span></label></div>' +
            "</div>" +
          "</div>" +
          '<div class="card-foot"><span class="tiny muted">تغییرات بلافاصله اعمال می‌شوند.</span>' +
            '<button type="submit" class="btn btn-primary btn-sm">' + A.icon("check") + " ذخیره تنظیمات</button></div>" +
        "</form>" +

        '<div class="settings-side">' +
          '<div class="card"><div class="card-head"><div><div class="card-title">' + A.icon("activity") + " وضعیت فعلی</div></div></div>" +
            '<div class="card-body"><div class="stat-mini">' +
              '<div class="stat-mini-item"><span>IPهای تحت نظر</span><b>' + A.toFa(stats.tracked || 0) + "</b></div>" +
              '<div class="stat-mini-item"><span>IPهای مسدود</span><b>' + A.toFa(stats.blocked_count || 0) + "</b></div>" +
            "</div>" +
            ((stats.blocked || []).length ? '<div class="chips mt-2">' + (stats.blocked || []).map(function (b) {
              return '<span class="ip-chip">' + A.esc(b.key) + '<span class="tiny muted">' + A.toFa(b.seconds_left) + "s</span>" +
                '<button data-act="security-clear-ip" data-ip="' + A.esc(b.key) + '" title="رفع مسدودی">' + A.icon("close") + "</button></span>";
            }).join("") + "</div>" : '<div class="hint mt-2">هیچ IP مسدودی وجود ندارد.</div>') +
            '<div class="field mt-2"><label class="label">رفع محدودیت دستی</label><div class="inline-form">' +
              '<input class="input" id="clearIpInput" dir="ltr" placeholder="IP address">' +
              '<button type="button" class="btn btn-sm btn-soft" data-act="security-clear-ip-manual">رفع</button></div></div></div></div>' +
          '<div class="card"><div class="card-head"><div><div class="card-title">' + A.icon("history") + " رویدادهای امنیتی</div></div></div>" +
            '<div class="card-body tight">' + (events.length ? '<div class="list">' + events.slice(0, 12).map(function (ev) {
              return '<div class="event-row"><div class="list-icon amber">' + A.icon("shield") + '</div><div class="grow">' +
                '<div class="event-title">' + A.esc(ev.kind || ev.event || "رویداد امنیتی") + '</div>' +
                '<div class="event-sub">' + A.esc(ev.detail || ev.message || "") + "</div></div>" +
                '<div class="cell-sub nowrap">' + A.esc(ev.created_at_fa || "") + "</div></div>";
            }).join("") + "</div>" : A.emptyState("shield", "رویدادی ثبت نشده", "رویدادهای محدودیت و مسدودسازی اینجا نمایش داده می‌شوند.")) + "</div></div>" +
        "</div>" +
      "</div>";

    const form = A.$("#securityForm");
    if (form) form.addEventListener("submit", async function (e) {
      e.preventDefault();
      const btn = e.target.querySelector('button[type="submit"]');
      A.setBusy(btn, true, "در حال ذخیره…");
      const body = {};
      ["rl_max", "rl_window", "rl_block", "rl_global", "as_max", "as_window", "as_block", "wsec_max", "wsec_window", "wsec_block"].forEach(function (k) {
        const el = form.elements[k];
        if (el && el.value !== "") body[k] = Number(el.value);
      });
      body.wsec_on = !!A.$("#wsecOn").checked;
      body.wsec_notify = !!A.$("#wsecNotify").checked;
      try {
        const res = await A.api("/api/admin/security", { method: "PUT", body: body });
        state.security = res;
        A.toast(res.message || "تنظیمات امنیتی ذخیره شد.", "success");
        refreshCurrent();
      } catch (err) {
        A.toast(err.message, "error");
        A.setBusy(btn, false);
      }
    });
  }

  async function clearSecurityIp(ip) {
    try {
      const res = await A.api("/api/admin/security/clear", { method: "POST", body: { ip: ip } });
      A.toast(res.message || "محدودیت برداشته شد.", "success");
      refreshCurrent();
    } catch (e) {
      A.toast(e.message, "error");
    }
  }

  /* ------------------------------------------------------------------
     ابزارهای مشترک محتوا
  ------------------------------------------------------------------ */
  const WIDTH_LABELS = { narrow: "باریک (۷۲۰ پیکسل)", normal: "معمولی (۹۶۰ پیکسل)", wide: "پهن (۱۲۰۰ پیکسل)", full: "تمام عرض" };
  const FIELD_TYPE_LABELS = { text: "متن", email: "ایمیل", phone: "موبایل", password: "رمز عبور", number: "عدد", select: "انتخابی" };
  const MENU_TYPE_LABELS = { page: "صفحه", post: "مقاله", category: "دسته‌بندی", custom_link: "لینک دلخواه" };

  function optionsHTML(pairs, current) {
    return (pairs || []).map(function (p) {
      return '<option value="' + A.esc(p[0]) + '"' + (String(current) === String(p[0]) ? " selected" : "") + ">" + A.esc(p[1]) + "</option>";
    }).join("");
  }
  function widthOptions(current) {
    return optionsHTML(Object.keys(WIDTH_LABELS).map(function (k) { return [k, WIDTH_LABELS[k]]; }), current || "normal");
  }
  function robotsOptions(current) {
    return optionsHTML([
      ["", "پیش‌فرض سامانه"], ["index,follow", "index,follow"], ["noindex,follow", "noindex,follow"],
      ["index,nofollow", "index,nofollow"], ["noindex,nofollow", "noindex,nofollow"]
    ], current || "");
  }
  function typeBadge(type) {
    return '<span class="pill ' + (type === "page" ? "blue" : (type === "post" ? "violet" : (type === "category" ? "cyan" : "gray"))) + '">' +
      A.esc(MENU_TYPE_LABELS[type] || type || "-") + "</span>";
  }

  function bindTabGroup(root, tabAttr, panelAttr) {
    root.querySelectorAll("[" + tabAttr + "]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        setTabGroup(root, tabAttr, panelAttr, btn.getAttribute(tabAttr));
      });
    });
  }
  function setTabGroup(root, tabAttr, panelAttr, key) {
    root.querySelectorAll("[" + tabAttr + "]").forEach(function (b) {
      b.classList.toggle("active", b.getAttribute(tabAttr) === key);
    });
    root.querySelectorAll("[" + panelAttr + "]").forEach(function (p) {
      p.hidden = p.getAttribute(panelAttr) !== key;
    });
  }

  /* ---------- محتوای غنی: CKEditor ۵ خودمیزبان، با جایگزین داخلی ---------- */

  function cmsEditor() { return window.AbanCMSEditor || null; }

  function cmsReady() {
    const C = cmsEditor();
    return !!(C && typeof C.create === "function" && typeof C.isAvailable === "function" && C.isAvailable());
  }

  // نوار ابزار ساده؛ وقتی CKEditor فعال است پنهان می‌ماند و در صورت شکست فعال می‌شود.
  function legacyToolbarHTML(hidden) {
    return '<div class="editor-toolbar"' + (hidden ? " hidden" : "") + ">" +
      '<button type="button" data-cmd="bold" title="ضخیم"><b>B</b></button>' +
      '<button type="button" data-cmd="italic" title="مورب"><i>I</i></button>' +
      '<button type="button" data-cmd="underline" title="زیرخط"><u>U</u></button>' +
      '<button type="button" data-cmd="formatBlock" data-value="h2">H2</button>' +
      '<button type="button" data-cmd="formatBlock" data-value="h3">H3</button>' +
      '<button type="button" data-cmd="formatBlock" data-value="p">P</button>' +
      '<button type="button" data-cmd="insertUnorderedList">لیست</button>' +
      '<button type="button" data-cmd="insertOrderedList">شماره</button>' +
      '<button type="button" data-cmd="createLink">لینک</button>' +
      '<button type="button" data-cmd="media">تصویر</button>' +
      '<button type="button" data-cmd="removeFormat">پاک‌سازی</button>' +
      "</div>";
  }

  // ویرایشگر را روی میزبان سوار می‌کند؛ در هر خطا ویرایشگر ساده بازمی‌گردد.
  function mountCmsEditor(modal, host, initialHTML) {
    const C = cmsEditor();
    if (!C || !host) return;
    modal._cmsInitial = initialHTML || "";
    modal._cmsPending = true;
    C.create(host, { value: modal._cmsInitial, previewStyles: ["/assets/css/public.css"] })
      .then(function (handle) {
        modal._cmsPending = false;
        if (!host.isConnected) { try { handle.destroy(); } catch (e) {} return; }
        modal._cms = handle;
      })
      .catch(function (err) {
        if (modal._cmsPending === false) return;
        modal._cmsPending = false;
        useLegacyEditor(modal);
        A.toast("ویرایشگر حرفه‌ای باز نشد؛ ویرایشگر ساده فعال شد.", "error");
      });
  }

  // میزبان را به ویرایشگر ساده‌ی قبلی برمی‌گرداند (بدون بافتِ نیمه‌کاره‌ی CKEditor).
  function useLegacyEditor(modal) {
    const wrap = modal.root.querySelector(".cms-editor");
    if (!wrap) return;
    if (modal._cms) { try { modal._cms.destroy(); } catch (e) {} modal._cms = null; }
    wrap.innerHTML = legacyToolbarHTML(false) +
      '<div class="rich-editor" id="edContent" contenteditable="true">' + (modal._cmsInitial || "") + "</div>" +
      '<div class="hint">محتوا قبل از ذخیره با فیلتر امنیتی پاک‌سازی می‌شود.</div>';
    bindRichEditor(wrap, wrap.querySelector("#edContent"));
  }

  function cmsContent(modal) {
    const handle = modal._cms;
    if (handle && typeof handle.getData === "function") {
      try { return handle.getData() || ""; } catch (e) { return ""; }
    }
    const el = modal.root.querySelector("#edContent");
    return el ? (el.innerHTML || "") : "";
  }

  function bindRichEditor(root, area) {
    if (!area) return;
    const bar = root.querySelector(".editor-toolbar");
    if (!bar) return;
    bar.addEventListener("mousedown", function (e) { e.preventDefault(); });
    bar.addEventListener("click", async function (e) {
      const btn = e.target.closest("[data-cmd]");
      if (!btn) return;
      e.preventDefault();
      const cmd = btn.getAttribute("data-cmd");
      if (cmd === "media") { openMediaPicker(area); return; }
      if (cmd === "createLink") {
        const url = await A.prompt({ title: "درج لینک", label: "آدرس مقصد", placeholder: "https://", confirmText: "درج" });
        if (!url) return;
        area.focus();
        document.execCommand("createLink", false, url);
        return;
      }
      area.focus();
      if (cmd === "formatBlock") document.execCommand(cmd, false, btn.getAttribute("data-value") || "p");
      else document.execCommand(cmd, false, null);
    });
  }

  function openMediaPicker(area) {
    A.api("/api/admin/media?page=1&page_size=100").then(function (data) {
      const items = data.items || [];
      const m = A.openModal({
        size: "wide",
        title: "انتخاب تصویر از رسانه",
        sub: "روی تصویر بزنید تا داخل محتوا درج شود.",
        body: items.length
          ? '<div class="media-grid">' + items.map(function (it) {
              return '<button type="button" class="media-card" data-pick-media data-url="' + A.esc(it.path) + '" data-alt="' + A.esc(it.alt_text || "") + '">' +
                '<span class="media-thumb"><img src="' + A.esc(A.safeImage(it.path)) + '" alt="' + A.esc(it.alt_text || "") + '" loading="lazy"></span>' +
                '<span class="media-meta"><b>' + A.esc(it.original_name || "") + "</b><span>" + A.esc(A.fmtBytes(it.size || 0)) + "</span></span></button>";
            }).join("") + "</div>"
          : A.emptyState("image", "تصویری وجود ندارد", "ابتدا از بخش رسانه تصویر بارگذاری کنید."),
        footer: '<button class="btn btn-ghost" data-modal-close>بستن</button>'
      });
      if (!m) return;
      m.root.querySelectorAll("[data-pick-media]").forEach(function (btn) {
        btn.addEventListener("click", function () {
          const url = btn.getAttribute("data-url") || "";
          const alt = btn.getAttribute("data-alt") || "";
          const html = '<img src="' + A.esc(url) + '" alt="' + A.esc(alt) + '">';
          area.focus();
          if (document.queryCommandSupported && document.queryCommandSupported("insertHTML")) document.execCommand("insertHTML", false, html);
          else area.innerHTML += html;
          A.closeModal();
          A.toast("تصویر درج شد.", "success");
        });
      });
    }).catch(function (e) { A.toast(e.message, "error"); });
  }

  function editorTabsHTML() {
    return '<div class="admin-tabs">' +
      '<button type="button" class="admin-tab active" data-etab="content">محتوا</button>' +
      '<button type="button" class="admin-tab" data-etab="display">نمایش</button>' +
      '<button type="button" class="admin-tab" data-etab="seo">سئو</button></div>';
  }

  function editorBodyHTML(item, kind, categories) {
    const isPost = kind === "post";
    const catOptions = '<option value="0">بدون دسته‌بندی</option>' + (categories || []).map(function (c) {
      return '<option value="' + c.id + '"' + (Number(item.category_id) === Number(c.id) ? " selected" : "") + ">" + A.esc(c.name) + "</option>";
    }).join("");
    const published = String(item.published_at || "").replace(" ", "T").slice(0, 16);
    const useCms = cmsReady();
    return '<form id="editorForm" class="editor-form">' +
      editorTabsHTML() +
      '<div data-epanel="content">' +
        '<div class="field"><label class="label">عنوان</label><input class="input" name="title" value="' + A.esc(item.title || "") + '" required></div>' +
        '<div class="field"><label class="label">نامک (slug)</label><input class="input mono" dir="ltr" name="slug" value="' + A.esc(item.slug || "") + '" placeholder="' + (isPost ? "post-slug" : "page-slug") + '"><div class="hint">حروف کوچک انگلیسی، عدد و خط تیره؛ خالی بگذارید تا خودکار ساخته شود.</div></div>' +
        (isPost ? '<div class="field"><label class="label">خلاصه</label><textarea class="textarea" name="summary" rows="3">' + A.esc(item.summary || "") + "</textarea></div>" : "") +
        '<div class="field"><label class="label">محتوا</label>' +
          '<div class="cms-editor">' +
            legacyToolbarHTML(useCms) +
            '<div class="rich-editor" id="edContent"' + (useCms ? "" : ' contenteditable="true"') + "></div>" +
            '<div class="hint">محتوا قبل از ذخیره با فیلتر امنیتی پاک‌سازی می‌شود.</div>' +
          "</div></div>" +
      "</div>" +
      '<div data-epanel="display" hidden>' +
        '<div class="form-grid three">' +
          '<div class="field"><label class="label">وضعیت</label><select class="input" name="status">' +
            optionsHTML([["draft", "پیش‌نویس"], ["published", "منتشرشده"]], item.status || "draft") + "</select></div>" +
          '<div class="field"><label class="label">عرض محتوا</label><select class="input" name="content_width">' + widthOptions(item.content_width) + "</select></div>" +
          '<div class="field"><label class="label">حالت نمایش</label><label class="switch" style="margin-top:8px"><input type="checkbox" name="has_sidebar"' + (item.has_sidebar ? " checked" : "") + '><span class="switch-track"></span><span class="switch-text">نمایش سایدبار</span></label></div>' +
        "</div>" +
        (isPost ? '<div class="form-grid three">' +
          '<div class="field"><label class="label">دسته‌بندی</label><select class="input" name="category_id">' + catOptions + "</select></div>" +
          '<div class="field"><label class="label">تاریخ انتشار</label><input class="input" type="datetime-local" name="published_at" value="' + A.esc(published) + '"></div>' +
          '<div class="field"><label class="label">زمان مطالعه (دقیقه)</label><input class="input" type="number" min="0" name="reading_time" value="' + Number(item.reading_time || 0) + '"></div>' +
        "</div>" +
        '<div class="field"><label class="label">تصویر شاخص</label><input class="input mono" dir="ltr" name="thumbnail" value="' + A.esc(item.thumbnail || "") + '" placeholder="/uploads/media/..."></div>' : "") +
      "</div>" +
      '<div data-epanel="seo" hidden>' +
        '<div class="form-grid">' +
          '<div class="field"><label class="label">عنوان متا</label><input class="input" name="meta_title" value="' + A.esc(item.meta_title || "") + '"></div>' +
          '<div class="field"><label class="label">توضیح متا</label><textarea class="textarea" name="meta_description" rows="3">' + A.esc(item.meta_description || "") + "</textarea></div>" +
          '<div class="field"><label class="label">آدرس کانونیکال</label><input class="input mono" dir="ltr" name="canonical_url" value="' + A.esc(item.canonical_url || "") + '"></div>' +
          '<div class="field"><label class="label">robots</label><select class="input" name="robots">' + robotsOptions(item.robots) + "</select></div>" +
          '<div class="field"><label class="label">عنوان Open Graph</label><input class="input" name="og_title" value="' + A.esc(item.og_title || "") + '"></div>' +
          '<div class="field"><label class="label">توضیح Open Graph</label><textarea class="textarea" name="og_description" rows="2">' + A.esc(item.og_description || "") + "</textarea></div>" +
          '<div class="field"><label class="label">تصویر Open Graph</label><input class="input mono" dir="ltr" name="og_image" value="' + A.esc(item.og_image || "") + '"></div>' +
        "</div>" +
      "</div>" +
    "</form>";
  }

  function editorPayload(modal, kind) {
    const q = function (s) { const el = modal.root.querySelector(s); return el ? el.value : ""; };
    const side = modal.root.querySelector('[name="has_sidebar"]');
    const body = {
      title: q('[name="title"]').trim(),
      slug: q('[name="slug"]').trim().toLowerCase(),
      content: cmsContent(modal),
      status: q('[name="status"]') || "draft",
      has_sidebar: side ? !!side.checked : false,
      content_width: q('[name="content_width"]') || "normal",
      meta_title: q('[name="meta_title"]'),
      meta_description: q('[name="meta_description"]'),
      canonical_url: q('[name="canonical_url"]'),
      robots: q('[name="robots"]'),
      og_title: q('[name="og_title"]'),
      og_description: q('[name="og_description"]'),
      og_image: q('[name="og_image"]')
    };
    if (kind === "post") {
      body.summary = q('[name="summary"]');
      body.thumbnail = q('[name="thumbnail"]');
      body.category_id = Number(q('[name="category_id"]') || 0);
      body.published_at = q('[name="published_at"]') || null;
      body.reading_time = Number(q('[name="reading_time"]') || 0);
    }
    return body;
  }

  async function openEditor(kind, pid) {
    const isPost = kind === "post";
    let item = null;
    let categories = state.content.categories || [];
    try {
      if (pid) item = (await A.api((isPost ? "/api/admin/posts/" : "/api/admin/pages/") + pid)).item;
      if (isPost && (!categories || !categories.length)) {
        categories = (await A.api("/api/admin/categories")).items || [];
        state.content.categories = categories;
      }
    } catch (e) {
      A.toast(e.message, "error");
      return;
    }
    const data = item || {
      title: "", slug: "", content: "", summary: "", status: "draft", has_sidebar: false, content_width: "normal",
      category_id: 0, thumbnail: "", published_at: "", reading_time: 0, meta_title: "", meta_description: "",
      canonical_url: "", robots: "", og_title: "", og_description: "", og_image: ""
    };
    const m = A.openModal({
      size: "wide",
      title: pid ? "ویرایش " + (isPost ? "مقاله" : "صفحه") : (isPost ? "مقاله جدید" : "صفحه جدید"),
      sub: isPost ? "محتوای آموزشی، دسته‌بندی و تنظیمات سئو" : "صفحه ثابت سایت و تنظیمات نمایش",
      body: editorBodyHTML(data, kind, categories),
      footer: '<button class="btn btn-ghost" data-modal-close>انصراف</button>' +
        '<button class="btn btn-primary" id="editorSaveBtn">' + A.icon("check") + " ذخیره</button>",
      onMount: function (modal) {
        const ed = modal.root.querySelector("#edContent");
        if (ed && !cmsReady()) ed.innerHTML = data.content || "";
        if (ed && cmsReady()) mountCmsEditor(modal, ed, data.content || "");
        else bindRichEditor(modal.root, ed);
        bindTabGroup(modal.root, "data-etab", "data-epanel");
        const save = modal.root.querySelector("#editorSaveBtn");
        if (save) save.addEventListener("click", function () { saveEditor(kind, pid, modal, save); });
      },
      onClose: function (modal) {
        const handle = modal._cms;
        modal._cms = null;
        modal._cmsPending = false;
        if (handle && typeof handle.destroy === "function") { try { handle.destroy(); } catch (e) {} }
      }
    });
    return m;
  }

  async function saveEditor(kind, pid, modal, btn) {
    if (modal._cmsPending) { A.toast("ویرایشگر هنوز آماده نشده است؛ چند لحظه بعد دوباره ذخیره کنید.", "error"); return; }
    const body = editorPayload(modal, kind);
    if (!body.title) { A.toast("عنوان را وارد کنید.", "error"); return; }
    const isPost = kind === "post";
    A.setBusy(btn, true, "در حال ذخیره…");
    try {
      const res = await A.api((isPost ? "/api/admin/posts" : "/api/admin/pages") + (pid ? "/" + pid : ""), {
        method: pid ? "PUT" : "POST", body: body
      });
      A.toast(res.message || "ذخیره شد.", "success");
      A.closeModal();
      if (routeName() === "pages" || routeName() === "education") refreshCurrent();
    } catch (e) {
      A.toast(e.message, "error");
      A.setBusy(btn, false);
    }
  }

  async function deleteContentItem(kind, pid, title) {
    const ok = await A.confirm({
      title: "حذف " + (kind === "post" ? "مقاله" : "صفحه"),
      message: "«" + (title || "") + "» حذف می‌شود. این عملیات قابل بازگشت نیست.",
      danger: true, confirmText: "حذف"
    });
    if (!ok) return;
    try {
      await A.api((kind === "post" ? "/api/admin/posts/" : "/api/admin/pages/") + pid, { method: "DELETE" });
      A.toast("حذف شد.", "success");
      refreshCurrent();
    } catch (e) {
      A.toast(e.message, "error");
    }
  }

  function bindSubmit(id, handler) {
    const form = A.$(id);
    if (!form) return;
    form.addEventListener("submit", function (e) {
      e.preventDefault();
      handler(form);
    });
  }

  /* ------------------------------------------------------------------
     صفحات ثابت
  ------------------------------------------------------------------ */
  async function renderPages(token) {
    const root = viewEl();
    root.innerHTML = loadingCard("در حال دریافت صفحات…");
    let data;
    try {
      data = await A.api("/api/admin/pages");
    } catch (e) {
      if (!alive(token)) return;
      root.innerHTML = errorCard(e.message, '<button class="btn btn-soft" data-act="refresh">' + A.icon("refresh") + " تلاش دوباره</button>");
      return;
    }
    if (!alive(token)) return;
    const items = data.items || [];
    const rows = items.map(function (p) {
      return '<tr>' +
        '<td><div class="cell-main">' + A.esc(p.title) + '</div><div class="cell-sub mono" dir="ltr">/p/' + A.esc(p.slug) + "</div></td>" +
        '<td>' + (p.status === "published" ? A.pill("منتشرشده", "green") : A.pill("پیش‌نویس", "amber")) + (p.is_system ? " " + A.pill("سیستمی", "blue") : "") + "</td>" +
        '<td class="tiny muted nowrap">' + A.esc(p.updated_at_fa || "-") + "</td>" +
        '<td><div class="cell-actions">' +
          '<a class="icon-btn sm" href="/p/' + A.esc(p.slug) + '" target="_blank" rel="noopener" title="نمایش">' + A.icon("external") + "</a>" +
          '<button class="icon-btn sm" data-act="page-edit" data-pid="' + p.id + '" title="ویرایش">' + A.icon("edit") + "</button>" +
          (p.is_system ? "" : '<button class="icon-btn sm danger" data-act="page-delete" data-pid="' + p.id + '" data-title="' + A.esc(p.title) + '" title="حذف">' + A.icon("trash") + "</button>") +
        "</div></td></tr>";
    }).join("");
    root.innerHTML =
      '<div class="page-head"><div><div class="eyebrow">' + A.icon("book") + " مدیریت محتوا</div>" +
      '<h1 class="page-title">صفحات ثابت</h1><p class="page-sub">صفحات درباره ما، قوانین و سایر محتوای ثابت سایت. صفحات سیستمی قابل حذف نیستند.</p></div>' +
      '<div class="page-actions"><button class="btn btn-primary" data-act="page-new">' + A.icon("plus") + " صفحه جدید</button></div></div>" +
      '<div class="card"><div class="card-head bordered"><div class="card-title">' + A.icon("book") + " فهرست صفحات</div>" +
        '<div class="card-sub">' + A.toFa(items.length) + " صفحه</div></div>" +
        (items.length ? '<div class="table-wrap"><table class="table table-fix"><thead><tr><th>صفحه</th><th>وضعیت</th><th>آخرین ویرایش</th><th></th></tr></thead><tbody>' + rows + "</tbody></table></div>"
          : A.emptyState("book", "صفحه‌ای وجود ندارد", "برای شروع یک صفحه جدید بسازید.", '<button class="btn btn-primary btn-sm" data-act="page-new">' + A.icon("plus") + " صفحه جدید</button>")) +
      "</div>";
  }

  /* ------------------------------------------------------------------
     آموزش (مقاله‌ها و دسته‌بندی‌ها)
  ------------------------------------------------------------------ */
  async function renderEducation(token) {
    const root = viewEl();
    root.innerHTML =
      '<div class="page-head"><div><div class="eyebrow">' + A.icon("layers") + " مدیریت محتوا</div>" +
      '<h1 class="page-title">آموزش و مقالات</h1><p class="page-sub">مقالات آموزشی، دسته‌بندی‌ها و آدرس‌های سئو.</p></div>' +
      '<div class="page-actions"><button class="btn btn-soft" data-act="categories-manage">' + A.icon("layers") + " دسته‌بندی‌ها</button>" +
      '<button class="btn btn-primary" data-act="post-new">' + A.icon("plus") + " مقاله جدید</button></div></div>" +
      '<form class="card filter-card" id="postsFilter"><div class="card-body"><div class="form-grid three">' +
        '<div class="field"><label class="label">جستجو</label><input class="input" name="q" value="' + A.esc(state.content.postsQ || "") + '" placeholder="عنوان یا نامک"></div>' +
        '<div class="field"><label class="label">وضعیت</label><select class="input" name="status">' + optionsHTML([["all", "همه"], ["published", "منتشرشده"], ["draft", "پیش‌نویس"]], state.content.postsStatus || "all") + "</select></div>" +
        '<div class="field"><label class="label">دسته‌بندی</label><select class="input" name="category" id="postsCategory"></select></div>' +
      "</div></div></form>" +
      '<div class="card"><div id="postsBox">' + loadingCard("در حال دریافت مقالات…") + '</div><div id="postsPager"></div></div>';
    bindSubmit("#postsFilter", function (form) {
      state.content.postsQ = form.elements.q.value.trim();
      state.content.postsStatus = form.elements.status.value;
      state.content.postsCategory = Number(form.elements.category.value || 0);
      state.content.postsPage = 1;
      loadPosts(token);
    });
    const catSel = A.$("#postsCategory");
    if (catSel) catSel.addEventListener("change", function () {
      state.content.postsCategory = Number(catSel.value || 0);
      state.content.postsPage = 1;
      loadPosts(token);
    });
    await loadPosts(token);
  }

  async function loadPosts(token) {
    const box = A.$("#postsBox");
    if (!box) return;
    box.innerHTML = loadingCard("در حال دریافت مقالات…");
    let data;
    try {
      data = await A.api("/api/admin/posts?q=" + encodeURIComponent(state.content.postsQ || "") +
        "&status=" + encodeURIComponent(state.content.postsStatus || "all") +
        "&category=" + Number(state.content.postsCategory || 0) +
        "&page=" + Number(state.content.postsPage || 1) + "&page_size=20");
    } catch (e) {
      if (!alive(token)) return;
      box.innerHTML = A.emptyState("alert", "دریافت مقالات انجام نشد", e.message);
      return;
    }
    if (!alive(token) || !A.$("#postsBox")) return;
    const items = data.items || [];
    state.content.categories = data.categories || [];
    const catSel = A.$("#postsCategory");
    if (catSel) catSel.innerHTML = '<option value="0">همه دسته‌ها</option>' + (data.categories || []).map(function (c) {
      return '<option value="' + c.id + '"' + (Number(state.content.postsCategory) === Number(c.id) ? " selected" : "") + ">" + A.esc(c.name) + " (" + A.toFa(c.posts || 0) + ")</option>";
    }).join("");
    if (!items.length) {
      box.innerHTML = A.emptyState("layers", "مقاله‌ای یافت نشد", "فیلترها را تغییر دهید یا مقاله جدید بسازید.",
        '<button class="btn btn-primary btn-sm" data-act="post-new">' + A.icon("plus") + " مقاله جدید</button>");
    } else {
      const rows = items.map(function (p) {
        return '<tr>' +
          '<td><div class="cell-main">' + A.esc(p.title) + '</div><div class="cell-sub mono" dir="ltr">/education/' + A.esc(p.slug) + "</div></td>" +
          "<td>" + A.esc(p.category_name || "بدون دسته") + "</td>" +
          "<td>" + (p.status === "published" ? A.pill("منتشرشده", "green") : A.pill("پیش‌نویس", "amber")) + "</td>" +
          '<td class="tiny muted nowrap">' + A.esc(p.published_at_fa || "-") + "</td>" +
          '<td class="num">' + A.toFa(p.views || 0) + "</td>" +
          '<td><div class="cell-actions">' +
            '<a class="icon-btn sm" href="/education/' + A.esc(p.slug) + '" target="_blank" rel="noopener" title="نمایش">' + A.icon("external") + "</a>" +
            '<button class="icon-btn sm" data-act="post-edit" data-pid="' + p.id + '" title="ویرایش">' + A.icon("edit") + "</button>" +
            '<button class="icon-btn sm danger" data-act="post-delete" data-pid="' + p.id + '" data-title="' + A.esc(p.title) + '" title="حذف">' + A.icon("trash") + "</button>" +
          "</div></td></tr>";
      }).join("");
      box.innerHTML = '<div class="table-wrap"><table class="table table-fix"><thead><tr><th>مقاله</th><th>دسته</th><th>وضعیت</th><th>انتشار</th><th>بازدید</th><th></th></tr></thead><tbody>' + rows + "</tbody></table></div>";
    }
    const pager = A.$("#postsPager");
    if (pager) pager.innerHTML = pagerHTML(state.content.postsPage, Math.ceil((data.total || 0) / 20));
  }

  async function openCategoryManager() {
    let cats = [];
    try {
      cats = (await A.api("/api/admin/categories")).items || [];
    } catch (e) {
      A.toast(e.message, "error");
      return;
    }
    const m = A.openModal({
      size: "wide",
      title: "دسته‌بندی‌های آموزش",
      sub: "دسته‌بندی‌ها در فهرست مقالات و منوها استفاده می‌شوند.",
      body: "",
      footer: '<button class="btn btn-ghost" data-modal-close>بستن</button>'
    });
    if (!m) return;
    function draw() {
      m.body.innerHTML =
        '<form id="catAddForm" class="inline-form" style="margin-bottom:14px">' +
          '<input class="input" name="name" placeholder="نام دسته‌بندی" required>' +
          '<input class="input mono" dir="ltr" name="slug" placeholder="slug (اختیاری)">' +
          '<button class="btn btn-primary btn-sm" type="submit">' + A.icon("plus") + " افزودن</button></form>" +
        (cats.length ? '<div class="list">' + cats.map(function (c) {
          return '<div class="list-item"><div class="list-icon blue">' + A.icon("layers") + '</div>' +
            '<div class="list-body"><div class="list-title">' + A.esc(c.name) + '</div><div class="list-sub mono" dir="ltr">' + A.esc(c.slug) + " • " + A.toFa(c.posts || 0) + " مقاله</div></div>" +
            '<div class="list-side"><button class="icon-btn sm" data-cat-edit="' + c.id + '" data-name="' + A.esc(c.name) + '" title="ویرایش">' + A.icon("edit") + "</button>" +
            '<button class="icon-btn sm danger" data-cat-del="' + c.id + '" data-name="' + A.esc(c.name) + '" title="حذف">' + A.icon("trash") + "</button></div></div>";
        }).join("") + "</div>" : A.emptyState("layers", "دسته‌بندی‌ای وجود ندارد", "اولین دسته‌بندی را بسازید."));
      m.body.querySelector("#catAddForm").addEventListener("submit", async function (e) {
        e.preventDefault();
        const form = e.target;
        const name = form.elements.name.value.trim();
        if (!name) return;
        try {
          await A.api("/api/admin/categories", { method: "POST", body: { name: name, slug: form.elements.slug.value.trim().toLowerCase() } });
          A.toast("دسته‌بندی ساخته شد.", "success");
          cats = (await A.api("/api/admin/categories")).items || [];
          state.content.categories = cats;
          draw();
        } catch (err) { A.toast(err.message, "error"); }
      });
      m.body.querySelectorAll("[data-cat-edit]").forEach(function (b) {
        b.addEventListener("click", async function () {
          const name = await A.prompt({ title: "ویرایش دسته‌بندی", label: "نام جدید", value: b.getAttribute("data-name") || "", confirmText: "ذخیره" });
          if (name === null || !name.trim()) return;
          try {
            await A.api("/api/admin/categories/" + b.getAttribute("data-cat-edit"), { method: "PUT", body: { name: name.trim() } });
            A.toast("دسته‌بندی ذخیره شد.", "success");
            cats = (await A.api("/api/admin/categories")).items || [];
            state.content.categories = cats;
            draw();
          } catch (err) { A.toast(err.message, "error"); }
        });
      });
      m.body.querySelectorAll("[data-cat-del]").forEach(function (b) {
        b.addEventListener("click", async function () {
          const ok = await A.confirm({ title: "حذف دسته‌بندی", message: "دسته‌بندی «" + (b.getAttribute("data-name") || "") + "» حذف می‌شود؛ مقالات بدون دسته خواهند شد.", danger: true, confirmText: "حذف" });
          if (!ok) return;
          try {
            await A.api("/api/admin/categories/" + b.getAttribute("data-cat-del"), { method: "DELETE" });
            A.toast("دسته‌بندی حذف شد.", "success");
            cats = (await A.api("/api/admin/categories")).items || [];
            state.content.categories = cats;
            draw();
            if (routeName() === "education") loadPosts(viewToken);
          } catch (err) { A.toast(err.message, "error"); }
        });
      });
    }
    draw();
  }

  /* ------------------------------------------------------------------
     منوها
  ------------------------------------------------------------------ */
  async function renderMenus(token) {
    const root = viewEl();
    root.innerHTML = loadingCard("در حال دریافت منوها…");
    let data;
    try {
      data = await A.api("/api/admin/menus");
    } catch (e) {
      if (!alive(token)) return;
      root.innerHTML = errorCard(e.message, '<button class="btn btn-soft" data-act="refresh">' + A.icon("refresh") + " تلاش دوباره</button>");
      return;
    }
    if (!alive(token)) return;
    const menus = data.items || [];
    const details = {};
    await Promise.all(menus.map(async function (m) {
      try { details[m.id] = (await A.api("/api/admin/menus/" + m.id)).item; } catch (e) { details[m.id] = null; }
    }));
    if (!alive(token)) return;

    function menuCard(location, label, icon) {
      const menu = menus.find(function (m) { return m.location === location; });
      if (!menu) {
        return '<div class="card"><div class="card-head"><div><div class="card-title">' + A.icon(icon) + " " + label + "</div>" +
          '<div class="card-sub">هنوز منویی برای این مکان ساخته نشده است.</div></div></div>' +
          '<div class="card-body"><button class="btn btn-primary btn-sm" data-act="menu-create" data-loc="' + location + '">' + A.icon("plus") + " ساخت منوی " + label + "</button></div></div>";
      }
      const info = details[menu.id] || { items: [] };
      const items = info.items || [];
      const roots = items.filter(function (i) { return !Number(i.parent_id || 0); });
      const list = roots.length ? '<div class="menu-preview">' + roots.map(function (it) {
        const children = items.filter(function (x) { return Number(x.parent_id) === Number(it.id); });
        return '<div class="menu-preview-item"><span class="menu-preview-title">' + A.esc(it.title || it.resolved_title || "بدون عنوان") + "</span>" +
          (it.is_active ? "" : " " + A.pill("غیرفعال", "gray")) +
          (children.length ? '<div class="menu-preview-sub">' + children.map(function (c) { return A.esc(c.title || c.resolved_title || ""); }).join(" • ") + "</div>" : "") + "</div>";
      }).join("") + "</div>" : '<div class="hint">آیتمی در این منو نیست.</div>';
      return '<div class="card"><div class="card-head"><div><div class="card-title">' + A.icon(icon) + " " + A.esc(menu.name || label) + "</div>" +
        '<div class="card-sub">' + A.toFa(items.length) + " آیتم • مکان: " + label + "</div></div>" +
        '<div class="row wrap">' +
        '<button class="btn btn-soft btn-sm" data-act="menu-manage" data-mid="' + menu.id + '">' + A.icon("settings") + " مدیریت</button>" +
        '<button class="icon-btn sm danger" data-act="menu-delete" data-mid="' + menu.id + '" data-name="' + A.esc(menu.name || label) + '" title="حذف منو">' + A.icon("trash") + "</button>" +
        "</div></div>" +
        '<div class="card-body">' + list + "</div></div>";
    }

    root.innerHTML =
      '<div class="page-head"><div><div class="eyebrow">' + A.icon("menu") + " مدیریت محتوا</div>" +
      '<h1 class="page-title">منوهای سایت</h1><p class="page-sub">منوی هدر و پابرگ سایت عمومی را بسازید و مرتب کنید.</p></div>' +
      '<div class="page-actions"><button class="btn btn-soft" data-act="refresh">' + A.icon("refresh") + " به‌روزرسانی</button></div></div>" +
      '<div class="grid grid-main">' + menuCard("header", "هدر", "menu") + menuCard("footer", "پابرگ", "layers") + "</div>";
  }

  async function createMenu(location) {
    const label = location === "footer" ? "پابرگ" : "هدر";
    const name = await A.prompt({ title: "ساخت منو", label: "نام منوی " + label, value: "منوی " + label, confirmText: "ساخت" });
    if (name === null || !name.trim()) return;
    try {
      const res = await A.api("/api/admin/menus", { method: "POST", body: { name: name.trim(), location: location } });
      A.toast(res.message || "منو ساخته شد.", "success");
      openMenuManager(res.id);
    } catch (e) { A.toast(e.message, "error"); }
  }

  async function deleteMenu(mid, name) {
    if (!mid) return;
    const ok = await A.confirm({
      title: "حذف منو",
      message: "منوی «" + (name || "") + "» به همراه همه آیتم‌های آن حذف می‌شود. این عملیات قابل بازگشت نیست.",
      danger: true,
      confirmText: "حذف منو"
    });
    if (!ok) return;
    try {
      const res = await A.api("/api/admin/menus/" + mid, { method: "DELETE" });
      A.toast(res.message || "منو حذف شد.", "success");
      A.closeModal();
      if (routeName() === "menus") refreshCurrent();
    } catch (e) { A.toast(e.message, "error"); }
  }

  async function openMenuManager(mid) {
    let menu;
    try {
      menu = (await A.api("/api/admin/menus/" + mid)).item;
    } catch (e) {
      A.toast(e.message, "error");
      return;
    }
    const m = A.openModal({
      size: "wide",
      title: "مدیریت " + A.esc(menu.name || "منو"),
      sub: "مکان: " + (menu.location === "footer" ? "پابرگ" : "هدر"),
      body: "",
      footer: '<button class="btn btn-ghost" data-modal-close>بستن</button>' +
        '<button class="btn btn-danger" data-act="menu-delete" data-mid="' + mid + '" data-name="' + A.esc(menu.name || "منو") + '">' + A.icon("trash") + " حذف منو</button>" +
        '<button class="btn btn-primary" id="menuAddRoot">' + A.icon("plus") + " آیتم جدید</button>",
      onMount: function (modal) {
        const addRoot = modal.root.querySelector("#menuAddRoot");
        if (addRoot) addRoot.addEventListener("click", function () { openMenuItemForm(mid, 0, null, 0, menu.items || [], reload); });
      }
    });
    if (!m) return;

    function sorted(list) {
      return list.slice().sort(function (a, b) { return (Number(a.sort_order) - Number(b.sort_order)) || (Number(a.id) - Number(b.id)); });
    }

    function draw() {
      const items = menu.items || [];
      const byParent = {};
      items.forEach(function (it) { const p = Number(it.parent_id || 0); (byParent[p] = byParent[p] || []).push(it); });
      Object.keys(byParent).forEach(function (k) { byParent[k] = sorted(byParent[k]); });

      function rows(parent, depth) {
        return (byParent[parent] || []).map(function (it) {
          const kids = byParent[it.id] || [];
          return '<div class="menu-row" data-menu-row="' + it.id + '" draggable="true" style="--depth:' + depth + '">' +
            '<div class="menu-row-main"><span class="menu-drag">' + "↕" + "</span>" +
              '<div class="grow"><div class="menu-row-title">' + A.esc(it.title || it.resolved_title || "بدون عنوان") + "</div>" +
              '<div class="menu-row-sub"><span class="mono" dir="ltr">' + A.esc(it.href || "-") + "</span></div></div>" +
              typeBadge(it.type) + " " + (it.is_active ? A.pill("فعال", "green") : A.pill("غیرفعال", "gray")) +
            "</div>" +
            '<div class="cell-actions">' +
              '<button class="icon-btn sm" data-mi-up="' + it.id + '" title="بالا">' + A.icon("arrow-up-left") + "</button>" +
              '<button class="icon-btn sm" data-mi-down="' + it.id + '" title="پایین">' + A.icon("chevron-down") + "</button>" +
              '<button class="icon-btn sm" data-mi-child="' + it.id + '" title="زیرمنو">' + A.icon("plus") + "</button>" +
              '<button class="icon-btn sm" data-mi-edit="' + it.id + '" title="ویرایش">' + A.icon("edit") + "</button>" +
              '<button class="icon-btn sm danger" data-mi-del="' + it.id + '" title="حذف">' + A.icon("trash") + "</button>" +
            "</div>" + rows(it.id, depth + 1) + "</div>";
        }).join("");
      }

      m.body.innerHTML =
        '<form id="menuNameForm" class="inline-form" style="margin-bottom:14px">' +
          '<input class="input" name="name" value="' + A.esc(menu.name || "") + '" placeholder="نام منو">' +
          '<button class="btn btn-soft btn-sm" type="submit">' + A.icon("check") + " ذخیره نام</button></form>" +
        (items.length ? '<div class="menu-tree">' + rows(0, 0) + "</div>" : A.emptyState("menu", "آیتمی وجود ندارد", "اولین آیتم منو را اضافه کنید."));

      m.body.querySelector("#menuNameForm").addEventListener("submit", async function (e) {
        e.preventDefault();
        const name = e.target.elements.name.value.trim();
        if (!name) { A.toast("نام منو را وارد کنید.", "error"); return; }
        try {
          await A.api("/api/admin/menus/" + mid, { method: "PUT", body: { name: name, location: menu.location } });
          menu.name = name;
          A.toast("نام منو ذخیره شد.", "success");
        } catch (err) { A.toast(err.message, "error"); }
      });
      m.body.querySelectorAll("[data-mi-up]").forEach(function (b) { b.addEventListener("click", function () { moveMenuItem(mid, Number(b.getAttribute("data-mi-up")), -1, menu.items, reload); }); });
      m.body.querySelectorAll("[data-mi-down]").forEach(function (b) { b.addEventListener("click", function () { moveMenuItem(mid, Number(b.getAttribute("data-mi-down")), 1, menu.items, reload); }); });
      m.body.querySelectorAll("[data-mi-child]").forEach(function (b) {
        b.addEventListener("click", function () { openMenuItemForm(mid, 0, null, Number(b.getAttribute("data-mi-child")), menu.items, reload); });
      });
      m.body.querySelectorAll("[data-mi-edit]").forEach(function (b) {
        b.addEventListener("click", function () {
          const it = (menu.items || []).find(function (x) { return Number(x.id) === Number(b.getAttribute("data-mi-edit")); });
          if (it) openMenuItemForm(mid, it.id, it, it.parent_id || 0, menu.items, reload);
        });
      });
      m.body.querySelectorAll("[data-mi-del]").forEach(function (b) {
        b.addEventListener("click", async function () {
          const ok = await A.confirm({ title: "حذف آیتم منو", message: "این آیتم از منو حذف می‌شود.", danger: true, confirmText: "حذف" });
          if (!ok) return;
          try {
            await A.api("/api/admin/menu-items/" + b.getAttribute("data-mi-del"), { method: "DELETE" });
            A.toast("آیتم حذف شد.", "success");
            reload();
          } catch (err) { A.toast(err.message, "error"); }
        });
      });

      let dragId = 0;
      m.body.querySelectorAll("[data-menu-row]").forEach(function (row) {
        row.addEventListener("dragstart", function (e) {
          dragId = Number(row.getAttribute("data-menu-row"));
          row.classList.add("dragging");
          if (e.dataTransfer) { e.dataTransfer.effectAllowed = "move"; e.dataTransfer.setData("text/plain", String(dragId)); }
        });
        row.addEventListener("dragend", function () {
          row.classList.remove("dragging");
          m.body.querySelectorAll(".drag-over").forEach(function (el) { el.classList.remove("drag-over"); });
        });
        row.addEventListener("dragover", function (e) {
          if (!dragId || Number(row.getAttribute("data-menu-row")) === dragId) return;
          e.preventDefault();
          row.classList.add("drag-over");
        });
        row.addEventListener("dragleave", function () { row.classList.remove("drag-over"); });
        row.addEventListener("drop", function (e) {
          e.preventDefault();
          row.classList.remove("drag-over");
          dropMenuItem(mid, dragId, Number(row.getAttribute("data-menu-row")), menu.items, reload);
          dragId = 0;
        });
      });
    }

    async function reload() {
      try {
        menu = (await A.api("/api/admin/menus/" + mid)).item;
      } catch (e) { A.toast(e.message, "error"); return; }
      draw();
    }

    draw();
  }

  async function moveMenuItem(mid, iid, dir, items, reload) {
    const current = (items || []).find(function (i) { return Number(i.id) === Number(iid); });
    if (!current) return;
    const siblings = (items || []).filter(function (i) { return Number(i.parent_id || 0) === Number(current.parent_id || 0); })
      .sort(function (a, b) { return (Number(a.sort_order) - Number(b.sort_order)) || (Number(a.id) - Number(b.id)); });
    const index = siblings.findIndex(function (i) { return Number(i.id) === Number(iid); });
    const target = siblings[index + dir];
    if (!target) return;
    siblings[index] = target;
    siblings[index + dir] = current;
    siblings.forEach(function (it, k) { it.sort_order = k; });
    try {
      await A.api("/api/admin/menus/" + mid + "/reorder", {
        method: "POST",
        body: { items: (items || []).map(function (i) { return { id: Number(i.id), parent_id: Number(i.parent_id || 0), sort_order: Number(i.sort_order || 0) }; }) }
      });
      if (typeof reload === "function") reload();
      else openMenuManager(mid);
    } catch (e) { A.toast(e.message, "error"); }
  }

  async function dropMenuItem(mid, dragId, targetId, items, reload) {
    if (!dragId || !targetId || Number(dragId) === Number(targetId)) return;
    const drag = (items || []).find(function (i) { return Number(i.id) === Number(dragId); });
    const target = (items || []).find(function (i) { return Number(i.id) === Number(targetId); });
    if (!drag || !target) return;
    const newParent = Number(target.parent_id || 0);
    let cur = newParent, seen = {};
    while (cur) {
      if (cur === Number(dragId)) { A.toast("این جابه‌جایی باعث حلقه در منو می‌شود.", "error"); return; }
      if (seen[cur]) break;
      seen[cur] = 1;
      const row = (items || []).find(function (x) { return Number(x.id) === cur; });
      cur = row ? Number(row.parent_id || 0) : 0;
    }
    const siblings = (items || []).filter(function (i) {
      return Number(i.parent_id || 0) === newParent && Number(i.id) !== Number(dragId);
    }).sort(function (a, b) { return (Number(a.sort_order) - Number(b.sort_order)) || (Number(a.id) - Number(b.id)); });
    const at = siblings.findIndex(function (i) { return Number(i.id) === Number(targetId); });
    drag.parent_id = newParent;
    siblings.splice(at < 0 ? siblings.length : at, 0, drag);
    siblings.forEach(function (it, k) { it.sort_order = k; });
    try {
      await A.api("/api/admin/menus/" + mid + "/reorder", {
        method: "POST",
        body: { items: (items || []).map(function (i) { return { id: Number(i.id), parent_id: Number(i.parent_id || 0), sort_order: Number(i.sort_order || 0) }; }) }
      });
      if (typeof reload === "function") reload();
    } catch (e) { A.toast(e.message, "error"); }
  }

  async function openMenuItemForm(mid, iid, item, parentId, menuItems, onSaved) {
    let pages = [], cats = [], posts = [];
    try {
      const res = await Promise.all([
        A.api("/api/admin/pages"),
        A.api("/api/admin/categories"),
        A.api("/api/admin/posts?page=1&page_size=100")
      ]);
      pages = res[0].items || [];
      cats = res[1].items || [];
      posts = res[2].items || [];
    } catch (e) {
      A.toast(e.message, "error");
      return;
    }
    const data = item || { title: "", type: "custom_link", reference_id: 0, url: "", parent_id: parentId || 0, is_active: true };
    const parentOptions = '<option value="0">سطح اول</option>' + (menuItems || []).filter(function (x) {
      return !iid || Number(x.id) !== Number(iid);
    }).map(function (x) {
      return '<option value="' + x.id + '"' + (Number(data.parent_id) === Number(x.id) ? " selected" : "") + ">" + A.esc(x.title || x.resolved_title || "بدون عنوان") + "</option>";
    }).join("");
    const m = A.openModal({
      size: "normal",
      title: iid ? "ویرایش آیتم منو" : "آیتم جدید منو",
      body: '<form id="menuItemForm">' +
        '<div class="field"><label class="label">عنوان</label><input class="input" name="title" value="' + A.esc(data.title || "") + '"></div>' +
        '<div class="form-grid two">' +
          '<div class="field"><label class="label">نوع</label><select class="input" name="type">' +
            optionsHTML([["custom_link", "لینک دلخواه"], ["page", "صفحه"], ["post", "مقاله"], ["category", "دسته‌بندی"]], data.type) + "</select></div>" +
          '<div class="field"><label class="label">والد</label><select class="input" name="parent_id">' + parentOptions + "</select></div>" +
        "</div>" +
        '<div class="field" id="menuRefField"><label class="label">مورد مرجع</label><select class="input" name="reference_id"></select></div>' +
        '<div class="field" id="menuUrlField"><label class="label">آدرس لینک</label><input class="input mono" dir="ltr" name="url" value="' + A.esc(data.url || "") + '" placeholder="/education یا https://..."></div>' +
        '<label class="switch"><input type="checkbox" name="is_active"' + (data.is_active ? " checked" : "") + '><span class="switch-track"></span><span class="switch-text">نمایش در منو</span></label>' +
      "</form>",
      footer: '<button class="btn btn-ghost" data-modal-close>انصراف</button><button class="btn btn-primary" id="menuItemSave">' + A.icon("check") + " ذخیره</button>",
      onMount: function (modal) {
        const form = modal.root.querySelector("#menuItemForm");
        const typeSel = form.elements.type;
        const refSel = form.elements.reference_id;
        const refField = modal.root.querySelector("#menuRefField");
        const urlField = modal.root.querySelector("#menuUrlField");
        function fillRefs() {
          const type = typeSel.value;
          refField.hidden = type === "custom_link";
          urlField.hidden = type !== "custom_link";
          let list = [];
          if (type === "page") list = pages.map(function (p) { return [p.id, p.title]; });
          else if (type === "post") list = posts.map(function (p) { return [p.id, p.title]; });
          else if (type === "category") list = cats.map(function (c) { return [c.id, c.name]; });
          refSel.innerHTML = list.length ? optionsHTML(list, data.reference_id) : '<option value="0">موردی وجود ندارد</option>';
        }
        typeSel.addEventListener("change", fillRefs);
        fillRefs();
        modal.root.querySelector("#menuItemSave").addEventListener("click", async function () {
          const btn = modal.root.querySelector("#menuItemSave");
          const body = {
            menu_id: Number(mid),
            title: form.elements.title.value.trim(),
            type: typeSel.value,
            reference_id: Number(refSel.value || 0),
            url: form.elements.url.value.trim(),
            parent_id: Number(form.elements.parent_id.value || 0),
            is_active: !!form.elements.is_active.checked
          };
          if (!body.title) { A.toast("عنوان آیتم را وارد کنید.", "error"); return; }
          A.setBusy(btn, true, "در حال ذخیره…");
          try {
            const res = await A.api("/api/admin/menu-items" + (iid ? "/" + iid : ""), { method: iid ? "PUT" : "POST", body: body });
            A.toast(res.message || "ذخیره شد.", "success");
            A.closeModal();
            if (typeof onSaved === "function") onSaved();
            else openMenuManager(mid);
          } catch (e) {
            A.toast(e.message, "error");
            A.setBusy(btn, false);
          }
        });
      }
    });
    return m;
  }

  /* ------------------------------------------------------------------
     کتابخانه رسانه
  ------------------------------------------------------------------ */
  async function renderMedia(token) {
    const root = viewEl();
    root.innerHTML =
      '<div class="page-head"><div><div class="eyebrow">' + A.icon("image") + " مدیریت محتوا</div>" +
      '<h1 class="page-title">کتابخانه رسانه</h1><p class="page-sub">تصاویر سایت را بارگذاری، عنوان‌گذاری و مدیریت کنید.</p></div>' +
      '<div class="page-actions"><button class="btn btn-soft" data-act="media-refresh">' + A.icon("refresh") + " به‌روزرسانی</button></div></div>" +
      '<div class="card"><div class="card-body">' +
        '<form id="mediaSearch" class="inline-form"><input class="input" name="q" value="' + A.esc(state.media.q || "") + '" placeholder="جستجوی نام یا عنوان تصویر">' +
          '<button class="btn btn-soft btn-sm" type="submit">' + A.icon("search") + " جستجو</button></form>" +
        '<div class="upload-zone" id="mediaDrop"><div class="upload-zone-icon">' + A.icon("download") + "</div>" +
          '<div><b>بارگذاری تصویر جدید</b><div class="hint">JPG، PNG، WebP، GIF یا SVG — حداکثر ۵ مگابایت</div></div>' +
          '<input type="file" id="mediaFile" accept=".jpg,.jpeg,.png,.webp,.gif,.svg,image/*" hidden>' +
          '<button class="btn btn-primary btn-sm" type="button" data-act="media-pick">' + A.icon("plus") + " انتخاب فایل</button>" +
        "</div>" +
        '<div class="form-grid" style="margin-top:10px"><div class="field"><label class="label">متن جایگزین (alt)</label><input class="input" id="mediaAlt" placeholder="توضیح تصویر"></div></div>' +
        '<div class="row" style="justify-content:flex-end;margin-top:8px"><button class="btn btn-primary btn-sm" type="button" data-act="media-upload">' + A.icon("check") + " بارگذاری</button></div>" +
      "</div></div>" +
      '<div class="card"><div class="card-head bordered"><div class="card-title">' + A.icon("image") + ' تصاویر</div><div class="card-sub" id="mediaCount"></div></div>' +
        '<div id="mediaBox">' + loadingCard("در حال دریافت رسانه…") + '</div><div id="mediaPager"></div></div>';
    bindSubmit("#mediaSearch", function (form) {
      state.media.q = form.elements.q.value.trim();
      state.media.page = 1;
      loadMedia(token);
    });
    const file = A.$("#mediaFile");
    if (file) file.addEventListener("change", function () {
      if (file.files && file.files[0]) A.toast("فایل انتخاب شد: " + file.files[0].name, "info", 2200);
    });
    await loadMedia(token);
  }

  async function loadMedia(token) {
    const box = A.$("#mediaBox");
    if (!box) return;
    box.innerHTML = loadingCard("در حال دریافت رسانه…");
    let data;
    try {
      data = await A.api("/api/admin/media?q=" + encodeURIComponent(state.media.q || "") + "&page=" + Number(state.media.page || 1) + "&page_size=24");
    } catch (e) {
      if (!alive(token)) return;
      box.innerHTML = A.emptyState("alert", "دریافت رسانه انجام نشد", e.message);
      return;
    }
    if (!alive(token) || !A.$("#mediaBox")) return;
    const items = data.items || [];
    state.media.items = items;
    state.media.total = data.total || 0;
    const count = A.$("#mediaCount");
    if (count) count.textContent = A.toFa(data.total || 0) + " فایل";
    if (!items.length) {
      box.innerHTML = A.emptyState("image", "تصویری وجود ندارد", "اولین تصویر را بارگذاری کنید.");
    } else {
      box.innerHTML = '<div class="media-grid">' + items.map(function (it) {
        return '<div class="media-card admin-media-card">' +
          '<span class="media-thumb"><img src="' + A.esc(A.safeImage(it.path)) + '" alt="' + A.esc(it.alt_text || "") + '" loading="lazy"></span>' +
          '<div class="media-meta"><b title="' + A.esc(it.original_name || "") + '">' + A.esc(clip(it.original_name || "", 28)) + "</b>" +
            "<span>" + A.esc(A.fmtBytes(it.size || 0)) + " • " + A.esc(it.created_at_fa || "") + "</span>" +
            (it.alt_text ? '<span class="tiny muted">' + A.esc(clip(it.alt_text, 40)) + "</span>" : "") + "</div>" +
          '<div class="media-actions">' +
            '<button class="icon-btn sm" data-act="media-copy" data-path="' + A.esc(it.path) + '" title="کپی آدرس">' + A.icon("copy") + "</button>" +
            '<button class="icon-btn sm" data-act="media-edit" data-mid="' + it.id + '" title="اطلاعات">' + A.icon("edit") + "</button>" +
            '<button class="icon-btn sm danger" data-act="media-delete" data-mid="' + it.id + '" data-name="' + A.esc(it.original_name || "") + '" title="حذف">' + A.icon("trash") + "</button>" +
          "</div></div>";
      }).join("") + "</div>";
    }
    const pager = A.$("#mediaPager");
    if (pager) pager.innerHTML = pagerHTML(state.media.page, Math.ceil((data.total || 0) / 24));
  }

  async function uploadMedia() {
    const input = A.$("#mediaFile");
    const f = input && input.files && input.files[0];
    if (!f) { A.toast("فایل تصویر را انتخاب کنید.", "error"); return; }
    const fd = new FormData();
    fd.append("file", f);
    fd.append("alt_text", (A.$("#mediaAlt") || {}).value || "");
    fd.append("title", f.name);
    try {
      const res = await A.api("/api/admin/media/upload", { method: "POST", body: fd });
      A.toast(res.message || "فایل بارگذاری شد.", "success");
      if (input) input.value = "";
      const alt = A.$("#mediaAlt");
      if (alt) alt.value = "";
      state.media.page = 1;
      loadMedia(viewToken);
    } catch (e) { A.toast(e.message, "error"); }
  }

  async function openMediaMeta(mid) {
    const it = (state.media.items || []).find(function (x) { return Number(x.id) === Number(mid); });
    if (!it) { A.toast("رسانه پیدا نشد.", "error"); return; }
    A.openModal({
      size: "normal",
      title: "اطلاعات رسانه",
      sub: A.esc(it.original_name || ""),
      body: '<form id="mediaMetaForm">' +
        '<div class="field"><label class="label">متن جایگزین (alt)</label><input class="input" name="alt_text" value="' + A.esc(it.alt_text || "") + '"></div>' +
        '<div class="field"><label class="label">عنوان</label><input class="input" name="title" value="' + A.esc(it.title || "") + '"></div>' +
        '<div class="field"><label class="label">آدرس</label><div class="copy-row"><input class="input mono" dir="ltr" readonly value="' + A.esc(it.path) + '">' +
          '<button class="icon-btn sm" type="button" data-copy-media="' + A.esc(it.path) + '" title="کپی">' + A.icon("copy") + "</button></div></div>" +
      "</form>",
      footer: '<button class="btn btn-ghost" data-modal-close>انصراف</button><button class="btn btn-primary" id="mediaMetaSave">' + A.icon("check") + " ذخیره</button>",
      onMount: function (modal) {
        const copy = modal.root.querySelector("[data-copy-media]");
        if (copy) copy.addEventListener("click", function () { A.copy(location.origin + (copy.getAttribute("data-copy-media") || "")); });
        modal.root.querySelector("#mediaMetaSave").addEventListener("click", async function () {
          const form = modal.root.querySelector("#mediaMetaForm");
          try {
            const res = await A.api("/api/admin/media/" + mid, { method: "PUT", body: { alt_text: form.elements.alt_text.value, title: form.elements.title.value } });
            A.toast(res.message || "ذخیره شد.", "success");
            A.closeModal();
            loadMedia(viewToken);
          } catch (e) { A.toast(e.message, "error"); }
        });
      }
    });
  }

  async function deleteMedia(mid, name) {
    const ok = await A.confirm({ title: "حذف رسانه", message: "فایل «" + (name || "") + "» حذف می‌شود. اگر در محتوا استفاده شده باشد، تصویر نمایش داده نخواهد شد.", danger: true, confirmText: "حذف" });
    if (!ok) return;
    try {
      await A.api("/api/admin/media/" + mid, { method: "DELETE" });
      A.toast("رسانه حذف شد.", "success");
      loadMedia(viewToken);
    } catch (e) { A.toast(e.message, "error"); }
  }

  /* ------------------------------------------------------------------
     سئو
  ------------------------------------------------------------------ */
  async function renderSeo(token) {
    const root = viewEl();
    root.innerHTML = loadingCard("در حال دریافت تنظیمات سئو…");
    let data;
    try {
      data = await A.api("/api/admin/seo");
    } catch (e) {
      if (!alive(token)) return;
      root.innerHTML = errorCard(e.message, '<button class="btn btn-soft" data-act="refresh">' + A.icon("refresh") + " تلاش دوباره</button>");
      return;
    }
    if (!alive(token)) return;
    const s = data.settings || {};
    const defaults = data.defaults || {};
    root.innerHTML =
      '<div class="page-head"><div><div class="eyebrow">' + A.icon("search") + " بهینه‌سازی</div>" +
      '<h1 class="page-title">سئو و تنظیمات فنی</h1><p class="page-sub">متای سراسری، Open Graph، نقشه سایت، robots و راهنمای تلگرام.</p></div>' +
      '<div class="page-actions"><a class="btn btn-soft" href="/sitemap.xml" target="_blank" rel="noopener">' + A.icon("external") + " sitemap.xml</a>" +
      '<a class="btn btn-soft" href="/robots.txt" target="_blank" rel="noopener">' + A.icon("external") + " robots.txt</a></div></div>" +
      '<form class="card" id="seoForm">' +
        '<div class="card-head bordered"><div><div class="card-title">' + A.icon("search") + " تنظیمات سئو</div>" +
          '<div class="card-sub">این مقادیر پیش‌فرض صفحاتی هستند که تنظیم اختصاصی ندارند.</div></div></div>' +
        '<div class="card-body">' +
          '<div class="admin-tabs" style="margin-bottom:14px">' +
            '<button type="button" class="admin-tab active" data-stab="site">وب‌سایت</button>' +
            '<button type="button" class="admin-tab" data-stab="telegram">تلگرام</button>' +
            '<button type="button" class="admin-tab" data-stab="tech">فنی و ایندکس</button></div>' +
          '<div data-spanel="site">' +
            '<div class="form-grid">' +
              '<div class="field"><label class="label">عنوان پیش‌فرض متا</label><input class="input" name="seo_meta_title" value="' + A.esc(s.seo_meta_title || "") + '"></div>' +
              '<div class="field"><label class="label">توضیح پیش‌فرض متا</label><textarea class="textarea" rows="3" name="seo_meta_description">' + A.esc(s.seo_meta_description || "") + "</textarea></div>" +
              '<div class="field"><label class="label">تصویر پیش‌فرض Open Graph</label><input class="input mono" dir="ltr" name="seo_og_image" value="' + A.esc(s.seo_og_image || "") + '" placeholder="/uploads/media/..."></div>' +
              '<div class="field"><label class="label">robots پیش‌فرض</label><select class="input" name="seo_robots_default">' + robotsOptions(s.seo_robots_default || "index,follow") + "</select></div>" +
            "</div></div>" +
          '<div data-spanel="telegram" hidden>' +
            '<div class="form-grid">' +
              '<div class="field"><label class="label">نام کاربری ربات</label><input class="input mono" dir="ltr" name="tg_bot_username" value="' + A.esc(s.tg_bot_username || "") + '" placeholder="' + A.esc(defaults.bot_username || "MyBot") + '">' +
                '<div class="hint">بدون @ — خالی بگذارید تا از BOT_USERNAME محیط سرور استفاده شود.</div></div>' +
              '<div class="field"><label class="label">نام اپلیکیشن Mini App</label><input class="input mono" dir="ltr" name="tg_share_app_name" value="' + A.esc(s.tg_share_app_name || "") + '" placeholder="app">' +
                '<div class="hint">برای ساخت لینک اشتراک‌گذاری به شکل t.me/bot/app?startapp=...</div></div>' +
              '<div class="field"><label class="label">راهنمای نام ربات</label><textarea class="textarea" rows="3" name="tg_bot_name_guide">' + A.esc(s.tg_bot_name_guide || "") + "</textarea></div>" +
              '<div class="field"><label class="label">راهنمای About ربات</label><textarea class="textarea" rows="3" name="tg_bot_about_guide">' + A.esc(s.tg_bot_about_guide || "") + "</textarea></div>" +
              '<div class="field"><label class="label">راهنمای Description ربات</label><textarea class="textarea" rows="4" name="tg_bot_description_guide">' + A.esc(s.tg_bot_description_guide || "") + "</textarea></div>" +
            "</div>" +
            '<div class="setting-block"><div class="setting-title">' + A.icon("send") + " ساخت لینک اشتراک‌گذاری</div>" +
              '<div class="inline-form"><input class="input mono" dir="ltr" id="shareParam" placeholder="startapp parameter (اختیاری)">' +
              '<button class="btn btn-soft btn-sm" type="button" id="shareBuild">' + A.icon("link") + " ساخت لینک</button></div>" +
              '<div class="copy-row mt-2"><input class="input mono" dir="ltr" id="shareOut" readonly placeholder="لینک اینجا نمایش داده می‌شود">' +
              '<button class="btn btn-soft btn-sm" type="button" id="shareCopy">' + A.icon("copy") + " کپی</button></div>" +
              '<div class="hint">ربات: <span class="mono" dir="ltr">' + A.esc(defaults.bot_username || "ثبت نشده") + "</span></div></div>" +
          "</div>" +
          '<div data-spanel="tech" hidden>' +
            '<div class="form-grid">' +
              '<div class="field"><label class="label">آدرس پایه سایت (canonical)</label><input class="input mono" dir="ltr" name="seo_canonical_base" value="' + A.esc(s.seo_canonical_base || "") + '" placeholder="https://example.com"></div>' +
              '<div class="field"><label class="label">کد تأیید Google Search Console</label><input class="input mono" dir="ltr" name="google_site_verification" value="' + A.esc(s.google_site_verification || "") + '"></div>' +
              '<div class="field"><label class="label">محتوای robots.txt</label><textarea class="textarea mono" dir="ltr" rows="10" name="robots_txt">' + A.esc(s.robots_txt || defaults.robots_txt || "") + "</textarea></div>" +
            "</div>" +
            '<div class="notice mt-2">' + A.icon("info") + "<div>نقشه سایت به صورت خودکار از صفحات و مقالات منتشرشده ساخته می‌شود.</div></div>" +
          "</div>" +
        "</div>" +
        '<div class="card-foot"><span class="tiny muted">آدرس سایت پیش‌فرض: ' + A.esc(defaults.site || "-") + "</span>" +
          '<button class="btn btn-primary btn-sm" type="submit">' + A.icon("check") + " ذخیره تنظیمات</button></div>" +
      "</form>";

    bindTabGroup(root, "data-stab", "data-spanel");
    if (state.seo.section) {
      const seoKey = state.seo.section;
      state.seo.section = "";
      setTabGroup(root, "data-stab", "data-spanel", seoKey);
    }
    const shareBuild = A.$("#shareBuild");
    if (shareBuild) shareBuild.addEventListener("click", async function () {
      const param = (A.$("#shareParam") || {}).value || "";
      try {
        const res = await A.api("/api/admin/seo/share-link?param=" + encodeURIComponent(param.trim()));
        const out = A.$("#shareOut");
        if (out) out.value = res.link || "";
        A.toast("لینک ساخته شد.", "success");
      } catch (e) { A.toast(e.message, "error"); }
    });
    const shareCopy = A.$("#shareCopy");
    if (shareCopy) shareCopy.addEventListener("click", function () {
      const out = A.$("#shareOut");
      A.copy(out ? out.value : "");
    });
    bindSubmit("#seoForm", async function (form) {
      const body = {};
      ["seo_meta_title", "seo_meta_description", "seo_og_image", "seo_robots_default", "seo_canonical_base",
        "google_site_verification", "robots_txt", "tg_bot_username", "tg_bot_name_guide", "tg_bot_about_guide",
        "tg_bot_description_guide", "tg_share_app_name"].forEach(function (key) {
        body[key] = form.elements[key] ? form.elements[key].value : "";
      });
      const btn = form.querySelector('button[type="submit"]');
      A.setBusy(btn, true, "در حال ذخیره…");
      try {
        const res = await A.api("/api/admin/seo", { method: "PUT", body: body });
        A.toast(res.message || "تنظیمات سئو ذخیره شد.", "success");
        A.setBusy(btn, false);
      } catch (e) {
        A.toast(e.message, "error");
        A.setBusy(btn, false);
      }
    });
  }

  /* ------------------------------------------------------------------
     ظاهر سایت
  ------------------------------------------------------------------ */
  async function renderAppearance(token) {
    const root = viewEl();
    root.innerHTML = loadingCard("در حال دریافت تنظیمات ظاهر…");
    let data;
    try {
      data = await A.api("/api/admin/appearance");
    } catch (e) {
      if (!alive(token)) return;
      root.innerHTML = errorCard(e.message, '<button class="btn btn-soft" data-act="refresh">' + A.icon("refresh") + " تلاش دوباره</button>");
      return;
    }
    if (!alive(token)) return;
    const s = data.settings || {};
    const widths = data.widths || [];
    const formats = data.date_formats || [];
    const tokens = data.tokens || [];
    root.innerHTML =
      '<div class="page-head"><div><div class="eyebrow">' + A.icon("image") + " ظاهر سایت</div>" +
      '<h1 class="page-title">ظاهر و چیدمان</h1><p class="page-sub">عرض محتوا، سایدبار و قالب تاریخ شمسی صفحات عمومی.</p></div>' +
      '<div class="page-actions"><a class="btn btn-soft" href="/education" target="_blank" rel="noopener">' + A.icon("external") + " نمای سایت</a></div></div>" +
      '<div class="settings-grid">' +
        '<form class="card" id="appearanceForm">' +
          '<div class="card-head bordered"><div><div class="card-title">' + A.icon("layers") + " چیدمان محتوا</div>" +
            '<div class="card-sub">این تنظیمات روی صفحات و مقالات منتشرشده اعمال می‌شود.</div></div></div>' +
          '<div class="card-body">' +
            '<div class="form-grid">' +
              '<div class="field"><label class="label">عرض پیش‌فرض محتوا</label><select class="input" name="default_content_width">' +
                widths.map(function (w) { return '<option value="' + w.key + '"' + (s.default_content_width === w.key ? " selected" : "") + ">" + A.esc(w.label) + "</option>"; }).join("") + "</select>" +
                '<div class="hint">متغیر CSS: <span class="mono">--content-max-width</span></div></div>' +
              '<div class="field"><label class="label">جای سایدبار</label><select class="input" name="sidebar_position">' +
                optionsHTML([["right", "سمت راست"], ["left", "سمت چپ"]], s.sidebar_position || "right") + "</select></div>" +
              '<div class="field"><label class="label">قالب نمایش تاریخ</label><select class="input" name="date_format" id="dateFormatSel">' +
                formats.map(function (f) { return '<option value="' + f.key + '"' + (s.date_format === f.key ? " selected" : "") + ">" + A.esc(f.label) + "</option>"; }).join("") + "</select></div>" +
              '<div class="field" id="customDateField"><label class="label">الگوی تاریخ دلخواه</label><input class="input mono" dir="ltr" name="date_format_custom" value="' + A.esc(s.date_format_custom || "") + '">' +
                '<div class="hint">توکن‌ها: ' + tokens.map(function (t) { return '<span class="token-chip mono">' + A.esc(t) + "</span>"; }).join(" ") + "</div></div>" +
            "</div>" +
            '<div class="setting-block"><div class="setting-title">' + A.icon("menu") + " سایدبار</div>" +
              '<div class="row wrap"><label class="switch"><input type="checkbox" name="sidebar_enabled"' + (s.sidebar_enabled ? " checked" : "") + '><span class="switch-track"></span><span class="switch-text">فعال بودن سایدبار</span></label>' +
              '<label class="switch"><input type="checkbox" name="sidebar_pages"' + (s.sidebar_pages ? " checked" : "") + '><span class="switch-track"></span><span class="switch-text">نمایش در صفحات ثابت</span></label>' +
              '<label class="switch"><input type="checkbox" name="sidebar_posts"' + (s.sidebar_posts ? " checked" : "") + '><span class="switch-track"></span><span class="switch-text">نمایش در مقالات</span></label></div></div>' +
          "</div>" +
          '<div class="card-foot"><span class="tiny muted">تغییرات پس از ذخیره، در صفحات عمومی اعمال می‌شود.</span>' +
            '<button class="btn btn-primary btn-sm" type="submit">' + A.icon("check") + " ذخیره</button></div>" +
        "</form>" +
        '<div class="settings-side"><div class="card"><div class="card-head"><div><div class="card-title">' + A.icon("calendar") + " پیش‌نمایش تاریخ</div></div></div>" +
          '<div class="card-body"><div class="date-preview" id="appearancePreview">—</div>' +
          '<button class="btn btn-soft btn-sm mt-2" type="button" id="datePreviewBtn">' + A.icon("refresh") + " به‌روزرسانی پیش‌نمایش</button>" +
          '<div class="hint mt-2">پیش‌نمایش با تنظیمات فعلی فرم (حتی قبل از ذخیره) ساخته می‌شود.</div></div></div>' +
          '<div class="card"><div class="card-head"><div><div class="card-title">' + A.icon("info") + " راهنما</div></div></div>" +
          '<div class="card-body"><div class="hint">عرض باریک ۷۲۰، معمولی ۹۶۰ و پهن ۱۲۰۰ پیکسل است. گزینه «تمام عرض» محدودیت را برمی‌دارد. هر صفحه و مقاله می‌تواند تنظیم اختصاصی داشته باشد.</div></div></div>' +
        "</div>" +
      "</div>";

    const formatSel = A.$("#dateFormatSel");
    const customField = A.$("#customDateField");
    function syncCustom() { if (customField && formatSel) customField.hidden = formatSel.value !== "custom"; }

    async function preview() {
      const form = A.$("#appearanceForm");
      const fmt = form.elements.date_format.value;
      const custom = form.elements.date_format_custom.value;
      const out = A.$("#appearancePreview");
      if (!out) return;
      try {
        const res = await A.api("/api/admin/appearance/preview?format=" + encodeURIComponent(fmt) + "&custom=" + encodeURIComponent(custom));
        out.textContent = res.preview || "—";
      } catch (e) { out.textContent = "پیش‌نمایش در دسترس نیست"; }
    }
    if (formatSel) formatSel.addEventListener("change", function () { syncCustom(); preview(); });
    const previewBtn = A.$("#datePreviewBtn");
    if (previewBtn) previewBtn.addEventListener("click", preview);
    syncCustom();
    preview();
    bindSubmit("#appearanceForm", async function (form) {
      const body = {
        default_content_width: form.elements.default_content_width.value,
        sidebar_position: form.elements.sidebar_position.value,
        date_format: form.elements.date_format.value,
        date_format_custom: form.elements.date_format_custom.value,
        sidebar_enabled: !!form.elements.sidebar_enabled.checked,
        sidebar_pages: !!form.elements.sidebar_pages.checked,
        sidebar_posts: !!form.elements.sidebar_posts.checked
      };
      const btn = form.querySelector('button[type="submit"]');
      A.setBusy(btn, true, "در حال ذخیره…");
      try {
        const res = await A.api("/api/admin/appearance", { method: "PUT", body: body });
        A.toast(res.message || "تنظیمات ظاهر ذخیره شد.", "success");
        A.setBusy(btn, false);
      } catch (e) {
        A.toast(e.message, "error");
        A.setBusy(btn, false);
      }
    });
  }

  /* ------------------------------------------------------------------
     نوار قیمت زندهٔ وب‌اپ
  ------------------------------------------------------------------ */
  async function renderTicker(token) {
    const root = viewEl();
    root.innerHTML = loadingCard("در حال دریافت تنظیمات نوار قیمت…");
    let data;
    try {
      data = await A.api("/api/admin/ticker");
    } catch (e) {
      if (!alive(token)) return;
      root.innerHTML = errorCard(e.message, '<button class="btn btn-soft" data-act="refresh">' + A.icon("refresh") + " تلاش دوباره</button>");
      return;
    }
    if (!alive(token)) return;

    const cfg = data.config || {};
    const sources = data.sources || [];
    const maxItems = data.max_items || 30;
    let rows = (cfg.items || []).map(function (i) {
      return { symbol: i.symbol || "", label: i.label || "", exchange: i.exchange || "", enabled: i.enabled !== false };
    });
    let preview = data.preview || { items: [] };

    const itemSources = [["", "منبع کلی"]].concat(sources.filter(function (s) { return s.slug; })
      .map(function (s) { return [s.slug, s.name]; }));

    function rowsHTML() {
      if (!rows.length) {
        return '<tr><td colspan="5"><div class="empty-row">' + A.icon("grid") + " هنوز نمادی اضافه نشده است.</div></td></tr>";
      }
      return rows.map(function (r, i) {
        return '<tr data-tick-row="' + i + '">' +
          '<td><input class="input mono" dir="ltr" maxlength="24" data-tick-field="symbol" value="' + A.esc(r.symbol) + '" placeholder="BTCUSDT"></td>' +
          '<td><input class="input" maxlength="24" data-tick-field="label" value="' + A.esc(r.label) + '" placeholder="بیت‌کوین"></td>' +
          '<td><select class="input" data-tick-field="exchange">' + optionsHTML(itemSources, r.exchange) + "</select></td>" +
          '<td><label class="switch"><input type="checkbox" data-tick-field="enabled"' + (r.enabled ? " checked" : "") + '><span class="switch-track"></span></label></td>' +
          '<td><div class="row gap-2 no-wrap">' +
            '<button class="icon-btn sm" type="button" data-tick-move="-1" title="بالا بردن"' + (i === 0 ? " disabled" : "") + ">" + A.icon("chevron-up") + "</button>" +
            '<button class="icon-btn sm" type="button" data-tick-move="1" title="پایین بردن"' + (i === rows.length - 1 ? " disabled" : "") + ">" + A.icon("chevron-down") + "</button>" +
            '<button class="icon-btn sm danger" type="button" data-tick-del title="حذف نماد">' + A.icon("trash") + "</button>" +
          "</div></td></tr>";
      }).join("");
    }

    function previewHTML() {
      const items = (preview.items || []).filter(function (i) { return Number(i.price) > 0; });
      if (!items.length) {
        return '<div class="empty-row">' + A.icon("info") + " برای دیدن پیش‌نمایش، نمادها را ذخیره کنید (قیمت‌ها از منبع انتخابی خوانده می‌شود).</div>";
      }
      const cell = function (i) {
        const pct = Number(i.pct) || 0;
        const cls = pct > 0 ? "up" : (pct < 0 ? "down" : "flat");
        const price = Number(i.price).toLocaleString("en-US", {
          minimumFractionDigits: Number(i.price) >= 1000 ? 2 : 4, maximumFractionDigits: Number(i.price) >= 1000 ? 2 : 4
        });
        return '<span class="ticker-item"><span class="ticker-sym">' + A.esc(i.label || i.symbol) + "</span>" +
          '<span class="ticker-price">' + price + "</span>" +
          '<span class="ticker-chg ' + cls + '">' + (pct >= 0 ? "▲" : "▼") + " " + Math.abs(pct).toFixed(2) + "%</span></span>";
      };
      const one = items.map(cell).join("");
      return '<div class="ticker-bar ticker-preview" dir="ltr" style="--ticker-speed:' + (Number(cfg.speed) || 48) + 's">' +
        '<div class="ticker-badge"><span class="ticker-live-dot"></span><span dir="rtl">پیش‌نمایش</span></div>' +
        '<div class="ticker-viewport"><div class="ticker-track">' + one + one + "</div></div></div>";
    }

    function drawItems() {
      const body = A.$("#tickerRows");
      if (body) body.innerHTML = rowsHTML();
      const count = A.$("#tickerItemCount");
      if (count) count.textContent = A.toFa(rows.length) + " از " + A.toFa(maxItems);
      bindRows();
      drawPreviewState();
    }

    function drawPreviewState() {
      const box = A.$("#tickerPreview");
      if (box) box.innerHTML = previewHTML();
    }

    function bindRows() {
      const body = A.$("#tickerRows");
      if (!body) return;
      body.querySelectorAll("[data-tick-field]").forEach(function (input) {
        const ev = input.tagName === "SELECT" || input.type === "checkbox" ? "change" : "input";
        input.addEventListener(ev, function () {
          const tr = input.closest("[data-tick-row]");
          const i = parseInt(tr.getAttribute("data-tick-row"), 10);
          if (!rows[i]) return;
          const field = input.getAttribute("data-tick-field");
          rows[i][field] = input.type === "checkbox" ? input.checked : input.value;
          if (field === "symbol") rows[i].symbol = String(input.value).toUpperCase();
        });
      });
      body.querySelectorAll("[data-tick-move]").forEach(function (btn) {
        btn.addEventListener("click", function () {
          const i = parseInt(btn.closest("[data-tick-row]").getAttribute("data-tick-row"), 10);
          const j = i + parseInt(btn.getAttribute("data-tick-move"), 10);
          if (j < 0 || j >= rows.length) return;
          const tmp = rows[i]; rows[i] = rows[j]; rows[j] = tmp;
          drawItems();
        });
      });
      body.querySelectorAll("[data-tick-del]").forEach(function (btn) {
        btn.addEventListener("click", function () {
          const i = parseInt(btn.closest("[data-tick-row]").getAttribute("data-tick-row"), 10);
          rows.splice(i, 1);
          drawItems();
        });
      });
    }

    root.innerHTML =
      '<div class="page-head"><div><div class="eyebrow">' + A.icon("activity") + " بازار</div>" +
      '<h1 class="page-title">نوار قیمت زنده</h1>' +
      '<p class="page-sub">نمایش قیمت لحظه‌ای و تغییر ۲۴ ساعتهٔ نمادها در بالای وب‌اپ کاربر.</p></div>' +
      '<div class="page-actions"><button class="btn btn-soft" type="button" data-act="refresh">' + A.icon("refresh") + " نوسازی قیمت‌ها</button></div></div>" +
      '<div class="settings-grid">' +
        '<form class="card" id="tickerForm">' +
          '<div class="card-head bordered"><div><div class="card-title">' + A.icon("settings") + " تنظیمات نوار</div>" +
            '<div class="card-sub">فعال‌سازی، سرعت اسکرول و منبع قیمت.</div></div>' +
            '<label class="switch"><input type="checkbox" id="tickerEnabled"' + (cfg.enabled ? " checked" : "") + '><span class="switch-track"></span><span class="switch-text">نمایش در وب‌اپ کاربر</span></label></div>' +
          '<div class="card-body">' +
            '<div class="form-grid">' +
              '<div class="field"><label class="label">منبع قیمت و تغییرات</label><select class="input" id="tickerSourceSel">' +
                optionsHTML(sources.map(function (s) { return [s.slug, s.name]; }), cfg.source || "") + "</select>" +
                '<div class="hint">صرافی‌ای که قیمت لحظه‌ای و درصد ۲۴ ساعت از آن خوانده می‌شود.</div></div>' +
              '<div class="field"><label class="label">سرعت اسکرول نوار</label>' +
                '<div class="row gap-2"><input type="range" id="tickerSpeedRange" min="8" max="240" step="2" value="' + (Number(cfg.speed) || 48) + '" style="flex:1">' +
                '<input class="input mono" id="tickerSpeedNum" type="number" min="8" max="240" step="2" value="' + (Number(cfg.speed) || 48) + '" style="width:82px" dir="ltr"></div>' +
                '<div class="hint">مدت زمان یک دور کامل نوار بر حسب ثانیه؛ عدد کمتر یعنی سریع‌تر.</div></div>' +
              '<div class="field"><label class="label">نمایش درصد تغییر ۲۴ ساعت</label>' +
                '<label class="switch"><input type="checkbox" id="tickerShowChange"' + (cfg.show_change ? " checked" : "") + '><span class="switch-track"></span><span class="switch-text">همراه با رنگ سبز و قرمز</span></label>' +
                '<div class="hint">اعداد این بخش همیشه با ارقام لاتین نمایش داده می‌شوند.</div></div>' +
            "</div>" +
            '<div class="setting-block"><div class="setting-head">' +
              '<div class="setting-title">' + A.icon("grid") + " نمادهای قابل نمایش" +
              '<span class="tiny muted" id="tickerItemCount"></span></div>' +
              '<button class="btn btn-soft btn-sm" type="button" id="tickerAddBtn">' + A.icon("plus") + " افزودن نماد</button></div>" +
              '<div class="table-wrap"><table class="table"><thead><tr>' +
                "<th>نماد</th><th>برچسب</th><th>صرافی نماد</th><th>نمایش</th><th>ترتیب</th>" +
              "</tr></thead><tbody id=\"tickerRows\">" + rowsHTML() + "</tbody></table></div>" +
              '<div class="hint mt-2">برچسب اختیاری است؛ اگر خالی بماند نام خود نماد روی نوار نمایش داده می‌شود.</div>' +
            "</div>" +
          "</div>" +
          '<div class="card-foot"><span class="tiny muted">پس از ذخیره، نوار در تمام صفحات وب‌اپ کاربر به‌روزرسانی می‌شود.</span>' +
            '<button class="btn btn-primary btn-sm" type="submit">' + A.icon("check") + " ذخیره تنظیمات</button></div>" +
        "</form>" +
        '<div class="settings-side">' +
          '<div class="card"><div class="card-head"><div><div class="card-title">' + A.icon("eye") + " پیش‌نمایش زنده</div></div></div>" +
            '<div class="card-body"><div id="tickerPreview">' + previewHTML() + "</div>" +
            '<div class="hint mt-2">پیش‌نمایش با آخرین قیمت‌های دریافتی ساخته می‌شود؛ نشانگر را روی آن نگه دارید تا متوقف شود.</div></div></div>' +
          '<div class="card"><div class="card-head"><div><div class="card-title">' + A.icon("info") + " راهنما</div></div></div>" +
            '<div class="card-body"><div class="hint">حداکثر ' + A.toFa(maxItems) + " نماد قابل نمایش است. اگر نمادی در صرافی منبع پیدا نشود، به‌صورت خودکار از صرافی اصلی همان نماد خوانده می‌شود. قیمت‌ها تا ۳۰ ثانیه کش می‌شوند تا به سرویس داده فشار نیاید.</div></div></div>" +
        "</div>" +
      "</div>";

    const range = A.$("#tickerSpeedRange");
    const num = A.$("#tickerSpeedNum");
    function syncSpeed(from) {
      const v = Math.max(8, Math.min(240, parseInt(from.value, 10) || 48));
      if (range && from !== range) range.value = v;
      if (num && from !== num) num.value = v;
      const track = A.$(".ticker-preview .ticker-track");
      const bar = A.$(".ticker-preview");
      if (bar) bar.style.setProperty("--ticker-speed", v + "s");
      if (track) track.style.animationDuration = v + "s";
    }
    if (range) range.addEventListener("input", function () { syncSpeed(range); });
    if (num) num.addEventListener("input", function () { syncSpeed(num); });

    const addBtn = A.$("#tickerAddBtn");
    if (addBtn) addBtn.addEventListener("click", function () {
      if (rows.length >= maxItems) { A.toast("به سقف مجاز نمادها رسیدید.", "error"); return; }
      rows.push({ symbol: "", label: "", exchange: "", enabled: true });
      drawItems();
      const last = A.$("#tickerRows tr:last-child [data-tick-field='symbol']");
      if (last) last.focus();
    });

    drawItems();

    bindSubmit("#tickerForm", async function (form) {
      const btn = form.querySelector('button[type="submit"]');
      A.setBusy(btn, true, "در حال ذخیره…");
      const body = {
        enabled: !!(A.$("#tickerEnabled") || {}).checked,
        show_change: !!(A.$("#tickerShowChange") || {}).checked,
        speed: parseInt((A.$("#tickerSpeedNum") || {}).value, 10) || 48,
        source: (A.$("#tickerSourceSel") || {}).value || "",
        items: rows.filter(function (r) { return String(r.symbol || "").trim(); }).map(function (r) {
          return {
            symbol: String(r.symbol).trim().toUpperCase(), label: String(r.label || "").trim(),
            exchange: r.exchange || "", enabled: !!r.enabled
          };
        })
      };
      try {
        const res = await A.api("/api/admin/ticker", { method: "PUT", body: body });
        preview = res.preview || { items: [] };
        cfg.enabled = res.config.enabled; cfg.speed = res.config.speed;
        cfg.show_change = res.config.show_change; cfg.source = res.config.source;
        rows = (res.config.items || []).map(function (i) {
          return { symbol: i.symbol, label: i.label || "", exchange: i.exchange || "", enabled: i.enabled !== false };
        });
        drawItems();
        A.toast(res.message || "تنظیمات نوار قیمت ذخیره شد.", "success");
      } catch (e) {
        A.toast(e.message, "error");
      }
      A.setBusy(btn, false);
    });
  }

  /* ------------------------------------------------------------------
     ثبت‌نام و فیلدها
  ------------------------------------------------------------------ */
  async function renderRegistration(token) {
    const root = viewEl();
    root.innerHTML = loadingCard("در حال دریافت تنظیمات ثبت‌نام…");
    let data;
    try {
      data = await A.api("/api/admin/registration-fields");
    } catch (e) {
      if (!alive(token)) return;
      root.innerHTML = errorCard(e.message, '<button class="btn btn-soft" data-act="refresh">' + A.icon("refresh") + " تلاش دوباره</button>");
      return;
    }
    if (!alive(token)) return;
    state.registration = data;
    const fields = data.items || [];
    const settings = data.settings || {};
    const rows = fields.map(function (f) {
      return '<tr>' +
        '<td><div class="cell-main">' + A.esc(f.label) + '</div><div class="cell-sub mono" dir="ltr">' + A.esc(f.field_key) + "</div></td>" +
        "<td>" + A.esc(FIELD_TYPE_LABELS[f.field_type] || f.field_type) + "</td>" +
        "<td>" + (Number(f.is_required) ? A.pill("الزامی", "amber") : A.pill("اختیاری", "gray")) + "</td>" +
        "<td>" + (Number(f.is_active) ? A.pill("فعال", "green") : A.pill("غیرفعال", "gray")) + "</td>" +
        '<td class="num">' + A.toFa(f.sort_order || 0) + "</td>" +
        '<td><div class="cell-actions">' +
          '<button class="icon-btn sm" data-act="reg-field-edit" data-fid="' + f.id + '" title="ویرایش">' + A.icon("edit") + "</button>" +
          (["username", "password"].indexOf(f.field_key) >= 0 ? "" : '<button class="icon-btn sm danger" data-act="reg-field-delete" data-fid="' + f.id + '" data-name="' + A.esc(f.label) + '" title="حذف">' + A.icon("trash") + "</button>") +
        "</div></td></tr>";
    }).join("");
    root.innerHTML =
      '<div class="page-head"><div><div class="eyebrow">' + A.icon("user") + " حساب کاربری</div>" +
      '<h1 class="page-title">ثبت‌نام و فیلدها</h1><p class="page-sub">فیلدهای فرم ثبت‌نام و وضعیت باز بودن ثبت‌نام را مدیریت کنید.</p></div>' +
      '<div class="page-actions"><a class="btn btn-soft" href="/register" target="_blank" rel="noopener">' + A.icon("external") + " فرم ثبت‌نام</a>" +
      '<button class="btn btn-primary" data-act="reg-field-add">' + A.icon("plus") + " فیلد جدید</button></div></div>" +
      '<div class="settings-grid">' +
        '<div class="card"><div class="card-head bordered"><div><div class="card-title">' + A.icon("settings") + " تنظیمات ثبت‌نام</div>" +
          '<div class="card-sub">کلیدهای username و password همیشه فعال و الزامی هستند.</div></div></div>' +
          '<form id="regSettingsForm"><div class="card-body">' +
            '<div class="row wrap"><label class="switch"><input type="checkbox" name="registration_open"' + (settings.registration_open ? " checked" : "") + '><span class="switch-track"></span><span class="switch-text">ثبت‌نام باز است</span></label></div>' +
            '<div class="field mt-2"><label class="label">حداقل طول رمز عبور</label><input class="input" type="number" min="4" max="64" name="registration_password_min" value="' + Number(settings.registration_password_min || 8) + '"></div>' +
          "</div>" +
          '<div class="card-foot"><span class="tiny muted">حداقل ۴ و حداکثر ۶۴ کاراکتر</span>' +
            '<button class="btn btn-primary btn-sm" type="submit">' + A.icon("check") + " ذخیره</button></div></form></div>" +
        '<div class="card"><div class="card-head bordered"><div><div class="card-title">' + A.icon("layers") + " فیلدهای فرم</div>" +
          '<div class="card-sub">' + A.toFa(fields.length) + " فیلد</div></div></div>" +
          (fields.length ? '<div class="table-wrap"><table class="table table-fix"><thead><tr><th>فیلد</th><th>نوع</th><th>الزام</th><th>وضعیت</th><th>ترتیب</th><th></th></tr></thead><tbody>' + rows + "</tbody></table></div>"
            : A.emptyState("user", "فیلدی وجود ندارد", "یک فیلد جدید اضافه کنید.")) + "</div>" +
      "</div>";
    bindSubmit("#regSettingsForm", async function (form) {
      const btn = form.querySelector('button[type="submit"]');
      A.setBusy(btn, true, "در حال ذخیره…");
      try {
        const res = await A.api("/api/admin/registration-settings", {
          method: "PUT",
          body: {
            registration_open: !!form.elements.registration_open.checked,
            registration_password_min: Number(form.elements.registration_password_min.value || 8)
          }
        });
        A.toast(res.message || "تنظیمات ثبت‌نام ذخیره شد.", "success");
        A.setBusy(btn, false);
      } catch (e) {
        A.toast(e.message, "error");
        A.setBusy(btn, false);
      }
    });
  }

  function openRegFieldForm(field) {
    const f = field || { field_key: "", label: "", field_type: "text", is_required: false, is_active: true, sort_order: 0, validation_regex: "" };
    const types = (state.registration && state.registration.types) || ["text", "email", "phone", "password", "number", "select"];
    const reserved = (state.registration && state.registration.reserved) || ["username", "password"];
    const lock = field && reserved.indexOf(field.field_key) >= 0;
    A.openModal({
      size: "normal",
      title: field ? "ویرایش فیلد ثبت‌نام" : "فیلد ثبت‌نام جدید",
      body: '<form id="regFieldForm">' +
        '<div class="form-grid two">' +
          '<div class="field"><label class="label">کلید فیلد</label><input class="input mono" dir="ltr" name="field_key" value="' + A.esc(f.field_key) + '"' + (lock ? " readonly" : "") + ' placeholder="first_name"><div class="hint">حروف کوچک انگلیسی، عدد و _</div></div>' +
          '<div class="field"><label class="label">عنوان</label><input class="input" name="label" value="' + A.esc(f.label) + '"></div>' +
          '<div class="field"><label class="label">نوع فیلد</label><select class="input" name="field_type">' +
            types.map(function (t) { return '<option value="' + t + '"' + (f.field_type === t ? " selected" : "") + ">" + A.esc(FIELD_TYPE_LABELS[t] || t) + "</option>"; }).join("") + "</select></div>" +
          '<div class="field"><label class="label">ترتیب</label><input class="input" type="number" min="0" max="999" name="sort_order" value="' + Number(f.sort_order || 0) + '"></div>' +
        "</div>" +
        '<div class="field"><label class="label">الگوی اعتبارسنجی (اختیاری)</label><input class="input mono" dir="ltr" name="validation_regex" value="' + A.esc(f.validation_regex || "") + '" placeholder="^09\\d{9}$"></div>' +
        '<div class="row wrap mt-2">' +
          '<label class="switch"><input type="checkbox" name="is_required"' + (f.is_required ? " checked" : "") + '><span class="switch-track"></span><span class="switch-text">الزامی</span></label>' +
          '<label class="switch"><input type="checkbox" name="is_active"' + (f.is_active ? " checked" : "") + '><span class="switch-track"></span><span class="switch-text">فعال</span></label></div>' +
      "</form>",
      footer: '<button class="btn btn-ghost" data-modal-close>انصراف</button><button class="btn btn-primary" id="regFieldSave">' + A.icon("check") + " ذخیره</button>",
      onMount: function (modal) {
        modal.root.querySelector("#regFieldSave").addEventListener("click", async function () {
          const form = modal.root.querySelector("#regFieldForm");
          const btn = modal.root.querySelector("#regFieldSave");
          const body = {
            field_key: form.elements.field_key.value.trim().toLowerCase(),
            label: form.elements.label.value.trim(),
            field_type: form.elements.field_type.value,
            is_required: !!form.elements.is_required.checked,
            is_active: !!form.elements.is_active.checked,
            sort_order: Number(form.elements.sort_order.value || 0),
            validation_regex: form.elements.validation_regex.value.trim()
          };
          A.setBusy(btn, true, "در حال ذخیره…");
          try {
            const res = await A.api("/api/admin/registration-fields" + (field ? "/" + field.id : ""), { method: field ? "PUT" : "POST", body: body });
            A.toast(res.message || "فیلد ذخیره شد.", "success");
            A.closeModal();
            refreshCurrent();
          } catch (e) {
            A.toast(e.message, "error");
            A.setBusy(btn, false);
          }
        });
      }
    });
  }

  async function deleteRegField(fid, name) {
    const ok = await A.confirm({ title: "حذف فیلد ثبت‌نام", message: "فیلد «" + (name || "") + "» از فرم ثبت‌نام حذف می‌شود.", danger: true, confirmText: "حذف" });
    if (!ok) return;
    try {
      await A.api("/api/admin/registration-fields/" + fid, { method: "DELETE" });
      A.toast("فیلد حذف شد.", "success");
      refreshCurrent();
    } catch (e) { A.toast(e.message, "error"); }
  }

  /* ------------------------------------------------------------------
     کاربران وب
  ------------------------------------------------------------------ */
  async function renderWebUsers(token) {
    const root = viewEl();
    root.innerHTML =
      '<div class="page-head"><div><div class="eyebrow">' + A.icon("users") + " حساب کاربری</div>" +
      '<h1 class="page-title">کاربران وب‌اپ</h1><p class="page-sub">حساب‌های ثبت‌نام‌شده، دسترسی و اطلاعات تماس.</p></div>' +
      '<div class="page-actions"><button class="btn btn-soft" data-act="webusers-refresh">' + A.icon("refresh") + " به‌روزرسانی</button></div></div>" +
      '<div class="card"><div class="card-body">' +
        '<form id="webUsersSearch" class="inline-form"><input class="input" name="q" value="' + A.esc(state.webusers.q || "") + '" placeholder="نام، نام کاربری، ایمیل، موبایل یا شناسه">' +
          '<button class="btn btn-soft btn-sm" type="submit">' + A.icon("search") + " جستجو</button></form>" +
      "</div></div>" +
      '<div class="card"><div id="webUsersBox">' + loadingCard("در حال دریافت کاربران…") + '</div><div id="webUsersPager"></div></div>';
    bindSubmit("#webUsersSearch", function (form) {
      state.webusers.q = form.elements.q.value.trim();
      state.webusers.page = 1;
      loadWebUsers(token);
    });
    await loadWebUsers(token);
  }

  async function loadWebUsers(token) {
    const box = A.$("#webUsersBox");
    if (!box) return;
    box.innerHTML = loadingCard("در حال دریافت کاربران…");
    let data;
    try {
      data = await A.api("/api/admin/web-users?q=" + encodeURIComponent(state.webusers.q || "") + "&page=" + Number(state.webusers.page || 1) + "&page_size=20");
    } catch (e) {
      if (!alive(token)) return;
      box.innerHTML = A.emptyState("alert", "دریافت کاربران انجام نشد", e.message);
      return;
    }
    if (!alive(token) || !A.$("#webUsersBox")) return;
    const items = data.items || [];
    state.webusers.items = items;
    if (!items.length) {
      box.innerHTML = A.emptyState("users", "کاربری یافت نشد", "عبارت جستجو را تغییر دهید.");
    } else {
      const rows = items.map(function (u) {
        const name = ((u.first_name || "") + " " + (u.last_name || "")).trim();
        const avatar = A.initials(name || u.username || "?");
        return '<tr>' +
          '<td><div class="row"><div class="avatar sm">' + A.esc(avatar) + '</div><div><div class="cell-main">' + A.esc(name || "بدون نام") + '</div>' +
            '<div class="cell-sub mono" dir="ltr">' + A.esc(u.username ? "@" + u.username : "") + "</div></div></div></td>" +
          '<td class="tiny muted mono" dir="ltr">' + A.esc(u.email || u.phone || "-") + "</td>" +
          "<td>" + (u.access_enabled ? A.pill("فعال", "green") : A.pill("غیرفعال", "gray")) + (u.has_password ? "" : " " + A.pill("بدون رمز", "amber")) + "</td>" +
          '<td class="tiny muted nowrap">' + A.esc(u.joined_at || "-") + "</td>" +
          '<td class="tiny muted nowrap">' + A.esc(u.last_login || "-") + "</td>" +
          '<td><div class="cell-actions"><button class="icon-btn sm" data-act="webuser-edit" data-uid="' + u.user_id + '" title="ویرایش">' + A.icon("edit") + "</button></div></td></tr>";
      }).join("");
      box.innerHTML = '<div class="table-wrap"><table class="table table-fix"><thead><tr><th>کاربر</th><th>تماس</th><th>دسترسی</th><th>عضویت</th><th>آخرین ورود</th><th></th></tr></thead><tbody>' + rows + "</tbody></table></div>";
    }
    const pager = A.$("#webUsersPager");
    if (pager) pager.innerHTML = pagerHTML(state.webusers.page, Math.ceil((data.total || 0) / 20));
  }

  function openWebUserEditor(uid) {
    const u = (state.webusers.items || []).find(function (x) { return Number(x.user_id) === Number(uid); });
    if (!u) { A.toast("کاربر پیدا نشد.", "error"); return; }
    A.openModal({
      size: "normal",
      title: "ویرایش کاربر وب",
      sub: "شناسه " + A.toFa(u.user_id) + " • " + A.esc(u.username ? "@" + u.username : ""),
      body: '<form id="webUserForm">' +
        '<div class="row wrap" style="margin-bottom:12px"><label class="switch"><input type="checkbox" name="access_enabled"' + (u.access_enabled ? " checked" : "") + '><span class="switch-track"></span><span class="switch-text">دسترسی وب‌اپ فعال</span></label></div>' +
        '<div class="form-grid two">' +
          '<div class="field"><label class="label">نام</label><input class="input" name="first_name" value="' + A.esc(u.first_name || "") + '"></div>' +
          '<div class="field"><label class="label">نام خانوادگی</label><input class="input" name="last_name" value="' + A.esc(u.last_name || "") + '"></div>' +
          '<div class="field"><label class="label">ایمیل</label><input class="input mono" dir="ltr" name="email" value="' + A.esc(u.email || "") + '"></div>' +
          '<div class="field"><label class="label">موبایل</label><input class="input mono" dir="ltr" name="phone" value="' + A.esc(u.phone || "") + '" placeholder="+989..."></div>' +
        "</div>" +
        (u.webapp_until ? '<div class="hint mt-2">پایان اشتراک وب‌اپ: ' + A.esc(u.webapp_until) + "</div>" : "") +
      "</form>",
      footer: '<button class="btn btn-ghost" data-modal-close>انصراف</button><button class="btn btn-primary" id="webUserSave">' + A.icon("check") + " ذخیره</button>",
      onMount: function (modal) {
        modal.root.querySelector("#webUserSave").addEventListener("click", async function () {
          const form = modal.root.querySelector("#webUserForm");
          const btn = modal.root.querySelector("#webUserSave");
          A.setBusy(btn, true, "در حال ذخیره…");
          try {
            const res = await A.api("/api/admin/web-users/" + uid, {
              method: "PUT",
              body: {
                access_enabled: !!form.elements.access_enabled.checked,
                first_name: form.elements.first_name.value,
                last_name: form.elements.last_name.value,
                email: form.elements.email.value,
                phone: form.elements.phone.value
              }
            });
            A.toast(res.message || "کاربر ذخیره شد.", "success");
            A.closeModal();
            loadWebUsers(viewToken);
          } catch (e) {
            A.toast(e.message, "error");
            A.setBusy(btn, false);
          }
        });
      }
    });
  }

  /* ------------------------------------------------------------------
     ورود با پیامک (OTP)
  ------------------------------------------------------------------ */
  async function renderOtp(token) {
    const root = viewEl();
    root.innerHTML = loadingCard("در حال دریافت تنظیمات پیامک…");
    let data;
    try {
      data = await A.api("/api/admin/otp");
    } catch (e) {
      if (!alive(token)) return;
      root.innerHTML = errorCard(e.message, '<button class="btn btn-soft" data-act="refresh">' + A.icon("refresh") + " تلاش دوباره</button>");
      return;
    }
    if (!alive(token)) return;
    const s = data.settings || {};
    root.innerHTML =
      '<div class="page-head"><div><div class="eyebrow">' + A.icon("lock") + " حساب کاربری</div>" +
      '<h1 class="page-title">ورود با پیامک</h1><p class="page-sub">تنظیمات ippanel و محدودیت‌های ارسال کد یک‌بارمصرف.</p></div>' +
      '<div class="page-actions"><a class="btn btn-soft" href="/login" target="_blank" rel="noopener">' + A.icon("external") + " صفحه ورود</a></div></div>" +
      '<div class="settings-grid">' +
        '<form class="card" id="otpForm">' +
          '<div class="card-head bordered"><div><div class="card-title">' + A.icon("send") + " تنظیمات ارسال</div>" +
            '<div class="card-sub">کلید API فقط در سرور ذخیره می‌شود و در پاسخ‌ها نمایش داده نمی‌شود.</div></div></div>' +
          '<div class="card-body">' +
            '<div class="row wrap"><label class="switch"><input type="checkbox" name="otp_login_enabled"' + (s.otp_login_enabled ? " checked" : "") + '><span class="switch-track"></span><span class="switch-text">ورود با پیامک فعال باشد</span></label></div>' +
            '<div class="form-grid two mt-2">' +
              '<div class="field"><label class="label">کلید API آی‌پی‌پنل</label><div class="inline-form"><input class="input mono" dir="ltr" type="password" name="ippanel_api_key" id="otpApiKey" placeholder="' + (s.ippanel_api_key_set ? "ذخیره شده — برای تغییر مقدار جدید وارد کنید" : "کلید API") + '">' +
                '<button class="icon-btn sm" type="button" data-act="otp-toggle-key" title="نمایش">' + A.icon("eye") + "</button></div></div>" +
              '<div class="field"><label class="label">شماره فرستنده</label><input class="input mono" dir="ltr" name="ippanel_sender" value="' + A.esc(s.ippanel_sender || "") + '"></div>' +
              '<div class="field"><label class="label">کد الگوی پیامک</label><input class="input mono" dir="ltr" name="ippanel_pattern_code" value="' + A.esc(s.ippanel_pattern_code || "") + '"></div>' +
              '<div class="field"><label class="label">طول کد</label><input class="input" type="number" min="4" max="8" name="otp_code_length" value="' + Number(s.otp_code_length || 5) + '"></div>' +
              '<div class="field"><label class="label">اعتبار کد (ثانیه)</label><input class="input" type="number" min="30" max="1800" name="otp_expiry_seconds" value="' + Number(s.otp_expiry_seconds || 120) + '"></div>' +
              '<div class="field"><label class="label">حداکثر تلاش</label><input class="input" type="number" min="1" max="10" name="otp_max_attempts" value="' + Number(s.otp_max_attempts || 3) + '"></div>' +
              '<div class="field"><label class="label">فاصله ارسال مجدد (ثانیه)</label><input class="input" type="number" min="10" max="3600" name="otp_resend_cooldown" value="' + Number(s.otp_resend_cooldown || 60) + '"></div>' +
              '<div class="field"><label class="label">سقف روزانه هر شماره</label><input class="input" type="number" min="1" max="100" name="otp_daily_limit_per_phone" value="' + Number(s.otp_daily_limit_per_phone || 10) + '"></div>' +
            "</div>" +
          "</div>" +
          '<div class="card-foot"><span class="tiny muted">' + (s.ippanel_api_key_set ? "کلید API ثبت شده است." : "کلید API ثبت نشده است.") + "</span>" +
            '<button class="btn btn-primary btn-sm" type="submit">' + A.icon("check") + " ذخیره تنظیمات</button></div>" +
        "</form>" +
        '<div class="settings-side">' +
          '<div class="card"><div class="card-head"><div><div class="card-title">' + A.icon("send") + " ارسال آزمایشی</div></div></div>" +
            '<form id="otpTestForm"><div class="card-body"><div class="field"><label class="label">شماره موبایل</label><input class="input mono" dir="ltr" name="phone" placeholder="+989121234567"></div>' +
              '<div class="notice mt-2">' + A.icon("info") + "<div>کد آزمایشی ذخیره نمی‌شود و فقط برای بررسی اتصال ارسال می‌گردد.</div></div></div>" +
            '<div class="card-foot"><button class="btn btn-soft btn-sm" type="submit">' + A.icon("send") + " ارسال آزمایشی</button></div></form></div>" +
          '<div class="card"><div class="card-head"><div><div class="card-title">' + A.icon("info") + " نکات امنیتی</div></div></div>" +
            '<div class="card-body"><div class="hint">کدها به صورت هش‌شده ذخیره می‌شوند. خطاهای ارسال برای کاربر عمومی و برای مدیر در رویدادهای امنیتی ثبت می‌شود. برای فعال شدن تب پیامک در صفحه ورود، گزینه «ورود با پیامک» را فعال کنید.</div></div></div>' +
        "</div>" +
      "</div>";
    bindSubmit("#otpForm", async function (form) {
      const btn = form.querySelector('button[type="submit"]');
      const body = {};
      ["ippanel_sender", "ippanel_pattern_code"].forEach(function (k) { body[k] = form.elements[k] ? form.elements[k].value.trim() : ""; });
      ["otp_code_length", "otp_expiry_seconds", "otp_max_attempts", "otp_resend_cooldown", "otp_daily_limit_per_phone"].forEach(function (k) {
        body[k] = Number(form.elements[k].value || 0);
      });
      body.otp_login_enabled = !!form.elements.otp_login_enabled.checked;
      const apiKey = (form.elements.ippanel_api_key.value || "").trim();
      if (apiKey) body.ippanel_api_key = apiKey;
      A.setBusy(btn, true, "در حال ذخیره…");
      try {
        const res = await A.api("/api/admin/otp", { method: "PUT", body: body });
        A.toast(res.message || "تنظیمات پیامک ذخیره شد.", "success");
        A.setBusy(btn, false);
        form.elements.ippanel_api_key.value = "";
      } catch (e) {
        A.toast(e.message, "error");
        A.setBusy(btn, false);
      }
    });
    bindSubmit("#otpTestForm", async function (form) {
      const phone = form.elements.phone.value.trim();
      if (!phone) { A.toast("شماره موبایل را وارد کنید.", "error"); return; }
      const btn = form.querySelector('button[type="submit"]');
      A.setBusy(btn, true, "در حال ارسال…");
      try {
        const res = await A.api("/api/admin/otp/test", { method: "POST", body: { phone: phone } });
        A.toast(res.message || "پیامک آزمایشی ارسال شد.", "success");
      } catch (e) {
        A.toast(e.message, "error");
      } finally {
        A.setBusy(btn, false);
      }
    });
  }

  /* ------------------------------------------------------------------
     مرکز امنیت
  ------------------------------------------------------------------ */
  function secTabItems() {
    return [["overview", "نمای کلی"], ["events", "رویدادها"], ["ip", "بلاک IP"], ["users", "بلاک کاربر"], ["audit", "لاگ مدیریت"]];
  }
  function setSecTabUI() {
    const wrap = A.$("#secTabs");
    if (!wrap) return;
    wrap.querySelectorAll("[data-sec-tab]").forEach(function (b) {
      b.classList.toggle("active", b.getAttribute("data-sec-tab") === state.sec.tab);
    });
  }
  async function renderSecurityCenter(token) {
    const root = viewEl();
    root.innerHTML =
      '<div class="page-head"><div><div class="eyebrow">' + A.icon("shield") + " حفاظت سامانه</div>" +
      '<h1 class="page-title">مرکز امنیت</h1><p class="page-sub">رویدادهای امنیتی، مسدودسازی IP و کاربر، و لاگ تغییرات مدیریت.</p></div>' +
      '<div class="page-actions"><button class="btn btn-soft" data-act="sec-refresh">' + A.icon("refresh") + " به‌روزرسانی</button></div></div>" +
      '<div class="admin-tabs" id="secTabs">' + secTabItems().map(function (t) {
        return '<button type="button" class="admin-tab' + (state.sec.tab === t[0] ? " active" : "") + '" data-sec-tab="' + t[0] + '">' + A.esc(t[1]) + "</button>";
      }).join("") + "</div>" +
      '<div id="secBody">' + loadingCard("در حال دریافت اطلاعات امنیتی…") + "</div>";
    setSecTabUI();
    root.querySelectorAll("[data-sec-tab]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        state.sec.tab = btn.getAttribute("data-sec-tab") || "overview";
        setSecTabUI();
        loadSecurityTab(token);
      });
    });
    await loadSecurityTab(token);
  }

  async function loadSecurityTab(token) {
    const box = A.$("#secBody");
    if (!box) return;
    box.innerHTML = loadingCard("در حال دریافت اطلاعات…");
    try {
      if (state.sec.tab === "overview") await secRenderOverview(box, token);
      else if (state.sec.tab === "events") await secRenderEvents(box, token);
      else if (state.sec.tab === "ip") await secRenderIp(box, token);
      else if (state.sec.tab === "users") await secRenderUsers(box, token);
      else await secRenderAudit(box, token);
    } catch (e) {
      if (!alive(token)) return;
      if (A.$("#secBody")) box.innerHTML = errorCard(e.message, '<button class="btn btn-soft" data-act="sec-refresh">' + A.icon("refresh") + " تلاش دوباره</button>");
    }
  }

  function severityPill(sev) {
    if (sev === "critical") return A.pill("بحرانی", "red");
    if (sev === "warning") return A.pill("هشدار", "amber");
    return A.pill("اطلاع", "blue");
  }
  function secEventRow(ev) {
    return '<tr><td class="tiny muted nowrap">' + A.esc(ev.created_at_fa || "-") + "</td>" +
      "<td>" + severityPill(ev.severity) + "</td>" +
      '<td><div class="cell-main">' + A.esc(ev.kind || "-") + '</div><div class="cell-sub">' + A.esc(ev.detail || "") + "</div></td>" +
      '<td class="mono tiny" dir="ltr">' + A.esc(ev.ip || "-") + "</td>" +
      "<td>" + (Number(ev.user_id) ? A.toFa(ev.user_id) : "-") + "</td>" +
      "<td>" + (ev.resolved ? A.pill("رسیدگی‌شده", "green") : A.pill("باز", "amber")) + "</td>" +
      '<td><button class="icon-btn sm" data-act="sec-event-resolve" data-eid="' + ev.id + '" data-resolved="' + (ev.resolved ? "0" : "1") + '" title="' + (ev.resolved ? "بازگشایی" : "رسیدگی شد") + '">' +
        A.icon(ev.resolved ? "refresh" : "check") + "</button></td></tr>";
  }

  async function secRenderOverview(box, token) {
    const data = await A.api("/api/admin/security/overview");
    if (!alive(token) || !A.$("#secBody")) return;
    state.sec.overview = data;
    const ev = data.events || {};
    const blocks = data.blocks || {};
    const recent = data.recent || [];
    box.innerHTML =
      '<div class="kpi-grid">' +
        '<div class="kpi"><div class="kpi-top"><div><div class="kpi-label">کل رویدادها</div><div class="kpi-value">' + A.fmtNum(ev.total || 0) + '</div></div><div class="kpi-icon blue">' + A.icon("shield") + "</div></div>" +
          '<div class="kpi-foot"><span class="tiny muted">' + A.toFa(ev.unresolved || 0) + " مورد رسیدگی‌نشده</span></div></div>" +
        '<div class="kpi"><div class="kpi-top"><div><div class="kpi-label">بحرانی باز</div><div class="kpi-value">' + A.fmtNum(ev.critical || 0) + '</div></div><div class="kpi-icon red">' + A.icon("alert") + "</div></div>" +
          '<div class="kpi-foot"><span class="tiny muted">' + A.toFa(ev.warning || 0) + " هشدار باز</span></div></div>" +
        '<div class="kpi"><div class="kpi-top"><div><div class="kpi-label">IPهای مسدود</div><div class="kpi-value">' + A.fmtNum(blocks.ip || 0) + '</div></div><div class="kpi-icon amber">' + A.icon("lock") + "</div></div>" +
          '<div class="kpi-foot"><span class="tiny muted">' + A.toFa(blocks.users || 0) + " کاربر مسدود</span></div></div>" +
        '<div class="kpi"><div class="kpi-top"><div><div class="kpi-label">تغییرات مدیریت</div><div class="kpi-value">' + A.fmtNum(data.audit || 0) + '</div></div><div class="kpi-icon violet">' + A.icon("history") + "</div></div>" +
          '<div class="kpi-foot"><span class="tiny muted">ثبت‌شده در لاگ</span></div></div>' +
      "</div>" +
      '<div class="grid grid-main"><div class="card"><div class="card-head bordered"><div class="card-title">' + A.icon("activity") + " رویدادهای اخیر</div>" +
        '<button class="btn btn-soft btn-sm" data-act="sec-tab" data-tab="events">همه رویدادها</button></div>' +
        (recent.length ? '<div class="table-wrap"><table class="table table-fix"><thead><tr><th>زمان</th><th>سطح</th><th>رویداد</th><th>IP</th><th>کاربر</th><th>وضعیت</th><th></th></tr></thead><tbody>' +
          recent.map(secEventRow).join("") + "</tbody></table></div>" : A.emptyState("shield", "رویدادی ثبت نشده", "رویدادهای امنیتی اینجا نمایش داده می‌شوند.")) + "</div>" +
        '<div><div class="card"><div class="card-head"><div class="card-title">' + A.icon("lock") + ' مسدودسازی‌های فعال</div></div><div class="card-body">' +
          '<div class="stat-mini"><div class="stat-mini-item"><span>IP</span><b>' + A.toFa(blocks.ip || 0) + '</b></div><div class="stat-mini-item"><span>کاربر</span><b>' + A.toFa(blocks.users || 0) + "</b></div></div>" +
          '<div class="row wrap mt-2"><button class="btn btn-soft btn-sm" data-act="sec-tab" data-tab="ip">مدیریت IP</button><button class="btn btn-soft btn-sm" data-act="sec-tab" data-tab="users">مدیریت کاربران</button></div></div></div>' +
        '<div class="card"><div class="card-head"><div class="card-title">' + A.icon("settings") + ' محدودیت درخواست وب</div></div><div class="card-body">' +
          '<div class="stat-mini"><div class="stat-mini-item"><span>حداکثر درخواست</span><b>' + A.toFa((data.web || {}).max || 0) + '</b></div>' +
          '<div class="stat-mini-item"><span>پنجره</span><b>' + A.toFa((data.web || {}).window || 0) + 's</b></div>' +
          '<div class="stat-mini-item"><span>مسدودی</span><b>' + A.toFa((data.web || {}).block || 0) + "s</b></div></div>" +
          '<a class="btn btn-soft btn-sm mt-2" href="/admin/security" data-admin-link="security">تنظیم محدودیت‌ها</a></div></div></div>' +
      "</div>";
  }

  async function secRenderEvents(box, token) {
    const data = await A.api("/api/admin/security/events?page=" + Number(state.sec.eventsPage || 1) + "&page_size=25&severity=" +
      encodeURIComponent(state.sec.evSeverity || "all") + "&resolved=" + encodeURIComponent(state.sec.evResolved || "all") +
      "&q=" + encodeURIComponent(state.sec.evQ || ""));
    if (!alive(token) || !A.$("#secBody")) return;
    const items = data.items || [];
    box.innerHTML =
      '<div class="card"><div class="card-body">' +
        '<form id="secEventsFilter" class="form-grid three">' +
          '<div class="field"><label class="label">جستجو</label><input class="input" name="q" value="' + A.esc(state.sec.evQ || "") + '" placeholder="نوع، جزئیات، IP یا شناسه"></div>' +
          '<div class="field"><label class="label">سطح</label><select class="input" name="severity">' + optionsHTML([["all", "همه"], ["critical", "بحرانی"], ["warning", "هشدار"], ["info", "اطلاع"]], state.sec.evSeverity || "all") + "</select></div>" +
          '<div class="field"><label class="label">وضعیت</label><select class="input" name="resolved">' + optionsHTML([["all", "همه"], ["0", "رسیدگی‌نشده"], ["1", "رسیدگی‌شده"]], state.sec.evResolved || "all") + "</select></div>" +
        "</form></div></div>" +
      '<div class="card"><div class="card-head bordered"><div class="card-title">' + A.icon("shield") + ' رویدادها</div><div class="card-sub">' + A.toFa(data.total || 0) + ' مورد</div></div>' +
        (items.length ? '<div class="table-wrap"><table class="table table-fix"><thead><tr><th>زمان</th><th>سطح</th><th>رویداد</th><th>IP</th><th>کاربر</th><th>وضعیت</th><th></th></tr></thead><tbody>' +
          items.map(secEventRow).join("") + "</tbody></table></div>" : A.emptyState("shield", "رویدادی یافت نشد", "فیلترها را تغییر دهید.")) +
        '<div class="card-foot">' + pagerHTML(state.sec.eventsPage, Math.ceil((data.total || 0) / 25)) + "</div></div>";
    bindSubmit("#secEventsFilter", function (form) {
      state.sec.evQ = form.elements.q.value.trim();
      state.sec.evSeverity = form.elements.severity.value;
      state.sec.evResolved = form.elements.resolved.value;
      state.sec.eventsPage = 1;
      loadSecurityTab(viewToken);
    });
  }

  async function secRenderIp(box, token) {
    const data = await A.api("/api/admin/security/ip-blocks?q=" + encodeURIComponent(state.sec.ipQ || ""));
    if (!alive(token) || !A.$("#secBody")) return;
    const items = data.items || [];
    state.sec.ipItems = items;
    const rows = items.map(function (b) {
      return '<tr><td class="mono" dir="ltr">' + A.esc(b.ip) + "</td>" +
        "<td>" + A.esc(b.reason || "-") + "</td>" +
        "<td>" + (b.active ? A.pill("فعال", "red") : A.pill("منقضی", "gray")) + "</td>" +
        '<td class="tiny muted">' + A.esc(b.created_at_fa || "-") + "</td>" +
        '<td><div class="cell-actions"><button class="icon-btn sm" data-act="sec-ip-edit" data-bid="' + b.id + '" title="تمدید">' + A.icon("edit") + "</button>" +
        '<button class="icon-btn sm danger" data-act="sec-ip-delete" data-bid="' + b.id + '" data-ip="' + A.esc(b.ip) + '" title="رفع مسدودی">' + A.icon("trash") + "</button></div></td></tr>";
    }).join("");
    box.innerHTML =
      '<div class="card"><div class="card-head bordered"><div><div class="card-title">' + A.icon("lock") + " بلاک آدرس IP</div>" +
        '<div class="card-sub">این بلاک‌ها قبل از محدودیت نرخ درخواست اعمال می‌شوند.</div></div></div>' +
        '<div class="card-body"><form id="ipBlockForm" class="form-grid three">' +
          '<div class="field"><label class="label">آدرس IP</label><input class="input mono" dir="ltr" name="ip" placeholder="203.0.113.10" required></div>' +
          '<div class="field"><label class="label">مدت (ساعت)</label><input class="input" type="number" min="0" step="0.5" name="hours" value="24"></div>' +
          '<div class="field"><label class="label">دلیل</label><input class="input" name="reason" placeholder="اختیاری"></div>' +
          '<div class="field"><button class="btn btn-primary btn-sm" type="submit">' + A.icon("plus") + " مسدود کردن</button></div>" +
        "</form></div></div>" +
      '<div class="card"><div class="card-head bordered"><div class="card-title">' + A.icon("lock") + ' فهرست بلاک‌ها</div><div class="card-sub">' + A.toFa(data.total || 0) + ' مورد</div></div>' +
        (items.length ? '<div class="table-wrap"><table class="table table-fix"><thead><tr><th>IP</th><th>دلیل</th><th>وضعیت</th><th>ایجاد</th><th></th></tr></thead><tbody>' + rows + "</tbody></table></div>"
          : A.emptyState("lock", "بلاکی ثبت نشده", "هیچ آدرس IP مسدودی وجود ندارد.")) + "</div>";
    bindSubmit("#ipBlockForm", async function (form) {
      const btn = form.querySelector('button[type="submit"]');
      A.setBusy(btn, true, "در حال ذخیره…");
      try {
        const res = await A.api("/api/admin/security/ip-blocks", {
          method: "POST",
          body: { ip: form.elements.ip.value.trim(), reason: form.elements.reason.value.trim(), hours: Number(form.elements.hours.value || 24) }
        });
        A.toast(res.message || "آدرس IP مسدود شد.", "success");
        form.reset();
        loadSecurityTab(viewToken);
      } catch (e) {
        A.toast(e.message, "error");
        A.setBusy(btn, false);
      }
    });
  }

  async function secRenderUsers(box, token) {
    const data = await A.api("/api/admin/security/user-blocks");
    if (!alive(token) || !A.$("#secBody")) return;
    const items = data.items || [];
    state.sec.userItems = items;
    const rows = items.map(function (b) {
      const name = ((b.first_name || "") + " " + (b.last_name || "")).trim() || (b.tg_username ? "@" + b.tg_username : "کاربر " + b.user_id);
      return '<tr><td class="num">' + A.toFa(b.user_id) + "</td>" +
        '<td><div class="cell-main">' + A.esc(name) + '</div><div class="cell-sub mono" dir="ltr">' + A.esc(b.phone_number || "") + "</div></td>" +
        "<td>" + A.esc(b.reason || "-") + "</td>" +
        "<td>" + (b.active ? A.pill("فعال", "red") : A.pill("منقضی", "gray")) + "</td>" +
        '<td class="tiny muted">' + A.esc(b.created_at_fa || "-") + "</td>" +
        '<td><button class="icon-btn sm danger" data-act="sec-user-delete" data-uid="' + b.user_id + '" data-name="' + A.esc(name) + '" title="رفع مسدودی">' + A.icon("trash") + "</button></td></tr>";
    }).join("");
    box.innerHTML =
      '<div class="grid grid-main"><div class="card"><div class="card-head bordered"><div><div class="card-title">' + A.icon("user") + " مسدودسازی کاربر</div>" +
        '<div class="card-sub">کاربر مسدود نمی‌تواند از وب‌اپ استفاده کند.</div></div></div>' +
        '<div class="card-body"><form id="userBlockForm">' +
          '<div class="form-grid two">' +
            '<div class="field"><label class="label">شناسه کاربر</label><input class="input mono" dir="ltr" type="number" name="user_id" min="1" required></div>' +
            '<div class="field"><label class="label">مدت (ساعت)</label><input class="input" type="number" min="0" step="0.5" name="hours" value="24"></div>' +
          "</div>" +
          '<div class="field"><label class="label">دلیل</label><input class="input" name="reason" placeholder="اختیاری"></div>' +
          '<button class="btn btn-danger btn-sm" type="submit">' + A.icon("lock") + " مسدود کردن کاربر</button>" +
        "</form></div></div>" +
        '<div class="card"><div class="card-head bordered"><div class="card-title">' + A.icon("users") + ' کاربران مسدود</div><div class="card-sub">' + A.toFa(data.total || 0) + ' مورد</div></div>' +
        (items.length ? '<div class="table-wrap"><table class="table table-fix"><thead><tr><th>شناسه</th><th>کاربر</th><th>دلیل</th><th>وضعیت</th><th>ایجاد</th><th></th></tr></thead><tbody>' + rows + "</tbody></table></div>"
          : A.emptyState("users", "کاربری مسدود نیست", "همه کاربران دسترسی دارند.")) + "</div></div>";
    bindSubmit("#userBlockForm", async function (form) {
      const btn = form.querySelector('button[type="submit"]');
      A.setBusy(btn, true, "در حال ذخیره…");
      try {
        const res = await A.api("/api/admin/security/user-blocks", {
          method: "POST",
          body: { user_id: Number(form.elements.user_id.value || 0), reason: form.elements.reason.value.trim(), hours: Number(form.elements.hours.value || 24) }
        });
        A.toast(res.message || "کاربر مسدود شد.", "success");
        form.reset();
        loadSecurityTab(viewToken);
      } catch (e) {
        A.toast(e.message, "error");
        A.setBusy(btn, false);
      }
    });
  }

  async function secRenderAudit(box, token) {
    const data = await A.api("/api/admin/audit-log?page=" + Number(state.sec.auditPage || 1) + "&page_size=30&q=" + encodeURIComponent(state.sec.auditQ || ""));
    if (!alive(token) || !A.$("#secBody")) return;
    const items = data.items || [];
    const rows = items.map(function (a) {
      return '<tr><td class="tiny muted nowrap">' + A.esc(a.created_at_fa || "-") + "</td>" +
        '<td class="mono tiny" dir="ltr">' + A.esc(a.admin_id ? "#" + a.admin_id : "-") + "</td>" +
        '<td><div class="cell-main mono" dir="ltr">' + A.esc(a.action || "-") + "</div><div class=\"cell-sub\">" + A.esc(a.details || "") + "</div></td>" +
        '<td class="tiny">' + A.esc(a.target_type || "-") + (a.target_id ? " / " + A.esc(a.target_id) : "") + "</td>" +
        '<td class="mono tiny" dir="ltr">' + A.esc(a.ip || "-") + "</td></tr>";
    }).join("");
    box.innerHTML =
      '<div class="card"><div class="card-body"><form id="auditFilter" class="inline-form">' +
        '<input class="input" name="q" value="' + A.esc(state.sec.auditQ || "") + '" placeholder="جستجو در عملیات، جزئیات، IP">' +
        '<button class="btn btn-soft btn-sm" type="submit">' + A.icon("search") + " جستجو</button></form></div></div>" +
      '<div class="card"><div class="card-head bordered"><div class="card-title">' + A.icon("history") + " لاگ تغییرات مدیریت</div><div class=\"card-sub\">" + A.toFa(data.total || 0) + " رکورد</div></div>" +
        (items.length ? '<div class="table-wrap"><table class="table table-fix"><thead><tr><th>زمان</th><th>مدیر</th><th>عملیات</th><th>هدف</th><th>IP</th></tr></thead><tbody>' + rows + "</tbody></table></div>"
          : A.emptyState("history", "رکوردی وجود ندارد", "تغییرات مدیریت اینجا ثبت می‌شوند.")) +
        '<div class="card-foot">' + pagerHTML(state.sec.auditPage, Math.ceil((data.total || 0) / 30)) + "</div></div>";
    bindSubmit("#auditFilter", function (form) {
      state.sec.auditQ = form.elements.q.value.trim();
      state.sec.auditPage = 1;
      loadSecurityTab(viewToken);
    });
  }

  async function resolveSecurityEvent(eid, resolved) {
    try {
      const res = await A.api("/api/admin/security/events/" + eid + "/resolve", { method: "POST", body: { resolved: !!resolved } });
      A.toast(res.message || "وضعیت رویداد ذخیره شد.", "success");
      loadSecurityTab(viewToken);
    } catch (e) { A.toast(e.message, "error"); }
  }

  async function editIpBlock(bid) {
    const row = (state.sec.ipItems || []).find(function (x) { return Number(x.id) === Number(bid); });
    if (!row) return;
    const value = await A.prompt({ title: "تمدید بلاک IP", label: "مدت جدید (ساعت) برای " + row.ip, value: "24", confirmText: "ذخیره" });
    if (value === null) return;
    try {
      const res = await A.api("/api/admin/security/ip-blocks/" + bid, {
        method: "PUT", body: { ip: row.ip, reason: row.reason || "", hours: Number(A.toEn(value).replace(/[^\d.]/g, "") || 24) }
      });
      A.toast(res.message || "بلاک به‌روزرسانی شد.", "success");
      loadSecurityTab(viewToken);
    } catch (e) { A.toast(e.message, "error"); }
  }

  async function removeIpBlock(bid, ip) {
    const ok = await A.confirm({ title: "رفع مسدودی IP", message: "محدودیت آدرس " + (ip || "") + " برداشته شود؟", confirmText: "رفع مسدودی" });
    if (!ok) return;
    try {
      const res = await A.api("/api/admin/security/ip-blocks/" + bid, { method: "DELETE" });
      A.toast(res.message || "رفع مسدودی انجام شد.", "success");
      loadSecurityTab(viewToken);
    } catch (e) { A.toast(e.message, "error"); }
  }

  async function removeUserBlock(uid, name) {
    const ok = await A.confirm({ title: "رفع مسدودی کاربر", message: "محدودیت «" + (name || "") + "» برداشته شود؟", confirmText: "رفع مسدودی" });
    if (!ok) return;
    try {
      const res = await A.api("/api/admin/security/user-blocks/" + uid, { method: "DELETE" });
      A.toast(res.message || "رفع مسدودی انجام شد.", "success");
      loadSecurityTab(viewToken);
    } catch (e) { A.toast(e.message, "error"); }
  }

  /* ------------------------------------------------------------------
     مسیرها و رویدادها
  ------------------------------------------------------------------ */
  const PAGES = {
    overview: { title: "داشبورد مدیریت", sub: "نمای کلی کاربران، پرداخت‌ها و سامانه", nav: "overview", render: renderOverview },
    users: { title: "کاربران", sub: "مدیریت کاربران و اشتراک‌ها", nav: "users", render: renderUsers },
    payments: { title: "پرداخت‌ها", sub: "بررسی تراکنش‌های ربات و وب‌اپ", nav: "payments", render: renderPayments },
    support: { title: "پشتیبانی", sub: "مکالمات و پاسخ به کاربران", nav: "support", render: renderSupport },
    plans: { title: "پلن‌ها و پرداخت", sub: "قیمت، شبکه‌ها، تخفیف و اشتراک رایگان", nav: "plans", render: renderPlans },
    strategies: { title: "استراتژی‌ها و راهنما", sub: "کاتالوگ تحلیل و راهنمای استراتژی‌ها", nav: "strategies", render: renderStrategies },
    exchanges: { title: "صرافی‌ها", sub: "منابع داده و اولویت پردازش", nav: "exchanges", render: renderExchanges },
    ticker: { title: "نوار قیمت زنده", sub: "قیمت و تغییر ۲۴ ساعتهٔ نمادها در وب‌اپ کاربر", nav: "ticker", render: renderTicker },
    messages: { title: "ارسال پیام", sub: "ارتباط با کاربران وب‌اپ", nav: "messages", render: renderMessages },
    pages: { title: "صفحات ثابت", sub: "مدیریت محتوای ثابت سایت", nav: "pages", render: renderPages },
    education: { title: "آموزش و مقالات", sub: "مقالات، دسته‌بندی و سئو محتوا", nav: "education", render: renderEducation },
    menus: { title: "منوهای سایت", sub: "منوی هدر و پابرگ", nav: "menus", render: renderMenus },
    media: { title: "کتابخانه رسانه", sub: "بارگذاری و مدیریت تصاویر", nav: "media", render: renderMedia },
    seo: { title: "سئو و تنظیمات فنی", sub: "متا، Open Graph، sitemap و robots", nav: "seo", render: renderSeo },
    appearance: { title: "ظاهر و چیدمان", sub: "عرض محتوا، سایدبار و تاریخ شمسی", nav: "appearance", render: renderAppearance },
    registration: { title: "ثبت‌نام و فیلدها", sub: "فرم ثبت‌نام و فیلدهای سفارشی", nav: "registration", render: renderRegistration },
    "web-users": { title: "کاربران وب‌اپ", sub: "حساب‌های ثبت‌نام‌شده و دسترسی", nav: "web-users", render: renderWebUsers },
    otp: { title: "ورود با پیامک", sub: "تنظیمات ippanel و کد یک‌بارمصرف", nav: "otp", render: renderOtp },
    "security-center": { title: "مرکز امنیت", sub: "رویدادها، بلاک‌ها و لاگ مدیریت", nav: "security-center", render: renderSecurityCenter },
    branding: { title: "برندینگ و ظاهر سایت", sub: "نام، شعار، لوگو و متون ورود", nav: "branding", render: renderBranding },
    maintenance: { title: "حالت تعمیر", sub: "هشدار نگهداری در تمام صفحات", nav: "maintenance", render: renderMaintenance },
    "api-keys": { title: "کلیدهای API", sub: "تأیید خودکار تراکنش‌ها", nav: "api-keys", render: renderApiKeys },
    security: { title: "امنیت و محدودیت‌ها", sub: "محدودیت ربات و وب‌اپ و رویدادها", nav: "security", render: renderSecurity },
    backups: { title: "بکاپ و بازیابی", sub: "بکاپ کامل یا بخشی با تاریخچه", nav: "backups", render: renderBackups },
    settings: { title: "تنظیمات سامانه", sub: "پیام‌ها و تنظیمات پیش‌فرض", nav: "settings", render: renderSettings }
  };

  function routeName() {
    let path = location.pathname.replace(/\/+$/, "");
    if (!path || path === "/admin") return "overview";
    if (path.indexOf("/admin/") === 0) return path.slice(7).split("/")[0];
    return "overview";
  }

  function route() {
    let name = routeName();
    if (!PAGES[name]) {
      name = "overview";
      history.replaceState({}, "", "/admin");
    }
    applyDeepLink();
    const meta = PAGES[name];
    A.setPage(meta.title, meta.sub);
    markNavDeep(meta.nav);
    const token = beginView();
    meta.render(token);
  }

  function openAdminLink(href) {
    if (!href || href.charAt(0) !== "/") return;
    if (href === location.pathname + (location.search || "")) return;
    A.go(href);
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  document.addEventListener("click", function (e) {
    const nav = e.target.closest("[data-admin-link]");
    if (nav) {
      if (e.target.closest("a[data-link]")) return;
      if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
      e.preventDefault();
      openAdminLink(nav.getAttribute("href"));
      return;
    }
    const el = e.target.closest("[data-act]");
    if (!el) return;
    const act = el.getAttribute("data-act");
    const uid = Number(el.getAttribute("data-uid") || 0);
    const pid = Number(el.getAttribute("data-pid") || 0);
    if (act === "refresh") { refreshCurrent(); return; }
    if (act === "user-open") { openUser(uid); return; }
    if (act === "user-message") { sendUserMessage(uid, el.getAttribute("data-name") || ""); return; }
    if (act === "user-password") { openUserPassword(uid); return; }
    if (act === "user-delete") { deleteUser(uid, el.getAttribute("data-name") || ""); return; }
    if (act === "user-sub-add") { userSubscription(uid, "add"); return; }
    if (act === "user-sub-reduce") { userSubscription(uid, "reduce"); return; }
    if (act === "user-sub-remove") { userSubscription(uid, "remove"); return; }
    if (act === "payment-view") { openPayment(pid); return; }
    if (act === "payment-approve") { paymentActionModal(pid, "approve"); return; }
    if (act === "payment-reject") { paymentActionModal(pid, "reject"); return; }
    if (act === "payment-suspend") { paymentActionModal(pid, "suspend"); return; }
    if (act === "payment-manual") { openManualPayment(); return; }
    if (act === "brand-logo-pick") {
      const f = A.$("#brandLogoInput");
      if (f) f.click();
      return;
    }
    if (act === "brand-logo-delete") {
      A.confirm({ title: "حذف لوگو", message: "لوگوی فعلی حذف و آیکون پیش‌فرض نمایش داده می‌شود.", danger: true, confirmText: "حذف" }).then(function (ok) {
        if (!ok) return;
        A.api("/api/admin/branding/logo", { method: "DELETE" }).then(function (res) {
          A.toast(res.message || "لوگو حذف شد.", "success");
          refreshCurrent();
          A.applyPublicConfig();
        }).catch(function (e) { A.toast(e.message, "error"); });
      });
      return;
    }
    if (act === "api-keys-save") { saveApiKeys(); return; }
    if (act === "api-key-clear") { clearApiKey(el.getAttribute("data-key") || ""); return; }
    if (act === "support-open") { openSupportTicket(Number(el.getAttribute("data-tid"))); return; }
    if (act === "support-refresh") { refreshCurrent(); return; }
    if (act === "support-send") { sendSupportReply(Number(el.getAttribute("data-tid"))); return; }
    if (act === "support-status") { toggleSupportStatus(Number(el.getAttribute("data-tid")), el.getAttribute("data-status") || "closed"); return; }
    if (act === "plan-tab") {
      state.plans.tab = el.getAttribute("data-plan") === "webapp" ? "webapp" : "bot";
      renderPlanPanel(viewToken);
      return;
    }
    if (act === "plan-net-save") { savePlanNetwork(el.getAttribute("data-plan") || "bot", el.getAttribute("data-key") || ""); return; }
    if (act === "dest-save") { savePlanDestination(el.getAttribute("data-kind") || "card", Number(el.getAttribute("data-id") || 0)); return; }
    if (act === "dest-delete") { deletePlanDestination(el.getAttribute("data-kind") || "card", Number(el.getAttribute("data-id") || 0)); return; }
    if (act === "free-pick-all") {
      A.$$("#freeStrategyPicker [data-free-strategy]").forEach(function (x) { x.checked = true; });
      return;
    }
    if (act === "free-pick-none") {
      A.$$("#freeStrategyPicker [data-free-strategy]").forEach(function (x) { x.checked = false; });
      return;
    }
    if (act === "backups-refresh") { refreshCurrent(); return; }
    if (act === "backup-create") { createBackup(); return; }
    if (act === "backup-restore") { restoreBackup(Number(el.getAttribute("data-bid"))); return; }
    if (act === "backup-delete") { deleteBackup(Number(el.getAttribute("data-bid"))); return; }
    if (act === "backup-upload") { uploadBackup(); return; }
    if (act === "security-refresh") { refreshCurrent(); return; }
    if (act === "security-clear-ip") { clearSecurityIp(el.getAttribute("data-ip") || ""); return; }
    if (act === "security-clear-ip-manual") {
      const input = A.$("#clearIpInput");
      const ip = input ? input.value.trim() : "";
      if (!ip) { A.toast("آدرس IP را وارد کنید.", "error"); return; }
      clearSecurityIp(ip);
      return;
    }
    if (act === "page-new") { openEditor("page", 0); return; }
    if (act === "page-edit") { openEditor("page", pid); return; }
    if (act === "page-delete") { deleteContentItem("page", pid, el.getAttribute("data-title") || ""); return; }
    if (act === "post-new") { openEditor("post", 0); return; }
    if (act === "post-edit") { openEditor("post", pid); return; }
    if (act === "post-delete") { deleteContentItem("post", pid, el.getAttribute("data-title") || ""); return; }
    if (act === "categories-manage") { openCategoryManager(); return; }
    if (act === "menu-create") { createMenu(el.getAttribute("data-loc") || "header"); return; }
    if (act === "menu-manage") { openMenuManager(Number(el.getAttribute("data-mid") || 0)); return; }
    if (act === "menu-delete") { deleteMenu(Number(el.getAttribute("data-mid") || 0), el.getAttribute("data-name") || ""); return; }
    if (act === "media-refresh") { refreshCurrent(); return; }
    if (act === "media-pick") {
      const file = A.$("#mediaFile");
      if (file) file.click();
      return;
    }
    if (act === "media-upload") { uploadMedia(); return; }
    if (act === "media-copy") { A.copy(location.origin + (el.getAttribute("data-path") || "")); return; }
    if (act === "media-edit") { openMediaMeta(Number(el.getAttribute("data-mid") || 0)); return; }
    if (act === "media-delete") { deleteMedia(Number(el.getAttribute("data-mid") || 0), el.getAttribute("data-name") || ""); return; }
    if (act === "reg-field-add") { openRegFieldForm(null); return; }
    if (act === "reg-field-edit") {
      const f = ((state.registration || {}).items || []).find(function (x) { return Number(x.id) === Number(el.getAttribute("data-fid")); });
      if (f) openRegFieldForm(f);
      return;
    }
    if (act === "reg-field-delete") { deleteRegField(Number(el.getAttribute("data-fid") || 0), el.getAttribute("data-name") || ""); return; }
    if (act === "webusers-refresh") { refreshCurrent(); return; }
    if (act === "webuser-edit") { openWebUserEditor(uid); return; }
    if (act === "otp-toggle-key") {
      const input = A.$("#otpApiKey");
      if (input) input.type = input.type === "password" ? "text" : "password";
      return;
    }
    if (act === "sec-refresh") { loadSecurityTab(viewToken); return; }
    if (act === "sec-tab") {
      state.sec.tab = el.getAttribute("data-tab") || "overview";
      setSecTabUI();
      loadSecurityTab(viewToken);
      return;
    }
    if (act === "sec-event-resolve") {
      resolveSecurityEvent(Number(el.getAttribute("data-eid") || 0), el.getAttribute("data-resolved") === "1");
      return;
    }
    if (act === "sec-ip-edit") { editIpBlock(Number(el.getAttribute("data-bid") || 0)); return; }
    if (act === "sec-ip-delete") { removeIpBlock(Number(el.getAttribute("data-bid") || 0), el.getAttribute("data-ip") || ""); return; }
    if (act === "sec-user-delete") { removeUserBlock(uid, el.getAttribute("data-name") || ""); return; }
    if (act === "pay-service") {
      state.payments.service = el.getAttribute("data-service") || "all";
      state.payments.page = 1;
      A.$$("#payServiceTabs .seg").forEach(function (b) { b.classList.toggle("active", b === el); });
      loadPaymentsTable(viewToken);
      return;
    }
    if (act === "page") {
      let page = pageNumber(el.getAttribute("data-page"));
      if (routeName() === "users") { state.users.page = page; loadUsersTable(viewToken); }
      else if (routeName() === "payments") { state.payments.page = page; loadPaymentsTable(viewToken); }
      else if (routeName() === "strategies") { state.strategies.page = page; loadStrategiesTable(viewToken); }
      else if (routeName() === "support") { state.support.page = page; loadSupportList(viewToken); }
      else if (routeName() === "backups") { state.backups.page = page; loadBackups(viewToken); }
      else if (routeName() === "education") { state.content.postsPage = page; loadPosts(viewToken); }
      else if (routeName() === "media") { state.media.page = page; loadMedia(viewToken); }
      else if (routeName() === "web-users") { state.webusers.page = page; loadWebUsers(viewToken); }
      else if (routeName() === "plans") { state.plans.claims.page = page; loadPlanClaims(state.plans.claims.plan || state.plans.tab, viewToken); }
      else if (routeName() === "security-center") {
        if (state.sec.tab === "audit") state.sec.auditPage = page;
        else state.sec.eventsPage = page;
        loadSecurityTab(viewToken);
      }
      return;
    }
    if (act === "strategy-guide") { openGuide(el.getAttribute("data-key")); return; }
    if (act === "strategy-guide-delete") { deleteGuide(el.getAttribute("data-key"), el.getAttribute("data-title") || ""); return; }
    if (act === "exchange-add") { openExchangeForm(0); return; }
    if (act === "exchange-edit") { openExchangeForm(Number(el.getAttribute("data-id"))); return; }
    if (act === "exchange-delete") { deleteExchange(Number(el.getAttribute("data-id")), el.getAttribute("data-name") || ""); return; }
    if (act === "exchange-sync") { syncExchanges(); return; }
    if (act === "msg-tab") {
      const text = A.$("#msgText");
      if (text) state.message.text = text.value;
      state.message.tab = el.getAttribute("data-tab") || "single";
      const card = A.$("#msgComposerCard");
      if (card) { card.innerHTML = composerHTML(); bindComposer(viewToken); }
      return;
    }
    if (act === "msg-user-clear") {
      state.message.user = null;
      const selected = A.$("#msgSelected");
      if (selected) { selected.hidden = true; selected.innerHTML = ""; }
      A.$$("#msgUserResults [data-pick-user]").forEach(function (x) { x.classList.remove("active"); });
      return;
    }
    if (act === "msg-cancel") { cancelJob(el.getAttribute("data-job")); return; }
    if (act === "settings-reset") {
      const field = el.getAttribute("data-field");
      const defs = (state.settings && state.settings.defaults) || {};
      const form = A.$("#adminSettingsForm");
      if (form && field && defs[field] != null) {
        const input = form.querySelector('[name="' + field + '"]');
        if (input) input.value = defs[field];
        A.toast("مقدار پیش‌فرض بازگردانده شد.", "info", 1800);
      }
      return;
    }
    if (act === "copy-site") {
      const input = A.$("#siteAddress");
      A.copy(input ? input.value : "");
      return;
    }
  });

  document.addEventListener("change", async function (e) {
    const access = e.target.closest("[data-user-access]");
    if (access) {
      const uid = parseInt(access.getAttribute("data-user-access"), 10);
      const enabled = !!access.checked;
      try {
        const res = await A.api("/api/admin/users/" + uid + "/access", { method: "POST", body: { enabled: enabled } });
        A.toast(res.message || "دسترسی به‌روزرسانی شد.", "success");
      } catch (err) {
        access.checked = !enabled;
        A.toast(err.message, "error");
      }
      return;
    }
    const strategy = e.target.closest("[data-strategy-toggle]");
    if (strategy) {
      const key = strategy.getAttribute("data-strategy-toggle");
      const active = !!strategy.checked;
      try {
        await A.api("/api/admin/strategies/" + encodeURIComponent(key) + "/toggle", { method: "POST", body: { active: active } });
        A.toast(active ? "استراتژی فعال شد." : "استراتژی غیرفعال شد.", "success", 2000);
      } catch (err) {
        strategy.checked = !active;
        A.toast(err.message, "error");
      }
      return;
    }
    const flag = e.target.closest("[data-exchange-flag]");
    if (flag) {
      const id = parseInt(flag.getAttribute("data-exchange-flag"), 10);
      const field = flag.getAttribute("data-field");
      const value = !!flag.checked;
      try {
        await A.api("/api/admin/exchanges/" + id + "/flag", { method: "POST", body: { field: field, value: value } });
        A.toast("وضعیت صرافی به‌روزرسانی شد.", "success", 2000);
      } catch (err) {
        flag.checked = !value;
        A.toast(err.message, "error");
      }
    }
  });

  async function refreshSideStatus() {
    const side = A.$("#adminSideStatus");
    if (!side) return;
    try {
      const data = await A.api("/api/admin/overview");
      const stats = data.stats || {};
      const exchanges = data.exchanges || [];
      const activeEx = exchanges.filter(function (e) { return Number(e.is_active) === 1; }).length;
      side.textContent = A.toFa(stats.web_users || 0) + " کاربر وب‌اپ • " + A.toFa(activeEx) + " صرافی فعال";
      setUsersBadge(stats);
    } catch (e) {
      side.textContent = "وضعیت سامانه در دسترس نیست";
    }
  }

  window.addEventListener("popstate", function () { route(); });
  window.addEventListener("beforeunload", stopJobPolling);

  async function boot() {
    A.setupShell();
    try {
      const session = await A.api("/api/auth/session", { redirectOn401: false });
      if (!session || !session.authenticated) {
        location.href = "/login?admin=1";
        return;
      }
      if (!session.user || !session.user.is_admin) {
        location.href = "/";
        return;
      }
      state.user = session.user;
      A.fillUser(session.user);
    } catch (e) {
      location.href = "/login?admin=1";
      return;
    }
    route();
    refreshSideStatus();
  }

  boot();
})();
