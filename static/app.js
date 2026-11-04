(() => {
  "use strict";

  const csrf = document.body.dataset.csrf;
  const mainScreen = document.getElementById("screen-main");
  const paymentScreen = document.getElementById("screen-payment");
  const plateModal = document.getElementById("plate-modal");
  const plateInput = document.getElementById("plate-input");
  const paidCheck = document.getElementById("paid-check");
  const payButton = document.getElementById("btn-pay");
  const weightInput = document.getElementById("weight-input");
  const entryService = document.getElementById("service-entry");
  const reloadService = document.getElementById("service-reload");
  const entryFeeInput = document.getElementById("entry-fee-input");
  const reloadFeeInput = document.getElementById("reload-fee-input");
  const receiptPreviewModal = document.getElementById("receipt-preview-modal");
  const receiptPreviewFrame = document.getElementById("receipt-preview-frame");
  const localAgentEnabled = document.body.dataset.localPrinterAgent === "1";
  let current = null;
  let paymentMethod = "cash";
  let servicePrices = { weighing: 30000, entry: 30000, reload: 30000 };
  let statsLoading = false;
  let lastSyncVersion = null;
  let syncLoading = false;
  let journalDay = "today";
  let journalPage = 1;
  let journalPages = 1;
  let journalRequestId = 0;
  let receiptPrintPending = false;
  let receiptPreviewUrl = "";
  let localAgentReady = false;
  let operationBusy = false;
  let restoringDraft = true;
  let lastReceipt = null;
  let createRequestId = null;
  const draftKey = `tarozi-draft-v2-${document.body.dataset.userId}`;
  const printLabels = {not_requested:"Hali chop etilmagan", requested:"Chop etish so'ralgan", dialog_closed:"Natija tasdiqlanmagan", spooled:"Printer navbatiga yuborilgan", confirmed:"Chek chiqqani tasdiqlangan", failed:"Chek chiqmagan"};
  const fmt = value => Number(value || 0).toLocaleString("ru-RU").replace(/[\u00a0\u202f]/g, " ");

  function moneyInputValue(input) {
    const digits = String(input.value || "").replace(/\D/g, "");
    return digits ? Number(digits) : 0;
  }

  function formatMoneyInput(input) {
    let digits = String(input.value || "").replace(/\D/g, "").slice(0, 10);
    digits = digits.replace(/^0+(?=\d)/, "");
    input.value = digits ? fmt(Number(digits)) : "";
    return digits ? Number(digits) : 0;
  }

  async function api(url, options = {}) {
    const headers = new Headers(options.headers || {});
    headers.set("Accept", "application/json");
    if (options.body && !(options.body instanceof FormData)) headers.set("Content-Type", "application/json");
    if (options.method && options.method !== "GET") headers.set("X-CSRF-Token", csrf);
    const response = await fetch(url, { credentials: "same-origin", ...options, headers });
    const data = await response.json().catch(() => ({ success: false, message: "Server javobi noto'g'ri" }));
    if (response.status === 401) {
      window.location.href = "/login";
      throw new Error("Kirish talab qilinadi");
    }
    if (!response.ok || data.success === false) throw new Error(data.message || "So'rov bajarilmadi");
    return data;
  }

  function showToast(message, error = false) {
    const toast = document.getElementById("toast");
    document.getElementById("toast-text").textContent = message;
    document.getElementById("toast-icon").textContent = error ? "!" : "✓";
    toast.classList.toggle("error", error);
    toast.classList.remove("hidden");
    clearTimeout(showToast.timer);
    showToast.timer = setTimeout(() => toast.classList.add("hidden"), 4200);
  }

  function showScreen(target) {
    [mainScreen, paymentScreen].forEach(screen => screen.classList.toggle("hidden", screen !== target));
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  function setClock() {
    const now = new Date();
    document.getElementById("clock").textContent = now.toLocaleTimeString("uz-UZ", { hour12: false });
    document.getElementById("clock-date").textContent = now.toLocaleDateString("uz-UZ", { day: "2-digit", month: "2-digit", year: "numeric", weekday: "short" }).toUpperCase();
  }

  async function loadStats() {
    if (statsLoading) return;
    statsLoading = true;
    try {
      const data = await api("/api/stats/today");
      document.getElementById("today-count").textContent = data.count;
      document.getElementById("today-revenue").textContent = data.total_fmt;
      document.getElementById("current-price-chip").textContent = `Narx: ${Number(data.current_price).toLocaleString("ru-RU")} so'm`;
      servicePrices = { weighing: Number(data.current_price), entry: Number(data.entry_price), reload: Number(data.reload_price) };
      for (const method of ["cash", "card", "bank"]) document.getElementById(`my-${method}`).textContent = `${fmt(data.personal?.[method] || 0)} so'm`;
      lastReceipt = data.last_receipt;
      document.getElementById("last-receipt").disabled = !lastReceipt;
      if (current) renderPricing();
    } catch (error) {
      console.warn(error);
    } finally {
      statsLoading = false;
    }
  }

  async function pollSync() {
    if (syncLoading || document.hidden) return;
    syncLoading = true;
    try {
      const data = await api("/api/sync/state");
      if (lastSyncVersion && data.version !== lastSyncVersion) await loadStats();
      lastSyncVersion = data.version;
    } catch (error) {
      console.warn("Sinxronlash vaqtincha mavjud emas", error);
    } finally {
      syncLoading = false;
    }
  }

  async function checkLocalAgent() {
    const status = document.getElementById("printer-status");
    if (!localAgentEnabled) {
      localAgentReady = false;
      status.classList.add("offline");
      status.querySelector("b").textContent = "Brauzer rejimi";
      document.getElementById("printer-name").textContent = "Brauzer orqali chop etish";
      return;
    }
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 2200);
    try {
      const response = await fetch("http://127.0.0.1:17832/health", { signal: controller.signal, cache: "no-store" });
      const data = await response.json();
      if (!response.ok || !data.success) throw new Error(data.message || "Printer tayyor emas");
      localAgentReady = true;
      status.classList.remove("offline");
      status.querySelector("b").textContent = data.printer || "Tayyor";
      document.getElementById("printer-name").textContent = data.printer || "Termal printer";
    } catch (_error) {
      localAgentReady = false;
      status.classList.add("offline");
      status.querySelector("b").textContent = "Brauzer rejimi";
      document.getElementById("printer-name").textContent = "Brauzer orqali chop etish";
    } finally {
      clearTimeout(timer);
    }
  }

  async function loadRecentPlates() {
    const wrap = document.getElementById("recent-plates");
    try {
      const data = await api("/api/weighings/recent-plates");
      wrap.replaceChildren();
      if (!data.plates.length) {
        const empty = document.createElement("span"); empty.className = "muted"; empty.textContent = "Hali yozuv yo'q"; wrap.append(empty); return;
      }
      data.plates.forEach(plate => {
        const button = document.createElement("button"); button.type = "button"; button.textContent = plate;
        button.addEventListener("click", () => { plateInput.value = plate; validatePlate(); });
        wrap.append(button);
      });
    } catch (_error) {
      wrap.textContent = "Oxirgi raqamlarni olib bo'lmadi";
    }
  }

  function journalCell(text, className = "") {
    const cell = document.createElement("td"); cell.textContent = text ?? "—"; if (className) cell.className = className; return cell;
  }

  function journalStatus(status) {
    const span = document.createElement("span"); span.className = `badge ${status}`;
    span.textContent = status === "paid" ? "To'langan" : status === "pending" ? "Kutilmoqda" : "Bekor qilingan";
    return span;
  }

  async function loadOperatorJournal() {
    const requestId = ++journalRequestId;
    const body = document.getElementById("operator-journal-body");
    const loadingRow = document.createElement("tr"); const loadingCell = journalCell("Yuklanmoqda...", "table-empty"); loadingCell.colSpan = 7; loadingRow.append(loadingCell); body.replaceChildren(loadingRow);
    try {
      const params = new URLSearchParams({ day: journalDay, page: journalPage, per_page: document.getElementById("operator-journal-size").value });
      const query = document.getElementById("operator-journal-search").value.trim(); const status = document.getElementById("operator-journal-status").value;
      if (query) params.set("q", query); if (status) params.set("status", status);
      const data = await api(`/api/operator/weighings?${params}`); if (requestId !== journalRequestId) return; journalPages = data.total_pages; body.replaceChildren();
      if (!data.results.length) { const row = document.createElement("tr"); const cell = journalCell("Bu kunda ma'lumot topilmadi", "table-empty"); cell.colSpan = 7; row.append(cell); body.append(row); }
      data.results.forEach(item => {
        const row = document.createElement("tr"); row.append(journalCell(item.receipt_no), journalCell(item.plate_number), journalCell(`${item.weight_fmt} kg`), journalCell(`${item.total_fmt} so'm`), journalCell(item.time));
        const state = document.createElement("td"); state.append(journalStatus(item.status)); row.append(state);
        const action = document.createElement("td");
        if (item.status === "paid") { const label = document.createElement("small"); label.className="print-state"; label.textContent=printLabels[item.print_state] || "Hali chop etilmagan"; state.append(label); }
        if (item.status === "paid") { const link = document.createElement("a"); link.className = "button secondary journal-receipt-link"; link.href = `/receipt/${item.id}`; link.target = "_blank"; link.rel = "noopener"; link.textContent = "Chekni ko'rish"; action.append(link); } else action.textContent = "—";
        row.append(action); body.append(row);
      });
      document.getElementById("operator-journal-date").textContent = `${data.date_label} kungi operatsiyalar`;
      document.getElementById("operator-journal-total").textContent = `${data.total} ta yozuv`;
      document.getElementById("operator-journal-page").textContent = `${data.page} / ${data.total_pages}`;
      document.getElementById("operator-journal-prev").disabled = data.page <= 1;
      document.getElementById("operator-journal-next").disabled = data.page >= data.total_pages;
    } catch (error) {
      if (requestId !== journalRequestId) return;
      const row = document.createElement("tr"); const cell = journalCell(error.message, "table-empty"); cell.colSpan = 7; row.append(cell); body.replaceChildren(row);
    }
  }

  function openOperatorJournal() {
    journalDay = "today"; journalPage = 1;
    document.querySelectorAll("[data-journal-day]").forEach(button => button.classList.toggle("active", button.dataset.journalDay === journalDay));
    document.getElementById("operator-journal-modal").classList.remove("hidden"); loadOperatorJournal();
  }

  async function saveOperatorBackup() {
    const button = document.getElementById("operator-backup"); const original = button.innerHTML; button.disabled = true;
    try {
      let directory = null;
      if ("showDirectoryPicker" in window) directory = await window.showDirectoryPicker({ mode: "readwrite" });
      button.textContent = "Backup tayyorlanmoqda...";
      const response = await fetch("/api/backup/download", { credentials: "same-origin", cache: "no-store" });
      if (response.status === 401) { window.location.href = "/login"; return; }
      if (!response.ok) { const error = await response.json().catch(() => ({})); throw new Error(error.message || "Backup olinmadi"); }
      const blob = await response.blob(); const now = new Date(); const date = now.toLocaleDateString("uz-UZ").replace(/\//g, "."); const filename = `${date} 00-00 holatiga backup.db`;
      if (directory) {
        const file = await directory.getFileHandle(filename, { create: true }); const writable = await file.createWritable(); await writable.write(blob); await writable.close(); showToast(`Backup tanlangan papkaga saqlandi: ${filename}`);
      } else {
        const url = URL.createObjectURL(blob); const link = document.createElement("a"); link.href = url; link.download = filename; document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000); showToast("Brauzer papka tanlashni qo'llamaydi — backup Downloads papkasiga saqlandi");
      }
    } catch (error) { if (error.name !== "AbortError") showToast(error.message, true); }
    finally { button.disabled = false; button.innerHTML = original; }
  }

  function validatePlate() {
    plateInput.value = plateInput.value.toUpperCase().replace(/\s+/g, " ").replace(/[^0-9A-ZА-ЯЁЎҚҒҲ -]/g, "");
    const plain = plateInput.value.replace(/[ -]/g, "");
    if (/^\d{2}[A-Z]\d{3}[A-Z]{2}$/.test(plain)) plateInput.value=plain.replace(/^(\d{2})([A-Z])(\d{3})([A-Z]{2})$/, "$1 $2 $3 $4");
    else if (/^\d{5}[A-Z]{3}$/.test(plain)) plateInput.value=plain.replace(/^(\d{2})(\d{3})([A-Z]{3})$/, "$1 $2 $3");
    const valid = plateInput.value.trim().length >= 2;
    const badge = document.getElementById("plate-validation");
    badge.classList.toggle("valid", valid);
    badge.lastChild.textContent = valid ? " Format qabul qilindi" : " To'g'ri formatni kiriting";
    return valid;
  }

  function openPlateModal() {
    if (operationBusy || restoringDraft) return;
    if (current) { showScreen(paymentScreen); weightInput.focus(); return; }
    document.getElementById("plate-error").classList.add("hidden");
    validatePlate();
    plateModal.classList.remove("hidden");
    loadRecentPlates();
    setTimeout(() => plateInput.focus(), 80);
  }

  function closePlateModal() { plateModal.classList.add("hidden"); }

  function validWeight(showError = false) {
    const value = Number(weightInput.value);
    const valid = Number.isInteger(value) && value >= 1 && value <= 1000000;
    const error = document.getElementById("weight-error");
    error.classList.toggle("error", showError && !valid);
    error.textContent = showError && !valid ? "Vaznni 1–1 000 000 kg oralig'ida kiriting" : "1 dan 1 000 000 kg gacha";
    return valid;
  }

  function updatePayAvailability() {
    payButton.disabled = operationBusy || !(
      paidCheck.checked && validWeight(false) &&
      validServiceAmount(entryService, entryFeeInput, "entry-fee-error", false) &&
      validServiceAmount(reloadService, reloadFeeInput, "reload-fee-error", false)
    );
  }

  function validServiceAmount(checkbox, input, errorId, showError = false) {
    const value = moneyInputValue(input);
    const valid = !checkbox.checked || (Number.isInteger(value) && value >= 1 && value <= 1000000000);
    const error = document.getElementById(errorId);
    error.classList.toggle("error", showError && !valid);
    error.textContent = showError && !valid ? "To'lovni 1–1 000 000 000 so'm oralig'ida kiriting" : "1 dan 1 000 000 000 so'mgacha";
    return valid;
  }

  function toggleServiceEditor(checkbox, panelId, input, errorId) {
    document.getElementById(panelId).classList.toggle("hidden", !checkbox.checked);
    validServiceAmount(checkbox, input, errorId, false);
    renderPricing();
    if (checkbox.checked) setTimeout(() => input.focus(), 60);
  }

  function renderPricing() {
    if (!current) return;
    const base = Number(current.weighing_fee ?? current.price ?? servicePrices.weighing);
    const entry = entryService.checked ? Math.max(0, moneyInputValue(entryFeeInput)) : 0;
    const reload = reloadService.checked ? Math.max(0, moneyInputValue(reloadFeeInput)) : 0;
    const total = base + entry + reload;
    const weight = Math.max(0, Number(weightInput.value) || 0);
    document.getElementById("payment-price").textContent = fmt(base);
    document.getElementById("grand-total-value").textContent = fmt(total);
    document.getElementById("preview-weight").textContent = fmt(weight);
    document.getElementById("preview-weighing-fee").textContent = `${fmt(base)} so'm`;
    document.getElementById("preview-entry-fee").textContent = `${fmt(entry)} so'm`;
    document.getElementById("preview-reload-fee").textContent = `${fmt(reload)} so'm`;
    document.getElementById("preview-entry-row").classList.toggle("hidden", !entryService.checked);
    document.getElementById("preview-reload-row").classList.toggle("hidden", !reloadService.checked);
    document.getElementById("preview-price").textContent = `${fmt(total)} so'm`;
    updatePayAvailability();
  }

  function setReceiptPreview(item) {
    document.getElementById("payment-plate").textContent = item.plate_number;
    document.getElementById("payment-time").textContent = item.created_at;
    document.getElementById("payment-receipt-top").textContent = `Chek #${item.receipt_no}`;
    document.getElementById("preview-plate").textContent = item.plate_number;
    document.getElementById("preview-time").textContent = item.created_at;
    document.getElementById("preview-receipt").textContent = item.receipt_no;
    document.getElementById("payment-status-badge").textContent = "Kutilmoqda";
    document.getElementById("payment-status-badge").className = "badge pending";
    document.getElementById("receipt-status").textContent = "KUTILMOQDA";
    paidCheck.checked = false;
    weightInput.value = "";
    entryFeeInput.value = "";
    reloadFeeInput.value = "";
    entryService.checked = false;
    reloadService.checked = false;
    document.getElementById("entry-fee-panel").classList.add("hidden");
    document.getElementById("reload-fee-panel").classList.add("hidden");
    payButton.disabled = true;
    paymentMethod = "cash";
    document.querySelectorAll(".payment-method").forEach(button => button.classList.toggle("selected", button.dataset.method === "cash"));
    document.getElementById("receipt-payment-type").textContent = "NAQD PUL";
    renderPricing();
  }

  async function submitPlate() {
    if (operationBusy || restoringDraft) return;
    const button = document.getElementById("btn-submit-plate");
    const errorBox = document.getElementById("plate-error");
    errorBox.classList.add("hidden");
    if (!validatePlate()) {
      errorBox.textContent = "Mashina raqamini to'liq kiriting"; errorBox.classList.remove("hidden"); plateInput.focus(); return;
    }
    button.disabled = true;
    operationBusy = true;
    createRequestId ||= crypto.randomUUID();
    saveDraft();
    try {
      const data = await api("/api/weighings", { method: "POST", body: JSON.stringify({ plate_number: plateInput.value.trim(), request_id:createRequestId }) });
      current = data.weighing;
      if(current.status !== "pending") { current=null; clearDraft(); closePlateModal(); showToast("Bu operatsiya avval yakunlangan. Jurnaldan chekni oching."); return; }
      if (data.service_prices) servicePrices = data.service_prices;
      closePlateModal();
      setReceiptPreview(current);
      showScreen(paymentScreen);
      saveDraft();
    } catch (error) {
      errorBox.textContent = error.message; errorBox.classList.remove("hidden");
    } finally {
      button.disabled = false;
      operationBusy = false;
    }
  }

  async function cancelCurrent() {
    if (operationBusy) return;
    if (!current) { showScreen(mainScreen); return; }
    const button = document.getElementById("btn-cancel");
    button.disabled = true;
    operationBusy = true;
    try {
      await api(`/api/weighings/${current.id}/cancel`, { method: "POST" });
      current = null; clearDraft(); showScreen(mainScreen); loadStats(); showToast("Operatsiya bekor qilindi");
    } catch (error) {
      showToast(error.message, true);
    } finally {
      button.disabled = false;
      operationBusy = false;
    }
  }

  async function localPrint(receipt) {
    const attempt_id=crypto.randomUUID();
    await api(`/api/weighings/${receipt.id}/print`,{method:"POST",body:JSON.stringify({attempt_id,state:"requested",method:"agent"})});
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 4500);
    try {
      const response = await fetch("http://127.0.0.1:17832/print", { method: "POST", mode: "cors", headers: { "Content-Type": "application/json" }, body: JSON.stringify(receipt), signal: controller.signal });
      const data = await response.json().catch(() => ({}));
      if (!response.ok || !data.success) throw new Error(data.message || "Lokal printer xatosi");
      await api(`/api/weighings/${receipt.id}/print`,{method:"POST",body:JSON.stringify({attempt_id,state:"spooled",method:"agent"})}).catch(()=>showToast("Chek printerga yuborildi, ammo chop jurnali saqlanmadi",true));
      receiptPreviewFrame.dataset.agentAttempt=attempt_id;
      try {receiptPreviewFrame.contentWindow.attachAgentAttempt?.(attempt_id);} catch (_) {}
      return data;
    } catch(error) {await api(`/api/weighings/${receipt.id}/print`,{method:"POST",body:JSON.stringify({attempt_id,state:"failed",method:"agent"})}).catch(()=>{});throw error;}
    finally { clearTimeout(timer); }
  }

  function printReceiptPreview() {
    try {
      const frameWindow = receiptPreviewFrame.contentWindow;
      const frameDocument = receiptPreviewFrame.contentDocument;
      if (!frameWindow || !frameDocument?.querySelector(".receipt")) throw new Error("Chek oynasi yuklanmagan");
      if (typeof frameWindow.printReceipt !== "function") throw new Error("Chek hali tayyor emas");
      frameWindow.printReceipt();
    } catch (_error) {
      const directUrl = receiptPreviewUrl.replace("embedded=1", "autoprint=1");
      const popup = directUrl ? window.open(directUrl, "tarozi-receipt", "width=520,height=800") : null;
      showToast(
        popup ? "Chek alohida oynada ochildi" : "Chop etish oynasi bloklandi. Brauzerda pop-up oynalarga ruxsat bering.",
        !popup,
      );
    }
  }

  function requestBrowserPrint() {
    if (receiptPreviewFrame.classList.contains("loading")) receiptPrintPending = true;
    else printReceiptPreview();
  }

  function openReceiptPreview(weighingId, autoPrint = true) {
    delete receiptPreviewFrame.dataset.agentAttempt;
    receiptPrintPending = autoPrint;
    document.getElementById("receipt-frame-loading").textContent = "Chek tayyorlanmoqda...";
    document.getElementById("receipt-frame-loading").classList.remove("hidden");
    receiptPreviewFrame.classList.add("loading");
    receiptPreviewModal.classList.remove("hidden");
    receiptPreviewUrl = `/receipt/${weighingId}?embedded=1`;
    receiptPreviewFrame.src = receiptPreviewUrl;
  }

  function closeReceiptPreview() {
    receiptPrintPending = false;
    receiptPreviewModal.classList.add("hidden");
    receiptPreviewFrame.src = "about:blank";
    receiptPreviewUrl = "";
  }

  async function confirmAndPrint() {
    if (operationBusy || !current || !paidCheck.checked) return;
    if (!validWeight(true)) { weightInput.focus(); updatePayAvailability(); return; }
    if (!validServiceAmount(entryService, entryFeeInput, "entry-fee-error", true)) { entryFeeInput.focus(); updatePayAvailability(); return; }
    if (!validServiceAmount(reloadService, reloadFeeInput, "reload-fee-error", true)) { reloadFeeInput.focus(); updatePayAvailability(); return; }
    payButton.disabled = true;
    operationBusy = true;
    const original = payButton.textContent;
    payButton.textContent = "To'lov saqlanmoqda...";
    try {
      const weighingId = current.id;
      const data = await api(`/api/weighings/${weighingId}/pay`, { method: "POST", body: JSON.stringify({ payment_method: paymentMethod, weight_kg: Number(weightInput.value), entry_service: entryService.checked, entry_fee: entryService.checked ? moneyInputValue(entryFeeInput) : 0, reload_service: reloadService.checked, reload_fee: reloadService.checked ? moneyInputValue(reloadFeeInput) : 0 }) });
      lastReceipt={id:weighingId,receipt_no:data.receipt.receipt_no};
      document.getElementById("payment-success").classList.remove("hidden");
      document.getElementById("success-receipt").textContent=`${data.receipt.plate_number} · ${data.receipt.total_fmt} so'm · Chek ${data.receipt.receipt_no}`;
      document.getElementById("last-print-status").textContent="Chop natijasi hali tasdiqlanmagan";
      clearDraft();
      document.getElementById("payment-status-badge").textContent = "To'langan";
      document.getElementById("payment-status-badge").className = "badge paid";
      document.getElementById("receipt-status").textContent = "TO'LANGAN";
      document.getElementById("preview-weight").textContent = data.receipt.weight_fmt;
      document.getElementById("preview-weighing-fee").textContent = `${data.receipt.weighing_fee_fmt} so'm`;
      document.getElementById("preview-entry-fee").textContent = `${data.receipt.entry_fee_fmt} so'm`;
      document.getElementById("preview-reload-fee").textContent = `${data.receipt.reload_fee_fmt} so'm`;
      document.getElementById("preview-price").textContent = `${data.receipt.total_fmt} so'm`;
      document.getElementById("grand-total-value").textContent = data.receipt.total_fmt;
      payButton.textContent = "Chek tayyorlanmoqda...";
      openReceiptPreview(weighingId, !localAgentReady);
      if (localAgentReady) {
        try {
          const printed = await localPrint(data.receipt);
          showToast(`Chek ${printed.printer || "printer"}ga yuborildi. Nusxa ekranda ochiq.`);
        } catch (printError) {
          localAgentReady = false;
          requestBrowserPrint();
          showToast(`Printer agenti ishlamadi: ${printError.message}. Brauzer chop oynasi ochiladi.`, true);
        }
      } else showToast("Chek tayyor — chop etish oynasi ochiladi");
      current = null;
      showScreen(mainScreen); loadStats();
    } catch (error) {
      showToast(error.message, true);
      payButton.disabled = false;
    } finally {
      payButton.textContent = original;
      operationBusy = false;
      updatePayAvailability();
    }
  }

  async function feedPaper() {
    const button = document.getElementById("btn-test-feed");
    if (!localAgentEnabled) {
      showToast("Qog'ozni surish lokal printer agenti o'rnatilganda ishlaydi", true);
      return;
    }
    button.disabled = true;
    try {
      const response = await fetch("http://127.0.0.1:17832/feed", { method: "POST", mode: "cors", headers: { "Content-Type": "application/json" }, body: "{}" });
      if (!response.ok) throw new Error("Agent ulanmagan");
      showToast("Qog'oz surildi");
    } catch (error) { showToast(error.message, true); }
    finally { button.disabled = false; }
  }

  document.getElementById("btn-start").addEventListener("click", openPlateModal);
  document.getElementById("operator-journal-open").addEventListener("click", openOperatorJournal);
  document.getElementById("operator-journal-close").addEventListener("click", () => document.getElementById("operator-journal-modal").classList.add("hidden"));
  document.querySelectorAll("[data-journal-day]").forEach(button => button.addEventListener("click", () => { journalDay = button.dataset.journalDay; journalPage = 1; document.querySelectorAll("[data-journal-day]").forEach(item => item.classList.toggle("active", item === button)); loadOperatorJournal(); }));
  document.getElementById("operator-journal-search-btn").addEventListener("click", () => { journalPage = 1; loadOperatorJournal(); });
  document.getElementById("operator-journal-search").addEventListener("keydown", event => { if (event.key === "Enter") { journalPage = 1; loadOperatorJournal(); } });
  document.getElementById("operator-journal-status").addEventListener("change", () => { journalPage = 1; loadOperatorJournal(); });
  document.getElementById("operator-journal-size").addEventListener("change", () => { journalPage = 1; loadOperatorJournal(); });
  document.getElementById("operator-journal-prev").addEventListener("click", () => { if (journalPage > 1) { journalPage -= 1; loadOperatorJournal(); } });
  document.getElementById("operator-journal-next").addEventListener("click", () => { if (journalPage < journalPages) { journalPage += 1; loadOperatorJournal(); } });
  document.getElementById("operator-backup").addEventListener("click", saveOperatorBackup);
  receiptPreviewFrame.addEventListener("load", () => {
    if (!receiptPreviewFrame.src.includes("/receipt/")) return;
    let hasReceipt = false;
    try { hasReceipt = Boolean(receiptPreviewFrame.contentDocument?.querySelector("#receipt-raster")); } catch (_) {}
    if (!hasReceipt) {
      receiptPrintPending = false;
      document.getElementById("receipt-frame-loading").textContent = "Chek oynasi yuklanmadi. Chop etish tugmasini qayta bosing.";
      receiptPreviewFrame.classList.remove("loading");
      return;
    }
    document.getElementById("receipt-frame-loading").classList.add("hidden"); receiptPreviewFrame.classList.remove("loading");
    if(receiptPreviewFrame.dataset.agentAttempt)receiptPreviewFrame.contentWindow.attachAgentAttempt?.(receiptPreviewFrame.dataset.agentAttempt);
    if (receiptPrintPending) { receiptPrintPending = false; setTimeout(printReceiptPreview, 500); }
  });
  document.getElementById("receipt-preview-print").addEventListener("click", requestBrowserPrint);
  document.getElementById("receipt-preview-close").addEventListener("click", closeReceiptPreview);
  document.getElementById("receipt-preview-finish").addEventListener("click", closeReceiptPreview);
  document.querySelectorAll("[data-close='plate-modal']").forEach(button => button.addEventListener("click", closePlateModal));
  document.getElementById("plate-clear").addEventListener("click", () => { plateInput.value = ""; validatePlate(); plateInput.focus(); });
  plateInput.addEventListener("input", validatePlate);
  plateInput.addEventListener("keydown", event => { if (event.key === "Enter") submitPlate(); });
  document.getElementById("btn-submit-plate").addEventListener("click", submitPlate);
  document.querySelectorAll(".payment-method").forEach(button => button.addEventListener("click", () => {
    paymentMethod = button.dataset.method;
    document.querySelectorAll(".payment-method").forEach(item => item.classList.toggle("selected", item === button));
    document.getElementById("receipt-payment-type").textContent = { cash: "NAQD PUL", card: "UZCARD / HUMO", bank: "HISOB RAQAM" }[paymentMethod];
  }));
  weightInput.addEventListener("input", () => { if (weightInput.value.length > 7) weightInput.value = weightInput.value.slice(0, 7); validWeight(false); renderPricing(); });
  entryFeeInput.addEventListener("input", () => { formatMoneyInput(entryFeeInput); validServiceAmount(entryService, entryFeeInput, "entry-fee-error", false); renderPricing(); });
  reloadFeeInput.addEventListener("input", () => { formatMoneyInput(reloadFeeInput); validServiceAmount(reloadService, reloadFeeInput, "reload-fee-error", false); renderPricing(); });
  entryFeeInput.addEventListener("focus", () => entryFeeInput.select());
  reloadFeeInput.addEventListener("focus", () => reloadFeeInput.select());
  entryService.addEventListener("change", () => toggleServiceEditor(entryService, "entry-fee-panel", entryFeeInput, "entry-fee-error"));
  reloadService.addEventListener("change", () => toggleServiceEditor(reloadService, "reload-fee-panel", reloadFeeInput, "reload-fee-error"));
  paidCheck.addEventListener("change", updatePayAvailability);
  document.getElementById("btn-cancel").addEventListener("click", cancelCurrent);
  document.getElementById("btn-back-payment").addEventListener("click", cancelCurrent);
  payButton.addEventListener("click", confirmAndPrint);
  document.getElementById("btn-test-feed").addEventListener("click", feedPaper);
  document.addEventListener("keydown", event => { if (event.key !== "Escape" || operationBusy) return; if (!receiptPreviewModal.classList.contains("hidden")) closeReceiptPreview(); else if (!plateModal.classList.contains("hidden")) closePlateModal(); else if (!document.getElementById("operator-journal-modal").classList.contains("hidden")) document.getElementById("operator-journal-modal").classList.add("hidden"); else if(current){saveDraft();showScreen(mainScreen);} });

  function saveDraft() {
    if(restoringDraft) return;
    try { sessionStorage.setItem(draftKey, JSON.stringify({id:current?.id || null,plate:plateInput.value,requestId:createRequestId,weight:weightInput.value,entry:entryService.checked,entryFee:entryFeeInput.value,reload:reloadService.checked,reloadFee:reloadFeeInput.value,method:paymentMethod,paid:paidCheck.checked})); }
    catch (_) { showToast("Brauzer vaqtincha saqlashni bloklagan; sahifani yangilamang",true); }
  }
  function clearDraft() {
    createRequestId=null; plateInput.value="";
    try {sessionStorage.removeItem(draftKey);} catch (_) {}
  }
  async function restoreDraft() {
    try {
      const draft=JSON.parse(sessionStorage.getItem(draftKey) || "null");
      if(!draft) return;
      plateInput.value=draft.plate || ""; createRequestId=draft.requestId || null; validatePlate();
      if(draft.id) {
        const data=await api(`/api/weighings/${draft.id}`);
        if(data.weighing.status!=="pending") {clearDraft(); return;}
        current=data.weighing; setReceiptPreview(current);
        weightInput.value=draft.weight || "";
        entryService.checked=Boolean(draft.entry); reloadService.checked=Boolean(draft.reload);
        entryFeeInput.value=draft.entryFee || ""; reloadFeeInput.value=draft.reloadFee || "";
        paymentMethod=["cash","card","bank"].includes(draft.method)?draft.method:"cash";
        document.querySelectorAll(".payment-method").forEach(b=>b.classList.toggle("selected",b.dataset.method===paymentMethod));
        document.getElementById("receipt-payment-type").textContent={cash:"NAQD PUL",card:"UZCARD / HUMO",bank:"HISOB RAQAM"}[paymentMethod];
        document.getElementById("entry-fee-panel").classList.toggle("hidden",!entryService.checked);
        document.getElementById("reload-fee-panel").classList.toggle("hidden",!reloadService.checked);
        paidCheck.checked=Boolean(draft.paid); showScreen(paymentScreen); renderPricing();
        showToast("Kiritilgan ma'lumotlar tiklandi");
      }
    } catch (_) { showToast("Saqlangan formani tiklab bo'lmadi. Aloqani tekshirib, sahifani yangilang.",true); }
    finally {restoringDraft=false;}
  }
  for(const [panel,input] of [["entry-fee-panel",entryFeeInput],["reload-fee-panel",reloadFeeInput]]) {
    const bar=document.createElement("div"); bar.className="fee-presets";
    for(const amount of [30000,50000,100000]) {
      const b=document.createElement("button"); b.type="button"; b.textContent=fmt(amount);
      b.addEventListener("click",()=>{if(operationBusy)return; input.value=fmt(amount); input.dispatchEvent(new Event("input",{bubbles:true})); input.focus();}); bar.append(b);
    }
    document.getElementById(panel).append(bar);
  }
  [weightInput,entryFeeInput,reloadFeeInput,plateInput].forEach(input=>input.addEventListener("input",saveDraft));
  plateInput.addEventListener("input",()=>{if(!operationBusy && !current) {createRequestId=null; saveDraft();}});
  [paidCheck,entryService,reloadService].forEach(input=>input.addEventListener("change",saveDraft));
  document.querySelectorAll(".payment-method").forEach(b=>b.addEventListener("click",saveDraft));
  document.getElementById("last-receipt").addEventListener("click",()=>{if(lastReceipt)openReceiptPreview(lastReceipt.id,true);});
  window.addEventListener("message",event=>{
    if(event.origin!==location.origin || event.source!==receiptPreviewFrame.contentWindow || event.data?.type!=="receipt-status")return;
    if(event.data.id===lastReceipt?.id)document.getElementById("last-print-status").textContent=printLabels[event.data.state] || "Natija noma'lum";
  });
  document.addEventListener("keydown",event=>{
    if(event.repeat || operationBusy || restoringDraft)return;
    if(event.key==="F2") {event.preventDefault(); if(!receiptPreviewModal.classList.contains("hidden"))closeReceiptPreview(); openPlateModal();}
    if(event.key==="Enter" && !paymentScreen.classList.contains("hidden") && receiptPreviewModal.classList.contains("hidden") && event.target.tagName==="INPUT") {
      event.preventDefault();
      const fields=[weightInput,...(entryService.checked?[entryFeeInput]:[]),...(reloadService.checked?[reloadFeeInput]:[]),paidCheck];
      const index=fields.indexOf(event.target);
      if(index>=0 && index<fields.length-1)fields[index+1].focus();
      else if(!payButton.disabled)confirmAndPrint();
    }
  });
  window.addEventListener("pagehide",()=>{if(current || plateInput.value)saveDraft();});
  restoreDraft();
  setClock(); setInterval(setClock, 1000);
  loadStats(); pollSync(); setInterval(pollSync, 12000);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) { pollSync(); loadStats(); } });
  checkLocalAgent(); if (localAgentEnabled) setInterval(checkLocalAgent, 30000);
})();
