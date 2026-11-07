const $=id=>document.getElementById(id);
const money=new Intl.NumberFormat("uz-UZ");
const time=new Intl.DateTimeFormat("uz-UZ",{timeZone:"Asia/Tashkent",hour:"2-digit",minute:"2-digit"});
const names={scale:"Tarozi",parkovka_tashqi:"E-navbat parkovka",ayritom:"Afg‘oniston darvozasi",kpp2:"O‘zbekiston darvozasi"};
let scene,state,demo=false,timer,request,selected,attempts={},demoState,command,commandAttempt,commandPayload;
const statusNames={waiting:"Kutilmoqda",called:"Chaqirilgan",hold:"Kutishga qo‘yilgan",no_show:"Kelmagan",cancelled:"Navbat bekor",inside:"Hudud ichida",completed:"Yakunlangan"};
const actionNames={hold:"Kutishga qo‘yish",resume:"Navbatga qaytarish",no_show:"Kelmadi",cancel:"Navbatni bekor qilish",recall:"Qayta chaqirish",priority:"Ustuvorlik",assign:"Joy ajratish",spot:"Joy holati",gate:"Virtual darvoza holati"};
const gateNames={open:"Ochiq",closed:"Yopiq",locked:"Bloklangan",fault:"Nosoz",maintenance:"Xizmatda"};
const code=v=>v.flow+"-"+String(v.number).padStart(4,"0");
const isTech=document.body.dataset.role==="techadmin";
function row(a,b){
 const line=document.createElement("div"),value=document.createElement("b");
 line.className="row";line.append(document.createTextNode(a),value);value.textContent=b;return line;
}
async function api(path,body,signal){
 const r=await fetch(path,{method:body?"POST":"GET",cache:"no-store",signal,
   headers:{Accept:"application/json","Content-Type":"application/json","X-CSRF-Token":document.body.dataset.csrf},
   body:body?JSON.stringify(body):undefined});
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
     (e.action==="in"?"Kirish":"Chiqish")+" · "+money.format(e.amount)+" · "+time.format(new Date(e.occurred)))));
   $("audit").replaceChildren(...detail.audit.map(e=>row((actionNames[e.action]||e.action)+" · "+e.actor,e.detail.reason||time.format(new Date(e.at)))));
   selectedInfo();
 }catch(e){$("timeline").textContent=e.message}
}
function show(data,reset=false){
 state=data;scene?.update(data.vehicles,data.config,reset,data.spots||[],data.gates||[]);
 $("revenue").textContent=money.format(data.totals.reduce((sum,r)=>sum+Number(r.amount),0))+" so‘m";
 $("breakdown").replaceChildren(...Object.entries(names).map(([k,v])=>row(v,money.format(data.totals.find(t=>t.point===k)?.amount||0))));
 $("occupancy").replaceChildren(...[["parking","E-navbat parkovka"],["inside","Free Zone"]].map(([area,title])=>
   row(title,(data.counts.find(t=>t.area===area)?.n||0)+" / "+data.config[area+"_capacity"])));
 const missing=data.vehicles.filter(v=>!v.slot).length;
 if(missing)$("occupancy").append(row("Joy ajratish kutilmoqda",missing));
 vehicleOptions(); selectedInfo();
 const waiting=data.vehicles.filter(v=>v.area==="parking"&&v.queue_state==="waiting");
 const called=data.vehicles.filter(v=>v.queue_state==="called");
 const average=waiting.length?Math.round(waiting.reduce((n,v)=>n+Math.max(0,(new Date(data.at)-new Date(v.started))/60000),0)/waiting.length):0;
 $("kpis").replaceChildren(...[["Jami transport",data.counts.reduce((n,r)=>n+Number(r.n),0)],["FZ navbati",waiting.filter(v=>v.flow==="FZ").length],["CH navbati",waiting.filter(v=>v.flow==="CH").length],["Chaqirilgan",called.length],["O‘rtacha kutish",average+" daq"]].map(([label,value])=>row(label,value)));
 const alerts=[];
 for(const area of ["parking","inside"]){const occupied=Number(data.counts.find(c=>c.area===area)?.n||0),capacity=data.config[area+"_capacity"],unavailable=(data.spots||[]).filter(s=>s.area===area).length;
  if((occupied+unavailable)/capacity>=.8)alerts.push((area==="parking"?"Parkovka":"Free Zone")+": "+Math.round((occupied+unavailable)/capacity*100)+"% joy band yoki rezervda");}
 if(missing)alerts.push(missing+" avtomobilga joy ajratilmagan");
 const late=called.filter(v=>new Date(data.at)-new Date(v.called_at)>data.config.no_show_minutes*60000);
 if(late.length)alerts.push(late.length+" chaqiruv kutish muddatidan o‘tdi. Xodim tekshirsin");
 for(const g of data.gates||[])if(["locked","fault","maintenance"].includes(g.state))alerts.push("Virtual G"+g.id+": "+gateNames[g.state]);
 $("alerts").replaceChildren(...(alerts.length?alerts:["Faol ogohlantirish yo‘q"]).map(a=>{const p=document.createElement("p");p.textContent=a;return p}));
 $("spots").replaceChildren(...(data.spots||[]).map(s=>row((s.area==="parking"?"P":"FZ")+"-"+String(s.slot).padStart(3,"0"),s.state==="reserved"?"Rezerv":"Xizmatdan tashqari")));
 $("gates").replaceChildren(...(data.gates||[]).map(g=>{const el=document.createElement("div");el.append(row("Virtual G"+g.id,gateNames[g.state]));if(isTech){const select=document.createElement("select");select.setAttribute("aria-label","G"+g.id+" holati");for(const [k,v] of Object.entries(gateNames))select.add(new Option(v,k));select.value=g.state;select.onchange=()=>openCommand({action:"gate",gate:g.id,state:select.value});el.append(select)}return el}));
 if(!scene){$("map-fallback").hidden=false;$("map-fallback").replaceChildren(...data.vehicles.map(v=>{const b=document.createElement("button");b.textContent=v.plate+" · "+code(v)+" · "+(v.slot||"joysiz");b.onclick=()=>inspect(v.id);return b}))}
 $("events").replaceChildren(...data.events.slice(0,8).map(e=>{
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
 return {vehicles,config:{parking_capacity:120,inside_capacity:60,reset_hour:0,no_show_minutes:5},totals:[],counts:[],events:[],calls:[],spots:[],gates:[{id:1,state:"closed"},{id:2,state:"closed"}]};
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
 if(arriving){v.area="inside";v.queue_state="inside";v.slot=1;while(data.vehicles.some(w=>w!==v&&w.area==="inside"&&w.slot===v.slot))v.slot++}
 else data.vehicles=data.vehicles.filter(w=>w!==v);
 data.events.unshift({visit_id:v.id,plate:v.plate,point,action:arriving?"in":"out",amount:0,occurred:new Date().toISOString()});
 data.calls.unshift({plate:v.plate,action:"Demo · "+point});
 renderDemo();
}
$("demo-enter").onclick=()=>demoMove(true);$("demo-exit").onclick=()=>demoMove(false);
$("demo-reset").onclick=()=>{if(demo){selected=null;demoState=fixture();renderDemo(true)}};
$("demo-add").onclick=()=>{
 if(!demo)return;const id=Math.max(20,...demoState.vehicles.map(v=>v.id))+1;let slot=1;
 while(demoState.vehicles.some(v=>v.area==="parking"&&v.slot===slot)||demoState.spots.some(s=>s.area==="parking"&&s.slot===slot))slot++;
 if(slot>demoState.config.parking_capacity){$("message").textContent="Demo parkovka to‘ldi";return}
 const flow=id%3?"FZ":"CH",number=1+Math.max(0,...demoState.vehicles.filter(v=>v.flow===flow).map(v=>v.number));
 demoState.vehicles.push({id,plate:"DEMO "+id,area:"parking",flow,number,slot,version:1,priority:0,queue_state:"waiting",started:new Date().toISOString()});renderDemo();
};
function renderDemo(reset=false){
 demoState.at=new Date().toISOString();
 demoState.counts=["parking","inside"].map(area=>({area,n:demoState.vehicles.filter(v=>v.area===area).length}));
 show(demoState,reset);
}
async function refresh(reset=false){
 clearTimeout(timer);request?.abort();
 if(demo||document.hidden)return;
 const current=new AbortController(); request=current;
 const timeout=setTimeout(()=>current.abort(),12000);
 try{const data=await api("/api/admin/control-room",null,current.signal);
   if(request===current&&!demo)show(data,reset);
 }catch{if(request===current&&!demo)$("status").textContent="Aloqa yo‘q · oxirgi ko‘rinish"}
 finally{clearTimeout(timeout);if(request===current&&!demo&&!document.hidden)timer=setTimeout(refresh,6500)}
}
$("camera").onchange=e=>scene?.view(e.target.value);
$("quality").onchange=e=>scene?.quality(Number(e.target.value));
$("filter").oninput=vehicleOptions;
$("vehicle").onchange=e=>inspect(e.target.value);
$("full").onclick=async()=>{try{if(document.fullscreenElement)await document.exitFullscreen();else await document.documentElement.requestFullscreen()}catch{$("message").textContent="Brauzer menyusidan to‘liq ekran rejimini tanlang"}};
$("demo").onclick=()=>{
 demo=!demo;request?.abort();clearTimeout(timer);selected=null;$("timeline").replaceChildren();$("message").textContent="";
 $("demo").textContent=demo?"Jonli rejim":"Demo";
 $("demo-controls").hidden=!demo;
 if($("settings"))$("settings").closest("details").hidden=demo;
 if(demo){demoState=fixture();renderDemo(true)}else refresh(true);
};
function vehicleOptions(){
 const needle=$("filter").value.trim().toUpperCase();
 const vehicles=state.vehicles.filter(v=>v.id===selected||!needle||(v.plate+" "+code(v)).toUpperCase().includes(needle));
 $("vehicle").replaceChildren(new Option("Tashrifni tanlang",""),...vehicles.map(v=>new Option(code(v)+" · "+v.plate,v.id)));
 if(vehicles.some(v=>v.id===selected))$("vehicle").value=String(selected);
}
function selectedInfo(){
 const v=state?.vehicles.find(v=>v.id===selected);$("vehicle-actions").replaceChildren();$("vehicle-info").replaceChildren();if(!v)return;
 $("vehicle-info").append(row("Holat",statusNames[v.queue_state]),row("Joy",(v.area==="parking"?"P-":"FZ-")+(v.slot||"ajratilmagan")),row("Ustuvorlik",["Oddiy","Maxsus","Favqulodda"][v.priority]));
 const actions=[];
 if(v.area==="parking"){
  if(["waiting","called"].includes(v.queue_state))actions.push("hold");
  if(v.queue_state==="called")actions.push("recall","no_show");
  if(["hold","no_show","cancelled"].includes(v.queue_state))actions.push("resume");
  if(v.queue_state!=="cancelled")actions.push("cancel");
  if(isTech)actions.push("priority");
 }actions.push("assign");
 for(const action of actions){const b=document.createElement("button");b.textContent=actionNames[action];b.onclick=()=>openCommand({action,visit_id:v.id,version:v.version});$("vehicle-actions").append(b)}
}
function openCommand(data){
 command=data;commandAttempt=null;commandPayload=null;const form=$("command-form");form.reset();$("command-title").textContent=actionNames[data.action];
 $("command-error").textContent="";$("command-hint").textContent=data.action==="cancel"?"Navbatdan chiqariladi. Tashrif, to‘lov va band joy saqlanadi; haqiqiy chiqish postda qayd etiladi.":data.action==="gate"?"Faqat virtual darvoza o‘zgaradi. Haqiqiy shlagbaum boshqarilmaydi.":"O‘zgarish sababi va xodim nomi tarixda saqlanadi.";
 const numeric=["assign","priority"].includes(data.action);$("command-value-label").hidden=!numeric;form.elements.value.required=numeric;form.elements.value.min=data.action==="priority"?0:1;form.elements.value.max=data.action==="priority"?2:500;
 $("command-value-label").querySelector("span").textContent=data.action==="priority"?"0 — oddiy, 1 — maxsus, 2 — favqulodda":"Joy raqami";
 $("command-dialog").showModal();
}
$("command-cancel").onclick=()=>$("command-dialog").close();
$("command-form").onsubmit=async e=>{
 e.preventDefault();const form=e.target,data={...command,reason:form.elements.reason.value.trim()};
 if(command.action==="priority")data.priority=Number(form.elements.value.value);
 if(command.action==="assign")data.slot=Number(form.elements.value.value);
 const button=form.querySelector('[type=submit]');button.disabled=true;
 try{
  if(demo){
   const v=demoState.vehicles.find(v=>v.id===data.visit_id);
   if(data.action==="gate")demoState.gates.find(g=>g.id===data.gate).state=data.state;
   else if(data.action==="spot"){demoState.spots=demoState.spots.filter(s=>s.area!==data.area||Number(s.slot)!==Number(data.slot));if(data.state!=="free")demoState.spots.push({...data,slot:Number(data.slot)})}
   else if(v){if(data.action==="assign")v.slot=data.slot;else if(data.action==="priority")v.priority=data.priority;else v.queue_state={hold:"hold",resume:"waiting",no_show:"no_show",cancel:"cancelled",recall:"called"}[data.action];v.version++}
   renderDemo();
  }else{
   const serialized=JSON.stringify(data);if(serialized!==commandPayload){commandPayload=serialized;commandAttempt=crypto.randomUUID()}
   await api("/api/admin/control-room/command",{...data,request_id:commandAttempt});await refresh();
  }
  $("command-dialog").close();if(selected)await inspect(selected);
 }catch(err){$("command-error").textContent=err.message}finally{button.disabled=false}
};
if($("spot-form"))$("spot-form").onsubmit=e=>{e.preventDefault();openCommand({action:"spot",...Object.fromEntries(new FormData(e.target))})};
for(const button of document.querySelectorAll("[data-call]"))button.onclick=async()=>{
 const flow=button.dataset.call;if(demo){demoStep(flow);return}
 button.disabled=true;
 try{attempts[flow]??=crypto.randomUUID();
   await api("/api/admin/control-room/call",{flow,request_id:attempts[flow]});delete attempts[flow];
   $("message").textContent="Navbat chaqirildi";await refresh();
 }catch(e){$("message").textContent=e.message}finally{button.disabled=false}
};
if($("settings"))$("settings").onsubmit=async e=>{
 e.preventDefault();if(demo)return;
 try{await api("/api/admin/control-room/config",Object.fromEntries(new FormData(e.target)));$("message").textContent="Sozlamalar saqlandi";await refresh(true)}
 catch(e){$("message").textContent=e.message}
};
document.addEventListener("visibilitychange",()=>refresh());
try{const {createScene}=await import("./control-scene.js");scene=createScene($("scene"),inspect)}
catch{$("map-error").hidden=false;$("map-error").textContent="3D uchun WebGL kerak. Hudud bandligi va amallar o‘ng panelda ko‘rinadi."}
refresh(true);
