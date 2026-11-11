"""LMemM - the Memory tab's drawing: search, notes and the Settings page. The words, rows and groups it shows come from
memory_view.py (pure, tested).

Colour: one blue accent (palette.ACCENT) for every icon, stripe and button. Green, orange and grey appear only as state
(a dot, a status word). Red is not used here: it is for a permission that is off.
"""
from memory_view import CONFIRMS, PERIOD_LABELS, STATE_LABELS, evidence_rows, friendly, group_evidence, search_note, short_name, source_presentation, source_provenance
import palette

ACC = palette.ACCENT
GREEN, ORANGE, GRAY = (.18, .62, .36), (.91, .45, .16), (.50, .50, .55)
BTN_H,BTN_PAD=32,34                              # the one button size, shared by Settings and the Memory page
KINDS={'primary':'primary','secondary':'outline','tertiary':'ghost','destructive':'danger'}
PADX=20                                          # inner side padding of every container
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
        self.project=None;self.shown_state=None;self.settings=False;self.composer=False;self.details=False;self.detail_for=None;self.confirm=None;self.status_open=False;self.refresh_state=None
        self.action=on_action;self.host=_Flipped.alloc().initWithFrame_(NSMakeRect(0,0,800,600))
        self.query_typing=_Fields.alloc().initWithChange_submit_cancel_(lambda text:self.action('query',{'text':text}),lambda text:self.action('submit',{'enter':True}),lambda:self.action('query',{'text':''}))
        self.query=NSTextField.alloc().initWithFrame_(NSMakeRect(0,0,100,30));self.query.setDelegate_(self.query_typing)
        self.query.setPlaceholderString_('Ask your project…');self.query.setAccessibilityLabel_('Search project memory')
        self.note_typing=_Fields.alloc().initWithChange_submit_cancel_(lambda text:None,self._save_note,lambda:self.note.setStringValue_(''))
        self.note=NSTextField.alloc().initWithFrame_(NSMakeRect(0,0,100,30));self.note.setDelegate_(self.note_typing)
        self.note.setPlaceholderString_('Add a note… start with TODO: for a task, or “We decided … because …”');self.note.setAccessibilityLabel_('New intentional project note')
        import setup_kit as kit
        for field in (self.query,self.note):                       # no square bezel: the pane draws a rounded box behind each field
            field.setBezeled_(False);field.setDrawsBackground_(False);field.setFocusRingType_(1);field.setFont_(kit.font(16 if field is self.query else 14,400))
        self.host.addSubview_(self.query);self.host.addSubview_(self.note)
    def _save_note(self,text):
        if text.strip():self.composer=False;self.action('note',{'text':text});self.note.setStringValue_('')
    def render(self,view,width=800,x=32,w=None):
        from AppKit import NSColor,NSMakeRect
        import setup_kit as kit
        w=w or width-64;host=self.host
        def rgb(c,alpha=1.0):return NSColor.colorWithRed_green_blue_alpha_(c[0],c[1],c[2],alpha)
        for sub in list(host.subviews()):
            if sub is not self.query and sub is not self.note:sub.removeFromSuperview()
        retained=view.get('retained_binding') or view.get('binding')
        project=retained.get('project_id') if retained else None
        if project!=self.project:self.project=project;self.composer=False;self.details=False;self.settings=False;self.confirm=None;self.status_open=False;self.note.setStringValue_('')
        binding=view.get('binding') or (retained if self.settings else None)
        if str(self.query.stringValue())!=view['query']:self.query.setStringValue_(view['query'])
        hide=not binding or self.settings
        self.query.setHidden_(hide);self.note.setHidden_(True)          # the note field is shown only while the composer is open
        y=8;self.bottom=None
        def panel(px,py,pw,ph,bar=None):
            box=kit._KitButton.alloc().initWithFrame_callback_(NSMakeRect(px,py,pw,ph),None);box.fill=kit.faint(0.05);box.radius=10;host.addSubview_(box)
            if bar is not None:
                stripe=kit._KitButton.alloc().initWithFrame_callback_(NSMakeRect(px,py+10,3,ph-20),None);stripe.fill=rgb(bar);stripe.radius=1.5;host.addSubview_(stripe)
        def field_box(field,fy,left=14,height=40):
            box=kit._KitButton.alloc().initWithFrame_callback_(NSMakeRect(x,fy,w,height),None);box.fill=kit.faint(0.06);box.border=kit.line();box.radius=14 if height>40 else 11
            host.addSubview_positioned_relativeTo_(box,-1,field)        # behind the field
            field.setFrame_(NSMakeRect(x+left,fy+(height-22)/2,w-left-18,22))
        def fragment(row,first):
            """A saved fragment: its title or text first, the time at the right, a quiet way to its source. The whole row opens it."""
            nonlocal y
            RIGHT_W=104;tw_=w-2*PADX-RIGHT_W-12
            if not first:host.addSubview_(kit._KitRule.alloc().initWithFrame_(NSMakeRect(x+PADX,y,w-2*PADX,1)))
            lead=row['title'] or row['text']
            lines=[l for l in str(row['text']).split('\n') if l.strip()][:2] if row['title'] else []
            head_h=min(kit.text_height(lead,15,600 if row['title'] else 500,tw_,True,-0.004,None),57)
            body='\n'.join(lines);bh_=min(kit.text_height(body,13,400,tw_,True,0.004,1.3),36) if body else 0
            h=max(56,10+head_h+(3+bh_ if bh_ else 0)+10)
            if row['citation_id']:
                hit=kit._KitButton.alloc().initWithFrame_callback_(NSMakeRect(x+6,y+2,w-12,h-4),lambda c=row['citation_id']:self.action('source',{'citation_id':c}));hit.radius=10;hit.ax_label='Open source: '+lead[:60];host.addSubview_(hit)
            kit.put_text(host,lead,x+PADX,y+10,tw_,15,600 if row['title'] else 500,height=head_h,track=-0.004)
            if bh_:kit.put_text(host,body,x+PADX,y+10+head_h+3,tw_,13,400,kit.mute(),height=bh_,track=0.004,lead=1.3)
            if row['at']:kit.put_text(host,friendly(row['at']),x+w-PADX-RIGHT_W,y+11,RIGHT_W,12,400,kit.mute(),align=kit.RIGHT,wrap=False,height=18,track=0.01)
            if row['citation_id']:
                sw_=int(kit.text_width('Source',12,600))+30
                sb=kit._KitButton.alloc().initWithFrame_callback_(NSMakeRect(x+w-PADX-sw_,y+h-28,sw_,24),lambda c=row['citation_id']:self.action('source',{'citation_id':c}))
                sb.fill=kit.accent(0.10);sb.radius=8;sb.ax_label='Source for: '+lead[:60];host.addSubview_(sb)
                kit.put_text(sb,'Source  ›',0,0,sw_,12,600,kit.accent_text(),align=kit.CENTER,wrap=False,height=24,middle=True)
            y+=h
        def para(text,size=13,weight=400,color=None,gap=10):
            nonlocal y
            y+=kit.put_text(host,str(text),x,y,w,size,weight,color or kit.mute())+gap
        def error_card(text,at):
            # A raw error line says nothing; say what happened, what is safe, and give a way out.
            nonlocal y
            slow='stopped' in text
            title='Memory stopped' if slow else "Memory couldn't start"
            body='Trace keeps your notes safe on this Mac. Its private memory service is not responding right now. Try again, and if it keeps happening quit and reopen Trace.'
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
            body='Save notes, decisions and browser sources for this project. A folder and VS Code are optional. Trace never scans your folders or imports history.'
            bw=min(w,420);y+=kit.put_text(host,body,cx-bw/2,y,bw,14,400,kit.mute(),align=kit.CENTER)+20
            kit.button(host,'Enable Memory',cx-90,y,180,40,lambda:self.action('enroll',{}),kind='primary',size=15);y+=40
            if view.get('error'):y+=24;error_card(view['error'],y)
        elif self.settings:self._settings(dict(view,binding=binding),x,w,kit,rgb,panel)
        else:
            if view.get('error'):error_card(view['error'],y)
            if view.get('notice'):para(view['notice'][0],13,500,kit.status_text('orange') if view['notice'][1] else kit.mute())
            field_box(self.query,y,left=46,height=48)                                  # search is the anchor of the page
            glyph=kit.icon('magnifyingglass',17,kit.mute(),(x+16,y+15,18,18))
            if glyph is not None:host.addSubview_(glyph)
            y+=48+14
            # one quiet controls line: time range at the left; Ask Codex, Refresh and the state at the right
            px=x;items=[(PERIOD_LABELS.get(period,period),period==view['period'],period) for period in view['periods']]
            widths=[int(kit.text_width(l,13,600))+28 for l,_o,_p in items]
            track=kit._KitSurface.alloc().initWithFrame_fill_edge_radius_(NSMakeRect(x,y,sum(widths)+4,BTN_H),kit.faint(0.06),kit.faint(0.12),10);host.addSubview_(track)
            sx=x+2
            for (l,on,period),sw in zip(items,widths):
                seg=kit._KitButton.alloc().initWithFrame_callback_(NSMakeRect(sx,y+2,sw,BTN_H-4),lambda p=period:self.action('time_filter',{'period':p}))
                seg.radius=8;seg.ax_label=l+(', selected' if on else '')
                if on:seg.fill=kit.accent(0.18);seg.border=kit.accent(0.55)
                host.addSubview_(seg)
                kit.put_text(seg,l,14,0,sw-28,13,600 if on else 500,kit.accent_text() if on else kit.mute(),wrap=False,height=BTN_H-4,middle=True);sx+=sw
            right=x+w
            color=state_text(mode,kit);label=STATE_LABELS.get(mode,mode).split(' ·')[0]
            busy=self.refresh_state=='busy';done=self.refresh_state=='done'
            rl='Refreshing…' if busy else 'Updated' if done else 'Refresh'
            rw=int(kit.text_width('Refreshing…',13,600))+BTN_PAD+14
            rb=kit._KitButton.alloc().initWithFrame_callback_(NSMakeRect(right-rw,y,rw,BTN_H),None if busy else self._refresh)
            rb.border=kit.line();rb.radius=9;rb.ax_label=rl;host.addSubview_(rb)
            if busy:rb.setAlphaValue_(0.6)
            tw_=int(kit.text_width(rl,13,600));gl=13+7+tw_;gx=(rw-gl)/2                 # icon and label as one group, centred in the button
            rg=kit.icon('checkmark' if done else 'arrow.clockwise',13,kit.accent_text() if done else kit.mute(),(gx,(BTN_H-13)/2,13,13))
            if rg is not None:rb.addSubview_(rg)
            kit.put_text(rb,rl,gx+20,0,tw_+4,13,600,kit.accent_text() if done else None,wrap=False,height=BTN_H,middle=True);right-=rw+8
            pw=int(kit.text_width(label,12,500))+36
            chip=kit._KitButton.alloc().initWithFrame_callback_(NSMakeRect(right-pw,y+2,pw,28),lambda:self.action('open_status'));chip.radius=9;chip.focusable=True;chip.ax_label='Memory status: '+label;host.addSubview_(chip)
            dot=kit.icon('circle.fill',7,rgb(STATE_COLORS.get(mode,GREEN)),(12,10,8,8))
            if dot is not None:chip.addSubview_(dot)
            kit.put_text(chip,label,24,0,pw-26,12,500,color,wrap=False,height=28,middle=True);right-=pw+8
            if view.get('codex_connected'):
                aw=int(kit.text_width('Ask Codex',13,600))+BTN_PAD
                kit.button(host,'Ask Codex',right-aw,y,aw,BTN_H,lambda:self.action('ask_codex',{'text':str(self.query.stringValue())}),kind='outline',size=13,weight=600)
            y+=BTN_H+18
            ask=view.get('ask')
            if ask:
                if ask['state']=='asking':title,body='Asking Codex…','Reading this project’s notes and files. This takes a few seconds.'
                elif ask['state']=='done':title,body='Codex’s answer'+(' to “'+ask['question']+'”' if ask.get('question') else ''),ask['text']
                else:title,body="Codex couldn't answer",ask.get('message','')
                bh=kit.text_height(body,14,400,w-40);top=y
                tall=(56+BTN_H) if ask['state']!='asking' else 0
                host.addSubview_positioned_relativeTo_(kit._KitSurface.alloc().initWithFrame_fill_edge_radius_(NSMakeRect(x,top,w,bh+tall+60),kit.faint(0.05),kit.faint(0.09),14),-1,None)
                kit.put_text(host,title,x+PADX,top+16,w-2*PADX,15,600,wrap=False,height=20)
                kit.put_text(host,body,x+PADX,top+42,w-2*PADX,14,400,height=bh)
                if ask['state']=='done':kit.put_text(host,'Generated by Codex. Verify its citations; the search cards below may differ.',x+PADX,top+bh+52,w-2*PADX,12,400,kit.mute(),wrap=False,height=16)
                if ask['state']!='asking':kit.button(host,'Close',x+PADX,top+bh+(76 if ask['state']=='done' else 58),84,BTN_H,lambda:self.action('close_ask'),kind='ghost',size=13,weight=600)
                y=top+bh+tall+60+16
            if view['loading']:para('Loading permitted evidence…')
            if view['empty'] and not (view['query'].strip() and view.get('asked')==view['query']):para(view['empty'],14,500)
            previous=None;source=view.get('source');rows=[] if source else evidence_rows(view['result'])
            miss=search_note(view['query'],rows) if view['query'].strip() and view.get('asked')==view['query'] and not view['loading'] and not source else None
            if miss:para(miss,14,500,kit.mute() if rows else kit.status_text('orange'))
            group={'top':None}
            def close_list():
                nonlocal y
                if group['top'] is None:return
                y+=6
                host.addSubview_positioned_relativeTo_(kit._KitSurface.alloc().initWithFrame_fill_edge_radius_(NSMakeRect(x,group['top'],w,y-group['top']),kit.faint(0.05),kit.faint(0.09),14),-1,None)
                group['top']=None;y+=22
            for row in ([] if source else group_evidence(rows)):
                if row['section']!=previous:
                    close_list()
                    title,symbols,color_=SECTIONS.get(row['section'],(row['section'],['circle'],GRAY))
                    if miss and row['section']=='Search results':title='Possible matches'
                    kit.put_text(host,title,x+4,y,w,14,700,kit.mute(),wrap=False,height=20,track=-0.005);y+=28
                    group['top']=y;y+=6;previous=row['section'];first=True
                fragment(row,first);first=False
            close_list()
            if source:
                self.details=self.details if self.detail_for==source.get('citation_id') else False;self.detail_for=source.get('citation_id')
                p=source_presentation(source);pv=source_provenance(source);inner=w-2*PADX;top=y
                LW=76;vw=inner-LW-8
                head=pv['heading'] if pv['heading']!=pv['kind'] else ''          # a note with no title: the kind label already says it
                heading_h=min(kit.text_height(head,17,600,inner-190,True,-0.006,None),46) if head else 0
                facts=[(l,v,min(kit.text_height(v,13,400,vw,True,0.002,1.3),36)) for l,v in pv['facts']]
                dets=[(l,v,min(kit.text_height(v,12,400,vw,True,0.004,1.3),48)) for l,v in pv['details']] if self.details else []
                total=16+18+6+(heading_h+12 if head else 6)+sum(hh+6 for _l,_v,hh in facts)+10+BTN_H+(10+sum(hh+4 for _l,_v,hh in dets) if dets else 0)+16
                host.addSubview_positioned_relativeTo_(kit._KitSurface.alloc().initWithFrame_fill_edge_radius_(NSMakeRect(x,top,w,total),kit.accent(0.05),kit.accent(0.30),14),-1,None)
                kit.put_text(host,pv['kind'],x+PADX,top+16,inner-190,12,600,kit.accent_text(),wrap=False,height=18,track=0.01)
                menu=self._source_menu(source,view)
                rx=x+w-PADX
                if menu:
                    mb=kit.button(host,'\u22EF',rx-BTN_H,top+10,BTN_H,BTN_H,lambda items=menu:self._menu(items),kind='ghost',size=16,weight=700);mb.ax_label='More actions';rx-=BTN_H+4
                kit.button(host,'Close',rx-76,top+10,76,BTN_H,lambda:self.action('close_source'),kind='ghost',size=13,weight=600)
                hy=top+16+18+6
                if head:kit.put_text(host,head,x+PADX,hy,inner-190,17,600,height=heading_h,track=-0.006)
                fy=hy+(heading_h+12 if head else 6)
                for l,v,hh in facts:
                    kit.put_text(host,l,x+PADX,fy+1,LW,12,500,kit.mute(),wrap=False,height=18)
                    kit.put_text(host,v,x+PADX+LW+8,fy,vw,13,400,height=hh,track=0.002,lead=1.3);fy+=hh+6
                by=fy+10;bx=x+PADX
                if pv['url']:
                    ow=int(kit.text_width('Open original',13,600))+BTN_PAD
                    kit.button(host,'Open original',bx,by,ow,BTN_H,lambda c=source['citation_id']:self.action('open_original',{'citation_id':c}),kind='primary',size=13,weight=600);bx+=ow+8
                if p['open_enabled']:
                    fw=int(kit.text_width('Open file',13,600))+BTN_PAD
                    kit.button(host,'Open file',bx,by,fw,BTN_H,lambda c=source['citation_id']:self.action('open_file',{'citation_id':c}),kind='primary' if not pv['url'] else 'outline',size=13,weight=600)
                dl='Hide details' if self.details else 'More details'
                dw=int(kit.text_width(dl,13,600))+BTN_PAD
                kit.button(host,dl,x+w-PADX-dw,by,dw,BTN_H,lambda:self.action('toggle_details'),kind='ghost',size=13,weight=600)
                if dets:
                    dy=by+BTN_H+10
                    host.addSubview_(kit._KitRule.alloc().initWithFrame_(NSMakeRect(x+PADX,dy-5,inner,1)))
                    for l,v,hh in dets:
                        kit.put_text(host,l,x+PADX,dy+1,LW,12,500,kit.mute(),wrap=False,height=18)
                        kit.put_text(host,v,x+PADX+LW+8,dy,vw,12,400,kit.mute(),height=hh,track=0.004,lead=1.3);dy+=hh+4
                y=top+total+22
            # the composer: one quiet row; it opens into a field only when asked for
            if self.composer:
                inner=w-2*PADX;top=y;fh=64
                host.addSubview_positioned_relativeTo_(kit._KitSurface.alloc().initWithFrame_fill_edge_radius_(NSMakeRect(x,top,w,16+22+10+fh+14+BTN_H+16),kit.faint(0.05),kit.faint(0.09),14),-1,None)
                kit.put_text(host,'Add to project memory',x+PADX,top+16,inner,15,600,wrap=False,height=22,track=-0.004)
                fy=top+16+22+10
                box=kit._KitButton.alloc().initWithFrame_callback_(NSMakeRect(x+PADX,fy,inner,fh),None);box.fill=kit.faint(0.06);box.border=kit.line();box.radius=11
                host.addSubview_positioned_relativeTo_(box,-1,self.note)
                self.note.setHidden_(False);self.note.setFrame_(NSMakeRect(x+PADX+12,fy+10,inner-24,fh-20))
                by=fy+fh+14
                sw_=int(kit.text_width('Save note',13,600))+BTN_PAD
                kit.button(host,'Save note',x+PADX,by,sw_,BTN_H,lambda:self._save_note(str(self.note.stringValue())),kind='primary',size=13,weight=600)
                cw_=int(kit.text_width('Cancel',13,600))+BTN_PAD
                kit.button(host,'Cancel',x+PADX+sw_+8,by,cw_,BTN_H,lambda:self.action('toggle_composer'),kind='ghost',size=13,weight=600)
                xw=int(kit.text_width('Save excerpt…',13,600))+BTN_PAD
                kit.button(host,'Save excerpt…',x+w-PADX-xw,by,xw,BTN_H,lambda:self.action('show_excerpt_form'),kind='outline',size=13,weight=600)
                y=by+BTN_H+16+22
            else:
                host.addSubview_positioned_relativeTo_(kit._KitSurface.alloc().initWithFrame_fill_edge_radius_(NSMakeRect(x,y,w,56),kit.faint(0.05),kit.faint(0.09),14),-1,None)
                kit.put_text(host,'Add to project memory',x+PADX,y,w,15,600,wrap=False,height=56,middle=True,track=-0.004)
                xw=int(kit.text_width('Save excerpt…',13,600))+BTN_PAD;nw=int(kit.text_width('Add note',13,600))+BTN_PAD
                kit.button(host,'Add note',x+w-PADX-nw,y+12,nw,BTN_H,lambda:self.action('toggle_composer'),kind='primary',size=13,weight=600)
                kit.button(host,'Save excerpt…',x+w-PADX-nw-8-xw,y+12,xw,BTN_H,lambda:self.action('show_excerpt_form'),kind='outline',size=13,weight=600)
                y+=56+22
        if view.get('preview') and binding and not self.settings:self.bottom=self._preview(view,x,w,y,kit,panel)
        end=(self.bottom if self.bottom is not None else y)+40
        self.host.setFrame_(NSMakeRect(0,0,width,max(end,500)))
        ask=view.get('ask') or {}
        shown=(self.settings,self.confirm,bool(view.get('preview')),ask.get('state'),bool(view.get('notice')))
        if self.shown_state is not None and shown!=self.shown_state:self._fade_in()      # a state changed: ease it in, never snap
        self.shown_state=shown
        return end
    def _source_menu(self,source,view):
        """The actions that belong to one source: record it as a task or a decision, remove a connection to it, delete the snapshot
        (that last one asks first, in the window)."""
        menu=[]
        if source.get('source_id'):
            span={'source_id':source['source_id'],'start':0,'end':len(source.get('text',''))}
            menu+=[('Record as task','record_claim',dict(span,kind='task')),('Record as decision','record_claim',dict(span,kind='decision'))]
        cid=source.get('citation_id')
        for edge in view.get('related') or []:
            if cid and cid in (edge.get('citation_id'),edge.get('from_citation')):
                menu.append(('Remove connection to \u201c%s\u201d'%short_name(edge.get('title') or 'source',40),'remove_relationship',{'edge_id':edge['id']}))
        if source.get('source_id'):menu.append(('Delete saved snapshot…','forget_source',{'source_id':source['source_id']}))
        return menu
    def _refresh(self):
        """Refresh once at a time: show 'Refreshing…', then a brief 'Updated', then the plain button again."""
        if self.refresh_state=='busy':return
        self.refresh_state='busy';self.action('refresh')
        from Foundation import NSTimer
        def settle(_t):
            self.refresh_state='done';self.action('repaint')
            def reset(_t2):
                self.refresh_state=None;self.action('repaint')
            NSTimer.scheduledTimerWithTimeInterval_repeats_block_(1.6,False,reset)
        NSTimer.scheduledTimerWithTimeInterval_repeats_block_(1.2,False,settle)
        self.action('repaint')
    def _menu(self,items):
        """A native menu of the secondary actions under a setting's ellipsis button."""
        from AppKit import NSMenu,NSMenuItem
        if not hasattr(self,'_pick'):self._pick=_pick_class().alloc().init()
        self._pick.calls={}
        menu=NSMenu.alloc().initWithTitle_('')
        for n,(label,action,*rest) in enumerate(items):
            item=NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(label,'pick:','');item.setTarget_(self._pick);item.setTag_(n)
            self._pick.calls[n]=(lambda a=action,r=rest:self.action(a,r[0]) if r else self.action(a));menu.addItem_(item)
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
                kit.button(self.host,'Copy command',x+16,by,130,32,lambda:self.action('copy_codex_command'),kind='primary',size=13,weight=600)
                kit.button(self.host,'Done',x+154,by,80,32,lambda:self.action('close_preview'),kind='ghost',size=13,weight=600)
            elif state=='added':
                kit.button(self.host,'Done',x+16,by,80,32,lambda:self.action('close_preview'),kind='primary',size=13,weight=600)
            return by+30+16
        title='Proposed context for Codex · no access granted' if preview.get('proposed') else 'Exact context preview for Codex'
        body=json.dumps(preview,ensure_ascii=False,indent=2);th=kit.text_height(body,12,400,w-32)
        y+=12;y+=kit.put_text(self.host,title,x,y,w,14,700,height=20)+6
        panel(x,y,w,th+24);kit.put_text(self.host,body,x+16,y+12,w-32,12,400,height=th);y+=th+36
        kit.button(self.host,'Copy context preview',x,y,200,32,lambda:self.action('copy_preview'),kind='outline',size=13,weight=600)
        kit.button(self.host,'Close',x+208,y,80,32,lambda:self.action('close_preview'),kind='ghost',size=13,weight=600)
        return y+46
    def _settings(self,view,x,w,kit,rgb,panel):
        """Memory settings. One button system: BTN_H tall, text + BTN_PAD wide, primary (blue), secondary (outline), tertiary
        (ghost) and destructive (red outline). Wide windows put Capture on the left and Agents and Danger zone on the right so
        the whole page fits without scrolling; narrow ones stack them and scroll."""
        from AppKit import NSMakeRect
        ACTION_W=148                                  # the action column's fixed width
        GUTTER=20                                             # between the two columns
        SLOT,ICON,ICON_GAP,TEXT_GAP=BTN_H+2,16,12,14          # ellipsis slot, icon size, icon to text, text column to action column
        PAD_Y,LINE_GAP,MIN_ROW,PROGRESS_H=8,2,50,12            # inside a row: top and bottom, between lines, shortest row, room for a bar
        LABEL_GAP,SECTION_GAP=6,20                            # a section label to its first row, one container to the next
        host=self.host;y=8;notice=view.get('notice')
        X0,W0=x,w
        y+=4
        kit.put_text(host,'Memory settings',x,y,w,24,700,height=32,track=-0.022);y+=44
        def button(label,bx,by,call,kind='secondary',enabled=True,width=None):
            width=width or int(kit.text_width(label,13,600))+BTN_PAD
            kit.button(host,label,bx,by,width,BTN_H,call,kind=KINDS[kind],size=13,weight=600,enabled=enabled);return width
        if not (view.get('binding') or {}).get('active',True):
            y+=kit.put_text(host,'Memory is disconnected. Its retained snapshots remain on this Mac. Re-enable it to search, or delete retained memory before deleting the project.',x,y,w,14,400,kit.mute())+20
            button('Enable Memory',x,y,lambda:self.action('enroll',{}),'primary');y+=BTN_H+12
            button('Delete retained memory…',x,y,lambda:self.action('confirm_delete_memory'),'destructive')
            self.bottom=y+BTN_H+28;return
        if view.get('preview'):y=self._preview(view,x,w,y,kit,panel)+8
        mode=view['state']
        if mode=='Needs attention':
            st=view.get('status',{});detail=view.get('message') or ''
            facts=['Waiting: %s'%st.get('pending',0)]
            if st.get('failed'):facts.append('Failed: %s'%st['failed'])
            if st.get('indexed_at'):facts.append('Last indexed '+friendly(st['indexed_at']))
            facts=' · '.join(facts);dh=kit.text_height(detail,13,400,w-32);top=y
            panel(x,top,w,dh+92,ORANGE)
            kit.put_text(host,'What needs attention',x+16,top+12,w-32,14,700,wrap=False,height=20)
            kit.put_text(host,detail,x+16,top+36,w-32,13,400,kit.status_text('orange'),height=dh)
            kit.put_text(host,facts,x+16,top+dh+42,w-32,11,400,kit.mute(),wrap=False,height=16)
            bw=button('Try again',x+16,top+dh+54,lambda:self.action('resume'),'primary')
            button('Recover notes',x+16+bw+8,top+dh+54,lambda:self.action('recover_native_notes'),'secondary')
            y=top+dh+92+14
        group={'top':None,'danger':False,'rows':0,'surface':False,'subs':0}
        def end_group():
            """Every section is one soft container with fine dividers between its rows."""
            nonlocal y
            if group['top'] is None:return
            y+=10                                                 # one container geometry for every section; Danger zone is darker and edged in muted red
            fill=rgb((0,0,0),0.30) if group['danger'] else kit.faint(0.05)
            edge=rgb(RED,0.26) if group['danger'] else kit.faint(0.09)
            surface=kit._KitSurface.alloc().initWithFrame_fill_edge_radius_(NSMakeRect(x,group['top'],w,y-group['top']),fill,edge,14)
            host.addSubview_positioned_relativeTo_(surface,-1,None)
            group['top']=None;y+=SECTION_GAP
        def confirm_open(title,detail,go,destructive,call_yes):
            """A confirmation is the setting's own expanded state: an inset tonal panel under the row, same material and
            radius as the surfaces around it, a red edge mark only when it is destructive, the same buttons."""
            nonlocal y
            inner=w-2*PADX-16-12;dh=kit.text_height(detail,13,400,inner,True,0.004,1.3);ph=16+20+6+dh+14+BTN_H+16;top=y+4
            host.addSubview_(kit._KitSurface.alloc().initWithFrame_fill_edge_radius_(NSMakeRect(x+PADX-4,top,w-2*PADX+8,ph),(rgb((0,0,0),0.24) if destructive else kit.faint(0.06)),None,12))
            if destructive:
                bar=kit._KitButton.alloc().initWithFrame_callback_(NSMakeRect(x+PADX-4,top+14,3,ph-28),None);bar.fill=rgb(RED);bar.radius=1.5;host.addSubview_(bar)
            kit.put_text(host,title,x+PADX+12,top+16,inner,15,600,wrap=False,height=20)
            kit.put_text(host,detail,x+PADX+12,top+42,inner,13,400,kit.mute(),height=dh,track=0.004,lead=1.3)
            by=top+42+dh+14;gw=button(go,x+PADX+12,by,call_yes,'destructive' if destructive else 'primary')
            button('Cancel',x+PADX+12+gw+8,by,lambda:self.action('confirm_no'),'tertiary')
            y=top+ph+8
        def row(symbols,title,state,buttons,state_color=None,dot=False,more=(),warn=None,tip=None,progress=False,fraction=None,danger=False):
            """One grid for every setting. Left: icon, then a text column (name, state, note) that wraps inside its own width.
            Right: the action column, ACTION_W wide on every row, and the ellipsis slot outside it. The text column ends before
            the action column, so text can never reach a button; a longer text makes the row taller instead."""
            nonlocal y
            if group['rows']:host.addSubview_(kit._KitRule.alloc().initWithFrame_(NSMakeRect(x+PADX,y,w-2*PADX,1)))
            group['rows']+=1
            btn=[(b[0],b[1],b[2],b[3] if len(b)>3 else True) for b in buttons]
            action_x=x+w-PADX-SLOT-ACTION_W                       # where every row's action column begins
            tx=x+PADX+ICON+ICON_GAP;text_w=action_x-TEXT_GAP-tx
            state_text_=('\u25CF '+state) if dot and state else state
            th=kit.text_height(title,15,600,text_w,True,-0.004,None)
            sh=kit.text_height(state_text_,13,400,text_w,True,0.004,1.3) if state_text_ else 0
            wh=kit.text_height(warn,13,400,text_w,True,0.004,1.3) if warn else 0
            content=th+(LINE_GAP+sh if sh else 0)+(LINE_GAP+wh if wh else 0)+(PROGRESS_H if progress else 0)
            h=max(MIN_ROW,PAD_Y+content+PAD_Y)
            cy=y+(min(h,MIN_ROW+PROGRESS_H*bool(progress))-BTN_H)/2        # actions sit level with the first line, however tall the row grows
            kit.glyph(host,symbols,x+PADX,y+PAD_Y+(15-ICON)/2+2,ICON,rgb(RED,0.85) if danger else kit.faint(0.55))
            ty=y+PAD_Y
            kit.put_text(host,title,tx,ty,text_w,15,600,height=th,track=-0.004);ty+=th
            if sh:ty+=LINE_GAP;kit.put_text(host,state_text_,tx,ty,text_w,13,400,state_color or kit.mute(),height=sh,track=0.004,lead=1.3);ty+=sh
            if wh:ty+=LINE_GAP;kit.put_text(host,warn,tx,ty,text_w,13,400,kit.status_text('orange'),height=wh,track=0.004,lead=1.3);ty+=wh
            if progress:
                bw_=min(text_w,300);by=ty+6;track=kit._KitButton.alloc().initWithFrame_callback_(NSMakeRect(tx,by,bw_,4),None);track.fill=kit.faint(0.10);track.radius=2;host.addSubview_(track)
                fw=bw_*(fraction or 0)
                if fw>0:fill=kit._KitButton.alloc().initWithFrame_callback_(NSMakeRect(tx,by,max(fw,4),4),None);fill.fill=rgb(ACC);fill.radius=2;host.addSubview_(fill)
            if tip:
                hover=kit._KitButton.alloc().initWithFrame_callback_(NSMakeRect(tx,y,text_w,h),None);hover.setToolTip_(tip);host.addSubview_(hover)
            if more:
                opts=list(more);mb=kit.button(host,'\u22EF',x+w-PADX-BTN_H,cy,BTN_H,BTN_H,lambda items=opts:self._menu(items),kind='ghost',size=16,weight=700);mb.ax_label='More actions'
            bx=action_x+ACTION_W
            for item in reversed(btn):
                label,action,kind,on=item;bw=max(ACTION_W,int(kit.text_width(label,13,600))+28);bx-=bw
                button(label,bx,cy,(lambda a=action:self.action(a)),kind,on,bw);bx-=8
            y+=h
            if self.confirm in CONFIRMS and title==ASKED.get(self.confirm):
                ctitle,detail,go=CONFIRMS[self.confirm];confirm_open(ctitle,detail,go,self.confirm=='confirm_disconnect',lambda:self.action('confirm_yes'))
        def sub(text):
            nonlocal y
            if group['subs']:host.addSubview_(kit._KitRule.alloc().initWithFrame_(NSMakeRect(x+PADX,y+4,w-2*PADX,1)))
            group['subs']+=1
            y+=8;kit.put_text(host,text,x+PADX,y,w,12,600,kit.mute(),wrap=False,height=16,track=0.02);y+=16+LABEL_GAP
            group['rows']=0
        def cap(text,danger=False,surface=False):
            nonlocal y
            end_group()
            kit.put_text(host,text,x,y,w,15,700,kit.status_text('red') if danger else None,wrap=False,height=22,track=-0.01);y+=30
            group['top']=y;y+=10;group['rows']=0;group['subs']=0;group['danger']=danger;group['surface']=surface
        def note_for(kind,default):
            if notice and len(notice)>2 and notice[2]==kind:return notice[0],(kit.status_text('orange') if notice[1] else None)
            return default,None
        two=W0>=740                                              # wide enough for two columns
        cw=int((W0-GUTTER)/2) if two else W0
        top_y=y;x,w=X0,cw
        root=(view.get('binding') or {}).get('workspace_root')
        status=view.get('status',{})
        cap('Capture',surface=True)
        sub('Storage')
        row(['folder'],'Project folder',(STATE_LABELS.get(mode,mode).split(' \u00b7')[0])+(' \u00b7 '+short_name(root,28) if root else ''),
            [('Resume' if mode=='Paused' else 'Pause','resume' if mode=='Paused' else 'pause','secondary')],state_color=state_text(mode,kit),dot=True)
        if not root:
            row(['folder.badge.plus','folder'],'Optional workspace','Not connected',[('Choose folder…','choose_root','secondary')],dot=True,tip='Connect a folder for VS Code capture. Notes and browser saves work without one.')
        sub('Connected apps')
        paired=status.get('browser_paired')
        row(['globe'],'Chrome','Paired' if paired else 'Not connected',
            [('Disconnect','revoke_browser','secondary')] if paired else [('Connect','install_browser','primary')],
            state_color=kit.status_text('green') if paired else None,dot=True,
            more=[('Show extension folder','open_browser_extension')],
            tip='Pin the Trace extension in Chrome and save selected text with Control + Shift + S. Saved sources stay when you disconnect. No background browsing is collected.' if paired else 'Connect Chrome, then load the bundled Trace extension from its installation folder. No background browsing is collected.')
        installed=view.get('extension_installed');kind=notice[2] if notice and len(notice)>2 else None
        busy=bool(notice and kind in ('install_extension','connect_extension') and notice[0].endswith('…'))
        said=notice[0] if notice and kind in ('install_extension','connect_extension') else None
        connected=status.get('source_connected');blind=view.get('accessibility') is False
        icons=['chevron.left.forwardslash.chevron.right','curlybraces']
        if connected:
            row(icons,'VS Code','Connected, paused' if mode=='Paused' else 'Connected',[],state_color=kit.status_text('green'),dot=True,
                warn=('Nothing kept from this file. '+status['capture_blocker']) if status.get('capture_blocker') else None,tip='Shares the text you view in the editor.')
        elif installed and not busy:
            row(icons,'VS Code',said or ('Needs accessibility' if blind else 'Installed'),
                [('Allow Accessibility','allow_accessibility','primary')] if blind else [('Connect','reconnect_extension','primary')],
                dot=not said,state_color=kit.status_text('orange') if (notice and notice[1]) or blind else kit.status_text('green') if not said else None,
                tip='Press Allow, switch Trace on in the list, then come back.' if blind else 'Open this project in standard VS Code, then connect. Your editor windows stay open.')
        else:
            row(icons,'VS Code','Installing in VS Code…' if busy and kind=='install_extension' else (said or 'Not installed'),
                [('Working…' if busy else 'Install','install_extension','secondary' if busy else 'primary',not busy)],
                dot=not (busy or said),state_color=kit.status_text('orange') if notice and notice[1] else None,tip='Shares the text you view in the editor.')
        sub('Intelligence')
        ready=status.get('model_state')=='available';state=status.get('model_install')
        starting=bool(notice and len(notice)>2 and notice[2]=='install_model' and not notice[1])
        model_tip='A 46 MB model that stays on this Mac. Finds notes by meaning, not just by words. Needs Ollama installed first.'
        if state=='installing' or starting:
            percent=status.get('model_install_percent');secs=status.get('model_install_seconds') or 0
            text='Starting the download…' if state!='installing' else ('Downloading… %d%%'%percent if percent else 'Starting the download…')+' \u00b7 %d:%02d'%(secs//60,secs%60)
            row(['sparkles'],'Meaning search',text,[('Installing…','confirm_model','secondary',False)],progress=True,fraction=(percent or 0)/100,
                warn='Taking longer than usual. Check your connection.' if secs>180 else None,tip=model_tip)
        elif state=='failed':
            row(['sparkles'],'Meaning search',status.get('model_install_message') or 'The install failed.',[('Try again','confirm_model','primary')],state_color=kit.status_text('orange'),tip=model_tip)
        else:
            text,color=note_for('install_model','On' if ready else 'Not installed')
            row(['sparkles'],'Meaning search',text,[] if ready else [('Install','confirm_model','primary')],state_color=color or (kit.status_text('green') if ready else None),dot=not color,tip=model_tip)
        end_group();left_bottom=y
        if two:x,w=X0+cw+GUTTER,cw;y=top_y
        cap('Agents')
        connected_codex=view.get('codex_connected')
        row(['terminal'],'Codex','Connected' if connected_codex else 'Not connected',[('Preview','preview','secondary')] if connected_codex else [('Connect…','confirm_agent','primary')],
            state_color=kit.status_text('green') if connected_codex else None,dot=True,
            more=[('Copy prompt','copy_codex_prompt'),('Revoke access','revoke_agent')] if connected_codex else [],
            tip='Codex can search this project’s notes and sources and read the cited text.')
        end_group()
        cap('Danger zone',True)
        row(['bolt.slash'],'Disconnect Memory','Stops capture and agent access. Notes stay on this Mac.',[('Disconnect…','confirm_disconnect','destructive')],danger=True)
        row(['trash'],'Delete retained memory','Removes retained snapshots, graph and agent access. Original files stay.',[('Delete…','confirm_delete_memory','destructive')],danger=True)
        end_group()
        self.bottom=max(left_bottom,y) if two else y
