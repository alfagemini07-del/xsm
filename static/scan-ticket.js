(()=>{
 const scan=document.getElementById("scan-window"),video=document.getElementById("scanner-video"),status=document.getElementById("scan-status");
 const plate=document.getElementById("plate");
 let stream,timer,sequence=0,decoder;
 plate.addEventListener("input",()=>delete plate.dataset.queue);
 function stop(){
   sequence++;clearTimeout(timer);stream?.getTracks().forEach(t=>t.stop());stream=null;video.srcObject=null;
 }
 scan.addEventListener("close",stop);
 document.getElementById("scan-close").onclick=()=>scan.close();
 document.addEventListener("visibilitychange",()=>{if(document.hidden&&scan.open)scan.close()});
 function load(){
   if(!decoder)decoder=new Promise((resolve,reject)=>{
     const script=document.createElement("script");script.src="/static/vendor/jsQR.js";
     script.onload=resolve;script.onerror=()=>{decoder=null;reject(Error("QR kutubxonasi yuklanmadi"))};
     document.head.append(script);
   });
   return decoder;
 }
 document.getElementById("scan-ticket").onclick=async()=>{
   const id=++sequence;plate.blur();scan.showModal();status.textContent="Kamerani navbat chekining QR kodiga qarating.";
   try{
     await load();if(id!==sequence)return;
     const media=await navigator.mediaDevices.getUserMedia({video:{facingMode:{ideal:"environment"}},audio:false});
     if(id!==sequence){media.getTracks().forEach(t=>t.stop());return}
     stream=media;video.srcObject=media;await video.play();
     const canvas=document.createElement("canvas"),ctx=canvas.getContext("2d",{willReadFrequently:true});
     function read(){
       if(id!==sequence||!scan.open)return;
       if(video.videoWidth){
         canvas.width=Math.min(640,video.videoWidth);canvas.height=Math.round(video.videoHeight*canvas.width/video.videoWidth);
         ctx.drawImage(video,0,0,canvas.width,canvas.height);
         const img=ctx.getImageData(0,0,canvas.width,canvas.height);
         const raw=window.jsQR(img.data,img.width,img.height,{inversionAttempts:"dontInvert"})?.data;
         if(raw){
           const ref=raw.match(/Chek\s*:\s*(\d{2}\.\d{2}\.\d{4}\/\d{7})/);
           const car=raw.match(/Mashina\s*:\s*([^\r\n]+)/);
           if(ref&&car){plate.value=car[1].trim();plate.dataset.queue=ref[1];scan.close();document.getElementById("plate-form").requestSubmit();return}
           status.textContent="E-navbat parkovka navbat chekini ko‘rsating.";
         }
       }
       timer=setTimeout(read,200);
     }
     read();
   }catch{stop();status.textContent="Kamera ochilmadi. Kamera ruxsatini tekshiring yoki davlat raqamini qo‘lda kiriting."}
 };
})();
