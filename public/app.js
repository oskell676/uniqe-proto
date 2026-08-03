// ---------- element refs ----------
const authView = document.getElementById("authView");
const chatView = document.getElementById("chatView");
const authForm = document.getElementById("authForm");
const authIdentifier = document.getElementById("authIdentifier");
const authPassword = document.getElementById("authPassword");
const authSubmit = document.getElementById("authSubmit");
const authError = document.getElementById("authError");
const authTabs = document.querySelectorAll(".auth-tab");
const userIndicator = document.getElementById("userIndicator");

const authCard = document.getElementById("authCard");
const verifyCard = document.getElementById("verifyCard");
const verifyEmailLabel = document.getElementById("verifyEmailLabel");
const verifyForm = document.getElementById("verifyForm");
const verifyCode = document.getElementById("verifyCode");
const verifySubmit = document.getElementById("verifySubmit");
const verifyError = document.getElementById("verifyError");
const resendCodeBtn = document.getElementById("resendCodeBtn");
const cancelVerifyBtn = document.getElementById("cancelVerifyBtn");

const forgotPasswordBtn = document.getElementById("forgotPasswordBtn");
const forgotCard = document.getElementById("forgotCard");
const forgotForm = document.getElementById("forgotForm");
const forgotIdentifier = document.getElementById("forgotIdentifier");
const forgotSubmit = document.getElementById("forgotSubmit");
const forgotMessage = document.getElementById("forgotMessage");
const backToLoginBtn = document.getElementById("backToLoginBtn");

const resetCard = document.getElementById("resetCard");
const resetForm = document.getElementById("resetForm");
const resetNewPassword = document.getElementById("resetNewPassword");
const resetConfirmPassword = document.getElementById("resetConfirmPassword");
const resetSubmit = document.getElementById("resetSubmit");
const resetMessage = document.getElementById("resetMessage");

const chatEl = document.getElementById("chat");
const emptyStateEl = document.getElementById("emptyState");
const form = document.getElementById("composer");
const input = document.getElementById("input");
const sendBtn = document.getElementById("sendBtn");
const checkinBtn = document.getElementById("checkinBtn");
const resetBtn = document.getElementById("resetBtn");
const logoutBtn = document.getElementById("logoutBtn");

const settingsBtn = document.getElementById("settingsBtn");
const settingsView = document.getElementById("settingsView");
const closeSettingsBtn = document.getElementById("closeSettingsBtn");
const settingsFolderList = document.getElementById("settingsFolderList");
const settingsFolders = document.querySelectorAll(".settings-folder");
const folderRows = document.querySelectorAll(".folder-row");
const folderBackBtns = document.querySelectorAll(".folder-back");
const settingsIdentifier = document.getElementById("settingsIdentifier");
const settingsCreatedAt = document.getElementById("settingsCreatedAt");
const passwordForm = document.getElementById("passwordForm");
const currentPasswordInput = document.getElementById("currentPassword");
const newPasswordInput = document.getElementById("newPassword");
const confirmPasswordInput = document.getElementById("confirmPassword");
const passwordSubmit = document.getElementById("passwordSubmit");
const passwordMessage = document.getElementById("passwordMessage");

const enableNotificationsBtn = document.getElementById("enableNotificationsBtn");
const notificationsStatus = document.getElementById("notificationsStatus");
const notificationsMessage = document.getElementById("notificationsMessage");
const installBtn = document.getElementById("installBtn");
const installModal = document.getElementById("installModal");
const closeInstallBtn = document.getElementById("closeInstallBtn");
const installNativeSection = document.getElementById("installNativeSection");
const installNativeBtn = document.getElementById("installNativeBtn");
const installIosSection = document.getElementById("installIosSection");
const installAndroidSection = document.getElementById("installAndroidSection");
const installDesktopSection = document.getElementById("installDesktopSection");

const calendarBtn = document.getElementById("calendarBtn");
const calendarView = document.getElementById("calendarView");
const closeCalendarBtn = document.getElementById("closeCalendarBtn");
const calendarPrevBtn = document.getElementById("calendarPrevBtn");
const calendarNextBtn = document.getElementById("calendarNextBtn");
const calendarMonthLabel = document.getElementById("calendarMonthLabel");
const calendarGrid = document.getElementById("calendarGrid");
const calendarDetail = document.getElementById("calendarDetail");
const calendarDetailDate = document.getElementById("calendarDetailDate");
const calendarDetailNote = document.getElementById("calendarDetailNote");
const calendarDetailPositive = document.getElementById("calendarDetailPositive");
const calendarLoading = document.getElementById("calendarLoading");

const adminBtn = document.getElementById("adminBtn");
const adminView = document.getElementById("adminView");
const closeAdminBtn = document.getElementById("closeAdminBtn");
const adminLoading = document.getElementById("adminLoading");
const adminError = document.getElementById("adminError");
const statTotalUsers = document.getElementById("statTotalUsers");
const statNew7d = document.getElementById("statNew7d");
const statActive24h = document.getElementById("statActive24h");
const statActive7d = document.getElementById("statActive7d");
const statPushEnabled = document.getElementById("statPushEnabled");
const statTotalMessages = document.getElementById("statTotalMessages");
const statMessages7d = document.getElementById("statMessages7d");
const statEstCost = document.getElementById("statEstCost");

let authMode = "login"; // or "signup"
let currentUser = null;
let pendingIdentifier = null;
let deferredInstallPrompt = null;
let resetToken = null;
let calendarYear = null;
let calendarMonth = null; // 1-12
let calendarDays = {}; // date string -> {note, positive}
let selectedCalendarDate = null;

function getUserTimezone() {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "";
  } catch (err) {
    return "";
  }
}

// ---------- view switching ----------

function hideAllAuthCards() {
  authCard.hidden = true;
  verifyCard.hidden = true;
  forgotCard.hidden = true;
  resetCard.hidden = true;
}

function showAuthView() {
  authView.hidden = false;
  chatView.hidden = true;
  settingsView.hidden = true;
  calendarView.hidden = true;
  adminView.hidden = true;
  hideAllAuthCards();
  authCard.hidden = false;
  pendingIdentifier = null;
}

function showVerifyView(identifier) {
  pendingIdentifier = identifier;
  verifyEmailLabel.textContent = identifier;
  verifyForm.reset();
  verifyError.hidden = true;
  verifyError.classList.remove("auth-success");
  authView.hidden = false;
  chatView.hidden = true;
  settingsView.hidden = true;
  calendarView.hidden = true;
  adminView.hidden = true;
  hideAllAuthCards();
  verifyCard.hidden = false;
  verifyCode.focus();
}

function showForgotView() {
  authView.hidden = false;
  chatView.hidden = true;
  settingsView.hidden = true;
  calendarView.hidden = true;
  adminView.hidden = true;
  hideAllAuthCards();
  forgotCard.hidden = false;
  forgotForm.reset();
  forgotMessage.hidden = true;
  forgotMessage.classList.remove("auth-success");
  forgotIdentifier.focus();
}

function showResetView() {
  authView.hidden = false;
  chatView.hidden = true;
  settingsView.hidden = true;
  calendarView.hidden = true;
  adminView.hidden = true;
  hideAllAuthCards();
  resetCard.hidden = false;
  resetMessage.hidden = true;
  resetMessage.classList.remove("auth-success");
  resetNewPassword.focus();
}

function showChatView(user) {
  currentUser = user;
  authView.hidden = true;
  chatView.hidden = false;
  settingsView.hidden = true;
  calendarView.hidden = true;
  adminView.hidden = true;
  adminBtn.hidden = !user.is_admin;
  userIndicator.textContent = user.identifier;
  chatEl.innerHTML = "";
  const empty = document.createElement("div");
  empty.className = "empty-state";
  empty.id = "emptyState";
  empty.textContent =
    "Dette er begynnelsen på samtalen. Skriv noe under, eller trykk «Simuler innsjekk» for å se hvordan det føles når UNIQE tar kontakt selv.";
  chatEl.appendChild(empty);
  loadHistory();
}

function setAuthMode(mode) {
  authMode = mode;
  authTabs.forEach((tab) => tab.classList.toggle("active", tab.dataset.mode === mode));
  authSubmit.textContent = mode === "login" ? "Logg inn" : "Opprett bruker";
  authPassword.autocomplete = mode === "login" ? "current-password" : "new-password";
  authError.hidden = true;
  authError.classList.remove("auth-success");
}

authTabs.forEach((tab) => {
  tab.addEventListener("click", () => setAuthMode(tab.dataset.mode));
});

authForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const identifier = authIdentifier.value.trim();
  const password = authPassword.value;
  authError.hidden = true;
  authSubmit.disabled = true;

  try {
    const res = await fetch(`/api/auth/${authMode}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({ identifier, password, timezone: getUserTimezone() }),
    });
    const data = await res.json();
    if (!res.ok) {
      authError.textContent = data.error || "Noe gikk galt.";
      authError.hidden = false;
      return;
    }
    authForm.reset();
    if (data.verification_required) {
      showVerifyView(data.identifier);
      return;
    }
    showChatView(data.user);
    if (data.show_install_prompt) openInstallModal();
  } catch (err) {
    authError.textContent = "Klarte ikke å nå serveren.";
    authError.hidden = false;
  } finally {
    authSubmit.disabled = false;
  }
});

logoutBtn.addEventListener("click", async () => {
  await fetch("/api/auth/logout", { method: "POST", credentials: "same-origin" });
  showAuthView();
  setAuthMode("login");
});

// ---------- email verification ----------

verifyForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  if (!pendingIdentifier) return;
  verifyError.hidden = true;
  verifySubmit.disabled = true;

  try {
    const res = await fetch("/api/auth/verify-email", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({
        identifier: pendingIdentifier,
        code: verifyCode.value.trim(),
        timezone: getUserTimezone(),
      }),
    });
    const data = await res.json();
    if (!res.ok) {
      verifyError.textContent = data.error || "Noe gikk galt.";
      verifyError.hidden = false;
      return;
    }
    verifyForm.reset();
    showChatView(data.user);
    if (data.show_install_prompt) openInstallModal();
  } catch (err) {
    verifyError.textContent = "Klarte ikke å nå serveren.";
    verifyError.hidden = false;
  } finally {
    verifySubmit.disabled = false;
  }
});

resendCodeBtn.addEventListener("click", async () => {
  if (!pendingIdentifier) return;
  resendCodeBtn.disabled = true;
  verifyError.hidden = true;
  try {
    const res = await fetch("/api/auth/resend-code", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({ identifier: pendingIdentifier }),
    });
    const data = await res.json();
    verifyError.classList.toggle("auth-success", res.ok);
    verifyError.textContent = res.ok
      ? "Ny kode sendt."
      : data.error || "Noe gikk galt.";
    verifyError.hidden = false;
  } catch (err) {
    verifyError.classList.remove("auth-success");
    verifyError.textContent = "Klarte ikke å nå serveren.";
    verifyError.hidden = false;
  } finally {
    resendCodeBtn.disabled = false;
  }
});

cancelVerifyBtn.addEventListener("click", () => {
  showAuthView();
  setAuthMode("login");
});

// ---------- forgot / reset password ----------

forgotPasswordBtn.addEventListener("click", showForgotView);

backToLoginBtn.addEventListener("click", () => {
  showAuthView();
  setAuthMode("login");
});

forgotForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const identifier = forgotIdentifier.value.trim();
  forgotMessage.hidden = true;
  forgotMessage.classList.remove("auth-success");
  forgotSubmit.disabled = true;

  try {
    const res = await fetch("/api/auth/forgot-password", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({ identifier }),
    });
    const data = await res.json();
    forgotMessage.textContent = res.ok
      ? (data.message || "Hvis denne e-posten er registrert, har vi sendt en lenke.")
      : (data.error || "Noe gikk galt.");
    forgotMessage.classList.toggle("auth-success", res.ok);
    forgotMessage.hidden = false;
    if (res.ok) forgotForm.reset();
  } catch (err) {
    forgotMessage.classList.remove("auth-success");
    forgotMessage.textContent = "Klarte ikke å nå serveren.";
    forgotMessage.hidden = false;
  } finally {
    forgotSubmit.disabled = false;
  }
});

function extractResetToken() {
  const params = new URLSearchParams(window.location.search);
  const token = params.get("reset_token");
  if (!token) return false;
  resetToken = token;
  window.history.replaceState({}, "", window.location.pathname);
  return true;
}

resetForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const newPassword = resetNewPassword.value;
  const confirmPassword = resetConfirmPassword.value;
  resetMessage.hidden = true;
  resetMessage.classList.remove("auth-success");

  if (newPassword !== confirmPassword) {
    resetMessage.textContent = "Passordene er ikke like.";
    resetMessage.hidden = false;
    return;
  }

  resetSubmit.disabled = true;
  try {
    const res = await fetch("/api/auth/reset-password", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({ token: resetToken, new_password: newPassword }),
    });
    const data = await res.json();
    if (!res.ok) {
      resetMessage.textContent = data.error || "Noe gikk galt.";
      resetMessage.hidden = false;
      return;
    }
    resetToken = null;
    resetForm.reset();
    showAuthView();
    setAuthMode("login");
    authError.textContent = "Passordet er tilbakestilt. Logg inn med det nye passordet.";
    authError.classList.add("auth-success");
    authError.hidden = false;
  } catch (err) {
    resetMessage.textContent = "Klarte ikke å nå serveren.";
    resetMessage.hidden = false;
  } finally {
    resetSubmit.disabled = false;
  }
});

// ---------- settings page ----------

function formatJoinedDate(unixSeconds) {
  if (!unixSeconds) return "";
  const date = new Date(unixSeconds * 1000);
  return date.toLocaleDateString("nb-NO", { year: "numeric", month: "long", day: "numeric" });
}

function updateNotificationsStatus() {
  if (!("Notification" in window)) {
    notificationsStatus.textContent = "Nettleseren din støtter ikke varsler.";
    enableNotificationsBtn.hidden = true;
    return;
  }
  enableNotificationsBtn.hidden = false;
  if (Notification.permission === "granted") {
    notificationsStatus.textContent = "Varsler er på for denne enheten.";
    enableNotificationsBtn.textContent = "🔔 På";
    enableNotificationsBtn.disabled = true;
  } else {
    notificationsStatus.textContent = "Få et varsel selv om UNIQE er lukket.";
    enableNotificationsBtn.textContent = "🔔 Slå på";
    enableNotificationsBtn.disabled = false;
  }
}

function showSettingsFolderList() {
  settingsFolderList.hidden = false;
  settingsFolders.forEach((el) => { el.hidden = true; });
}

function showSettingsFolder(name) {
  const targetId = "settingsFolder" + name.charAt(0).toUpperCase() + name.slice(1);
  settingsFolderList.hidden = true;
  settingsFolders.forEach((el) => { el.hidden = el.id !== targetId; });
}

folderRows.forEach((row) => {
  row.addEventListener("click", () => showSettingsFolder(row.dataset.folder));
});
folderBackBtns.forEach((btn) => {
  btn.addEventListener("click", showSettingsFolderList);
});

function showSettingsView() {
  if (!currentUser) return;
  settingsIdentifier.textContent = currentUser.identifier;
  settingsCreatedAt.textContent = currentUser.created_at
    ? `Medlem siden ${formatJoinedDate(currentUser.created_at)}`
    : "";
  passwordForm.reset();
  passwordMessage.hidden = true;
  passwordMessage.classList.remove("auth-success");
  notificationsMessage.hidden = true;
  notificationsMessage.classList.remove("auth-success");
  updateNotificationsStatus();
  showSettingsFolderList();
  chatView.hidden = true;
  calendarView.hidden = true;
  adminView.hidden = true;
  settingsView.hidden = false;
}

function hideSettingsView() {
  settingsView.hidden = true;
  calendarView.hidden = true;
  adminView.hidden = true;
  chatView.hidden = false;
}

settingsBtn.addEventListener("click", showSettingsView);
closeSettingsBtn.addEventListener("click", hideSettingsView);
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !settingsView.hidden) hideSettingsView();
  if (e.key === "Escape" && !calendarView.hidden) hideCalendarView();
});

passwordForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const currentPassword = currentPasswordInput.value;
  const newPassword = newPasswordInput.value;
  const confirmPassword = confirmPasswordInput.value;

  passwordMessage.classList.remove("auth-success");
  passwordMessage.hidden = true;

  if (newPassword !== confirmPassword) {
    passwordMessage.textContent = "De nye passordene er ikke like.";
    passwordMessage.hidden = false;
    return;
  }

  passwordSubmit.disabled = true;
  try {
    const res = await fetch("/api/auth/change-password", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
    });
    const data = await res.json();
    if (res.status === 401 && data.error === "Du må logge inn først.") {
      showAuthView();
      return;
    }
    if (!res.ok) {
      passwordMessage.textContent = data.error || "Noe gikk galt.";
      passwordMessage.hidden = false;
      return;
    }
    passwordMessage.textContent = "Passordet er oppdatert.";
    passwordMessage.classList.add("auth-success");
    passwordMessage.hidden = false;
    passwordForm.reset();
  } catch (err) {
    passwordMessage.textContent = "Klarte ikke å nå serveren.";
    passwordMessage.hidden = false;
  } finally {
    passwordSubmit.disabled = false;
  }
});

// ---------- calendar ----------

const CALENDAR_MONTH_NAMES = [
  "januar", "februar", "mars", "april", "mai", "juni",
  "juli", "august", "september", "oktober", "november", "desember",
];

function pad2(n) {
  return String(n).padStart(2, "0");
}

function daysInMonth(year, month) {
  return new Date(year, month, 0).getDate();
}

function firstWeekdayOffset(year, month) {
  const jsDay = new Date(year, month - 1, 1).getDay(); // 0=Sun..6=Sat
  return (jsDay + 6) % 7; // convert to Monday-first: 0=Man..6=Søn
}

function renderCalendarGrid() {
  calendarMonthLabel.textContent = `${CALENDAR_MONTH_NAMES[calendarMonth - 1]} ${calendarYear}`;
  calendarGrid.innerHTML = "";

  const offset = firstWeekdayOffset(calendarYear, calendarMonth);
  const total = daysInMonth(calendarYear, calendarMonth);

  for (let i = 0; i < offset; i++) {
    const filler = document.createElement("button");
    filler.className = "calendar-day";
    filler.type = "button";
    filler.disabled = true;
    calendarGrid.appendChild(filler);
  }

  for (let day = 1; day <= total; day++) {
    const dateStr = `${calendarYear}-${pad2(calendarMonth)}-${pad2(day)}`;
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "calendar-day";
    btn.textContent = String(day);
    if (calendarDays[dateStr]) btn.classList.add("has-note");
    if (dateStr === selectedCalendarDate) btn.classList.add("selected");
    btn.addEventListener("click", () => selectCalendarDate(dateStr));
    calendarGrid.appendChild(btn);
  }
}

function selectCalendarDate(dateStr) {
  const entry = calendarDays[dateStr];
  if (!entry) {
    selectedCalendarDate = null;
    calendarDetail.hidden = true;
    renderCalendarGrid();
    return;
  }
  selectedCalendarDate = dateStr;
  const [y, m, d] = dateStr.split("-").map(Number);
  const label = new Date(y, m - 1, d).toLocaleDateString("nb-NO", {
    weekday: "long", day: "numeric", month: "long",
  });
  calendarDetailDate.textContent = label.charAt(0).toUpperCase() + label.slice(1);
  calendarDetailNote.textContent = entry.note;
  calendarDetailPositive.textContent = entry.positive;
  calendarDetail.hidden = false;
  renderCalendarGrid();
}

async function loadCalendarMonth() {
  calendarLoading.hidden = false;
  calendarDetail.hidden = true;
  selectedCalendarDate = null;
  calendarGrid.innerHTML = "";
  try {
    const monthParam = `${calendarYear}-${pad2(calendarMonth)}`;
    const res = await fetch(`/api/calendar?month=${monthParam}`, { credentials: "same-origin" });
    if (res.status === 401) {
      showAuthView();
      return;
    }
    const data = await res.json();
    calendarDays = data.days || {};
  } catch (err) {
    calendarDays = {};
  } finally {
    calendarLoading.hidden = true;
    renderCalendarGrid();
  }
}

function showCalendarView() {
  if (!currentUser) return;
  const now = new Date();
  calendarYear = now.getFullYear();
  calendarMonth = now.getMonth() + 1;
  chatView.hidden = true;
  settingsView.hidden = true;
  adminView.hidden = true;
  calendarView.hidden = false;
  loadCalendarMonth();
}

function hideCalendarView() {
  calendarView.hidden = true;
  adminView.hidden = true;
  chatView.hidden = false;
}

function shiftCalendarMonth(delta) {
  let m = calendarMonth + delta;
  let y = calendarYear;
  if (m < 1) { m = 12; y -= 1; }
  if (m > 12) { m = 1; y += 1; }
  calendarMonth = m;
  calendarYear = y;
  loadCalendarMonth();
}

calendarBtn.addEventListener("click", showCalendarView);
closeCalendarBtn.addEventListener("click", hideCalendarView);
calendarPrevBtn.addEventListener("click", () => shiftCalendarMonth(-1));
calendarNextBtn.addEventListener("click", () => shiftCalendarMonth(1));

// ---------- admin dashboard ----------

function formatNumber(n) {
  return new Intl.NumberFormat("nb-NO").format(n);
}

async function loadAdminStats() {
  adminLoading.hidden = false;
  adminError.hidden = true;
  try {
    const res = await fetch("/api/admin/stats", { credentials: "same-origin" });
    if (res.status === 401) {
      showAuthView();
      return;
    }
    if (!res.ok) {
      adminError.textContent = "Fikk ikke hentet tallene.";
      adminError.hidden = false;
      return;
    }
    const data = await res.json();
    statTotalUsers.textContent = formatNumber(data.total_users);
    statNew7d.textContent = formatNumber(data.new_signups_7d);
    statActive24h.textContent = formatNumber(data.active_users_24h);
    statActive7d.textContent = formatNumber(data.active_users_7d);
    statPushEnabled.textContent = formatNumber(data.push_enabled);
    statTotalMessages.textContent = formatNumber(data.total_messages);
    statMessages7d.textContent = formatNumber(data.messages_7d);
    statEstCost.textContent = `${formatNumber(Math.round(data.estimated_monthly_ai_cost_kr))} kr`;
  } catch (err) {
    adminError.textContent = "Klarte ikke å nå serveren.";
    adminError.hidden = false;
  } finally {
    adminLoading.hidden = true;
  }
}

function showAdminView() {
  if (!currentUser) return;
  chatView.hidden = true;
  settingsView.hidden = true;
  calendarView.hidden = true;
  adminView.hidden = false;
  loadAdminStats();
}

function hideAdminView() {
  adminView.hidden = true;
  chatView.hidden = false;
}

adminBtn.addEventListener("click", showAdminView);
closeAdminBtn.addEventListener("click", hideAdminView);
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !adminView.hidden) hideAdminView();
});

// ---------- service worker + push notifications ----------

function urlBase64ToUint8Array(base64String) {
  const padding = "=".repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding).replace(/-/g, "+").replace(/_/g, "/");
  const rawData = atob(base64);
  return Uint8Array.from([...rawData].map((c) => c.charCodeAt(0)));
}

let swRegistration = null;

async function registerServiceWorker() {
  if (!("serviceWorker" in navigator)) return null;
  try {
    swRegistration = await navigator.serviceWorker.register("/sw.js");
    return swRegistration;
  } catch (err) {
    console.warn("Kunne ikke registrere service worker:", err);
    return null;
  }
}

if ("serviceWorker" in navigator) {
  navigator.serviceWorker.addEventListener("message", (event) => {
    // The service worker sends this when a check-in notification is tapped
    // while the app is already open in the background — focusing a window
    // doesn't reload it, so without this the new message would never
    // actually appear even though it's already saved server-side.
    if (event.data && event.data.type === "checkin-opened" && currentUser) {
      showChatView(currentUser);
    }
  });
}

async function enableNotifications() {
  notificationsMessage.hidden = true;
  notificationsMessage.classList.remove("auth-success");

  if (!("serviceWorker" in navigator) || !("PushManager" in window)) {
    notificationsMessage.textContent = "Nettleseren din støtter ikke push-varsler.";
    notificationsMessage.hidden = false;
    return;
  }

  enableNotificationsBtn.disabled = true;
  try {
    const reg = swRegistration || (await registerServiceWorker());
    if (!reg) throw new Error("no service worker");

    const permission = await Notification.requestPermission();
    if (permission !== "granted") {
      notificationsMessage.textContent = "Du må tillate varsler i nettleseren for at dette skal fungere.";
      notificationsMessage.hidden = false;
      return;
    }

    const keyRes = await fetch("/api/push/vapid-public-key", { credentials: "same-origin" });
    if (!keyRes.ok) throw new Error("no vapid key");
    const { publicKey } = await keyRes.json();

    const subscription = await reg.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: urlBase64ToUint8Array(publicKey),
    });

    const subRes = await fetch("/api/push/subscribe", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify(subscription.toJSON()),
    });
    if (!subRes.ok) throw new Error("subscribe failed");

    notificationsMessage.textContent = "Varsler er slått på for denne enheten.";
    notificationsMessage.classList.add("auth-success");
    notificationsMessage.hidden = false;
    updateNotificationsStatus();
  } catch (err) {
    notificationsMessage.textContent = "Fikk ikke slått på varsler. Prøv igjen.";
    notificationsMessage.hidden = false;
  } finally {
    enableNotificationsBtn.disabled = false;
  }
}

enableNotificationsBtn.addEventListener("click", enableNotifications);

// ---------- add to home screen ----------

window.addEventListener("beforeinstallprompt", (e) => {
  e.preventDefault();
  deferredInstallPrompt = e;
});

function detectPlatform() {
  const ua = navigator.userAgent || "";
  const isIos = /iPad|iPhone|iPod/.test(ua) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
  const isAndroid = /Android/.test(ua);
  return { isIos, isAndroid };
}

function openInstallModal() {
  installNativeSection.hidden = true;
  installIosSection.hidden = true;
  installAndroidSection.hidden = true;
  installDesktopSection.hidden = true;

  if (deferredInstallPrompt) {
    installNativeSection.hidden = false;
  } else {
    const { isIos, isAndroid } = detectPlatform();
    if (isIos) installIosSection.hidden = false;
    else if (isAndroid) installAndroidSection.hidden = false;
    else installDesktopSection.hidden = false;
  }

  installModal.hidden = false;
}

function closeInstallModal() {
  installModal.hidden = true;
}

installBtn.addEventListener("click", openInstallModal);
closeInstallBtn.addEventListener("click", closeInstallModal);
installModal.addEventListener("click", (e) => {
  if (e.target === installModal) closeInstallModal();
});

installNativeBtn.addEventListener("click", async () => {
  if (!deferredInstallPrompt) return;
  deferredInstallPrompt.prompt();
  await deferredInstallPrompt.userChoice;
  deferredInstallPrompt = null;
  closeInstallModal();
});

// ---------- chat rendering ----------

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}

function hideEmptyState() {
  const el = document.getElementById("emptyState");
  if (el) el.remove();
}

function renderMessage({ role, content, proactive }) {
  hideEmptyState();
  const bubble = document.createElement("div");
  bubble.className = `msg ${role}`;

  if (proactive) {
    const tag = document.createElement("div");
    tag.className = "proactive-tag";
    tag.innerHTML = `<span class="glow"></span> kom uoppfordret`;
    bubble.appendChild(tag);
  }

  const body = document.createElement("div");
  body.innerHTML = escapeHtml(content);
  bubble.appendChild(body);

  chatEl.appendChild(bubble);
  chatEl.scrollTop = chatEl.scrollHeight;
  return bubble;
}

function renderError(message) {
  hideEmptyState();
  const bubble = document.createElement("div");
  bubble.className = "msg error";
  bubble.textContent = message;
  chatEl.appendChild(bubble);
  chatEl.scrollTop = chatEl.scrollHeight;
}

function showTyping() {
  const el = document.createElement("div");
  el.className = "typing";
  el.id = "typingIndicator";
  el.innerHTML = "<span></span><span></span><span></span>";
  chatEl.appendChild(el);
  chatEl.scrollTop = chatEl.scrollHeight;
}

function hideTyping() {
  const el = document.getElementById("typingIndicator");
  if (el) el.remove();
}

function setBusy(busy) {
  sendBtn.disabled = busy;
  checkinBtn.disabled = busy;
  input.disabled = busy;
}

async function loadHistory() {
  try {
    const res = await fetch("/api/history", { credentials: "same-origin" });
    if (res.status === 401) {
      showAuthView();
      return;
    }
    const data = await res.json();
    if (data.messages && data.messages.length > 0) {
      data.messages.forEach(renderMessage);
    }
  } catch (err) {
    renderError("Klarte ikke å laste tidligere samtale.");
  }
}

async function sendMessage(text) {
  renderMessage({ role: "user", content: text });
  setBusy(true);
  showTyping();
  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({ message: text }),
    });
    if (res.status === 401) {
      hideTyping();
      showAuthView();
      return;
    }
    const data = await res.json();
    hideTyping();
    if (!res.ok) {
      renderError(data.error || "Noe gikk galt.");
      return;
    }
    renderMessage({ role: "assistant", content: data.reply });
  } catch (err) {
    hideTyping();
    renderError("Klarte ikke å nå serveren.");
  } finally {
    setBusy(false);
    input.focus();
  }
}

async function simulateCheckin() {
  setBusy(true);
  showTyping();
  try {
    const res = await fetch("/api/checkin", { method: "POST", credentials: "same-origin" });
    if (res.status === 401) {
      hideTyping();
      showAuthView();
      return;
    }
    const data = await res.json();
    hideTyping();
    if (!res.ok) {
      renderError(data.error || "Noe gikk galt.");
      return;
    }
    renderMessage({ role: "assistant", content: data.reply, proactive: true });
  } catch (err) {
    hideTyping();
    renderError("Klarte ikke å nå serveren.");
  } finally {
    setBusy(false);
  }
}

form.addEventListener("submit", (e) => {
  e.preventDefault();
  const text = input.value.trim();
  if (!text) return;
  input.value = "";
  input.style.height = "auto";
  sendMessage(text);
});

input.addEventListener("input", () => {
  input.style.height = "auto";
  input.style.height = Math.min(input.scrollHeight, 120) + "px";
});

input.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    form.requestSubmit();
  }
});

checkinBtn.addEventListener("click", simulateCheckin);

resetBtn.addEventListener("click", async () => {
  if (!confirm("Slette hele samtalen og starte på nytt?")) return;
  await fetch("/api/reset", { method: "POST", credentials: "same-origin" });
  chatEl.innerHTML = "";
  location.reload();
});

// ---------- bootstrap ----------

(async function init() {
  registerServiceWorker();

  if (extractResetToken()) {
    showResetView();
    return;
  }
  try {
    const res = await fetch("/api/auth/me", { credentials: "same-origin" });
    if (res.ok) {
      const data = await res.json();
      showChatView(data.user);
    } else {
      showAuthView();
    }
  } catch (err) {
    showAuthView();
  }
})();
