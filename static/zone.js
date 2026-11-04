"use strict";
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
