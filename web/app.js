const CATEGORIES = ["ทั้งหมด", "เทคโนโลยี", "ภาษา", "ดนตรี", "ออกแบบ", "กีฬา", "ศิลปะ", "วิชาการ", "อื่น ๆ"];
const SLOT_DAY_LABELS = [["mon", "จันทร์"], ["tue", "อังคาร"], ["wed", "พุธ"], ["thu", "พฤหัส"], ["fri", "ศุกร์"], ["sat", "เสาร์"], ["sun", "อาทิตย์"]];
const SLOT_PERIOD_LABELS = [["m", "เช้า", "06–12"], ["a", "บ่าย", "12–18"], ["e", "เย็น", "18–24"]];
const LEVEL_OPTIONS = ["เริ่มต้น", "ฝึกฝน", "ขั้นสูง", "ทุกระดับ"];
function slotPicker(selected = []) {
  const chosen = new Set(selected);
  return `<div class="slot-grid" role="group" aria-label="เลือกช่วงเวลา"><span></span>${SLOT_PERIOD_LABELS.map(([, label, hours]) => `<span class="slot-head"><span>${label}</span><small>${hours}</small></span>`).join("")}${SLOT_DAY_LABELS.map(([day, dayLabel]) => `<span class="slot-day">${dayLabel}</span>${SLOT_PERIOD_LABELS.map(([p, pLabel, hours]) => `<label class="slot-cell"><input type="checkbox" data-slot="${day}-${p}" ${chosen.has(`${day}-${p}`) ? "checked" : ""} aria-label="${dayLabel} ${pLabel} ${hours}"><span></span></label>`).join("")}`).join("")}</div>`;
}
function pickedSlots(form) { return [...form.querySelectorAll("input[data-slot]:checked")].map(input => input.dataset.slot); }
function formatOfferSlots(slots = []) {
  const selected = new Set(slots);
  if (!selected.size) return "";
  if (SLOT_DAY_LABELS.every(([day]) => SLOT_PERIOD_LABELS.every(([period]) => selected.has(`${day}-${period}`)))) {
    return "ทุกวัน 06:00–24:00";
  }
  const daysFor = period => SLOT_DAY_LABELS.filter(([day]) => selected.has(`${day}-${period}`)).map(([, label]) => label);
  const formatDays = days => {
    if (days.length === 7) return "ทุกวัน";
    if (days.length === 5 && days.every((day, index) => day === SLOT_DAY_LABELS[index][1])) return "วันธรรมดา";
    if (days.length === 2 && days[0] === "เสาร์" && days[1] === "อาทิตย์") return "เสาร์–อาทิตย์";
    return days.join(", ");
  };
  return SLOT_PERIOD_LABELS.map(([period, label, hours]) => {
    const days = daysFor(period);
    return days.length ? `${formatDays(days)} ${label} (${hours})` : "";
  }).filter(Boolean).join(" · ");
}
function offerAvailabilityText(offer) {
  return formatOfferSlots(offer.slots) || offer.availability || "นัดหมายล่วงหน้า";
}
const STATUS_NAMES = {
  pending: "รอผู้สอนตอบรับ", accepted: "นัดหมายแล้ว", awaiting_confirmation: "รอยืนยันกิจกรรม",
  disputed: "รอตรวจสอบ", completed: "สำเร็จแล้ว", rejected: "ไม่ได้รับคำขอ",
  cancelled: "ยกเลิกแล้ว", cancelled_no_show: "ไม่มาตามนัด"
};
const state = { user: null, offers: [], category: "ทั้งหมด", query: "", sortMatch: false, view: "explore", dashboard: null, admin: null, adminMemberQuery: "", adminOfferQuery: "", adminSearchTimer: null, adminSearchSequence: 0, authMode: "login", avatarDraft: null, avatarCleared: false, notifications: [], notificationsInitialized: false, notificationTimer: null, chatTimer: null, chatRequestId: null, calendarDate: new Date(new Date().getFullYear(), new Date().getMonth(), 1), calendarSelected: null };
const $ = (selector, scope = document) => scope.querySelector(selector);
const $$ = (selector, scope = document) => [...scope.querySelectorAll(selector)];
const esc = value => String(value ?? "").replace(/[&<>"']/g, character => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[character]));

async function api(path, options = {}) {
  const response = await fetch(path, {
    method: options.method || "GET",
    credentials: "same-origin",
    headers: options.body ? { "Content-Type": "application/json" } : {},
    body: options.body ? JSON.stringify(options.body) : undefined
  });
  let data = {};
  try { data = await response.json(); } catch { data = {}; }
  if (!response.ok) {
    const error = new Error(data.error || "ทำรายการไม่สำเร็จ");
    error.status = response.status;
    throw error;
  }
  return data;
}

function toast(message, type = "success") {
  const element = document.createElement("div");
  element.className = `toast ${type === "error" ? "error" : ""}`;
  element.textContent = message;
  $("#toast-region").append(element);
  window.setTimeout(() => element.remove(), 3400);
}

function initials(name) {
  const parts = String(name || "?").trim().split(/\s+/);
  return Array.from(parts[0] || "?")[0] || "?";
}

function miniAvatar(name, url, frame, sticker, size = "") {
  const inner = url ? `<img src="${esc(url)}" alt="" loading="lazy">` : esc(initials(name));
  return `<span class="mini-avatar ${size} ${esc(frame || "")}" data-sticker="${esc(sticker || "")}">${inner}</span>`;
}

function formatDuration(minutes) {
  const hours = minutes / 60;
  return Number.isInteger(hours) ? `${hours} ชั่วโมง` : `${hours.toFixed(1)} ชั่วโมง`;
}

function formatDate(value, includeTime = true) {
  try {
    return new Intl.DateTimeFormat("th-TH", includeTime
      ? { dateStyle: "medium", timeStyle: "short" }
      : { dateStyle: "medium" }).format(new Date(value));
  } catch { return "เวลานัดหมาย"; }
}

function bangkokDateTimeValue(date) {
  return new Date(date.getTime() + 7 * 60 * 60 * 1000).toISOString().slice(0, 16);
}

function modal(content, wide = false) {
  const backdrop = $("#modal-backdrop");
  const box = $("#modal-content");
  box.className = `modal${wide ? " wide" : ""}`;
  box.innerHTML = `<button class="modal-close" type="button" data-close-modal aria-label="ปิด">×</button>${content}`;
  backdrop.classList.remove("hidden");
  document.body.style.overflow = "hidden";
  window.setTimeout(() => $("input,select,textarea", box)?.focus(), 40);
}

function closeModal() {
  $("#modal-backdrop").classList.add("hidden");
  document.body.style.overflow = "";
  if (state.chatTimer) window.clearInterval(state.chatTimer);
  state.chatTimer = null;
  state.chatRequestId = null;
}

function setHeader() {
  const authenticated = Boolean(state.user);
  $("#top-actions").classList.toggle("hidden", authenticated);
  $("#user-actions").classList.toggle("hidden", !authenticated);
  $$(".auth-only").forEach(item => item.classList.toggle("hidden", !authenticated));
  if (authenticated) {
    $("#user-avatar").innerHTML = state.user.avatar_url
      ? `<img src="${esc(state.user.avatar_url)}" alt="">`
      : esc(initials(state.user.full_name));
    $("#user-avatar").className = `avatar ${state.user.frame || ""}`.trim();
    $("#user-avatar").dataset.sticker = state.user.sticker || "";
    $("#user-short-name").textContent = state.user.full_name.split(/\s+/)[0];
  }
  $("#nav-count").classList.toggle("hidden", !state.dashboard?.stats?.active_requests);
  if (state.dashboard?.stats?.active_requests) $("#nav-count").textContent = state.dashboard.stats.active_requests;
  if (state.user?.role === "admin" && !$("#admin-nav")) {
    $(".main-nav").insertAdjacentHTML("beforeend", '<button class="nav-link" id="admin-nav" data-view="admin">ผู้ดูแล</button>');
  } else if (state.user?.role !== "admin") {
    $("#admin-nav")?.remove();
  }
  if (authenticated && !state.notificationTimer) {
    refreshNotifications(true);
    state.notificationTimer = window.setInterval(() => refreshNotifications(false), 30000);
  } else if (!authenticated && state.notificationTimer) {
    window.clearInterval(state.notificationTimer);
    state.notificationTimer = null;
    state.notifications = [];
    updateNotificationBadge();
  }
}

function updateNotificationBadge() {
  const count = state.notifications.filter(item => !item.is_read).length;
  const badge = $("#notification-count");
  if (!badge) return;
  badge.textContent = count > 99 ? "99+" : String(count);
  badge.classList.toggle("hidden", count === 0);
}

async function refreshNotifications(initial = false) {
  if (!state.user) return;
  try {
    const previous = new Set(state.notifications.map(item => item.id));
    const data = await api("/api/notifications");
    state.notifications = data.notifications || [];
    updateNotificationBadge();
    if (!initial && state.notificationsInitialized) {
      state.notifications.filter(item => !item.is_read && !previous.has(item.id)).forEach(item => toast(item.title));
    }
    state.notificationsInitialized = true;
    if (!$("#modal-backdrop").classList.contains("hidden") && $("#notification-list")) renderNotificationModal();
  } catch { /* notification polling should not interrupt the page */ }
}

function notificationItem(item) {
  return `<button class="notification-item ${item.is_read ? "read" : "unread"}" data-action="open-notification" data-request="${item.request_id || ""}" data-id="${item.id}"><span class="notification-dot"></span><span><strong>${esc(item.title)}</strong><small>${esc(item.message)}</small><em>${formatDate(item.created_at)}</em></span></button>`;
}

function renderNotificationModal() {
  const list = $("#notification-list");
  if (!list) return;
  list.innerHTML = state.notifications.length
    ? state.notifications.map(notificationItem).join("")
    : '<div class="request-empty">ยังไม่มีการแจ้งเตือน</div>';
}

async function openNotifications() {
  await refreshNotifications(true);
  modal(`<div class="eyebrow">ข่าวสารของบัญชี</div><h2 id="modal-title">การแจ้งเตือน</h2><p class="modal-subtitle">คำขอเรียน ข้อความใหม่ และการนัดหมายใกล้ถึงเวลา</p><div id="notification-list" class="notification-list"></div><div class="form-actions"><button class="btn btn-quiet" data-close-modal>ปิด</button><button class="btn btn-outline" data-action="read-all-notifications">ทำเครื่องหมายว่าอ่านแล้ว</button></div>`, true);
  renderNotificationModal();
}

function renderCategories() {
  $("#category-row").innerHTML = CATEGORIES.map(category => `<button class="category-chip ${state.category === category ? "selected" : ""}" data-category="${esc(category)}">${esc(category)}</button>`).join("");
}

function renderOffers() {
  let offers = [...state.offers];
  if (state.sortMatch) offers.sort((a, b) => (b.match_score || 0) - (a.match_score || 0) || b.id - a.id);
  $("#offer-count").textContent = state.offers.length;
  $("#match-banner").classList.toggle("hidden", !state.user || !state.user.interests?.length);
  const grid = $("#offer-grid");
  const empty = $("#empty-state");
  if (!offers.length) {
    grid.innerHTML = "";
    empty.classList.remove("hidden");
    return;
  }
  empty.classList.add("hidden");
  grid.innerHTML = offers.map((offer, index) => {
    const avatarStyle = index % 3 === 1 ? "v2" : (index % 3 === 2 ? "v3" : "");
    const match = offer.matched && state.user?.interests?.length;
    const providerAvatar = offer.avatar_url
      ? `<img src="${esc(offer.avatar_url)}" alt="">`
      : esc(initials(offer.provider_name));
    return `<article class="offer-card" data-offer="${offer.id}" tabindex="0" role="button" aria-label="ดูรายละเอียด ${esc(offer.title)}">
      <div class="offer-topline"><span class="category-label">${esc(offer.category)}</span>${match ? `<span class="match-label">✦ ตรงใจ ${offer.match_score}%</span>` : ""}</div>
      <h3 class="offer-title">${esc(offer.title)}</h3><p class="offer-desc">${esc(offer.description)}</p>
      <div class="offer-meta"><span class="level-pill">${esc(offer.level)}</span><span>◷ ${esc(offerAvailabilityText(offer))}</span></div>
      ${offer.score_detail && state.user?.role === "member" ? `<div class="score-detail" title="คะแนนรวม = 0.40·ทักษะ + 0.30·เวลา + 0.15·ระดับ + 0.15·ชื่อเสียง">ทักษะ ${offer.score_detail.skill} · เวลา ${offer.score_detail.time} · ระดับ ${offer.score_detail.level} · ชื่อเสียง ${offer.score_detail.rep}</div>` : ""}
      <div class="offer-bottom"><button type="button" class="provider provider-btn" data-profile="${offer.user_id}" aria-label="ดูโปรไฟล์ ${esc(offer.provider_name)}"><span class="provider-avatar ${avatarStyle} ${esc(offer.frame || "")}" data-sticker="${esc(offer.sticker || "")}">${providerAvatar}</span><span><span class="provider-name">${esc(offer.provider_name)}</span><span class="provider-faculty">${esc(offer.faculty || "นักศึกษา")}</span></span></button><span class="offer-rating">${offer.review_count ? `★ <strong>${Number(offer.rating).toFixed(1)}</strong> (${offer.review_count})` : "ผู้สอนใหม่"}</span></div>
    </article>`;
  }).join("");
}

async function loadOffers() {
  const params = new URLSearchParams();
  if (state.query) params.set("q", state.query);
  if (state.category !== "ทั้งหมด") params.set("category", state.category);
  try {
    const data = await api(`/api/offers?${params.toString()}`);
    state.offers = data.offers;
    renderOffers();
  } catch (error) {
    $("#offer-grid").innerHTML = `<div class="loading-card">${esc(error.message)}</div>`;
  }
}

async function refreshDashboard() {
  if (!state.user) return;
  state.dashboard = await api("/api/dashboard");
  state.user = state.dashboard.user;
  setHeader();
}

// ดึงสถานะใหม่อัตโนมัติ: เมื่ออีกฝ่ายตอบรับ/ยืนยัน หน้าแดชบอร์ดจะอัปเดตเองโดยไม่ต้องรีเฟรช
function dashboardSignature(data) {
  return JSON.stringify([data.requests, data.ledger, data.stats, data.user?.credits, data.offers]);
}

let dashboardPolling = false;
async function pollDashboard() {
  if (dashboardPolling || !state.user || state.view !== "dashboard" || document.hidden) return;
  if (!$("#modal-backdrop").classList.contains("hidden")) return; // ไม่รบกวนตอนเปิดหน้าต่างอยู่
  dashboardPolling = true;
  try {
    const data = await api("/api/dashboard");
    if (state.view !== "dashboard" || !state.dashboard) return;
    if (dashboardSignature(data) === dashboardSignature(state.dashboard)) return;
    state.dashboard = data;
    state.user = data.user;
    setHeader();
    const scrollY = window.scrollY;
    renderPrivateView("dashboard");
    window.scrollTo({ top: scrollY });
  } catch { /* ลองใหม่รอบถัดไป */ }
  finally { dashboardPolling = false; }
}
window.setInterval(pollDashboard, 4000);
document.addEventListener("visibilitychange", () => { if (!document.hidden) pollDashboard(); });
window.addEventListener("focus", pollDashboard);

async function loadShop() {
  try { renderShop(await api("/api/shop")); } catch (error) { toast(error.message, "error"); }
}

function renderShop(data) {
  $("#view-page")?.remove();
  const page = document.createElement("section");
  page.className = "view-page";
  page.id = "view-page";
  const card = item => {
    const preview = item.kind === "frame"
      ? `<div class="shop-preview ${esc(item.id)}">${esc(initials(state.user.full_name))}</div>`
      : `<div class="shop-preview shop-emoji">${item.emoji}</div>`;
    const button = !item.owned
      ? `<button class="mini-btn primary" data-shop="buy" data-item="${item.id}" ${data.coins < item.price ? "disabled" : ""}>ซื้อ · ${item.price} เหรียญ</button>`
      : item.equipped
        ? `<button class="mini-btn" data-shop="equip" data-kind="${item.kind}" data-item="">ถอดออก</button>`
        : `<button class="mini-btn primary" data-shop="equip" data-kind="${item.kind}" data-item="${item.id}">ใช้งาน</button>`;
    return `<div class="shop-item ${item.equipped ? "is-equipped" : ""}">${preview}<h3>${esc(item.name)}</h3>${button}</div>`;
  };
  const section = (title, kind) => `<div class="panel"><div class="panel-heading"><div><h2>${title}</h2></div></div><div class="shop-grid">${data.items.filter(item => item.kind === kind).map(card).join("")}</div></div>`;
  page.innerHTML = `<div class="page-heading"><div><div class="eyebrow">ร้านค้า</div><h1>แลกกรอบรูปและสติกเกอร์</h1><p>ทุก 30 นาทีที่เรียนหรือสอนสำเร็จ ได้ ${data.per_30_min} เหรียญ (แยกจากเครดิตเวลา ไม่กระทบยอดชั่วโมงของคุณ)</p></div><button class="btn btn-outline" data-view="dashboard">← กลับแดชบอร์ด</button></div>
    <div class="credit-panel"><div><div class="credit-caption">เหรียญของคุณ</div><div class="credit-value">${data.unlimited ? "∞" : data.coins} <small>${data.unlimited ? "ไม่จำกัด" : "เหรียญ"}</small></div><div class="credit-sub">${data.unlimited ? "บัญชีผู้ดูแลระบบ ซื้อไอเทมได้ไม่จำกัด" : `สะสมแล้ว ${data.earned} · ใช้ไป ${data.spent}`}</div></div></div>
    ${section("กรอบรูปโปรไฟล์", "frame")}${section("สติกเกอร์", "sticker")}`;
  $("#app-main").append(page);
}

async function shopAction(action, itemId, kind) {
  try {
    if (action === "buy") await api("/api/shop/buy", { method: "POST", body: { item_id: itemId } });
    else await api("/api/shop/equip", { method: "POST", body: { kind, item_id: itemId || "" } });
    toast(action === "buy" ? "ซื้อสำเร็จ กด “ใช้งาน” เพื่อสวมใส่" : "อัปเดตโปรไฟล์แล้ว");
    await refreshDashboard();
    await loadShop();
  } catch (error) { toast(error.message, "error"); }
}

function adminApiUrl() {
  const query = new URLSearchParams();
  if (state.adminMemberQuery) query.set("members", state.adminMemberQuery);
  if (state.adminOfferQuery) query.set("offers", state.adminOfferQuery);
  const suffix = query.toString();
  return `/api/admin${suffix ? `?${suffix}` : ""}`;
}

function setView(view) {
  if (["dashboard", "create", "profile", "calendar", "admin", "shop"].includes(view) && !state.user) {
    openAuth("login");
    toast("เข้าสู่ระบบเพื่อใช้ฟังก์ชันนี้", "error");
    return;
  }
  state.view = view;
  const views = ["#hero", ".trust-row", "#market", "#how-it-works", ".bottom-cta"];
  views.forEach(selector => $(selector).classList.toggle("hidden", view !== "explore"));
  $("#view-page")?.remove();
  $$(".nav-link[data-view]").forEach(button => button.classList.toggle("active", button.dataset.view === view));
  if (view === "explore") {
    window.scrollTo({ top: 0, behavior: "smooth" });
    return;
  }
  if (view === "dashboard" || view === "profile" || view === "create") {
    refreshDashboard().then(() => renderPrivateView(view)).catch(error => toast(error.message, "error"));
  } else if (view === "shop") {
    loadShop();
  } else if (view === "calendar") {
    loadCalendar(state.calendarDate);
  } else if (view === "admin") {
    api(adminApiUrl()).then(data => { state.admin = data; renderAdmin(); }).catch(error => toast(error.message, "error"));
  }
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function statusPill(status) {
  return `<span class="status-pill status-${esc(status)}">${esc(STATUS_NAMES[status] || status)}</span>`;
}

function requestCard(request) {
  const learner = request.viewer_role === "requester";
  const otherName = learner ? request.provider_name : request.requester_name;
  const partyLabel = request.viewer_role === "admin"
    ? `ผู้เรียน: ${request.requester_name} · ผู้สอน: ${request.provider_name}`
    : `${learner ? "เรียนกับ" : "สอนให้"} ${otherName}`;
  const requesterChip = miniAvatar(request.requester_name, request.requester_avatar, request.requester_frame, request.requester_sticker);
  const providerChip = miniAvatar(request.provider_name, request.provider_avatar, request.provider_frame, request.provider_sticker);
  const partyAvatars = request.viewer_role === "admin" ? requesterChip + providerChip : (learner ? providerChip : requesterChip);
  let actions = "";
  if (request.status === "pending" && !learner) {
    actions = `<button class="mini-btn primary" data-action="accept" data-request="${request.id}">ตอบรับคำขอ</button><button class="mini-btn" data-action="decline" data-request="${request.id}">ปฏิเสธ</button>`;
  } else if (["accepted", "awaiting_confirmation"].includes(request.status)) {
    const confirmed = learner ? request.requester_confirmed : request.provider_confirmed;
    actions = `${confirmed ? '<span class="field-help">คุณยืนยันกิจกรรมแล้ว</span>' : `<button class="mini-btn primary" data-action="confirm" data-request="${request.id}">ยืนยันว่าเรียนเสร็จ</button>`}<button class="mini-btn" data-action="dispute" data-request="${request.id}">แจ้งปัญหา</button><button class="mini-btn" data-action="cancel" data-request="${request.id}">ยกเลิก</button>`;
    const finished = Date.now() > new Date(request.scheduled_at).getTime() + request.duration_minutes * 60000;
    if (finished && !learner && request.status === "accepted") actions += `<button class="mini-btn danger" data-action="no-show" data-request="${request.id}">รายงานไม่มาตามนัด</button>`;
  } else if (request.status === "completed" && learner && !request.reviewed_by_viewer) {
    actions = `<button class="mini-btn" data-action="review" data-request="${request.id}">รีวิวผู้สอน</button>`;
  } else if (request.status === "disputed") {
    actions = `<span class="field-help">ผู้ดูแลจะตรวจสอบข้อมูลของทั้งสองฝ่าย</span>`;
  }
  const canChat = ["pending", "accepted", "awaiting_confirmation", "disputed", "completed"].includes(request.status) && request.viewer_role !== "admin";
  if (canChat) actions += `<button class="mini-btn" data-action="chat" data-request="${request.id}">แชทนัดหมาย</button><button class="mini-btn" data-action="report-member" data-request="${request.id}" data-id="${learner ? request.provider_id : request.requester_id}">รายงานสมาชิก</button>`;
  if (learner) actions += `<button class="mini-btn" data-profile="${request.provider_id}">โปรไฟล์ผู้สอน</button>`;
  return `<article class="request-card"><div class="request-card-top"><div><h3>${esc(request.offer_title)}</h3><div class="request-person">${partyAvatars}<span>${esc(partyLabel)}</span></div></div>${statusPill(request.status)}</div>
    <div class="request-details"><span>◷ ${formatDate(request.scheduled_at)}</span><span>⌛ ${formatDuration(request.duration_minutes)}</span></div>
    ${request.message ? `<div class="request-message">“${esc(request.message)}”</div>` : ""}
    ${request.dispute_note ? `<div class="request-message">รายละเอียดที่แจ้ง: ${esc(request.dispute_note)}</div>` : ""}
    ${request.status === "awaiting_confirmation" ? `<div class="field-help" style="margin-top:9px">ยืนยันแล้ว ${Number(request.requester_confirmed) + Number(request.provider_confirmed)} จาก 2 ฝ่าย · เครดิตจะโอนเมื่อทั้งสองฝ่ายยืนยัน</div>` : ""}
    ${actions ? `<div class="request-actions">${actions}</div>` : ""}</article>`;
}

function renderLedger(ledger) {
  if (!ledger.length) return '<div class="request-empty">ยังไม่มีกิจกรรมในบัญชีเครดิต</div>';
  return `<div class="ledger-list">${ledger.map(item => `<div class="ledger-row"><div><div class="ledger-label">${esc(item.description)}</div><div class="ledger-date">${formatDate(item.created_at)}</div></div><span class="ledger-amount ${item.amount_minutes > 0 ? "positive" : "negative"}">${item.amount_minutes > 0 ? "+" : "−"}${formatDuration(Math.abs(item.amount_minutes))}</span></div>`).join("")}</div>`;
}

function renderPrivateView(view) {
  const dashboard = state.dashboard;
  if (!dashboard) return;
  // ลบหน้าเดิมก่อนวาดใหม่ ไม่เช่นนั้นหน้าเก่า (สถานะเก่า) จะค้างอยู่ด้านบนและหน้าใหม่ถูกต่อท้าย
  $("#view-page")?.remove();
  const page = document.createElement("section");
  page.className = "view-page";
  page.id = "view-page";
  if (view === "create") {
    page.innerHTML = `<div class="page-heading"><div><div class="eyebrow">แบ่งปันสิ่งที่คุณรู้</div><h1>เพิ่มทักษะที่คุณสอน</h1><p>เมื่อมีคนมาเรียน คุณจะได้รับเครดิตเวลาหลังทั้งสองฝ่ายยืนยันกิจกรรม</p></div><button class="btn btn-outline" data-view="dashboard">← กลับแดชบอร์ด</button></div>
      <div class="panel" style="max-width:750px"><div class="inline-note">ทุก 1 ชั่วโมงที่สอนสำเร็จ จะได้รับ 1 เครดิตเวลา ผู้เรียนเลือกเวลานัดหมายได้จากประกาศของคุณ</div>
      <form id="offer-form"><div class="form-grid"><div class="field"><label for="offer-title">ชื่อทักษะ</label><input id="offer-title" name="title" required maxlength="100" placeholder="เช่น วาดภาพดิจิทัลเบื้องต้น"></div><div class="field"><label for="offer-category">หมวดหมู่</label><select id="offer-category" name="category" required>${CATEGORIES.slice(1).map(c => `<option>${esc(c)}</option>`).join("")}</select></div><div class="field"><label for="offer-level">ระดับที่สอนได้</label><select id="offer-level" name="level"><option>เริ่มต้น</option><option>ฝึกฝน</option><option>ทุกระดับ</option><option>ขั้นสูง</option></select></div><div class="field"><label for="offer-availability">ช่วงเวลาที่สะดวก</label><input id="offer-availability" name="availability" maxlength="120" placeholder="เช่น เย็นวันธรรมดา / นัดหมายล่วงหน้า"></div></div>
      <div class="field"><label>ช่วงเวลาที่สอนได้</label>${slotPicker([])}<span class="field-help">เวลาไทย: เช้า 06:00–12:00 · บ่าย 12:00–18:00 · เย็น 18:00–24:00 ระบบรับนัดเฉพาะช่วงที่เลือก (ไม่ติ๊กเลย = อ่านจากข้อความช่วงเวลาด้านบน)</span></div>
      <div class="field"><label for="offer-description">รายละเอียด</label><textarea id="offer-description" name="description" required maxlength="600" placeholder="บอกผู้เรียนว่าจะได้เรียนรู้อะไร เตรียมตัวอย่างไร"></textarea><span class="field-help">อย่างน้อย 12 ตัวอักษร</span></div>
      <div class="form-actions"><button type="button" class="btn btn-quiet" data-view="dashboard">ยกเลิก</button><button class="btn btn-dark" type="submit">เผยแพร่ทักษะ <span>↗</span></button></div></form></div>`;
  } else if (view === "profile") {
    const user = dashboard.user;
    state.avatarDraft = null;
    state.avatarCleared = false;
    const profileImage = user.avatar_url ? `<img src="${esc(user.avatar_url)}" alt="รูปโปรไฟล์" id="profile-avatar-preview">` : `<span id="profile-avatar-fallback">${esc(initials(user.full_name))}</span>`;
    page.innerHTML = `<div class="page-heading"><div><div class="eyebrow">โปรไฟล์ของคุณ</div><h1>บัญชีและทักษะที่สนใจ</h1><p>ข้อมูลนี้ช่วยให้คนในชุมชนรู้จักคุณ และช่วยแนะนำทักษะที่ตรงใจ</p></div><button class="btn btn-outline" data-view="dashboard">← กลับแดชบอร์ด</button></div>
      <div class="panel profile-edit-panel" style="max-width:720px"><form id="profile-form"><div class="profile-photo-editor"><div class="profile-photo-large ${esc(user.frame || "")}" data-sticker="${esc(user.sticker || "")}" id="profile-avatar-box">${profileImage}</div><div><div class="profile-photo-title">รูปโปรไฟล์</div><div class="profile-photo-help">ใช้ JPG, PNG หรือ WebP · ระบบย่อรูปก่อนบันทึก</div><div class="profile-photo-actions"><label class="mini-btn upload-label" for="profile-avatar-input">เลือกรูป</label><input id="profile-avatar-input" type="file" accept="image/jpeg,image/png,image/webp" hidden>${user.avatar_url ? '<button type="button" class="mini-btn danger" id="remove-profile-avatar">ลบรูป</button>' : ""}</div></div></div><div class="field"><label for="profile-name">ชื่อที่แสดง</label><input id="profile-name" name="full_name" required maxlength="80" value="${esc(user.full_name)}"></div>
      <div class="form-grid"><div class="field"><label for="profile-university">มหาวิทยาลัย</label><input id="profile-university" name="university" maxlength="120" value="${esc(user.university)}"></div><div class="field"><label for="profile-faculty">คณะ</label><input id="profile-faculty" name="faculty" maxlength="100" value="${esc(user.faculty)}"></div></div>
      <div class="field"><label for="profile-bio">แนะนำตัว</label><textarea id="profile-bio" name="bio" maxlength="500" placeholder="เล่าเกี่ยวกับตัวคุณและสิ่งที่ชอบแบ่งปัน">${esc(user.bio)}</textarea></div>
      <div class="field"><label for="profile-interests">ทักษะที่อยากเรียน</label><input id="profile-interests" name="interests" maxlength="400" value="${esc((user.interests || []).join(", "))}" placeholder="เช่น เทคโนโลยี, Python, ดนตรี, กีตาร์"><span class="field-help">ใส่หมวดและคำสำคัญแยกกัน เช่น เทคโนโลยี, Python; ตรงทั้งคู่ได้คะแนนเต็ม ตรงเฉพาะหมวดได้ครึ่งคะแนน (ไม่เกิน 8 รายการ)</span></div>
      <div class="field"><label for="profile-level">ระดับที่อยากเรียน</label><select id="profile-level" name="desired_level"><option value="">ไม่ระบุ</option>${LEVEL_OPTIONS.map(level => `<option ${user.desired_level === level ? "selected" : ""}>${level}</option>`).join("")}</select><span class="field-help">ใช้จัดอันดับทักษะที่แนะนำ (ระดับตรงหรือ "ทุกระดับ" ได้คะแนนเต็ม)</span></div>
      <div class="field"><label>ช่วงเวลาที่สะดวกเรียน</label>${slotPicker(user.preferred_slots || [])}<span class="field-help">เวลาไทย: เช้า 06:00–12:00 · บ่าย 12:00–18:00 · เย็น 18:00–24:00 ใช้ช่วยจัดอันดับคำแนะนำ</span></div>
      <div class="form-actions"><button type="button" class="btn btn-quiet" data-action="logout">ออกจากระบบ</button><button class="btn btn-dark" type="submit">บันทึกโปรไฟล์</button></div></form></div>`;
  } else {
    const credits = dashboard.user.credits;
    const incoming = dashboard.requests.filter(request => ["pending", "accepted", "awaiting_confirmation", "disputed"].includes(request.status));
    const offers = dashboard.offers;
    page.innerHTML = `<div class="page-heading"><div><div class="eyebrow">พื้นที่ของคุณ</div><h1>สวัสดี ${esc(dashboard.user.full_name.split(/\s+/)[0])} <span style="color:#91b34a">✳</span></h1><p>แบ่งปันทักษะที่คุณมี แล้วใช้เวลาเรียนรู้สิ่งที่คุณสนใจ</p></div><div class="dashboard-shortcuts"><button class="btn btn-outline" data-action="export-user">ส่งออกข้อมูล</button><button class="btn btn-outline" data-view="calendar">ปฏิทิน</button><button class="btn btn-outline" data-view="profile">แก้ไขโปรไฟล์</button><button class="btn btn-dark" data-view="create">＋ ลงประกาศทักษะ</button></div></div>
      ${dashboard.user.email_verified ? "" : '<div class="email-warning"><span>อีเมลยังไม่ได้ยืนยัน</span><span>ยืนยันเพื่อให้บัญชีนี้กู้คืนได้หากลืมรหัสผ่าน</span><button class="mini-btn primary" data-action="resend-verification">ส่งลิงก์ยืนยันอีกครั้ง</button></div>'}
      <div class="credit-panel"><div><div class="credit-caption">เครดิตเวลาที่ใช้ได้</div>${credits.unlimited ? '<div class="credit-value">∞ <small>ไม่จำกัด</small></div><div class="credit-sub">บัญชีผู้ดูแลระบบ ไม่ถูกหักเครดิตและไม่มีเพดาน</div></div></div>' : `<div class="credit-value">${(credits.available / 60).toFixed(credits.available % 60 ? 1 : 0)} <small>ชั่วโมง</small></div><div class="credit-sub">ยอดสะสม ${(credits.balance / 60).toFixed(credits.balance % 60 ? 1 : 0)} จากสูงสุด 20 ชั่วโมง</div></div><div class="credit-right"><div class="credit-stat"><b>${(credits.held_out / 60).toFixed(credits.held_out % 60 ? 1 : 0)} ชม.</b><small>กันไว้กับคำขอเรียน</small></div><div class="credit-stat"><b>${(credits.capacity_remaining / 60).toFixed(credits.capacity_remaining % 60 ? 1 : 0)} ชม.</b><small>พื้นที่รับเครดิตเพิ่ม</small></div></div></div>`}
      <div class="stat-cards"><div class="stat-card"><small>กิจกรรมที่สำเร็จ</small><b>${dashboard.stats.completed}</b><span>เรียนรู้และแบ่งปันแล้ว</span></div><div class="stat-card"><small>คำขอที่กำลังดำเนินการ</small><b>${dashboard.stats.active_requests}</b><span>รอการตอบรับหรือยืนยัน</span></div><div class="stat-card"><small>ทักษะที่เปิดสอน</small><b>${offers.filter(item => item.active).length}</b><span>จาก ${offers.length} ประกาศของคุณ</span></div></div>
      <div class="dashboard-grid"><div><div class="panel"><div class="panel-heading"><div><h2>คำขอและกิจกรรม</h2><p>ติดตามนัดหมาย แล้วยืนยันเมื่อเรียนเสร็จ</p></div><button class="panel-link" data-view="explore">ค้นหาทักษะ ↗</button></div><div class="request-list">${dashboard.requests.length ? dashboard.requests.map(requestCard).join("") : '<div class="request-empty">ยังไม่มีกิจกรรม <br> ลองค้นหาทักษะที่สนใจเพื่อเริ่มต้น</div>'}</div></div>
      <div class="panel"><div class="panel-heading"><div><h2>ทักษะที่คุณสอน</h2><p>ช่วยให้เพื่อน ๆ ค้นพบสิ่งที่คุณแบ่งปัน</p></div><button class="panel-link" data-view="create">＋ เพิ่มทักษะ</button></div>${offers.length ? `<div class="offer-manager">${offers.map(offer => `<div class="managed-offer"><h3>${esc(offer.title)}</h3><p>${esc(offer.category)} · ${esc(offer.level)}</p><div class="offer-manager-foot"><span class="tag ${offer.active ? "" : "closed"}">${offer.active ? "เปิดรับผู้เรียน" : "ปิดประกาศ"}</span>${offer.active ? `<button class="mini-btn" data-action="archive-offer" data-offer="${offer.id}">ปิดประกาศ</button>` : ""}</div></div>`).join("")}</div>` : '<div class="request-empty">ยังไม่มีประกาศทักษะ</div>'}</div></div>
      <aside><div class="panel"><div class="panel-heading"><div><h2>บัญชีเครดิตเวลา</h2><p>ทุกการเปลี่ยนแปลงบันทึกไว้อย่างโปร่งใส</p></div></div>${renderLedger(dashboard.ledger)}</div>
      <div class="panel"><div class="panel-heading"><div><h2>ทักษะที่น่าจะถูกใจ</h2><p>ระบบจับคู่จากความสนใจในโปรไฟล์</p></div></div>${dashboard.recommendations.length ? `<div class="skill-list">${dashboard.recommendations.slice(0, 4).map(item => `<div class="skill-row"><span>${esc(item.title)}</span><small>${item.matched ? `✦ ตรงใจ ${item.match_score}%` : `${item.match_score}%`}</small></div>`).join("")}</div><button class="panel-link" data-view="explore" style="margin-top:12px">ดูทั้งหมด ↗</button>` : '<div class="request-empty">เพิ่มทักษะที่อยากเรียนในโปรไฟล์ เพื่อรับคำแนะนำ</div>'}</div>
      <div class="panel profile-card"><div class="profile-big-avatar ${esc(dashboard.user.frame)}" data-sticker="${esc(dashboard.user.sticker)}">${dashboard.user.avatar_url ? `<img src="${esc(dashboard.user.avatar_url)}" alt="">` : esc(initials(dashboard.user.full_name))}</div><div><h3>${esc(dashboard.user.full_name)}</h3><p>${esc(dashboard.user.faculty || dashboard.user.university)}</p><button class="panel-link" data-view="profile">จัดการโปรไฟล์ ↗</button></div></div></aside></div>`;
  }
  $("#app-main").append(page);
}

function localDayKey(date) {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
}

async function loadCalendar(month = state.calendarDate) {
  const selected = new Date(month.getFullYear(), month.getMonth(), 1);
  state.calendarDate = selected;
  const query = new URLSearchParams({ month: `${selected.getFullYear()}-${String(selected.getMonth() + 1).padStart(2, "0")}` });
  try {
    const data = await api(`/api/calendar?${query}`);
    renderCalendar(data);
  } catch (error) { toast(error.message, "error"); }
}

function renderCalendar(data) {
  $("#view-page")?.remove();
  const page = document.createElement("section");
  page.className = "view-page";
  page.id = "view-page";
  const year = state.calendarDate.getFullYear();
  const month = state.calendarDate.getMonth();
  const firstWeekday = new Date(year, month, 1).getDay();
  const daysInMonth = new Date(year, month + 1, 0).getDate();
  const monthKey = `${year}-${String(month + 1).padStart(2, "0")}`;
  const events = data.events || [];
  if (!state.calendarSelected || !state.calendarSelected.startsWith(monthKey)) {
    const todayKey = localDayKey(new Date());
    state.calendarSelected = todayKey.startsWith(monthKey) ? todayKey : `${monthKey}-01`;
  }
  const eventDateSet = new Set(events.map(event => localDayKey(new Date(event.scheduled_at))));
  const blanks = Array.from({ length: firstWeekday }, () => '<span class="calendar-day blank"></span>');
  const cells = Array.from({ length: daysInMonth }, (_, index) => {
    const day = index + 1;
    const key = `${monthKey}-${String(day).padStart(2, "0")}`;
    return `<button class="calendar-day ${eventDateSet.has(key) ? "has-events" : ""} ${state.calendarSelected === key ? "selected" : ""}" data-calendar-day="${key}">${day}${eventDateSet.has(key) ? '<i></i>' : ""}</button>`;
  });
  const selectedEvents = events.filter(event => localDayKey(new Date(event.scheduled_at)) === state.calendarSelected);
  const monthName = new Intl.DateTimeFormat("th-TH", { month: "long", year: "numeric" }).format(state.calendarDate);
  page.innerHTML = `<div class="page-heading"><div><div class="eyebrow">นัดหมายของคุณ</div><h1>ปฏิทินนัดหมาย</h1><p>ทุกคำขอและนัดหมายอยู่ในที่เดียว พร้อมตรวจเวลาซ้อนก่อนส่งคำขอ</p></div><button class="btn btn-outline" data-view="dashboard">← กลับกิจกรรม</button></div>
    <div class="calendar-layout"><div class="panel calendar-panel"><div class="calendar-toolbar"><button class="mini-btn" data-action="calendar-prev" aria-label="เดือนก่อน">←</button><h2>${esc(monthName)}</h2><button class="mini-btn" data-action="calendar-next" aria-label="เดือนถัดไป">→</button></div><div class="calendar-grid calendar-weekdays">${["อา", "จ", "อ", "พ", "พฤ", "ศ", "ส"].map(day => `<span>${day}</span>`).join("")}</div><div class="calendar-grid">${[...blanks, ...cells].join("")}</div></div>
    <div class="panel calendar-events"><div class="panel-heading"><div><h2>นัดวันที่ ${esc(new Intl.DateTimeFormat("th-TH", { dateStyle: "long" }).format(new Date(`${state.calendarSelected}T12:00:00`)))}</h2><p>คำขอเรียนและกิจกรรมของคุณ</p></div><span class="tag">${selectedEvents.length} รายการ</span></div><div class="request-list">${selectedEvents.length ? selectedEvents.map(requestCard).join("") : '<div class="request-empty">วันนี้ยังไม่มีนัดหมาย เลือกวันอื่นหรือส่งคำขอเรียนใหม่ได้</div>'}</div></div></div>`;
  $("#app-main").append(page);
}

function renderAdmin() {
  $("#view-page")?.remove();
  const page = document.createElement("section");
  page.className = "view-page";
  page.id = "view-page";
  const memberList = state.admin.members.length ? state.admin.members.map(member => `<div class="admin-row"><div><div class="admin-row-title">${esc(member.full_name)}</div><div class="admin-row-meta">${esc(member.email)}${member.faculty ? ` · ${esc(member.faculty)}` : ""} · สมัคร ${formatDate(member.created_at, false)}</div></div><div class="admin-row-actions"><span class="tag ${member.is_active ? "" : "closed"}">${member.is_active ? "ใช้งาน" : "ถูกระงับ"}</span><button class="mini-btn ${member.is_active ? "danger" : "primary"}" data-action="admin-user-toggle" data-id="${member.id}" data-active="${!member.is_active}">${member.is_active ? "ระงับบัญชี" : "เปิดใช้งาน"}</button></div></div>`).join("") : '<div class="request-empty">ไม่พบสมาชิกที่ตรงกับคำค้น</div>';
  const offerList = state.admin.offers.length ? state.admin.offers.map(offer => `<div class="admin-row"><div><div class="admin-row-title">${esc(offer.title)} <span class="tag">${esc(offer.category)}</span></div><div class="admin-row-meta">${esc(offer.provider_name)} · ${esc(offer.provider_email)}${offer.owner_active ? "" : " · บัญชีผู้สอนถูกระงับ"}</div></div><div class="admin-row-actions"><span class="tag ${offer.active ? "" : "closed"}">${offer.active ? "แสดงในตลาด" : "ปิดประกาศ"}</span>${offer.owner_active ? `<button class="mini-btn ${offer.active ? "danger" : "primary"}" data-action="admin-offer-toggle" data-id="${offer.id}" data-active="${!offer.active}">${offer.active ? "ปิดประกาศ" : "เปิดประกาศ"}</button>` : ""}<button class="mini-btn danger" data-action="admin-offer-delete" data-id="${offer.id}">ลบประกาศ</button></div></div>`).join("") : '<div class="request-empty">ไม่พบประกาศที่ตรงกับคำค้น</div>';
  const reportList = state.admin.reports?.length ? state.admin.reports.map(report => `<div class="admin-row report-row"><div><div class="admin-row-title">${esc(report.reason)} · ${esc(report.target_name)}</div><div class="admin-row-meta">รายงานโดย ${esc(report.reporter_name)} · ${report.target_type === "user" ? "สมาชิก" : "ประกาศทักษะ"} #${report.target_id}${report.request_id ? ` · กิจกรรม #${report.request_id}` : ""} · ${formatDate(report.created_at)}</div>${report.details ? `<div class="report-details">${esc(report.details)}</div>` : ""}</div><div class="admin-row-actions"><span class="tag ${report.status === "reviewed" ? "" : "closed"}">${report.status === "reviewed" ? "กำลังตรวจสอบ" : "รอตรวจ"}</span><button class="mini-btn" data-action="report-review" data-id="${report.id}">กำลังตรวจสอบ</button><button class="mini-btn primary" data-action="report-resolve" data-id="${report.id}">ปิดเรื่อง</button><button class="mini-btn danger" data-action="report-dismiss" data-id="${report.id}">ไม่รับรายงาน</button></div></div>`).join("") : '<div class="request-empty">ไม่มีรายงานที่รอตรวจสอบ</div>';
  const auditList = state.admin.audit?.length ? state.admin.audit.map(item => `<div class="admin-row audit-row"><div><div class="admin-row-title">${esc(item.summary)}</div><div class="admin-row-meta">${esc(item.admin_name)} · ${formatDate(item.created_at)}</div></div><span class="tag">${esc(item.target_type === "request" ? "คำขอ" : item.target_type === "user" ? "สมาชิก" : "ประกาศ")} #${item.target_id}</span></div>`).join("") : '<div class="request-empty">ยังไม่มีประวัติการดำเนินการ</div>';
  page.innerHTML = `<div class="page-heading"><div><div class="eyebrow">เครื่องมือผู้ดูแล</div><h1>ภาพรวมชุมชน</h1><p>ตรวจสอบกรณีที่สมาชิกขอให้ช่วยพิจารณา</p></div><button class="btn btn-outline" data-action="download-backup">สำรองฐานข้อมูล</button></div>
    <div class="stat-cards admin-stats"><div class="stat-card"><small>สมาชิก</small><b>${state.admin.stats.members}</b><span>บัญชีที่ลงทะเบียน</span></div><div class="stat-card"><small>กรณีรอตรวจสอบ</small><b>${state.admin.stats.disputes}</b><span>รอผู้ดูแลพิจารณา</span></div><div class="stat-card"><small>รายงานสมาชิก/ประกาศ</small><b>${state.admin.stats.reports || 0}</b><span>รอแอดมินตรวจสอบ</span></div><div class="stat-card"><small>ทักษะที่เปิดสอน</small><b>${state.admin.stats.offers}</b><span>ในตลาดแลกเปลี่ยน</span></div><div class="stat-card"><small>กิจกรรมสำเร็จ</small><b>${state.admin.stats.completed}</b><span>ยืนยันครบทั้งสองฝ่าย</span></div></div>
    <div class="panel"><div class="panel-heading"><div><h2>รายงานสมาชิกและประกาศ</h2><p>ตรวจสอบเหตุผลและทำเครื่องหมายเมื่อจัดการเสร็จ</p></div><span class="tag">${state.admin.reports?.length || 0} รายการ</span></div><div class="admin-list report-list">${reportList}</div></div>
    <div class="panel"><div class="panel-heading"><div><h2>กรณีรอตรวจสอบ</h2><p>พิจารณาข้อมูลจากทั้งผู้เรียนและผู้สอน</p></div></div><div class="request-list">${state.admin.disputes.length ? state.admin.disputes.map(request => `${requestCard(request)}<div class="request-actions" style="margin:-8px 0 10px 14px"><button class="mini-btn primary" data-action="resolve-complete" data-request="${request.id}">ยืนยันว่ากิจกรรมเสร็จ</button><button class="mini-btn danger" data-action="resolve-cancel" data-request="${request.id}">ยกเลิกและคืนเครดิตที่กันไว้</button></div>`).join("") : '<div class="request-empty">ไม่มีกรณีที่ต้องตรวจสอบ</div>'}</div></div>
    <div class="admin-grid"><div class="panel"><div class="panel-heading"><div><h2>จัดการสมาชิก</h2><p>ระงับบัญชีหรือเปิดใช้งานอีกครั้ง</p></div><span class="tag">${state.admin.members.length} / ${state.admin.stats.members} คน</span></div><div class="admin-search"><input id="admin-member-search" type="search" value="${esc(state.adminMemberQuery)}" placeholder="ค้นหาชื่อ อีเมล หรือคณะ" aria-label="ค้นหาสมาชิก"></div><div class="admin-list">${memberList}</div></div>
    <div class="panel"><div class="panel-heading"><div><h2>จัดการประกาศ</h2><p>ปิดหรือเปิดประกาศในตลาดแลกเปลี่ยน</p></div><span class="tag">${state.admin.offers.length} รายการ</span></div><div class="admin-search"><input id="admin-offer-search" type="search" value="${esc(state.adminOfferQuery)}" placeholder="ค้นหาทักษะ ผู้สอน หรือหมวดหมู่" aria-label="ค้นหาประกาศทักษะ"></div><div class="admin-list">${offerList}</div></div></div>
    <div class="panel"><div class="panel-heading"><div><h2>ประวัติผู้ดูแล</h2><p>รายการล่าสุดที่มีการระงับบัญชี จัดการประกาศ หรือปิดข้อพิพาท</p></div><span class="tag">${state.admin.audit?.length || 0} รายการล่าสุด</span></div><div class="admin-list audit-list">${auditList}</div></div>`;
  $("#app-main").append(page);
}

async function runAdminAction(action, itemId, active) {
  const disable = active === false;
  if (action === "admin-user-toggle" && disable && !window.confirm("ระงับบัญชีนี้หรือไม่? สมาชิกจะเข้าสู่ระบบไม่ได้ และคำขอที่ยังดำเนินการจะถูกยกเลิก")) return;
  if (action === "admin-offer-toggle" && disable && !window.confirm("ปิดประกาศนี้จากตลาดแลกเปลี่ยนหรือไม่?")) return;
  const resource = action === "admin-user-toggle" ? "users" : "offers";
  try {
    await api(`/api/admin/${resource}/${itemId}`, { method: "POST", body: { active } });
    state.admin = await api(adminApiUrl());
    await loadOffers();
    renderAdmin();
    toast(action === "admin-user-toggle" ? (active ? "เปิดใช้งานบัญชีแล้ว" : "ระงับบัญชีแล้ว") : (active ? "เปิดประกาศแล้ว" : "ปิดประกาศแล้ว"));
  } catch (error) { toast(error.message, "error"); }
}

async function runAdminDeleteOffer(offerId) {
  if (!window.confirm("ลบประกาศนี้ถาวรหรือไม่? ประกาศจะหายจากตลาดและรายการจัดการ และกู้คืนไม่ได้ (ต่างจากการปิดประกาศ)")) return;
  try {
    await api(`/api/admin/offers/${offerId}`, { method: "DELETE" });
    state.admin = await api(adminApiUrl());
    await loadOffers();
    renderAdmin();
    toast("ลบประกาศแล้ว");
  } catch (error) { toast(error.message, "error"); }
}

async function runReportAction(reportId, status) {
  try {
    const resolution = status === "reviewed" ? "รับเรื่องแล้ว กำลังตรวจสอบ" : status === "resolved" ? "ตรวจสอบและจัดการเรียบร้อย" : "ตรวจสอบแล้ว ไม่พบเหตุให้ดำเนินการ";
    await api(`/api/admin/reports/${reportId}`, { method: "POST", body: { status, resolution } });
    state.admin = await api(adminApiUrl());
    renderAdmin();
    toast(status === "reviewed" ? "บันทึกว่ากำลังตรวจสอบ" : "ปิดรายงานแล้ว");
  } catch (error) { toast(error.message, "error"); }
}

async function downloadFile(path, fallbackName) {
  const response = await fetch(path, { credentials: "same-origin" });
  if (!response.ok) {
    let message = "ดาวน์โหลดไม่สำเร็จ";
    try { message = (await response.json()).error || message; } catch { /* response may not be JSON */ }
    throw new Error(message);
  }
  const blob = await response.blob();
  const disposition = response.headers.get("Content-Disposition") || "";
  const fileName = disposition.match(/filename="?([^";]+)"?/i)?.[1] || fallbackName;
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = fileName;
  document.body.append(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
  toast("เริ่มดาวน์โหลดไฟล์แล้ว");
}

async function loadChatMessages(requestId, scrollToBottom = false) {
  const list = $("#chat-messages");
  if (!list || state.chatRequestId !== Number(requestId)) return;
  try {
    const data = await api(`/api/requests/${requestId}/messages`);
    const nearBottom = list.scrollHeight - list.scrollTop - list.clientHeight < 60;
    list.innerHTML = data.messages.length ? data.messages.map(message => `<article class="chat-message ${Number(message.sender_id) === Number(state.user.id) ? "mine" : "theirs"}"><div>${esc(message.body)}</div><small class="chat-sender">${miniAvatar(message.sender_name, message.sender_avatar, message.sender_frame, message.sender_sticker, "sm")}${esc(message.sender_name)} · ${formatDate(message.created_at)}</small></article>`).join("") : '<div class="request-empty">เริ่มคุยเรื่องวันเวลาและสิ่งที่อยากเรียนได้เลย</div>';
    if (scrollToBottom || nearBottom) list.scrollTop = list.scrollHeight;
  } catch (error) { toast(error.message, "error"); }
}

async function openChat(requestId, title) {
  state.chatRequestId = Number(requestId);
  modal(`<div class="eyebrow">คุยเพื่อเตรียมนัดหมาย</div><h2 id="modal-title">${esc(title || "แชทกิจกรรม")}</h2><p class="modal-subtitle">ข้อความนี้มองเห็นได้เฉพาะผู้เรียนและผู้สอนในกิจกรรมนี้</p><div id="chat-messages" class="chat-messages"></div><form id="chat-form" data-request="${Number(requestId)}"><div class="field"><label for="chat-text">ข้อความ</label><textarea id="chat-text" name="body" required maxlength="2000" placeholder="พิมพ์ข้อความ แล้วกด Enter เพื่อส่ง (Shift+Enter ขึ้นบรรทัดใหม่)"></textarea></div><div class="form-actions"><button class="btn btn-dark" type="submit">ส่งข้อความ ↗</button></div></form>`, true);
  await loadChatMessages(requestId, true);
  state.chatTimer = window.setInterval(() => loadChatMessages(requestId), 4000);
}

function openReportForm(targetType, targetId, requestId = "") {
  const targetName = targetType === "offer" ? "ประกาศทักษะ" : "สมาชิก";
  modal(`<div class="modal-icon">!</div><h2 id="modal-title">รายงาน${targetName}</h2><p class="modal-subtitle">รายงานนี้จะส่งให้แอดมินตรวจสอบ ผู้ใช้ทั่วไปจะไม่เห็นชื่อผู้รายงาน</p><form id="report-form" data-target-type="${esc(targetType)}" data-target-id="${Number(targetId)}" data-request="${requestId ? Number(requestId) : ""}"><div class="field"><label for="report-reason">เหตุผล</label><select id="report-reason" name="reason"><option>เนื้อหาไม่เหมาะสม</option><option>ข้อมูลประกาศไม่ตรงความจริง</option><option>พฤติกรรมไม่เหมาะสม</option><option>สแปมหรือหลอกลวง</option><option>อื่น ๆ</option></select></div><div class="field"><label for="report-details">รายละเอียดเพิ่มเติม (ไม่บังคับ)</label><textarea id="report-details" name="details" maxlength="800" placeholder="บอกข้อมูลที่ช่วยให้แอดมินตรวจสอบได้"></textarea></div><div class="form-actions"><button type="button" class="btn btn-quiet" data-close-modal>ยกเลิก</button><button class="btn btn-dark" type="submit">ส่งรายงาน</button></div></form>`);
}

document.addEventListener("input", event => {
  const input = event.target;
  if (input.id !== "admin-member-search" && input.id !== "admin-offer-search") return;
  const focusId = input.id;
  const cursor = input.selectionStart;
  if (focusId === "admin-member-search") state.adminMemberQuery = input.value;
  else state.adminOfferQuery = input.value;
  const sequence = ++state.adminSearchSequence;
  window.clearTimeout(state.adminSearchTimer);
  state.adminSearchTimer = window.setTimeout(async () => {
    if (state.view !== "admin") return;
    try {
      const data = await api(adminApiUrl());
      if (sequence !== state.adminSearchSequence || state.view !== "admin") return;
      state.admin = data;
      renderAdmin();
      const nextInput = $(`#${focusId}`);
      nextInput?.focus();
      if (nextInput && cursor !== null) nextInput.setSelectionRange(cursor, cursor);
    } catch (error) { toast(error.message, "error"); }
  }, 250);
});

function openAuth(mode = "login") {
  state.authMode = mode;
  const registering = mode === "register";
  modal(`<div class="modal-icon">◷</div><h2 id="modal-title">${registering ? "เริ่มต้นกับ ChronoSwap" : "ยินดีต้อนรับกลับ"}</h2><p class="modal-subtitle">${registering ? "สร้างบัญชี แล้วเริ่มสะสมเครดิตเวลาเพื่อเรียนรู้ร่วมกัน" : "เข้าสู่ระบบเพื่อจัดการเครดิตและนัดหมายของคุณ"}</p>
    <div class="auth-tabs"><button class="auth-tab ${!registering ? "active" : ""}" data-auth="login">เข้าสู่ระบบ</button><button class="auth-tab ${registering ? "active" : ""}" data-auth="register">สมัครสมาชิก</button></div>
    <form class="modal-form" id="auth-form" data-mode="${mode}">${registering ? `<div class="field"><label for="auth-name">ชื่อที่แสดง</label><input id="auth-name" name="full_name" required maxlength="80" placeholder="ชื่อ นามสกุล"></div><div class="field"><label for="auth-faculty">คณะ (ไม่บังคับ)</label><input id="auth-faculty" name="faculty" maxlength="100" placeholder="คณะหรือวิทยาลัย"></div>` : ""}
    <div class="field"><label for="auth-email">อีเมล</label><input id="auth-email" name="email" type="email" autocomplete="email" required placeholder="you@university.ac.th"></div><div class="field"><label for="auth-password">รหัสผ่าน</label><input id="auth-password" name="password" type="password" autocomplete="${registering ? "new-password" : "current-password"}" required minlength="8" placeholder="อย่างน้อย 8 ตัวอักษร"></div>
    ${registering ? '<div class="inline-note">สมาชิกใหม่เริ่มต้นด้วยเครดิตเวลา 2 ชั่วโมง และสะสมได้สูงสุด 20 ชั่วโมง</div>' : '<div class="auth-extras"><button type="button" class="text-link" data-action="forgot-password">ลืมรหัสผ่าน?</button></div><div class="demo-box"><strong>ลองระบบด้วยบัญชีตัวอย่าง</strong><br>demo@chronoswap.local &nbsp;·&nbsp; Swap@2026<br><span>หรือ mentor.design@chronoswap.local / mentor.music@chronoswap.local ด้วยรหัสผ่านเดียวกัน</span></div>'}
    <button class="btn btn-dark" type="submit">${registering ? "สร้างบัญชี" : "เข้าสู่ระบบ"} <span>↗</span></button></form>`);
}

function openForgotPassword() {
  modal(`<div class="modal-icon">↻</div><h2 id="modal-title">ตั้งรหัสผ่านใหม่</h2><p class="modal-subtitle">กรอกอีเมลที่ใช้สมัคร หากพบข้อมูล ระบบจะส่งลิงก์ตั้งรหัสผ่านใหม่ให้</p><form id="forgot-password-form"><div class="field"><label for="forgot-email">อีเมล</label><input id="forgot-email" name="email" type="email" required autocomplete="email" placeholder="you@university.ac.th"></div><div class="form-actions"><button type="button" class="btn btn-quiet" data-auth="login">กลับเข้าสู่ระบบ</button><button class="btn btn-dark" type="submit">ส่งลิงก์</button></div></form>`);
}

function openResetPassword(token) {
  state.pendingEmailToken = token;
  modal(`<div class="modal-icon">↻</div><h2 id="modal-title">กำหนดรหัสผ่านใหม่</h2><p class="modal-subtitle">ใช้รหัสผ่านที่คาดเดายาก อย่างน้อย 8 ตัวอักษร</p><form id="reset-password-form"><div class="field"><label for="reset-password">รหัสผ่านใหม่</label><input id="reset-password" name="password" type="password" minlength="8" maxlength="128" autocomplete="new-password" required></div><div class="form-actions"><button class="btn btn-dark" type="submit">บันทึกรหัสผ่าน</button></div></form>`);
}

function openVerifyEmail(token) {
  state.pendingEmailToken = token;
  modal(`<div class="modal-icon">✓</div><h2 id="modal-title">ยืนยันอีเมล</h2><p class="modal-subtitle">กดยืนยันเพื่อเปิดใช้งานการกู้คืนบัญชีผ่านอีเมล</p><div class="form-actions"><button class="btn btn-dark" data-action="verify-email">ยืนยันอีเมล</button></div>`);
}

async function verifyPendingEmail() {
  try {
    await api("/api/email/verify", { method: "POST", body: { token: state.pendingEmailToken } });
    state.pendingEmailToken = null;
    closeModal();
    if (state.user) await refreshDashboard();
    toast("ยืนยันอีเมลเรียบร้อยแล้ว");
    if (state.view === "dashboard") renderPrivateView("dashboard");
  } catch (error) { toast(error.message, "error"); }
}

function openBooking(offer) {
  if (!state.user) { openAuth("login"); toast("เข้าสู่ระบบก่อนส่งคำขอเรียน", "error"); return; }
  const credits = state.dashboard?.user?.credits || state.user.credits || { available: 120 };
  const minDate = bangkokDateTimeValue(new Date(Date.now() + 60 * 60 * 1000));
  modal(`<div class="eyebrow">ส่งคำขอเรียน</div><h2 id="modal-title">${esc(offer.title)}</h2><p class="modal-subtitle modal-provider">${miniAvatar(offer.provider_name, offer.avatar_url, offer.frame, offer.sticker)}<span>นัดหมายกับ <button type="button" class="link-btn" data-profile="${offer.user_id}">${esc(offer.provider_name)}</button> · ${esc(offer.faculty || "นักศึกษา")}</span></p>
    <div class="modal-offer-meta"><span class="category-label">${esc(offer.category)}</span><span class="category-label">${esc(offer.level)}</span></div><div class="modal-offer-summary">${esc(offer.description)}<br><br><strong>ช่วงเวลาที่ผู้สอนสะดวก:</strong> ${esc(offerAvailabilityText(offer))}</div>
    <div class="inline-note">เครดิตที่ใช้ได้ ${credits.unlimited ? "ไม่จำกัด" : formatDuration(credits.available)} · จะกันเครดิตไว้ก่อน และโอนให้ผู้สอนเมื่อทั้งสองฝ่ายยืนยันว่าเรียนเสร็จ</div>
    <form id="booking-form" data-offer="${offer.id}"><div class="form-grid"><div class="field"><label for="booking-duration">เวลาเรียน</label><select id="booking-duration" name="duration_minutes"><option value="30">30 นาที</option><option value="60" selected>1 ชั่วโมง</option><option value="90">1 ชั่วโมง 30 นาที</option><option value="120">2 ชั่วโมง</option><option value="180">3 ชั่วโมง</option></select></div><div class="field"><label for="booking-date">วันและเวลา (เวลาไทย)</label><input id="booking-date" name="scheduled_at" type="datetime-local" min="${minDate}" required></div></div>
    <div class="field"><label for="booking-message">บอกผู้สอนเพิ่มเติม (ไม่บังคับ)</label><textarea id="booking-message" name="message" maxlength="400" placeholder="อยากเรียนหัวข้อไหน หรือมีพื้นฐานอะไรบ้าง"></textarea></div><div class="form-actions"><button type="button" class="btn btn-quiet" data-close-modal>ยกเลิก</button><button class="btn btn-dark" type="submit">ส่งคำขอเรียน <span>↗</span></button></div></form><div class="booking-footnote">ต้องเลือกเวลาเริ่มและเวลาจบให้อยู่ในช่วงที่ผู้สอนระบุ: เช้า 06:00–12:00, บ่าย 12:00–18:00 หรือเย็น 18:00–24:00 (เวลาไทย) · ระบบป้องกันเวลานัดซ้อนให้อัตโนมัติ</div><button class="text-link report-offer-link" data-action="report-offer" data-id="${offer.id}">รายงานประกาศหรือผู้สอน</button>`, true);
}

function openDispute(requestId) {
  modal(`<div class="modal-icon">!</div><h2 id="modal-title">แจ้งปัญหากิจกรรม</h2><p class="modal-subtitle">เครดิตจะยังไม่โอนจนกว่าผู้ดูแลจะตรวจสอบ</p><form id="dispute-form" data-request="${requestId}"><div class="field"><label for="dispute-note">เล่าให้เราฟังว่าเกิดอะไรขึ้น</label><textarea id="dispute-note" name="note" required minlength="8" maxlength="500" placeholder="อธิบายปัญหาโดยย่อ"></textarea></div><div class="form-actions"><button type="button" class="btn btn-quiet" data-close-modal>กลับ</button><button class="btn btn-dark" type="submit">ส่งให้ผู้ดูแลตรวจสอบ</button></div></form>`);
}

function starText(rating) {
  const full = Math.round(Number(rating) || 0);
  return "★".repeat(full) + "☆".repeat(5 - full);
}

async function openProfile(memberId) {
  let profile;
  try { profile = (await api(`/api/users/${Number(memberId)}`)).profile; }
  catch (error) { toast(error.message, "error"); return; }
  const hours = profile.taught_minutes / 60;
  const hoursText = Number.isInteger(hours) ? `${hours}` : hours.toFixed(1);
  state.profileOffers = profile.offers.map(offer => ({
    ...offer, provider_name: profile.full_name, faculty: profile.faculty, avatar_url: profile.avatar_url,
    frame: profile.frame, sticker: profile.sticker, provider_bio: profile.bio, rating: profile.rating, review_count: profile.review_count
  }));
  const offers = profile.offers.length
    ? profile.offers.map(offer => `<div class="tp-course"><div class="tp-course-head"><h4>${esc(offer.title)}</h4><span class="tag">${esc(offer.category)}</span></div>
        <p>${esc(offer.description)}</p>
        <div class="tp-course-meta"><span class="level-pill">${esc(offer.level)}</span><span>◷ ${esc(offerAvailabilityText(offer))}</span>${profile.is_self ? "" : `<button type="button" class="mini-btn primary" data-profile-book="${offer.id}">ขอเรียน</button>`}</div></div>`).join("")
    : '<div class="request-empty">ตอนนี้ยังไม่มีคอร์สที่เปิดสอน</div>';
  const reviews = profile.reviews.length
    ? profile.reviews.map(review => `<div class="tp-review"><div class="tp-review-top"><span class="tp-stars" aria-label="${review.rating} จาก 5 ดาว">${starText(review.rating)}</span><small>${esc(review.reviewer_name)} · ${formatDate(review.created_at)}</small></div>${review.offer_title ? `<small class="tp-review-course">คอร์ส: ${esc(review.offer_title)}</small>` : ""}${review.comment ? `<p>${esc(review.comment)}</p>` : ""}</div>`).join("")
    : '<div class="request-empty">ยังไม่มีรีวิว</div>';
  modal(`<div class="tp-head">${miniAvatar(profile.full_name, profile.avatar_url, profile.frame, profile.sticker, "lg")}<div><h2 id="modal-title">${esc(profile.full_name)}</h2><p class="modal-subtitle">${esc(profile.faculty || profile.university || "นักศึกษา")}${profile.is_self ? " · นี่คือโปรไฟล์ของคุณ" : ""}</p></div></div>
    <div class="tp-stats"><div><b>${profile.review_count ? `★ ${Number(profile.rating).toFixed(1)}` : "ใหม่"}</b><small>${profile.review_count ? `${profile.review_count} รีวิว` : "ยังไม่มีรีวิว"}</small></div><div><b>${profile.taught_sessions}</b><small>ครั้งที่สอน</small></div><div><b>${hoursText}</b><small>ชั่วโมงที่สอน</small></div><div><b>${profile.learned_sessions}</b><small>ครั้งที่เรียน</small></div></div>
    ${profile.bio ? `<p class="tp-bio">${esc(profile.bio)}</p>` : ""}
    ${profile.interests.length ? `<div class="tp-interests"><small>สนใจอยากเรียน</small>${profile.interests.map(item => `<span class="tag">${esc(item)}</span>`).join("")}</div>` : ""}
    <h3 class="tp-section">คอร์สที่เปิดสอน (${profile.offers.length})</h3>${offers}
    <h3 class="tp-section">รีวิวจากผู้เรียน (${profile.review_count})</h3>${reviews}`, true);
}

function openReview(requestId) {
  const target = state.dashboard?.requests?.find(item => Number(item.id) === Number(requestId));
  modal(`<div class="modal-icon">★</div><h2 id="modal-title">รีวิวผู้สอน${target ? ` ${esc(target.provider_name)}` : ""}</h2><p class="modal-subtitle">รีวิวช่วยให้ชุมชนเลือกนัดหมายได้มั่นใจขึ้น หลังส่งแล้วคุณจะเห็นโปรไฟล์และคอร์สทั้งหมดของผู้สอน</p><form id="review-form" data-request="${requestId}"><div class="field"><label for="review-rating">ให้คะแนน</label><select id="review-rating" name="rating"><option value="5">★★★★★ · ดีมาก</option><option value="4">★★★★☆ · ดี</option><option value="3">★★★☆☆ · พอใช้</option><option value="2">★★☆☆☆ · ควรปรับปรุง</option><option value="1">★☆☆☆☆ · ไม่พอใจ</option></select></div><div class="field"><label for="review-comment">ความคิดเห็น (ไม่บังคับ)</label><textarea id="review-comment" name="comment" maxlength="400" placeholder="แบ่งปันประสบการณ์ของคุณ"></textarea></div><div class="form-actions"><button type="button" class="btn btn-quiet" data-close-modal>ยกเลิก</button><button class="btn btn-dark" type="submit">ส่งรีวิว</button></div></form>`);
}

function openFilters() {
  modal(`<div class="eyebrow">เลือกหมวดหมู่</div><h2 id="modal-title">ค้นหาทักษะที่ใช่</h2><p class="modal-subtitle">กรองประกาศในตลาดแลกเปลี่ยนทักษะ</p><div class="category-row" style="flex-wrap:wrap">${CATEGORIES.map(category => `<button class="category-chip ${state.category === category ? "selected" : ""}" data-category="${esc(category)}" data-close-after-category="true">${esc(category)}</button>`).join("")}</div><div class="form-actions"><button class="btn btn-outline" data-category="ทั้งหมด" data-close-after-category="true">ล้างตัวกรอง</button><button class="btn btn-dark" data-close-modal>เสร็จแล้ว</button></div>`);
}

async function runAction(action, requestId) {
  if (action === "dispute") { openDispute(requestId); return; }
  if (action === "review") { openReview(requestId); return; }
  if (action === "no-show" && !window.confirm("ยืนยันว่าผู้เรียนไม่มาตามนัด? ระบบจะหักเครดิตชดเชยได้ไม่เกิน 30 นาที")) return;
  if (action === "resolve-cancel" && !window.confirm("ยกเลิกคำขอนี้และปล่อยเครดิตที่กันไว้หรือไม่?")) return;
  let endpoint = action;
  let body;
  if (action === "resolve-complete" || action === "resolve-cancel") {
    endpoint = "resolve";
    body = { outcome: action === "resolve-complete" ? "complete" : "cancel" };
  }
  try {
    await api(`/api/requests/${requestId}/${endpoint}`, { method: "POST", body });
    toast(action === "confirm" ? "บันทึกการยืนยันแล้ว" : action === "accept" ? "ตอบรับคำขอแล้ว" : action === "resolve-complete" ? "ปิดกรณีและโอนเครดิตแล้ว" : "อัปเดตรายการแล้ว");
    await refreshDashboard();
    if (state.view === "admin") { state.admin = await api(adminApiUrl()); renderAdmin(); }
    else renderPrivateView("dashboard");
  } catch (error) { toast(error.message, "error"); }
}

async function showVerificationLink(result, title = "ยืนยันอีเมล") {
  if (result.verification_url) {
    modal(`<div class="eyebrow">ตั้งค่าอีเมล</div><h2 id="modal-title">${esc(title)}</h2><p class="modal-subtitle">ระบบเมลยังไม่ได้เชื่อมต่อ ลิงก์ทดสอบนี้ใช้ได้บนเครื่องที่เปิดเซิร์ฟเวอร์และหมดอายุใน 24 ชั่วโมง</p><a class="btn btn-dark email-action-link" href="${esc(result.verification_url)}">เปิดลิงก์ยืนยัน ↗</a>`);
  } else if (result.email_sent) toast("ส่งอีเมลยืนยันแล้ว กรุณาตรวจกล่องจดหมาย");
  else toast("ยังส่งอีเมลไม่ได้ กรุณาตั้งค่า SMTP ของระบบก่อน", "error");
}

async function resendVerification() {
  try {
    const result = await api("/api/email/resend", { method: "POST", body: {} });
    if (result.already_verified) { toast("อีเมลนี้ยืนยันแล้ว"); return; }
    await showVerificationLink(result);
  } catch (error) { toast(error.message, "error"); }
}

document.addEventListener("click", async event => {
  const target = event.target.closest("button, .offer-card, [data-scroll]");
  if (!target) return;
  if (target.hasAttribute("data-close-modal")) { closeModal(); return; }
  if (target.dataset.auth) { openAuth(target.dataset.auth); return; }
  if (target.dataset.profile) { event.stopPropagation(); openProfile(target.dataset.profile); return; }
  if (target.dataset.profileBook) {
    const offer = state.profileOffers?.find(item => Number(item.id) === Number(target.dataset.profileBook));
    if (offer) { closeModal(); openBooking(offer); }
    return;
  }
  if (target.dataset.view) { setView(target.dataset.view); return; }
  if (target.dataset.shop) { shopAction(target.dataset.shop, target.dataset.item, target.dataset.kind); return; }
  if (target.id === "notification-button") { openNotifications(); return; }
  if (target.dataset.calendarDay) {
    state.calendarSelected = target.dataset.calendarDay;
    await loadCalendar(state.calendarDate);
    return;
  }
  if (target.dataset.scroll) { document.getElementById(target.dataset.scroll)?.scrollIntoView({ behavior: "smooth" }); return; }
  if (target.dataset.category) {
    state.category = target.dataset.category;
    renderCategories();
    await loadOffers();
    if (target.dataset.closeAfterCategory) closeModal();
    return;
  }
  if (target.id === "filter-button") { openFilters(); return; }
  if (target.id === "sort-match") {
    state.sortMatch = !state.sortMatch;
    target.innerHTML = state.sortMatch ? "แสดงตามความเกี่ยวข้อง <span>↗</span>" : "แสดงที่ตรงกับฉันก่อน <span>↗</span>";
    renderOffers();
    return;
  }
  if (target.id === "clear-search") {
    state.query = ""; state.category = "ทั้งหมด"; $("#search-input").value = ""; renderCategories(); await loadOffers(); return;
  }
  if (target.id === "user-menu") { setView("profile"); return; }
  if (target.dataset.action === "forgot-password") { openForgotPassword(); return; }
  if (target.dataset.action === "verify-email") { verifyPendingEmail(); return; }
  if (target.dataset.action === "resend-verification") { resendVerification(); return; }
  if (target.dataset.action === "read-all-notifications") {
    try { await api("/api/notifications/read", { method: "POST", body: {} }); await refreshNotifications(true); renderNotificationModal(); }
    catch (error) { toast(error.message, "error"); }
    return;
  }
  if (target.dataset.action === "open-notification") {
    const notification = state.notifications.find(item => Number(item.id) === Number(target.dataset.id));
    try { await api("/api/notifications/read", { method: "POST", body: { id: Number(target.dataset.id) } }); await refreshNotifications(true); }
    catch { /* continue to the related activity */ }
    const requestId = Number(notification?.request_id || target.dataset.request);
    closeModal();
    if (requestId) setView("dashboard");
    else if (state.user?.role === "admin" && notification?.kind === "report") setView("admin");
    else setView("dashboard");
    return;
  }
  if (target.dataset.action === "calendar-prev" || target.dataset.action === "calendar-next") {
    const offset = target.dataset.action === "calendar-prev" ? -1 : 1;
    state.calendarSelected = null;
    loadCalendar(new Date(state.calendarDate.getFullYear(), state.calendarDate.getMonth() + offset, 1));
    return;
  }
  if (target.dataset.action === "chat") { openChat(target.dataset.request); return; }
  if (target.dataset.action === "report-member") { openReportForm("user", target.dataset.id, target.dataset.request); return; }
  if (target.dataset.action === "report-offer") { openReportForm("offer", target.dataset.id); return; }
  if (target.dataset.action === "report-review") { runReportAction(target.dataset.id, "reviewed"); return; }
  if (target.dataset.action === "report-resolve") { runReportAction(target.dataset.id, "resolved"); return; }
  if (target.dataset.action === "report-dismiss") { runReportAction(target.dataset.id, "dismissed"); return; }
  if (target.dataset.action === "export-user") {
    try { await downloadFile("/api/export", "chronoswap-account.json"); } catch (error) { toast(error.message, "error"); }
    return;
  }
  if (target.dataset.action === "download-backup") {
    try { await downloadFile("/api/admin/backup", "chronoswap-backup.sqlite3"); } catch (error) { toast(error.message, "error"); }
    return;
  }
  if (target.classList.contains("offer-card")) {
    const offer = state.offers.find(item => Number(item.id) === Number(target.dataset.offer));
    if (offer) openBooking(offer);
    return;
  }
  if (target.dataset.action) {
    if (target.dataset.action === "admin-user-toggle" || target.dataset.action === "admin-offer-toggle") {
      runAdminAction(target.dataset.action, target.dataset.id, target.dataset.active === "true");
      return;
    }
    if (target.dataset.action === "admin-offer-delete") {
      runAdminDeleteOffer(target.dataset.id);
      return;
    }
    if (target.dataset.action === "logout") {
      try {
        await api("/api/logout", { method: "POST" });
        state.user = null; state.dashboard = null; state.admin = null;
        setHeader(); await loadOffers(); setView("explore"); toast("ออกจากระบบแล้ว");
      } catch (error) { toast(error.message, "error"); }
      return;
    }
    if (target.dataset.action === "archive-offer") {
      if (!window.confirm("ปิดประกาศนี้ใช่ไหม? ผู้เรียนใหม่จะไม่เห็นประกาศนี้")) return;
      try { await api(`/api/offers/${target.dataset.offer}`, { method: "DELETE" }); await refreshDashboard(); renderPrivateView("dashboard"); toast("ปิดประกาศแล้ว"); }
      catch (error) { toast(error.message, "error"); }
      return;
    }
    runAction(target.dataset.action, target.dataset.request);
  }
});

document.addEventListener("keydown", event => {
  if (event.key === "Escape") closeModal();
  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
    event.preventDefault(); $("#search-input").focus();
  }
  if (event.key === "Enter" && event.target.matches(".offer-card")) event.target.click();
  // แชท: Enter = ส่งข้อความ, Shift+Enter = ขึ้นบรรทัดใหม่
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing && event.target.matches("#chat-text")) {
    event.preventDefault();
    if (event.target.value.trim()) event.target.form.requestSubmit();
  }
});

$("#modal-backdrop").addEventListener("click", event => { if (event.target.id === "modal-backdrop") closeModal(); });
$("#search-input").addEventListener("input", event => {
  window.clearTimeout(window.searchTimer);
  state.query = event.target.value.trim();
  window.searchTimer = window.setTimeout(loadOffers, 180);
});

document.addEventListener("submit", async event => {
  const form = event.target;
  if (!(form instanceof HTMLFormElement)) return;
  event.preventDefault();
  const data = new FormData(form);
  const fields = Object.fromEntries(data.entries());
  const submitButton = $("button[type=submit]", form);
  if (submitButton) submitButton.disabled = true;
  try {
    if (form.id === "auth-form") {
      const mode = form.dataset.mode;
      const payload = mode === "register"
        ? { full_name: fields.full_name, faculty: fields.faculty || "", email: fields.email, password: fields.password }
        : { email: fields.email, password: fields.password };
      const result = await api(`/api/${mode}`, { method: "POST", body: payload });
      state.user = result.user;
      closeModal();
      setHeader();
      await loadOffers();
      if (mode === "register") toast("สร้างบัญชีแล้ว รับเครดิตเริ่มต้น 2 ชั่วโมง");
      else toast("เข้าสู่ระบบแล้ว ยินดีต้อนรับกลับ");
      setView("dashboard");
      if (mode === "register" && result.verification_url) await showVerificationLink(result);
      else if (mode === "register" && result.verification_email_sent) toast("ส่งอีเมลยืนยันแล้ว กรุณาตรวจกล่องจดหมาย");
    } else if (form.id === "booking-form") {
      const scheduled = new Date(`${fields.scheduled_at}:00+07:00`);
      if (!Number.isFinite(scheduled.getTime())) throw new Error("เลือกวันและเวลานัดหมาย");
      const result = await api("/api/requests", { method: "POST", body: { offer_id: Number(form.dataset.offer), duration_minutes: Number(fields.duration_minutes), scheduled_at: scheduled.toISOString(), message: fields.message || "" } });
      closeModal();
      toast(result.message || "ส่งคำขอเรียนแล้ว");
      await refreshDashboard();
      setView("dashboard");
    } else if (form.id === "offer-form") {
      await api("/api/offers", { method: "POST", body: { ...fields, slots: pickedSlots(form) } });
      toast("เผยแพร่ทักษะของคุณแล้ว");
      await loadOffers();
      setView("dashboard");
    } else if (form.id === "profile-form") {
      const interests = (fields.interests || "").split(",").map(item => item.trim()).filter(Boolean).slice(0, 8);
      const body = { full_name: fields.full_name, university: fields.university, faculty: fields.faculty, bio: fields.bio, interests, desired_level: fields.desired_level || "", preferred_slots: pickedSlots(form) };
      if (state.avatarDraft !== null) body.avatar_url = state.avatarDraft;
      else if (state.avatarCleared) body.avatar_url = "";
      await api("/api/profile", { method: "PATCH", body });
      state.avatarDraft = null;
      state.avatarCleared = false;
      await refreshDashboard();
      await loadOffers();
      toast("บันทึกโปรไฟล์แล้ว");
      renderPrivateView("profile");
    } else if (form.id === "dispute-form") {
      await api(`/api/requests/${form.dataset.request}/dispute`, { method: "POST", body: { note: fields.note } });
      closeModal(); await refreshDashboard(); renderPrivateView("dashboard"); toast("ส่งเรื่องให้ผู้ดูแลแล้ว");
    } else if (form.id === "review-form") {
      await api(`/api/requests/${form.dataset.request}/review`, { method: "POST", body: { rating: Number(fields.rating), comment: fields.comment || "" } });
      const reviewed = state.dashboard?.requests?.find(item => Number(item.id) === Number(form.dataset.request));
      closeModal(); await refreshDashboard(); renderPrivateView("dashboard"); toast("ขอบคุณสำหรับรีวิว");
      if (reviewed) openProfile(reviewed.provider_id);
    } else if (form.id === "chat-form") {
      const requestId = Number(form.dataset.request);
      await api(`/api/requests/${requestId}/messages`, { method: "POST", body: { body: fields.body } });
      form.reset();
      await loadChatMessages(requestId, true);
      $("#chat-text")?.focus();
    } else if (form.id === "report-form") {
      const requestId = form.dataset.request ? Number(form.dataset.request) : null;
      await api("/api/reports", { method: "POST", body: { target_type: form.dataset.targetType, target_id: Number(form.dataset.targetId), request_id: requestId, reason: fields.reason, details: fields.details || "" } });
      closeModal();
      toast("ส่งรายงานให้แอดมินตรวจสอบแล้ว");
    } else if (form.id === "forgot-password-form") {
      const result = await api("/api/password-reset/request", { method: "POST", body: { email: fields.email } });
      if (result.reset_url) {
        modal(`<div class="eyebrow">ตั้งค่าการส่งอีเมล</div><h2 id="modal-title">ลิงก์ตั้งรหัสผ่านใหม่</h2><p class="modal-subtitle">${esc(result.message)} ในโหมดทดสอบสามารถใช้ลิงก์นี้ได้ ลิงก์จะหมดอายุใน 1 ชั่วโมง</p><a class="btn btn-dark email-action-link" href="${esc(result.reset_url)}">เปิดลิงก์ตั้งรหัสผ่าน ↗</a>`);
      } else {
        toast(result.message);
        closeModal();
      }
    } else if (form.id === "reset-password-form") {
      const result = await api("/api/password-reset/complete", { method: "POST", body: { token: state.pendingEmailToken, password: fields.password } });
      state.pendingEmailToken = null;
      closeModal();
      toast(result.message);
      openAuth("login");
    }
  } catch (error) {
    toast(error.message, "error");
  } finally {
    if (submitButton) submitButton.disabled = false;
  }
});

document.addEventListener("change", async event => {
  if (event.target.id !== "profile-avatar-input") return;
  const file = event.target.files?.[0];
  if (!file) return;
  if (!/^image\/(jpeg|png|webp)$/.test(file.type) || file.size > 10 * 1024 * 1024) {
    toast("เลือกรูป JPG, PNG หรือ WebP ขนาดไม่เกิน 10 MB", "error");
    event.target.value = "";
    return;
  }
  const objectUrl = URL.createObjectURL(file);
  try {
    const image = new Image();
    image.src = objectUrl;
    await image.decode();
    const scale = Math.min(1, 640 / Math.max(image.naturalWidth, image.naturalHeight));
    const canvas = document.createElement("canvas");
    canvas.width = Math.max(1, Math.round(image.naturalWidth * scale));
    canvas.height = Math.max(1, Math.round(image.naturalHeight * scale));
    canvas.getContext("2d").drawImage(image, 0, 0, canvas.width, canvas.height);
    state.avatarDraft = canvas.toDataURL("image/jpeg", 0.84);
    state.avatarCleared = false;
    $("#profile-avatar-box").innerHTML = `<img src="${state.avatarDraft}" alt="รูปโปรไฟล์" id="profile-avatar-preview">`;
    if (!$("#remove-profile-avatar")) $(".profile-photo-actions").insertAdjacentHTML("beforeend", '<button type="button" class="mini-btn danger" id="remove-profile-avatar">ลบรูป</button>');
  } catch {
    toast("เปิดไฟล์รูปนี้ไม่ได้ ลองเลือกรูปอื่น", "error");
  } finally {
    URL.revokeObjectURL(objectUrl);
  }
});

document.addEventListener("click", event => {
  if (event.target.id !== "remove-profile-avatar") return;
  state.avatarDraft = null;
  state.avatarCleared = true;
  const box = $("#profile-avatar-box");
  const name = $("#profile-name")?.value || state.dashboard?.user?.full_name || "สมาชิก";
  box.innerHTML = `<span id="profile-avatar-fallback">${esc(initials(name))}</span>`;
  event.target.remove();
});

async function init() {
  renderCategories();
  try {
    const data = await api("/api/session");
    state.user = data.user;
  } catch { state.user = null; }
  setHeader();
  await loadOffers();
  if (state.user) {
    try { await refreshDashboard(); } catch { /* session state will be refreshed when a private page is opened */ }
    setHeader();
  }
  const query = new URLSearchParams(window.location.search);
  const hash = new URLSearchParams(window.location.hash.replace(/^#/, ""));
  const verifyToken = query.get("verify_token") || hash.get("verify_token");
  const resetToken = query.get("reset_token") || hash.get("reset_token");
  if (verifyToken || resetToken) {
    window.history.replaceState({}, "", window.location.pathname);
    if (verifyToken) openVerifyEmail(verifyToken);
    else openResetPassword(resetToken);
  }
}

init();
