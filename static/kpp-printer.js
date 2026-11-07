(()=>{
 const el=id=>document.getElementById(id);let activeAttempt=null;
 const key='xsm-print-outcomes';const pending=()=>{try{return JSON.parse(localStorage.getItem(key)||'{}');}catch{return {};}};
 function save(result){const all=pending();if(all[result.attempt_id]?.state==='confirmed'&&result.state!=='confirmed')return;all[result.attempt_id]=result;localStorage.setItem(key,JSON.stringify(all));}
 async function record(result){
  const r=await fetch('/api/kpp/receipts/'+result.eid+'/print',{method:'POST',headers:{'Content-Type':'application/json','Accept':'application/json','X-CSRF-Token':document.body.dataset.csrf},body:JSON.stringify(result),signal:AbortSignal.timeout(15000)});
  if(!r.ok)throw Error('Chop jurnali serverga hali yetmadi');return r.json();
 }
 const labels={requested:'Chop etish kutilmoqda',sent:'Printerga yuborildi; qog‘oz natijasi tasdiqlanmagan',confirmed:'Chek chop etilgani tasdiqlandi',failed:'Chek chop etilmadi',cancelled:'Chop bekor qilindi',unknown:'Natija noma’lum — qog‘ozni tekshiring'};
 async function outcome(result){
  if(!result?.attempt_id||!result.eid)return;save(result);
  if (String(result.eid) === document.body.dataset.receiptId) {
    el('print-status').textContent=(labels[result.state]||result.state)+(result.detail?' · '+result.detail:'');
  }
  if (result.attempt_id === activeAttempt && !['sent','requested'].includes(result.state)) {
    activeAttempt=null;
    el('print').disabled=false;
  }
  try{await record(result);const all=pending();if(all[result.attempt_id]?.state===result.state){delete all[result.attempt_id];localStorage.setItem(key,JSON.stringify(all));}
   if(window.XsmNativePrinter)location.href='airitom:print-ack?id='+encodeURIComponent(result.attempt_id)+'&state='+encodeURIComponent(result.state);
  }catch{if(String(result.eid)===document.body.dataset.receiptId)el('print-status').textContent+=' · Jurnal aloqadan keyin yuboriladi';}
 }
 window.addEventListener('xsm-print-result',event=>outcome(event.detail));
 async function replay(){for(const result of Object.values(pending()))await outcome(result);}
 window.addEventListener('online',replay);setInterval(()=>{if(!document.hidden)replay();},30000);replay();
 el('printer-settings').onclick=()=>{if(window.XsmNativePrinter)location.href='airitom:printer';else alert('Ichki SUNMI, Bluetooth yoki Wi-Fi printer uchun KPP APKdan foydalaning.');};
 el('print').onclick=async()=>{
  if(activeAttempt)return;const eid=Number(document.body.dataset.receiptId);if(!eid)return;
  const unknown=Object.values(pending()).some(x=>x.eid===eid&&x.state!=='confirmed')||Number(document.body.dataset.printAttempts||0)>0;
  if(unknown&&!confirm('Bu chek avval chopga berilgan. Qog‘ozni tekshiring. Yana bir nusxa chiqarilsinmi?'))return;
  el('print').disabled=true;el('print-status').textContent='Printerga tayyorlanmoqda...';
  const result={attempt_id:crypto.randomUUID(),eid,method:window.XsmNativePrinter?'internal':'browser',state:'requested'};
  try{
   activeAttempt=result.attempt_id;save(result);document.body.dataset.printAttempts=Number(document.body.dataset.printAttempts||0)+1;
   record(result).then(()=>{const all=pending();if(all[result.attempt_id]?.state==='requested'){delete all[result.attempt_id];localStorage.setItem(key,JSON.stringify(all));}}).catch(()=>{});
   if(window.XsmNativePrinter){location.href='airitom:print?attempt_id='+result.attempt_id+'&eid='+eid;}
   else {window.print();result.state=confirm('Chek qog‘ozda to‘liq chiqdimi?')?'confirmed':'unknown';result.detail='Brauzer orqali xodim tasdig‘i';await outcome(result);}
  }catch(e){el('print-status').textContent='Printer ishga tushirilmadi: '+e.message;activeAttempt=null;el('print').disabled=false;}
 };
})();
