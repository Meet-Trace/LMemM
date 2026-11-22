"""Run window.py's drawing code against a fake AppKit, to catch Python mistakes (a wrong key, a bad call) that
only show on a Mac. It cannot judge looks. Run on its own: it stubs modules, so tests/test_window_smoke.py
starts it in a separate process. Exits 1 and prints the traceback when any page fails to draw."""
import sys, types, traceback
from unittest import mock
import os
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.join(HERE, "..", "src"), HERE]

class Meta(type):
    def __getattr__(cls, name):
        if name.startswith("__"): raise AttributeError(name)
        return mock.MagicMock()

class Base(metaclass=Meta):
    def __init__(self, *a, **k): pass
    @classmethod
    def alloc(cls): return cls()
    def __getattr__(self, name):
        if name.startswith("__"): raise AttributeError(name)
        return mock.MagicMock()

def fake(name, bases=()):
    m = types.ModuleType(name)
    def getattr_(attr):
        if attr.startswith("__"): raise AttributeError(attr)
        if attr[:2] in ("NS",) and attr[2:3].isupper() and attr not in ("NSMakeRect","NSMakePoint"):
            return type(attr, (Base,), {})
        return mock.MagicMock()
    m.__getattr__ = getattr_
    sys.modules[name] = m
    return m
for n in ("objc", "Foundation", "AppKit", "Quartz", "CoreText", "Cocoa", "UserNotifications", "ApplicationServices", "AVFoundation", "Speech"):
    fake(n)
sys.modules["objc"].super = lambda *a: mock.MagicMock()
sys.modules["objc"].python_method = lambda f: f

fake_widget = types.ModuleType("widget")
for n in ("_Fields", "_Flipped", "_Ring", "_Tap"):
    setattr(fake_widget, n, type(n, (Base,), {"initWithChange_submit_cancel_": lambda self,*a: self, "initWithFrame_callback_": lambda self,*a: self, "initWithFrame_": lambda self,*a: self, "initWithChecked_": lambda self,*a: self}))
SIZE = types.SimpleNamespace(width=1080, height=720)
fake_widget._Flipped.frame = lambda self: types.SimpleNamespace(size=SIZE)
sys.modules["widget"] = fake_widget
kit = types.ModuleType("setup_kit")
for n in ("put_text","tile","button","text_width","text_height","ink","mute","accent","accent_text","status_text","card_background","faint","line","icon","first_symbol","note","chip","chip_width","font","warm","surface","glyph","pill","accent_wash","meta","display","body"):
    setattr(kit, n, mock.MagicMock(return_value=20))
kit._KitRule = type("_KitRule", (Base,), {"initWithFrame_": lambda self, *a: self})
kit._KitSurface = type("_KitSurface", (Base,), {"initWithFrame_fill_edge_radius_": lambda self, *a: self})
kit.LEFT, kit.RIGHT, kit.CENTER = 0, 2, 1
kit.button = mock.MagicMock()
kit.surface = mock.MagicMock()
kit.text_width = lambda *a, **k: 50
kit.text_height = lambda *a, **k: 20
kit._KitButton = type("_KitButton", (Base,), {"initWithFrame_callback_": lambda self, *a: self})

sys.modules["setup_kit"] = kit
import window
print("window imported with fakes")

from datetime import datetime
from test_project_actions import world
import projects, suggestions as sg, page_model, notes, time
import settings_model as sm
USER = {"name": "Ada", "works_on": ["code"], "watch_apps": [{"id": "com.a", "name": "A"}], "skip_apps": [{"id": "com.x", "name": "X"}]}
FACTS = {"screen": False, "screen_fresh": True, "ax": False, "mic": "denied", "speech": "granted"}

reg, items, ids = world()
sug = sg.empty()
sg.offer_project(sug, items, "Trip", ["a", "e"])
sg.offer_items(sug, reg, items, ids["q3"], ["c", "d", "e"])
data = lambda: (reg, items)
calls = []
def on_project(action, args):
    calls.append((action, args))
    if action in sm.ACTIONS:
        r = sm.apply(USER, action, args.get("value"))
        return {"message": r["message"], "go": None, "stay": True, "undo": {"settings": USER}}
    import project_actions as pa
    return pa.do(reg, items, action, **({**args, "sug": sug} if action in sg.ACTIONS else args))
w = window.MainWindow(lambda r: None, data, on_project=on_project, on_project_undo=lambda u: None,
                      suggest=lambda: sg.view(sug, reg, items, datetime.now()),
                      prefs=lambda section: sm.view(USER, section, FACTS, {"data_dir": "/x/data", "files": 3, "bytes": 1200}))
class FakeScroll:
    def frame(self): return types.SimpleNamespace(size=types.SimpleNamespace(height=600, width=800))
    def contentView(self):
        return types.SimpleNamespace(bounds=lambda: types.SimpleNamespace(origin=types.SimpleNamespace(y=0)),
                                     scrollToPoint_=lambda p: None)
    def __getattr__(self, n): return mock.MagicMock()
w.side_scroll, w.main_scroll = FakeScroll(), FakeScroll()
import window_model
w.banner = window_model.view({"screen": True})

FAILED = []


def step(name, fn):
    try:
        fn()
        print("ok  ", name)
    except Exception:
        FAILED.append(name)
        print("FAIL", name); traceback.print_exc()

def render():
    w.shown = None
    w._render()

step("home", render)
w.nav = page_model.press(reg, w.nav, "go", ids["q3"])
step("project q3 (suggested)", render)
w.nav = page_model.press(reg, w.nav, "go", ids["home"])
step("project home", render)
w.start_select(); step("select", render)
w.pick_thing("d"); step("select ticked", render)
w.open_thing_menu(["d"]); w.open_thing_pick("assign", ["d"]); w.open_thing_forget(["d"])
w.close_dialog(); w.stop_select()
w.nav = page_model.press(reg, w.nav, "thing", "a"); step("thing page", render)
for section in sm.SECTIONS:
    w.nav = page_model.press(reg, w.nav, "settings", section)
    step("settings " + section, render)
w.installed = [{"id": "com.b", "name": "B", "path": "/Applications/B.app"}, {"id": "com.a", "name": "A", "path": "/Applications/A.app"}]
w.app_paths = {}
w.open_apps("watch"); step("app picker (watch)", lambda: w._dialog())
w.open_apps("skip"); step("app picker (skip)", lambda: w._dialog())
w.dialog["query"] = "b"; step("app picker narrowed", lambda: w._dialog())
w.setting("watch_add", {"id": "com.b", "name": "B"})
if not w.dialog:
    FAILED.append("the app picker closed after adding an app")
w.close_dialog()
w.open_name("me"); step("name dialog", lambda: w._dialog(focus=True)); w.close_dialog()
w.setting("keep_days", 30)
w.nav = page_model.press(reg, w.nav, "back")
step("back from settings", render)
w.nav = page_model.press(reg, w.nav, "go", ids["q3"])
for action, sid, kw in (("accept_items", "s2", {"ids": ["c"]}), ("reject_items", "s2", {"ids": ["d"]}), ("accept_project", "s1", {})):
    step("answer " + action, lambda: w.answer(action, sid, **kw))

reg, items, ids = world()
sug = sg.empty()
sg.offer_project(sug, items, "Trip", ["a", "e"])
sg.offer_items(sug, reg, items, ids["q3"], ["c", "d"])
w.nav = page_model.new_state()
kit.put_text.reset_mock()
render()
texts = [c.args[1] for c in kit.put_text.call_args_list if len(c.args) > 1]
for want in ("Suggestions · 2", "Make “Trip” a project?", "2 things may belong in “Q3 plan”"):
    if want not in texts:
        FAILED.append("home is missing: " + want)
w.nav = page_model.press(reg, w.nav, "go", ids["q3"])
kit.put_text.reset_mock(); render()
if "Suggested for this project · 2" not in [c.args[1] for c in kit.put_text.call_args_list]:
    FAILED.append("project page is missing its suggestions")
for label, wd, ht in (("narrow", 880, 560), ("wide", 2200, 1300), ("small than minimum", 600, 400), ("back", 1080, 720)):
    SIZE.width, SIZE.height = wd, ht
    step("resize " + label, w._resized)
    if not (880 <= w.W and 560 <= w.H and 560 <= w.main_w <= 980 and w.col_x >= 24):
        FAILED.append("bad layout at %s: %s" % (label, (w.W, w.H, w.main_w, w.col_x)))
    if w.col_x + w.main_w > w.W - window.SIDE_W:
        FAILED.append("column overflows at " + label)
    for kind in ("go", "thing"):
        w.nav = page_model.press(reg, w.nav, kind, ids["q3"] if kind == "go" else "a")
        step("page at %s (%s)" % (label, kind), render)
import keynav
w.nav = page_model.new_state(); render()
step("keys: down in page", lambda: w.key(keynav.DOWN))
if w.cur["id"] is None: FAILED.append("Down did not land on a row")
step("keys: left to sidebar", lambda: w.key(keynav.LEFT))
step("keys: down in sidebar", lambda: w.key(keynav.DOWN))
if w.cur["zone"] != "side" or w.cur["id"] is None: FAILED.append("sidebar walk failed %s" % w.cur)
step("keys: return opens", lambda: w.key(keynav.RETURN))
w.dialog = {"kind": "x"}
if w.key(keynav.DOWN): FAILED.append("keys must not move behind a card")
w.dialog = None
w.focus_search(); w.go_back(); w._regular(True); w._regular(False)
# Exercise real native-pane drawing and callbacks against fakes in every status.
from memory_model import new_memory_state,memory_view
memory_calls=[]
w.on_memory=lambda action,payload:memory_calls.append((action,payload))
state=new_memory_state();state['tab']='Memory'
for memory_status in ('Disabled','Ready','Processing','Paused','Needs attention'):
    binding=None if memory_status=='Disabled' else {'workspace_root':'/synthetic/repo','active':True}
    snapshot=memory_view(state,binding,{'state':memory_status,'model_state':'missing'}, {'primary':[{'text':'Retained synthetic evidence','at':'2026-10-10','citation_id':'c1','locator':'/synthetic/repo/a.py'}]})
    w.memory_view=lambda pid,v=snapshot:v
    w.nav=page_model.press(reg,w.nav,'go',ids['q3'])
    step('Memory '+memory_status,render)
# Settings page, install notices, source view, preview.
rich={'decisions':[{'statement':'We decided X because Y.','at':'2026-10-11T08:00:00+00:00','citations':['c2']}],'open_tasks':[{'statement':'TODO: z','at':'2026-10-01T08:00:00+00:00','citations':['c3']}],
      'primary':[{'text':'hit','at':'2026-10-12T08:38:03+00:00','citation_id':'c1','locator':'/synthetic/repo/.lmemm-project-notes/native-3131876c15f4e10d0127af062170c0a1.md','origin_type':'user_note'}]}
binding={'workspace_root':'/synthetic/repo','active':True,'project_id':'p1'}
for label,notice,source in (('rich page',None,None),('page with notice',('VS Code could not install the extension: x',True,'install_extension'),None),
                            ('source view',None,{'text':'snap','locator':'/a/b.md','at':'2026-10-12T08:38:03+00:00','citation_id':'c1','can_open_file':True,'origin_type':'user_note','truncated':False})):
    state['source']=source;state['preview']=None
    snapshot=memory_view(state,binding,{'state':'Ready','model_state':'missing'},rich);snapshot['notice']=notice
    w.memory_view=lambda pid,v=snapshot:v
    step('Memory '+label,render)
state['source']=None
for label,notice,preview in (('settings',None,None),('settings with notice',('Installed. Quit VS Code.',False,'install_extension'),None),('settings with preview',None,{'proposed':True,'x':1}),('settings with Codex connected',None,{'connection':{'grant_id':'g','command':'codex mcp add lmemm_memory -- python adapter --config /p/g.json'}})):
    state['preview']=preview
    snapshot=memory_view(state,binding,{'state':'Paused','model_state':'available'},rich);snapshot['notice']=notice
    w.memory_view=lambda pid,v=snapshot:v
    w._memory_action('open_settings')
    step('Memory '+label,render)
for label,status,notice in (
        ('model installing',{'state':'Ready','model_state':'missing','model_install':'installing','model_install_percent':42,'model_install_seconds':65},None),
        ('model installing unknown percent, slow',{'state':'Ready','model_state':'missing','model_install':'installing','model_install_percent':None,'model_install_seconds':400},None),
        ('model failed',{'state':'Ready','model_state':'missing','model_install':'failed','model_install_message':'Install the local Ollama runtime, then retry model setup'},None),
        ('model starting',{'state':'Ready','model_state':'missing'},('Starting…',False,'install_model')),
        ('extension installing',{'state':'Ready','model_state':'missing'},('Installing…',False,'install_extension'))):
    state['preview']=None
    snapshot=memory_view(state,binding,status,rich);snapshot['notice']=notice
    w.memory_view=lambda pid,v=snapshot:v
    w._memory_action('open_settings')
    step('Memory settings: '+label,render)
state['preview']=None
snapshot=memory_view(state,binding,{'state':'Ready','model_state':'missing'},rich);snapshot['extension_installed']=True;snapshot['notice']=('Opened VS Code. Choose Allow there to finish.',False,'connect_extension')
w.memory_view=lambda pid,v=snapshot:v
w._memory_action('open_settings');step('Memory settings: extension installed',render)
snapshot=memory_view(state,binding,{'state':'Ready','model_state':'available','source_connected':True},rich);snapshot['extension_installed']=True;snapshot['notice']=None
w.memory_view=lambda pid,v=snapshot:v
step('Memory settings: VS Code connected',render)
snapshot=memory_view(state,binding,{'state':'Ready','model_state':'missing'},rich);snapshot['extension_installed']=True;snapshot['notice']=('Opened VS Code. Choose Allow there to finish.',False,'connect_extension')
w.memory_view=lambda pid,v=snapshot:v
for chosen in ('confirm_model','confirm_agent','confirm_disconnect'):
    w._memory_action(chosen);step('Memory settings: '+chosen,render)
w._memory_action('confirm_no');step('Memory settings: confirm cancelled',render)
memory_calls.clear();w._memory_action('confirm_model');w._memory_action('confirm_yes')
if ('install_model',{'approved':True}) not in memory_calls:FAILED.append('confirm_yes did not start the install')
w._memory_action('confirm_yes')
if len([c for c in memory_calls if c[0]=='install_model'])!=1:FAILED.append('confirm_yes ran twice')
snapshot=memory_view(state,binding,{'state':'Needs attention','reason':'2 saved items could not be indexed for meaning search (the local model failed). Keyword search still works.','pending':1,'failed':2,'model_state':'available','indexed_at':'2026-10-12T08:00:00+00:00'},rich)
w.memory_view=lambda pid,v=snapshot:v
w._memory_action('close_settings');step('Memory: pill with needs attention',render)
w._memory_action('open_status');step('Memory settings: details card',render)
if not w.memory_pane.settings:FAILED.append('status pill did not open Settings')
w._memory_action('tab',{'tab':'Memory'})
if w.memory_pane.settings:FAILED.append('choosing the Memory tab left Settings open')
w._memory_action('open_settings');w._memory_action('close_settings')
step('Memory back from settings',render)
w._memory_action('source',{'citation_id':'c1'})
# A dialog arrives with its short pop, and every control answers VoiceOver's press.
w.open_name('new'); step('dialog arriving',lambda:None); w.close_dialog()
for tap in [v for v in w.side_foot.subviews() if hasattr(v,'accessibilityPerformPress')][:1]:
    if tap.accessibilityRole()!='AXButton':FAILED.append('side foot control is not a button for VoiceOver')
if ('source',{'citation_id':'c1'}) not in memory_calls:FAILED.append('Memory citation callback not forwarded')
broken=dict(snapshot,error='Project memory unavailable')
w.memory_view=lambda pid,v=broken:v
step('Memory: error card with bound folder',render)
unbound=dict(memory_view(state,None,{'state':'Disabled'},rich),error='Project memory unavailable')
w.memory_view=lambda pid,v=unbound:v
step('Memory: error card on empty state',render)
import json, tempfile
hist_dir = tempfile.mkdtemp()
today = datetime.now()
with open(os.path.join(hist_dir, today.strftime("%Y%m%d") + "-000001.json"), "w") as fh:
    json.dump({"timeline": [{"from": "00:00:02", "to": "00:20:00", "seconds": 1198, "item": "a"},
                            {"from": "00:20:00", "to": "00:21:00", "seconds": 30, "item": "b"}]}, fh)
w.history = lambda: {"sessions_dir": hist_dir, "live": None, "skip": []}
w.open_history(); step("history today", render)
for mode in ("projects", "apps", "timeline"):
    w.press("history_mode", mode); step("history " + mode, render)
for scope in ("yesterday", "week", "earlier", "today"):
    w.press("history_day", scope); step("history " + scope, render)
w.press("history_day", "prev"); step("history previous day", render)
w.press("history_search", "q3"); step("history search", render)
w.press("history_search", "zzzzzz"); step("history search, nothing", render)
w.press("history_search", ""); w.press("history_day", "today")
w.press("thing", "a"); step("history entry opens its thing", render)
w.press("back"); step("back to history", render)
if w.nav["view"] != "history":
    FAILED.append("back from a thing did not return to history")
w.history = lambda: {"sessions_dir": os.path.join(hist_dir, "missing"), "live": None, "skip": []}
step("history empty", render)
w.escape(); step("history closed", render)
print("FAILED:", FAILED) if FAILED else print("all pages drew")
sys.exit(1 if FAILED else 0)
