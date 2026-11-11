"""Pure project-memory navigation; request tokens fence asynchronous responses."""
from copy import deepcopy
from datetime import datetime,timedelta,timezone

TABS=['Overview','Memory']        # Resume is part of Overview now
PERIODS=['Last 2 days','Last 7 days','All retained']

def new_memory_state():
    return dict(project_id=None,tab='Overview',history={},query='',period=PERIODS[0],generation=None,epoch=0,
                request_count=0,request=None,result=None,source=None,preview=None,error=None,loading=False)

def memory_reduce(state,action,payload):
    s=deepcopy(state)
    if action=='select_project':
        pid=payload.get('project_id')
        if pid!=s['project_id']:
            if s['project_id']:s['history'][s['project_id']]=s['tab']
            s.update(project_id=pid,tab=s['history'].get(pid,'Overview') if s['history'].get(pid) in TABS else 'Overview',query='',result=None,source=None,preview=None,error=None,loading=False,request=None,epoch=s['epoch']+1)
    elif action=='tab':
        if payload['tab'] not in TABS:raise ValueError('Unknown tab')
        s['tab']=payload['tab'];s['source']=None
    elif action=='query':
        if payload['text']!=s['query']:
            s.update(query=payload['text'],asked=None,epoch=s['epoch']+1,request=None,result=None,source=None,loading=False)
    elif action=='time_filter':
        if payload['period'] not in PERIODS:raise ValueError('Unknown period')
        s.update(period=payload['period'],result=None,source=None,epoch=s['epoch']+1)
    elif action in {'submit','source','preview'}:
        if action=='submit' and payload.get('enter'):s['asked']=s['query']      # Enter pressed: only now may the tab say "no match"
        s['request_count']+=1
        s.update(request={'id':s['request_count'],'project_id':s['project_id'],'epoch':s['epoch'],'generation':s['generation'],'kind':action},loading=True,error=None)
        if action=='source':s['request']['citation_id']=payload['citation_id']
    elif action=='response':
        if s['request'] is None or payload.get('request')!=s['request']:return s
        s['loading']=False
        if payload.get('revoked'):
            s.update(result=None,source=None,preview=None,error='Access unavailable')
        elif payload.get('error'):s['error']=payload['error']
        else:
            key={'source':'source','preview':'preview'}.get(s['request']['kind'],'result')
            s[key]=payload['result'];s['error']=None
    elif action in {'revoke','cancel','generation'}:
        s.update(epoch=s['epoch']+1,request=None,loading=False,source=None,preview=None,result=None)
        if action=='generation':s['generation']=payload['generation']
    else:raise ValueError('Unknown memory action')
    return s

def period_args(period,now=None):
    now=now or datetime.now(timezone.utc)
    if period=='All retained':return {'start':'1970-01-01T00:00:00+00:00','end':now.isoformat()}
    return {'start':(now-timedelta(days=7 if period=='Last 7 days' else 2)).isoformat(),'end':now.isoformat()}

def memory_view(state,binding,status,result):
    status=status or {};result=result or {};mode=status.get('state','Disabled')
    model_only=mode=='Needs attention' and status.get('reason')=='Local model unavailable'
    if model_only:mode='Ready'           # the optional search model is the only problem: working, not alarming
    messages={'Disabled':'Enable Memory for this project to retain new permitted evidence.',
              'Paused':'Capture and indexing paused. Keyword recall of retained evidence remains available.',
              'Processing':'Processing new evidence. Retained evidence remains available.',
              'Needs attention':status.get('reason') or 'Memory needs attention.'}
    message=messages.get(mode,'Retained snapshots with citations. Matching evidence may not answer your question.')
    if model_only or (status.get('model_state')=='missing' and mode!='Disabled'):
        message=(message if not model_only else 'Memory is working.')+' Keyword search is on; install the local model for semantic recall.'
    return dict(tabs=list(TABS),periods=list(PERIODS),tab=state['tab'],query=state['query'],asked=state.get('asked'),period=state['period'],
                state=mode,binding=binding,status=status,message=message,loading=state['loading'],error=state['error'],
                result=result,source=state['source'],preview=state['preview'],
                empty='No retained evidence for this period.' if not any(result.get(k) for k in ('primary','decisions','open_tasks','recent_evidence')) else '',
                coverage=result.get('coverage',{}))
