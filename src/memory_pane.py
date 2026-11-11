"""LMemM - the Memory tab's drawing: search, notes and the Settings page. The words, rows and groups it shows come from
memory_view.py (pure, tested).

Colour: one blue accent (palette.ACCENT) for every icon, stripe and button. Green, orange and grey appear only as state
(a dot, a status word). Red is not used here: it is for a permission that is off.
"""
from memory_view import CONFIRMS, PERIOD_LABELS, STATE_LABELS, evidence_rows, friendly, group_evidence, search_note, short_name, source_presentation
import palette

ACC = palette.ACCENT
GREEN, ORANGE, GRAY = (.18, .62, .36), (.91, .45, .16), (.50, .50, .55)
RED = (.86, .27, .25)                            # only the Danger zone's quiet accent; never a whole red section
SECTIONS = {'Search results': ('Best matches', ['magnifyingglass'], ACC), 'Related evidence': ('Related', ['link'], ACC),
            'Decisions': ('Decisions', ['flag.fill'], ACC), 'Open tasks': ('Open tasks', ['checkmark.circle', 'checkmark.circle.fill'], ACC),
            'Recent evidence': ('Recent', ['clock'], GRAY)}
ASKED = {'confirm_model': 'Meaning search', 'confirm_agent': 'Codex', 'confirm_disconnect': 'Disconnect Memory'}     # the row each confirmation belongs under
STATE_COLORS = {'Ready': GREEN, 'Processing': ACC, 'Paused': GRAY, 'Needs attention': ORANGE}


def state_text(mode, kit):
    """The colour of the state *word*: the deeper green and orange that stay readable on the light card."""
    return {'Ready': lambda: kit.status_text('green'), 'Processing': kit.accent_text,
            'Needs attention': lambda: kit.status_text('orange')}.get(mode, kit.mute)()


def _pick_class():
    """The small AppKit object that receives a menu pick (built on first use, so importing this file needs no AppKit)."""
    from Foundation import NSObject
    class _PanePick(NSObject):
        def pick_(self,sender):
            call=self.calls.get(sender.tag())
            if call:call()
    return _PanePick


class MemoryPane:
    def __init__(self,on_action):
        from AppKit import NSTextField,NSMakeRect
        from widget import _Fields,_Flipped
        self.project=None;self.shown_state=None;self.settings=False;self.confirm=None;self.status_open=False
        self.action=on_action;self.host=_Flipped.alloc().initWithFrame_(NSMakeRect(0,0,800,600))
        self.query_typing=_Fields.alloc().initWithChange_submit_cancel_(lambda text:self.action('query',{'text':text}),lambda text:self.action('submit',{'enter':True}),lambda:self.action('query',{'text':''}))
        self.query=NSTextField.alloc().initWithFrame_(NSMakeRect(0,0,100,30));self.query.setDelegate_(self.query_typing)
        self.query.setPlaceholderString_('Ask your project…');self.query.setAccessibilityLabel_('Search project memory')
        self.note_typing=_Fields.alloc().initWithChange_submit_cancel_(lambda text:None,self._save_note,lambda:self.note.setStringValue_(''))
        self.note=NSTextField.alloc().initWithFrame_(NSMakeRect(0,0,100,30));self.note.setDelegate_(self.note_typing)
        self.note.setPlaceholderString_('Add a note… start with TODO: for a task, or “We decided … because …”');self.note.setAccessibilityLabel_('New intentional project note')
        import setup_kit as kit
        for field in (self.query,self.note):                       # no square bezel: the pane draws a rounded box behind each field
            field.setBezeled_(False);field.setDrawsBackground_(False);field.setFocusRingType_(1);field.setFont_(kit.font(14,400))
        self.host.addSubview_(self.query);self.host.addSubview_(self.note)
    def _save_note(self,text):
        if text.strip():self.action('note',{'text':text});self.note.setStringValue_('')
    def render(self,view,width=800,x=32,w=None):
        from AppKit import NSColor,NSMakeRect
        import setup_kit as kit
        w=w or width-64;host=self.host
        def rgb(c,alpha=1.0):return NSColor.colorWithRed_green_blue_alpha_(c[0],c[1],c[2],alpha)
        for sub in list(host.subviews()):
            if sub is not self.query and sub is not self.note:sub.removeFromSuperview()
        retained=view.get('retained_binding') or view.get('binding')
        project=retained.get('project_id') if retained else None
        if project!=self.project:self.project=project;self.settings=False;self.confirm=None;self.status_open=False;self.note.setStringValue_('')
        binding=view.get('binding') or (retained if self.settings else None)
        if str(self.query.stringValue())!=view['query']:self.query.setStringValue_(view['query'])
        hide=not binding or self.settings
        self.query.setHidden_(hide);self.note.setHidden_(hide)
        y=8;self.bottom=None
        def panel(px,py,pw,ph,bar=None):
            box=kit._KitButton.alloc().initWithFrame_callback_(NSMakeRect(px,py,pw,ph),None);box.fill=kit.faint(0.05);box.radius=10;host.addSubview_(box)
            if bar is not None:
                stripe=kit._KitButton.alloc().initWithFrame_callback_(NSMakeRect(px,py+10,3,ph-20),None);stripe.fill=rgb(bar);stripe.radius=1.5;host.addSubview_(stripe)
        def label_row(symbols,color,text,at):
            kit.tile(host,symbols,rgb(color),x,at,22)
            kit.put_text(host,text.upper(),x+30,at+3,w-30,11,700,kit.mute(),wrap=False,height=16)
            return 30
        def card(text,meta,locator,citation,bar,title=None):
            nonlocal y
            inner=w-32;head=28 if title else 0
            th=kit.text_height(text,13 if title else 15,400 if title else 500,inner);top=y
            panel(x,top,w,th+58+head,bar)
            if title:kit.put_text(host,title,x+16,top+12,inner,15,700,wrap=False,height=20)
            kit.put_text(host,text,x+16,top+12+head,inner,13 if title else 15,400 if title else 500,kit.mute() if title else None,height=th)
            my=top+th+20+head;kit.put_text(host,meta,x+16,my,kit.text_width(meta,12,400)+8,12,400,kit.mute(),wrap=False,height=16);cx=x+16+kit.text_width(meta,12,400)+14
            name=None if title else short_name(locator)
            if name:
                cw=kit.text_width(name,11,500)+26;chip=kit._KitButton.alloc().initWithFrame_callback_(NSMakeRect(cx,my-2,cw,20),None);chip.fill=kit.faint(0.07);chip.radius=6;host.addSubview_(chip)
                glyph=kit.icon(kit.first_symbol(['doc.text','doc']),11,kit.mute(),(cx+7,my+1,12,13))
                if glyph is not None:host.addSubview_(glyph)
                kit.put_text(host,name,cx+22,my-2,cw-24,11,500,kit.mute(),wrap=False,height=20,middle=True)
            if citation:kit.button(host,'View source',x+w-108,my-6,96,28,lambda c=citation:self.action('source',{'citation_id':c}),kind='link',size=12,weight=600)
            y=top+th+58+head+10
        def field_box(field,fy,left=14):
            box=kit._KitButton.alloc().initWithFrame_callback_(NSMakeRect(x,fy,w,40),None);box.fill=kit.faint(0.05);box.border=kit.line();box.radius=11
            host.addSubview_positioned_relativeTo_(box,-1,field)        # behind the field
            field.setFrame_(NSMakeRect(x+left,fy+10,w-left-14,20))
        def para(text,size=13,weight=400,color=None,gap=10):
            nonlocal y
            y+=kit.put_text(host,str(text),x,y,w,size,weight,color or kit.mute())+gap
        def error_card(text,at):
            # A raw error line says nothing; say what happened, what is safe, and give a way out.
            nonlocal y
            slow='stopped' in text
            title='Memory stopped' if slow else "Memory couldn't start"
            body='LMemM keeps your notes safe on this Mac. Its private memory service is not responding right now. Try again, and if it keeps happening quit and reopen LMemM.'
            bh=kit.text_height(body,13,400,w-32);top=at
            panel(x,top,w,bh+90,ORANGE)
            kit.tile(host,['exclamationmark.triangle.fill','exclamationmark.triangle'],rgb(ORANGE),x+16,top+14,22)
            kit.put_text(host,title,x+48,top+16,w-64,14,700,wrap=False,height=20)
            kit.put_text(host,body,x+16,top+46,w-32,13,400,kit.mute(),height=bh)
            kit.button(host,'Try again',x+16,top+bh+54,110,30,lambda:self.action('refresh'),kind='primary',size=13,weight=600)
            y=top+bh+90+14
        mode=view['state']
        if not binding:
            y=40;cx=x+w/2
            kit.tile(host,['brain','sparkles'],rgb(ACC),cx-32,y,64);y+=84
            kit.put_text(host,'Give this project a memory',x,y,w,22,700,align=kit.CENTER,height=30);y+=38
            body='Save notes, decisions and browser sources for this project. A folder and VS Code are optional. LMemM never scans your folders or imports history.'
            bw=min(w,420);y+=kit.put_text(host,body,cx-bw/2,y,bw,14,400,kit.mute(),align=kit.CENTER)+20
            kit.button(host,'Enable Memory',cx-90,y,180,40,lambda:self.action('enroll',{}),kind='primary',size=15);y+=40
            if view.get('error'):y+=24;error_card(view['error'],y)
        elif self.settings:self._settings(dict(view,binding=binding),x,w,kit,rgb,panel)
        else:
            color=state_text(mode,kit);label=STATE_LABELS.get(mode,mode);shown=label+'  ›'
            pw=kit.text_width(shown,12,600)+34;chip=kit._KitButton.alloc().initWithFrame_callback_(NSMakeRect(x,y,pw,26),lambda:self.action('open_status'));chip.fill=rgb(STATE_COLORS.get(mode,GREEN),.14);chip.radius=13;chip.focusable=True;host.addSubview_(chip)
            dot=kit.icon('circle.fill',7,rgb(STATE_COLORS.get(mode,GREEN)),(12,9,8,8))
            if dot is not None:chip.addSubview_(dot)
            kit.put_text(chip,shown,24,0,pw-26,12,600,color,wrap=False,height=26,middle=True)   # inside the pill, so a click on the words is a click on the pill
            rw=kit.chip_width('Refresh','arrow.clockwise');kit.chip(host,'Refresh','arrow.clockwise',x+w-rw,y-3,False,lambda:self.action('refresh'));y+=40
            if view.get('error'):error_card(view['error'],y)
            if view.get('notice'):para(view['notice'][0],13,500,kit.status_text('orange') if view['notice'][1] else kit.mute())
            field_box(self.query,y,left=42)
            glyph=kit.icon('magnifyingglass',16,rgb(ACC),(x+14,y+11,18,18))
            if glyph is not None:host.addSubview_(glyph)
            y+=54
            px=x
            for period in view['periods']:                       # the same selected look as the tabs: a filled blue chip
                label=PERIOD_LABELS.get(period,period)
                kit.chip(host,label,None,px,y-3,period==view['period'],lambda p=period:self.action('time_filter',{'period':p}));px+=kit.chip_width(label,None)+8
            if view.get('codex_connected'):
                aw=kit.chip_width('Ask Codex','sparkles');kit.chip(host,'Ask Codex','sparkles',x+w-aw,y-3,False,lambda:self.action('ask_codex',{'text':str(self.query.stringValue())}))
            y+=44
            ask=view.get('ask')
            if ask:
                if ask['state']=='asking':title,body='Asking Codex…','Reading this project’s notes and files. This takes a few seconds.'
                elif ask['state']=='done':title,body='Codex’s answer'+(' to “'+ask['question']+'”' if ask.get('question') else ''),ask['text']
                else:title,body="Codex couldn't answer",ask.get('message','')
                bh=kit.text_height(body,14,400,w-32);top=y
                panel(x,top,w,bh+(96 if ask['state']!='asking' else 68),ACC)
                kit.put_text(host,title,x+16,top+14,w-32,15,700,wrap=False,height=20)
                kit.put_text(host,body,x+16,top+42,w-32,14,400,height=bh)
                if ask['state']=='done':kit.put_text(host,'Generated by Codex. Verify its citations; the search cards below may differ.',x+16,top+bh+50,w-32,11,400,kit.mute(),wrap=False,height=16)
                if ask['state']!='asking':kit.button(host,'Close',x+16,top+bh+(68 if ask['state']=='done' else 54),80,26,lambda:self.action('close_ask'),kind='quiet',size=12,weight=600)
                y=top+bh+(96 if ask['state']!='asking' else 68)+14
            if view['loading']:para('Loading permitted evidence…')
            if view['empty'] and not (view['query'].strip() and view.get('asked')==view['query']):para(view['empty'],14,500)
            previous=None;source=view.get('source');rows=[] if source else evidence_rows(view['result'])
            miss=search_note(view['query'],rows) if view['query'].strip() and view.get('asked')==view['query'] and not view['loading'] and not source else None
            if miss:para(miss,14,500,kit.mute() if rows else kit.status_text('orange'))
            for row in ([] if source else group_evidence(rows)):
                if row['section']!=previous:
                    title,symbols,color_=SECTIONS.get(row['section'],(row['section'],['circle'],GRAY))
                    if miss and row['section']=='Search results':title,symbols,color_='Possible matches',['magnifyingglass'],GRAY
                    if previous is not None:y+=8
                    y+=label_row(symbols,color_,title,y);previous=row['section']
                meta=row['provenance']+(' · seen %d times'%row['count'] if row['count']>1 else '')+(' · Truncated excerpt' if row['truncated'] else '')+(' · '+friendly(row['at']) if row['at'] else '')
                card(row['text'],meta,row['locator'],row['citation_id'],SECTIONS.get(row['section'],('',[],GRAY))[2],row['title'])
            if source:
                p=source_presentation(source);y+=label_row(['doc.text'],ACC,'Retained source',y)
                inner=w-32;th=kit.text_height(p['text'],14,400,inner);top=y;panel(x,top,w,th+88,ACC)
                kit.put_text(host,p['text'],x+16,top+12,inner,14,400,height=th)
                kit.put_text(host,p['provenance']+(' · Truncated excerpt' if p['truncated'] else '')+' · '+friendly(p['at']),x+16,top+th+20,inner,11,400,kit.mute(),height=16)
                kit.put_text(host,short_name(p['locator'],60)+'   '+p['open_explanation'],x+16,top+th+40,inner,11,400,kit.mute(),height=34)
                y=top+th+98
                if p['open_enabled']:kit.button(host,'Open current file…',x,y,170,30,lambda c=source['citation_id']:self.action('open_file',{'citation_id':c}),kind='outline',size=13,weight=600)
                kit.button(host,'Close source',x+(180 if p['open_enabled'] else 0),y,120,30,lambda:self.action('close_source'),kind='quiet',size=13,weight=600);y+=46
                if p.get('url'):kit.button(host,'Open original URL…',x,y,180,30,lambda c=source['citation_id']:self.action('open_original',{'citation_id':c}),kind='outline',size=13);y+=42
                if source.get('source_id'):kit.button(host,'Delete saved snapshot…',x,y,210,30,lambda s=source['source_id']:self.action('forget_source',{'source_id':s}),kind='outline',size=13);y+=42
            y+=14;y+=label_row(['note.text','doc.text'],ACC,'Add a note',y)
            field_box(self.note,y);y+=54
            kit.button(host,'Save excerpt…',x,y,160,32,lambda:self.action('show_excerpt_form'),kind='outline',size=13);y+=44
        if view.get('preview') and binding and not self.settings:self.bottom=self._preview(view,x,w,y,kit,panel)
        end=(self.bottom if self.bottom is not None else y)+40
        self.host.setFrame_(NSMakeRect(0,0,width,max(end,500)))
        ask=view.get('ask') or {}
        shown=(self.settings,self.confirm,bool(view.get('preview')),ask.get('state'),bool(view.get('notice')))
        if self.shown_state is not None and shown!=self.shown_state:self._fade_in()      # a state changed: ease it in, never snap
        self.shown_state=shown
        return end
    def _menu(self,items):
        """A native menu of the secondary actions under a setting's ellipsis button."""
        from AppKit import NSEvent,NSMenu,NSMenuItem
        if not hasattr(self,'_pick'):self._pick=_pick_class().alloc().init()
        self._pick.calls={}
        menu=NSMenu.alloc().initWithTitle_('')
        for n,(label,action) in enumerate(items):
            item=NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(label,'pick:','');item.setTarget_(self._pick);item.setTag_(n)
            self._pick.calls[n]=(lambda a=action:self.action(a));menu.addItem_(item)
        window=self.host.window()
        where=self.host.convertPoint_fromView_(window.mouseLocationOutsideOfEventStream(),None) if window else (0,0)
        menu.popUpMenuPositioningItem_atLocation_inView_(None,where,self.host)
    def _fade_in(self,seconds=0.15):
        """A short fade, no movement. Skipped when macOS Reduce Motion is on."""
        from AppKit import NSAnimationContext,NSWorkspace
        if NSWorkspace.sharedWorkspace().accessibilityDisplayShouldReduceMotion():return
        self.host.setWantsLayer_(True);self.host.setAlphaValue_(0.0)
        NSAnimationContext.beginGrouping();NSAnimationContext.currentContext().setDuration_(seconds)
        self.host.animator().setAlphaValue_(1.0);NSAnimationContext.endGrouping()
    def _preview(self,view,x,w,y,kit,panel):
        """The Codex card: what Codex would see (Preview), or the one step left after Connect. Returns the y below it."""
        import json
        from AppKit import NSMakeRect
        preview=view['preview'];connection=preview.get('connection')
        if connection:                                   # after Connect: words, never the access file
            codex=connection.get('codex') or {};state=codex.get('state');command=connection.get('command','')
            if state=='adding':
                title,body,show_command='Adding Trace to Codex…','This takes a few seconds.',False
            elif state=='added':
                title,body,show_command='Codex is connected','Trace is added to Codex. If Codex is already open, restart it to see it.',False
            else:
                title,show_command='Codex can now read this project',True
                body=(codex.get('message') or 'Trace could not add itself to Codex.')+' Paste this line in Terminal to finish. It stays on this Mac.'
            bh=kit.text_height(body,13,400,w-32);ch=kit.text_height(command,12,400,w-56) if show_command else 0
            panel(x,y,w,bh+ch+(120 if show_command else 100 if state=='added' else 70),ACC)
            kit.put_text(self.host,title,x+16,y+14,w-32,15,700,wrap=False,height=20)
            kit.put_text(self.host,body,x+16,y+40,w-32,13,400,kit.mute(),height=bh)
            by=y+bh+54
            if show_command:
                top=by-2;box=kit._KitButton.alloc().initWithFrame_callback_(NSMakeRect(x+16,top,w-32,ch+16),None);box.fill=kit.faint(0.07);box.radius=8;self.host.addSubview_(box)
                kit.put_text(self.host,command,x+28,top+8,w-56,12,400,height=ch);by=top+ch+26
                kit.button(self.host,'Copy command',x+16,by,130,30,lambda:self.action('copy_codex_command'),kind='primary',size=13,weight=600)
                kit.button(self.host,'Done',x+154,by,80,30,lambda:self.action('close_preview'),kind='quiet',size=13,weight=600)
            elif state=='added':
                kit.button(self.host,'Done',x+16,by,80,30,lambda:self.action('close_preview'),kind='primary',size=13,weight=600)
            return by+30+16
        title='Proposed context for Codex · no access granted' if preview.get('proposed') else 'Exact context preview for Codex'
        body=json.dumps(preview,ensure_ascii=False,indent=2);th=kit.text_height(body,12,400,w-32)
        y+=12;y+=kit.put_text(self.host,title,x,y,w,14,700,height=20)+6
        panel(x,y,w,th+24);kit.put_text(self.host,body,x+16,y+12,w-32,12,400,height=th);y+=th+36
        kit.button(self.host,'Copy context preview',x,y,200,30,lambda:self.action('copy_preview'),kind='outline',size=13,weight=600)
        kit.button(self.host,'Close',x+208,y,80,30,lambda:self.action('close_preview'),kind='quiet',size=13,weight=600)
        return y+46
    def _settings(self,view,x,w,kit,rgb,panel):
        from AppKit import NSMakeRect
        host=self.host;y=8;notice=view.get('notice')
        y+=4
        kit.put_text(host,'Memory settings',x,y,w,24,700,height=32,track=-0.022);y+=52
        if not (view.get('binding') or {}).get('active',True):
            y+=kit.put_text(host,'Memory is disconnected. Its retained snapshots remain on this Mac. Re-enable it to search, or delete retained memory before deleting the project.',x,y,w,14,400,kit.mute())+20
            kit.button(host,'Enable Memory',x,y,170,34,lambda:self.action('enroll',{}),kind='primary',size=13);y+=46
            kit.button(host,'Delete retained memory…',x,y,220,34,lambda:self.action('confirm_delete_memory'),kind='outline',size=13)
            self.bottom=y+60;return
        if view.get('preview'):y=self._preview(view,x,w,y,kit,panel)+8
        mode=view['state']
        if mode=='Needs attention':
            st=view.get('status',{});detail=view.get('message') or ''
            facts=['Waiting: %s'%st.get('pending',0)]
            if st.get('failed'):facts.append('Failed: %s'%st['failed'])
            if st.get('indexed_at'):facts.append('Last indexed '+friendly(st['indexed_at']))
            facts=' · '.join(facts);dh=kit.text_height(detail,13,400,w-32);top=y
            panel(x,top,w,dh+100,ORANGE)
            kit.put_text(host,'What needs attention',x+16,top+12,w-32,14,700,wrap=False,height=20)
            kit.put_text(host,detail,x+16,top+36,w-32,13,400,kit.status_text('orange'),height=dh)
            kit.put_text(host,facts,x+16,top+dh+42,w-32,11,400,kit.mute(),wrap=False,height=16)
            kit.button(host,'Try again',x+16,top+dh+66,110,30,lambda:self.action('resume'),kind='primary',size=13,weight=600)
            kit.chip(host,'Recover notes','lifepreserver',x+134,top+dh+66,False,lambda:self.action('recover_native_notes'))
            y=top+dh+100+14
        def confirm_card():
            """The question sits directly under the row whose button asked it, so it is never off screen."""
            nonlocal y
            title,detail,go=CONFIRMS[self.confirm];dh=kit.text_height(detail,13,400,w-32);top=y
            panel(x,top,w,dh+88,ACC)
            kit.put_text(host,title,x+16,top+14,w-32,15,700,wrap=False,height=20)
            kit.put_text(host,detail,x+16,top+38,w-32,13,400,kit.mute(),height=dh)
            gw=kit.text_width(go,13,600)+34;kit.button(host,go,x+16,top+dh+50,gw,30,lambda:self.action('confirm_yes'),kind='outline' if self.confirm=='confirm_disconnect' else 'primary',size=13,weight=600)
            kit.button(host,'Cancel',x+24+gw,top+dh+50,90,30,lambda:self.action('confirm_no'),kind='quiet',size=13,weight=600);y=top+dh+88+8
        group={'top':None,'danger':False,'rows':0,'surface':False}
        def end_group():
            """Only the Capture list sits on a soft tonal surface; the other sections are rows between fine dividers."""
            nonlocal y
            if group['top'] is None:return
            if group['surface']:
                y+=8
                surface=kit._KitSurface.alloc().initWithFrame_fill_edge_radius_(NSMakeRect(x,group['top'],w,y-group['top']),kit.faint(0.04),None,14)
                host.addSubview_positioned_relativeTo_(surface,-1,None)
            group['top']=None;y+=30
        def tip_over(px,py,pw,ph,text):
            """The longer instruction, kept out of the page until the pointer rests on the row."""
            if text:
                hover=kit._KitButton.alloc().initWithFrame_callback_(NSMakeRect(px,py,pw,ph),None);hover.setToolTip_(text);host.addSubview_(hover)
        def row(symbols,title,state,buttons,state_color=None,dot=False,more=(),warn=None,tip=None,progress=False,fraction=None,danger=False):
            """A setting is a title, its state under it, and one action. Other actions wait in the ellipsis menu."""
            nonlocal y
            if group['rows']:host.addSubview_(kit._KitRule.alloc().initWithFrame_(NSMakeRect(x+16,y,w-32,1)))
            group['rows']+=1
            btn=[(b[0],b[1],b[2],b[3] if len(b)>3 else True) for b in buttons]
            widths=[kit.text_width(b[0],13,600)+34 for b in btn];right=x+w-16
            menu_w=34 if more else 0;action_w=sum(widths)+8*len(widths)+menu_w
            text_w=max(w-44-16-action_w-24,160)
            state_text_=('\u25CF '+state) if dot and state else state
            sh=kit.text_height(state_text_,13,400,text_w,True,0.004,1.3) if state_text_ else 0
            wh=kit.text_height(warn,13,400,text_w,True,0.004,1.3) if warn else 0
            h=max(58,14+22+sh+(wh+4 if warn else 0)+14)+(14 if progress else 0)
            kit.glyph(host,symbols,x+16,y+16,18,rgb(RED) if danger else kit.mute())
            kit.put_text(host,title,x+46,y+13,text_w,15,600,wrap=False,height=22,track=-0.004)
            if state_text_:kit.put_text(host,state_text_,x+46,y+36,text_w,13,400,state_color or kit.mute(),height=sh,track=0.004,lead=1.3)
            if warn:kit.put_text(host,warn,x+46,y+36+sh+4,text_w,13,400,kit.status_text('orange'),height=wh,track=0.004,lead=1.3)
            if progress:
                by=y+h-20;track=kit._KitButton.alloc().initWithFrame_callback_(NSMakeRect(x+46,by,min(text_w,420),4),None);track.fill=kit.faint(0.10);track.radius=2;host.addSubview_(track)
                fw=min(text_w,420)*(fraction or 0)
                if fw>0:fill=kit._KitButton.alloc().initWithFrame_callback_(NSMakeRect(x+46,by,max(fw,4),4),None);fill.fill=rgb(ACC);fill.radius=2;host.addSubview_(fill)
            tip_over(x+46,y,text_w,h,tip)
            if more:
                right-=34;opts=[m for m in more]
                more_btn=kit.button(host,'\u22EF',right,y+(h-32)/2-(7 if progress else 0),34,32,lambda items=opts:self._menu(items),kind='quiet',size=16,weight=700);right-=8
            for item,bw in reversed(list(zip(btn,widths))):
                label,action,kind,on=item;right-=bw
                kit.button(host,label,right,y+(h-32)/2-(7 if progress else 0),bw,32,lambda a=action:self.action(a),kind=kind,size=13,weight=600,enabled=on);right-=8
            y+=h
            if self.confirm in CONFIRMS and title==ASKED.get(self.confirm):y+=8;confirm_card()
        def sub(text):
            nonlocal y
            y+=10;kit.put_text(host,text,x+16,y,w,12,600,kit.mute(),wrap=False,height=16,track=0.02);y+=24
            group['rows']=0
        def cap(text,danger=False,surface=False):
            nonlocal y
            end_group()
            kit.put_text(host,text,x,y,w,15,700,kit.status_text('red') if danger else None,wrap=False,height=22,track=-0.01);y+=34
            group['top']=y;group['rows']=0;group['danger']=danger;group['surface']=surface
        def note_for(kind,default):
            if notice and len(notice)>2 and notice[2]==kind:return notice[0],(kit.status_text('orange') if notice[1] else None)
            return default,None
        root=(view.get('binding') or {}).get('workspace_root')
        binding=view.get('binding') or {};status=view.get('status',{})
        cap('Capture',surface=True)
        sub('Storage')
        row(['folder'],'Project folder',(STATE_LABELS.get(mode,mode).split(' \u00b7')[0])+(' \u00b7 '+short_name(root,48) if root else ''),
            [('Resume' if mode=='Paused' else 'Pause','resume' if mode=='Paused' else 'pause','outline')],state_color=state_text(mode,kit),dot=True)
        if not root:
            row(['folder.badge.plus','folder'],'Optional workspace','Not connected. Notes and browser saves work without one.',[('Choose folder…','choose_root','outline')],tip='Connect a folder for VS Code capture.')
        sub('Connected apps')
        paired=status.get('browser_paired')
        row(['globe'],'Chrome','Paired' if paired else 'Not connected',
            [('Show extension folder','open_browser_extension','outline')] if paired else [('Connect Chrome','install_browser','primary')],
            state_color=kit.status_text('green') if paired else None,dot=bool(paired),
            more=[('Disconnect','revoke_browser')] if paired else [('Show extension folder','open_browser_extension')],
            tip='Pin the Trace extension in Chrome and save selected text with Control + Shift + S. Saved sources stay when you disconnect. No background browsing is collected.' if paired else 'Connect Chrome, then load the bundled Trace extension from its installation folder. No background browsing is collected.')
        installed=view.get('extension_installed');kind=notice[2] if notice and len(notice)>2 else None
        busy=bool(notice and kind in ('install_extension','connect_extension') and notice[0].endswith('…'))
        said=None
        if notice and kind in ('install_extension','connect_extension'):said=notice[0]
        connected=status.get('source_connected');blind=view.get('accessibility') is False
        vs_tip='Open this project in standard VS Code, then connect. Your editor windows stay open.'
        if connected:
            row(['chevron.left.forwardslash.chevron.right','curlybraces'],'VS Code','Connected, paused' if mode=='Paused' else 'Connected',[],state_color=kit.status_text('green'),dot=True,
                warn=('Nothing kept from this file. '+status['capture_blocker']) if status.get('capture_blocker') else None,tip='Shares the text you view in the editor.')
        elif installed and not busy:
            row(['chevron.left.forwardslash.chevron.right','curlybraces'],'VS Code',said or ('Needs Accessibility access' if blind else 'Installed'),
                [('Allow Accessibility','allow_accessibility','primary')] if blind else [('Connect','reconnect_extension','primary')],
                state_color=kit.status_text('orange') if (notice and notice[1]) or blind else None,tip=('Press Allow, switch Trace on in the list, then come back.' if blind else vs_tip))
        else:
            row(['chevron.left.forwardslash.chevron.right','curlybraces'],'VS Code','Installing in VS Code…' if busy and kind=='install_extension' else (said or 'Not installed'),
                [('Working…' if busy else 'Install extension','install_extension','quiet' if busy else 'primary',not busy)],
                state_color=kit.status_text('orange') if notice and notice[1] else None,tip='Shares the text you view in the editor.')
        sub('Intelligence')
        ready=status.get('model_state')=='available';state=status.get('model_install')
        starting=bool(notice and len(notice)>2 and notice[2]=='install_model' and not notice[1])
        model_tip='A 46 MB model that stays on this Mac. Finds notes by meaning, not just by words. Needs Ollama installed first.'
        if state=='installing' or starting:
            percent=status.get('model_install_percent');secs=status.get('model_install_seconds') or 0
            text='Starting the download…' if state!='installing' else ('Downloading… %d%%'%percent if percent else 'Starting the download…')+' \u00b7 %d:%02d'%(secs//60,secs%60)
            row(['sparkles'],'Meaning search',text,[('Installing…','confirm_model','quiet',False)],progress=True,fraction=(percent or 0)/100,
                warn='Taking longer than usual. Check your connection.' if secs>180 else None,tip=model_tip)
        elif state=='failed':
            row(['sparkles'],'Meaning search',status.get('model_install_message') or 'The install failed.',[('Try again','confirm_model','primary')],state_color=kit.status_text('orange'),tip=model_tip)
        else:
            text,color=note_for('install_model','On' if ready else 'Not installed')
            row(['sparkles'],'Meaning search',text,[] if ready else [('Install','confirm_model','primary')],state_color=color or (kit.status_text('green') if ready else None),dot=ready,tip=model_tip)
        end_group()
        cap('Agents')
        connected_codex=view.get('codex_connected')
        row(['terminal'],'Codex','Connected' if connected_codex else 'Not connected',[('Preview','preview','outline')] if connected_codex else [('Connect…','confirm_agent','primary')],
            state_color=kit.status_text('green') if connected_codex else None,dot=bool(connected_codex),
            more=[('Copy prompt','copy_codex_prompt'),('Revoke access','revoke_agent')] if connected_codex else [],
            tip='Codex can search this project’s notes and sources and read the cited text.')
        end_group()
        cap('Danger zone',True)
        row(['bolt.slash'],'Disconnect Memory','Stops capture and agent access. Saved notes stay on this Mac.',[('Disconnect…','confirm_disconnect','outline')],danger=True)
        row(['trash'],'Delete retained memory','Removes retained snapshots, the graph and agent access. Original notes and files stay.',[('Delete…','confirm_delete_memory','outline')],danger=True)
        end_group()
        self.bottom=y
