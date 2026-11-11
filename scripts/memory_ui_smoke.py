#!/usr/bin/env python3
"""Draw only synthetic Memory evidence in a real AppKit window; export its view."""
import argparse,sys,time
from pathlib import Path
sys.path[:0]=[str(Path(__file__).resolve().parents[1]/'src')]
from AppKit import NSApplication,NSBitmapImageFileTypePNG,NSAppearance,NSImage,NSColor,NSBezierPath,NSBitmapImageRep
import projects,page_model,window,window_model,macos
import config
from memory_model import new_memory_state,memory_view
p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('--background',default='auto',help='auto, morning, afternoon, evening or night: pins the time-of-day background for the captures');args=p.parse_args();args.output.mkdir(parents=True,exist_ok=True)
config.BACKDROP=args.background          # set before the window is built
app=NSApplication.sharedApplication();app.setActivationPolicy_(0);app.setAppearance_(NSAppearance.appearanceNamed_('NSAppearanceNameAqua'))
reg=projects.empty();pid=projects.create(reg,'MVP launch · synthetic');items={}
state=new_memory_state();state['tab']='Memory'
binding={'project_id':'synthetic-project','ui_project_id':pid,'workspace_root':'/synthetic/lmemm-mvp','enrolled_at':'2026-10-10T09:00:00Z','active':True}
result={'decisions':[{'statement':'We decided to use local embeddings because project evidence stays on device.','reason':'because project evidence stays on device.','at':'2026-10-10T10:00:00Z','citations':['synthetic-1']}],
        'open_tasks':[{'statement':'TODO: Verify the project resume flow.','at':'2026-10-10T10:15:00Z','citations':['synthetic-2']}],
        'recent_evidence':[{'text':'The source bridge captures only approved visible local text.','at':'2026-10-10T10:20:00Z','citation_id':'synthetic-3','locator':'/synthetic/lmemm-mvp/bridge.py','origin_type':'observed_screen_text','truncated':True}],
        'coverage':{'evidence_count':3,'claims':'explicit supported intent only'}}
snapshot=memory_view(state,binding,{'state':'Ready','model_state':'available'},result)
actions=[]
w=window.MainWindow(lambda *a:None,lambda:(reg,items),on_memory=lambda *a:actions.append(a),memory_view=lambda _:snapshot)
w.nav=page_model.press(reg,w.nav,'go',pid)
w.show(window_model.view({'screen':True}));app.activateIgnoringOtherApps_(True)
for label in ['disabled','ready','processing','paused','error','source','overview','settings','settings-paused','settings-error','ask-done','ask-failed','asking','resume-empty','resume-populated','resume-source','folder-free-settings','settings-disconnected','resume-related']:
    state['tab']='Overview' if label.startswith('resume') else 'Overview' if label=='overview' else 'Memory'
    status={'state':{'disabled':'Disabled','processing':'Processing','paused':'Paused','error':'Needs attention'}.get(label,'Ready'),'model_state':'missing' if label=='disabled' else 'available','enrolled_at':'2026-10-10T09:00:00Z','indexed_at':'2026-10-10T10:21:00Z','pending':2 if label=='processing' else 0}
    state['source']={'text':'We decided to use local embeddings because project evidence stays on device.','locator':'/synthetic/lmemm-mvp/notes.md','at':'2026-10-10T10:00:00Z','citation_id':'synthetic-1','source_id':'synthetic-source','can_open_file':False,'origin_type':'user_note','truncated':False} if label=='source' else None
    snapshot=memory_view(state,None if label=='disabled' else binding,status,result)
    if label.startswith('resume'):
        snapshot['result']={} if label=='resume-empty' else dict(result,saved_activity=result['recent_evidence'],related_sources=[])
        if label!='resume-empty':snapshot['result']['open_tasks']=[dict(t,id='synthetic-task') for t in result['open_tasks']]
        if label=='resume-source':snapshot['source']=dict(text='SQLite stays on this Mac.',title='Database research',url='https://example.org/research',locator='https://example.org/research',origin_type='browser_selection',at='2026-10-20T10:00:00Z',citation_id='synthetic-web',source_id='synthetic-source',can_open_file=False)
    if label=='folder-free-settings':snapshot['binding']=dict(binding,workspace_root=None)
    if label=='settings-disconnected':snapshot['binding']=None;snapshot['retained_binding']=dict(binding,active=False)
    if label=='resume-related':snapshot['result']['related_sources']=[dict(id='synthetic-reference',title='Database comparison',citation_id='synthetic-web',from_citation='synthetic-3')]
    snapshot['extension_installed']=True
    if label in ('ask-done','ask-failed','asking'):
        snapshot['codex_connected']=True
        snapshot['ask']={'state':{'ask-done':'done','ask-failed':'failed','asking':'asking'}[label],
            'question':'What did we decide, and what is next?',
            'text':'We chose local embeddings so project evidence stays on device [synthetic-1]. Next: verify the project resume flow [synthetic-2].',
            'message':'Codex was not found on this Mac.'}
    if label.startswith('settings') or label=='folder-free-settings':
        w.memory_pane.settings=True
        if label=='settings-paused':
            snapshot['state']='Paused';snapshot['status']['source_connected']=True
        if label=='settings-error':
            snapshot['state']='Needs attention';snapshot['message']='Local model unavailable. Keyword search is available.'
            snapshot['status']['model_state']='missing'
    elif w.memory_pane:w.memory_pane.settings=False
    w.shown=None;w.render()
    w.window.makeKeyAndOrderFront_(None);app.activateIgnoringOtherApps_(True)
    w.window.displayIfNeeded()
    deadline=time.monotonic()+.6
    while time.monotonic()<deadline:macos.next_event(app,.05)
    import Quartz
    captured=Quartz.CGWindowListCreateImage(Quartz.CGRectNull,Quartz.kCGWindowListOptionIncludingWindow,int(w.window.windowNumber()),Quartz.kCGWindowImageBoundsIgnoreFraming)
    if captured is None:raise RuntimeError('Own-window capture unavailable')
    rep=NSBitmapImageRep.alloc().initWithCGImage_(captured)
    rep.representationUsingType_properties_(NSBitmapImageFileTypePNG,{}).writeToFile_atomically_(str(args.output/(label+'.png')),True)
    print(label,flush=True)
w._memory_action('source',{'citation_id':'synthetic-1'})
assert actions[-1]==('source',{'citation_id':'synthetic-1'})
w.window.orderOut_(None)
print('Native synthetic pane smoke passed; no personal capture, no live service.',flush=True)
