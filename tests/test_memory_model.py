import unittest
from memory_model import new_memory_state,memory_reduce,memory_view

class MemoryModelTests(unittest.TestCase):
    def test_submit_only_and_late_results(self):
        s=memory_reduce(new_memory_state(),'select_project',{'project_id':'a'})
        s=memory_reduce(s,'query',{'text':'rollback 🔎'})
        self.assertEqual(s['request_count'],0)
        s=memory_reduce(s,'submit',{})
        request=s['request'];self.assertEqual(s['request_count'],1)
        s=memory_reduce(s,'select_project',{'project_id':'b'})
        s=memory_reduce(s,'response',{'request':request,'result':{'primary':[{'text':'secret a'}]}})
        self.assertIsNone(s['result'])
    def test_navigation_and_status(self):
        s=memory_reduce(new_memory_state(),'select_project',{'project_id':'a'})
        s=memory_reduce(s,'tab',{'tab':'Memory'})
        s=memory_reduce(s,'select_project',{'project_id':'b'})
        s=memory_reduce(s,'select_project',{'project_id':'a'})
        self.assertEqual(s['tab'],'Memory')
        v=memory_view(s,{'workspace_root':'/repo'},{'state':'Paused','model_state':'missing'},None)
        self.assertEqual(v['tabs'],['Overview','Memory'])
        self.assertIn('keyword',v['message'].lower())
        self.assertEqual(v['periods'],['Last 2 days','Last 7 days','All retained'])
    def test_a_missing_optional_model_is_not_a_warning(self):
        v=memory_view(new_memory_state(),{'workspace_root':'/repo'},{'state':'Needs attention','reason':'Local model unavailable','model_state':'missing'},None)
        self.assertEqual(v['state'],'Ready')
        self.assertIn('Memory is working. Keyword search is on;',v['message'])
        v=memory_view(new_memory_state(),{'workspace_root':'/repo'},{'state':'Needs attention','reason':'A note save was interrupted.','model_state':'available'},None)
        self.assertEqual(v['state'],'Needs attention')
    def test_revocation_clears_previous_evidence(self):
        s=new_memory_state();s['result']={'primary':[{'text':'old'}]}
        s=memory_reduce(s,'revoke',{})
        self.assertIsNone(s['result'])
        self.assertIsNone(s['source'])
    def test_generation_changes_reject_old_response(self):
        s=memory_reduce(new_memory_state(),'submit',{});r=s['request']
        s=memory_reduce(s,'generation',{'generation':'new'})
        self.assertIsNone(memory_reduce(s,'response',{'request':r,'result':{'text':'old'}})['result'])
    def test_empty_evidence_has_no_completion_claim(self):
        v=memory_view(new_memory_state(),{'workspace_root':'/repo'},{'state':'Ready'},{'open_tasks':[],'decisions':[],'recent_evidence':[]})
        self.assertIn('No retained evidence',v['empty'])
        self.assertNotIn('caught up',repr(v).lower())
    def test_typing_new_query_discards_inflight_old_query(self):
        s=memory_reduce(new_memory_state(),'query',{'text':'old'})
        s=memory_reduce(s,'submit',{});token=s['request']
        s=memory_reduce(s,'query',{'text':'new'})
        s=memory_reduce(s,'response',{'request':token,'result':{'primary':[{'text':'old answer'}]}})
        self.assertIsNone(s['result'])
