// Offline UI checks: renders templates, intercepts every browser request.
// Usage: node test_ui.cjs (PYTHON, JINJA_PATH, PLAYWRIGHT_PATH, CHROME_PATH optional)
const fs = require("fs"), path = require("path"), os = require("os");
const {spawnSync} = require("child_process");
const {chromium} = require(process.env.PLAYWRIGHT_PATH || "playwright");
const script = `
import sys,json,os
sys.path.insert(0,os.environ.get("JINJA_PATH",""))
from jinja2 import Environment,FileSystemLoader,select_autoescape
from datetime import datetime,date,timezone
from types import SimpleNamespace
env=Environment(loader=FileSystemLoader("templates"),autoescape=select_autoescape())
env.filters["som"]=lambda n:format(int(n),",").replace(","," ")
env.filters["zone_time"]=lambda d:d.strftime("%d.%m.%Y %H:%M")
env.globals.update(csrf_token=lambda:"test",url_for=lambda endpoint,**kw:("/static/"+kw["filename"] if endpoint=="static" else "/"+endpoint))
labels={"kpp2":"KPP2 · O‘zbekiston","parkovka_tashqi":"Tashqi parkovka","ayritom":"Ayritom","border":"Ayritom navbati","zone":"Free Zone","inside":"Hudud ichida","in":"Kirish","out":"Chiqish","scale":"Tarozi"}
base=dict(user=dict(username="test",role="kpp2",id=1),labels=labels,points=labels,queue=[],recent=[],csrf_token=lambda:"test",request=SimpleNamespace(args={}),company="AIRITOM",local_printer_agent=False,default_tab="tech")
pages={}
for role in ("kpp2","parkovka_tashqi","ayritom"):
    pages[role]=env.get_template("zone.html").render(**(base|dict(mode="station",user=dict(username=role,role=role,id=1))))
pages["dashboard"]=env.get_template("zone.html").render(**(base|dict(mode="dashboard",user=dict(username="techadmin",role="techadmin"),stats=dict(border=12,zone=8,transfer=3,inside=24),summary=[dict(point="kpp2",operations=1,total=30000,arrivals=0,departures=1,cash=30000,card=0,bank=0)],trend=[dict(day=date.today(),revenue=30000)],rows=[dict(ts=datetime.now(),point="kpp2",plate="01A777BA",worker="test",action="out",amount=30000,ref="AFZ-1")],active=[dict(id=1,plate="01A777BA",purpose="zone",location="inside",started=datetime.now())],pg=1,pages=1,start=date.today(),end=date.today(),total=30000,selected="")))
pages["login"]=env.get_template("login.html").render(error=None,username="")
import qrcode,base64,io
fields=[("Chek","AFZ-1"),("Navbat / tashrif","1"),("Avtomobil","BLH123"),("Maqsad","Free Zone"),("Nuqta","Tashqi parkovka"),("Amal","Kirish"),("Holat","Parkovkada"),("Kirish vaqti","05.11.2026 08:18"),("Chek vaqti","05.11.2026 08:18"),("Kun","1"),("Kunlik tarif","30 000"),("Oldin to‘langan","0"),("Hozir olindi","30 000 so‘m"),("Jami to‘langan","30 000"),("To‘lov turi","Naqd"),("Xodim","parkovka")]
buffer=io.BytesIO()
qrcode.make("Airitom Logistics Center MCHJ"+chr(10)+chr(10).join(k+": "+v for k,v in fields)).save(buffer,format="PNG")
pages["receipt"]=env.get_template("zone.html").render(**(base|dict(mode="receipt",receipt=dict(id=1,plate="BLH123",point="parkovka_tashqi",action="in",amount=30000),fields=fields,qr=base64.b64encode(buffer.getvalue()).decode())))
pages["confirm"]=env.get_template("zone.html").render(**(base|dict(mode="confirm",q=dict(point="kpp2",action="out",plate="01 A 777 BA",purpose="zone",location="closed",visit=42,days=2,rate=30000,paid=30000,amount=30000),token="test")))
pages["admin"]=env.get_template("admin.html").render(**(base|dict(user=dict(username="techadmin",role="techadmin",id=1))))
pages["kiosk"]=env.get_template("kiosk.html").render(**base)
print(json.dumps(pages))
`;
const rendered = spawnSync(process.env.PYTHON || "python", ["-c", script], {cwd:__dirname, encoding:"utf8"});
if(rendered.status !== 0) throw new Error(rendered.stderr);
const pages = JSON.parse(rendered.stdout);
const output = fs.mkdtempSync(path.join(os.tmpdir(),"airitom-ui-"));
function assert(value, message){if(!value)throw new Error(message);}
(async()=>{
  const browser = await chromium.launch({headless:true,...(process.env.CHROME_PATH?{executablePath:process.env.CHROME_PATH}:{})});
  try {
    const page = await browser.newPage({viewport:{width:1440,height:900}});
    const errors = [];
    page.on("pageerror",error=>errors.push(error.message));
    await page.route("**/*", async route=>{
      const url=new URL(route.request().url());
      if(url.pathname.startsWith("/static/")){
        const file=path.join(__dirname,url.pathname.slice(1));
        return route.fulfill({body:fs.readFileSync(file),contentType:file.endsWith(".css")?"text/css":file.endsWith(".js")?"application/javascript":file.endsWith(".jpg")?"image/jpeg":"image/x-icon"});
      }
      if(pages[url.pathname.slice(1)])return route.fulfill({body:pages[url.pathname.slice(1)],contentType:"text/html"});
      if(url.pathname.startsWith("/api/"))return route.fulfill({status:503,contentType:"application/json",body:'{"success":false,"message":"Offline preview"}'});
      return route.abort();
    });
    for(const name of ["login","kpp2","parkovka_tashqi","dashboard","receipt","confirm","admin","kiosk"]){
      await page.goto("http://preview.local/"+name);
      await page.screenshot({path:path.join(output,name+".png"),fullPage:true,animations:"disabled"});
      if(name==="dashboard"){
        await page.locator(".plot-node").first().focus();
        await page.keyboard.press("Enter");
        assert(await page.locator("#place-detail").isVisible(),"Keyboard chart selection");
        await page.keyboard.press("Escape");
        await page.getByRole("button",{name:"2D ko‘rinish"}).click();
        assert(await page.locator(".plot-item").first().isVisible(),"Flat view available");
        await page.locator(".plot-item").first().click();
        await page.getByRole("button",{name:"Yopish"}).click();
        assert(!(await page.locator("#place-detail").isVisible()),"Close chart detail");
        for(const [width,height] of [[1440,900],[1280,800],[1024,768],[768,1024],[390,844]]){
          await page.setViewportSize({width,height});
          await page.reload();
          assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),"Dashboard overflow at "+width);
          if(width===390){
            assert(await page.locator(".plot-item").first().isVisible(),"Phone opens in 2D");
            await page.locator(".rail-toggle").click();
            assert(await page.locator("#section-links").isVisible(),"Mobile navigation opens");
            await page.keyboard.press("Escape");
            assert(!(await page.locator("#section-links").isVisible()),"Escape closes mobile navigation");
          }
        }
        assert(await page.locator(".parking-card .place-count strong").textContent()==="23","Outside includes KPP2 transfers");
        assert(await page.locator(".inside-card .place-count strong").textContent()==="24","Inside counts confirmed arrivals");
      }
      if(name==="receipt"){
        await page.emulateMedia({media:"print"});
        const qr=await page.locator(".ticket .qr").boundingBox();
        assert(qr && Math.abs(qr.width-34*96/25.4)<1,"Printed QR must be 34 mm");
        assert(await page.locator(".ticket-actions").isHidden(),"Printed ticket hides controls");
        await page.locator(".ticket").screenshot({path:path.join(output,"receipt-print.png")});
        await page.emulateMedia({media:"screen"});
      }
      if(["kpp2","parkovka_tashqi"].includes(name)){
        assert(await page.locator("#vehicle-step").isHidden(),"Plate step must start hidden");
        await page.locator('[data-action="in"]').click();
        assert(await page.locator("#vehicle-plate").evaluate(el=>el===document.activeElement),"Plate field focus");
        await page.locator("#vehicle-plate").fill("01a777ba");
        assert(await page.locator("#vehicle-plate").inputValue()==="01A777BA","Uppercase plate");
        await page.screenshot({path:path.join(output,name+"-plate.png"),fullPage:true,animations:"disabled"});
        await page.keyboard.press("Escape");
        await page.locator('[data-action="out"]').click();
        assert(await page.locator("#movement-action").inputValue()==="out","Exit action selected");
        if(name==="parkovka_tashqi")assert(await page.locator("#purpose-field input").first().isDisabled(),"Exit must not require purpose");
      }
      if(name==="login"){
        await page.locator("#toggle-password").click();
        assert(await page.locator("#access-password").getAttribute("type")==="text","Show password");
      }
      await page.setViewportSize({width:390,height:844});
      assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),name+" mobile overflow");
      await page.screenshot({path:path.join(output,name+"-mobile.png"),fullPage:true,animations:"disabled"});
      await page.setViewportSize({width:1440,height:1000});
    }
    assert(!errors.length,"Browser errors: "+errors.join("; "));
    console.log("PASS: template rendering, wizard, focus, purpose, password, mobile overflow; screenshots: "+output);
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
