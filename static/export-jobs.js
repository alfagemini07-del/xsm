(()=>{
 const host=document.createElement('div');host.id='export-progress';host.setAttribute('role','status');host.hidden=true;
 const label=document.createElement('span'),link=document.createElement('a'),close=document.createElement('button');
 link.textContent='Excelni yuklab olish';link.className='button success';close.textContent='×';close.setAttribute('aria-label','Eksport xabarini yopish');
 host.append(label,link,close);document.body.append(host);
 let job,timer,busy=false,attempt,filtersKey;
 const csrf=document.body.dataset.csrf;
 async function api(url,body){const response=await fetch(url,{method:body?'POST':'GET',cache:'no-store',signal:AbortSignal.timeout(20000),headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:body?JSON.stringify(body):undefined});if(response.redirected)throw Error('Qayta login qiling');const data=await response.json();if(!response.ok)throw Error(data.message||'Eksport so‘rovi bajarilmadi');return data}
 async function poll(){
   clearTimeout(timer);if(!job||document.hidden)return;
   try{const data=await api('/api/admin/exports/'+job);host.hidden=false;
    label.textContent={queued:'Excel navbatda…',running:'Excel tayyorlanmoqda. Ishni davom ettirishingiz mumkin.',ready:'Excel tayyor. Fayl 6 soat saqlanadi.',failed:data.error||'Eksport bajarilmadi'}[data.state];
    link.hidden=data.state!=='ready';if(!link.hidden)link.href='/api/admin/exports/'+job+'/file';
    if(['ready','failed'].includes(data.state)){sessionStorage.removeItem('xsm-export-job');return}
   }catch(e){label.textContent='Eksport holatini tekshirish kutilmoqda: '+e.message}
   timer=setTimeout(poll,3000);
 }
 window.XSMExports={async start(params){
   if(busy)return;busy=true;host.hidden=false;link.hidden=true;label.textContent='Eksport yuborilmoqda…';
   const filters=Object.fromEntries(params);delete filters.page;delete filters.per_page;
   const key=JSON.stringify(filters);if(key!==filtersKey){filtersKey=key;attempt=crypto.randomUUID()}
   try{const data=await api('/api/admin/exports',{request_id:attempt,filters});job=data.id;attempt=null;filtersKey=null;sessionStorage.setItem('xsm-export-job',job);poll()}
   catch(e){label.textContent=e.message}finally{busy=false}
 }};
 close.onclick=()=>{host.hidden=true;clearTimeout(timer);job=null;sessionStorage.removeItem('xsm-export-job')};
 document.addEventListener('visibilitychange',poll);
 job=sessionStorage.getItem('xsm-export-job');if(job)poll();
})();
