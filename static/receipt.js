(() => {
  "use strict";
  const image = document.getElementById("receipt-raster");
  const button = document.getElementById("btn-browser-print");
  const status = document.getElementById("print-status");
  const confirmation = document.getElementById("print-confirmation");
  const id = document.body.dataset.id;
  let ready = false, busy = false, attempt = null, method = "browser";
  let pendingRecord = Promise.resolve();
  const labels = {not_requested:"Hali chop etilmagan", requested:"Chop etish so'ralgan", dialog_closed:"Chop oynasi yopilgan — natija tasdiqlanmagan", spooled:"Printer navbatiga yuborilgan", confirmed:"Operator: chek to'liq chiqqanini tasdiqladi", failed:"Chek chiqmadi — qayta urinishingiz mumkin"};
  function record(state) {
    const payload={attempt_id:attempt,state,method};
    pendingRecord=pendingRecord.then(()=>saveRecord(payload));
    return pendingRecord;
  }
  async function saveRecord(payload) {
    if (document.body.dataset.public === "1") return;
    const controller=new AbortController();
    const timer=setTimeout(()=>controller.abort(),8000);
    try {
      const response = await fetch(`/api/weighings/${id}/print`, {method:"POST",credentials:"same-origin",signal:controller.signal,headers:{"Content-Type":"application/json","X-CSRF-Token":document.body.dataset.csrf},body:JSON.stringify(payload)});
      if (!response.ok) throw new Error();
      const saved=await response.json();
      status.textContent = labels[saved.state];
      window.parent.postMessage({type:"receipt-status",id:Number(id),state:saved.state},location.origin);
    } catch (_) { status.textContent = "Chop holatini saqlab bo'lmadi. Chekni qayta to'lamang; jurnal orqali qayta oching."; }
    finally {clearTimeout(timer);}
  }
  async function printReceipt() {
    if (busy) return;
    busy = true; button.disabled = true;
    if(!ready){try{await image.decode();ready=true;}catch(_){busy=false;status.textContent="Chek tasviri yuklanmadi";return;}}
    attempt = crypto.randomUUID();
    method = "browser";
    // Keep the user click's activation: logging must never delay the print dialog.
    record("requested");
    try { window.focus(); window.print(); }
    catch (_) { record("failed"); }
    finally { setTimeout(()=>{busy=false;button.disabled=false;},700); if(confirmation) confirmation.hidden=false; }
  }
  window.addEventListener("afterprint", () => { if(attempt) record("dialog_closed"); });
  document.getElementById("confirm-printed")?.addEventListener("click", () => { if(attempt) {record("confirmed"); confirmation.hidden=true;} });
  document.getElementById("confirm-failed")?.addEventListener("click", () => { if(attempt) {record("failed"); confirmation.hidden=true;} });
  document.getElementById("btn-close-print")?.addEventListener("click", () => window.close());
  button.addEventListener("click", printReceipt);
  window.printReceipt = printReceipt;
  window.attachAgentAttempt = attemptId => { attempt=attemptId;method="agent";status.textContent=labels.spooled;if(confirmation)confirmation.hidden=false; };
  image.decode().then(async () => {
    ready = true; button.disabled=false; status.textContent="Chek tayyor";
    if (document.body.dataset.autoPrint === "1") printReceipt();
    else if (document.body.dataset.public !== "1") {
      try { const r=await fetch(`/api/weighings/${id}/print`); const d=await r.json(); if(r.ok && !busy) {status.textContent=labels[d.state] || "Chek tayyor";if(d.attempt_id && ["spooled","dialog_closed","requested"].includes(d.state)){attempt=d.attempt_id;method=d.method;if(confirmation)confirmation.hidden=false;}} } catch (_) {}
    }
  }).catch(() => { status.textContent="Chek tasviri yuklanmadi. Sahifani yangilang."; });
})();
