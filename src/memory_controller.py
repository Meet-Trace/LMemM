"""Main-thread adapter between pure Memory navigation and asynchronous IPC."""
import time
from memory_model import new_memory_state,memory_reduce,memory_view,period_args

def codex_binary():
    """The `codex` program, found the way a person's shell would (an app launched from the Dock has a short PATH)."""
    import os,shutil
    if os.environ.get('LMEMM_NO_CODEX'):return None                  # tests must never touch a real Codex
    home=os.path.expanduser('~')
    extra=['/opt/homebrew/bin','/usr/local/bin',home+'/.local/bin',home+'/.npm-global/bin',home+'/.bun/bin']
    found=shutil.which('codex',path=os.pathsep.join([os.environ.get('PATH','')]+extra))
    if found:return found
    for app in ('/Applications/ChatGPT.app','/Applications/Codex.app',home+'/Applications/ChatGPT.app',home+'/Applications/Codex.app'):          # the desktop app carries its own copy
        for inside in ('Contents/Resources/codex-cli/bin/codex','Contents/Resources/codex','Contents/Resources/bin/codex','Contents/MacOS/codex','Contents/Resources/app/bin/codex'):
            path=os.path.join(app,inside)
            if os.path.isfile(path) and os.access(path,os.X_OK):return path
    return None

def codex_server_name(config_path):
    import hashlib
    return 'lmemm_memory_'+hashlib.sha256(str(config_path).encode()).hexdigest()[:12]

def codex_args(config_path):
    """The program and arguments that add LMemM's memory to Codex."""
    import sys
    from pathlib import Path
    from semantic_memory.runtime_environment import standalone_resources,worker_argv
    if standalone_resources():return ['mcp','add',codex_server_name(config_path),'--',*worker_argv('mcp',['--config',str(config_path)])]
    adapter=Path(__file__).resolve().parent.parent/'scripts'/'project_memory_mcp.py'
    venv=adapter.parent.parent/'.venv'/'bin'/'python'      # the repo's own Python has the MCP package; the app's interpreter may not
    python=str(venv) if venv.exists() else sys.executable
    return ['mcp','add',codex_server_name(config_path),'--',python,str(adapter),'--config',str(config_path)]

def codex_command(config_path):
    """The same thing as one line to paste in Terminal: the fallback when LMemM cannot add itself."""
    import shlex
    return ' '.join(['codex']+[shlex.quote(a) if a!='--' else a for a in codex_args(config_path)])

def codex_exec_args(binary,config_path,prompt,folder):
    """Use this grant even when the persisted Codex registration is stale."""
    import json
    server=codex_server_name(config_path);adapter=codex_args(config_path)[4:]
    return [binary,'exec','--ephemeral','--sandbox','read-only','--skip-git-repo-check','-C',str(folder),
            '-c','mcp_servers.'+server+'.command='+json.dumps(adapter[0]),
            '-c','mcp_servers.'+server+'.args='+json.dumps(adapter[1:]),'--json',prompt]

def codex_question(project_id,question,now=None,server_name="lmemm_memory",bounds=None):
    """The words sent to Codex: use only LMemM's tools, and explain, do not just quote."""
    from datetime import datetime,timedelta,timezone
    now=now or datetime.now(timezone.utc);fmt='%Y-%m-%dT%H:%M:%SZ'
    question=(question or '').strip() or 'What was I working on? What is still open, and what did I decide?'
    return ('Use only the '+server_name+' tools. Call get_project_context for project_id %s with start %s and end %s'
            ' (and search_project_memory if the question names something specific). '
            'Then answer this question for the person who works on this project: %s\n'
            'Explain what the code or notes are for and why they matter, in plain language. Do not paste code. '
            'Name the files you mean and cite the exact citation IDs returned by the tools for factual claims. '
            'Keep it under 150 words, and say so if the memory does not contain the answer.')%(
            project_id,(bounds or {}).get('start',(now-timedelta(days=30)).strftime(fmt)),
            (bounds or {}).get('end',now.strftime(fmt)),question)

def codex_answer(stdout):
    """The last message Codex wrote, from its --json output; None when there was none."""
    import json
    answer=None
    for line in str(stdout).splitlines():
        try:event=json.loads(line)
        except ValueError:continue
        item=event.get('item') or {}
        if event.get('type')=='item.completed' and item.get('type')=='agent_message' and item.get('text'):answer=item['text']
    return answer

class MemoryController:
    def __init__(self,client,items,save):
        self.client=client;self.items=items;self.save=save;self.state=new_memory_state()
        self.bindings=[];self.status={};self.pending={};self.serial=0;self.last_poll=0;self.ui_project=None
        import threading
        self.codex_lock=threading.Lock();self.codex_states={};self.codex_removed=set();self.ask_serial=0
        self.agents={};self.codex=None;self.ask=None;self.ax_trusted=True;self.busy=None;self.notice=None;self.notice_at=0;self.notice_version=0;self.seen_notice=0;self.vscode_restart=False
    @property
    def agent(self):
        return next((a for a in self.agents.values() if self.binding and self.binding['project_id'] in a['project_ids'] and a.get('client_id','Codex')=='Codex'),None)
    @property
    def binding(self):
        return next((b for b in self.bindings if b['ui_project_id']==self.ui_project and b['active']),None)
    @property
    def retained_binding(self):
        return next((b for b in self.bindings if b['ui_project_id']==self.ui_project),None)
    def send(self,method,params,kind,token=None):
        self.serial+=1;rid='memory-ui-'+str(self.serial)
        self.pending[rid]=(kind,token);self.client().request(method,params,rid)
    def select(self,ui_project):
        changed=self.ui_project!=ui_project;self.ui_project=ui_project
        if changed:self.ask_serial+=1;self.ask=None
        self.state=memory_reduce(self.state,'select_project',{'project_id':ui_project})
        if changed and self.binding and self.state['tab'] in {'Memory','Overview'}:self.action('submit',{})
    def set_notice(self,text,error=False,kind=None):
        self.notice=(text,error,kind) if text else None;self.notice_version+=1;self.notice_at=time.monotonic()
    def install_extension(self):
        # Built and installed by the app itself: it touches no memory data, and the service
        # hides error details from the app, which would hide why an install failed.
        import threading
        from pathlib import Path
        self.set_notice('Installing…',False,'install_extension')
        directory=Path(self.client().directory).parent
        def work():
            try:
                from semantic_memory.model_setup import install_extension,vscode_running
                install_extension(directory);self.vscode_restart=vscode_running()
                self.set_notice('Installed. In VS Code run Developer: Reload Window once, then press Connect.' if self.vscode_restart else 'Extension installed. Press Connect to continue.',False,'install_extension')
            except ValueError as error:self.set_notice(str(error),True,'install_extension')
            except Exception:self.set_notice('Could not install the VS Code extension.',True,'install_extension')
        threading.Thread(target=work,daemon=True).start()
    def connect_extension(self):
        # Open the connection URI without closing unrelated editor windows.
        import subprocess,threading,urllib.parse
        from pathlib import Path
        if self.status.get('state')=='Paused':
            self.set_notice('Memory is paused. Resume it in the Folder row above, then press Connect.',True,'connect_extension');return
        self.set_notice('Connecting…',False,'connect_extension')
        grant=str(Path(self.client().directory)/'bridge'/'grant.json')
        def work():
            try:
                subprocess.run(['pbcopy'],input=grant.encode(),timeout=5)
                self.vscode_restart=False
                subprocess.run(['open','vscode://lmemm.lmemm-source/connect?grant='+urllib.parse.quote(grant)],timeout=10,check=True)
                self.set_notice('Opened VS Code. Choose Allow there to finish.',False,'connect_extension')
            except ValueError as error:self.set_notice(str(error),True,'connect_extension')
            except Exception:self.set_notice('Could not open VS Code. The path is copied: in VS Code run “LMemM: Connect Project Memory” and paste it.',True,'connect_extension')
        threading.Thread(target=work,name="memory-connect",daemon=True).start()
    @staticmethod
    def _accessibility():
        try:
            import permissions
            return bool(permissions.accessibility_trusted())
        except Exception:return True                 # unknown is not "off": never block on a guess
    def ask_codex(self,question):
        import subprocess,tempfile,threading
        if not (self.binding and self.agent):return
        project=self.binding['project_id'];grant=self.agent['grant_id']
        config=self.agent.get('config_path')
        if not config:return
        prompt=codex_question(project,question,server_name=codex_server_name(config),bounds=period_args(self.state['period']))
        self.ask_serial+=1;serial=self.ask_serial
        question=(question or '').strip()
        base={'question':question,'project_id':project,'serial':serial,'grant_id':grant}
        self.ask=dict(base,state='asking');self._touch()
        def work():
            try:
                binary=codex_binary()
                if not binary:raise FileNotFoundError
                with tempfile.TemporaryDirectory() as folder:
                    done=subprocess.run(codex_exec_args(binary,config,prompt,folder),capture_output=True,text=True,timeout=180)
                if done.returncode:raise RuntimeError('Codex could not complete the request. Check sign-in and the project connection.')
                answer=codex_answer(done.stdout)
                if not answer:raise RuntimeError('Codex did not answer. Check that it is signed in.')
                result=dict(base,state='done',text=answer)
            except FileNotFoundError:result=dict(base,state='failed',message='Codex was not found on this Mac.')
            except subprocess.TimeoutExpired:result=dict(base,state='failed',message='Codex took too long. Try again.')
            except Exception:result=dict(base,state='failed',message='Codex could not answer. Check sign-in and the project connection.')
            if self.ask_serial==serial and self.binding and self.binding['project_id']==project and self.agent and self.agent['grant_id']==grant:
                self.ask=result;self._touch()
        threading.Thread(target=work,name='memory-ask',daemon=True).start()
    def _touch(self):self.notice_version+=1
    def add_to_codex(self,config_path):
        import subprocess,threading
        name=codex_server_name(config_path)
        self.codex_states[name]={'state':'adding'};self._touch()
        def work():
            with self.codex_lock:
                if name in self.codex_removed:return
                try:
                    binary=codex_binary()
                    if not binary:raise FileNotFoundError
                    done=subprocess.run([binary]+codex_args(config_path),capture_output=True,text=True,timeout=30)
                    if done.returncode:raise RuntimeError
                    result={'state':'added'}
                except FileNotFoundError:result={'state':'failed','message':'Codex was not found on this Mac.'}
                except Exception:result={'state':'failed','message':'Codex could not add the project connection. Use the command below to retry.'}
                if name not in self.codex_removed:self.codex_states[name]=result;self._touch()
        threading.Thread(target=work,name='memory-codex-add',daemon=True).start()
    def remove_from_codex(self,connection):
        import subprocess,threading
        config=connection.get('config_path')
        if not config:return
        name=codex_server_name(config);self.codex_removed.add(name);self.codex_states.pop(name,None)
        def work():
            with self.codex_lock:
                try:
                    binary=codex_binary()
                    if binary:subprocess.run([binary,'mcp','remove',name],capture_output=True,timeout=30)
                except Exception:pass
        threading.Thread(target=work,name='memory-codex-remove',daemon=True).start()
    def view(self):
        status=dict(self.status) if self.binding else {'state':'Disabled'}
        if self.binding and 'source_connected_project_ids' in status:
            status['source_connected']=self.binding['project_id'] in status['source_connected_project_ids']
        view=memory_view(self.state,self.binding,status,self.state['result'])
        view['notice']=self.notice;view['accessibility']=self.ax_trusted;view['codex_connected']=bool(self.binding and self.agent)
        view['last_completed']=getattr(self,'last_completed',{}).get(self.ui_project)
        view['related']=getattr(self,'related',{}).get(self.ui_project,[])
        view['retained_binding']=self.retained_binding
        view['ask']=self.ask if self.ask and self.binding and self.agent and self.ask.get('grant_id')==self.agent['grant_id'] and self.ask.get('project_id')==self.binding['project_id'] and self.ask.get('serial')==self.ask_serial else None
        connection=(view.get('preview') or {}).get('connection')
        if connection:connection['codex']=self.codex_states.get(codex_server_name(connection['config_path']),{})
        if self.busy:view['busy']=self.busy
        try:
            from semantic_memory.model_setup import extension_installed
            view['extension_installed']=extension_installed()
        except Exception:view['extension_installed']=False
        return view
    def action(self,action,payload=None):
        payload=payload or {}
        if action=='time_filter':self.ask_serial+=1;self.ask=None
        if action=='enroll':self.busy='Enabling Memory…';self.send('enroll',{'ui_project_id':self.ui_project,'root':payload.get('root')},'enroll');return
        if action=='delete_project_memory':
            binding=self.retained_binding
            if not binding:raise ValueError('Project memory unavailable')
            self.ask_serial+=1;self.ask=None
            for connection in list(self.agents.values()):
                if binding['project_id'] in connection['project_ids']:self.remove_from_codex(connection)
            self.send(action,{'project_id':binding['project_id']},'purge',{'project_id':self.ui_project})
            self.state=memory_reduce(self.state,'revoke',{});return
        if action in {'save_excerpt','record_claim','remove_relationship','set_task_state','delete_saved_source'}:
            if not self.binding:raise ValueError('Enable Memory first')
            data=dict(payload)
            if action=='set_task_state' and not payload.get('done'):getattr(self,'last_completed',{}).pop(self.ui_project,None)        # undone
            if action=='set_task_state' and payload.get('done'):
                if not hasattr(self,'last_completed'):self.last_completed={}
                self.last_completed[self.ui_project]=(action,dict(payload,done=False))
            if action not in {'delete_saved_source'}:data['project_id']=self.binding['project_id']
            self.send(action,data,'mutation');return
        if action=='complete_native_note':
            import notes
            for item in self.items().values():
                for note in item.get('notes',[]):
                    if note.get('memory_id')==payload['note_id'] and note.get('memory_project_id')==self.ui_project:
                        if not payload['done']:getattr(self,'last_completed',{}).pop(self.ui_project,None)
                        if payload['done']:
                            if not hasattr(self,'last_completed'):self.last_completed={}
                            self.last_completed[self.ui_project]=(action,dict(payload,done=False))
                        notes.set_done(self.items(),[notes.note_id(note)],payload['done']);self.save();self.action('submit',{});return
            raise ValueError('Project note unavailable')
        if action=='copy_context':
            if self.binding:self.send('export_project_context',{'project_id':self.binding['project_id']},'copy_context',{'project_id':self.ui_project})
            return
        if action=='install_extension':self.install_extension();return
        if action=='open_browser_extension':
            from pathlib import Path
            from semantic_memory.runtime_environment import standalone_resources
            from AppKit import NSWorkspace
            from Foundation import NSURL
            directory=(standalone_resources() or Path(__file__).resolve().parents[1])/'extensions/lmemm-browser'
            NSWorkspace.sharedWorkspace().openURL_(NSURL.fileURLWithPath_(str(directory)))
            return
        if action=='reconnect_extension':self.connect_extension();return
        if action=='install_model':self.set_notice('Starting…',False,'install_model')
        if action in {'pause','resume','install_model','recover_native_notes','install_browser','revoke_browser'}:self.send(action,{},action);return
        if action=='refresh':
            retry=getattr(self.client(),'retry',None)
            if retry:retry()
            self.last_poll=0;self.action('submit',{});return
        if action=='note':
            if not self.binding:raise ValueError('Enable Memory first')
            from memory_notes import add_project_note
            add_project_note(self.items(),self.binding,payload['text']);self.save();self.status={'state':'Processing'};return
        if action=='disconnect':
            self.ask_serial+=1;self.ask=None
            for connection in list(self.agents.values()):
                if self.binding and self.binding['project_id'] in connection['project_ids']:self.remove_from_codex(connection)
            if self.binding:self.send('revoke_project',{'project_id':self.binding['project_id']},'disconnect')
            self.state=memory_reduce(self.state,'revoke',{});return
        if action=='open_file':
            if self.binding:self.send('resolve_citation',{'project_id':self.binding['project_id'],'citation_id':payload['citation_id']},'open',{'project_id':self.ui_project})
            return
        if action=='open_original':
            if self.binding:self.send('resolve_citation',{'project_id':self.binding['project_id'],'citation_id':payload['citation_id']},'open_original',{'project_id':self.ui_project})
            return
        if action=='close_source':
            self.state['source']=None
            if self.binding:self.action('submit',{})        # opened from Overview: reload the Memory list
            return
        if action=='close_preview':self.state['preview']=None;return
        if action=='ask_codex':self.ask_codex(payload.get('text'));return
        if action=='close_ask':self.ask_serial+=1;self.ask=None;return
        if action=='connect_agent':
            if not self.binding:raise ValueError('Enable Memory first')
            from semantic_memory.intentional_sources import NATIVE_APP,BROWSER_APP,BROWSER_SCOPE
            self.send('connect_agent',dict(client_id='Codex',project_ids=[self.binding['project_id']],app_ids=['com.microsoft.VSCode',NATIVE_APP,BROWSER_APP],origins=[BROWSER_SCOPE],disclosure_approved=payload.get('approved') is True),'agent');return
        if action=='revoke_agent':
            for grant_id,connection in list(self.agents.items()):
                if self.binding and self.binding['project_id'] in connection['project_ids'] and connection.get('client_id','Codex')=='Codex':
                    self.send('revoke_agent',{'grant_id':grant_id},'revoke_agent');self.agents.pop(grant_id,None);self.remove_from_codex(connection);self.ask_serial+=1;self.ask=None
            return
        self.state=memory_reduce(self.state,action,payload)
        if action=='tab' and payload['tab'] in {'Memory','Overview'}:self.last_poll=0;self.action('submit',{})
        elif action=='time_filter':self.action('submit',{})
        elif action in {'submit','source','preview'} and self.binding:
            params={'project_id':self.binding['project_id']}
            if action=='source':method='resolve_citation';params['citation_id']=payload['citation_id']
            else:
                params.update(period_args(self.state['period']))
                method='get_project_context'
                if action=='submit' and self.state['tab']=='Overview':method='resume_project';params={'project_id':self.binding['project_id']}
                elif action=='submit' and self.state['query'].strip():method='search_project_memory';params['query']=self.state['query'].strip()
                if action=='preview':
                    method='preview_agent_context';params['client_id']='Codex'
                    if self.agent:params['grant_id']=self.agent['grant_id']
            self.send(method,params,'read',self.state['request'])
    def poll(self,events=None):
        changed=False
        trusted=self._accessibility()
        if trusted!=self.ax_trusted:self.ax_trusted=trusted;changed=True        # the person just switched it on in System Settings
        for event in self.client().poll() if events is None else events:
            if event.get('type')=='ready':
                self.ask_serial+=1;self.ask=None
                self.busy=None;self.last_poll=0;self.agents={};self.pending={};self.state=memory_reduce(self.state,'cancel',{});changed=True;continue
            if event.get('type')=='error':
                self.busy=None
                self.state=memory_reduce(self.state,'cancel',{});self.state['error']=event['error'];changed=True;continue
            entry=self.pending.pop(event.get('id'),None)
            if not entry:continue
            kind,token=entry;changed=True
            if kind=='enroll':self.busy=None
            if kind=='read':
                self.state=memory_reduce(self.state,'response',dict(request=token,result=event.get('result'),error=event.get('error')))
                got=event.get('result')
                if isinstance(got,dict) and 'related_sources' in got and token and token.get('project_id')==self.ui_project:
                    if not hasattr(self,'related'):self.related={}
                    self.related[self.ui_project]=got['related_sources']        # the source view offers Remove connection from these
            elif event.get('error'):
                if kind=='install_model':self.set_notice('Could not start the model install. Refresh Memory and try again.',True,kind)
                else:self.state['error']=event['error']
            elif kind=='bindings':
                prior=self.binding
                self.bindings=event['result']
                if not self.binding:self.state=memory_reduce(self.state,'revoke',{})
                elif prior is None and self.state['tab'] in {'Memory','Overview'}:self.action('submit',{})
            elif kind=='status':
                previous=(self.status.get('service_epoch'),self.status.get('policy_revision'),self.status.get('index_generation'))
                self.status=event['result']
                if self.notice and self.notice[2]=='connect_extension' and self.binding and self.binding['project_id'] in self.status.get('source_connected_project_ids',[]):self.set_notice(None)
                if self.notice and self.notice[2]=='install_model' and (self.status.get('model_install') or self.status.get('model_state')=='available'):self.set_notice(None)
                current=(self.status.get('service_epoch'),self.status.get('policy_revision'),self.status.get('index_generation'))
                if current!=previous:
                    self.state=memory_reduce(self.state,'generation',{'generation':current})
                    if self.binding and self.state['tab'] in {'Memory','Overview'}:self.action('submit',{})
            elif kind=='agents':self.agents={a['grant_id']:a for a in event['result']}
            elif kind=='copy_context':
                if token['project_id']==self.ui_project:
                    from AppKit import NSPasteboard
                    pb=NSPasteboard.generalPasteboard();pb.clearContents();pb.setString_forType_(event['result'],'public.utf8-plain-text')
                    self.set_notice('Project context copied with source citations.')
            elif kind=='open_original':
                if token['project_id']==self.ui_project and event['result'].get('url'):
                    from semantic_memory.policy import origin
                    from Foundation import NSURL
                    from AppKit import NSWorkspace
                    url=event['result']['url'];origin(url)
                    NSWorkspace.sharedWorkspace().openURL_(NSURL.URLWithString_(url))
            elif kind=='mutation':self.action('submit',{})
            elif kind=='purge':
                self.bindings=[b for b in self.bindings if b['ui_project_id']!=token['project_id']]
                self.last_poll=0
                if self.ui_project==token['project_id']:self.set_notice('Retained project memory deleted. Original notes and files remain.')
            elif kind=='agent':
                connection={k:v for k,v in event['result'].items() if k!='capability'}
                self.agents[connection['grant_id']]=connection
                if self.binding and self.binding['project_id'] in connection['project_ids']:self.state['preview']={'connection':dict(connection,command=codex_command(connection['config_path']))};self.add_to_codex(connection['config_path'])
            elif kind=='open':
                detail=event['result']
                if self.binding and token['project_id']==self.ui_project and detail.get('can_open_file'):
                    from pathlib import Path
                    path=Path(detail['locator']).resolve();root=Path(self.binding['workspace_root']).resolve()
                    if path.is_file() and path.is_relative_to(root):
                        from AppKit import NSWorkspace
                        from Foundation import NSURL
                        NSWorkspace.sharedWorkspace().openURL_(NSURL.fileURLWithPath_(str(path)))
            else:
                self.last_poll=0
                if kind=='enroll':self.bindings=[b for b in self.bindings if b['ui_project_id']!=event['result']['ui_project_id']]+[event['result']]
                if kind in {'enroll','recover_native_notes','resume'}:self.action('submit',{})
        if self.notice and self.notice[2]=='connect_extension' and not self.notice[1] and self.notice[0].startswith('Opened VS Code') and time.monotonic()-self.notice_at>25:
            why=self.status.get('focus_blocker')              # the service says which check failed
            self.set_notice('Still waiting for VS Code to be verified. '+(why+' ' if why else '')+'Fix that, then press Connect again.',True,'connect_extension')
        if self.seen_notice!=self.notice_version:self.seen_notice=self.notice_version;changed=True
        if self.ui_project and time.monotonic()-self.last_poll>2:
            self.last_poll=time.monotonic();self.send('bindings',{},'bindings');self.send('status',{},'status');self.send('agent_connections',{},'agents')
        return changed
