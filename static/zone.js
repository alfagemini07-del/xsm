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
for (const form of document.querySelectorAll("form:not([method=dialog])")) {
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
  const panel = document.querySelector(".network-panel");
  const switcher = panel.querySelector(".view-switch");
  const detail = document.querySelector("#place-detail");
  const status = document.querySelector("#refresh-status");
  function chooseView(view) {
    panel.dataset.view = view;
    for (const button of switcher.querySelectorAll("button")) {
      button.setAttribute("aria-pressed", String(button.dataset.view === view));
    }
  }
  function enableChart() {
    switcher.hidden = false;
    chooseView(matchMedia("(min-width: 761px)").matches ? "depth" : "flat");
  }
  if ("IntersectionObserver" in window) {
    const observer = new IntersectionObserver(entries => {
      if (entries.some(entry => entry.isIntersecting)) { enableChart(); observer.disconnect(); }
    });
    observer.observe(panel);
  } else { enableChart(); }
  switcher.addEventListener("click", event => {
    if (event.target.dataset.view) chooseView(event.target.dataset.view);
  });
  panel.addEventListener("click", event => {
    const link = event.target.closest("[data-count]");
    if (!link || !detail.showModal || event.ctrlKey || event.metaKey) return;
    event.preventDefault();
    detail.querySelector("h2").textContent = link.dataset.name;
    detail.querySelector("p").textContent = link.dataset.count + " ta transport";
    detail.querySelector("a").href = link.getAttribute("href");
    detail.showModal();
  });
  const liveKeys = ["outside", "inside", "border", "zone", "inside_kpp2", "inside_ayritom"];
  function updateLiveStats(stats) {
    stats.outside = Number(stats.border || 0) + Number(stats.zone || 0);
    for (const key of liveKeys) {
      const value = Number(stats[key] || 0);
      document.querySelectorAll(`[data-kpi="${key}"]`).forEach(node => { node.textContent = String(value); });
      document.querySelectorAll(`.plot-item[data-key="${key}"]`).forEach(item => {
        item.dataset.count = String(value);
        item.querySelector("b").textContent = String(value);
        const progress = item.querySelector("progress");
        if (progress) progress.value = value;
      });
    }
    const maximum = Math.max(1, ...["border", "zone", "inside_kpp2", "inside_ayritom"].map(key => Number(stats[key] || 0)));
    document.querySelectorAll(".plot-node[data-key]").forEach(node => {
      const key = node.dataset.key;
      const value = Number(stats[key] || 0);
      const x = Number(node.dataset.x);
      const top = Math.round(191 - (value / maximum) * 132);
      node.dataset.count = String(value);
      node.setAttribute("aria-label", `${node.dataset.name}: ${value} ta transport`);
      node.querySelector("title").textContent = `${node.dataset.name} · ${value} ta`;
      node.querySelector(".front").setAttribute("d", `M${x} 191V${top}h67V191Z`);
      node.querySelector(".side").setAttribute("d", `M${x + 67} 191V${top}l20 -16V175Z`);
      node.querySelector(".top").setAttribute("d", `M${x} ${top}l20 -16h67l-20 16Z`);
      const count = node.querySelector(".node-count");
      count.textContent = String(value);
      count.setAttribute("y", String(Math.max(38, top - 13)));
    });
    document.querySelectorAll(".plot-item progress").forEach(progress => { progress.max = maximum; });
  }
  function updateFeed(events) {
    const feed = document.querySelector(".event-feed");
    if (!feed) return;
    feed.replaceChildren();
    if (!events.length) {
      const empty = document.createElement("p");
      empty.className = "empty-feed";
      empty.textContent = "Hozircha harakat qayd etilmagan.";
      feed.append(empty);
      return;
    }
    for (const event of events) {
      const row = document.createElement("div");
      const mark = document.createElement("i");
      mark.setAttribute("aria-hidden", "true");
      mark.textContent = event.action === "in" ? "↘" : "↗";
      const text = document.createElement("span");
      const plate = document.createElement("b");
      plate.textContent = event.plate;
      const meta = document.createElement("small");
      meta.textContent = `${event.point} · ${event.action === "in" ? "Kirish" : "Chiqish"}`;
      text.append(plate, meta);
      const time = document.createElement("time");
      time.textContent = event.time;
      row.append(mark, text, time);
      feed.append(row);
    }
  }
  let busy = false;
  setInterval(async () => {
    if (busy || document.hidden || detail.open) return;
    busy = true;
    try {
      const response = await fetch("/api/zone/live", {cache:"no-store", signal:AbortSignal.timeout(8000), headers:{Accept:"application/json"}});
      if (!response.ok) throw new Error();
      const data = await response.json();
      updateLiveStats(data.stats || {});
      updateFeed(data.events || []);
      status.textContent = "Yangilandi · " + new Date().toLocaleTimeString("uz-UZ", {timeZone:"Asia/Tashkent"});
    } catch { status.textContent = "Yangilash kechikdi · qayta tekshirilmoqda"; }
    finally { busy = false; }
  }, 30000);
}
for (const form of document.querySelectorAll("form[action='/zone/confirm']")) {
  form.addEventListener("submit", () => { form.querySelector("button").disabled = true; });
}
