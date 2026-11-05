"use strict";
const password = document.getElementById("access-password");
const toggle = document.getElementById("toggle-password");
toggle?.addEventListener("click", () => {
  const visible = password.type === "password";
  password.type = visible ? "text" : "password";
  toggle.textContent = visible ? "Yashirish" : "Ko‘rsatish";
  toggle.setAttribute("aria-pressed", String(visible));
  toggle.setAttribute("aria-label", visible ? "Parolni yashirish" : "Parolni ko‘rsatish");
});
password?.addEventListener("keyup", event => {
  document.getElementById("caps-warning").hidden = !event.getModifierState("CapsLock");
});
document.getElementById("access-form")?.addEventListener("submit", event => {
  const button = event.currentTarget.querySelector("[type=submit]");
  button.disabled = true;
  button.textContent = "Kirilmoqda…";
  document.getElementById("login-progress").textContent = "Hisob tekshirilmoqda. Oynani ochiq qoldiring.";
});
window.addEventListener("pageshow", event => { if (event.persisted && password) location.reload(); });
