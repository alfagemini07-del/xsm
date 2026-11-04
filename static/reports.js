(() => {
  "use strict";
  const el=id=>document.getElementById(id), fmt=n=>Number(n).toLocaleString("ru-RU");
  let page=1,pages=1,sequence=0,loaded=false;
  const today=()=>new Intl.DateTimeFormat("en-CA",{timeZone:"Asia/Tashkent",year:"numeric",month:"2-digit",day:"2-digit"}).format(new Date());
  function range(kind) {
    const end=today(), start=new Date(end+"T12:00:00Z");
    if(kind==="week")start.setUTCDate(start.getUTCDate()-6);
    if(kind==="month")start.setUTCDate(1);
    el("analytics-start").value=start.toISOString().slice(0,10);el("analytics-end").value=end;
  }
  function params() {
    return new URLSearchParams({start:el("analytics-start").value,end:el("analytics-end").value,group:el("analytics-group").value,operator:el("analytics-operator").value,method:el("analytics-method").value,page,per_page:el("analytics-size").value});
  }
  async function load() {
    const ticket=++sequence;el("analytics-error").textContent="Yuklanmoqda...";
    try {
      const r=await fetch(`/api/admin/analytics?${params()}`), d=await r.json();
      if(!r.ok)throw new Error(d.message||"Hisobotni olib bo'lmadi. Sanalarni tekshiring.");
      if(ticket!==sequence)return;
      page=d.page;pages=d.pages;loaded=true;
      el("analytics-title").textContent=d.title;
      const head=document.createElement("tr");
      d.headers.forEach(text=>{const th=document.createElement("th");th.textContent=text;head.append(th);});el("analytics-head").replaceChildren(head);
      el("analytics-body").replaceChildren();
      d.rows.forEach(row=>{const tr=document.createElement("tr");row.forEach(value=>{const td=document.createElement("td");td.textContent=typeof value==="number"?fmt(value):value??"—";tr.append(td);});el("analytics-body").append(tr);});
      el("analytics-summary").replaceChildren();
      for(const [key,label] of [["paid","To'langan"],["cancelled","Bekor"],["total","Tushum"],["cash","Naqd"],["card","Karta"],["bank","Hisob raqam"]]) {const span=document.createElement("span");span.textContent=`${label}: ${fmt(d.totals[key])}`;el("analytics-summary").append(span);}
      el("analytics-count").textContent=`${fmt(d.total)} qator`;el("analytics-page").textContent=`${page} / ${pages}`;
      el("analytics-prev").disabled=page<=1;el("analytics-next").disabled=page>=pages;
      el("analytics-error").textContent=d.total?"":"Ma'lumot topilmadi";
    } catch(error){if(ticket===sequence)el("analytics-error").textContent=error.message;}
  }
  async function download(url,filename,button) {
    if(button.disabled)return;button.disabled=true;el("analytics-error").textContent="Fayl tayyorlanmoqda...";
    try {const r=await fetch(url);if(!r.ok){const d=await r.json().catch(()=>({}));throw new Error(d.message||"Eksport bajarilmadi. Davrni yoki filtrni tekshiring.");}const blob=await r.blob();const link=document.createElement("a");link.href=URL.createObjectURL(blob);link.download=filename;document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(link.href),10000);el("analytics-error").textContent="Fayl tayyor";}
    catch(error){el("analytics-error").textContent=error.message;}finally{button.disabled=false;}
  }
  document.querySelector('[data-tab="analytics"]').addEventListener("click",()=>{if(!loaded)load();});
  el("analytics-load").addEventListener("click",()=>{page=1;load();});
  ["analytics-group","analytics-operator","analytics-method","analytics-size"].forEach(id=>el(id).addEventListener("change",()=>{page=1;load();}));
  document.querySelectorAll("[data-analytics-range]").forEach(b=>b.addEventListener("click",()=>{range(b.dataset.analyticsRange);page=1;load();}));
  el("analytics-prev").addEventListener("click",()=>{if(page>1){page--;load();}});el("analytics-next").addEventListener("click",()=>{if(page<pages){page++;load();}});
  document.querySelectorAll("[data-analytics-export]").forEach(b=>b.addEventListener("click",()=>download(`/api/admin/analytics/export/${b.dataset.analyticsExport}?${params()}`,`hisobot.${b.dataset.analyticsExport}`,b)));
  el("closing-cash").addEventListener("input",()=>{const digits=el("closing-cash").value.replace(/\D/g,"").slice(0,16);el("closing-cash").value=digits?fmt(digits):"";});
  el("closing-download").addEventListener("click",event=>{
    if(el("analytics-start").value!==el("analytics-end").value || el("analytics-method").value || !el("closing-cash").value){el("analytics-error").textContent="Bitta kun, barcha to'lovlar va haqiqiy naqd summani kiriting.";return;}
    const p=params();p.set("actual_cash",el("closing-cash").value.replace(/\D/g,""));download(`/api/admin/cash-closing.pdf?${p}`,`kassa-${el("analytics-start").value}.pdf`,event.currentTarget);
  });
  range("today");
  fetch("/api/admin/report-operators").then(r=>r.json()).then(d=>{(d.users||[]).forEach(u=>{const option=document.createElement("option");option.value=u.id;option.textContent=u.username;el("analytics-operator").append(option);});}).catch(()=>{el("analytics-error").textContent="Operatorlar ro'yxati yuklanmadi. Sahifani yangilang.";});
})();
