import * as T from "./vendor/three.module.js";
import {OrbitControls} from "./vendor/OrbitControls.js";
import {mapAnchors} from "./control-counts.js";

// Real-world terminal layout with a dedicated road between E-navbat Parkovka and Free Zone:
// - E-navbat Parkovka (1000+ trucks): x = -164 .. -30 (center -98, width 134)
// - Parkovka Exit Gate (in middle of Parking east wall): x = -30, z = -6
// - Inter-Zone Road (between Parkovka & Free Zone, facing the main highway): x = -22 (width 14, z = -46 .. +43)
// - Free Zone Entry Gate / O'zbekiston darvozasi (in middle of Free Zone west wall): x = -14, z = -6
// - Free Zone (300+ trucks + internal Tarozi at x=14, z=18): x = -14 .. +90 (center +38, width 104)
// - Afg'oniston darvozasi / KPP1 (in middle of Free Zone east wall): x = +90, z = -6
// - Chegara Post: x = +91 .. +151 (center +122, width 60)
const areas={parking:-98,inside:38,border:122};

const views={
  all:[-4,0,-2,255],
  parking:[-98,0,-6,122],
  inside:[38,0,-4,110],
  gate1:[-22,0,-2,54],
  gate2:[90,0,-6,48],
  scale:[14,0,18,44],
  border:[122,0,-4,76],
  top:[-4,0,-2,282]
};

const box=new T.BoxGeometry(1,1,1),wheel=new T.CylinderGeometry(1,1,1,14),cyl=new T.CylinderGeometry(1,1,1,16),cone=new T.ConeGeometry(1,1,10);
wheel.rotateZ(Math.PI/2);
const colors={CH:0xff8533,FZ:0x00d8ff};
const vec=(x,y,z)=>new T.Vector3(x,y,z);
const tmp=new T.Object3D();

function instance(geometry,items,scene,matOpts={roughness:.62,metalness:.2}){
  if(!items.length)return null;
  const mesh=new T.InstancedMesh(geometry,new T.MeshStandardMaterial(matOpts),items.length);
  items.forEach(([x,y,z,w,h,d,c,rx=0,ry=0,rz=0],i)=>{
    tmp.position.set(x,y,z);tmp.scale.set(w,h,d);tmp.rotation.set(rx,ry,rz);tmp.updateMatrix();
    mesh.setMatrixAt(i,tmp.matrix);mesh.setColorAt(i,new T.Color(c));
  });
  mesh.instanceMatrix.needsUpdate=true;
  if(mesh.instanceColor)mesh.instanceColor.needsUpdate=true;
  scene.add(mesh);return mesh;
}

export function createScene(canvas,select,connectLabels){
  const renderer=new T.WebGLRenderer({canvas,antialias:true,powerPreference:"high-performance"});
  renderer.setPixelRatio(Math.min(devicePixelRatio,1.6));
  renderer.toneMapping=T.ACESFilmicToneMapping;
  renderer.toneMappingExposure=1.22;
  const scene=new T.Scene();
  scene.background=new T.Color(0x08111f);
  scene.fog=new T.FogExp2(0x08111f,0.00135);

  const anchors=mapAnchors.map(coords=>vec(...coords));
  const projected=anchors.map(()=>new T.Vector3());
  const camera=new T.PerspectiveCamera(35,1,.5,1250);
  const controls=new OrbitControls(camera,canvas);
  controls.enableDamping=true;controls.dampingFactor=0.08;
  controls.maxPolarAngle=Math.PI*.465;controls.minDistance=12;controls.maxDistance=460;

  // Natural realistic lighting
  scene.add(new T.HemisphereLight(0xe4f2ff,0x162438,1.8));
  const sun=new T.DirectionalLight(0xfff5e4,2.45);sun.position.set(-85,155,95);scene.add(sun);
  const fill=new T.DirectionalLight(0x7cb8ff,0.95);fill.position.set(120,90,-80);scene.add(fill);
  const rim=new T.DirectionalLight(0x4de8ff,0.5);rim.position.set(0,60,-130);scene.add(rim);

  // Subtle ground grid
  const grid=new T.GridHelper(660,132,0x183050,0x0d1b30);
  grid.position.set(-4,-2.3,0);
  scene.add(grid);

  const matte=[],glossy=[],glow=[],foliage=[];
  const addMatte=(x,y,z,w,h,d,c,rx=0,ry=0,rz=0)=>matte.push([x,y,z,w,h,d,c,rx,ry,rz]);
  const addGloss=(x,y,z,w,h,d,c,rx=0,ry=0,rz=0)=>glossy.push([x,y,z,w,h,d,c,rx,ry,rz]);
  const addGlow=(x,y,z,w,h,d,c,rx=0,ry=0,rz=0)=>glow.push([x,y,z,w,h,d,c,rx,ry,rz]);
  const addTree=(x,z)=>{
    matte.push([x,1.1,z,.35,2.2,.35,0x5c4033]);
    foliage.push([x,2.8,z,1.35,2.4,1.35,0x2d6a4f]);
    foliage.push([x,4.1,z,1.0,1.9,1.0,0x40916c]);
  };

  // =========================================================================
  // 1. EXPANDED COMPOUND FOUNDATION & LANDSCAPING
  // =========================================================================
  addMatte(-5,-1.6,2,346,1.2,128,0x0e192b);
  addMatte(-5,-0.55,2,336,0.9,118,0x16253b);
  // Green landscape belts outside north & south perimeter walls
  addMatte(-5,0.02,-51,326,0.12,5.5,0x1a3829);
  addMatte(-5,0.02,55.5,326,0.12,4.5,0x1a3829);
  for(let x=-158;x<=148;x+=18){
    if(Math.abs(x+22)>9)addTree(x,-51);
    if(Math.abs(x+22)>10 && (x<-36||x>98))addTree(x,55.5);
  }

  // =========================================================================
  // 2. THREE ZONES + DEDICATED AVENUE BETWEEN PARKOVKA & FREE ZONE
  //    - E-navbat Parkovka: x = -164 .. -30 (center -97, width 134, depth 78)
  //    - Inter-Zone Corridor Road: x = -30 .. -14 (center -22, width 16, runs z = -46.5 to +43)
  //    - Free Zone: x = -14 .. +90 (center +38, width 104, depth 78)
  //    - Chegara Post: x = +90 .. +152 (center +121, width 62, depth 78)
  // =========================================================================
  addMatte(-97,.04,-7,134,.16,78,0x152338); // E-navbat Parkovka pad
  addMatte(38,.04,-7,104,.16,78,0x1a213d);  // Free Zone pad
  addMatte(121,.04,-7,62,.16,78,0x281d27);  // Chegara pad

  // INTER-ZONE ROAD (x = -22, width 15.2) between E-navbat Parkovka (-30) and Free Zone (-14)
  // Runs all the way from the north perimeter (-46) straight down into the main highway (z = +43)!
  addMatte(-22,.06,-1.5,15.2,.15,89,0x0d1624);
  // Glowing curbs & center lane dividers on the Inter-Zone Road (x = -22)
  addGlow(-29.2,.15,-4,0.22,.06,74,0x00e5ff);
  addGlow(-14.8,.15,-4,0.22,.06,74,0x00e5ff);
  for(let z=-38;z<=36;z+=6.5){
    if(Math.abs(z+6)>4.5){
      addGlow(-22,.15,z,.22,.04,3.0,0xffd166);
    }
  }
  // Curved junction aprons where Inter-Zone Road (x=-22) meets the Main Highway (z=43)
  addGlow(-29.2,.15,35.5,.22,.06,6.0,0xff8533);
  addGlow(-14.8,.15,35.5,.22,.06,6.0,0xff8533);

  // East-West Central Gate Avenue (z = -6) connecting Parking -> Gate (-30) -> Inter-Zone Road (-22) -> Free Zone Gate (-14) -> Free Zone -> KPP1 (90) -> Border
  addMatte(-5,.07,-6,310,.14,8.8,0x0e1724);
  addGlow(-97,.15,-10.5,132,.05,.18,0x00e5ff);
  addGlow(-97,.15,-1.5,132,.05,.18,0x00e5ff);
  addGlow(38,.15,-10.5,102,.05,.18,0x00e5ff);
  addGlow(38,.15,-1.5,102,.05,.18,0x00e5ff);
  addGlow(121,.15,-10.5,58,.05,.18,0x00e5ff);
  addGlow(121,.15,-1.5,58,.05,.18,0x00e5ff);
  for(let x=-154;x<=146;x+=7){
    if(Math.abs(x+30)>4 && Math.abs(x+22)>4 && Math.abs(x+14)>4 && Math.abs(x-90)>4.5){
      addGlow(x,.15,-6,3.0,.04,.18,0xd8ecff);
    }
  }

  // Southern Main Highway / Katta Yo'l (z = 43)
  addMatte(-5,.05,43,326,.15,10.5,0x0f1828);
  addGlow(-5,.14,37.6,326,.05,.24,0xff8533);
  addGlow(-5,.14,48.4,326,.05,.24,0xff8533);
  for(let x=-158;x<=148;x+=8){
    addGlow(x,.14,43,3.6,.04,.22,0xff9e5e);
  }
  // Slip road from Main Highway into Chegara (x = 122)
  addMatte(122,.05,35,12,.14,9,0x121d30);

  // =========================================================================
  // 3. PERIMETER WALLS & THREE REALISTIC GATE COMPLEXES:
  //    1) E-navbat Parkovka East Wall (x = -30) with its own Gate in the middle (z = -6)
  //    2) Free Zone West Wall (x = -14) with O'zbekiston darvozasi (KPP2) in the middle (z = -6)
  //    3) Free Zone East Wall (x = +90) with Afg'oniston darvozasi (KPP1) in the middle (z = -6)
  // =========================================================================
  // Rear security wall (z = -46.5), split by the Inter-Zone Road at x = -22
  addMatte(-97,1.5,-46.5,134,3.0,.5,0x273b59);
  addGlow(-97,3.05,-46.5,134,.14,.6,0x38bdf8);
  addMatte(69,1.5,-46.5,166,3.0,.5,0x273b59);
  addGlow(69,3.05,-46.5,166,.14,.6,0x38bdf8);
  for(let x=-162;x<=150;x+=12){
    if(Math.abs(x+22)>7)addGloss(x,1.65,-46.5,.7,3.3,.85,0x3e587d);
  }

  // Front security wall facing Main Highway (z = 32.5)
  // Left of Inter-Zone Road: encloses E-navbat Parkovka (x = -164 .. -30)
  addMatte(-97,1.3,32.5,134,2.6,.45,0x273b59);
  addGlow(-97,2.65,32.5,134,.12,.5,0x00e5ff);
  // Right of Inter-Zone Road: encloses Free Zone (x = -14 .. +90) & Chegara (with exit opening at 122)
  addMatte(50,1.3,32.5,128,2.6,.45,0x273b59);
  addGlow(50,2.65,32.5,128,.12,.5,0xb388ff);
  addMatte(140,1.3,32.5,24,2.6,.45,0x273b59);

  // WALL A (x = -30): E-navbat Parkovka East Wall facing the Inter-Zone Road
  // North segment (z = -46.5 to -12.5, length 34, center -29.5)
  addMatte(-30,1.5,-29.5,.55,3.0,34,0x2b4468);
  addGlow(-30,3.05,-29.5,.65,.15,34,0x00e5ff);
  // South segment (z = +0.5 to +32.5, length 32, center +16.5)
  addMatte(-30,1.5,16.5,.55,3.0,32,0x2b4468);
  addGlow(-30,3.05,16.5,.65,.15,32,0x00e5ff);
  for(const z of [-42,-34,-26,-18,5,13,21,29])addGloss(-30,1.7,z,.85,3.4,.85,0x456691);

  // WALL B (x = -14): Free Zone West Wall facing the Inter-Zone Road
  // North segment (z = -46.5 to -12.5, length 34, center -29.5)
  addMatte(-14,1.5,-29.5,.55,3.0,34,0x2b4468);
  addGlow(-14,3.05,-29.5,.65,.15,34,0xb388ff);
  // South segment (z = +0.5 to +32.5, length 32, center +16.5)
  addMatte(-14,1.5,16.5,.55,3.0,32,0x2b4468);
  addGlow(-14,3.05,16.5,.65,.15,32,0xb388ff);
  for(const z of [-42,-34,-26,-18,5,13,21,29])addGloss(-14,1.7,z,.85,3.4,.85,0x456691);

  // WALL C (x = +90): Free Zone East Wall separating Free Zone & Chegara
  // North segment (z = -46.5 to -12.5, length 34, center -29.5)
  addMatte(90,1.5,-29.5,.55,3.0,34,0x2b4468);
  addGlow(90,3.05,-29.5,.65,.15,34,0xff6b8b);
  // South segment (z = +0.5 to +32.5, length 32, center +16.5)
  addMatte(90,1.5,16.5,.55,3.0,32,0x2b4468);
  addGlow(90,3.05,16.5,.65,.15,32,0xff6b8b);
  for(const z of [-42,-34,-26,-18,5,13,21,29])addGloss(90,1.7,z,.85,3.4,.85,0x456691);

  // Build the 3 Gate Portals:
  // - Gate at x = -30 (E-navbat Parkovka gate onto Inter-Zone Road)
  // - Gate at x = -14 (Free Zone / O'zbekiston darvozasi KPP2 from Inter-Zone Road)
  // - Gate at x = +90 (Afg'oniston darvozasi KPP1 between Free Zone & Chegara)
  const gateSpecs=[
    {x:-30,accent:0x00e5ff,boothSide:-1},
    {x:-14,accent:0xb388ff,boothSide:1},
    {x:90,accent:0xff6b8b,boothSide:-1}
  ];
  for(const g of gateSpecs){
    const gx=g.x,accent=g.accent,bs=g.boothSide;
    // Portal pillars flanking the gate passage (z = -11.6 and z = -0.4)
    addGloss(gx,3.8,-11.6,1.8,7.6,1.8,0x2c4263);
    addGloss(gx,3.8,-0.4,1.8,7.6,1.8,0x2c4263);
    // Overhead canopy arch
    addGloss(gx,7.8,-6.0,5.2,.8,14.4,0x1b2d48);
    addGlow(gx-2.62,7.8,-6.0,.12,.38,14.2,accent);
    addGlow(gx+2.62,7.8,-6.0,.12,.38,14.2,accent);
    addGlow(gx,7.36,-6.0,1.8,.08,11.0,0xe6f7ff);
    // Security cabin beside the gate
    addMatte(gx+bs*2.2,2.0,-10.5,3.4,4.0,2.8,0x1d304d);
    addGloss(gx+bs*2.2,4.15,-10.5,3.9,.35,3.3,0x314b70);
    addGlow(gx+bs*2.2,2.3,-9.05,2.6,1.25,.08,0x8ceeff);
    // Zebra crosswalk stripes in the gateway
    for(let sz=-9.0;sz<=-3.0;sz+=1.2){
      addGlow(gx-2.2,.16,sz,1.1,.04,.5,0xf0f7ff);
      addGlow(gx+2.2,.16,sz,1.1,.04,.5,0xf0f7ff);
    }
    // Yellow/black barrier housing pedestal at (gx, 0, -2.2)
    addGloss(gx,1.65,-2.2,1.0,3.3,1.0,0xe5a93c);
    addMatte(gx,.3,-2.2,1.3,.6,1.3,0x1b283d);
    for(const bz of [-3.2,-1.2])addGlow(gx+bs*1.0,.55,bz,.2,1.1,.2,0xffae00);
  }

  // =========================================================================
  // 4. TAROZI (WEIGHBRIDGE) INSIDE FREE ZONE (x = 14, z = 18)
  // =========================================================================
  addMatte(14,.07,18,32,.14,7.5,0x142238);
  addMatte(0,.06,6,6.0,.14,24,0x142238);
  addMatte(28,.06,6,6.0,.14,24,0x142238);
  addMatte(14,.24,18,18.5,.45,5.6,0x21324d);
  addGloss(14,.52,18,16.8,.18,4.4,0x5a708e);
  for(const sz of [15.5,20.5]){
    addGloss(14,.78,sz,17.0,.42,.32,0xf4b942);
    addGlow(14,1.02,sz,17.0,.1,.18,0xffe082);
  }
  addMatte(4.8,.18,18,2.2,.28,4.4,0x3c506b,0,0,0.11);
  addMatte(23.2,.18,18,2.2,.28,4.4,0x3c506b,0,0,-0.11);
  addMatte(14,1.9,23.5,4.8,3.8,3.4,0x1d304d);
  addGloss(14,3.95,23.5,5.4,.35,4.0,0x314b70);
  addGlow(14,2.3,21.75,3.4,1.2,.08,0xffe082);
  addGloss(21.5,2.4,22.2,.35,4.8,.35,0x3c5270);
  addGlow(21.5,4.2,22.2,2.4,1.1,.25,0x00e676);

  // =========================================================================
  // 5. TERMINAL BUILDINGS & WAREHOUSES ALONG NORTH PERIMETER (z = -40)
  // =========================================================================
  // A. E-navbat Parkovka Dispatch Building (x = -98, z = -40)
  addMatte(-98,2.8,-40,34,5.6,8.5,0x1d304b);
  addGloss(-98,5.8,-39.8,36.4,.55,10.4,0x284368);
  addGlow(-98,5.85,-34.5,36.5,.2,.15,0x00e5ff);
  for(let i=-2;i<=2;i++)addGlow(-98+i*6,3.4,-35.65,4.2,1.7,.1,0x8aefff);

  // B. Free Zone Customs Terminal & Logistics Warehouses (x = 20 & x = 62, z = -40)
  addMatte(20,3.1,-40,34,6.2,9.2,0x212952);
  addGloss(20,6.4,-39.8,36.4,.6,11.0,0x333c75);
  addGlow(20,6.45,-34.2,36.5,.22,.16,0xb388ff);
  for(let i=-2;i<=2;i++)addGlow(20+i*6,3.8,-35.3,4.2,1.9,.1,0x9be7ff);

  addMatte(62,2.7,-40,26,5.4,8.8,0x253554);
  addGloss(62,5.55,-40,27.6,.45,9.8,0x354b73);
  for(let i=-2;i<=2;i++){
    addMatte(62+i*4.5,1.6,-35.5,2.8,3.0,.15,0x121d30);
    addGlow(62+i*4.5,3.3,-35.5,2.8,.18,.18,0xb388ff);
  }
  // Stacked containers in Free Zone logistics corner (x = 72, z = 22)
  addMatte(72,1.3,22,6.5,2.5,2.6,0x0077b6);
  addMatte(72,3.8,22,6.5,2.5,2.6,0xe63946);
  addMatte(72,1.3,25.2,6.5,2.5,2.6,0x2a9d8f);
  addMatte(64.5,1.3,23.5,6.5,2.5,2.6,0xf4a261);

  // C. Chegara Complex (x = 122, z = -38)
  addMatte(122,2.9,-38,28,5.8,8.8,0x3b2230);
  addGloss(122,6.0,-37.8,30.4,.55,10.4,0x592e42);
  addGlow(122,6.05,-32.5,30.5,.2,.16,0xff7838);
  for(let i=-1;i<=1;i++)addGlow(122+i*7,3.5,-33.5,4.4,1.8,.1,0xffcfa8);

  // High-mast stadium floodlights
  for(let x=-154;x<=146;x+=34){
    if(Math.abs(x+22)>8){
      addGloss(x,4.5,-46.2,.35,9.0,.35,0x4d6584);
      addGlow(x,8.9,-45.4,1.3,.22,.8,0xfff3d1);
      addGloss(x,4.2,32.5,.35,8.4,.35,0x4d6584);
      addGlow(x,8.3,31.8,1.3,.22,.8,0xfff3d1);
    }
  }

  instance(box,matte,scene,{roughness:.8,metalness:.14});
  instance(box,glossy,scene,{roughness:.34,metalness:.56});
  instance(box,glow,scene,{roughness:.2,metalness:.1,emissive:0xffffff,emissiveIntensity:.48});
  instance(cone,foliage,scene,{roughness:.75,metalness:.05});

  // Ground-painted markings
  function groundLabel(text,sub,x,z,w,h,colorHex){
    const c=document.createElement("canvas");c.width=512;c.height=128;
    const ctx=c.getContext("2d");
    ctx.fillStyle="rgba(10, 20, 38, 0.55)";
    ctx.beginPath();ctx.roundRect(6,6,500,116,18);ctx.fill();
    ctx.strokeStyle=colorHex;ctx.lineWidth=3;ctx.stroke();
    ctx.fillStyle=colorHex;ctx.font="800 34px 'Segoe UI', sans-serif";
    ctx.textAlign="center";ctx.fillText(text,256,56);
    ctx.fillStyle="#c9dff8";ctx.font="600 20px 'Segoe UI', sans-serif";
    ctx.fillText(sub,256,92);
    const tex=new T.CanvasTexture(c);
    const plane=new T.PlaneGeometry(w,h);plane.rotateX(-Math.PI/2);
    const mesh=new T.Mesh(plane,new T.MeshBasicMaterial({map:tex,transparent:true,opacity:.88,depthWrite:false}));
    mesh.position.set(x,.21,z);scene.add(mesh);
  }
  groundLabel("E-NAVBAT PARKOVKA","1000+ O‘RINLI TASHQI KUTISH HUDUDI",-98,27.5,28,6.5,"#00e5ff");
  groundLabel("FREE ZONE","300+ O‘RINLI BOJXONA VA LOGISTIKA HUDUDI",44,27.5,28,6.5,"#b388ff");
  groundLabel("TAROZI","FREE ZONE TAROZI NUQTASI",14,13.0,16,4,"#ffd166");
  groundLabel("CHEGARA","CHIQISH NAZORAT HUDUDI",122,27.5,22,5.5,"#ff8533");

  // Gate status indicator beacons on all 3 gates: [-30 (Parking gate), -14 (Free Zone KPP2 gate), +90 (Ayritom KPP1 gate)]
  const gateLights=[-30,-14,90].map(x=>{
    const lamp=new T.Mesh(cyl,new T.MeshBasicMaterial({color:0x00e676}));
    lamp.position.set(x,3.45,-2.2);lamp.scale.set(.3,.25,.3);
    scene.add(lamp);return lamp;
  });

  // Compact Selection Halo Ring
  const ringGeo=new T.RingGeometry(1.05,1.42,32);ringGeo.rotateX(-Math.PI/2);
  const selectRing=new T.Mesh(ringGeo,new T.MeshBasicMaterial({color:0xffdd4d,side:T.DoubleSide,transparent:true,opacity:.92}));
  selectRing.visible=false;scene.add(selectRing);

  let lines=null,capKey="",trucks=[],parts=[],selected=null,mode="all",moves=[],raf=0,aim=null,signature="",gateStates=[];
  const reduced=matchMedia('(prefers-reduced-motion: reduce)').matches;
  let meshCapacity=0;
  let posesDirty=true, colorsDirty=true;
  const drawColor=new T.Color();
  const followDelta=new T.Vector3();

  // Articulated barrier arms at:
  // 0: E-navbat Parkovka gate (-30, z=-2.2)
  // 1: Free Zone / O'zbekiston darvozasi KPP2 (-14, z=-2.2)
  // 2: Afg'oniston darvozasi KPP1 (+90, z=-2.2)
  const gates=[-30,-14,90].map(x=>{
    const arm=new T.Group();arm.position.set(x,2.95,-2.2);
    const bar=new T.Mesh(box,new T.MeshStandardMaterial({color:0xf5f8ff,roughness:.35}));
    bar.position.z=-3.8;bar.scale.set(.22,.24,7.6);arm.add(bar);
    for(let s=-1;s>=-7;s-=2){
      const stripe=new T.Mesh(box,new T.MeshBasicMaterial({color:0xff3b30}));
      stripe.position.z=s;stripe.scale.set(.24,.26,1.0);arm.add(stripe);
    }
    scene.add(arm);return arm;
  });

  function gridDims(area,capacity){
    const cap=Math.max(1,capacity);
    if(area==="parking"){
      const cols=Math.max(20,Math.ceil(Math.sqrt(cap*2.5)));
      return {cols,rows:Math.max(4,Math.ceil(cap/cols)),w:124,d:50};
    }
    if(area==="inside"){
      const cols=Math.max(15,Math.ceil(Math.sqrt(cap*2.2)));
      return {cols,rows:Math.max(4,Math.ceil(cap/cols)),w:94,d:36};
    }
    const cols=Math.max(8,Math.ceil(Math.sqrt(cap*1.5)));
    return {cols,rows:Math.max(4,Math.ceil(cap/cols)),w:48,d:44};
  }

  function location(area,slot,capacity){
    if(!slot)return vec(areas[area],.22,22);
    const {cols,rows}=gridDims(area,capacity);
    const i=slot-1,col=i%cols,row=Math.floor(i/cols);
    const half=Math.ceil(rows/2);
    if(area==="parking"){
      // North bank (z = -32 to -12) and South bank (z = +1 to +24) across x = -159 .. -35 (width 124)
      const zPos=row<half
        ? -32+(row+.5)*(20/Math.max(1,half))
        : 1+((row-half)+.5)*(23/Math.max(1,rows-half));
      return vec(-159+(col+.5)*(124/cols),.22,zPos);
    }
    if(area==="inside"){
      // North bank (z = -32 to -12) and Central-East bank (z = +1 to +12) across x = -8 .. +86 (width 94)
      const zPos=row<half
        ? -32+(row+.5)*(20/Math.max(1,half))
        : 1+((row-half)+.5)*(11/Math.max(1,rows-half));
      return vec(-8+(col+.5)*(94/cols),.22,zPos);
    }
    return vec(96+(col+.5)*(48/cols),.22,-26+(row+.5)*(44/rows));
  }

  function truckScale(area,capacity){
    const {cols,rows,w,d}=gridDims(area,capacity);
    return Math.max(0.28,Math.min(0.46,(w/cols)*0.42,(d/rows)*0.22));
  }

  function rebuildLines(cfg,spots){
    const key=cfg.parking_capacity+"/"+cfg.inside_capacity+JSON.stringify(spots);if(key===capKey)return;capKey=key;
    if(lines){scene.remove(lines);lines.dispose();lines.material.dispose()}
    const marks=[];
    for(const a of ["parking","inside"]){
      const cap=cfg[a+"_capacity"],{cols,rows,w,d}=gridDims(a,cap);
      const lineColor=a==="parking"?0x336b99:0x594ca8;
      const cellW=w/cols,bayLen=Math.min(2.2,(d/rows)*0.68);
      for(let i=1;i<=cap;i++){
        const p=location(a,i,cap),special=spots.find(s=>s.area===a&&s.slot===i);
        marks.push([p.x-cellW*.46,.17,p.z,.05,.03,bayLen,lineColor]);
        if(special)marks.push([p.x,.19,p.z,cellW*.82,.04,bayLen*.9,special.state==='reserved'?0x00b4d8:0x6c757d]);
      }
    }
    lines=instance(box,marks,scene,{roughness:.5,metalness:.1});
  }

  function makeParts(count){
    if(count<=meshCapacity){for(const p of parts)p.mesh.count=count;return}
    for(const p of parts){scene.remove(p.mesh);p.mesh.dispose();p.mesh.material.dispose()}
    parts=[];meshCapacity=Math.max(64,2**Math.ceil(Math.log2(Math.max(1,count))));
    const specs=[
      [box,0,1.35,-.65,1.54,1.72,3.22,0xe8f0fa,.42,.22],
      [box,0,2.24,-.65,1.46,.08,3.14,null,.3,.3],
      [box,0,.52,-.15,1.16,.24,4.18,0x1d2738,.7,.3],
      [box,0,1.18,1.66,1.48,1.46,1.16,null,.32,.35],
      [box,0,2.04,1.52,1.38,.34,.82,null,.32,.35],
      [box,0,1.52,2.25,1.28,.46,.06,0x0a1828,.15,.85],
      [box,0,.85,2.25,.92,.52,.06,0x22324a,.4,.6],
      [box,-.82,1.5,2.05,.12,.38,.18,0x1c2838,.5,.4],
      [box,.82,1.5,2.05,.12,.38,.18,0x1c2838,.5,.4],
      [box,0,.58,2.26,1.3,.15,.08,0xfff2b8,.2,.1],
      [box,0,.58,-2.26,1.34,.14,.06,0xff3b30,.2,.1]
    ];
    for(const x of [-.72,.72])for(const z of [-1.68,-.88,1.66]){
      specs.push([wheel,x,.42,z,.43,.32,.43,0x141a22,.85,.1]);
      specs.push([wheel,x,.42,z,.23,.34,.23,0x9ab0cc,.35,.65]);
    }
    for(const [geo,x,y,z,w,h,d,color,rough,metal] of specs){
      const mesh=new T.InstancedMesh(geo,new T.MeshStandardMaterial({roughness:rough,metalness:metal}),meshCapacity);
      mesh.frustumCulled=false;mesh.count=count;scene.add(mesh);parts.push({mesh,x,y,z,w,h,d,color});
    }
  }

  function draw(){
    raf=0;if(document.hidden)return;
    const now=performance.now();let moving=false;
    // Map 2 logical gate states (gateData[0] = KPP2, gateData[1] = KPP1) to the 3 physical barriers [-30, -14, +90]
    const gateLift=[
      gateStates[0]?.state==='open'?1:0,
      gateStates[0]?.state==='open'?1:0,
      gateStates[1]?.state==='open'?1:0
    ];
    let positionsChanged=false;
    let selectedPos=null;
    for(let i=0;i<trucks.length;i++){
      const v=trucks[i],m=moves[i];let p=m.end;
      const animating=Boolean(m.path && now<m.finish);
      const updatePose=posesDirty || animating || m.wasMoving;
      if(!updatePose && !colorsDirty){
        if(v.id===selected && !v.removed)selectedPos=v.position||p;
        continue;
      }
      m.wasMoving=animating;
      positionsChanged ||= updatePose;
      let angle=v.angle||0;
      if(animating){
        const phase=Math.max(0,1-(m.finish-now)/3200),smooth=phase*phase*(3-2*phase);
        p=m.path.getPoint(smooth);const tangent=m.path.getTangent(smooth);angle=Math.atan2(tangent.x,tangent.z);moving=true;
        gates.forEach((g,k)=>{
          const st=gateStates[k<2?0:1]?.state;
          if(p.distanceTo(g.position)<12&&['open','closed',undefined].includes(st))gateLift[k]=1;
        });
      }else if(v.removed)p=vec(0,-40,0);
      if(mode==="follow" && v.id===selected && v.position && animating){
        followDelta.copy(p).sub(v.position);
        controls.target.add(followDelta);camera.position.add(followDelta);
        if(aim){aim.target.add(followDelta);aim.pos.add(followDelta)}
      }
      v.position=p;v.angle=angle;
      if(v.id===selected && !v.removed)selectedPos=p;
      for(const part of parts){
        if(colorsDirty)part.mesh.setColorAt(i,drawColor.setHex(part.color ?? (v.id===selected?0xffdd4d:v.queue_state==='called'?0xffffff:colors[v.flow])));
        if(!updatePose)continue;
        const scale=v.scale||0.38;
        tmp.position.set(p.x+(part.x*Math.cos(angle)+part.z*Math.sin(angle))*scale,p.y+part.y*scale,p.z+(-part.x*Math.sin(angle)+part.z*Math.cos(angle))*scale);
        tmp.rotation.set(0,angle,0);tmp.scale.set(part.w*scale,part.h*scale,part.d*scale);tmp.updateMatrix();
        part.mesh.setMatrixAt(i,tmp.matrix);
      }
    }
    if(selectedPos){
      selectRing.visible=true;
      selectRing.position.set(selectedPos.x,.22,selectedPos.z);
    }else selectRing.visible=false;

    for(const part of parts){
      if(positionsChanged)part.mesh.instanceMatrix.needsUpdate=true;
      if(colorsDirty && part.mesh.instanceColor)part.mesh.instanceColor.needsUpdate=true;
    }
    posesDirty=false;colorsDirty=false;
    gates.forEach((g,i)=>{
      const target=1.35*(gateLift[i]||0);
      g.rotation.x=reduced?target:T.MathUtils.lerp(g.rotation.x,target,.14);
      if(Math.abs(g.rotation.x-target)>.01)moving=true;
      const st=gateStates[i<2?0:1]?.state;
      gateLights[i]?.material.color.setHex(gateLift[i]?0x00e676:st==='fault'||st==='locked'?0xff3b30:0xffab00);
    });
    if(aim){camera.position.lerp(aim.pos,.16);controls.target.lerp(aim.target,.16);
      if(camera.position.distanceTo(aim.pos)<.15)aim=null;else moving=true}
    if(controls.update())moving=true;
    renderer.render(scene,camera);
    for(let i=0;i<anchors.length;i++)projected[i].copy(anchors[i]).project(camera);
    connectLabels(projected);
    if(moving)invalidate();
  }
  function invalidate(){if(!raf&&!document.hidden)raf=requestAnimationFrame(draw)}
  function view(key){
    mode=key;let [x,y,z,d]=views[key]||views.all;
    const chosen=trucks.find(v=>v.id===selected);
    if(key==="follow"&&chosen){({x,y,z}=chosen.position||moves[trucks.indexOf(chosen)].end);d=18}
    if(key==='all')d=Math.max(262,180/Math.max(.6,camera.aspect)/Math.tan(T.MathUtils.degToRad(camera.fov/2)));
    aim={target:vec(x,y,z),pos:key==="top"?vec(x,y+d,z+.1):vec(x+d*.26,y+d*.52,z+d*.66)};
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
    trucks=[...rows.map(v=>({
      ...v,
      scale:truckScale(v.area,cfg[v.area+'_capacity'])
    })),...departed.map(v=>({...v,removed:true}))];
    moves=trucks.map(v=>{
      const old=previous.get(v.id);
      const exitX={ayritom:124,kpp2:-22,parkovka_tashqi:124}[exitPoints.get(v.id)];
      const end=v.removed?vec(exitX,.22,exitPoints.get(v.id)==="kpp2"?43:(v.flow==="CH"?43:-6)):location(v.area,v.slot,cfg[v.area+"_capacity"]);
      if(!v.removed&&v.area==="parking"&&v.parking_departed_at){
        // In-transit trucks waiting along the Inter-Zone Road (x = -22) right between Parking Gate (-30) and Free Zone Gate (-14)
        end.set(-24.5+(v.slot||1)%2*5.0,.22,-4+Math.floor(((v.slot||1)-1)/2)%8*3.2);
      }
      let path=null;
      if(old?.position&&(old.area!==v.area||old.slot!==v.slot||old.parking_departed_at!==v.parking_departed_at||v.removed)&&!reduced){
        const via=[old.position,vec(old.position.x,.22,-6)];
        if(v.removed && v.flow==="CH"){
          // Exiting Parkovka via Parkovka Gate (-30,-6) -> Inter-Zone Road (x=-22) -> Main Highway (z=43) -> Chegara (124,43)
          via.push(vec(-30,.22,-6),vec(-22,.22,-6),vec(-22,.22,43),end);
        }else if(v.removed && v.flow!=="CH"){
          if(exitPoints.get(v.id)==="ayritom"){
            // Exiting Free Zone via Tarozi (14,18) -> Afg'oniston darvozasi (90,-6) -> Chegara (124,-6)
            via.push(vec(14,.22,18),vec(28,.22,-6),vec(90,.22,-6),end);
          }else{
            // Exiting via Free Zone Gate (-14,-6) -> Inter-Zone Road (-22) -> Main Highway
            via.push(vec(-14,.22,-6),vec(-22,.22,-6),end);
          }
        }else if(!v.removed && old.area==="parking"&&v.area==="inside"){
          // Crossing from Parkovka through Parkovka Gate (-30,-6), across Inter-Zone Road (-22,-6), through Free Zone Gate (-14,-6) into Free Zone
          via.push(vec(-30,.22,-6),vec(-22,.22,-6),vec(-14,.22,-6),vec(end.x,.22,-6),end);
        }else{
          via.push(vec(end.x,.22,-6),end);
        }
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
  camera.position.set(68,142,194);controls.target.set(-4,0,-2);
  document.addEventListener("visibilitychange",invalidate);
  function restore(viewpoint){
    const valid=v=>Array.isArray(v)&&v.length===3&&v.every(n=>Number.isFinite(n)&&Math.abs(n)<1000);
    if(!valid(viewpoint?.position)||!valid(viewpoint?.target))return false;
    const position=vec(...viewpoint.position),target=vec(...viewpoint.target);
    if(position.distanceTo(target)<2 || position.distanceTo(target)>900)return false;
    mode="saved";aim=null;camera.position.copy(position);controls.target.copy(target);
    controls.update();invalidate();return true;
  }
  return {update,view,restore,
    capture(){return {position:camera.position.toArray(),target:controls.target.toArray()}},
    quality(ratio){renderer.setPixelRatio(Math.min(devicePixelRatio,ratio));renderer.setSize(canvas.clientWidth,canvas.clientHeight,false);invalidate()},
    select(id){selected=id;colorsDirty=true;if(mode==="follow")view(mode);invalidate()}
  };
}
