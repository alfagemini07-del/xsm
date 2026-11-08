const $=id=>document.getElementById(id);let timer,request,audio=false,seen=new Map(),initialized=false;
const code=t=>t.flow+'-'+String(t.number).padStart(4,'0');
const clock=new Intl.DateTimeFormat('uz-UZ',{timeZone:'Asia/Tashkent',dateStyle:'medium',timeStyle:'medium'});
setInterval(()=>{$('clock').textContent=clock.format(new Date())},1000);
$('audio').onclick=()=>{if(!('speechSynthesis' in window)){$('status').textContent='Ushbu brauzer ovozli e’lonni qo‘llamaydi';return}audio=!audio;$('audio').textContent=audio?'Ovozni o‘chirish':'Ovoz yoqish';if(!audio)speechSynthesis.cancel()};
$('full').onclick=async()=>{try{if(document.fullscreenElement)await document.exitFullscreen();else await document.documentElement.requestFullscreen()}catch{$('status').textContent='Brauzer menyusidan to‘liq ekran rejimini tanlang'}};
async function refresh(){
 clearTimeout(timer);request?.abort();if(document.hidden)return;const current=new AbortController();request=current;const timeout=setTimeout(()=>current.abort(),12000);
 try{
  const r=await fetch('/api/admin/queue-display',{cache:'no-store',signal:current.signal});
  if(r.status===401){window.location.href='/login?next='+encodeURIComponent(window.location.pathname);return}
  if(!r.ok||r.redirected)throw Error();
  const data=await r.json();if(current!==request)return;
  const announced=[];
  for(const panel of document.querySelectorAll('[data-flow]')){
   const rows=data.tickets.filter(t=>t.flow===panel.dataset.flow),called=rows.filter(t=>t.queue_state==='called').sort((a,b)=>new Date(b.called_at)-new Date(a.called_at));
   const waiting=rows.filter(t=>t.queue_state==='waiting').sort((a,b)=>b.priority-a.priority||new Date(a.day)-new Date(b.day)||a.number-b.number);
   const now=panel.querySelector('.current'),next=panel.querySelector('.next');
   const labels=called.slice(0,3).map(code).join(' · ')||'—';if(now.textContent!==labels){now.textContent=labels;now.classList.remove('changed');void now.offsetWidth;now.classList.add('changed')}
   next.replaceChildren(...waiting.slice(0,8).map(t=>{const el=document.createElement('span');el.textContent=code(t);return el}));
   for(const t of called){if(seen.get(t.id)!==t.called_at)announced.push(code(t));seen.set(t.id,t.called_at)}
  }
  if(audio&&initialized&&announced.length){const utterance=new SpeechSynthesisUtterance('Navbat '+announced.slice(0,4).join(', '));utterance.lang='uz-UZ';speechSynthesis.cancel();speechSynthesis.speak(utterance)}
  initialized=true;$('status').textContent='Oxirgi ma’lumot: '+clock.format(new Date(data.at));
 }catch{if(current===request)$('status').textContent='Aloqa uzildi. Ekranda oxirgi olingan navbat turibdi.'}
 finally{clearTimeout(timeout);if(current===request&&!document.hidden)timer=setTimeout(refresh,6500)}
}
document.addEventListener('visibilitychange',refresh);refresh();
