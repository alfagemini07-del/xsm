(() => {
  "use strict";
  const csrf = document.body.dataset.csrf;
  const role = document.body.dataset.role;
  const localAgentEnabled = document.body.dataset.localPrinterAgent === "1";
  let weeklyData = [];
  let reportData = [];
  let reportPage = 1;
  let reportPages = 1;
  let systemData = [];
  let historyPage = 1;
  let historyPages = 1;
  let searchPage = 1;
  let searchPages = 1;
  let usersPage = 1;
  let usersPages = 1;
  let resetUserId = null;
  let selectedDetail = null;
  let monitorTimer = null;
  let activeTab = document.body.dataset.defaultTab || (role === "techadmin" ? "monitor" : "dashboard");
  let lastSyncVersion = null;
  let syncBusy = false;

  async function api(url, options = {}) {
    const headers = new Headers(options.headers || {});
    headers.set("Accept", "application/json");
    if (options.body && !(options.body instanceof FormData)) headers.set("Content-Type", "application/json");
    if (options.method && options.method !== "GET") headers.set("X-CSRF-Token", csrf);
    const response = await fetch(url, { credentials: "same-origin", ...options, headers });
    const data = await response.json().catch(() => ({ success: false, message: "Server javobi noto'g'ri" }));
    if (response.status === 401) { window.location.href = "/login"; throw new Error("Kirish talab qilinadi"); }
    if (!response.ok || data.success === false) throw new Error(data.message || "So'rov bajarilmadi");
    return data;
  }

  function showToast(message, error = false) {
    const toast = document.getElementById("toast");
    document.getElementById("toast-text").textContent = message;
    document.getElementById("toast-icon").textContent = error ? "!" : "✓";
    toast.classList.toggle("error", error); toast.classList.remove("hidden");
    clearTimeout(showToast.timer); showToast.timer = setTimeout(() => toast.classList.add("hidden"), 4300);
  }

  function setBusy(button, busy, text = "Bajarilmoqda...") {
    if (!button) return;
    if (!button.dataset.label) button.dataset.label = button.textContent;
    button.disabled = busy; button.textContent = busy ? text : button.dataset.label;
  }
  const fmt = value => Number(value || 0).toLocaleString("ru-RU").replace(/\u00a0/g, " ");
  const isoDate = date => new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
  const bytes = value => {
    let n = Number(value || 0); const units = ["B", "KB", "MB", "GB", "TB"]; let index = 0;
    while (n >= 1024 && index < units.length - 1) { n /= 1024; index += 1; }
    return `${n.toFixed(index ? 1 : 0)} ${units[index]}`;
  };
  const uptime = seconds => { const d = Math.floor(seconds / 86400); const h = Math.floor(seconds % 86400 / 3600); const m = Math.floor(seconds % 3600 / 60); return d ? `${d} kun ${h} soat` : h ? `${h} soat ${m} daq` : `${m} daqiqa`; };

  function td(text, className = "") { const cell = document.createElement("td"); cell.textContent = text ?? "—"; if (className) cell.className = className; return cell; }
  function emptyRow(body, columns, text) { body.replaceChildren(); const row = document.createElement("tr"); const cell = td(text, "table-empty"); cell.colSpan = columns; row.append(cell); body.append(row); }
  function badge(status) { const span = document.createElement("span"); span.className = `badge ${status}`; span.textContent = status === "paid" ? "To'langan" : status === "pending" ? "Kutilmoqda" : "Bekor qilingan"; return span; }

  function setClock() { document.getElementById("admin-clock").textContent = new Date().toLocaleTimeString("uz-UZ", { hour12: false }); }
  async function checkAgent() {
    const label = document.getElementById("admin-printer-status"); if (!label) return;
    if (!localAgentEnabled) { label.textContent = "Brauzer rejimi"; label.parentElement.classList.add("offline"); return; }
    const controller = new AbortController(); const timer = setTimeout(() => controller.abort(), 2000);
    try { const response = await fetch("http://127.0.0.1:17832/health", { signal: controller.signal, cache: "no-store" }); const data = await response.json(); if (!response.ok) throw new Error(); label.textContent = data.printer || "Tayyor"; label.parentElement.classList.remove("offline"); }
    catch (_error) { label.textContent = "Brauzer rejimi"; label.parentElement.classList.add("offline"); }
    finally { clearTimeout(timer); }
  }

  function roundRect(ctx, x, y, width, height, radius) {
    const r = Math.max(0, Math.min(radius, width / 2, height / 2));
    ctx.beginPath(); ctx.moveTo(x + r, y); ctx.arcTo(x + width, y, x + width, y + height, r); ctx.arcTo(x + width, y + height, x, y + height, r); ctx.arcTo(x, y + height, x, y, r); ctx.arcTo(x, y, x + width, y, r); ctx.closePath();
  }

  function drawBarChart(canvasId, data, options = {}) {
    const canvas = document.getElementById(canvasId); if (!canvas || !data.length) return;
    const ratio = window.devicePixelRatio || 1; const width = Math.max(320, canvas.clientWidth); const height = Number(canvas.getAttribute("height")) || 280;
    canvas.width = width * ratio; canvas.height = height * ratio; const ctx = canvas.getContext("2d"); ctx.scale(ratio, ratio); ctx.clearRect(0, 0, width, height);
    const pad = { top: 28, right: 18, bottom: 43, left: 40 }; const graphW = width - pad.left - pad.right; const graphH = height - pad.top - pad.bottom;
    const maxCount = Math.max(1, ...data.map(item => Number(item.count || 0))); const maxTotal = Math.max(1, ...data.map(item => Number(item.total || 0)));
    ctx.strokeStyle = "rgba(148,163,184,.13)"; ctx.fillStyle = "#8795a8"; ctx.font = "10px Segoe UI";
    for (let i = 0; i <= 4; i += 1) { const y = pad.top + graphH * i / 4; ctx.beginPath(); ctx.moveTo(pad.left, y); ctx.lineTo(width - pad.right, y); ctx.stroke(); }
    const slot = graphW / data.length; const barWidth = Math.min(50, slot * .52);
    data.forEach((item, index) => {
      const countH = graphH * Number(item.count || 0) / maxCount; const totalH = graphH * Number(item.total || 0) / maxTotal;
      const x = pad.left + slot * index + (slot - barWidth) / 2; const countY = pad.top + graphH - countH; const totalY = pad.top + graphH - totalH;
      const gradient = ctx.createLinearGradient(0, totalY, 0, pad.top + graphH); gradient.addColorStop(0, "#4edea3"); gradient.addColorStop(1, "rgba(16,185,129,.38)"); ctx.fillStyle = gradient; roundRect(ctx, x, totalY, barWidth, Math.max(totalH, 2), 6); ctx.fill();
      ctx.fillStyle = "#0876e8"; roundRect(ctx, x + barWidth * .62, countY, barWidth * .38, Math.max(countH, 2), 4); ctx.fill();
      ctx.textAlign = "center"; ctx.fillStyle = options.highlightLast && index === data.length - 1 ? "#4edea3" : "#9caabd"; ctx.fillText(item.label, x + barWidth / 2, height - 15);
      if (item.count) { ctx.fillStyle = "#dce2f8"; ctx.fillText(String(item.count), x + barWidth / 2, Math.max(12, Math.min(countY, totalY) - 7)); }
    }); ctx.textAlign = "left";
  }

  async function loadDashboard() {
    try {
      const data = await api("/api/admin/dashboard"); weeklyData = data.weekly;
      document.getElementById("d-today-count").textContent = fmt(data.today_count); document.getElementById("d-today-total").textContent = data.today_total_fmt;
      document.getElementById("d-week-count").textContent = fmt(data.week_count); document.getElementById("d-week-total").textContent = data.week_total_fmt;
      document.getElementById("d-service-weighing").textContent = `${data.today_services.weighing_fmt} so'm`; document.getElementById("d-service-entry").textContent = `${data.today_services.entry_fmt} so'm`; document.getElementById("d-service-reload").textContent = `${data.today_services.reload_fmt} so'm`;
      drawBarChart("weekly-chart", weeklyData, { highlightLast: true });
    } catch (error) { showToast(error.message, true); }
  }

  function actionButton(text, handler, kind = "secondary") { const button = document.createElement("button"); button.type = "button"; button.className = `button ${kind} table-action`; button.textContent = text; button.addEventListener("click", handler); return button; }
  function buildRow(item, index, page, perPage) {
    const row = document.createElement("tr"); row.append(td((page - 1) * perPage + index + 1), td(item.receipt_no, "receipt-id"), td(item.plate_number, "plate-cell"), td(`${item.price_fmt} so'm`));
    const statusCell = document.createElement("td"); statusCell.append(badge(item.status)); row.append(statusCell, td(item.created_at), td(item.operator));
    const action = document.createElement("td"); action.append(actionButton("Ko'rish", () => openDetail(item))); if (item.status === "paid") action.append(actionButton("▤ Chek", event => reprint(item.id, event.currentTarget), "secondary")); row.append(action); return row;
  }

  async function loadHistory(silent = false) {
    const body = document.getElementById("history-body"); if (!silent) emptyRow(body, 8, "Yuklanmoqda...");
    const perPage = Number(document.getElementById("history-page-size").value || 15);
    try { const data = await api(`/api/admin/weighings?page=${historyPage}&per_page=${perPage}`); historyPages = data.total_pages; body.replaceChildren(); if (!data.results.length) emptyRow(body, 8, "Ma'lumot topilmadi"); else data.results.forEach((item, i) => body.append(buildRow(item, i, historyPage, data.per_page))); document.getElementById("history-page-info").textContent = `${data.page} / ${data.total_pages}`; document.getElementById("history-count-label").textContent = `${data.total} ta yozuv`; document.getElementById("history-prev").disabled = data.page <= 1; document.getElementById("history-next").disabled = data.page >= data.total_pages; }
    catch (error) { emptyRow(body, 8, error.message); }
  }

  function searchParams() {
    const params = new URLSearchParams({ page: searchPage, per_page: document.getElementById("search-page-size").value || 15 });
    const values = { plate: document.getElementById("search-plate").value.trim(), start: document.getElementById("search-start").value, end: document.getElementById("search-end").value, status: document.getElementById("search-status").value };
    Object.entries(values).forEach(([key, value]) => { if (value) params.set(key, value); }); return params;
  }
  async function doSearch(silent = false) {
    const body = document.getElementById("search-body"); if (!silent) emptyRow(body, 8, "Qidirilmoqda...");
    try {
      const data = await api(`/api/admin/weighings?${searchParams()}`); searchPages = data.total_pages; body.replaceChildren(); if (!data.results.length) emptyRow(body, 8, "Ma'lumot topilmadi"); else data.results.forEach((item, i) => body.append(buildRow(item, i, searchPage, data.per_page)));
      document.getElementById("search-page-info").textContent = `${data.page} / ${data.total_pages}`; document.getElementById("search-prev").disabled = data.page <= 1; document.getElementById("search-next").disabled = data.page >= data.total_pages;
      document.getElementById("search-total-count").textContent = fmt(data.total); document.getElementById("search-total-sum").textContent = fmt(data.results.filter(i => i.status === "paid").reduce((sum, item) => sum + Number(item.price), 0)); document.getElementById("search-current-page").textContent = data.page; document.getElementById("search-state-label").textContent = document.getElementById("search-status").selectedOptions[0].textContent; document.getElementById("search-count-label").textContent = `${data.total} ta yozuv`;
    } catch (error) { emptyRow(body, 8, error.message); }
  }

  function openDetail(item) {
    selectedDetail = item; document.getElementById("detail-receipt").textContent = item.receipt_no; const body = document.getElementById("detail-body"); body.replaceChildren();
    [["Davlat raqami", item.plate_number], ["Vazn", `${item.weight_fmt} kg`], ["Vazn o'lchash", `${item.weighing_fee_fmt} so'm`], ["Hududga kirish", item.entry_service ? `${item.entry_fee_fmt} so'm` : "Tanlanmagan"], ["Qayta yuklash", item.reload_service ? `${item.reload_fee_fmt} so'm` : "Tanlanmagan"], ["Jami", `${item.total_fmt} so'm`], ["Holat", item.status], ["To'lov usuli", item.payment_method], ["Sana va vaqt", item.created_at], ["Operator", item.operator], ["Manba", item.source], ["Chek ID", item.receipt_no]].forEach(([label, value]) => { const box = document.createElement("div"); const small = document.createElement("small"); const strong = document.createElement("strong"); small.textContent = label; strong.textContent = value; box.append(small, strong); body.append(box); });
    document.getElementById("detail-print").classList.toggle("hidden", item.status !== "paid"); document.getElementById("detail-modal").classList.remove("hidden");
  }

  async function localPrint(receipt) {
    const attempt_id=crypto.randomUUID();
    await api(`/api/weighings/${receipt.id}/print`,{method:"POST",body:JSON.stringify({attempt_id,state:"requested",method:"agent"})});
    const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),4500);
    try {
      const response=await fetch("http://127.0.0.1:17832/print",{method:"POST",mode:"cors",headers:{"Content-Type":"application/json"},body:JSON.stringify(receipt),signal:controller.signal});
      const data=await response.json().catch(()=>({}));if(!response.ok || !data.success)throw new Error(data.message || "Printer agenti xatosi");
      await api(`/api/weighings/${receipt.id}/print`,{method:"POST",body:JSON.stringify({attempt_id,state:"spooled",method:"agent"})}).catch(()=>showToast("Chek yuborildi, ammo chop jurnali saqlanmadi",true));
      return data;
    } catch(error) {await api(`/api/weighings/${receipt.id}/print`,{method:"POST",body:JSON.stringify({attempt_id,state:"failed",method:"agent"})}).catch(()=>{});throw error;}
    finally {clearTimeout(timer);}
  }
  async function reprint(id, button) {
    setBusy(button, true, "..."); const fallback = window.open("about:blank", "tarozi-receipt", "width=520,height=800");
    try { const data = await api(`/api/weighings/${id}/receipt`); if (!localAgentEnabled) { if (fallback) fallback.location.href = `/receipt/${id}?autoprint=1`; else window.location.href = `/receipt/${id}?autoprint=1`; showToast("Brauzer chop etish oynasi ochildi"); return; } if (fallback) fallback.location.href = `/receipt/${id}`; try { const printed = await localPrint(data.receipt); showToast(`Chek ${printed.printer || "printer"}ga yuborildi`); } catch (_error) { if (fallback) fallback.location.href = `/receipt/${id}?autoprint=1`; else window.location.href = `/receipt/${id}?autoprint=1`; showToast("Brauzer chop etish oynasi ochildi"); } }
    catch (error) { if (fallback) fallback.close(); showToast(error.message, true); } finally { setBusy(button, false); }
  }

  function setReportRange(type) {
    const now = new Date(); let start = new Date(now); let end = new Date(now);
    if (type === "yesterday") { start.setDate(start.getDate() - 1); end = new Date(start); }
    if (type === "week") start.setDate(start.getDate() - 6);
    if (type === "month") start = new Date(now.getFullYear(), now.getMonth(), 1);
    document.getElementById("report-start").value = isoDate(start); document.getElementById("report-end").value = isoDate(end);
    document.querySelectorAll("[data-range]").forEach(button => button.classList.toggle("active", button.dataset.range === type));
  }
  async function runReport(silent = false) {
    const button = document.getElementById("btn-report"); const start = document.getElementById("report-start").value; const end = document.getElementById("report-end").value; if (!start || !end) return; if (!silent) setBusy(button, true);
    try {
      const perPage = Number(document.getElementById("report-page-size").value || 15); const data = await api(`/api/admin/report?start=${start}&end=${end}&page=${reportPage}&per_page=${perPage}`); reportPages = data.total_pages; reportData = [...data.daily].reverse(); document.getElementById("report-count").textContent = fmt(data.total_count); document.getElementById("report-total").textContent = data.total_fmt; document.getElementById("report-average").textContent = data.total_days ? Math.round(data.total_count / data.total_days) : 0;
      const cash = data.payment_methods.cash?.count || 0; const card = data.payment_methods.card?.count || 0; document.getElementById("report-methods").textContent = `${cash} / ${card}`; document.getElementById("report-period-label").textContent = `${start} — ${end}`;
      document.getElementById("report-service-weighing").textContent = `${data.service_totals.weighing_fmt} so'm`; document.getElementById("report-service-entry").textContent = `${data.service_totals.entry_fmt} so'm`; document.getElementById("report-service-reload").textContent = `${data.service_totals.reload_fmt} so'm`;
      const body = document.getElementById("report-body"); body.replaceChildren(); if (!data.daily.length) emptyRow(body, 4, "Bu oraliqda ma'lumot yo'q"); else data.daily.forEach(item => { const row = document.createElement("tr"); row.append(td(item.date), td(`${item.count} ta`), td(`${item.total_fmt} so'm`), td(item.count ? `${fmt(Math.round(item.total / item.count))} so'm` : "0")); body.append(row); }); document.getElementById("report-count-label").textContent = `${data.total_days} kun`; document.getElementById("report-page-info").textContent = `${data.page} / ${data.total_pages}`; document.getElementById("report-prev").disabled = data.page <= 1; document.getElementById("report-next").disabled = data.page >= data.total_pages; drawBarChart("report-chart", reportData);
    } catch (error) { if (!silent) showToast(error.message, true); } finally { if (!silent) setBusy(button, false); }
  }

  function exportCsv(source) {
    const params = source === "report" ? new URLSearchParams({ start: document.getElementById("report-start").value, end: document.getElementById("report-end").value }) : searchParams(); params.delete("page"); params.delete("per_page"); window.location.href = `/api/admin/export.csv?${params}`;
  }

  async function importDatabase() {
    const input = document.getElementById("import-file"); const button = document.getElementById("btn-import"); const result = document.getElementById("import-result"); if (!input.files.length) return;
    const progress = document.getElementById("upload-progress"); const bar = document.getElementById("upload-progress-bar"); const percent = document.getElementById("upload-progress-percent"); const text = document.getElementById("upload-progress-text"); const form = new FormData(); form.append("database", input.files[0]);
    setBusy(button, true, "Import qilinmoqda..."); result.classList.add("hidden"); progress.classList.remove("hidden", "processing"); bar.style.width = "0%"; percent.textContent = "0%"; text.textContent = "Fayl serverga yuklanmoqda...";
    try {
      const data = await new Promise((resolve, reject) => {
        const xhr = new XMLHttpRequest(); xhr.open("POST", "/api/admin/import-sqlite"); xhr.responseType = "json"; xhr.setRequestHeader("Accept", "application/json"); xhr.setRequestHeader("X-CSRF-Token", csrf);
        xhr.upload.onprogress = event => { if (!event.lengthComputable) return; const value = Math.min(100, Math.round(event.loaded * 100 / event.total)); bar.style.width = `${value}%`; percent.textContent = `${value}%`; text.textContent = value < 100 ? "Fayl serverga yuklanmoqda..." : "Yuklandi. Ma'lumotlar Supabase'ga yozilmoqda..."; };
        xhr.upload.onload = () => { bar.style.width = "100%"; percent.textContent = "100%"; text.textContent = "Yuklandi. Ma'lumotlar Supabase'ga yozilmoqda..."; progress.classList.add("processing"); };
        xhr.onload = () => { const payload = xhr.response || {}; if (xhr.status === 401) { window.location.href = "/login"; return; } if (xhr.status < 200 || xhr.status >= 300 || payload.success === false) reject(new Error(payload.message || "Import bajarilmadi")); else resolve(payload); };
        xhr.onerror = () => reject(new Error("Tarmoq xatosi: fayl yuklanmadi")); xhr.send(form);
      });
      progress.classList.remove("processing"); bar.style.width = "100%"; percent.textContent = "100%"; text.textContent = "Import yakunlandi"; result.className = "alert success"; result.textContent = `${data.source_table || "SQLite"} jadvali: jami ${data.total} qator, ${data.inserted} ta import qilindi, ${data.skipped} ta takroriy o'tkazildi.`; showToast("Import muvaffaqiyatli yakunlandi"); lastSyncVersion = null;
    } catch (error) { progress.classList.remove("processing"); result.className = "alert error"; result.textContent = error.message; text.textContent = "Import to'xtadi"; }
    finally { result.classList.remove("hidden"); setBusy(button, false); }
  }

  async function loadMonitor(silent = false) {
    if (role !== "techadmin") return; const button = document.getElementById("btn-refresh-monitor"); if (!silent) setBusy(button, true, "Tekshirilmoqda...");
    try {
      const data = await api("/api/admin/system-status"); const db = data.database; const server = data.server; systemData = data.activity;
      document.getElementById("monitor-overall").textContent = data.overall_status === "healthy" ? "Barcha tizimlar ishlamoqda" : "Tizim sekin ishlamoqda"; document.getElementById("monitor-checked").textContent = new Date(data.checked_at).toLocaleString("uz-UZ");
      document.getElementById("storage-percent").textContent = `${db.used_percent}%`; document.getElementById("storage-used").textContent = bytes(db.used_bytes); document.getElementById("storage-free").textContent = bytes(db.remaining_bytes); document.getElementById("storage-limit").textContent = bytes(db.limit_bytes); document.getElementById("storage-ring").style.background = `conic-gradient(#4edea3 ${db.used_percent}%, #232a3a 0)`; document.getElementById("storage-progress").style.width = `${db.used_percent}%`;
      document.getElementById("db-latency").textContent = `${db.latency_ms} ms`; document.getElementById("db-connections").textContent = db.connections; document.getElementById("db-rows").textContent = fmt(db.weighing_rows); document.getElementById("active-users").textContent = db.active_users;
      document.getElementById("server-uptime").textContent = uptime(server.uptime_seconds); document.getElementById("server-memory").textContent = bytes(server.memory_bytes); document.getElementById("server-cpu").textContent = `${Number(server.cpu_percent).toFixed(1)}%`; document.getElementById("server-disk").textContent = `${Number(server.disk_used_percent).toFixed(1)}%`;
      document.getElementById("avg-response").textContent = `${server.avg_response_ms} ms`; document.getElementById("p95-response").textContent = `${server.p95_response_ms} ms`; document.getElementById("request-count").textContent = fmt(server.requests); document.getElementById("error-count").textContent = fmt(server.errors);
      document.getElementById("last-import").textContent = data.application.last_import ? `${data.application.last_import.filename} — ${data.application.last_import.inserted}/${data.application.last_import.total} qator` : "Hali import qilinmagan"; document.getElementById("app-version").textContent = data.application.version; document.getElementById("app-environment").textContent = data.application.environment; drawBarChart("system-chart", systemData, { highlightLast: true });
    } catch (error) { if (!silent) showToast(error.message, true); document.getElementById("monitor-overall").textContent = "Monitoring ma'lumoti olinmadi"; }
    finally { if (!silent) setBusy(button, false); }
  }

  async function loadTech() {
    if (role !== "techadmin") return;
    try { const [price, tokens] = await Promise.all([api("/api/admin/settings/price"), api("/api/admin/backup-tokens")]); document.getElementById("price-input").value = price.price; document.getElementById("current-price-value").textContent = fmt(price.price); renderTokens(tokens.tokens); }
    catch (error) { showToast(error.message, true); }
  }
  async function loadUsers() { if (role !== "techadmin") return; try { const perPage = document.getElementById("users-page-size").value || 15; const data = await api(`/api/admin/users?page=${usersPage}&per_page=${perPage}`); usersPages = data.total_pages; renderUsers(data.users); document.getElementById("users-count-label").textContent = `${data.total} ta foydalanuvchi`; document.getElementById("users-page-info").textContent = `${data.page} / ${data.total_pages}`; document.getElementById("users-prev").disabled = data.page <= 1; document.getElementById("users-next").disabled = data.page >= data.total_pages; } catch (error) { showToast(error.message, true); } }
  function renderUsers(users) {
    const body = document.getElementById("users-body"); body.replaceChildren(); users.forEach(user => { const row = document.createElement("tr"); row.append(td(user.username), td(user.role)); const state = document.createElement("td"); const stateBadge = badge(user.is_active ? "paid" : "cancelled"); stateBadge.textContent = user.is_active ? "Faol" : "Bloklangan"; state.append(stateBadge); row.append(state, td(user.last_login_at || "Hali kirmagan")); const actions = document.createElement("td"); actions.append(actionButton("Parol", () => openPasswordModal(user)), actionButton(user.is_active ? "Bloklash" : "Faollashtirish", event => toggleUser(user.id, event.currentTarget), "danger-soft")); row.append(actions); body.append(row); });
  }
  function renderTokens(tokens) {
    const body = document.getElementById("tokens-body"); body.replaceChildren(); if (!tokens.length) return emptyRow(body, 5, "Backup token yaratilmagan"); tokens.forEach(token => { const row = document.createElement("tr"); row.append(td(token.name), td(token.created_at), td(token.last_used_at || "Ishlatilmagan")); const state = document.createElement("td"); const stateBadge = badge(token.revoked ? "cancelled" : "paid"); stateBadge.textContent = token.revoked ? "Bekor qilingan" : "Faol"; state.append(stateBadge); row.append(state); const actions = document.createElement("td"); if (!token.revoked) actions.append(actionButton("Bekor qilish", event => revokeToken(token.id, event.currentTarget), "danger-soft")); row.append(actions); body.append(row); });
  }
  async function savePrice() { const button = document.getElementById("btn-save-price"); const price = Number(document.getElementById("price-input").value); setBusy(button, true); try { const saved = await api("/api/admin/settings/price", { method: "PUT", body: JSON.stringify({ price }) }); document.getElementById("current-price-value").textContent = fmt(saved.price); showToast("Tarozi narxi saqlandi"); lastSyncVersion = null; } catch (error) { showToast(error.message, true); } finally { setBusy(button, false); } }
  async function createUser() { const button = document.getElementById("btn-create-user"); const usernameInput = document.getElementById("new-username"); const passwordInput = document.getElementById("new-password"); const payload = { username: usernameInput.value.trim().toLowerCase(), password: passwordInput.value, role: document.getElementById("new-role").value }; if (!/^[a-z0-9_.-]{3,40}$/.test(payload.username)) { showToast("Login 3–40 belgi: lotin harfi, raqam, _, . yoki -", true); usernameInput.focus(); return; } if (payload.password.length < 8) { showToast("Parol kamida 8 belgidan iborat bo'lsin", true); passwordInput.focus(); return; } setBusy(button, true); try { await api("/api/admin/users", { method: "POST", body: JSON.stringify(payload) }); usernameInput.value = ""; passwordInput.value = ""; usersPage = 1; showToast("Foydalanuvchi yaratildi"); await loadUsers(); } catch (error) { showToast(error.message, true); } finally { setBusy(button, false); } }
  function openPasswordModal(user) { resetUserId = user.id; document.getElementById("password-user").textContent = `${user.username} uchun yangi parol`; document.getElementById("reset-password").value = ""; document.getElementById("password-modal").classList.remove("hidden"); }
  async function resetPassword() { const button = document.getElementById("btn-reset-password"); setBusy(button, true); try { await api(`/api/admin/users/${resetUserId}/reset-password`, { method: "POST", body: JSON.stringify({ password: document.getElementById("reset-password").value }) }); document.getElementById("password-modal").classList.add("hidden"); showToast("Parol yangilandi"); } catch (error) { showToast(error.message, true); } finally { setBusy(button, false); } }
  async function toggleUser(id, button) { setBusy(button, true); try { await api(`/api/admin/users/${id}/toggle`, { method: "POST" }); await loadUsers(); showToast("Foydalanuvchi holati yangilandi"); } catch (error) { showToast(error.message, true); } finally { setBusy(button, false); } }
  function openClearDataModal() {
    document.getElementById("clear-data-password").value = ""; document.getElementById("clear-data-confirmation").value = ""; document.getElementById("btn-confirm-clear-data").disabled = true; document.getElementById("clear-data-modal").classList.remove("hidden"); document.getElementById("clear-data-password").focus();
  }
  function validateClearDataConfirmation() { document.getElementById("btn-confirm-clear-data").disabled = document.getElementById("clear-data-confirmation").value.trim() !== "BARCHASINI OCHIRISH" || !document.getElementById("clear-data-password").value; }
  async function clearOperationalData() {
    const button = document.getElementById("btn-confirm-clear-data"); const payload = { password: document.getElementById("clear-data-password").value, confirmation: document.getElementById("clear-data-confirmation").value.trim() }; setBusy(button, true, "O'chirilmoqda...");
    try { const data = await api("/api/admin/clear-operational-data", { method: "POST", body: JSON.stringify(payload) }); document.getElementById("clear-data-modal").classList.add("hidden"); showToast(`${fmt(data.deleted_weighings)} ta operatsiya xavfsiz tozalandi`); lastSyncVersion = null; await loadMonitor(true); }
    catch (error) { showToast(error.message, true); }
    finally { setBusy(button, false); validateClearDataConfirmation(); }
  }
  async function createToken() { const button = document.getElementById("btn-create-token"); setBusy(button, true); try { const data = await api("/api/admin/backup-tokens", { method: "POST", body: JSON.stringify({ name: document.getElementById("token-name").value.trim() }) }); document.getElementById("new-token-value").textContent = data.token; document.getElementById("new-token-box").classList.remove("hidden"); document.getElementById("token-name").value = ""; renderTokens((await api("/api/admin/backup-tokens")).tokens); showToast("Backup token yaratildi"); } catch (error) { showToast(error.message, true); } finally { setBusy(button, false); } }
  async function revokeToken(id, button) { if (!window.confirm("Bu tokenni bekor qilasizmi?")) return; setBusy(button, true); try { await api(`/api/admin/backup-tokens/${id}/revoke`, { method: "POST" }); renderTokens((await api("/api/admin/backup-tokens")).tokens); showToast("Token bekor qilindi"); } catch (error) { showToast(error.message, true); } finally { setBusy(button, false); } }

  const loaders = { dashboard: loadDashboard, reports: runReport, search: doSearch, history: loadHistory, monitor: loadMonitor, users: loadUsers, tech: loadTech };
  function activateTab(name) {
    const target = document.getElementById(`tab-${name}`); if (!target) return;
    activeTab = name;
    document.querySelectorAll(".nav-link").forEach(link => link.classList.toggle("active", link.dataset.tab === name)); document.querySelectorAll(".admin-tab").forEach(tab => tab.classList.toggle("hidden", tab !== target));
    clearInterval(monitorTimer); monitorTimer = null; if (loaders[name]) loaders[name](); if (name === "monitor") monitorTimer = setInterval(loadMonitor, 30000); window.scrollTo(0, 0);
  }

  async function pollSync() {
    if (syncBusy || document.hidden) return; syncBusy = true;
    const indicator = document.getElementById("sync-status");
    try {
      const data = await api("/api/sync/state");
      if (lastSyncVersion && data.version !== lastSyncVersion) {
        if (activeTab === "dashboard") await loadDashboard();
        else if (activeTab === "history") await loadHistory(true);
        else if (activeTab === "search") await doSearch(true);
        else if (activeTab === "reports") await runReport(true);
        else if (activeTab === "monitor") await loadMonitor(true);
        else if (activeTab === "users") await loadUsers();
      }
      lastSyncVersion = data.version; if (indicator) { indicator.classList.remove("offline"); indicator.lastChild.textContent = " Qurilmalar sinxron"; }
    } catch (_error) { if (indicator) { indicator.classList.add("offline"); indicator.lastChild.textContent = " Sinxronlash kutilmoqda"; } }
    finally { syncBusy = false; }
  }

  document.querySelectorAll(".nav-link").forEach(link => link.addEventListener("click", () => activateTab(link.dataset.tab)));
  document.querySelectorAll("[data-go]").forEach(button => { if (!document.getElementById(`tab-${button.dataset.go}`)) button.classList.add("hidden"); else button.addEventListener("click", () => activateTab(button.dataset.go)); });
  document.querySelectorAll("[data-close]").forEach(button => button.addEventListener("click", () => document.getElementById(button.dataset.close).classList.add("hidden")));
  document.getElementById("btn-refresh-dashboard")?.addEventListener("click", loadDashboard);
  document.getElementById("btn-report").addEventListener("click", () => { reportPage = 1; runReport(); }); document.querySelectorAll("[data-range]").forEach(button => button.addEventListener("click", () => { setReportRange(button.dataset.range); reportPage = 1; runReport(); }));
  document.getElementById("report-page-size").addEventListener("change", () => { reportPage = 1; runReport(); }); document.getElementById("report-prev").addEventListener("click", () => { if (reportPage > 1) { reportPage -= 1; runReport(); } }); document.getElementById("report-next").addEventListener("click", () => { if (reportPage < reportPages) { reportPage += 1; runReport(); } });
  document.getElementById("btn-export-report").addEventListener("click", () => {const p=new URLSearchParams({start:document.getElementById("report-start").value,end:document.getElementById("report-end").value,group:"day"});window.location.href=`/api/admin/analytics/export/xlsx?${p}`;}); document.getElementById("btn-print-report").addEventListener("click", () => window.print());
  document.getElementById("btn-search").addEventListener("click", () => { searchPage = 1; doSearch(); }); document.getElementById("btn-refresh-search").addEventListener("click", () => doSearch()); document.getElementById("btn-export-search").addEventListener("click", () => exportCsv("search")); document.getElementById("btn-print-table").addEventListener("click", () => window.print());
  document.getElementById("search-plate").addEventListener("keydown", event => { if (event.key === "Enter") { searchPage = 1; doSearch(); } }); document.querySelectorAll("[data-search-sample]").forEach(button => button.addEventListener("click", () => { document.getElementById("search-plate").value = button.dataset.searchSample; searchPage = 1; doSearch(); }));
  document.getElementById("btn-reset-search").addEventListener("click", () => { ["search-plate", "search-start", "search-end"].forEach(id => { document.getElementById(id).value = ""; }); document.getElementById("search-status").value = ""; searchPage = 1; doSearch(); });
  document.getElementById("search-prev").addEventListener("click", () => { if (searchPage > 1) { searchPage -= 1; doSearch(); } }); document.getElementById("search-next").addEventListener("click", () => { if (searchPage < searchPages) { searchPage += 1; doSearch(); } });
  document.getElementById("search-page-size").addEventListener("change", () => { searchPage = 1; doSearch(); });
  document.getElementById("history-prev").addEventListener("click", () => { if (historyPage > 1) { historyPage -= 1; loadHistory(); } }); document.getElementById("history-next").addEventListener("click", () => { if (historyPage < historyPages) { historyPage += 1; loadHistory(); } }); document.getElementById("btn-refresh-history").addEventListener("click", () => loadHistory());
  document.getElementById("history-page-size").addEventListener("change", () => { historyPage = 1; loadHistory(); });
  document.getElementById("detail-print").addEventListener("click", event => { if (selectedDetail) reprint(selectedDetail.id, event.currentTarget); });
  document.getElementById("import-file").addEventListener("change", event => { const file = event.target.files[0]; document.getElementById("import-file-name").textContent = file ? `${file.name} (${bytes(file.size)})` : ".db faylni tanlash"; document.getElementById("btn-import").disabled = !file; }); document.getElementById("btn-import").addEventListener("click", importDatabase);
  if (role === "techadmin") { document.getElementById("btn-refresh-monitor").addEventListener("click", () => loadMonitor()); document.getElementById("btn-save-price").addEventListener("click", savePrice); document.getElementById("btn-create-user").addEventListener("click", createUser); document.getElementById("btn-reset-password").addEventListener("click", resetPassword); document.getElementById("btn-create-token").addEventListener("click", createToken); document.getElementById("users-page-size").addEventListener("change", () => { usersPage = 1; loadUsers(); }); document.getElementById("users-prev").addEventListener("click", () => { if (usersPage > 1) { usersPage -= 1; loadUsers(); } }); document.getElementById("users-next").addEventListener("click", () => { if (usersPage < usersPages) { usersPage += 1; loadUsers(); } }); document.getElementById("btn-open-clear-data").addEventListener("click", openClearDataModal); document.getElementById("clear-data-password").addEventListener("input", validateClearDataConfirmation); document.getElementById("clear-data-confirmation").addEventListener("input", validateClearDataConfirmation); document.getElementById("btn-confirm-clear-data").addEventListener("click", clearOperationalData); }
  document.getElementById("manual-backup-name").textContent = `${new Date().toLocaleDateString("uz-UZ")} 00-00 holatiga backup.db`;
  window.addEventListener("resize", () => { if (weeklyData.length) drawBarChart("weekly-chart", weeklyData, { highlightLast: true }); if (reportData.length) drawBarChart("report-chart", reportData); if (systemData.length) drawBarChart("system-chart", systemData, { highlightLast: true }); });
  setClock(); setInterval(setClock, 1000); checkAgent(); if (localAgentEnabled) setInterval(checkAgent, 30000); setReportRange("week"); activateTab(activeTab); pollSync(); setInterval(pollSync, 12000); document.addEventListener("visibilitychange", () => { if (!document.hidden) pollSync(); });
})();
