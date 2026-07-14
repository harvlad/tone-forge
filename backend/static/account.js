/* Account chip + magic-link sign-in + device identity.
 *
 * Self-contained: owns the persistent device id (localStorage
 * tf_device_id), the header account chip, and the email sign-in modal.
 * jam.js merges `window.tfAccount.deviceHeaders()` into analyze/history
 * fetches so anonymous analyses are stamped with this device and get
 * claimed on first sign-in.
 */
(function () {
  "use strict";

  // ---- device identity --------------------------------------------------
  function deviceId() {
    let id = null;
    try {
      id = localStorage.getItem("tf_device_id");
      if (!id) {
        id =
          window.crypto && crypto.randomUUID
            ? crypto.randomUUID()
            : "web-" + Date.now() + "-" + Math.random().toString(36).slice(2);
        localStorage.setItem("tf_device_id", id);
      }
    } catch (e) {
      /* private mode: per-page id, analyses just stay anonymous */
      id = id || "web-ephemeral";
    }
    return id;
  }

  function deviceHeaders() {
    return { "X-Device-Id": deviceId() };
  }

  // ---- API --------------------------------------------------------------
  async function fetchSession() {
    const r = await fetch("/api/auth/session", { headers: deviceHeaders() });
    if (!r.ok) return null;
    const data = await r.json();
    return data.user || null;
  }

  async function requestMagicLink(email) {
    const r = await fetch("/api/auth/magic-link", {
      method: "POST",
      headers: Object.assign(
        { "Content-Type": "application/json" },
        deviceHeaders()
      ),
      body: JSON.stringify({ email: email }),
    });
    return r.status === 202;
  }

  async function signOut() {
    await fetch("/api/auth/logout", {
      method: "POST",
      headers: deviceHeaders(),
    });
    try {
      localStorage.removeItem("tf_claimed");
    } catch (e) {}
  }

  // One-time claim after sign-in: attaches this device's anonymous
  // analyses to the account. Guarded so we don't re-post on every load.
  async function maybeClaim() {
    try {
      if (localStorage.getItem("tf_claimed")) return;
    } catch (e) {
      return;
    }
    try {
      const r = await fetch("/api/auth/claim", {
        method: "POST",
        headers: Object.assign(
          { "Content-Type": "application/json" },
          deviceHeaders()
        ),
        body: JSON.stringify({ device_id: deviceId() }),
      });
      if (r.ok) {
        localStorage.setItem("tf_claimed", "1");
        const data = await r.json();
        if (data.claimed > 0) {
          console.log("[account] claimed " + data.claimed + " analyses");
        }
      }
    } catch (e) {
      /* retry next load */
    }
  }

  // ---- UI ---------------------------------------------------------------
  function el(id) {
    return document.getElementById(id);
  }

  function renderChip(user) {
    const chip = el("account-chip");
    if (!chip) return;
    chip.innerHTML = "";
    if (user) {
      const label = document.createElement("span");
      label.className = "account-email";
      label.textContent = user.display_name || user.email || "Signed in";
      const out = document.createElement("button");
      out.className = "account-btn";
      out.textContent = "Sign out";
      out.addEventListener("click", async () => {
        await signOut();
        renderChip(null);
      });
      chip.appendChild(label);
      chip.appendChild(out);
    } else {
      const btn = document.createElement("button");
      btn.className = "account-btn";
      btn.textContent = "Sign in";
      btn.addEventListener("click", openModal);
      chip.appendChild(btn);
    }
  }

  function openModal() {
    const modal = el("account-modal");
    if (!modal) return;
    el("account-modal-form").style.display = "";
    el("account-modal-sent").style.display = "none";
    el("account-email-input").value = "";
    modal.style.display = "flex";
    el("account-email-input").focus();
  }

  function closeModal() {
    const modal = el("account-modal");
    if (modal) modal.style.display = "none";
  }

  async function submitEmail(ev) {
    ev.preventDefault();
    const email = el("account-email-input").value.trim();
    if (!email) return;
    const ok = await requestMagicLink(email);
    if (ok) {
      el("account-modal-form").style.display = "none";
      el("account-modal-sent").style.display = "";
    } else {
      el("account-email-error").textContent =
        "Couldn't send the link — try again in a minute.";
    }
  }

  async function init() {
    const modal = el("account-modal");
    if (modal) {
      modal.addEventListener("click", (ev) => {
        if (ev.target === modal) closeModal();
      });
      const form = el("account-email-form");
      if (form) form.addEventListener("submit", submitEmail);
      const cancel = el("account-modal-cancel");
      if (cancel) cancel.addEventListener("click", closeModal);
    }

    if (new URLSearchParams(location.search).get("auth_error")) {
      console.warn("[account] sign-in link expired");
    }

    let user = null;
    try {
      user = await fetchSession();
    } catch (e) {}
    renderChip(user);
    if (user) await maybeClaim();
  }

  window.tfAccount = {
    deviceId: deviceId,
    deviceHeaders: deviceHeaders,
    fetchSession: fetchSession,
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
