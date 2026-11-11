import {createCounters} from "./control-counts.js?v=20261108-6";
const $=id=>document.getElementById(id);
const money=new Intl.NumberFormat("uz-UZ");
const time=new Intl.DateTimeFormat("uz-UZ",{timeZone:"Asia/Tashkent",hour:"2-digit",minute:"2-digit"});
const names={scale:"Tarozi (Free Zone)",parkovka_tashqi:"E-navbat parkovka",ayritom:"Afg‘oniston darvozasi",kpp2:"O‘zbekiston darvozasi"};
let scene,state,demo=false,timer,request,selected,attempts={},demoState,command,commandAttempt,commandPayload;
let lastLive;
const statusNames={waiting:"Kutilmoqda (E-navbat parkovkada)",called:"Chaqirilgan (Kirishga tayyor)",hold:"Kutishga qo‘yilgan",no_show:"Kelmagan",cancelled:"Navbat bekor",inside:"Free Zone ichida",completed:"Yakunlangan"};
const actionNames={hold:"Kutishga qo‘yish",resume:"Navbatga qaytarish",no_show:"Kelmadi",cancel:"Navbatni bekor qilish",recall:"Qayta chaqirish",priority:"Ustuvorlik",assign:"Joy ajratish",spot:"Joy holati",gate:"Virtual darvoza holati","call.FZ":"Free Zone navbati chaqirildi","call.CH":"Chegara navbati chaqirildi"};
const gateNames={open:"Ochiq",closed:"Yopiq",locked:"Bloklangan",fault:"Nosoz",maintenance:"Xizmatda"};
const gateTitles={1:names.kpp2,2:names.ayritom};
const code=v=>v.queue_no||("№"+String(v.number).padStart(4,"0"));
const isTech=document.body.dataset.role==="techadmin";
const readOnly=document.body.dataset.role==="observer";
const counters=createCounters($("place-counts"), key => {
  $("camera").value=key;
  scene?.view(key);
});
function row(a,b){
 const line=document.createElement("div"),value=document.createElement("b");
 line.className="row";line.append(document.createTextNode(a),value);value.textContent=b;return line;
}
function kpiCard(title,value,locationHint){
 const card=document.createElement("div"),top=document.createElement("span"),val=document.createElement("b"),sub=document.createElement("small");
 card.className="row kpi-card";
 top.className="kpi-title";top.textContent=title;
 val.textContent=String(value);
 sub.className="kpi-loc";sub.textContent=locationHint;
 card.append(top,val,sub);
 return card;
}
async function api(path,body,signal){
 const r=await fetch(path,{method:body?"POST":"GET",cache:"no-store",signal,
   headers:{Accept:"application/json","Content-Type":"application/json","X-CSRF-Token":document.body.dataset.csrf},
   body:body?JSON.stringify(body):undefined});
 if(r.status===401){window.location.href="/login?next="+encodeURIComponent(window.location.pathname);throw Error("Kirish talab qilinadi")}
 if(r.redirected||!r.headers.get("Content-Type")?.includes("application/json"))throw Error("Sessiya yoki server aloqasini tekshiring");
 const data=await r.json();if(!r.ok)throw Error(data.message||"So‘rov bajarilmadi");return data;
}
async function inspect(id){
 selected=Number(id);$("vehicle").value=String(selected);scene?.select(selected);
 if(!selected){$("timeline").replaceChildren();return}
 try{
   const mode=demo;
   const detail=mode?{events:demoState.events.filter(e=>e.visit_id===selected),audit:[]}:
     await api("/api/admin/visits/"+selected+"/timeline");
   if(mode!==demo||Number(id)!==selected)return;
   $("timeline").replaceChildren(...detail.events.map(e=>row(names[e.point]||e.point,
     ({in:"Kirish",out:"Chiqish",weigh:"Tortish"}[e.action]||e.action)+" · "+money.format(e.amount)+" · "+time.format(new Date(e.occurred)))));
   $("audit").replaceChildren(...detail.audit.map(e=>row((actionNames[e.action]||e.action)+" · "+e.actor,e.detail.reason||time.format(new Date(e.at)))));
   selectedInfo();
 }catch(e){$("timeline").textContent=e.message}
}
function show(data,reset=false){
 state=data;counters.update(data,reset);
 scene?.update(data.vehicles,data.config,reset,data.spots||[],data.gates||[],data.recent_exits||[]);
 $("revenue").textContent=money.format(data.totals.reduce((sum,r)=>sum+Number(r.amount),0))+" so‘m";
 $("breakdown").replaceChildren(...Object.entries(names).map(([k,v])=>row(v,money.format(data.totals.find(t=>t.point===k)?.amount||0))));
 const parkingCountObj=data.counts.find(t=>t.area==="parking");
 const insideCountObj=data.counts.find(t=>t.area==="inside");
 const parkingTotal=Number(parkingCountObj?.parked||0);
 const transitTotal=Number(parkingCountObj?.in_transit||0);
 const insideTotal=Number(insideCountObj?.n||0);
 const pFlow=data.parking_flow||{};
 const borderDeparted=Number(pFlow.border_departures||0);
 const zoneDeparted=Number(pFlow.zone_departures||0);
 const parkingArrivals=Number(pFlow.arrivals||0);
 const parkingDepartures=Number(pFlow.departures||0);
 $("occupancy").replaceChildren(...[["parking","E-navbat parkovka"],["inside","Free Zone hududi"]].map(([area,title])=>
   row(title,(area==="parking"?(parkingCountObj?.parked||0):(data.counts.find(t=>t.area===area)?.n||0))+" / "+data.config[area+"_capacity"])));
 const missing=data.vehicles.filter(v=>!v.slot).length;
 if(missing)$("occupancy").append(row("Joy ajratish kutilmoqda",missing));
 $("occupancy").append(row("O‘zbekiston darvozasiga yo‘lda (oraliq yo‘l)",parkingCountObj?.in_transit??0));
 $("occupancy").append(row("Parkovkadan Ayritomga (Chegaraga) chiqdi",borderDeparted+" ta"));
 $("occupancy").append(row("Parkovkadan Free Zonega chiqdi",zoneDeparted+" ta"));
 vehicleOptions(); selectedInfo();
 const called=data.vehicles.filter(v=>v.queue_state==="called");
 const waiting=data.queue.filter(group=>group.queue_state==="waiting");
 const waitCount=waiting.reduce((sum,r)=>sum+Number(r.n),0);
 const average=waitCount?Math.round(waiting.reduce((sum,r)=>sum+Number(r.n)*Number(r.wait_minutes),0)/waitCount):0;
 const callCount=data.queue.filter(r=>r.queue_state==="called").reduce((sum,r)=>sum+Number(r.n),0);
 const queued=flow=>waiting.find(r=>r.flow===flow)?.n??0;
 const calledFZ=data.queue.find(r=>r.flow==="FZ"&&r.queue_state==="called")?.n??0;
 const calledCH=data.queue.find(r=>r.flow==="CH"&&r.queue_state==="called")?.n??0;
 $("kpis").replaceChildren(
   kpiCard("Jami transport", parkingTotal + transitTotal + insideTotal, `Parkovka: ${parkingTotal} ta · Yo‘lda: ${transitTotal} ta · Free Zone: ${insideTotal} ta`),
   kpiCard("E-navbat parkovka", `${parkingTotal} / ${data.config.parking_capacity}`, `Bugun kirdi: ${parkingArrivals} ta · Chiqdi: ${parkingDepartures} ta`),
   kpiCard("Free Zone ichida", `${insideTotal} / ${data.config.inside_capacity}`, `Bojxona va tarozi hududida (tortildi: ${data.weighed_today??0})`),
   kpiCard("Free Zone navbati", queued("FZ"), `Parkovkada kutmoqda · Chiqdi: ${zoneDeparted} ta`),
   kpiCard("Chegara (Katta yo‘l)", `${queued("CH")} kutmoqda`, `Parkovkadan Ayritomga chiqdi: ${borderDeparted} ta`),
   kpiCard("Chaqirilgan · Kutish", `${callCount} ta · ${average} daq`, `E-navbat parkovkada (FZ: ${calledFZ}, Chegara: ${calledCH})`)
 );
 const alerts=[];
 for(const area of ["parking","inside"]){const count=data.counts.find(c=>c.area===area),occupied=Number(area==="parking"?(count?.parked||0):(count?.n||0)),capacity=data.config[area+"_capacity"],unavailable=(data.spots||[]).filter(s=>s.area===area).length;
  const percent=(occupied+unavailable)/capacity*100;
  const level=percent>=100?"Bo‘sh joy qolmadi":percent>=(data.config.critical_percent??90)?"Joylar tugab bormoqda":
     percent>=(data.config.warning_percent??80)?"Bandlik yuqori":null;
  if(level)alerts.push((area==="parking"?"E-navbat parkovka":"Free Zone")+" · "+level+" ("+Math.floor(percent)+"%)");}
 if(missing)alerts.push(missing+" avtomobilga joy ajratilmagan");
 const late=called.filter(v=>new Date(data.at)-new Date(v.called_at)>data.config.no_show_minutes*60000);
 if(late.length)alerts.push(late.length+" chaqiruv kutish muddatidan o‘tdi. Xodim tekshirsin");
 for(const g of data.gates||[])if(["locked","fault","maintenance"].includes(g.state))alerts.push(gateTitles[g.id]+" · "+gateNames[g.state]);
 $("alerts").replaceChildren(...(alerts.length?alerts:["Faol ogohlantirish yo‘q"]).map(a=>{const p=document.createElement("p");p.textContent=a;return p}));
 $("spots").replaceChildren(...(data.spots||[]).map(s=>row((s.area==="parking"?"Parkovka-":"FreeZone-")+String(s.slot).padStart(3,"0"),s.state==="reserved"?"Rezerv":"Xizmatdan tashqari")));
 $("gates").replaceChildren(...(data.gates||[]).map(g=>{
   const el=document.createElement("div");el.append(row(gateTitles[g.id],gateNames[g.state]));
   if(isTech){const select=document.createElement("select");select.setAttribute("aria-label",gateTitles[g.id]+" holati");
     for(const [k,v] of Object.entries(gateNames))select.add(new Option(v,k));
     select.value=g.state;select.onchange=()=>openCommand({action:"gate",gate:g.id,state:select.value});el.append(select)}
   return el;
 }));
 if(!scene){$("map-fallback").hidden=false;$("map-fallback").replaceChildren(...data.vehicles.map(v=>{const b=document.createElement("button");b.textContent=v.plate+" · "+code(v)+" · "+(v.slot||"joysiz");b.onclick=()=>inspect(v.id);return b}))}
 if($("events"))$("events").replaceChildren(...data.events.slice(0,8).map(e=>{
   const el=document.createElement("article"),h=document.createElement("b"),s=document.createElement("small");
   h.textContent=e.plate;s.textContent=(names[e.point]||e.point)+" · "+time.format(new Date(e.occurred));
   el.append(h,s,document.createTextNode(money.format(e.amount)+" so‘m"));return el;
 }));
 $("calls").replaceChildren(...data.calls.slice(0,3).map(c=>row(c.plate||c.actor,actionNames[c.action]||c.action)));
 $("status").textContent=(demo?"DEMO":"Jonli")+" · "+time.format(new Date(data.at));
 const form=$("settings");
 if(form && (reset||!form.dataset.ready)){for(const k of Object.keys(data.config))if(form.elements[k])form.elements[k].value=data.config[k];form.dataset.ready="1"}
}
function fixture(){
 const vehicles=Array.from({length:20},(_,i)=>({id:i+1,plate:"DEMO "+String(i+1).padStart(3,"0"),
   area:i<14?"parking":"inside",slot:i<14?i+1:i-13,flow:i%3===0&&i<14?"CH":"FZ",number:i+1,version:1,queue_state:i<14?"waiting":"inside",priority:0,started:new Date(Date.now()-(i+1)*60000).toISOString()}));
 const events=vehicles.map(v=>({visit_id:v.id,plate:v.plate,point:v.area==="inside"?"ayritom":"parkovka_tashqi",
   action:"in",purpose:v.flow==="CH"?"border":"zone",amount:30000,occurred:v.started}));
 events.unshift({visit_id:vehicles[14].id,plate:vehicles[14].plate,point:"scale",action:"weigh",amount:10000,occurred:new Date().toISOString()});
 return {vehicles,config:{parking_capacity:1000,inside_capacity:300,reset_hour:0,no_show_minutes:5},totals:[],counts:[],events,calls:[],spots:[],gates:[{id:1,state:"closed"},{id:2,state:"closed"}]};
}
function demoStep(flow){
 const data=demoState,v=data.vehicles.filter(v=>v.flow===flow&&v.area==="parking"&&v.queue_state==="waiting").sort((a,b)=>b.priority-a.priority||a.number-b.number)[0];
 if(!v){$("message").textContent="Demo navbati tugadi";return}
 v.queue_state="called";v.called_at=new Date().toISOString();v.version++;selected=v.id;
 data.calls.unshift({plate:v.plate,action:"Demo · chaqirildi"});renderDemo();inspect(v.id);
}
function demoMove(arriving){
 if(!demo)return;const data=demoState,v=data.vehicles.find(v=>v.id===selected);
 if(!v||arriving&&(v.flow!=="FZ"||v.area!=="parking"||v.queue_state!=="called")||!arriving&&!(v.area==="inside"||v.flow==="CH"&&v.queue_state==="called")){$("message").textContent="Avval mos avtomobilni tanlang va navbatini chaqiring";return}
 const point=arriving?"kpp2":v.flow==="FZ"?"ayritom":"parkovka_tashqi";
 if(arriving){
   data.events.unshift({visit_id:v.id,plate:v.plate,point:"parkovka_tashqi",action:"out",purpose:"zone",amount:0,occurred:new Date().toISOString()});
   v.area="inside";v.queue_state="inside";v.slot=1;while(data.vehicles.some(w=>w!==v&&w.area==="inside"&&w.slot===v.slot))v.slot++;
 }
 else data.vehicles=data.vehicles.filter(w=>w!==v);
 data.events.unshift({visit_id:v.id,plate:v.plate,point,action:arriving?"in":"out",purpose:v.flow==="CH"?"border":"zone",amount:0,occurred:new Date().toISOString()});
 data.calls.unshift({plate:v.plate,action:"Demo · "+names[point]});
 renderDemo();
}
$("demo-enter").onclick=()=>demoMove(true);$("demo-exit").onclick=()=>demoMove(false);
$("demo-reset").onclick=()=>{if(demo){selected=null;demoState=fixture();renderDemo(true)}};
$("demo-add").onclick=()=>{
 if(!demo)return;const id=Math.max(20,...demoState.vehicles.map(v=>v.id))+1;let slot=1;
 while(demoState.vehicles.some(v=>v.area==="parking"&&v.slot===slot)||demoState.spots.some(s=>s.area==="parking"&&s.slot===slot))slot++;
 if(slot>demoState.config.parking_capacity){$("message").textContent="Demo parkovka to‘ldi";return}
 const flow=id%3?"FZ":"CH",number=1+Math.max(0,...demoState.vehicles.filter(v=>v.flow===flow).map(v=>v.number));
 const vehicle={id,plate:"DEMO "+id,area:"parking",flow,number,slot,version:1,priority:0,queue_state:"waiting",started:new Date().toISOString()};
 demoState.vehicles.push(vehicle);
 demoState.events.unshift({visit_id:id,plate:vehicle.plate,point:"parkovka_tashqi",action:"in",purpose:flow==="CH"?"border":"zone",amount:30000,occurred:vehicle.started});
 renderDemo();
};
function renderDemo(reset=false){
 demoState.at=new Date().toISOString();
 demoState.totals=Object.keys(names).map(point=>({point,amount:demoState.events.reduce((sum,e)=>sum+(e.point===point?e.amount:0),0)}));
 demoState.weighed_today=new Set(demoState.events.filter(e=>e.point==="scale").map(e=>e.plate)).size;
 demoState.counts=["parking","inside"].map(area=>({area,n:demoState.vehicles.filter(v=>v.area===area).length}));
 const parking=demoState.vehicles.filter(v=>v.area==="parking");
 Object.assign(demoState.counts[0], {parked:parking.length, in_transit:0, border_wait:parking.filter(v=>v.flow==="CH").length});
 demoState.crossings=["kpp2","ayritom","parkovka_tashqi"].map(point=>({
   point, arrivals:demoState.events.filter(e=>e.point===point&&e.action==="in").length,
   departures:demoState.events.filter(e=>e.point===point&&e.action==="out").length
 }));
 demoState.parking_flow={
   arrivals:demoState.events.filter(e=>e.point==="parkovka_tashqi"&&e.action==="in").length,
   departures:demoState.events.filter(e=>e.point==="parkovka_tashqi"&&e.action==="out").length,
   border_departures:demoState.events.filter(e=>e.point==="parkovka_tashqi"&&e.action==="out"&&e.purpose==="border").length,
   zone_departures:demoState.events.filter(e=>e.point==="parkovka_tashqi"&&e.action==="out"&&e.purpose==="zone").length
 };
 demoState.recent_exits=demoState.events.filter(e=>e.action==="out");
 demoState.queue=[];
 for(const flow of ["FZ","CH"])for(const queue_state of ["waiting","called"]){
   const group=parking.filter(v=>v.flow===flow&&v.queue_state===queue_state);
   const minutes=group.reduce((n,v)=>n+(Date.now()-new Date(v.started))/60000,0);
   demoState.queue.push({flow,queue_state,n:group.length,wait_minutes:group.length?minutes/group.length:0});
 }
 show(demoState,reset);
}
let snapshotTag=null;
async function refresh(reset=false){
 clearTimeout(timer);request?.abort();
 if(demo||document.hidden)return;
 const current=new AbortController(); request=current;
 const timeout=setTimeout(()=>current.abort(),12000);
 try{
   const response=await fetch("/api/admin/control-room",{cache:"no-store",signal:current.signal,
     headers:!reset&&snapshotTag?{"If-None-Match":snapshotTag}:{}});
   if(request!==current||demo)return;
   if(response.status===401){
     window.location.href="/login?next="+encodeURIComponent(window.location.pathname);
     return;
   }
   if(response.status===304 && state){
     state.at=response.headers.get("X-Snapshot-Time")||state.at;
     $("status").textContent="Jonli · "+time.format(new Date(state.at));
   }else{
     if(!response.ok)throw Error("Server aloqasini tekshiring");
     const data=await response.json();
     if(request!==current||demo)return;
     lastLive=data;show(data,reset);snapshotTag=response.headers.get("ETag");
   }
 }catch{if(request===current&&!demo)$("status").textContent="Aloqa yo‘q · oxirgi ko‘rinish"}
 finally{clearTimeout(timeout);if(request===current&&!demo&&!document.hidden)timer=setTimeout(refresh,4500)}
}
let savedView;
function loadDisplaySetting(key){
 try{return JSON.parse(localStorage.getItem("xsm-map-"+key))}catch{return null}
}
function storeDisplaySetting(key,value){
 try{localStorage.setItem("xsm-map-"+key,JSON.stringify(value));return true}catch{return false}
}
$("camera").onchange=e=>{
 if(e.target.value==="saved")scene?.restore(savedView);
 else scene?.view(e.target.value);
};
$("save-camera").onclick=()=>{
 if(!scene)return;
 savedView=scene.capture();const stored=storeDisplaySetting("camera",savedView);
 $("camera").querySelector('[value=saved]').disabled=false;$("camera").value="saved";
 scene.restore(savedView);
 $("message").textContent=stored?"Kamera shu ekran uchun saqlandi":"Kamera joriy seans uchun saqlandi";
};
$("quality").onchange=e=>{const ratio=Number(e.target.value);scene?.quality(ratio);storeDisplaySetting("quality",ratio)};
$("filter").oninput=vehicleOptions;
$("vehicle").onchange=e=>inspect(e.target.value);

// 3D-only Fullscreen Mode: hides header, KPIs, and sidebar (including Bugungi tushum), expanding only the 3D map
function syncFullscreenState(){
 const isFull = Boolean(document.fullscreenElement) || document.body.classList.contains("fullscreen-3d");
 document.body.classList.toggle("is-fullscreen-3d", isFull);
 if($("exit-full"))$("exit-full").hidden = !isFull;
 if($("full"))$("full").textContent = isFull ? "Oddiy ekran" : "To‘liq ekran (3D)";
}
async function toggle3DFullscreen(){
 const mapStage = $("map-stage") || document.querySelector(".map");
 const isFull = Boolean(document.fullscreenElement) || document.body.classList.contains("fullscreen-3d");
 if(isFull){
   document.body.classList.remove("fullscreen-3d");
   if(document.fullscreenElement){
     try{ await document.exitFullscreen(); }catch{}
   }
   syncFullscreenState();
 }else{
   document.body.classList.add("fullscreen-3d");
   syncFullscreenState();
   try{
     if(mapStage?.requestFullscreen) await mapStage.requestFullscreen();
   }catch{}
 }
}
if($("full"))$("full").onclick = toggle3DFullscreen;
if($("exit-full"))$("exit-full").onclick = toggle3DFullscreen;
document.addEventListener("fullscreenchange", ()=>{
 if(!document.fullscreenElement) document.body.classList.remove("fullscreen-3d");
 syncFullscreenState();
});
document.addEventListener("keydown", e=>{
 if(e.key==="Escape" && document.body.classList.contains("fullscreen-3d") && !document.fullscreenElement){
   document.body.classList.remove("fullscreen-3d");
   syncFullscreenState();
 }
});

const demoBtn = $("demo") || $("mode");
if(demoBtn)demoBtn.onclick=()=>{
 if(!demo && !lastLive){$("message").textContent="Jonli ma’lumot yuklanishini kuting";return}
 demo=!demo;snapshotTag=null;request?.abort();clearTimeout(timer);selected=null;$("timeline").replaceChildren();$("message").textContent="";
 $("audit").replaceChildren();scene?.select(null);
 demoBtn.textContent=demo?"Jonli rejim":"Demo";
 $("demo-controls").hidden=!demo;
 $("call-controls").hidden=readOnly && !demo;
 if($("settings"))$("settings").closest("details").hidden=demo;
 if(demo){demoState=fixture();renderDemo(true)}
 else if(lastLive){show(lastLive,true);$("status").textContent="Yangilanmoqda · "+time.format(new Date(lastLive.at));refresh(true)}
 else refresh(true);
};
for(const b of document.querySelectorAll("[data-call]"))b.onclick=async()=>{
 if(demo)return demoStep(b.dataset.call);
 b.disabled=true;
 try{
   const flow=b.dataset.call;attempts[flow]=attempts[flow]||crypto.randomUUID();
   const res=await api("/api/admin/control-room/call",{flow,request_id:attempts[flow]});
   delete attempts[flow];$("message").textContent="Navbat chaqirildi";await refresh();if(res?.visit_id)inspect(res.visit_id);
 }catch(e){if(!e.message.includes("tarmoq"))delete attempts[b.dataset.call];$("message").textContent=e.message}
 finally{b.disabled=false}
};
const settingsForm=$("settings");
if(settingsForm)settingsForm.onsubmit=async e=>{
 e.preventDefault();if(demo){$("message").textContent="Demo rejimda saqlanmaydi";return}
 const body=Object.fromEntries(new FormData(e.target));
 try{await api("/api/admin/control-room/config",body);$("message").textContent="Sozlamalar saqlandi";await refresh(true)}
 catch(err){$("message").textContent=err.message}
};
function vehicleOptions(){
 if(!state)return;const q=$("filter").value.trim().toUpperCase(),select=$("vehicle");
 const areaNames={parking:"E-navbat parkovka",inside:"Free Zone ichida",border:"Chegara"};
 select.replaceChildren(new Option("Tashrifni tanlang",""),...state.vehicles
   .filter(v=>v.id===selected||!q||v.plate.includes(q)||code(v).includes(q)||(v.plate+" "+code(v)).toUpperCase().includes(q)).slice(0,300)
   .map(v=>new Option(`${v.plate} · ${code(v)} · ${areaNames[v.area]||v.area} · ${statusNames[v.queue_state]||v.queue_state}`,v.id)));
 if(selected)select.value=String(selected);
}
function openCommand(data){
 const nextKey=JSON.stringify(data);
 if(commandPayload!==nextKey){commandPayload=nextKey;commandAttempt=crypto.randomUUID()}
 command=data;$("command-error").textContent="";
 const numeric=["assign","priority"].includes(data.action);$("command-value-label").hidden=!numeric;
 const form=$("command-form");form.reset();form.elements.value.required=numeric;
 form.elements.value.min=data.action==="priority"?0:1;form.elements.value.max=data.action==="priority"?2:2500;
 $("command-value-label").querySelector("span").textContent=data.action==="priority"?"0 — oddiy, 1 — maxsus, 2 — favqulodda":"Joy raqami";
 $("command-title").textContent=actionNames[data.action]||"Amalni tasdiqlash";
 $("command-hint").textContent=data.action==="cancel"?"Navbatdan chiqariladi. Tashrif, to‘lov va band joy saqlanadi; haqiqiy chiqish postda qayd etiladi.":data.action==="gate"?"Faqat virtual darvoza o‘zgaradi. Haqiqiy shlagbaum boshqarilmaydi.":(data.plate||"O‘zgarish sababi va xodim nomi tarixda saqlanadi.");
 $("command-dialog").showModal();
}
function selectedInfo(){
 const v=state?.vehicles.find(item=>item.id===selected);
 const infoEl=$("vehicle-info")||$("selected-info");
 if(infoEl)infoEl.replaceChildren();
 $("vehicle-actions").replaceChildren();
 if(!v)return;
 const FlowLabel = v.flow === "CH" ? "Chegara (Afg‘oniston darvozasi)" : "Free Zone (Bojxona va Tarozi)";
 if(infoEl)infoEl.append(
   row("Davlat raqami",v.plate),
   row("Navbat raqami",code(v)),
   row("Yo‘nalishi",FlowLabel),
   row("Hozirgi joyi",v.area==="parking"?(v.parking_departed_at?"Free Zone tomon yo‘lda":"E-navbat parkovka · joy "+(v.slot||"—")):"Free Zone ichida · joy "+(v.slot||"—")),
   row("Holati",statusNames[v.queue_state]||v.queue_state),
   row("Ustuvorlik",["Oddiy","Maxsus","Favqulodda"][v.priority]??(v.priority||0))
 );
 if(readOnly && !demo)return;
 const actions=[];
 if(v.area==="parking"){
   if(["waiting","called"].includes(v.queue_state))actions.push(["Kutishga qo‘yish",{action:"hold"}]);
   if(["hold","no_show","cancelled"].includes(v.queue_state))actions.push(["Navbatga qaytarish",{action:"resume"}]);
   if(v.queue_state==="called")actions.push(["Qayta chaqirish",{action:"recall"}],["Kelmadi",{action:"no_show"}]);
   if(["waiting","called","hold","no_show"].includes(v.queue_state))actions.push(["Navbatni bekor qilish",{action:"cancel"}]);
   if(isTech)actions.push(["Ustuvorlik",{action:"priority"}]);
 }
 actions.push(["Joy ajratish",{action:"assign"}]);
 for(const [label,item] of actions){
   const b=document.createElement("button");b.type="button";b.textContent=label;
   b.onclick=()=>openCommand({...item,visit_id:v.id,version:v.version,plate:v.plate});
   $("vehicle-actions").append(b);
 }
}
$("command-cancel").onclick=()=>$("command-dialog").close();
$("command-form").onsubmit=async e=>{
 e.preventDefault();if(!command)return;
 const form=e.target,fd=new FormData(form),data={...command,reason:String(fd.get("reason")||"").trim()};
 if(command.action==="priority")data.priority=Number(fd.get("value"));
 if(command.action==="assign")data.slot=Number(fd.get("value"));
 const button=form.querySelector('[type=submit]');if(button)button.disabled=true;
 try{
   if(demo){
     const v=demoState.vehicles.find(v=>v.id===data.visit_id);
     if(data.action==="gate")demoState.gates.find(g=>g.id===data.gate).state=data.state;
     else if(data.action==="spot"){demoState.spots=demoState.spots.filter(s=>s.area!==data.area||Number(s.slot)!==Number(data.slot));if(data.state!=="free")demoState.spots.push({...data,slot:Number(data.slot)})}
     else if(v){if(data.action==="assign")v.slot=data.slot;else if(data.action==="priority")v.priority=data.priority;else v.queue_state={hold:"hold",resume:"waiting",no_show:"no_show",cancel:"cancelled",recall:"called"}[data.action];v.version++}
     renderDemo();
   }else{
     const serialized=JSON.stringify(data);if(serialized!==commandPayload){commandPayload=serialized;commandAttempt=crypto.randomUUID()}
     await api("/api/admin/control-room/command",{...data,request_id:commandAttempt});commandAttempt=null;commandPayload=null;await refresh();
   }
   $("command-dialog").close();if(selected)await inspect(selected);
 }catch(err){$("command-error").textContent=err.message}finally{if(button)button.disabled=false}
};
if($("spot-form"))$("spot-form").onsubmit=e=>{e.preventDefault();openCommand({action:"spot",...Object.fromEntries(new FormData(e.target))})};
savedView=loadDisplaySetting("camera");
if(savedView)$("camera").querySelector('[value=saved]').disabled=false;
const savedQuality=loadDisplaySetting("quality");
if([1,1.5,2].includes(savedQuality))$("quality").value=String(savedQuality);
import("./control-scene.js?v=20261111-7").then(m=>{
 scene=m.createScene($("scene"),inspect,counters.connect);
 if([1,1.5,2].includes(savedQuality))scene.quality(savedQuality);
 if(savedView&&scene.restore(savedView)){$("camera").querySelector('[value=saved]').disabled=false;$("camera").value="saved"}
 if(state)scene.update(state.vehicles,state.config,true,state.spots||[],state.gates||[],state.recent_exits||[]);
}).catch(()=>{
 $("map-error").textContent="3D uchun WebGL kerak. Hudud hisoblari va avtomobillar ro‘yxati ko‘rinadi.";
 $("map-error").hidden=false;$("save-camera").disabled=true;
 if(state)show(state,true);
});
document.addEventListener("visibilitychange",()=>{if(document.hidden){clearTimeout(timer);request?.abort()}else refresh()});
refresh(true);
