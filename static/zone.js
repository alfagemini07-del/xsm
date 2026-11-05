"use strict";
const actionStep = document.getElementById("action-step");
const vehicleStep = document.getElementById("vehicle-step");
const plateField = document.getElementById("vehicle-plate");
function showAction() {
  vehicleStep.hidden = true;
  actionStep.hidden = false;
  document.querySelector("[data-action]")?.focus();
}
for (const button of document.querySelectorAll("[data-action]")) {
  button.addEventListener("click", () => {
    const action = button.dataset.action;
    document.getElementById("movement-action").value = action;
    document.getElementById("vehicle-title").textContent = action === "in" ? "Kirayotgan avtomobil" : "Chiqayotgan avtomobil";
    const purpose = document.getElementById("purpose-field");
    if (purpose) { purpose.hidden = action !== "in"; purpose.disabled = action !== "in"; }
    actionStep.hidden = true;
    vehicleStep.hidden = false;
    plateField.focus();
  });
}
document.getElementById("back-action")?.addEventListener("click", showAction);
document.getElementById("clear-plate")?.addEventListener("click", () => { plateField.value = ""; plateField.focus(); });
plateField?.addEventListener("input", () => {
  const start = plateField.selectionStart;
  plateField.value = plateField.value.toUpperCase();
  plateField.setSelectionRange(start, start);
});
document.addEventListener("keydown", event => {
  if (event.key === "Escape" && vehicleStep && !vehicleStep.hidden) showAction();
});
for (const form of document.querySelectorAll("form")) {
  form.addEventListener("submit", () => {
    if (form.dataset.submitting) return;
    form.dataset.submitting = "1";
    const button = form.querySelector("button[type=submit],button:not([type])");
    if (button) { button.disabled = true; button.textContent = "Bajarilmoqda…"; }
  });
}
window.addEventListener("pageshow", event => {
  if (event.persisted) location.reload();
});
document.getElementById("print")?.addEventListener("click", () => window.print());
if (document.body.dataset.mode === "receipt" && new URL(location.href).searchParams.has("print_now")) {
  history.replaceState(null, "", location.pathname);
  window.addEventListener("load", () => window.print(), {once:true});
}
if (document.body.dataset.mode === "dashboard") {
  let busy = false;
  setInterval(async () => {
    if (busy || document.hidden) return;
    busy = true;
    try {
      const response = await fetch(location.href, {cache:"no-store", signal:AbortSignal.timeout(10000)});
      if (!response.ok) return;
      const next = new DOMParser().parseFromString(await response.text(), "text/html");
      if (next.body.dataset.mode !== "dashboard") return;
      // Keep the user's filters, pagination and unfinished tariff edits in place.
      for (const selector of [".metrics", ".amount", ".distribution", ".card .scroll", "#active nav"]) {
        const blocks = next.querySelectorAll(selector);
        document.querySelectorAll(selector).forEach((block, i) => {
          if (blocks[i]) block.replaceChildren(...Array.from(blocks[i].childNodes));
        });
      }
    } catch { /* The current snapshot remains usable during a network interruption. */ }
    finally { busy = false; }
  }, 30000);
}
for (const form of document.querySelectorAll("form[action='/zone/confirm']")) {
  form.addEventListener("submit", () => { form.querySelector("button").disabled = true; });
}
