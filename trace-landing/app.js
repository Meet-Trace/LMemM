const root=document.documentElement;
const toggle=document.querySelector('.theme-toggle');
const systemTheme=matchMedia('(prefers-color-scheme: light)');
let explicitTheme=['dark','light'].includes(new URLSearchParams(location.search).get('theme'));
try{explicitTheme ||= ['dark','light'].includes(localStorage.getItem('trace-theme'));}catch{}
function themeLabel(){
 const dark=root.dataset.theme==='dark';
 const label=`Switch to ${dark?'light':'dark'} mode`;
 if(toggle){
  toggle.setAttribute('aria-label',label);
  toggle.title=label;
  toggle.innerHTML=dark
   ? '<svg viewBox="0 0 24 24" aria-hidden="true" data-theme-icon="sun"><circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1 1m12 12 1 1M5 19l1-1M18 6l1-1"/></svg>'
   : '<svg viewBox="0 0 24 24" aria-hidden="true" data-theme-icon="moon"><path d="M20.8 14A9 9 0 0 1 10 3.2 9 9 0 1 0 20.8 14Z"/></svg>';
 }
 document.querySelector('meta[name="theme-color"]')?.setAttribute('content',dark?'#101113':'#f7f6f2');
}
themeLabel();toggle?.addEventListener('click',()=>{root.dataset.theme=root.dataset.theme==='dark'?'light':'dark';explicitTheme=true;try{localStorage.setItem('trace-theme',root.dataset.theme);}catch{}themeLabel();});
systemTheme.addEventListener('change',e=>{if(!explicitTheme){root.dataset.theme=e.matches?'light':'dark';themeLabel();}});
for(const form of document.querySelectorAll('.waitlist-form'))form.addEventListener('submit',e=>{e.preventDefault();form.querySelector('[role="status"]').textContent='This preview does not save or send your email. Registration opens soon.';});
const film=document.querySelector('#film-dialog'),video=document.querySelector('#concept-video');
for(const button of document.querySelectorAll('[data-film]'))button.addEventListener('click',async()=>{film.showModal();document.body.style.overflow='hidden';video.volume=.7;try{await video.play();}catch{document.querySelector('#video-status').textContent='Press play to start the film.';}});
function dismiss(dialog){dialog.close();}
for(const dialog of document.querySelectorAll('dialog')){
 dialog.querySelector('.dialog-close').addEventListener('click',()=>dismiss(dialog));
 dialog.addEventListener('click',e=>{if(e.target===dialog){const r=dialog.getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)dialog.close();}});
 dialog.addEventListener('close',()=>{document.body.style.overflow='';if(dialog===film)video.pause();});
}
video?.addEventListener('error',()=>{document.querySelector('#video-status').textContent='The film is unavailable. Please try again later.';});
let currentWorld='building',currentQuestion='decision';
const worlds=window.TraceScenarios;
function text(selector,value){const el=document.querySelector(selector);if(el)el.textContent=value;}
function html(selector,value){const el=document.querySelector(selector);if(el)el.innerHTML=value;}
function renderQuestion(){
 const w=worlds[currentWorld],item=w[currentQuestion];
 html('#demo-answer',item.answer);text('.demo-label',item.label);text('#demo-description',item.description);
 html('.demo-output-top',`<span class="status-dot"></span> ${w.project} <span class="micro">PROJECT MEMORY</span>`);
 html('#inspect-source',`<span class="file-icon lavender">N</span><span>${w.note}<small>See where it came from</small></span><svg viewBox="0 0 24 24" aria-hidden="true"><rect x="5" y="3" width="14" height="18" rx="2"/><path d="M8 8h8m-8 4h8m-8 4h5"/></svg>`);
 text('#source-title',w.note);text('.source-quote',w.retained);
 for(const tab of document.querySelectorAll('[data-demo]')){const active=tab.dataset.demo===currentQuestion;tab.classList.toggle('active',active);tab.setAttribute('aria-pressed',String(active));}
}
function renderWorld(){
 const w=worlds[currentWorld];
 text('#world-description',w.description);html('.fragment-note h3',w.noteTitle);html('.fragment-note .window-bar',`<span class="file-icon lavender">N</span> ${w.note} <span class="dots">···</span>`);
 html('.fragment-decision p',w.quote);html('.source-label',`<span class="file-icon lavender">N</span> ${w.note}`);
 html('.fragment-task p',`<i class="checkbox"></i> ${w.task}`);
 html('.piece-one>div',`${w.note}<small>${w.shortNote}</small>`);html('.piece-three>div',`Next steps<small>${w.task}</small>`);
 text('.project-core h4',w.project);html('.return-top',`<span class="file-icon blue">P</span> ${w.project} <span>RESUME</span>`);
 html('.return-card h4',w.resume);text('.return-card>p',w.sourceDescription);html('.return-next>span:last-child',`<i class="checkbox"></i> ${w.task}`);
 text('.gather-note',w.note);
 document.querySelectorAll('.chapter').forEach((chapter,i)=>{
  const img=chapter.querySelector('img.mobile-scene');img.hidden=currentWorld!=='building';
  let card=chapter.querySelector('.mobile-world');if(!card){card=document.createElement('div');card.className='mobile-scene mobile-world';chapter.append(card);}
  card.hidden=currentWorld==='building';
  card.innerHTML=[`<span class="micro">${w.note}</span><h4>${w.noteTitle}</h4><p>${w.quote}</p>`,`<span class="micro">PROJECT MEMORY</span><h4>${w.project}</h4><p>${w.note} · Saved research · Next steps</p>`,`<span class="micro">WHERE YOU LEFT OFF</span><h4>${w.resume}</h4><p>${w.task}</p>`][i];
 });
 for(const button of document.querySelectorAll('[data-world]')){const active=button.dataset.world===currentWorld;button.classList.toggle('active',active);button.setAttribute('aria-pressed',String(active));}
 renderQuestion();
}
for(const button of document.querySelectorAll('[data-world]'))button.addEventListener('click',()=>{currentWorld=button.dataset.world;renderWorld();});
for(const button of document.querySelectorAll('[data-demo]'))button.addEventListener('click',()=>{currentQuestion=button.dataset.demo;renderQuestion();});
if(worlds&&document.querySelector('.audience-switch'))renderWorld();
document.querySelector('#inspect-source')?.addEventListener('click',()=>{document.querySelector('#source-dialog').showModal();document.body.style.overflow='hidden';});
const motion=matchMedia('(prefers-reduced-motion: reduce)'),desktop=matchMedia('(min-width: 761px)');
const stage=document.querySelector('.stage-shell'),chapters=[...document.querySelectorAll('.chapter')];
let ticking=false,lastStage=-1;
function updateScene(){
 ticking=false;
 const closing=document.querySelector('.closing');
 const g=motion.matches?1:Math.min(1,Math.max(0,(innerHeight-closing.getBoundingClientRect().top)/(innerHeight*.65)));
 closing.style.setProperty('--gather',g.toFixed(4));
 if(motion.matches||!desktop.matches){document.documentElement.style.removeProperty('--hero-shift');return;}
 if(stage){const target=innerHeight*.5;
 let selected=0,distance=Infinity;
 chapters.forEach((chapter,index)=>{const r=chapter.getBoundingClientRect(),d=Math.abs((r.top+r.bottom)/2-target);if(d<distance){selected=index;distance=d;}});
 if(selected!==lastStage){stage.dataset.stage=String(selected);stage.querySelector('.stage-counter').textContent=`0${selected+1} / 03`;stage.querySelector('.stage-status').textContent=['A DAY IN YOUR PROJECT','THE PIECES, CONNECTED','A CLEARER WAY BACK'][selected];stage.querySelectorAll('.chapter-dots i').forEach((dot,i)=>dot.classList.toggle('active',i===selected));lastStage=selected;}}
 const hero=document.querySelector('.hero');if(hero.getBoundingClientRect().bottom>0)root.style.setProperty('--hero-shift',`${Math.min(scrollY*.12,100)}px`);
}
function requestScene(){if(!ticking){ticking=true;requestAnimationFrame(updateScene);}}
addEventListener('scroll',requestScene,{passive:true});addEventListener('resize',requestScene);motion.addEventListener('change',requestScene);desktop.addEventListener('change',requestScene);requestScene();

// The proof viewer uses untouched real app captures, never illustrative scenario data.
const proofImages={decision:'assets/app-decision.png',source:'assets/app-source.png'};
function setProof(mode){
 const isSource=mode==='source',img=document.querySelector('#proof-image');if(!img)return;
 img.src=proofImages[mode];img.alt=isSource?'Actual earlier app build showing the retained source text for a synthetic project decision':'Actual earlier app build showing the synthetic project decision and View retained source button';
 document.querySelector('.proof-device').dataset.proof=mode;
 text('#proof-count',isSource?'02 / 02':'01 / 02');
 text('#proof-description',isSource?'The exact text, as it appears in the app. These are the original words behind the decision, not a generated summary.':'Every decision links back to where it came from. Select the highlighted button in the screenshot.');
 document.querySelector('.proof-hotspot').hidden=isSource;document.querySelector('.proof-reset').hidden=!isSource;
 for(const button of document.querySelectorAll('[data-proof]')){const active=button.dataset.proof===mode;button.classList.toggle('active',active);button.setAttribute('aria-pressed',String(active));}
}
for(const button of document.querySelectorAll('[data-proof]'))button.addEventListener('click',()=>setProof(button.dataset.proof));
document.querySelector('.proof-hotspot')?.addEventListener('click',()=>setProof('source'));
document.querySelector('.proof-reset')?.addEventListener('click',()=>setProof('decision'));
// Preload only the one next screenshot to keep the guided action immediate.
if(document.querySelector('#proof-image')){const nextCapture=new Image();nextCapture.src=proofImages.source;}

// Story: scroll position picks one of four steps (Activity, Context, Memory, Continuity).
(function(){
 const track=document.querySelector('.story-track'),stageEl=document.querySelector('.story-stage');
 if(!track||!stageEl)return;
 const steps=[...document.querySelectorAll('.story .step')],rail=[...document.querySelectorAll('.story-rail i')];
 const staticMode=matchMedia('(max-width: 760px), (prefers-reduced-motion: reduce)');
 let current=-1,queued=false;
 function apply(){
  queued=false;
  if(staticMode.matches){stageEl.dataset.step='3';current=-1;return;}
  const r=track.getBoundingClientRect(),span=r.height-innerHeight;
  const p=span>0?Math.min(1,Math.max(0,-r.top/span)):0,i=Math.min(3,Math.floor(p*4));
  if(i===current)return;current=i;stageEl.dataset.step=String(i);
  steps.forEach((el,k)=>el.classList.toggle('active',k===i));rail.forEach((el,k)=>el.classList.toggle('on',k===i));
 }
 function request(){if(!queued){queued=true;requestAnimationFrame(apply);}}
 addEventListener('scroll',request,{passive:true});addEventListener('resize',request);staticMode.addEventListener('change',request);request();
})();

// Story threads: solid curves drawn from each fragment's edge to the project, in real pixel coordinates.
(function(){
 const stageEl=document.querySelector('.story-stage'),svg=stageEl&&stageEl.querySelector('.threads');
 if(!svg)return;
 const grouped={f1:[24,24],f2:[76,22],f3:[16,52],f4:[84,56],f5:[28,80],f6:[74,82]};
 const ns='http://www.w3.org/2000/svg';
 function draw(){
  const w=stageEl.clientWidth,h=stageEl.clientHeight;if(!w||!h)return;
  svg.setAttribute('viewBox','0 0 '+w+' '+h);svg.replaceChildren();
  const cx=w/2,cy=h/2;let n=0;
  for(const [key,pos] of Object.entries(grouped)){
   const el=stageEl.querySelector('.'+key);if(!el)continue;
   const x=pos[0]/100*w,y=pos[1]/100*h,hw=el.offsetWidth/2+6,hh=el.offsetHeight/2+6,dx=cx-x,dy=cy-y;
   const t=Math.min(hw/Math.abs(dx||1e-6),hh/Math.abs(dy||1e-6)),sx=x+dx*t,sy=y+dy*t;
   const vx=cx-sx,vy=cy-sy,len=Math.hypot(vx,vy),nx=-vy/len,ny=vx/len,bend=(n%2?1:-1)*len*.12;n++;
   const f=v=>v.toFixed(1);
   const path=document.createElementNS(ns,'path');
   path.setAttribute('d','M'+f(sx)+' '+f(sy)+'C'+f(sx+vx*.35+nx*bend)+' '+f(sy+vy*.35+ny*bend)+' '+f(sx+vx*.7+nx*bend)+' '+f(sy+vy*.7+ny*bend)+' '+f(cx)+' '+f(cy));
   svg.append(path);path.style.setProperty('--len',Math.ceil(path.getTotalLength())+2);
   const dot=document.createElementNS(ns,'circle');dot.setAttribute('cx',f(sx));dot.setAttribute('cy',f(sy));dot.setAttribute('r','3.5');svg.append(dot);
  }
 }
 draw();addEventListener('resize',draw);if(document.fonts)document.fonts.ready.then(draw);
})();
