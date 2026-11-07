if(!AbortSignal.timeout)AbortSignal.timeout=ms=>{const c=new AbortController();setTimeout(()=>c.abort(),ms);return c.signal;};
if(!crypto.randomUUID)crypto.randomUUID=()=>{const a=crypto.getRandomValues(new Uint8Array(16));a[6]=(a[6]&15)|64;a[8]=(a[8]&63)|128;const h=Array.from(a,x=>x.toString(16).padStart(2,'0')).join('');return h.slice(0,8)+'-'+h.slice(8,12)+'-'+h.slice(12,16)+'-'+h.slice(16,20)+'-'+h.slice(20);};
"use strict";
const el = id => document.getElementById(id);
const money = n => Number(n || 0).toLocaleString("uz-UZ") + " so‘m";
const storageKey = "kpp-payment:" + document.body.dataset.role + ":" + document.body.dataset.user;
let current = null, submitting = false, heartbeatBusy = false, journalPage = 1, rowPage = 0;
async function api(url, body) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 20000);
  try {
    const response = await fetch(url, {
      method: body === undefined ? "GET" : "POST",
      headers: {Accept:"application/json","Content-Type":"application/json","X-CSRF-Token":document.body.dataset.csrf},
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: controller.signal, cache:"no-store"
    });
    if (response.redirected && new URL(response.url).pathname === "/login") {location.assign("/login"); throw Error("Qayta kirish kerak");}
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      const error = Error(data.message || "So‘rov bajarilmadi. Qayta tekshiring.");
      error.status = response.status;
      throw error;
    }
    return data;
  } catch (error) {
    if (error.name === "AbortError" || error instanceof TypeError) throw Error("Server javobi olinmadi. Aloqani tekshirib, shu amalni qayta yuboring.");
    throw error;
  } finally {clearTimeout(timer);}
}
function remember() {
  try { if(current) sessionStorage.setItem(storageKey,JSON.stringify(current)); else sessionStorage.removeItem(storageKey); } catch {}
}
function screen(payment) {
  el("screen-main").classList.toggle("hidden",payment);
  el("screen-payment").classList.toggle("hidden",!payment);
}
function openVehicle(action) {
  el("action").value=action;
  el("title").textContent=action==="in"?"Kirayotgan avtomobil":"Chiqayotgan avtomobil";
  el("plate").value="";el("plate-error").textContent="";
  delete el("plate").dataset.queue;
  if(el("scan-ticket"))el("scan-ticket").hidden=action!=="in";
  el("vehicle").showModal();
  el("plate").focus({preventScroll:true});
}
document.querySelectorAll("[data-action]").forEach(button=>button.addEventListener("click",()=>openVehicle(button.dataset.action)));
el("back").onclick=el("plate-back").onclick=()=>el("vehicle").close();
el("plate").addEventListener("input",()=>{el("plate").value=el("plate").value.toUpperCase();});
function showPayment(data) {
  current=data; remember();
  const q=data.quote;
  el("ticket").value=data.ticket;
  el("pay-plate").textContent=q.plate;
  el("payment-title").textContent=q.action==="in"?"Hududga kirish":"Hududdan chiqish";
  el("pay-days").textContent=q.days+" kun";el("pay-rate").textContent=money(q.rate);
  el("pay-paid").textContent=money(q.paid);el("pay-amount").textContent=money(q.amount);
  el("paid-fields").hidden=!q.amount;el("received").required=!!q.amount;
  el("received").checked=!!data.attempted;el("method").value=data.method||"cash";
  el("payment-error").textContent=data.attempted?"Avvalgi amal javobini qayta tekshiring.":"";
  el("confirm").textContent=data.attempted?"Natijani qayta tekshirish":"Tasdiqlash va chek";
  el("payment-back").disabled=!!data.attempted;
  screen(true);
}
el("plate-form").addEventListener("submit",async event=>{
  event.preventDefault();
  if(submitting)return;
  submitting=true;
  const button=event.target.querySelector('[type="submit"]');
  button.disabled=true;el("plate-error").textContent="Tekshirilmoqda...";
  try {
    const data=await api("/zone/quote",{plate:el("plate").value,action:el("action").value,queue_no:el("plate").dataset.queue});
    el("vehicle").close();showPayment(data);
  } catch(error){el("plate-error").textContent=error.message;}
  finally {submitting=false;button.disabled=false;}
});
el("payment-back").onclick=()=>{if(current?.attempted)return;current=null;remember();screen(false);};
function showReceipt(data) {
  document.body.dataset.receiptId=data.id;document.body.dataset.printAttempts=data.print_attempts||0;
  el("receipt-image").hidden=!data.image;
  el("receipt-image").src=data.image?"data:image/png;base64,"+data.image:"";
  el("receipt-fields").closest("article").hidden=!!data.image;
  el("print-status").textContent=data.print_confirmed?"Chop etilgani tasdiqlangan: "+data.print_confirmed+" marta":data.print_attempts?"Oldingi chop natijasi tasdiqlanmagan. Qog‘ozni tekshiring.":"";
  const fields=el("receipt-fields");fields.replaceChildren();
  for(const [key,value] of data.fields) {
    const dt=document.createElement("dt"),dd=document.createElement("dd");
    dt.textContent=key;dd.textContent=value;fields.append(dt,dd);
  }
  el("receipt-qr").src="data:image/png;base64,"+data.qr;
  history.replaceState(null,"","/zone/receipt/"+data.id);
  screen(false);
  if(!el("receipt-dialog").open)el("receipt-dialog").showModal();
}
el("payment-form").addEventListener("submit",async event=>{
  event.preventDefault();
  if(submitting||!current)return;
  submitting=true;el("confirm").disabled=true;el("payment-back").disabled=true;
  current.attempted=true;current.method=el("method").value;remember();
  el("payment-error").textContent="Saqlanmoqda...";
  try {
    const data=await api("/zone/confirm",{ticket:current.ticket,method:current.method,received:el("received").checked?"yes":"",amount:String(current.quote.amount)});
    current=null;remember();showReceipt(data);refreshStats();
  } catch(error) {
    el("payment-error").textContent=error.message;
    if([400,403,409].includes(error.status)){current.attempted=false;remember();el("payment-back").disabled=false;}
    el("confirm").textContent="Natijani qayta tekshirish";
  } finally {submitting=false;el("confirm").disabled=false;}
});
function closeReceipt() {el("receipt-dialog").close();history.replaceState(null,"","/zone");}
el("receipt-close").onclick=closeReceipt;
el("receipt-dialog").addEventListener("cancel",event=>{event.preventDefault();closeReceipt();});
function showRows() {
  const list=el("journal").querySelector(".journal-list");
  const rows=[...list.children], size=Math.max(1,Math.floor(list.clientHeight/48));
  const pages=Math.max(1,Math.ceil(rows.length/size));rowPage=Math.min(rowPage,pages-1);
  rows.forEach((r,i)=>r.hidden=i<rowPage*size||i>=(rowPage+1)*size);
  el("rows-page").textContent=rows.length?(rowPage+1)+" / "+pages:"Yozuv yo‘q";
  el("rows-prev").disabled=rowPage===0;el("rows-next").disabled=rowPage===pages-1;
}
async function loadJournal(target=journalPage) {
  el("journal-previous").disabled=el("journal-more").disabled=true;
  el("journal-status").textContent="Yuklanmoqda...";
  try {
    const data=await api("/api/kpp/journal?page="+target);
    journalPage=target;
    const list=el("journal").querySelector(".journal-list");list.replaceChildren();
    for(const row of data.recent) {
      const button=document.createElement("button");button.type="button";button.textContent=row.plate+" · "+(row.action==="in"?"Kirish":"Chiqish")+" · "+money(row.amount);
      button.onclick=async()=>{
        button.disabled=true;
        try{const receipt=await api("/zone/receipt/"+row.id);el("journal").close();showReceipt(receipt);}
        catch(error){el("journal-status").textContent=error.message;}
        finally{button.disabled=false;}
      };
      list.append(button);
    }
    for(const row of data.queue) {
      const button=document.createElement("button");button.type="button";button.textContent="Navbat "+(row.queue_no||row.id)+" · "+row.plate;
      button.onclick=()=>{el("journal").close();openVehicle("in");el("plate").value=row.plate;};
      list.append(button);
    }
    el("journal-more").hidden=!data.more;
    el("journal-previous").hidden=journalPage===1;
    el("journal-status").textContent=data.recent.length||data.queue.length?"":"Yozuv yo‘q";
    rowPage=0;showRows();
  } catch(error){el("journal-status").textContent=error.message;}
  finally{el("journal-previous").disabled=el("journal-more").disabled=false;}
}
el("journal-open").onclick=()=>{journalPage=1;el("journal").showModal();loadJournal();};
el("journal-close").onclick=()=>el("journal").close();
el("journal-more").onclick=()=>loadJournal(journalPage+1);
el("journal-previous").onclick=()=>loadJournal(journalPage-1);
el("rows-prev").onclick=()=>{rowPage--;showRows();};el("rows-next").onclick=()=>{rowPage++;showRows();};
window.addEventListener("resize",()=>{if(el("journal").open)showRows();});
async function refreshStats() {
  try{showStats(await api("/api/kpp/stats"));}catch{}
}
function showStats(data){if(!data)return;el("stat-active").textContent=data.active;el("stat-waiting").textContent=data.waiting;el("stat-departed").textContent=data.departed;el("stat-count").textContent=data.count;el("stat-total").textContent=Number(data.total).toLocaleString("uz-UZ");if(data.as_of)el("connection").title="Ma’lumot vaqti: "+new Date(data.as_of).toLocaleString("uz-UZ",{timeZone:"Asia/Tashkent"});}
async function heartbeat() {
  if(heartbeatBusy||document.hidden||submitting)return;
  heartbeatBusy=true;
  try{const data=await api("/api/kpp/heartbeat",{});el("connection").textContent="● Online";showStats(data.stats);}
  catch(error){el("connection").textContent="○ Server bilan aloqa yo‘q";if(error.status===401||error.status===403)location.assign("/login");}
  finally{heartbeatBusy=false;}
}
const clockFormat = new Intl.DateTimeFormat("uz-UZ",{timeZone:"Asia/Tashkent",hour:"2-digit",minute:"2-digit",second:"2-digit"});
function clock(){if(!document.hidden)el("clock").textContent=clockFormat.format(new Date());}
clock();setInterval(clock,1000);
if(["ayritom","kpp2"].includes(document.body.dataset.role)){heartbeat();setInterval(heartbeat,30000);}
document.addEventListener("visibilitychange",()=>{if(!document.hidden)heartbeat();});
if(document.body.dataset.mode==="receipt")el("receipt-dialog").showModal();
else if(el("initial-quote"))showPayment(JSON.parse(el("initial-quote").textContent));
else {
  try{const saved=JSON.parse(sessionStorage.getItem(storageKey));if(saved?.ticket&&saved.quote)showPayment(saved);}catch{}
}

let fullHeight = innerHeight;
function fitKeyboard() {
  const viewport = window.visualViewport;
  const height = viewport ? viewport.height : innerHeight;
  const typing = document.activeElement === el("plate") && el("vehicle").open;
  if (!typing) fullHeight = innerHeight;
  document.documentElement.style.setProperty("--visible-height", height + "px");
  document.documentElement.style.setProperty("--visible-top", (viewport?.offsetTop || 0) + "px");
  document.body.classList.toggle("keyboard-open", typing && fullHeight - height > 100);
}
window.visualViewport?.addEventListener("resize", fitKeyboard);
window.visualViewport?.addEventListener("scroll", fitKeyboard);
window.addEventListener("resize", fitKeyboard);
el("plate").addEventListener("focus", fitKeyboard);
el("plate").addEventListener("blur", fitKeyboard);
fitKeyboard();
