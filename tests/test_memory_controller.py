import os
import unittest
os.environ['LMEMM_NO_CODEX']='1'
from memory_controller import MemoryController
class Client:
    def __init__(self):self.calls=[];self.events=[]
    def request(self,*args):self.calls.append(args)
    def poll(self):e,self.events=self.events,[];return e
class ConnectResultTests(unittest.TestCase):
    def test_connecting_codex_shows_one_command_not_the_access_file(self):
        c=Client();m=MemoryController(lambda:c,lambda:{},lambda:None)
        m.bindings=[dict(ui_project_id='a',project_id='pa',active=True,workspace_root='/a')]
        m.select('a');m.action('connect_agent',{'approved':True});rid=c.calls[-1][2]
        m.poll([dict(type='response',id=rid,result=dict(grant_id='g',project_ids=['pa'],config_path='/my dir/g.json',capability='secret'))])
        connection=m.view()['preview']['connection']
        self.assertTrue(connection['command'].startswith('codex mcp add lmemm_memory_'))
        self.assertIn("'/my dir/g.json'",connection['command'])        # a path with a space stays one argument
        self.assertNotIn('capability',connection)
        import time
        for _ in range(50):
            if m.view()['preview']['connection'].get('codex',{}).get('state')!='adding':break
            time.sleep(.02)
        self.assertEqual(m.view()['preview']['connection']['codex']['state'],'failed')   # no Codex here: the card falls back to the line to paste
        self.assertIn('not found',m.view()['preview']['connection']['codex']['message'])
        self.assertTrue(m.view()['codex_connected'])                  # the Codex row says Connected, with Preview and Revoke
        from memory_controller import codex_args
        self.assertIn('python',codex_args('/p/g.json')[4])
        m.action('close_preview');self.assertIsNone(m.view()['preview'])
    def test_a_connect_that_never_verifies_says_what_to_check(self):
        import time
        c=Client();m=MemoryController(lambda:c,lambda:{},lambda:None)
        m.set_notice('Opened VS Code. Choose Allow there to finish.',False,'connect_extension');m.notice_at=time.monotonic()-30
        m.status={'focus_blocker':'VS Code is not the front app.'}
        m.poll([]);self.assertTrue(m.notice[1]);self.assertIn('not the front app',m.notice[0])
class AskCodexTests(unittest.TestCase):
    def test_time_filter_change_clears_old_period_answer(self):
        c=Client();m=MemoryController(lambda:c,lambda:{},lambda:None)
        m.bindings=[dict(ui_project_id='a',project_id='pa',active=True,workspace_root='/a')]
        m.agents={'g':dict(grant_id='g',project_ids=['pa'],config_path='/private/g.json')};m.select('a')
        m.ask=dict(state='done',project_id='pa',grant_id='g',serial=m.ask_serial,text='old period',question='old question')
        self.assertIsNotNone(m.view()['ask'])
        m.action('time_filter',{'period':'Last 7 days'})
        self.assertIsNone(m.view()['ask'])

    def test_answer_from_previous_project_is_discarded(self):
        import threading,json
        from unittest.mock import patch,Mock
        c=Client();m=MemoryController(lambda:c,lambda:{},lambda:None)
        m.bindings=[dict(ui_project_id=p,project_id='p'+p,active=True,workspace_root='/'+p) for p in ('a','b')]
        m.agents={'ga':dict(grant_id='ga',project_ids=['pa'],config_path='/private/ga.json')}
        entered=threading.Event();release=threading.Event();finished=threading.Event()
        def run(*args,**kwargs):
            entered.set();release.wait(2);finished.set()
            return Mock(returncode=0,stdout=json.dumps({'type':'item.completed','item':{'type':'agent_message','text':'Alpha-only answer'}}))
        with patch('memory_controller.codex_binary',return_value='/synthetic/codex'),patch('subprocess.run',side_effect=run):
            m.select('a');m.ask_codex('Alpha question');self.assertTrue(entered.wait(1))
            m.select('b');release.set()
            for thread in threading.enumerate():
                if thread.name=='memory-ask':thread.join(2)
            import time
            self.assertTrue(finished.wait(1));time.sleep(.03)
            self.assertIsNone(m.view()['ask'])

    def test_each_project_connection_has_its_own_mcp_entry(self):
        from memory_controller import codex_args
        self.assertNotEqual(codex_args('/private/ga.json')[2],codex_args('/private/gb.json')[2])

    def test_revoke_during_registration_removes_only_that_grant(self):
        import threading
        from unittest.mock import patch,Mock
        from memory_controller import codex_server_name
        c=Client();m=MemoryController(lambda:c,lambda:{},lambda:None)
        entered=threading.Event();release=threading.Event();calls=[]
        def run(args,**kwargs):
            calls.append(args)
            if args[2]=='add':entered.set();release.wait(2)
            return Mock(returncode=0)
        with patch('memory_controller.codex_binary',return_value='/synthetic/codex'),patch('subprocess.run',side_effect=run):
            m.add_to_codex('/private/ga.json');self.assertTrue(entered.wait(1))
            m.remove_from_codex({'config_path':'/private/ga.json'});release.set()
            for thread in threading.enumerate():
                if thread.name in ('memory-codex-add','memory-codex-remove'):thread.join(2)
        name=codex_server_name('/private/ga.json')
        self.assertEqual([(a[2],a[3]) for a in calls],[('add',name),('remove',name)])
        self.assertNotIn(name,m.codex_states)

    def test_the_question_asks_for_meaning_and_names_the_project(self):
        from memory_controller import codex_question
        text=codex_question('project-x','What is trail_store for?')
        self.assertIn('project_id project-x',text);self.assertIn('What is trail_store for?',text);self.assertIn('Do not paste code',text)
        self.assertIn('What was I working on?',codex_question('project-x',''))        # an empty box asks the default question

    def test_the_answer_is_the_last_message_codex_wrote(self):
        from memory_controller import codex_answer
        out='\n'.join(['not json','{"type":"item.completed","item":{"type":"mcp_tool_call"}}','{"type":"item.completed","item":{"type":"agent_message","text":"first"}}','{"type":"item.completed","item":{"type":"agent_message","text":"final"}}'])
        self.assertEqual(codex_answer(out),'final');self.assertIsNone(codex_answer('nothing'))

    def test_asking_needs_codex_connected_and_reports_when_it_is_missing(self):
        import time
        c=Client();m=MemoryController(lambda:c,lambda:{},lambda:None)
        m.bindings=[dict(ui_project_id='a',project_id='pa',active=True,workspace_root='/a')];m.select('a')
        m.action('ask_codex',{'text':'hi'});self.assertIsNone(m.view()['ask'])          # not connected: nothing runs
        m.agents={'g':dict(grant_id='g',project_ids=['pa'],config_path='/p')}
        m.action('ask_codex',{'text':'hi'})
        for _ in range(50):
            if m.view()['ask']['state']!='asking':break
            time.sleep(.02)
        self.assertEqual(m.view()['ask']['state'],'failed');self.assertIn('not found',m.view()['ask']['message'])
        m.action('close_ask');self.assertIsNone(m.view()['ask'])
class ControllerTests(unittest.TestCase):
    def test_late_purge_response_keeps_newly_selected_project_binding(self):
        c=Client();m=MemoryController(lambda:c,lambda:{},lambda:None)
        m.bindings=[dict(ui_project_id=p,project_id='p'+p,active=False,workspace_root=None) for p in ('a','b')]
        m.select('a');m.action('delete_project_memory');rid=c.calls[-1][2]
        m.select('b');m.poll([dict(type='response',id=rid,result={'removed':1})])
        self.assertEqual([b['ui_project_id'] for b in m.bindings],['b'])
    def test_entering_bound_project_loads_resume_without_waiting_for_a_global_revision(self):
        c=Client();m=MemoryController(lambda:c,lambda:{},lambda:None)
        m.bindings=[dict(ui_project_id='a',project_id='pa',active=True,workspace_root=None)]
        m.select('a')
        self.assertTrue(any(call[0]=='resume_project' and call[1]['project_id']=='pa' for call in c.calls))

    def test_startup_binding_response_loads_selected_resume(self):
        c=Client();m=MemoryController(lambda:c,lambda:{},lambda:None);m.select('a')
        m.send('bindings',{},'bindings');rid=c.calls[-1][2]
        m.poll([dict(type='response',id=rid,result=[dict(ui_project_id='a',project_id='pa',active=True,workspace_root=None)])])
        self.assertTrue(any(call[0]=='resume_project' for call in c.calls))
    def test_folder_free_enrollment_and_resume_request(self):
        c=Client();m=MemoryController(lambda:c,lambda:{},lambda:None);m.select('a')
        m.action('enroll',{})
        self.assertIsNone(c.calls[-1][1]['root'])
        m.bindings=[dict(ui_project_id='a',project_id='pa',active=True,workspace_root=None)]
        m.action('tab',{'tab':'Overview'})
        self.assertEqual(c.calls[-1][0],'resume_project')

    def test_submit_only_and_response_project_fence(self):
        c=Client();m=MemoryController(lambda:c,lambda:{},lambda:None)
        m.bindings=[dict(ui_project_id='a',project_id='pa',active=True,workspace_root='/a')]
        m.select('a');m.action('tab',{'tab':'Memory'});m.action('query',{'text':'rollback'})
        self.assertFalse(any(call[0]=='search_project_memory' for call in c.calls))
        m.action('submit',{});call=c.calls[-1]
        self.assertEqual(call[0],'search_project_memory');self.assertEqual(call[1]['project_id'],'pa')
        m.select('b');c.events=[dict(type='response',id=call[2],result={'primary':[{'text':'a'}]})];m.poll()
        self.assertIsNone(m.state['result'])
    def test_source_open_requires_fresh_resolution(self):
        c=Client();m=MemoryController(lambda:c,lambda:{},lambda:None)
        m.bindings=[dict(ui_project_id='a',project_id='pa',active=True,workspace_root='/a')];m.select('a')
        m.action('source',{'citation_id':'cite'})
        self.assertEqual(c.calls[-1][:2],('resolve_citation',{'project_id':'pa','citation_id':'cite'}))
    def test_service_restart_clears_cached_result(self):
        c=Client();m=MemoryController(lambda:c,lambda:{},lambda:None);m.state['result']={'primary':[{'text':'old'}]}
        m.poll([{'type':'ready'}]);self.assertIsNone(m.state['result'])
    def test_late_agent_connection_stays_with_origin_project(self):
        c=Client();m=MemoryController(lambda:c,lambda:{},lambda:None)
        m.bindings=[dict(ui_project_id=p,project_id='p'+p,active=True,workspace_root='/'+p) for p in ('a','b')]
        m.select('a');m.action('connect_agent',{'approved':True});rid=c.calls[-1][2]
        m.select('b');m.poll([dict(type='response',id=rid,result=dict(grant_id='ga',project_ids=['pa'],config_path='/private/a'))])
        self.assertIsNone(m.agent);self.assertIsNone(m.state.get('preview'))
        m.action('revoke_agent');self.assertFalse(any(call[0]=='revoke_agent' for call in c.calls))
        m.select('a');m.action('revoke_agent');self.assertEqual(c.calls[-1][:2],('revoke_agent',{'grant_id':'ga'}))
    def test_restored_connections_allow_revoke_and_late_enrollment_keeps_both_bindings(self):
        c=Client();m=MemoryController(lambda:c,lambda:{},lambda:None)
        b=dict(ui_project_id='b',project_id='pb',active=True,workspace_root='/b')
        a=dict(ui_project_id='a',project_id='pa',active=True,workspace_root='/a')
        m.bindings=[b];m.select('a');m.action('enroll',{'root':'/a'});rid=c.calls[-1][2]
        m.select('b');m.poll([dict(type='response',id=rid,result=a)])
        self.assertEqual({b['ui_project_id'] for b in m.bindings},{'a','b'})
        m.send('agent_connections',{},'agents');rid=c.calls[-1][2]
        m.poll([dict(type='response',id=rid,result=[dict(grant_id='gb',client_id='Codex',project_ids=['pb'])])])
        m.action('revoke_agent');self.assertEqual(c.calls[-1][:2],('revoke_agent',{'grant_id':'gb'}))
    def test_enable_shows_progress_until_the_answer_or_a_failure(self):
        c=Client();m=MemoryController(lambda:c,lambda:{},lambda:None);m.select('a')
        self.assertNotIn('busy',m.view())
        m.action('enroll',{'root':'/a'});rid=c.calls[-1][2]
        self.assertEqual(m.view()['busy'],'Enabling Memory…')
        m.poll([dict(type='response',id=rid,error='Project memory unavailable')])
        self.assertNotIn('busy',m.view())
        m.action('enroll',{'root':'/a'});self.assertIn('busy',m.view())
        m.poll([dict(type='error',error='Project memory service unavailable.')])
        self.assertNotIn('busy',m.view())


class OverviewResumeTests(unittest.TestCase):
    def setUp(self):
        self.c=Client();self.m=MemoryController(lambda:self.c,lambda:{},lambda:None)
        self.m.bindings=[dict(ui_project_id='a',project_id='pa',active=True,workspace_root='/a')];self.m.select('a')
    def test_overview_is_the_default_tab_and_loads_the_resume_packet(self):
        self.assertEqual(self.m.state['tab'],'Overview')
        self.assertEqual(self.c.calls[-1][0],'resume_project')
        self.assertNotIn('Resume',self.m.view()['tabs'])
    def test_mark_done_then_undo_uses_the_same_backend_calls_and_clears_the_undo(self):
        self.m.action('set_task_state',{'claim_id':'t1','done':True})
        self.assertEqual(self.c.calls[-1][:2],('set_task_state',{'claim_id':'t1','done':True,'project_id':'pa'}))
        self.assertEqual(self.m.view()['last_completed'],('set_task_state',{'claim_id':'t1','done':False}))
        self.m.action('set_task_state',{'claim_id':'t1','done':False})
        self.assertIsNone(self.m.view()['last_completed'])
    def test_related_sources_come_from_the_resume_packet(self):
        rid=self.c.calls[-1][2]
        self.m.poll([dict(type='response',id=rid,result=dict(related_sources=[dict(id='e1',citation_id='c2',from_citation='c1',title='T')]))])
        self.assertEqual(self.m.view()['related'][0]['id'],'e1')
    def test_closing_a_source_reloads_the_list(self):
        self.m.action('tab',{'tab':'Memory'});self.m.action('source',{'citation_id':'c'})
        n=len(self.c.calls);self.m.action('close_source')
        self.assertEqual(self.c.calls[n][0],'get_project_context')


class ResumeActionsSurviveTests(unittest.TestCase):
    def test_every_old_resume_action_is_still_reachable(self):
        root=os.path.join(os.path.dirname(__file__),'..','src')
        window=open(os.path.join(root,'window.py')).read();pane=open(os.path.join(root,'memory_pane.py')).read()
        self.assertFalse(os.path.exists(os.path.join(root,'resume_pane.py')))
        for a in ('copy_context','add_note','view_source'):self.assertIn(a,window)
        view=open(os.path.join(root,'memory_view.py')).read()
        for a in ('complete_native_note','set_task_state'):self.assertIn(a,view)
        for a in ('record_claim','remove_relationship','forget_source'):self.assertIn(a,pane)
