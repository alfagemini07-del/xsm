"use strict";
const rail = document.querySelector(".admin-sidebar");
const toggle = rail.querySelector(".rail-toggle");
const narrow = matchMedia("(max-width: 900px)");
function openMenu(open) {
  document.body.classList.toggle("rail-closed", !open);
  toggle.setAttribute("aria-expanded", String(open));
}
toggle.hidden = false;
openMenu(!narrow.matches);
narrow.addEventListener("change", () => openMenu(!narrow.matches));
toggle.addEventListener("click", () => openMenu(toggle.getAttribute("aria-expanded") === "false"));
rail.querySelector("nav").addEventListener("click", event => {
  if (narrow.matches && event.target.closest("button")) { openMenu(false); toggle.focus(); }
});
document.addEventListener("keydown", event => {
  if (event.key === "Escape" && narrow.matches && toggle.getAttribute("aria-expanded") === "true") {
    openMenu(false); toggle.focus();
  }
});
