(()=>{
 const el=id=>document.getElementById(id);
 window.showZoneReceipt=async id=>{
  try{const r=await fetch('/zone/receipt/'+id,{headers:{Accept:'application/json'},signal:AbortSignal.timeout(20000)});if(!r.ok)throw Error('Chek olinmadi');const data=await r.json();el('operation-receipt-image').src='data:image/png;base64,'+data.image;el('operation-print-state').textContent=data.print_attempts?'Chop urinishlari: '+data.print_attempts+' · Tasdiqlangan: '+data.print_confirmed+' · '+data.print_last:'Chop etish qayd etilmagan';el('operation-receipt').showModal();}catch(e){alert(e.message);}
 };
 el('operation-receipt-close').onclick=()=>el('operation-receipt').close();
 if(!el('transfer-file'))return;
 let checked=null;
 el('transfer-file').onchange=()=>{checked=null;el('transfer-import').disabled=true;};
 async function transfer(preview){
  const file=el('transfer-file').files[0];if(!file||(!preview&&checked!==file))return;
  el('transfer-preview').disabled=el('transfer-import').disabled=true;el('transfer-result').textContent='Tekshirilmoqda...';
  const form=new FormData();form.append('backup',file);form.append('preview',preview?'1':'0');
  try{const r=await fetch('/api/admin/transfer/import',{method:'POST',headers:{'X-CSRF-Token':document.body.dataset.csrf},body:form});const d=await r.json();if(!r.ok)throw Error(d.message||'Import bajarilmadi. Joriy baza saqlandi.');el('transfer-result').textContent=d.message+': '+d.inserted+' yangi, '+d.skipped+' mavjud';checked=preview?file:null;}catch(e){checked=null;el('transfer-result').textContent=e.message;}
  finally{el('transfer-preview').disabled=false;el('transfer-import').disabled=checked!==file;}
 }
 el('transfer-preview').onclick=()=>transfer(true);el('transfer-import').onclick=()=>transfer(false);
})();
