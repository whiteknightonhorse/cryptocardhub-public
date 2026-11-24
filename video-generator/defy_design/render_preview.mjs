import { chromium } from "playwright";
import fs from "node:fs"; import path from "node:path";
const DIR = path.dirname(new URL(import.meta.url).pathname);
function arg(n,d){const i=process.argv.indexOf("--"+n);return i>0?process.argv[i+1]:d;}
function rotate(file){
  const f=path.join(DIR,file); let used=[];
  try{used=(JSON.parse(fs.readFileSync(f,"utf8")).used)||[];}catch(_){}
  const all=Array.from({length:50},(_,i)=>i+1); let pool=all.filter(n=>!used.includes(n));
  if(!pool.length){used=[];pool=all;}
  const pick=pool[Math.floor(Math.random()*pool.length)]; used.push(pick);
  try{fs.writeFileSync(f,JSON.stringify({used}));}catch(_){}
  return pick;
}
const sArg=arg("style","auto");
const idx=sArg==="auto"?rotate(".rotation_prev.json"):(parseInt(sArg,10)||1);
const hook=arg("hook","SPEND CRYPTO ANYWHERE"), stat=arg("stat",""), out=arg("out","/tmp/defy_prev.png");
const browser=await chromium.launch({args:["--no-sandbox","--disable-dev-shm-usage","--force-color-profile=srgb","--hide-scrollbars"]});
const page=await browser.newPage({viewport:{width:1080,height:1920},deviceScaleFactor:1});
await page.goto("file://"+path.join(DIR,"prev_harness.html"),{waitUntil:"networkidle",timeout:60000});
await page.evaluate(o=>window.__renderPreview(o),{index:idx,hook,stat});
await page.waitForFunction("window.__ready===true",{timeout:15000});
try{await page.evaluate(()=>document.fonts.ready);}catch(_){}
await page.waitForTimeout(500);
const stage=await page.$("#stage");
await stage.screenshot({path:out,type:"png"});
await browser.close();
console.log(JSON.stringify({ok:true,out,style:idx,hook,bytes:fs.statSync(out).size}));
