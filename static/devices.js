(() => {
  const el = id => document.getElementById(id);
  const panel = el("tab-devices");
  if (!panel) return;

  let busy = false, selectedDevice = null, allDevices = [], eventPage = 1, eventPages = 1;
  // Both admin and techadmin can fully manage devices
  const canManage = ["admin", "techadmin"].includes(document.body.dataset.role);

  const roleLabels = {
    ayritom: "Afg‘oniston darvozasi (KPP1)",
    kpp2: "O‘zbekiston darvozasi (KPP2)",
    parkovka_tashqi: "E-navbat parkovka",
    operator: "Tarozi (Free Zone)"
  };

  const date = value => value ? new Date(value).toLocaleString("uz-UZ", { timeZone: "Asia/Tashkent" }) : "—";

  function relativeTime(value) {
    if (!value) return "Hali ulanmagan";
    const diffSec = Math.max(0, Math.floor((Date.now() - new Date(value).getTime()) / 1000));
    if (diffSec < 65) return "Hozir faol (" + diffSec + " soniya oldin)";
    if (diffSec < 3600) return Math.floor(diffSec / 60) + " daqiqa oldin";
    if (diffSec < 86400) return Math.floor(diffSec / 3600) + " soat oldin";
    return Math.floor(diffSec / 86400) + " kun oldin";
  }

  function cell(row, value, className) {
    const td = row.insertCell();
    if (className) td.className = className;
    if (value !== undefined && value !== null) td.textContent = value;
    return td;
  }

  function actionBtn(container, label, variant, click, title = "") {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "dev-act-btn " + variant;
    b.textContent = label;
    if (title) b.title = title;
    b.addEventListener("click", e => {
      e.preventDefault();
      e.stopPropagation();
      click(e);
    });
    container.append(b);
  }

  function notify(msg, isError = false) {
    if (el("devices-status")) el("devices-status").textContent = msg;
    const toast = el("toast");
    if (toast && el("toast-text")) {
      el("toast-text").textContent = msg;
      if (el("toast-icon")) el("toast-icon").textContent = isError ? "!" : "✓";
      toast.classList.toggle("error", isError);
      toast.classList.remove("hidden");
      setTimeout(() => toast.classList.add("hidden"), 4000);
    }
  }

  function keepDevicesTabOpen() {
    panel.classList.remove("hidden");
    panel.classList.add("active");
    document.querySelectorAll(".admin-tab").forEach(tab => {
      if (tab !== panel) tab.classList.add("hidden");
    });
    document.querySelectorAll(".nav-link[data-tab]").forEach(link => {
      link.classList.toggle("active", link.dataset.tab === "devices");
    });
  }

  async function change(id, action, extra = {}) {
    keepDevicesTabOpen();
    try {
      const r = await fetch("/api/admin/devices/" + id, {
        method: "POST",
        signal: AbortSignal.timeout(20000),
        headers: {
          "Content-Type": "application/json",
          "Accept": "application/json",
          "X-CSRF-Token": document.body.dataset.csrf
        },
        body: JSON.stringify({ action, ...extra })
      });
      const res = await r.json().catch(() => ({}));
      if (!r.ok) throw Error(res.message || "Amal bajarilmadi");
      if (["approve", "unblock", "rename"].includes(action) && el("devices-state-filter")?.value === "pending") {
        el("devices-state-filter").value = "";
        document.querySelectorAll("[data-kpi-filter]").forEach(c => c.classList.remove("active-kpi"));
      }
      const msgMap = {
        approve: "Qurilmaga doimiy ruxsat berildi ✓",
        unblock: "Qurilma blokdan chiqarildi ✓",
        block: "Qurilma bloklandi",
        rename: "Qurilma ma’lumotlari saqlandi ✓",
        reset_session: "Qurilma sessiyasi yangilandi ✓",
        delete: "Qurilma ro‘yxatdan o‘chirildi"
      };
      notify(msgMap[action] || "Saqlandi ✓");
      keepDevicesTabOpen();
      await refresh(true);
    } catch (e) {
      notify("Xatolik: " + e.message, true);
    }
  }

  async function enrollDevice(payload) {
    keepDevicesTabOpen();
    try {
      const r = await fetch("/api/admin/devices/enroll", {
        method: "POST",
        signal: AbortSignal.timeout(20000),
        headers: {
          "Content-Type": "application/json",
          "Accept": "application/json",
          "X-CSRF-Token": document.body.dataset.csrf
        },
        body: JSON.stringify(payload)
      });
      const res = await r.json().catch(() => ({}));
      if (!r.ok) throw Error(res.message || "Saqlanmadi");
      keepDevicesTabOpen();
      await refresh(true);
      if (payload.action === "deduplicate") {
        notify(res.merged
          ? `Takroriy qurilmalar tozalandi (${res.merged} ta birlashtirildi) ✓`
          : "Takroriy qurilmalar topilmadi — ro‘yxat toza ✓");
      } else {
        notify("Qurilma IMEI orqali muvaffaqiyatli qo‘shildi va tasdiqlandi ✓");
      }
    } catch (e) {
      notify("Xatolik: " + e.message, true);
    }
  }

  function openEditModal(device) {
    selectedDevice = device;
    el("device-name").value = device.name || "";
    el("device-hwid").value = device.hw_id || "";
    el("device-role").value = device.role || "ayritom";
    if (el("device-status-select")) {
      el("device-status-select").value = device.blocked ? "blocked" : (device.approved ? "approved" : "pending");
    }
    el("device-editor").showModal();
  }

  if (el("device-editor-cancel")) el("device-editor-cancel").onclick = () => el("device-editor").close("cancel");
  if (el("device-editor-x")) el("device-editor-x").onclick = () => el("device-editor").close("cancel");

  if (el("device-gen-hwid")) {
    el("device-gen-hwid").onclick = () => {
      if (selectedDevice && selectedDevice.id) {
        el("device-hwid").value = "HW-" + String(selectedDevice.id).replace(/-/g, "").slice(0, 14).toUpperCase();
      } else {
        el("device-hwid").value = "HW-" + Math.random().toString(16).slice(2, 16).toUpperCase();
      }
    };
  }
  if (el("enroll-gen-hwid")) {
    el("enroll-gen-hwid").onclick = () => {
      el("enroll-hwid").value = "HW-" + Math.random().toString(16).slice(2, 16).toUpperCase();
    };
  }

  if (el("device-editor-form")) {
    el("device-editor-form").onsubmit = e => {
      e.preventDefault();
      e.stopPropagation();
      if (!selectedDevice) return;
      el("device-editor").close("save");
      change(selectedDevice.id, "rename", {
        name: el("device-name").value.trim(),
        hw_id: el("device-hwid").value.trim(),
        role: el("device-role").value,
        status: el("device-status-select")?.value || "approved"
      });
    };
  }

  if (el("devices-enroll-open")) {
    el("devices-enroll-open").onclick = () => {
      el("device-enroll-form")?.reset();
      el("device-enroll-modal")?.showModal();
    };
  }
  if (el("device-enroll-cancel")) el("device-enroll-cancel").onclick = () => el("device-enroll-modal").close("cancel");
  if (el("device-enroll-x")) el("device-enroll-x").onclick = () => el("device-enroll-modal").close("cancel");

  if (el("device-enroll-form")) {
    el("device-enroll-form").onsubmit = e => {
      e.preventDefault();
      e.stopPropagation();
      el("device-enroll-modal").close("save");
      enrollDevice({
        action: "enroll",
        name: el("enroll-name").value.trim(),
        hw_id: el("enroll-hwid").value.trim(),
        role: el("enroll-role").value
      });
    };
  }
  if (el("devices-dedup")) {
    el("devices-dedup").onclick = e => {
      e.preventDefault();
      e.stopPropagation();
      enrollDevice({ action: "deduplicate" });
    };
  }

  // Interactive KPI cards act as quick state filters
  document.querySelectorAll("[data-kpi-filter]").forEach(card => {
    card.addEventListener("click", e => {
      e.preventDefault();
      e.stopPropagation();
      const val = card.dataset.kpiFilter || "";
      if (el("devices-state-filter")) el("devices-state-filter").value = val;
      document.querySelectorAll("[data-kpi-filter]").forEach(c => c.classList.toggle("active-kpi", c === card && val !== ""));
      renderTable();
    });
  });

  // Interactive Role filter pills
  document.querySelectorAll("[data-role-pill]").forEach(pill => {
    pill.addEventListener("click", e => {
      e.preventDefault();
      e.stopPropagation();
      const val = pill.dataset.rolePill || "";
      if (el("devices-role-filter")) el("devices-role-filter").value = val;
      document.querySelectorAll("[data-role-pill]").forEach(p => p.classList.toggle("active", p === pill));
      renderTable();
    });
  });

  function updateKPIs(devices) {
    const total = devices.length;
    const online = devices.filter(d => d.online && !d.blocked).length;
    const approved = devices.filter(d => d.approved && !d.blocked).length;
    const pending = devices.filter(d => !d.approved && !d.blocked).length;
    const blocked = devices.filter(d => d.blocked).length;
    if (el("dev-kpi-total")) el("dev-kpi-total").textContent = String(total);
    if (el("dev-kpi-online")) el("dev-kpi-online").textContent = String(online);
    if (el("dev-kpi-approved")) el("dev-kpi-approved").textContent = String(approved);
    if (el("dev-kpi-pending")) el("dev-kpi-pending").textContent = String(pending);
    if (el("dev-kpi-blocked")) el("dev-kpi-blocked").textContent = String(blocked);
  }

  function renderTable() {
    const body = el("devices-rows");
    if (!body) return;
    body.replaceChildren();

    const q = (el("devices-search")?.value || "").trim().toLowerCase();
    if (el("devices-search-clear")) el("devices-search-clear").hidden = !q;
    const roleFilter = el("devices-role-filter")?.value || "";
    const stateFilter = el("devices-state-filter")?.value || "";

    document.querySelectorAll("[data-role-pill]").forEach(p => {
      p.classList.toggle("active", (p.dataset.rolePill || "") === roleFilter);
    });

    const filtered = allDevices.filter(d => {
      if (roleFilter && d.role !== roleFilter) return false;
      if (stateFilter === "online" && !(d.online && !d.blocked)) return false;
      if (stateFilter === "pending" && !(!d.approved && !d.blocked)) return false;
      if (stateFilter === "approved" && !(d.approved && !d.blocked)) return false;
      if (stateFilter === "blocked" && !d.blocked) return false;
      if (q) {
        const hay = `${d.name || ""} ${d.hw_id || ""} ${d.ip || ""} ${d.last_user || ""} ${d.role || ""} ${roleLabels[d.role] || ""}`.toLowerCase();
        if (!hay.includes(q)) return false;
      }
      return true;
    });

    for (const device of filtered) {
      const tr = body.insertRow();
      if (!device.approved && !device.blocked) tr.className = "device-row-pending";

      // 1. Qurilma nomi
      const nameTd = cell(tr, "");
      const nameStrong = document.createElement("strong");
      nameStrong.className = "dev-name-title";
      nameStrong.textContent = device.name;
      const idSub = document.createElement("small");
      idSub.className = "dev-sub-meta";
      idSub.textContent = "UUID: " + String(device.id).slice(0, 8);
      nameTd.append(nameStrong, idSub);

      // 2. Nuqta / Xodim
      const roleTd = cell(tr, "");
      const roleBadge = document.createElement("span");
      roleBadge.className = "dev-role-badge role-" + device.role;
      roleBadge.textContent = roleLabels[device.role] || device.role;
      const userSub = document.createElement("small");
      userSub.className = "dev-sub-meta";
      userSub.textContent = "Xodim: " + (device.last_user || "Hali kirmagan");
      roleTd.append(roleBadge, userSub);

      // 3. IMEI / Qurilma ID
      const hwTd = cell(tr, "");
      if (device.hw_id) {
        const hwWrap = document.createElement("div");
        hwWrap.className = "dev-hwid-wrap";
        const hwCode = document.createElement("code");
        hwCode.className = "dev-hwid-code";
        hwCode.textContent = device.hw_id;
        const copyBtn = document.createElement("button");
        copyBtn.type = "button";
        copyBtn.className = "dev-copy-btn";
        copyBtn.title = "IMEI / Qurilma ID ni nusxalash";
        copyBtn.textContent = "Nusxa";
        copyBtn.onclick = async () => {
          try {
            await navigator.clipboard.writeText(device.hw_id);
            copyBtn.textContent = "✓";
            setTimeout(() => { copyBtn.textContent = "Nusxa"; }, 1400);
          } catch {}
        };
        hwWrap.append(hwCode, copyBtn);
        const hwSub = document.createElement("small");
        hwSub.className = "dev-sub-meta dev-imei-ok";
        hwSub.textContent = "Doimiy IMEI / HWID biriktirilgan ✓";
        hwTd.append(hwWrap, hwSub);
      } else {
        const hwWrap = document.createElement("div");
        hwWrap.className = "dev-hwid-wrap";
        const hwEmpty = document.createElement("span");
        hwEmpty.className = "dev-hwid-missing";
        hwEmpty.textContent = "Kiritilmagan";
        hwWrap.append(hwEmpty);
        if (canManage) {
          const bindBtn = document.createElement("button");
          bindBtn.type = "button";
          bindBtn.className = "dev-copy-btn";
          bindBtn.textContent = "+ IMEI biriktirish";
          bindBtn.onclick = () => openEditModal(device);
          hwWrap.append(bindBtn);
        }
        const hwSub = document.createElement("small");
        hwSub.className = "dev-sub-meta";
        hwSub.textContent = "Keyingi kirishda avto-olinadi yoki qo‘lda biriktiring";
        hwTd.append(hwWrap, hwSub);
      }

      // 4. IP manzil
      cell(tr, device.ip || "—", "dev-ip-cell");

      // 5. Holati
      const statusTd = cell(tr, "");
      const pill = document.createElement("span");
      if (device.blocked) {
        pill.className = "dev-status-pill pill-blocked";
        pill.textContent = "Bloklangan";
      } else if (!device.approved) {
        pill.className = "dev-status-pill pill-pending";
        pill.textContent = "Tasdiq kutilmoqda";
      } else if (device.online) {
        pill.className = "dev-status-pill pill-online";
        pill.textContent = "Online · Tasdiqlangan";
      } else {
        pill.className = "dev-status-pill pill-offline";
        pill.textContent = "Offline · Tasdiqlangan";
      }
      statusTd.append(pill);

      if (device.backlog) {
        const p = document.createElement("p");
        const hasConflict = Boolean(device.backlog.rejected && device.backlog.rejected > 0);
        p.className = hasConflict ? "device-warning-text" : "device-backlog-text";
        const alertBadge = hasConflict ? "⚠️ Ziddiyat · " : (device.backlog.count > 0 ? "⏳ Navbatda · " : "");
        p.textContent = alertBadge + device.backlog.count + " ta yozuv · " + Number(device.backlog.amount || 0).toLocaleString() + " so‘m";
        statusTd.append(p);
      }

      // 6. Oxirgi signal
      const seenTd = cell(tr, "");
      const seenMain = document.createElement("span");
      seenMain.textContent = date(device.last_seen);
      const seenSub = document.createElement("small");
      seenSub.className = "dev-sub-meta";
      seenSub.textContent = relativeTime(device.last_seen);
      seenTd.append(seenMain, seenSub);

      // 7. Boshqarish va amallar
      const actions = cell(tr, "", "dev-actions-cell");
      const btnGroup = document.createElement("div");
      btnGroup.className = "dev-btn-group";
      if (canManage) {
        if (!device.approved || device.blocked) {
          actionBtn(btnGroup, "✓ Ruxsat berish", "btn-approve", () => change(device.id, "approve"), "Ushbu qurilmaga doimiy ruxsat berish");
        }
        actionBtn(btnGroup, "✎ Tahrirlash", "btn-edit", () => openEditModal(device), "Qurilma nomi, IMEI/ID, post va holatini o‘zgartirish");
        if (!device.blocked) {
          actionBtn(btnGroup, "⊘ Bloklash", "btn-block", () => {
            if (confirm(`"${device.name}" qurilmasi bloklansinmi?`)) change(device.id, "block");
          }, "Qurilma kirishini vaqtincha cheklash");
        }
        actionBtn(btnGroup, "⟳ Sessiya", "btn-reset", () => {
          if (confirm(`"${device.name}" qurilmasining joriy sessiyasi yangilansinmi?`)) change(device.id, "reset_session");
        }, "Qurilma sessiyasini uzib qayta ulash");
        actionBtn(btnGroup, "🗑 O‘chirish", "btn-delete", () => {
          if (confirm(`"${device.name}" qurilmasi ro‘yxatdan butunlay o‘chirilsinmi?`)) change(device.id, "delete");
        }, "Qurilmani ro‘yxatdan o‘chirish");
      } else {
        btnGroup.textContent = "Faqat ko‘rish";
      }
      actions.append(btnGroup);
    }

    if (!filtered.length) {
      const emptyCell = cell(body.insertRow(), allDevices.length ? "Tanlangan filtr bo‘yicha qurilma topilmadi." : "Ro‘yxatdan o‘tgan qurilma yo‘q.");
      emptyCell.colSpan = 7;
      emptyCell.className = "dev-empty-row";
    }
  }

  async function refresh(force = false) {
    if (!force && (busy || document.hidden || panel.classList.contains("hidden"))) return;
    busy = true;
    if (el("devices-refresh")) el("devices-refresh").disabled = true;
    try {
      const r = await fetch("/api/admin/devices?event_page=" + eventPage + "&event_size=" + el("event-size").value, {
        cache: "no-store",
        signal: AbortSignal.timeout(20000)
      });
      if (!r.ok) throw Error("Qurilmalar holatini olib bo‘lmadi.");
      const d = await r.json();
      allDevices = d.devices || [];
      updateKPIs(allDevices);
      renderTable();

      eventPage = d.event_page;
      eventPages = d.event_pages;
      el("event-page").textContent = eventPage + " / " + eventPages;
      el("event-total").textContent = d.event_total + " ta hodisa";
      el("event-prev").disabled = eventPage <= 1;
      el("event-next").disabled = eventPage >= eventPages;

      const events = el("devices-events");
      events.replaceChildren();
      const names = {
        login: "Tizimga kirdi",
        logout: "Tizimdan chiqdi",
        offline: "Aloqa uzildi",
        reconnected: "Qayta ulandi",
        rename: "Ma’lumot yangilandi",
        approve: "Doimiy ruxsat berildi",
        unblock: "Blokdan chiqarildi",
        reset_session: "Sessiya yangilandi",
        block: "Bloklandi"
      };
      for (const event of d.events) {
        const row = events.insertRow();
        const [kind, user] = String(event.event || "").split(":");
        cell(row, date(event.occurred));
        cell(row, event.name + (event.hw_id ? " · " + event.hw_id : ""));
        cell(row, (names[kind] || kind) + (user ? " (" + user + ")" : ""));
        cell(row, date(event.previous_seen));
      }
      el("devices-status").textContent = "Tekshirildi · " + date(d.checked_at) + " · IMEI / HWID va kompyuter stansiyalari sinxron";
    } catch (e) {
      el("devices-status").textContent = e.message;
    } finally {
      busy = false;
      if (el("devices-refresh")) el("devices-refresh").disabled = false;
    }
  }

  if (el("devices-search")) el("devices-search").oninput = renderTable;
  if (el("devices-search-clear")) {
    el("devices-search-clear").onclick = () => {
      el("devices-search").value = "";
      renderTable();
      el("devices-search").focus();
    };
  }
  if (el("devices-role-filter")) el("devices-role-filter").onchange = renderTable;
  if (el("devices-state-filter")) el("devices-state-filter").onchange = renderTable;

  document.addEventListener("admin:tab", e => { if (e.detail === "devices") refresh(true); });
  el("event-prev").onclick = () => { if (eventPage > 1 && !busy) { eventPage--; refresh(true); } };
  el("event-next").onclick = () => { if (eventPage < eventPages && !busy) { eventPage++; refresh(true); } };
  el("event-size").onchange = () => { eventPage = 1; refresh(true); };
  el("devices-refresh").onclick = () => refresh(true);
  if (!panel.classList.contains("hidden") || document.body.dataset.defaultTab === "devices") {
    keepDevicesTabOpen();
    refresh(true);
  }
  setInterval(() => refresh(false), 30000);
})();
