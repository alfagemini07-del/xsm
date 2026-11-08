import * as T from "./vendor/three.module.js";
import {OrbitControls} from "./vendor/OrbitControls.js";
import {mapAnchors} from "./control-counts.js";

const areas={parking:-62,inside:27,border:94};
const views={all:[10,0,0,200],parking:[-62,0,0,92],inside:[27,0,0,95],
 gate1:[-18,0,19,48],gate2:[65,0,19,48],scale:[24,0,33,54],border:[94,0,8,72],top:[10,0,0,205]};
const box=new T.BoxGeometry(1,1,1),wheel=new T.CylinderGeometry(1,1,1,10);
wheel.rotateZ(Math.PI/2);
const colors={CH:0xff6b35,FZ:0x00e5ff};
const vec=(x,y,z)=>new T.Vector3(x,y,z);
const tmp=new T.Object3D();
function instance(geometry,items,scene){
 const mesh=new T.InstancedMesh(geometry,new T.MeshStandardMaterial({roughness:.85}),items.length);
 items.forEach(([x,y,z,w,h,d,c],i)=>{
   tmp.position.set(x,y,z);tmp.scale.set(w,h,d);tmp.rotation.set(0,0,0);tmp.updateMatrix();
   mesh.setMatrixAt(i,tmp.matrix);mesh.setColorAt(i,new T.Color(c));
 });scene.add(mesh);return mesh;
}
export function createScene(canvas,select,connectLabels){
 const renderer=new T.WebGLRenderer({canvas,antialias:true,powerPreference:"low-power"});
 renderer.setPixelRatio(Math.min(devicePixelRatio,1.6));
 const scene=new T.Scene();scene.background=new T.Color(0x0a0f1d);
 const anchors=mapAnchors.map(coords=>vec(...coords));
 const projected=anchors.map(()=>new T.Vector3());
 const camera=new T.PerspectiveCamera(37,1,.5,800);
 const controls=new OrbitControls(camera,canvas);
 controls.maxPolarAngle=Math.PI*.46;controls.minDistance=18;controls.maxDistance=310;
 scene.add(new T.HemisphereLight(0xe8f4ff,0x2a3650,2.0));
 const sun=new T.DirectionalLight(0xfff0db,2.2);sun.position.set(-60,100,50);scene.add(sun);
 const blocks=[];
 const block=(x,y,z,w,h,d,c)=>blocks.push([x,y,z,w,h,d,c]);
 block(3,-1.3,0,214,2.5,105,0x1b283d);
 block(3,.02,19,208,.17,10,0x111c2e);
 block(-5,.02,42,194,.17,7,0x152238);
 for(let x=-99;x<=100;x+=7){block(x,.15,19,3,.04,.22,0x00e5ff);block(x,.15,42,3,.04,.22,0xff8c5a)}
 for(const [name,x] of Object.entries(areas)){
   const deck={parking:0x163252,inside:0x22204c,border:0x4a2228}[name];
   block(x,.04,-9,name==="border"?27:75,.2,40,deck);
   block(x,1.2,-32,name==="border"?27:75,2.4,.6,0x6885a5);
   block(x-9,3,-25,15,6,9,0xcbdae7);block(x-9,6.3,-25,16,.7,10,0x516a8d);
   for(let i=0;i<3;i++)block(x-15+i*4,1.7,-20.4,2.8,3.2,.3,0x324561);
 }
 for(const x of [-18,65]){
   block(x-2,2.1,12,5,4.2,4,0xe8e0c4);block(x-2,2.8,14.05,3.7,1.3,.12,0x334d53);
   block(x,2,15,1,4,1,0xb58a4c);
 }
 for(let x=-94;x<105;x+=16){
   block(x,3,31,.35,6,.35,0x85908a);block(x,6.1,30,.7,.35,2.4,0xffe1a2);
 }
 block(24,.25,33,18,.5,5,0x8a92a4);
 for(const z of [30.7,35.3])block(24,.6,z,18,.24,.25,0xf3c278);
 block(14.5,1.8,29,3,3.6,3.5,0xb1c7d7);
 instance(box,blocks,scene);
 function sign(label,x,z){
   const surface=document.createElement("canvas");surface.width=512;surface.height=72;
   const ctx=surface.getContext("2d");ctx.fillStyle="#152844";ctx.fillRect(0,0,512,72);
   ctx.fillStyle="#e4f4ff";ctx.font="600 38px sans-serif";ctx.textAlign="center";ctx.fillText(label,256,50);
   const sprite=new T.Sprite(new T.SpriteMaterial({map:new T.CanvasTexture(surface),depthTest:false}));
   sprite.position.set(x,9,z);sprite.scale.set(25,3.5,1);scene.add(sprite);
 }
 sign("E-NAVBAT PARKOVKA",-62,-32);sign("FREE ZONE",27,-32);sign("CHEGARA",94,-32);
 sign("O‘ZBEKISTON DARVOZASI",-18,14);sign("AFG‘ONISTON DARVOZASI",65,14);
 let lines=null,capKey="",trucks=[],parts=[],selected=null,mode="all",moves=[],raf=0,aim=null,signature="",gateStates=[];
 const reduced=matchMedia('(prefers-reduced-motion: reduce)').matches;
 let meshCapacity=0;
 let posesDirty=true, colorsDirty=true;
 const drawColor=new T.Color();
 const followDelta=new T.Vector3();
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
   let positionsChanged=false;
   for(let i=0;i<trucks.length;i++){
     const v=trucks[i],m=moves[i];let p=m.end;
     const animating=Boolean(m.path && now<m.finish);
     const updatePose=posesDirty || animating || m.wasMoving;
     if(!updatePose && !colorsDirty)continue;
     m.wasMoving=animating;
     positionsChanged ||= updatePose;
     let angle=v.angle||0;
     if(animating){
       const phase=Math.max(0,1-(m.finish-now)/3200),smooth=phase*phase*(3-2*phase);
       p=m.path.getPoint(smooth);const tangent=m.path.getTangent(smooth);angle=Math.atan2(tangent.x,tangent.z);moving=true;
       gates.forEach((g,k)=>{if(p.distanceTo(g.position)<12&&['open','closed'].includes(gateStates[k]?.state))gateLift[k]=1});
     }else if(v.removed)p=vec(0,-40,0);
     if(mode==="follow" && v.id===selected && v.position && animating){
       followDelta.copy(p).sub(v.position);
       controls.target.add(followDelta);camera.position.add(followDelta);
       if(aim){aim.target.add(followDelta);aim.pos.add(followDelta)}
     }
     v.position=p;v.angle=angle;
     for(const part of parts){
       if(colorsDirty)part.mesh.setColorAt(i,drawColor.setHex(part.color ?? (v.id===selected?0xffdd4d:v.queue_state==='called'?0xf3f4ff:colors[v.flow])));
       if(!updatePose)continue;
       const scale=v.scale||1;
       tmp.position.set(p.x+(part.x*Math.cos(angle)+part.z*Math.sin(angle))*scale,p.y+part.y*scale,p.z+(-part.x*Math.sin(angle)+part.z*Math.cos(angle))*scale);
       tmp.rotation.set(0,angle,0);tmp.scale.set(part.w*scale,part.h*scale,part.d*scale);tmp.updateMatrix();
       part.mesh.setMatrixAt(i,tmp.matrix);
     }
   }
   for(const part of parts){
     if(positionsChanged)part.mesh.instanceMatrix.needsUpdate=true;
     if(colorsDirty && part.mesh.instanceColor)part.mesh.instanceColor.needsUpdate=true;
   }
   posesDirty=false;colorsDirty=false;
   gates.forEach((g,i)=>{const target=-1.35*(gateLift[i]||0);g.rotation.x=reduced?target:T.MathUtils.lerp(g.rotation.x,target,.14);if(Math.abs(g.rotation.x-target)>.01)moving=true});
   if(aim){camera.position.lerp(aim.pos,.16);controls.target.lerp(aim.target,.16);
     if(camera.position.distanceTo(aim.pos)<.15)aim=null;else moving=true}
   controls.update();renderer.render(scene,camera);
   for(let i=0;i<anchors.length;i++)projected[i].copy(anchors[i]).project(camera);
   connectLabels(projected);
   if(moving)invalidate();
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
 function update(rows,cfg,reset=false,spots=[],gateData=[],exits=[]){
   const gatesChanged=gateStates.some((g,i)=>g.state!==gateData[i]?.state) || gateStates.length!==gateData.length;
   rebuildLines(cfg,spots);gateStates=gateData;
   const key=JSON.stringify(rows.map(v=>[v.id,v.area,v.slot,v.flow,v.queue_state,v.version,v.parking_departed_at]))+capKey;
   if(key===signature&&!reset){if(gatesChanged)invalidate();return}signature=key;
   posesDirty=true;colorsDirty=true;
   const previous=new Map((reset?[]:trucks).filter(v=>!v.removed).map(v=>[v.id,v]));
   const previousMoves=new Map(trucks.map((v,i)=>[v.id,moves[i]]));
   const exitPoints=new Map(exits.filter(e=>['ayritom','kpp2','parkovka_tashqi'].includes(e.point)).map(e=>[e.visit_id,e.point]));
   const ids=new Set(rows.map(v=>v.id));
   const departed=[...previous.values()].filter(v=>!ids.has(v.id)&&exitPoints.has(v.id));
   trucks=[...rows.map(v=>{
     const capacity=cfg[v.area+'_capacity'],columns=Math.ceil(Math.sqrt(capacity*2)),rowCount=Math.ceil(capacity/columns);
     return {...v,scale:Math.min(1,70/columns/2,32/rowCount/5.3)};
   }),...departed.map(v=>({...v,removed:true}))];
   moves=trucks.map(v=>{
     const old=previous.get(v.id);
     const exitX={ayritom:94,kpp2:-42,parkovka_tashqi:94}[exitPoints.get(v.id)];
     const end=v.removed?vec(exitX,.3,v.flow==="CH"?42:19):location(v.area,v.slot,cfg[v.area+"_capacity"]);
     if(!v.removed&&v.area==="parking"&&v.parking_departed_at){
       end.set(-22-(v.slot||1)%16*2.3,.3,18+Math.floor(((v.slot||1)-1)/16)%3*2.3);
     }
     let path=null;
     if(old?.position&&(old.area!==v.area||old.slot!==v.slot||old.parking_departed_at!==v.parking_departed_at||v.removed)&&!reduced){
       const road=v.flow==="CH"?42:19;
       const via=[old.position,vec(old.position.x,.3,road)];
       if(v.removed && v.flow!=="CH")via.push(vec(exitPoints.get(v.id)==="ayritom"?65:-18,.3,road));
       else if(!v.removed && old.area==="parking"&&v.area==="inside")via.push(vec(-18,.3,road));
       via.push(vec(end.x,.3,road),end);
       const waypoints=via.filter((p,i)=>!i||p.distanceToSquared(via[i-1])>.0001);
       if(waypoints.length>=2)path=new T.CatmullRomCurve3(waypoints,false,'centripetal');
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
 function restore(viewpoint){
   const valid=v=>Array.isArray(v)&&v.length===3&&v.every(n=>Number.isFinite(n)&&Math.abs(n)<1000);
   if(!valid(viewpoint?.position)||!valid(viewpoint?.target))return false;
   const position=vec(...viewpoint.position),target=vec(...viewpoint.target);
   if(position.distanceTo(target)<2 || position.distanceTo(target)>800)return false;
   mode="saved";aim=null;camera.position.copy(position);controls.target.copy(target);
   controls.update();invalidate();return true;
 }
 return {update,view,restore,
   capture(){return {position:camera.position.toArray(),target:controls.target.toArray()}},
   quality(ratio){renderer.setPixelRatio(Math.min(devicePixelRatio,ratio));renderer.setSize(canvas.clientWidth,canvas.clientHeight,false);invalidate()},
   select(id){selected=id;colorsDirty=true;if(mode==="follow")view(mode);invalidate()}
 };
}
