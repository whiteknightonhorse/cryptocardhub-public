import { chromium } from "playwright";
import { spawnSync } from "node:child_process";
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
// ---- chunk words -> caption phrases (~3 words, break on >0.45s gap) ----
function chunk(words){
  const phr=[]; let cur=[];
  for(let i=0;i<words.length;i++){
    const w=words[i];
    if(cur.length){ const prev=cur[cur.length-1]; const gap=w.start-prev.end;
      if(cur.length>=3 || gap>0.45){ phr.push(cur); cur=[]; } }
    cur.push(w);
  }
  if(cur.length) phr.push(cur);
  return phr.map(ws=>({ words:ws, text:ws.map(x=>x.word).join(" "),
    start:ws[0].start, end:ws[ws.length-1].end }));
}
const sArg=arg("style","auto");
const style=sArg==="auto"?rotate(".rotation_vid.json"):(parseInt(sArg,10)||1);
const wordsFile=arg("words",""); const fps=parseInt(arg("fps","30"),10);
const outdir=arg("outdir","/tmp/defy_ovl"); const ctaSeconds=parseFloat(arg("cta-seconds","6"));
const captionsOnly=process.argv.includes("--captions-only"); // FTC+CTA stay in render.py
const FTC=!captionsOnly;
const videoIn=arg("video",""); const videoOut=arg("out","");  // optional composite onto a video
// duration: explicit --duration, else probe the --video, else 40
let dur=parseFloat(arg("duration","0"));
if((!dur||isNaN(dur)) && videoIn && fs.existsSync(videoIn)){
  const pr=spawnSync("ffprobe",["-v","error","-show_entries","format=duration","-of","default=nw=1:nk=1",videoIn],{encoding:"utf8"});
  dur=parseFloat((pr.stdout||"").trim());
}
if(!dur||isNaN(dur)) dur=40;

const words=JSON.parse(fs.readFileSync(wordsFile,"utf8"));
const phrases=chunk(words);
fs.rmSync(outdir,{recursive:true,force:true}); fs.mkdirSync(outdir,{recursive:true});
const statesDir=path.join(outdir,"states"); fs.mkdirSync(statesDir,{recursive:true});
const ctaStart=Math.max(0,dur-ctaSeconds);

function stateAt(t){
  if(!captionsOnly && t>=ctaStart) return {key:"cta", spec:{index:style,phase:"cta",showFTC:FTC}};
  let ph=null; for(const p of phrases){ if(t>=p.start && t<p.end+0.12){ ph=p; break; } }
  if(!ph){ for(const p of phrases){ if(Math.abs(t-p.start)<0.2){ ph=p; break; } } }
  if(!ph) return {key:"blank", spec:{index:style,hook:"",phase:"caption",showFTC:FTC}};
  let ai=null; for(let i=0;i<ph.words.length;i++){ if(t>=ph.words[i].start && t<ph.words[i].end){ ai=i; break; } }
  return {key:`p${ph.start.toFixed(2)}_${ai}`, spec:{index:style,hook:ph.text,activeIndex:ai,phase:"caption",showFTC:FTC}};
}

const browser=await chromium.launch({args:["--no-sandbox","--disable-dev-shm-usage","--force-color-profile=srgb","--hide-scrollbars"]});
const page=await browser.newPage({viewport:{width:1080,height:1920},deviceScaleFactor:1});
await page.goto("file://"+path.join(DIR,"overlay_harness.html"),{waitUntil:"networkidle",timeout:60000});
const cache={};
async function renderState(key,spec){
  if(cache[key]) return cache[key];
  await page.evaluate(o=>window.__renderOverlay(o),spec);
  await page.waitForFunction("window.__ready===true",{timeout:15000});
  await page.evaluate(()=>{window.__ready=false;});
  try{await page.evaluate(()=>document.fonts.ready);}catch(_){}
  const p=path.join(statesDir,key.replace(/[^a-z0-9]/gi,"_")+".png");
  const stage=await page.$("#stage");
  await stage.screenshot({path:p,type:"png",omitBackground:true});
  cache[key]=p; return p;
}
const total=Math.ceil(dur*fps);
for(let i=0;i<total;i++){
  const t=i/fps; const st=stateAt(t);
  const png=await renderState(st.key,st.spec);
  const fp=path.join(outdir,"f"+String(i).padStart(5,"0")+".png");
  try{fs.symlinkSync(png,fp);}catch(_){fs.copyFileSync(png,fp);}
}
await browser.close();

// ---- optional: composite the overlay sequence onto a video (keeps its audio) ----
let composited=false;
if(videoIn && videoOut && fs.existsSync(videoIn)){
  const tmp = videoOut + ".ovltmp.mp4";
  const r = spawnSync("ffmpeg",["-y","-hide_banner","-loglevel","error",
    "-i",videoIn,"-framerate",String(fps),"-i",path.join(outdir,"f%05d.png"),
    "-filter_complex","[0:v][1:v]overlay=format=auto:shortest=1[v]",
    "-map","[v]","-map","0:a?","-c:a","copy","-c:v","libx264","-preset","medium","-crf","19","-pix_fmt","yuv420p",
    tmp],{stdio:["ignore","inherit","inherit"]});
  if(r.status===0 && fs.existsSync(tmp)){ fs.renameSync(tmp, videoOut); composited=true; }
}
console.log(JSON.stringify({ok:true,style,frames:total,states:Object.keys(cache).length,phrases:phrases.length,outdir,composited}));
