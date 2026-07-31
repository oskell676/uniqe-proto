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
const settingsModal = document.getElementById("settingsModal");
const closeSettingsBtn = document.getElementById("closeSettingsBtn");
const settingsIdentifier = document.getElementById("settingsIdentifier");
const settingsCreatedAt = document.getElementById("settingsCreatedAt");
const passwordForm = document.getElementById("passwordForm");
const currentPasswordInput = document.getElementById("currentPassword");
const newPasswordInput = document.getElementById("newPassword");
const confirmPasswordInput = document.getElementById("confirmPassword");
const passwordSubmit = document.getElementById("passwordSubmit");
const passwordMessage = document.getElementById("passwordMessage");

let authMode = "login"; // or "signup"
let currentUser = null;
let pendingIdentifier = null;
let resetToken = null;

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
  hideAllAuthCards();
  verifyCard.hidden = false;
  verifyCode.focus();
}

function showForgotView() {
  authView.hidden = false;
  chatView.hidden = true;
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
      body: JSON.stringify({ identifier, password }),
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
      body: JSON.stringify({ identifier: pendingIdentifier, code: verifyCode.value.trim() }),
    });
    const data = await res.json();
    if (!res.ok) {
      verifyError.textContent = data.error || "Noe gikk galt.";
      verifyError.hidden = false;
      return;
    }
    verifyForm.reset();
    showChatView(data.user);
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

// ---------- settings modal ----------

function formatJoinedDate(unixSeconds) {
  if (!unixSeconds) return "";
  const date = new Date(unixSeconds * 1000);
  return date.toLocaleDateString("nb-NO", { year: "numeric", month: "long", day: "numeric" });
}

function openSettings() {
  if (!currentUser) return;
  settingsIdentifier.textContent = currentUser.identifier;
  settingsCreatedAt.textContent = currentUser.created_at
    ? `Medlem siden ${formatJoinedDate(currentUser.created_at)}`
    : "";
  passwordForm.reset();
  passwordMessage.hidden = true;
  passwordMessage.classList.remove("auth-success");
  settingsModal.hidden = false;
}

function closeSettings() {
  settingsModal.hidden = true;
}

settingsBtn.addEventListener("click", openSettings);
closeSettingsBtn.addEventListener("click", closeSettings);
settingsModal.addEventListener("click", (e) => {
  if (e.target === settingsModal) closeSettings();
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !settingsModal.hidden) closeSettings();
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
      closeSettings();
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
