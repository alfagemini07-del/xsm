"use strict";
const byId = (id) => document.getElementById(id);
document.querySelectorAll("[data-action]").forEach((b) =>
  b.addEventListener("click", () => {
    byId("actions").hidden = true;
    byId("vehicle").hidden = false;
    byId("action").value = b.dataset.action;
    byId("title").textContent =
      b.dataset.action === "in"
        ? "Kirayotgan avtomobil"
        : "Chiqayotgan avtomobil";
    byId("plate").focus();
  }),
);
byId("back")?.addEventListener("click", () => {
  byId("vehicle").hidden = true;
  byId("actions").hidden = false;
});
byId("plate")?.addEventListener("input", () => {
  byId("plate").value = byId("plate").value.toUpperCase();
});
let rowPage = 0;
function showRows() {
  const rows = [...document.querySelectorAll(".journal-list p")];
  const size = Math.max(
    1,
    Math.min(8, Math.floor((innerHeight * 0.88 - 210) / 38)),
  );
  const pages = Math.max(1, Math.ceil(rows.length / size));
  rowPage = Math.max(0, Math.min(rowPage, pages - 1));
  rows.forEach(
    (row, i) => (row.hidden = i < rowPage * size || i >= (rowPage + 1) * size),
  );
  byId("rows-page").textContent = rows.length
    ? rowPage + 1 + " / " + pages
    : "Yozuv yo‘q";
  byId("rows-prev").disabled = rowPage === 0;
  byId("rows-next").disabled = rowPage === pages - 1;
}
function openJournal() {
  showRows();
  byId("journal").showModal();
}
byId("rows-prev")?.addEventListener("click", () => {
  rowPage--;
  showRows();
});
byId("rows-next")?.addEventListener("click", () => {
  rowPage++;
  showRows();
});
window.addEventListener("resize", () => {
  if (byId("journal")?.open) showRows();
});
byId("journal-open")?.addEventListener("click", openJournal);
byId("journal-close")?.addEventListener("click", () => byId("journal").close());
if (location.hash === "#journal" && byId("journal")) openJournal();
byId("print")?.addEventListener("click", () => window.print());
document.querySelectorAll("form").forEach((f) =>
  f.addEventListener("submit", () => {
    f.querySelectorAll("button").forEach((b) => (b.disabled = true));
  }),
);
window.addEventListener("pageshow", (e) => {
  if (e.persisted) location.reload();
});
async function heartbeat() {
  try {
    const r = await fetch("/api/kpp/heartbeat", {
      method: "POST",
      headers: { "X-CSRF-Token": document.body.dataset.csrf },
      signal: AbortSignal.timeout(8000),
    });
    if (r.status === 401 || r.status === 403) {
      location.href = "/login";
      return;
    }
    byId("connection").textContent = r.ok ? "● Online" : "○ Aloqa kechikmoqda";
  } catch {
    byId("connection").textContent = "○ Server bilan aloqa yo‘q";
  }
}
if (
  ["kpp2", "ayritom"].includes(document.querySelector("header").dataset.role)
) {
  heartbeat();
  setInterval(heartbeat, 30000);
}
