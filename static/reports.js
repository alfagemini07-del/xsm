(() => {
  "use strict";
  const el = id => document.getElementById(id);
  const fmt = n => Number(n || 0).toLocaleString("ru-RU").replace(/\u00a0/g, " ");
  let page = 1, pages = 1, sequence = 0, loaded = false, loadController, lastRows = [];
  const today = () => new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Tashkent", year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date());

  function range(kind) {
    const end = today();
    const start = new Date(end + "T12:00:00Z");
    if (kind === "yesterday") {
      start.setUTCDate(start.getUTCDate() - 1);
      const yStr = start.toISOString().slice(0, 10);
      el("analytics-start").value = yStr;
      el("analytics-end").value = yStr;
      return;
    }
    if (kind === "week") start.setUTCDate(start.getUTCDate() - 6);
    if (kind === "month") start.setUTCDate(1);
    el("analytics-start").value = start.toISOString().slice(0, 10);
    el("analytics-end").value = end;
  }

  function params() {
    return new URLSearchParams({
      point: el("analytics-point").value,
      start: el("analytics-start").value,
      end: el("analytics-end").value,
      group: el("analytics-group").value,
      operator: el("analytics-operator").value,
      method: el("analytics-method").value,
      page,
      per_page: el("analytics-size").value
    });
  }

  function drawAnalyticsChart(rows, mode) {
    const canvas = el("analytics-chart");
    if (!canvas) return;
    const wrap = canvas.closest(".analytics-chart-card");
    if (!rows || !rows.length || ["detailed", "cancelled", "reprints"].includes(mode)) {
      if (wrap) wrap.classList.add("hidden");
      return;
    }
    if (wrap) wrap.classList.remove("hidden");

    const items = rows.slice(0, 14).map(r => ({
      label: String(r[0] || "—").slice(0, 16),
      count: Number(r[1] || 0),
      total: Number(r[2] || 0)
    }));
    if (["day", "hour", "week", "month"].includes(mode)) items.reverse();

    const ratio = window.devicePixelRatio || 1;
    const width = Math.max(360, canvas.clientWidth || 760);
    const height = Number(canvas.getAttribute("height")) || 230;
    canvas.width = width * ratio;
    canvas.height = height * ratio;
    const ctx = canvas.getContext("2d");
    ctx.scale(ratio, ratio);
    ctx.clearRect(0, 0, width, height);

    const pad = { top: 26, right: 20, bottom: 42, left: 42 };
    const graphW = width - pad.left - pad.right;
    const graphH = height - pad.top - pad.bottom;
    const maxTotal = Math.max(1, ...items.map(i => i.total));
    const maxCount = Math.max(1, ...items.map(i => i.count));

    ctx.strokeStyle = "rgba(148,163,184,0.14)";
    ctx.lineWidth = 1;
    for (let i = 0; i <= 4; i++) {
      const y = pad.top + (graphH * i) / 4;
      ctx.beginPath();
      ctx.moveTo(pad.left, y);
      ctx.lineTo(width - pad.right, y);
      ctx.stroke();
    }

    const slot = graphW / items.length;
    const barW = Math.min(48, slot * 0.56);

    items.forEach((item, idx) => {
      const tH = (graphH * item.total) / maxTotal;
      const cH = (graphH * item.count) / maxCount;
      const x = pad.left + slot * idx + (slot - barW) / 2;
      const tY = pad.top + graphH - tH;
      const cY = pad.top + graphH - cH;

      const grad = ctx.createLinearGradient(0, tY, 0, pad.top + graphH);
      grad.addColorStop(0, "#38bdf8");
      grad.addColorStop(1, "rgba(16,185,129,0.35)");
      ctx.fillStyle = grad;
      ctx.beginPath();
      ctx.roundRect(x, tY, barW, Math.max(3, tH), 6);
      ctx.fill();

      ctx.fillStyle = "#10b981";
      ctx.beginPath();
      ctx.roundRect(x + barW * 0.64, cY, barW * 0.36, Math.max(3, cH), 4);
      ctx.fill();

      ctx.fillStyle = "#94a3b8";
      ctx.font = "600 10.5px 'Segoe UI', sans-serif";
      ctx.textAlign = "center";
      ctx.fillText(item.label, x + barW / 2, height - 14);

      ctx.fillStyle = "#e2e8f0";
      ctx.font = "700 10px 'Segoe UI', sans-serif";
      const valShort = item.total >= 1000000 ? (item.total / 1000000).toFixed(1) + "M" : (item.total >= 1000 ? Math.round(item.total / 1000) + "K" : String(item.total));
      ctx.fillText(valShort, x + barW / 2, Math.max(14, tY - 6));
    });
  }

  async function load() {
    const ticket = ++sequence;
    el("analytics-error").textContent = "Yuklanmoqda...";
    loadController?.abort();
    loadController = new AbortController();
    const controller = loadController, timer = setTimeout(() => controller.abort(), 20000);
    try {
      const r = await fetch(`/api/admin/analytics?${params()}`, { signal: controller.signal });
      const d = await r.json();
      if (!r.ok) throw new Error(d.message || "Hisobotni olib bo'lmadi. Sanalarni tekshiring.");
      if (ticket !== sequence) return;
      page = d.page;
      pages = d.pages;
      loaded = true;
      lastRows = d.rows || [];

      el("analytics-title").textContent = d.title;
      el("analytics-error").dataset.asof = "Hisob: " + d.as_of + " (Toshkent) · Oxirgi kelgan: " + d.last_received;

      const head = document.createElement("tr");
      d.headers.forEach(text => {
        const th = document.createElement("th");
        th.textContent = text;
        head.append(th);
      });
      el("analytics-head").replaceChildren(head);

      el("analytics-body").replaceChildren();
      d.rows.forEach(row => {
        const tr = document.createElement("tr");
        row.forEach((value, idx) => {
          const td = document.createElement("td");
          if (typeof value === "number") {
            td.textContent = fmt(value);
            td.style.fontVariantNumeric = "tabular-nums";
            if (idx === 2 || (d.mode === "detailed" && idx === 8)) {
              td.style.color = "#4edea3";
              td.style.fontWeight = "700";
            }
          } else {
            td.textContent = value ?? "—";
            if (idx === 0) td.style.fontWeight = "700";
          }
          tr.append(td);
        });
        el("analytics-body").append(tr);
      });

      const tot = d.totals || {};
      const totalRev = Number(tot.total || 0);
      const pct = v => totalRev > 0 ? Math.round((Number(v || 0) * 100) / totalRev) + "%" : "0%";
      el("analytics-summary").replaceChildren();
      const summaryCards = [
        ["Jami tushum", fmt(tot.total) + " so‘m", "To‘langan: " + fmt(tot.paid) + " ta amal", "green"],
        ["Naqd pul", fmt(tot.cash) + " so‘m", "Ulushi: " + pct(tot.cash), "blue"],
        ["Plastik karta", fmt(tot.card) + " so‘m", "Ulushi: " + pct(tot.card), "violet"],
        ["Hisob raqam", fmt(tot.bank) + " so‘m", "Ulushi: " + pct(tot.bank), "orange"],
        ["Tarozi va xizmatlar", fmt((tot.weighing || 0) + (tot.extra || 0)) + " so‘m", "Parkovka/KPP: " + fmt(tot.parking || 0) + " so‘m", "blue"],
        ["Bekor qilingan", fmt(tot.cancelled) + " ta", "Jamiga qo‘shilmaydi", "red"]
      ];
      for (const [label, val, sub, color] of summaryCards) {
        const card = document.createElement("div");
        card.className = "an-kpi-card an-" + color;
        const sm = document.createElement("small");
        sm.textContent = label;
        const st = document.createElement("strong");
        st.textContent = val;
        const em = document.createElement("em");
        em.textContent = sub;
        card.append(sm, st, em);
        el("analytics-summary").append(card);
      }

      drawAnalyticsChart(d.rows, d.mode || el("analytics-group").value);

      el("analytics-count").textContent = `${fmt(d.total)} qator`;
      el("analytics-page").textContent = `${page} / ${pages}`;
      el("analytics-prev").disabled = page <= 1;
      el("analytics-next").disabled = page >= pages;
      el("analytics-error").textContent = (d.total ? "" : "Ma’lumot topilmadi. ") + el("analytics-error").dataset.asof;
    } catch (error) {
      if (ticket === sequence) {
        el("analytics-error").textContent = error.name === "AbortError" ? "Server javobi kechikdi. Qayta urinib ko‘ring." : error.message;
      }
    } finally {
      clearTimeout(timer);
    }
  }

  async function download(url, filename, button) {
    if (button.disabled) return;
    button.disabled = true;
    el("analytics-error").textContent = "Excel / PDF fayl tayyorlanmoqda...";
    try {
      const r = await fetch(url);
      if (!r.ok) {
        const d = await r.json().catch(() => ({}));
        throw new Error(d.message || "Eksport bajarilmadi. Davrni yoki filtrni tekshiring.");
      }
      const blob = await r.blob();
      const link = document.createElement("a");
      link.href = URL.createObjectURL(blob);
      link.download = filename;
      document.body.append(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(link.href), 10000);
      el("analytics-error").textContent = "Fayl muvaffaqiyatli yuklab olindi ✓";
    } catch (error) {
      el("analytics-error").textContent = error.message;
    } finally {
      button.disabled = false;
    }
  }

  el("analytics-load").addEventListener("click", () => { page = 1; load(); });
  ["analytics-point", "analytics-group", "analytics-operator", "analytics-method", "analytics-size"].forEach(id => {
    if (el(id)) el(id).addEventListener("change", () => { page = 1; load(); });
  });
  document.querySelectorAll("[data-analytics-range]").forEach(b => b.addEventListener("click", () => {
    document.querySelectorAll("[data-analytics-range]").forEach(btn => btn.classList.toggle("active", btn === b));
    range(b.dataset.analyticsRange);
    page = 1;
    load();
  }));
  el("analytics-prev").addEventListener("click", () => { if (page > 1) { page--; load(); } });
  el("analytics-next").addEventListener("click", () => { if (page < pages) { page++; load(); } });

  document.querySelectorAll("[data-analytics-export]").forEach(b => b.addEventListener("click", () => {
    const kind = b.dataset.analyticsExport;
    const s = el("analytics-start").value || "hisobot";
    const e = el("analytics-end").value || "";
    if (kind === "full_xlsx") {
      download(`/api/admin/operations/export.xlsx?${params()}`, `AIRITOM-Toliq-5-Varaqli-Hisobot-${s}-${e}.xlsx`, b);
    } else {
      download(`/api/admin/analytics/export/${kind}?${params()}`, `AIRITOM-${el("analytics-group").value}-${s}-${e}.${kind}`, b);
    }
  }));

  el("closing-cash").addEventListener("input", () => {
    const digits = el("closing-cash").value.replace(/\D/g, "").slice(0, 16);
    el("closing-cash").value = digits ? fmt(digits) : "";
  });
  el("closing-download").addEventListener("click", event => {
    if (el("analytics-start").value !== el("analytics-end").value || el("analytics-method").value || !el("closing-cash").value) {
      el("analytics-error").textContent = "Bitta kun, barcha to'lovlar va haqiqiy naqd summani kiriting.";
      return;
    }
    const p = params();
    p.set("actual_cash", el("closing-cash").value.replace(/\D/g, ""));
    download(`/api/admin/cash-closing.pdf?${p}`, `kassa-${el("analytics-start").value}.pdf`, event.currentTarget);
  });

  range("week");
  let started = false;
  function enter() {
    if (started) {
      if (lastRows.length) drawAnalyticsChart(lastRows, el("analytics-group").value);
      return;
    }
    started = true;
    if (!loaded) load();
    fetch("/api/admin/report-operators", { signal: AbortSignal.timeout(20000) })
      .then(r => r.json())
      .then(d => {
        (d.users || []).forEach(u => {
          const option = document.createElement("option");
          option.value = u.id;
          option.textContent = u.username;
          el("analytics-operator").append(option);
        });
      })
      .catch(() => {
        el("analytics-error").textContent = "Operatorlar ro'yxati yuklanmadi. Sahifani yangilang.";
      });
  }
  document.addEventListener("admin:tab", e => { if (e.detail === "analytics") enter(); });
  window.addEventListener("resize", () => {
    if (!el("tab-analytics").classList.contains("hidden") && lastRows.length) {
      drawAnalyticsChart(lastRows, el("analytics-group").value);
    }
  });
  if (!el("tab-analytics").classList.contains("hidden")) enter();
})();
