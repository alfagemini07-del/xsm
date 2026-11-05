"use strict";
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
if (document.body.dataset.mode === "dashboard") {
  const status = document.querySelector("#refresh-status");
  const liveKeys = ["outside", "inside", "border", "zone", "inside_kpp2", "inside_ayritom", "inside_unknown"];
  function updateLiveStats(stats) {
    stats.outside = Number(stats.border || 0) + Number(stats.zone || 0);
    for (const key of liveKeys) {
      const value = Number(stats[key] || 0);
      document.querySelectorAll(`[data-kpi="${key}"]`).forEach(node => { node.textContent = String(value); });
    }
    document.querySelector("#unknown-gate").hidden = !Number(stats.inside_unknown);
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
    if (busy || document.hidden) return;
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
