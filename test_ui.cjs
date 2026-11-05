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
pages["dashboard"]=env.get_template("zone.html").render(**(base|dict(mode="dashboard",user=dict(username="techadmin",role="techadmin"),stats=dict(border=12,zone=8,transfer=3,inside=24),summary=[],rows=[],active=[],pg=1,pages=1,start=date.today(),end=date.today(),total=0,selected="")))
pages["login"]=env.get_template("login.html").render(error=None,username="")
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
    const page = await browser.newPage({viewport:{width:1440,height:1000}});
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
    for(const name of ["login","kpp2","parkovka_tashqi","dashboard","confirm","admin","kiosk"]){
      await page.goto("http://preview.local/"+name);
      await page.screenshot({path:path.join(output,name+".png"),fullPage:true,animations:"disabled"});
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
