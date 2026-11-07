import * as T from "./vendor/three.module.js";
import {OrbitControls} from "./vendor/OrbitControls.js";

const areas={parking:-62,inside:27,border:94};
const views={all:[10,0,0,200],parking:[-62,0,0,92],inside:[27,0,0,95],
 gate1:[-18,0,19,48],gate2:[65,0,19,48],border:[94,0,8,72],top:[10,0,0,205]};
const box=new T.BoxGeometry(1,1,1),wheel=new T.CylinderGeometry(1,1,1,10);
wheel.rotateZ(Math.PI/2);
const colors={CH:0xe5b16d,FZ:0x79cdb1};
const vec=(x,y,z)=>new T.Vector3(x,y,z);
const tmp=new T.Object3D();
function instance(geometry,items,scene){
 const mesh=new T.InstancedMesh(geometry,new T.MeshStandardMaterial({roughness:.85}),items.length);
 items.forEach(([x,y,z,w,h,d,c],i)=>{
   tmp.position.set(x,y,z);tmp.scale.set(w,h,d);tmp.rotation.set(0,0,0);tmp.updateMatrix();
   mesh.setMatrixAt(i,tmp.matrix);mesh.setColorAt(i,new T.Color(c));
 });scene.add(mesh);return mesh;
}
export function createScene(canvas,select){
 const renderer=new T.WebGLRenderer({canvas,antialias:true,powerPreference:"low-power"});
 renderer.setPixelRatio(Math.min(devicePixelRatio,1.6));
 const scene=new T.Scene();scene.background=new T.Color(0x172421);
 const camera=new T.PerspectiveCamera(37,1,.5,800);
 const controls=new OrbitControls(camera,canvas);
 controls.maxPolarAngle=Math.PI*.46;controls.minDistance=18;controls.maxDistance=310;
 scene.add(new T.HemisphereLight(0xffffff,0x586c59,2.1));
 const sun=new T.DirectionalLight(0xffe4bd,2.4);sun.position.set(-60,100,50);scene.add(sun);
 const blocks=[];
 const block=(x,y,z,w,h,d,c)=>blocks.push([x,y,z,w,h,d,c]);
 block(3,-1.3,0,214,2.5,105,0x374b43);
 block(3,.02,19,208,.17,10,0x303a37);
 block(-5,.02,42,194,.17,7,0x353a35);
 for(let x=-99;x<=100;x+=7){block(x,.15,19,3,.04,.22,0x86c7ae);block(x,.15,42,3,.04,.22,0xd5af73)}
 for(const [name,x] of Object.entries(areas)){
   block(x,.04,-9,name==="border"?27:75,.2,40,0x455950);
   block(x,1.2,-32,name==="border"?27:75,2.4,.6,0x8a958e);
   block(x-9,3,-25,15,6,9,0x91a098);block(x-9,6.3,-25,16,.7,10,0x50625a);
   for(let i=0;i<3;i++)block(x-15+i*4,1.7,-20.4,2.8,3.2,.3,0x394640);
 }
 for(const x of [-18,65]){
   block(x-2,2.1,12,5,4.2,4,0xe8e0c4);block(x-2,2.8,14.05,3.7,1.3,.12,0x334d53);
   block(x,2,15,1,4,1,0xb58a4c);
 }
 for(let x=-94;x<105;x+=16){
   block(x,3,31,.35,6,.35,0x85908a);block(x,6.1,30,.7,.35,2.4,0xffe1a2);
 }
 instance(box,blocks,scene);
 function sign(label,x,z){
   const surface=document.createElement("canvas");surface.width=512;surface.height=72;
   const ctx=surface.getContext("2d");ctx.fillStyle="#182822";ctx.fillRect(0,0,512,72);
   ctx.fillStyle="#e8f0e3";ctx.font="600 38px sans-serif";ctx.textAlign="center";ctx.fillText(label,256,50);
   const sprite=new T.Sprite(new T.SpriteMaterial({map:new T.CanvasTexture(surface),depthTest:false}));
   sprite.position.set(x,9,z);sprite.scale.set(25,3.5,1);scene.add(sprite);
 }
 sign("E-NAVBAT PARKOVKA",-62,-32);sign("FREE ZONE",27,-32);sign("CHEGARA",94,-32);
 sign("O‘ZBEKISTON · G1",-18,14);sign("AFG‘ONISTON · G2",65,14);
 let lines=null,capKey="",trucks=[],parts=[],selected=null,mode="all",moves=[],raf=0,aim=null,signature="",gateStates=[];
 const reduced=matchMedia('(prefers-reduced-motion: reduce)').matches;
 let meshCapacity=0;
 const gates=[-18,65].map(x=>{
   const arm=new T.Group();arm.position.set(x,3,15);const bar=new T.Mesh(box,new T.MeshStandardMaterial({color:0xe4b679}));
   bar.position.z=4;bar.scale.set(.35,.35,8);arm.add(bar);scene.add(arm);return arm;
 });
 function location(area,slot,capacity){
   const columns=Math.ceil(Math.sqrt(capacity*2)),rows=Math.ceil(capacity/columns);
   if(!slot)return vec(areas[area],.3,32);
   const i=slot-1;
   return vec(areas[area]-35+(i%columns+.5)*70/columns,.3,-18+(Math.floor(i/columns)+.5)*32/rows);
 }
 function rebuildLines(cfg,spots){
   const key=cfg.parking_capacity+"/"+cfg.inside_capacity+JSON.stringify(spots);if(key===capKey)return;capKey=key;
   if(lines){scene.remove(lines);lines.dispose();lines.material.dispose()}
   const marks=[];for(const a of ["parking","inside"]){
     const cap=cfg[a+"_capacity"],columns=Math.ceil(Math.sqrt(cap*2)),rows=Math.ceil(cap/columns);
     for(let i=1;i<=cap;i++){const p=location(a,i,cap),special=spots.find(s=>s.area===a&&s.slot===i);
       marks.push([p.x-34/columns,.2,p.z,.1,.06,28/rows,0xb0bba0]);
       if(special)marks.push([p.x,.22,p.z,55/columns,.05,24/rows,special.state==='reserved'?0x4d8eb7:0x7d7d79]);}
   }lines=instance(box,marks,scene);
 }
 function makeParts(count){
   if(count<=meshCapacity){for(const p of parts)p.mesh.count=count;return}
   for(const p of parts){scene.remove(p.mesh);p.mesh.dispose();p.mesh.material.dispose()}
   parts=[];meshCapacity=Math.max(32,2**Math.ceil(Math.log2(count)));
   const specs=[[box,0,1.25,-.6,1.55,1.7,3.1,0xc4cec3],[box,0,1.15,1.65,1.5,1.5,1.15,null],
     [box,0,1.6,2.23,1.22,.48,.06,0x1a353e],[box,0,.6,2.25,1.25,.16,.08,0xffe6a4]];
   for(const x of [-.72,.72])for(const z of [-1.65,-.85,1.65])specs.push([wheel,x,.42,z,.43,.32,.43,0x18201d]);
   for(const [geo,x,y,z,w,h,d,color] of specs){
     const mesh=new T.InstancedMesh(geo,new T.MeshStandardMaterial({roughness:.78}),meshCapacity);
     mesh.frustumCulled=false;mesh.count=count;scene.add(mesh);parts.push({mesh,x,y,z,w,h,d,color});
   }
 }
 function draw(){
   raf=0;if(document.hidden)return;
   const now=performance.now();let moving=false;const gateLift=gateStates.map(g=>g.state==='open'?1:0);
   for(let i=0;i<trucks.length;i++){
     const v=trucks[i],m=moves[i];let p=m.end;
     let angle=v.angle||0;
     if(m.path && now<m.finish){
       const phase=Math.max(0,1-(m.finish-now)/3200),smooth=phase*phase*(3-2*phase);
       p=m.path.getPoint(smooth);const tangent=m.path.getTangent(smooth);angle=Math.atan2(tangent.x,tangent.z);moving=true;
       gates.forEach((g,k)=>{if(p.distanceTo(g.position)<12&&gateStates[k]?.state!=='locked'&&gateStates[k]?.state!=='fault')gateLift[k]=1});
     }else if(v.removed)p=vec(0,-40,0);
     v.position=p;v.angle=angle;
     for(const part of parts){
       const scale=v.scale||1;
       tmp.position.set(p.x+(part.x*Math.cos(angle)+part.z*Math.sin(angle))*scale,p.y+part.y*scale,p.z+(-part.x*Math.sin(angle)+part.z*Math.cos(angle))*scale);
       tmp.rotation.set(0,angle,0);tmp.scale.set(part.w*scale,part.h*scale,part.d*scale);tmp.updateMatrix();
       part.mesh.setMatrixAt(i,tmp.matrix);
       part.mesh.setColorAt(i,new T.Color(part.color ?? (v.id===selected?0xffdd4d:v.queue_state==='called'?0xe8f2df:colors[v.flow])));
     }
   }
   for(const part of parts){part.mesh.instanceMatrix.needsUpdate=true;if(part.mesh.instanceColor)part.mesh.instanceColor.needsUpdate=true}
   gates.forEach((g,i)=>{const target=-1.35*(gateLift[i]||0);g.rotation.x=reduced?target:T.MathUtils.lerp(g.rotation.x,target,.14);if(Math.abs(g.rotation.x-target)>.01)moving=true});
   if(aim){camera.position.lerp(aim.pos,.16);controls.target.lerp(aim.target,.16);
     if(camera.position.distanceTo(aim.pos)<.15)aim=null;else moving=true}
   controls.update();renderer.render(scene,camera);if(moving)invalidate();
 }
 function invalidate(){if(!raf&&!document.hidden)raf=requestAnimationFrame(draw)}
 function view(key){
   mode=key;let [x,y,z,d]=views[key]||views.all;
   const chosen=trucks.find(v=>v.id===selected);
   if(key==="follow"&&chosen){({x,y,z}=chosen.position||moves[trucks.indexOf(chosen)].end);d=28}
   if(key==='all')d=Math.max(260,145/Math.max(.6,camera.aspect)/Math.tan(T.MathUtils.degToRad(camera.fov/2)));
   aim={target:vec(x,y,z),pos:key==="top"?vec(x,y+d,z+.1):vec(x+d*.44,y+d*.64,z+d*.72)};
   if(reduced){camera.position.copy(aim.pos);controls.target.copy(aim.target);aim=null}invalidate();
 }
 function update(rows,cfg,reset=false,spots=[],gateData=[]){
   rebuildLines(cfg,spots);gateStates=gateData;
   const key=JSON.stringify(rows.map(v=>[v.id,v.area,v.slot,v.flow,v.queue_state,v.version]))+capKey;
   if(key===signature&&!reset){invalidate();return}signature=key;
   const previous=new Map((reset?[]:trucks).filter(v=>!v.removed).map(v=>[v.id,v]));
   const previousMoves=new Map(trucks.map((v,i)=>[v.id,moves[i]]));
   const ids=new Set(rows.map(v=>v.id));const departed=[...previous.values()].filter(v=>!ids.has(v.id));
   trucks=[...rows.map(v=>{
     const capacity=cfg[v.area+'_capacity'],columns=Math.ceil(Math.sqrt(capacity*2)),rowCount=Math.ceil(capacity/columns);
     return {...v,scale:Math.min(1,70/columns/2,32/rowCount/5.3)};
   }),...departed.map(v=>({...v,removed:true}))];
   moves=trucks.map(v=>{
     const old=previous.get(v.id),end=v.removed?vec(96,.3,19):location(v.area,v.slot,cfg[v.area+"_capacity"]);
     let path=null;
     if(old?.position&&(old.area!==v.area||old.slot!==v.slot||v.removed)&&!reduced){
       const road=v.flow==="CH"?42:19;
       path=new T.CatmullRomCurve3([old.position,vec(old.position.x,.3,road),vec(end.x,.3,road),end],false,'centripetal');
     }
     else if(old && previousMoves.get(v.id)?.finish>performance.now())return previousMoves.get(v.id);
     return {end,path,finish:performance.now()+3200};
   });
   makeParts(trucks.length);if(mode==="follow")view(mode);invalidate();
 }
 controls.addEventListener("change",invalidate);controls.addEventListener("start",()=>{aim=null});
 canvas.addEventListener("click",e=>{
   const rect=canvas.getBoundingClientRect(),ray=new T.Raycaster();
   ray.setFromCamera({x:(e.clientX-rect.left)/rect.width*2-1,y:1-(e.clientY-rect.top)/rect.height*2},camera);
   const hit=ray.intersectObjects(parts.map(p=>p.mesh),false)[0];
   if(hit && !trucks[hit.instanceId].removed)select(trucks[hit.instanceId].id);
 });
 canvas.addEventListener('dblclick',()=>{if(selected)view('follow')});
 new ResizeObserver(()=>{const w=canvas.clientWidth,h=canvas.clientHeight;if(w&&h){renderer.setSize(w,h,false);camera.aspect=w/h;camera.updateProjectionMatrix();if(mode==='all')view('all');invalidate()}}).observe(canvas);
 camera.position.set(100,136,167);controls.target.set(10,0,0);
 document.addEventListener("visibilitychange",invalidate);
 return {update,view,quality(ratio){renderer.setPixelRatio(Math.min(devicePixelRatio,ratio));renderer.setSize(canvas.clientWidth,canvas.clientHeight,false);invalidate()},select(id){selected=id;if(mode==="follow")view(mode);invalidate()}};
}
