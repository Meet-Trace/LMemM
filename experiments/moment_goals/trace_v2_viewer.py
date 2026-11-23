#!/usr/bin/env python3
"""Local viewer for Trace V2 observations: screenshot and Observation side by side.

Usage (from a checkout that contains trace_v2, with the venv python):
    python trace_v2_viewer.py --data-dir /tmp/trace-try-data [--port 8765] [--repo /path/to/checkout]

Serves http://127.0.0.1:PORT only (never the network). Opens the store like any other reader and never writes
evidence. Press "Live" in the page to follow a runner that is still capturing.
"""

import argparse
import json
import os
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Trace V2 viewer</title>
<style>
:root{--bg:#f6f7f9;--panel:#fff;--ink:#14161a;--mute:#6b7280;--line:#e3e6eb;--accent:#2563eb;--ok:#15803d;--warn:#b45309;--bad:#b91c1c;--chk1:#e9ecef;--chk2:#f8f9fa}
@media (prefers-color-scheme:dark){:root{--bg:#0f1115;--panel:#171a20;--ink:#e8eaee;--mute:#9aa3b2;--line:#2a2f38;--accent:#6ea0ff;--ok:#4ade80;--warn:#fbbf24;--bad:#f87171;--chk1:#232830;--chk2:#1b1f26}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
header{display:flex;gap:16px;align-items:center;padding:10px 16px;border-bottom:1px solid var(--line);background:var(--panel);position:sticky;top:0;z-index:2;flex-wrap:wrap}
header h1{font-size:15px;margin:0}header .meta{color:var(--mute)}header label{display:flex;gap:6px;align-items:center;cursor:pointer}
main{display:grid;grid-template-columns:260px minmax(0,1fr) minmax(0,1fr);gap:12px;padding:12px 16px;height:calc(100vh - 54px)}
@media (max-width:1100px){main{grid-template-columns:1fr;height:auto}}
.col{background:var(--panel);border:1px solid var(--line);border-radius:10px;overflow:auto;min-height:0}
.list .row{padding:8px 10px;border-bottom:1px solid var(--line);cursor:pointer}
.list .row:hover{background:var(--bg)}.list .row.sel{background:color-mix(in srgb,var(--accent) 14%,transparent)}
.row .t{font-variant-numeric:tabular-nums;font-weight:600}.row .a{color:var(--mute);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.chip{display:inline-block;padding:0 6px;border-radius:9px;font-size:11px;margin-right:4px;border:1px solid var(--line);color:var(--mute)}
.chip.ok{color:var(--ok)}.chip.warn{color:var(--warn)}.chip.bad{color:var(--bad)}
.shot{padding:10px;display:flex;flex-direction:column;gap:8px}
.frame{background-image:linear-gradient(45deg,var(--chk1) 25%,transparent 25%,transparent 75%,var(--chk1) 75%),linear-gradient(45deg,var(--chk1) 25%,var(--chk2) 25%,var(--chk2) 75%,var(--chk1) 75%);background-size:20px 20px;background-position:0 0,10px 10px;border:1px solid var(--line);border-radius:6px;overflow:hidden}
.frame img{display:block;width:100%;height:auto}
.none{padding:40px 16px;text-align:center;color:var(--mute)}
.obs{padding:10px 12px}.sum{display:grid;grid-template-columns:auto 1fr;gap:2px 12px;margin-bottom:10px}.sum b{color:var(--mute);font-weight:500}
pre{margin:0;white-space:pre-wrap;word-break:break-word;font:12px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace}
.hint{color:var(--mute);font-size:12px}
.seg{display:flex;border:1px solid var(--line);border-radius:7px;overflow:hidden}.seg button{border:0;background:transparent;color:var(--ink);padding:3px 10px;cursor:pointer;font:inherit}.seg button.on{background:var(--accent);color:#fff}
.goal{font-size:12px;color:var(--ink);margin-top:2px;white-space:normal}.lab{display:flex;gap:6px;align-items:center;margin:6px 0}.lab input{padding:3px 6px;border:1px solid var(--line);border-radius:6px;background:var(--bg);color:var(--ink);width:160px}
.thumbs{display:grid;grid-template-columns:repeat(auto-fill,minmax(120px,1fr));gap:6px}
.th{border:2px solid transparent;border-radius:6px;cursor:pointer;background:var(--bg);overflow:hidden;position:relative}.th.sel{border-color:var(--accent)}.th.sec{opacity:.55}
.th img{display:block;width:100%;aspect-ratio:16/10;object-fit:cover;background:var(--chk1)}.th .c{font-size:10px;padding:2px 4px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;color:var(--mute)}
.th .ph{aspect-ratio:16/10;display:flex;align-items:center;justify-content:center;font-size:10px;color:var(--mute)}
details{margin:6px 0}summary{cursor:pointer;color:var(--mute)}
.fw{position:relative}.fw svg{position:absolute;inset:0;width:100%;height:100%;pointer-events:none}
.fw svg *{vector-effect:non-scaling-stroke;fill:none}
.tg{display:flex;gap:10px;flex-wrap:wrap;align-items:center;font-size:12px}.tg label{display:flex;gap:4px;align-items:center;cursor:pointer}
.sw{width:10px;height:10px;display:inline-block;border-radius:2px}
</style></head><body>
<header><h1>Trace V2 viewer</h1><div class="seg"><button id="vobs" class="on">Observations</button><button id="vmom">Moments</button><button id="vact">Activities</button></div><span class="meta" id="meta"></span>
<label><input type="checkbox" id="live"> Live</label>
<label><input type="checkbox" id="raw"> Show raw OCR / accessibility</label>
<label><input type="checkbox" id="rawst"> Show structure JSON</label>
<span class="hint">&uarr; &darr; or j / k to step</span></header>
<main>
<div class="col list" id="list"></div>
<div class="col shot" id="shot"></div>
<div class="col obs" id="obs"></div>
</main>
<script>
const $=id=>document.getElementById(id);let items=[],sel=null,timer=null,mode='obs',msel=null,mdata=null,osel=null,asel=null,acts=[];
const el=(t,c,x)=>{const e=document.createElement(t);if(c)e.className=c;if(x!=null)e.textContent=x;return e};
const fmt=iso=>{const d=new Date(iso);return d.toLocaleTimeString([], {hour12:false})+'.'+String(d.getMilliseconds()).padStart(3,'0')};
function chip(text,cls){return el('span','chip '+(cls||''),text)}
function ocrCls(s){return s==='succeeded'?'ok':(s==='pending'?'warn':(s==='failed'?'bad':''))}
async function loadList(){
  const r=await fetch('/api/list');const d=await r.json();items=d.items;
  $('meta').textContent=d.data_dir+' • '+items.length+' observations';
  const list=$('list');list.textContent='';
  items.forEach(it=>{const row=el('div','row'+(it.id===sel?' sel':''));row.dataset.id=it.id;
    const t=el('div','t',fmt(it.timestamp));row.appendChild(t);
    row.appendChild(el('div','a',(it.app_name||it.app_bundle_id||'?')+(it.window_title?' — '+it.window_title:'')));
    const c=el('div');c.appendChild(chip(it.screenshot,it.screenshot==='stored'?'ok':'warn'));c.appendChild(chip('ocr '+it.ocr,ocrCls(it.ocr)));
    if(it.events)c.appendChild(chip(it.events+' ev'));row.appendChild(c);
    row.onclick=()=>select(it.id);list.appendChild(row)});
  if(!sel&&items.length)select(items[Math.floor(items.length/2)].id);else if(sel)mark();
}
function mark(){document.querySelectorAll('#list .row').forEach(r=>r.classList.toggle('sel',r.dataset.id===sel))}
async function select(id){sel=id;mark();const row=document.querySelector('#list .row.sel');if(row)row.scrollIntoView({block:'nearest'});
  const raw=$('raw').checked?1:0;
  const [o]=await Promise.all([fetch('/api/obs/'+id+'?raw='+raw).then(r=>r.json())]);
  if(sel!==id)return;
  showShot(id,o);showObs(o)}
function showShot(id,o,target){const s=target||$('shot');s.textContent='';
  const st=o.evidence&&o.evidence.screenshot||{};
  s.appendChild(el('div','hint','screenshot: '+(st.status||'?')+(st.width?' • '+st.width+'×'+st.height:'')+(st.byte_size?' • '+(st.byte_size/1024).toFixed(0)+' KB':'')));
  const tg=el('div','tg');s.appendChild(tg);
  const f=el('div','frame');const img=new Image();img.alt='screenshot';
  img.onerror=async()=>{f.remove();const r=await fetch('/img/'+id);s.appendChild(el('div','none','No screenshot: '+(r.headers.get('X-Reason')||'unavailable')))};
  const fw=el('div','fw');fw.appendChild(img);f.appendChild(fw);
  img.src='/img/'+id;s.appendChild(f);
  fetch('/api/structure/'+id+($('rawst').checked?'?all=1':'')).then(r=>r.json()).then(st=>{if(sel===id)overlay(st,fw,tg,st.width||st.w)})}
const LAYERS=[['text','#2563eb','Text',1],['regions','#16a34a','Regions',1],['elements','#dc2626','Elements',1],['lines','#c026d3','Lines',1]];
const on={text:true,regions:true,elements:true,lines:true};
function overlay(st,fw,tg,_w){
  if(!st.present){tg.appendChild(el('span','hint','structure: '+(st.reason||'none yet')));return}
  const raw=st.raw;if(!raw){tg.appendChild(el('span','hint','structure '+st.status+(st.error_code?' ('+st.error_code+')':'')));return}
  const [W,H]=raw.source.image_size;const ns='http://www.w3.org/2000/svg';
  const svg=document.createElementNS(ns,'svg');svg.setAttribute('viewBox','0 0 '+W+' '+H);svg.setAttribute('preserveAspectRatio','none');fw.appendChild(svg);
  const groups={};
  const draw=()=>{LAYERS.forEach(([k])=>groups[k].style.display=on[k]?'':'none')};
  LAYERS.forEach(([k,c,label])=>{const g=document.createElementNS(ns,'g');groups[k]=g;svg.appendChild(g);
    (raw[k]||[]).filter(i=>k!=='regions'||i.leaf).forEach(i=>{const r=document.createElementNS(ns,'rect');const b=i.bbox;
      r.setAttribute('x',b[0]);r.setAttribute('y',b[1]);r.setAttribute('width',b[2]);r.setAttribute('height',b[3]);
      r.setAttribute('stroke',c);r.setAttribute('stroke-width',k==='elements'?2:1);g.appendChild(r)});
    const lb=el('label');const cb=document.createElement('input');cb.type='checkbox';cb.checked=on[k];cb.onchange=()=>{on[k]=cb.checked;draw()};
    const sw=el('span','sw');sw.style.background=c;lb.append(cb,sw,document.createTextNode(label+' '+(k==='regions'?(raw[k]||[]).filter(i=>i.leaf).length:(raw[k]||[]).length)));tg.appendChild(lb)});
  draw();
  const o=raw.source.ocr;tg.appendChild(el('span','hint','structure rev '+st.revision+' • ocr '+(o?o.status+' r'+o.revision:'none')+' • '+(st.duration_ms!=null?st.duration_ms+' ms • ':'')+(st.raw_bytes/1024).toFixed(1)+' KB'));
  if(st.revisions>1)tg.appendChild(el('span','hint','('+st.revisions+' revisions)'))}
function showObs(o,target){const p=target||$('obs');if(!target)p.textContent='';const m=o.metadata||{},ev=m.extraction_status||{},c=o.context||{},app=c.application||{},w=c.window||{};
  const sum=el('div','sum');const add=(k,v)=>{sum.appendChild(el('b','',k));sum.appendChild(el('span','',v))};
  add('time',o.timestamp);add('app',(app.name||'?')+' ('+(app.bundle_id||'?')+')');add('window',w.title||'—');
  add('screenshot',(ev.screenshot||{}).status||'?');add('ocr',((ev.ocr||{}).status||'?')+((m.later_extractions&&(m.later_extractions.ocr||[]).length)?' • '+m.later_extractions.ocr.length+' earlier revision(s)':''));
  add('accessibility',(ev.accessibility||{}).status||'?');add('events',String((o.interaction_events||[]).length));
  const pv=m.privacy||{};add('privacy',pv.withheld?('withheld: '+pv.reason):(pv.screenshot_scope?'scope '+pv.screenshot_scope:'ok'));
  p.appendChild(sum);const oid=o.observation_id;
  if($('rawst').checked){fetch('/api/structure/'+oid).then(r=>r.json()).then(st=>{const pre=el('pre','',JSON.stringify(st.raw||st,null,1));p.insertBefore(pre,sum.nextSibling);p.insertBefore(el('div','hint','structure'),pre)})}
  p.appendChild(el('pre','',JSON.stringify(o,null,2)))}
document.addEventListener('keydown',e=>{if(e.target.tagName==='INPUT')return;const cur=mode==='mom'?msel:(mode==='act'?asel:sel),go=mode==='mom'?selectMoment:(mode==='act'?selectActivity:select);const i=items.findIndex(x=>x.id===cur);
  if((e.key==='ArrowDown'||e.key==='j')&&i<items.length-1){e.preventDefault();go(items[i+1].id)}
  if((e.key==='ArrowUp'||e.key==='k')&&i>0){e.preventDefault();go(items[i-1].id)}});
$('raw').onchange=()=>mode==='mom'?(osel&&selectMember(osel)):(sel&&select(sel));$('rawst').onchange=()=>mode==='mom'?(osel&&selectMember(osel)):(sel&&select(sel));
$('live').onchange=()=>{clearInterval(timer);if($('live').checked)timer=setInterval(async()=>{if(mode==='mom'){await loadMoments();return}if(mode==='act'){await loadActivities();return}await loadList();if(sel)select(sel)},3000)};
const tm=ns=>new Date(ns/1e6).toLocaleTimeString([], {hour12:false});
async function loadMoments(){
  const d=await fetch('/api/moments').then(r=>r.json());items=d.items;
  $('meta').textContent=d.data_dir+' • '+items.length+' moments';
  const list=$('list');list.textContent='';
  if(!items.length)list.appendChild(el('div','none','No Moments yet. Run: python -m trace_v2.moments --data-dir ...'));
  items.forEach(m=>{const row=el('div','row'+(m.id===msel?' sel':''));row.dataset.id=m.id;
    row.appendChild(el('div','t',tm(m.start_ns)+' – '+tm(m.end_ns)));
    row.appendChild(el('div','a',Object.keys(m.summary.apps||{}).map(a=>a.split('.').pop()).join(', ')||'?'));
    row.appendChild(el('div','a',(m.summary.top_tokens||[]).slice(0,5).join(' ')));
    if(m.goals){Object.entries(m.goals).forEach(([k,g])=>{const d=el('div','goal',k.split(':')[0]+': '+(g.goal||'(empty)'));row.appendChild(d)})}
    const c=el('div');c.appendChild(chip(m.state,m.state==='stable'?'ok':(m.state==='provisional'?'warn':'')));c.appendChild(chip(m.core+' obs'));
    if(m.text_group)c.appendChild(chip('text group '+m.text_group));if(m.label)c.appendChild(chip('your label: '+m.label,'ok'));
    if(m.secondary)c.appendChild(chip(m.secondary+' shared'));if(m.origin==='split')c.appendChild(chip('split'));row.appendChild(c);
    row.onclick=()=>selectMoment(m.id);list.appendChild(row)});
  if(!msel&&items.length)selectMoment(items[0].id);else if(msel)markM();
}
function markM(){document.querySelectorAll('#list .row').forEach(r=>r.classList.toggle('sel',r.dataset.id===msel))}
async function selectMoment(id){msel=id;markM();
  const d=await fetch('/api/moment/'+id).then(r=>r.json());if(msel!==id)return;mdata=d;
  const s=$('shot');s.textContent='';
  s.appendChild(el('div','hint',d.members.length+' observations in revision '+d.current.revision+' ('+d.current.state+') • click one to inspect'));
  const mi=items.find(x=>x.id===id);
  if(mi&&(mi.goals||mi.packet)){const gl=Object.entries(mi.goals||{});
    if(gl.length){gl.forEach(([k,g])=>{const pd=document.createElement('details');pd.open=true;pd.appendChild(el('summary','','Prompt sent to '+k));const pt=el('pre','',g.prompt_sent||mi.packet||'');pt.style.cssText='white-space:pre-wrap;max-height:420px;overflow:auto;font-size:11px';pd.appendChild(pt);s.appendChild(pd);
      const rd=document.createElement('details');rd.open=true;rd.appendChild(el('summary','','Response from '+k+(g.seconds?' ('+g.seconds+' s)':'')));const rp=el('pre','',g.raw||'(no response)');rp.style.cssText='white-space:pre-wrap;font-size:12px';rd.appendChild(rp);s.appendChild(rd)})}
    else{const pd=document.createElement('details');pd.open=true;pd.appendChild(el('summary','','Prompt (no model has answered yet)'));const pt=el('pre','',mi.packet||'');pt.style.cssText='white-space:pre-wrap;max-height:420px;overflow:auto;font-size:11px';pd.appendChild(pt);s.appendChild(pd)}}
  if(mi&&(mi.goals||mi.packet)){const lab=el('div','lab');lab.appendChild(el('span','hint','Same task as other Moments? Give them the same label:'));const inp=document.createElement('input');inp.value=mi.label||'';inp.placeholder='e.g. trace landing page';
    const dl=document.createElement('datalist');dl.id='labs';Array.from(new Set(items.map(x=>x.label).filter(Boolean))).forEach(v=>{const o=document.createElement('option');o.value=v;dl.appendChild(o)});inp.setAttribute('list','labs');
    inp.onchange=async()=>{mi.label=inp.value.trim()||null;await fetch('/api/label',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({moment_id:id,label:inp.value})})};lab.append(inp,dl);s.appendChild(lab)}
  const grid=el('div','thumbs');d.members.forEach(m=>{const t=el('div','th'+(m.role==='secondary'?' sec':''));t.dataset.id=m.id;t.title=m.role+' • score '+m.score;
    if(m.screenshot==='stored'){const i=new Image();i.loading='lazy';i.src='/img/'+m.id;t.appendChild(i)}else t.appendChild(el('div','ph',m.screenshot));
    t.appendChild(el('div','c',tm(m.t)+' '+(m.app||'')));t.onclick=()=>selectMember(m.id);grid.appendChild(t)});
  s.appendChild(grid);s.appendChild(el('div','',''));const big=el('div','');big.id='big';s.appendChild(big);
  const p=$('obs');p.textContent='';
  const det=el('details');det.appendChild(el('summary','','Moment data'));const slim=JSON.parse(JSON.stringify(d));const prm=slim.current.params;delete slim.current.params;slim.members=slim.members.map(m=>m.role+' '+m.score+' '+m.id);det.appendChild(el('pre','',JSON.stringify(slim,null,1)));p.appendChild(det);const dp=el('details');dp.appendChild(el('summary','','Parameters used'));dp.appendChild(el('pre','',JSON.stringify(prm,null,1)));p.appendChild(dp);
  const ob=el('div','');ob.id='mobs';p.appendChild(ob);
  if(d.members.length)selectMember(osel&&d.members.some(m=>m.id===osel)?osel:d.members[0].id);
}
async function selectMember(id){osel=id;
  document.querySelectorAll('.th').forEach(t=>t.classList.toggle('sel',t.dataset.id===id));
  const o=await fetch('/api/obs/'+id+'?raw='+($('raw').checked?1:0)).then(r=>r.json());if(osel!==id)return;
  showShot(id,o,$('big'));
  const ob=$('mobs');if(!ob)return;ob.textContent='';showObs(o,ob)}

async function loadActivities(){
  const d=await fetch('/api/moments').then(r=>r.json());const ms=d.items.filter(m=>m.state!=='merged');
  const by={};const add=(key,name,source,m,why)=>{const a=(by[key]=by[key]||{id:key,name,source,moments:[]});a.moments.push(m);if(why&&!a.why)a.why=why};
  ms.forEach(m=>{if(m.label)add('label:'+m.label,m.label,'your label',m);
    Object.entries(m.goals||{}).forEach(([k,g])=>{if(g.group)add('meaning:'+k+':'+g.group,k.split(':')[0]+' group '+g.group,'meaning',m,g.group_reason)});
    if(m.text_group)add('text:'+m.text_group,'text group '+m.text_group,'raw text',m)});
  acts=Object.values(by).filter(a=>a.moments.length>=2).sort((a,b)=>a.source.localeCompare(b.source)||b.moments.length-a.moments.length);
  items=acts;$('meta').textContent=d.data_dir+' • '+acts.length+' activities (groups of 2+ moments)';
  const list=$('list');list.textContent='';
  if(!acts.length)list.appendChild(el('div','none','No activities yet. Label Moments with the same name, or run the goals experiment to get groups.'));
  acts.forEach(a=>{const row=el('div','row'+(a.id===asel?' sel':''));row.dataset.id=a.id;
    row.appendChild(el('div','t',a.name));const c=el('div');c.appendChild(chip(a.source,a.source==='your label'?'ok':''));c.appendChild(chip(a.moments.length+' moments'));row.appendChild(c);
    row.onclick=()=>selectActivity(a.id);list.appendChild(row)});
  if(!asel&&acts.length)selectActivity(acts[0].id);else if(asel)document.querySelectorAll('#list .row').forEach(r=>r.classList.toggle('sel',r.dataset.id===asel));
}
async function selectActivity(id){asel=id;document.querySelectorAll('#list .row').forEach(r=>r.classList.toggle('sel',r.dataset.id===id));
  const a=acts.find(x=>x.id===id);const s=$('shot');s.textContent='';$('obs').textContent='';if(!a)return;
  s.appendChild(el('div','hint',a.source+' • '+a.name+' • '+a.moments.length+' moments, click a Moment to open it'));
  if(a.why){const rb=el('div','');rb.style.cssText='margin:8px 0;padding:8px;border:1px solid var(--line);border-radius:6px';rb.appendChild(el('div','hint','Why these Moments are grouped'));a.why.forEach(t=>rb.appendChild(el('div','goal',t)));s.appendChild(rb)}
  a.moments.slice().sort((x,y)=>x.start_ns-y.start_ns).forEach(async m=>{const card=el('div','row');card.style.cursor='pointer';
    card.appendChild(el('div','t',tm(m.start_ns)+' – '+tm(m.end_ns)+' • '+m.core+' observations'));
    card.appendChild(el('div','a',Object.keys(m.summary.apps||{}).map(x=>x.split('.').pop()).join(', ')));
    Object.entries(m.goals||{}).forEach(([k,g])=>card.appendChild(el('div','goal',k.split(':')[0]+': '+(g.goal||'(no interpretation)'))));
    const grid=el('div','thumbs');card.appendChild(grid);card.onclick=()=>{setMode('mom');msel=m.id;selectMoment(m.id)};s.appendChild(card);
    const dd=await fetch('/api/moment/'+m.id).then(r=>r.json());dd.members.slice(0,6).forEach(mm=>{const t=el('div','th');if(mm.screenshot==='stored'){const i=new Image();i.loading='lazy';i.src='/img/'+mm.id;t.appendChild(i)}else t.appendChild(el('div','ph',mm.screenshot));t.appendChild(el('div','c',tm(mm.t)+' '+(mm.app||'')));grid.appendChild(t)})})}
function setMode(m){mode=m;$('vobs').classList.toggle('on',m==='obs');$('vmom').classList.toggle('on',m==='mom');$('vact').classList.toggle('on',m==='act');items=[];asel=null;$('list').textContent='';$('shot').textContent='';$('obs').textContent='';sel=msel=null;
  if(m==='obs')loadList();else if(m==='act')loadActivities();else loadMoments()}
$('vobs').onclick=()=>setMode('obs');$('vmom').onclick=()=>setMode('mom');$('vact').onclick=()=>setMode('act');
loadList();
</script></body></html>
"""


def load_goals(goals_dir):
    if not goals_dir:
        return None
    path = os.path.join(goals_dir, "moment_goals.json")
    if not os.path.exists(path):
        return None
    data = json.load(open(path))
    by_moment = {}
    for model, info in data["models"].items():
        groups = {}
        for gi, grp in enumerate(g for g in info["groups"] if len(g) > 1):
            for idx in grp:
                groups[data["moments"][idx]["id"]] = gi + 1
        for g in info["goals"]:
            by_moment.setdefault(g["moment_id"], {})[model] = {"goal": g["goal"], "keywords": g["keywords"], "group": groups.get(g["moment_id"]), "group_reason": info.get("group_reasons", {}).get(str(groups.get(g["moment_id"]))), "raw": g.get("raw") or json.dumps({"goal": g["goal"], "keywords": g["keywords"]}), "seconds": g.get("seconds"), "parsed": g.get("parsed"), "metrics": g.get("metrics"), "image_tags": g.get("image_tags"), "prompt_sent": g.get("prompt_sent")}
    base = {}
    for gi, grp in enumerate(g for g in data["baseline_packet_groups"] if len(g) > 1):
        for idx in grp:
            base[data["moments"][idx]["id"]] = gi + 1
    packets = {m["id"]: m.get("prompt") or m["packet"] for m in data["moments"]}
    packets_old = {m["id"]: m.get("prompt_old") for m in data["moments"] if m.get("prompt_old")}
    return {"by_moment": by_moment, "text_group": base, "packets": packets, "packets_old": packets_old}


def build_handler(store, data_dir, queries, goals_dir=None):
    goals = load_goals(goals_dir)
    labels_path = os.path.join(goals_dir, "labels.json") if goals_dir else None

    def labels():
        if labels_path and os.path.exists(labels_path):
            return json.load(open(labels_path))
        return {}

    def save_label(mid, label):
        data = labels()
        if label:
            data[mid] = label
        else:
            data.pop(mid, None)
        tmp = labels_path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f, indent=1)
        os.replace(tmp, labels_path)
        return data

    def summary(oid):
        o = store.get(oid, include_raw=False, include_events=False)
        st = o["metadata"]["extraction_status"]
        return {"screenshot": st["screenshot"]["status"], "ocr": st["ocr"]["status"],
                "events": len(store.events(oid))}

    def structure(oid):
        try:
            rows = store.extractions(oid, "structure")
        except Exception:  # noqa: BLE001 - an older checkout without the structure reader
            return {"present": False, "reason": "this checkout has no structure support"}
        if not rows:
            return {"present": False, "reason": "not built yet (run python -m trace_v2.structure)"}
        last = rows[-1]
        raw = last.get("raw")
        duration = None
        if last.get("started_at_ns") and last.get("finished_at_ns"):
            duration = round((last["finished_at_ns"] - last["started_at_ns"]) / 1e6)
        return {"present": True, "revision": last["revision"], "revisions": len(rows), "status": last["status"],
                "error_code": last.get("error_code"), "raw": raw, "raw_bytes": last.get("raw_bytes") or 0,
                "duration_ms": duration, "source_extraction_id": last.get("source_extraction_id")}

    def moment_rows():
        try:
            return store.derived.execute(
                "SELECT r.moment_id, r.revision, r.state, r.start_ns, r.end_ns, r.observation_count, r.summary_json, m.origin, "
                "m.parent_moment_id FROM moment_revisions r JOIN moments m USING (moment_id) WHERE r.state != 'merged' AND "
                "r.revision=(SELECT max(x.revision) FROM moment_revisions x WHERE x.moment_id=r.moment_id) "
                "ORDER BY r.start_ns, r.moment_id").fetchall()
        except Exception:  # noqa: BLE001 - an older checkout without the moments tables
            return []

    def moment_list():
        out = []
        for mid, rev, state, start, end, count, summary, origin, parent in moment_rows():
            sm = json.loads(summary)
            out.append({"id": mid, "revision": rev, "state": state, "start_ns": start, "end_ns": end, "count": count,
                        "summary": sm, "core": sm.get("core", count), "secondary": sm.get("secondary", 0),
                        "origin": origin, "parent": parent})
            if goals:
                out[-1]["goals"] = goals["by_moment"].get(mid)
                out[-1]["text_group"] = goals["text_group"].get(mid)
                out[-1]["packet"] = goals["packets"].get(mid)
                out[-1]["packet_old"] = goals["packets_old"].get(mid)
        for item in out:
            item["label"] = labels().get(item["id"])
        return out

    def moment_detail(mid):
        revs = store.derived.execute(
            "SELECT revision, state, merged_into, start_ns, end_ns, observation_count, recorded_at, params_json, summary_json, "
            "members_encoding, members FROM moment_revisions WHERE moment_id=? ORDER BY revision", (mid,)).fetchall()
        if not revs:
            raise KeyError(mid)
        meta = store.derived.execute("SELECT algorithm, origin, parent_moment_id FROM moments WHERE moment_id=?", (mid,)).fetchone()
        history = [{"revision": r[0], "state": r[1], "merged_into": r[2], "start_ns": r[3], "end_ns": r[4],
                    "count": r[5], "recorded_at": r[6]} for r in revs]
        cur = revs[-1]
        for r in reversed(revs):
            if r[1] != "merged" and r[5]:
                cur = r
                break
        members = []
        for m in queries._decode(cur[9], cur[10]):
            row = store.db.execute("SELECT o.app_name, o.window_title, s.status FROM observations o JOIN screenshots s USING (observation_id) "
                                   "WHERE o.observation_id=?", (m["o"],)).fetchone()
            members.append({"id": m["o"], "t": m["t"], "role": m["role"], "score": m["s"], "app": row[0] if row else None,
                            "window": row[1] if row else None, "screenshot": row[2] if row else "gone"})
        absorbed = [r[0] for r in store.derived.execute("SELECT DISTINCT moment_id FROM moment_revisions WHERE merged_into=?", (mid,))]
        children = [r[0] for r in store.derived.execute("SELECT moment_id FROM moments WHERE parent_moment_id=?", (mid,))]
        return {"moment_id": mid, "algorithm": meta[0], "origin": meta[1], "parent_moment_id": meta[2],
                "absorbed_moments": absorbed, "split_children": children,
                "current": {"revision": cur[0], "state": cur[1], "start_ns": cur[3], "end_ns": cur[4],
                            "params": json.loads(cur[7]), "summary": json.loads(cur[8])},
                "history": history, "members": members}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send(self, code, body, ctype, extra=None):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            try:
                if self.path == "/api/label" and labels_path:
                    body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
                    save_label(str(body["moment_id"]), str(body.get("label", "")).strip())
                    return self.send(200, b"{}", "application/json")
                return self.send(404, b"not found", "text/plain")
            except Exception as e:  # noqa: BLE001
                return self.send(500, json.dumps({"error": f"{type(e).__name__}: {e}"}).encode(), "application/json")

        def do_GET(self):
            url = urlparse(self.path)
            try:
                if url.path == "/":
                    return self.send(200, PAGE.encode(), "text/html; charset=utf-8")
                if url.path == "/api/list":
                    items = []
                    for row in store.list(limit=1000):
                        info = summary(row["observation_id"])
                        items.append({"id": row["observation_id"], "timestamp": row["timestamp"],
                                      "app_name": row["app_name"], "app_bundle_id": row["app_bundle_id"],
                                      "window_title": row["window_title"], **info})
                    return self.send(200, json.dumps({"data_dir": data_dir, "items": items}).encode(),
                                     "application/json")
                if url.path.startswith("/api/obs/"):
                    oid = url.path.rsplit("/", 1)[1]
                    raw = parse_qs(url.query).get("raw", ["0"])[0] == "1"
                    obs = store.get(oid, include_raw=raw, include_events=True)
                    return self.send(200, json.dumps(obs, default=str).encode(), "application/json")
                if url.path == "/api/moments":
                    return self.send(200, json.dumps({"data_dir": data_dir, "items": moment_list()}).encode(), "application/json")
                if url.path.startswith("/api/moment/"):
                    return self.send(200, json.dumps(moment_detail(url.path.rsplit("/", 1)[1]), default=str).encode(),
                                     "application/json")
                if url.path.startswith("/api/structure/"):
                    oid = url.path.rsplit("/", 1)[1]
                    return self.send(200, json.dumps(structure(oid), default=str).encode(), "application/json")
                if url.path.startswith("/img/"):
                    oid = url.path.rsplit("/", 1)[1]
                    try:
                        data = store.screenshot_bytes(oid)
                    except queries.ScreenshotUnavailable as e:
                        return self.send(404, b"", "text/plain", {"X-Reason": e.reason})
                    return self.send(200, data, "image/png")
                return self.send(404, b"not found", "text/plain")
            except KeyError:
                return self.send(404, b"unknown observation", "text/plain")
            except Exception as e:  # noqa: BLE001 - show the error in the page instead of dropping the request
                return self.send(500, json.dumps({"error": f"{type(e).__name__}: {e}"}).encode(), "application/json")

    return Handler


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-dir", required=True)
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--repo", default=os.getcwd(), help="checkout that contains trace_v2 (default: current directory)")
    ap.add_argument("--no-open", action="store_true")
    ap.add_argument("--goals", help="folder with moment_goals.json from the goal experiment; also stores labels.json")
    args = ap.parse_args()
    sys.path.insert(0, os.path.abspath(args.repo))
    try:
        from trace_v2.observations import ObservationStore, queries
    except ImportError as e:
        sys.exit(f"cannot import trace_v2 from {args.repo}: {e}")
    data_dir = os.path.abspath(os.path.expanduser(args.data_dir))
    if not os.path.exists(os.path.join(data_dir, "observations.sqlite3")):
        sys.exit(f"no observations.sqlite3 in {data_dir}")
    store = ObservationStore(data_dir)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), build_handler(store, data_dir, queries, args.goals and os.path.abspath(os.path.expanduser(args.goals))))
    url = f"http://127.0.0.1:{args.port}/"
    print(f"Trace V2 viewer on {url}  (Ctrl-C to stop)")
    if not args.no_open:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        store.close()


if __name__ == "__main__":
    main()
