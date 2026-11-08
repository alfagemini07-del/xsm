(()=>{
 const host=document.createElement('div');host.id='export-progress';host.setAttribute('role','status');host.hidden=true;
 const label=document.createElement('span'),link=document.createElement('a'),close=document.createElement('button');
 link.textContent='Excelni yuklab olish';link.className='button success';close.textContent='×';close.setAttribute('aria-label','Eksport xabarini yopish');
 host.append(label,link,close);document.body.append(host);
 let job,timer,busy=false,attempt,filtersKey,pollVersion=0;
 const csrf=document.body.dataset.csrf;
 function remember(id){
   try{
     if(id)sessionStorage.setItem('xsm-export-job',id);
     else sessionStorage.removeItem('xsm-export-job');
   }catch{}
 }
 async function api(url,body){
   const response=await fetch(url,{method:body?'POST':'GET',cache:'no-store',signal:AbortSignal.timeout(20000),headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:body?JSON.stringify(body):undefined});
   const data=response.headers.get('Content-Type')?.includes('application/json') ? await response.json() : null;
   if(response.redirected || !response.ok || !data){
     const error=Error(response.redirected?'Qayta login qiling':data?.message||'Eksport so‘rovi bajarilmadi');
     error.status=response.redirected?401:response.status;
     throw error;
   }
   return data;
 }
 async function poll(){
   const version=++pollVersion;
   clearTimeout(timer);if(!job||document.hidden)return;
   try{const data=await api('/api/admin/exports/'+job);
    if(version!==pollVersion)return;
    host.hidden=false;
    label.textContent={queued:'Excel navbatda…',running:'Excel tayyorlanmoqda. Ishni davom ettirishingiz mumkin.',ready:'Excel tayyor. Fayl 6 soat saqlanadi.',failed:data.error||'Eksport bajarilmadi'}[data.state];
    link.hidden=data.state!=='ready';if(!link.hidden)link.href='/api/admin/exports/'+job+'/file';
    if(['ready','failed'].includes(data.state)){remember(null);job=null;return}
   }catch(e){
     if(version!==pollVersion)return;
     host.hidden=false;
     if([401,403,404].includes(e.status)){
       label.textContent=e.status===404?'Eksport muddati tugagan. Hisobotni qaytadan yarating.':e.message;
       remember(null);job=null;link.hidden=true;return;
     }
     label.textContent='Eksport holatini tekshirish kutilmoqda: '+e.message;
   }
   timer=setTimeout(poll,3000);
 }
 window.XSMExports={async start(params){
   if(busy)return;busy=true;++pollVersion;clearTimeout(timer);host.hidden=false;link.hidden=true;label.textContent='Eksport yuborilmoqda…';
   const filters=Object.fromEntries(params);delete filters.page;delete filters.per_page;
   const key=JSON.stringify(filters);if(key!==filtersKey){filtersKey=key;attempt=crypto.randomUUID()}
   try{const data=await api('/api/admin/exports',{request_id:attempt,filters});job=data.id;attempt=null;filtersKey=null;remember(job);poll()}
   catch(e){label.textContent=e.message}finally{busy=false}
 }};
 close.onclick=()=>{host.hidden=true;++pollVersion;clearTimeout(timer);job=null;remember(null)};
 document.addEventListener('visibilitychange',poll);
 try{job=sessionStorage.getItem('xsm-export-job')}catch{}if(job)poll();
})();
