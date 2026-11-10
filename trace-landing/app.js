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
  if(!toggle.querySelector('.theme-icons'))toggle.innerHTML='<span class="theme-icons" aria-hidden="true"><svg class="theme-sun" viewBox="0 0 24 24" data-theme-icon="sun"><circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1 1m12 12 1 1M5 19l1-1M18 6l1-1"/></svg><svg class="theme-moon" viewBox="0 0 24 24" data-theme-icon="moon"><path d="M20.8 14A9 9 0 0 1 10 3.2 9 9 0 1 0 20.8 14Z"/></svg></span>';
  toggle.classList.toggle('is-dark',dark);
 }
 document.querySelector('meta[name="theme-color"]')?.setAttribute('content',dark?'#101113':'#f7f6f2');
}
let themeFadeTimer;
themeLabel();toggle?.addEventListener('click',()=>{
 if(!matchMedia('(prefers-reduced-motion: reduce)').matches)root.classList.add('theme-changing');
 root.dataset.theme=root.dataset.theme==='dark'?'light':'dark';explicitTheme=true;
 try{localStorage.setItem('trace-theme',root.dataset.theme);}catch{}
 themeLabel();clearTimeout(themeFadeTimer);themeFadeTimer=setTimeout(()=>root.classList.remove('theme-changing'),320);
});
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
 const navHeader=document.querySelector('.header'),navHero=document.querySelector('.hero');
 if(navHeader&&navHero)navHeader.classList.toggle('over-hero',navHero.getBoundingClientRect().bottom>navHeader.offsetHeight);
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

// Keep geometry attached to the actual moving cards, not guessed percentage centers.
(function(){
 const track=document.querySelector('.story-track'),stage=document.querySelector('.story-stage');
 if(!track||!stage)return;
 const svg=stage.querySelector('.threads'),steps=[...document.querySelectorAll('.story .step')],rail=[...document.querySelectorAll('.story-rail i')];
 const staticMode=matchMedia('(max-width: 760px), (prefers-reduced-motion: reduce)'),ns='http://www.w3.org/2000/svg';
 const links=[...stage.querySelectorAll('.frag')].map((card,index)=>{const path=document.createElementNS(ns,'path'),dot=document.createElementNS(ns,'circle');dot.setAttribute('r','2.1');svg.append(path,dot);return {card,path,dot,index};});
 let current=-1,frameId=0,until=0,last=performance.now(),connection=0;
 function draw(now){
  const r=stage.getBoundingClientRect();if(!r.width||!r.height)return;
  svg.setAttribute('viewBox',`0 0 ${r.width} ${r.height}`);
  const core=stage.querySelector('.core-a').getBoundingClientRect(),middle=core.left+core.width/2;
  const dt=Math.min(50,now-last);last=now;connection+=(Number(current>0)-connection)*Math.min(1,dt/115);
  for(const {card,path,dot,index} of links){
   const box=card.getBoundingClientRect(),left=box.left+box.width/2<middle,side=left?1:-1;
   const sx=(left?box.right:box.left)-r.left+side*5,sy=box.top+box.height/2-r.top;
   const ex=(left?core.left:core.right)-r.left-side*5,ey=core.top+core.height*((Math.floor(index/2)+1)/4)-r.top;
   const bend=Math.max(8,Math.abs(ex-sx)*.55);
   path.setAttribute('d',`M ${sx} ${sy} C ${sx+side*bend} ${sy}, ${ex-side*bend} ${ey}, ${ex} ${ey}`);
   const length=path.getTotalLength();path.style.strokeDasharray=String(length);path.style.strokeDashoffset=String(length*(1-connection));
   dot.setAttribute('cx',String(sx));dot.setAttribute('cy',String(sy));
  }
 }
 function frame(now){
  frameId=0;
  if(staticMode.matches){stage.dataset.step='3';current=-1;steps.forEach(step=>step.removeAttribute('aria-hidden'));return;}
  const r=track.getBoundingClientRect(),span=r.height-innerHeight,p=span>0?Math.min(1,Math.max(0,-r.top/span)):0;
  let next=Math.min(3,Math.floor(p*4));
  if(current>=0&&Math.abs(next-current)===1&&Math.abs(p-Math.max(next,current)/4)<.012)next=current;
  if(next!==current){current=next;stage.dataset.step=String(next);until=now+1150;steps.forEach((step,i)=>{step.classList.toggle('active',i===next);step.setAttribute('aria-hidden',String(i!==next));});rail.forEach((dot,i)=>dot.classList.toggle('on',i===next));}
  draw(now);if(now<until)frameId=requestAnimationFrame(frame);
 }
 function request(){if(!frameId)frameId=requestAnimationFrame(frame);}
 addEventListener('scroll',request,{passive:true});addEventListener('resize',()=>{until=performance.now()+1100;request();});staticMode.addEventListener('change',request);document.fonts?.ready.then(request);request();
})();

// Informational controls only. This page never captures or sends user context.
(function(){
 const section=document.querySelector('.privacy-experience');if(!section)return;
 const copies={
  screen:{title:'Read the moment. Keep the context.',summary:'Trace can capture display frames while an allowed app is active to read visible text. Other visible windows can appear too. These are individual frames, not continuous screen video.',purpose:'Recognize the work you had open, so you can return to it.',retained:'Retained text and some small image previews in local app files.',destination:'Normal capture is processed on your Mac, without sending the frames to a cloud AI.',details:'When an allowed app is active, the beta can capture the display containing it. Other visible windows may appear too. Most full frames are processed in memory; some captures are kept as small image previews. Those previews normally expire after seven days, except for pinned items or items with open notes. Extracted text and notes have separate retention. If you enable the optional input timeline, it can retain full-size frames for up to 24 hours. Current beta files are not encrypted by Trace. You can pause capture, inspect saved items and delete retained data in the app.'},
  voice:{title:'A voice note, when you open one.',summary:'The microphone is used while the voice-note window is open. It is not an always-on background recorder.',purpose:'Turn a thought you choose to speak into a written note.',retained:'The written note. Speech recognition is on-device, and no audio file is kept.',destination:'The voice-note helper uses on-device speech recognition, without sending audio to a cloud AI.',details:'Voice notes are optional and require macOS microphone and speech permissions. The listening helper runs while the note window is open. It requires on-device recognition rather than falling back to remote speech recognition. The transcript becomes a note you can inspect or delete.'},
  sharing:{title:'An outside AI is your choice.',summary:'Local capture and memory do not need a cloud AI. Sharing context with an outside assistant is a separate action.',purpose:'Bring relevant saved context into an assistant conversation when you want to.',retained:'Your local memory stays in the app. You can preview the context selected for sharing.',destination:'If you approve outside-AI access or share a context packet, selected text goes to that provider, under its policies.',details:'The current app supports scoped agent access and a context preview. Copying a packet gives you control over where you paste it; connecting an outside assistant is an explicit disclosure choice. This preview uses made-up text and sends nothing. Local storage is not a claim that data already shared with another provider can be recalled from that provider.'}
 };
 let selected='screen',paused=false;
 const status=section.querySelector('#privacy-status'),screen=section.querySelector('.privacy-screen');
 function show(mode){selected=mode;const copy=copies[mode];for(const key of ['title','summary','purpose','retained','destination','details'])section.querySelector('#privacy-'+key).textContent=copy[key];section.querySelectorAll('[data-privacy]').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.privacy===mode)));section.querySelector('.capture-example').hidden=mode!=='screen';section.querySelector('.voice-example').hidden=mode!=='voice';section.querySelector('.sharing-example').hidden=mode!=='sharing';section.querySelector('.app-permissions').hidden=mode!=='screen';section.querySelector('.capture-toolbar').hidden=mode!=='screen';section.querySelector('.capture-details').open=false;status.textContent='';}
 section.querySelectorAll('[data-privacy]').forEach(button=>button.addEventListener('click',()=>show(button.dataset.privacy)));
 section.querySelector('#capture-toggle').addEventListener('click',()=>{paused=!paused;screen.classList.toggle('is-paused',paused);section.querySelector('#capture-toggle').textContent=paused?'Resume preview':'Pause preview';section.querySelector('#capture-toggle').setAttribute('aria-pressed',String(paused));section.querySelector('#capture-state').innerHTML=`<i></i> Preview ${paused?'paused':'running'}`;status.textContent=paused?'In the app, pausing stops new capture. Previously saved items remain until you delete them.':'Preview resumed. No real capture is running on this page.';});
 section.querySelectorAll('[data-preview-app]').forEach(input=>input.addEventListener('change',()=>{const selected=[...section.querySelectorAll('[data-preview-app]:checked')].map(input=>input.dataset.previewApp);section.querySelector('#app-selection-note').textContent=`${selected.length} ${selected.length===1?'app':'apps'} selected in this preview`;section.querySelector('.capture-example').style.opacity=selected.length?'':'.3';status.textContent=selected.length?`Example selection: ${selected.join(', ')}. Your Mac settings are unchanged.`:'No apps selected in the preview. In Trace, excluded apps do not trigger capture.';}));
 section.querySelector('#packet-toggle').addEventListener('click',()=>{const packet=section.querySelector('#context-packet');packet.hidden=!packet.hidden;section.querySelector('#packet-toggle').setAttribute('aria-expanded',String(!packet.hidden));section.querySelector('#packet-toggle').textContent=packet.hidden?'Preview a context packet':'Close example packet';status.textContent=packet.hidden?'':'This packet contains only the demo note. Nothing was sent.';});
})();

// A true exit from the pinned story: no scrub through all four stages on the way out.
document.querySelector('.story-skip')?.addEventListener('click',event=>{
 const target=document.querySelector('#after-story');if(!target)return;
 event.preventDefault();const previous=root.style.scrollBehavior;root.style.scrollBehavior='auto';
 target.scrollIntoView({behavior:'auto',block:'start'});root.style.scrollBehavior=previous;
 history.replaceState(null,'','#after-story');document.querySelector('#band-title')?.focus({preventScroll:true});
});
