(() => {
  const el = id => document.getElementById(id);
  const panel = el("tab-devices");
  if (!panel) return;
  let busy = false, selected, eventPage=1, eventPages=1;
  const date = value => value ? new Date(value).toLocaleString("uz-UZ", {timeZone:"Asia/Tashkent"}) : "—";
  function cell(row, value) { const td = row.insertCell(); td.textContent = value; return td; }
  function button(td, label, click) {
    const b = document.createElement("button"); b.type="button"; b.className="button secondary compact";
    b.textContent=label; b.onclick=click; td.append(b);
  }
  async function change(id, action, name) {
    try {
      const r=await fetch("/api/admin/devices/"+id,{method:"POST",signal:AbortSignal.timeout(20000),headers:{"Content-Type":"application/json","X-CSRF-Token":document.body.dataset.csrf},body:JSON.stringify({action,name})});
      if(!r.ok)throw Error((await r.json()).message||"Saqlanmadi");
      await refresh();
    } catch(e){el("devices-status").textContent=e.message;}
  }
  el("device-editor").onclose=e=>{if(e.target.returnValue==="save")change(selected,"rename",el("device-name").value);};
  async function refresh(){
    if(busy || document.hidden || panel.classList.contains("hidden"))return;
    busy=true;el("devices-refresh").disabled=true;
    try{
      const r=await fetch("/api/admin/devices?event_page="+eventPage+"&event_size="+el("event-size").value,{cache:"no-store",signal:AbortSignal.timeout(20000)});if(!r.ok)throw Error("Qurilmalar holatini olib bo‘lmadi.");
      const d=await r.json(),body=el("devices-rows");body.replaceChildren();
      for(const device of d.devices){
        const tr=body.insertRow();
        cell(tr,device.name);cell(tr,device.role+" / "+(device.last_user||"—"));cell(tr,device.ip||"—");
        const status=cell(tr,device.blocked?"Bloklangan":!device.approved?"Tasdiq kutilmoqda":device.online?"Online":"Offline");status.className=device.online&&!device.blocked?"device-online":"device-inactive";
        cell(tr,date(device.last_seen));const actions=cell(tr,"");
        if(document.body.dataset.role==="techadmin"){
          button(actions,"Tahrirlash",()=>{selected=device.id;el("device-name").value=device.name;el("device-editor").returnValue="";el("device-editor").showModal();});
          if(!device.approved || device.blocked)button(actions,"Ruxsat berish",()=>change(device.id,"approve"));
          if(!device.blocked)button(actions,"Bloklash",()=>{if(confirm(device.name+" bloklansinmi?"))change(device.id,"block");});
        }
      }
      if(!d.devices.length)cell(body.insertRow(),"Ro‘yxatdan o‘tgan qurilma yo‘q.").colSpan=6;
      eventPage=d.event_page;eventPages=d.event_pages;el("event-page").textContent=eventPage+" / "+eventPages;el("event-total").textContent=d.event_total+" ta hodisa";el("event-prev").disabled=eventPage<=1;el("event-next").disabled=eventPage>=eventPages;
      const events=el("devices-events");events.replaceChildren();
      const names={login:"Kirdi",logout:"Chiqdi",offline:"Signal uzildi",reconnected:"Qayta ulandi",rename:"Nom o‘zgardi",approve:"Ruxsat berildi",block:"Bloklandi"};
      for(const event of d.events){const row=events.insertRow(),[kind,user]=event.event.split(":");[date(event.occurred),event.name,(names[kind]||kind)+(user?" · "+user:""),date(event.previous_seen)].forEach(v=>cell(row,v));}
      el("devices-status").textContent="Tekshirildi · "+date(d.checked_at)+" · 100 soniya signal kelmasa offline";
    }catch(e){el("devices-status").textContent=e.message;}finally{busy=false;el("devices-refresh").disabled=false;}
  }
  document.addEventListener("admin:tab",e=>{if(e.detail==="devices")refresh();});
  el("event-prev").onclick=()=>{if(eventPage>1&&!busy){eventPage--;refresh();}};el("event-next").onclick=()=>{if(eventPage<eventPages&&!busy){eventPage++;refresh();}};el("event-size").onchange=()=>{eventPage=1;refresh();};
  el("devices-refresh").onclick=refresh;setInterval(refresh,30000);
})();
