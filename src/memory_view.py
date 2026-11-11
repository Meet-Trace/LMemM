"""LMemM - what the Memory tab says: the words, rows and dates it shows. Plain Python, no UI, so it is tested on any
machine. The drawing is in memory_pane.py."""
import re
from datetime import datetime, timezone

def friendly(at,now=None):
    """'2026-10-12T08:38:03+00:00' as 'Today, 08:38', 'Yesterday, 14:02' or '9 Oct, 10:00' in local time."""
    try:
        when=datetime.fromisoformat(str(at).replace('Z','+00:00'))
        if when.tzinfo is None:when=when.replace(tzinfo=timezone.utc)
        when=when.astimezone();now=(now or datetime.now(timezone.utc)).astimezone()
    except (ValueError,TypeError):return str(at or '')
    days=(now.date()-when.date()).days;clock=when.strftime('%H:%M')
    if days==0:return 'Today, '+clock
    if days==1:return 'Yesterday, '+clock
    return '%d %s, %s'%(when.day,when.strftime('%b'),clock)

def short_name(locator,limit=26):
    """The file name of a source, cut in the middle when long."""
    name=str(locator or '').replace('\\','/').rstrip('/').rsplit('/',1)[-1]
    if len(name)<=limit:return name
    keep=(limit-1)//2;return name[:keep]+'…'+name[-keep:]

PERIOD_LABELS={'Last 2 days':'2 days','Last 7 days':'7 days','All retained':'All'}
STATE_LABELS={'Ready':'Remembering','Processing':'Processing','Paused':'Paused · nothing new is kept','Needs attention':'Needs attention'}
CONFIRMS={'confirm_model':('Install meaning search?','Downloads about 46 MB (the MiniLM model) to Trace’s own folder on this Mac. Needs Ollama installed. No notes or text are sent anywhere.','Install'),
          'confirm_agent':('Let Codex read this project’s memory?','Codex can search saved notes and sources and read the cited text. Using these tools sends that text to Codex and its provider. Trace also adds itself to Codex’s settings on this Mac. You can turn it off at any time.','Allow and add to Codex'),
          'confirm_disconnect':('Disconnect Memory for this project?','Stops capture and revokes agent access. Saved notes stay on this Mac but can’t be searched until you enable Memory again.','Disconnect')}

def provenance(row):
    if row.get('origin_type') in {'browser_page','browser_selection'}:return 'Saved from browser'
    if row.get('origin_type')=='pasted_excerpt':return 'User-supplied excerpt'
    return 'Your note' if row.get('origin_type')=='user_note' else 'Observed content' if row.get('origin_type') else 'Supported by cited sources'

def evidence_rows(result):
    rows=[]
    for key,title in [('primary','Search results'),('related','Related evidence'),('decisions','Decisions'),('open_tasks','Open tasks'),('recent_evidence','Recent evidence')]:
        for row in result.get(key,[]):
            rows.append(dict(section=title,text=row.get('text',row.get('statement','')),at=row.get('at',''),
                citation_id=row.get('citation_id') or next(iter(row.get('citations',[])),None),reason=row.get('reason'),locator=row.get('locator',''),
                provenance=provenance(row),truncated=row.get('truncated',False)))
    return rows

def excerpt(text,lines=6,width=110):
    """A few readable lines of a captured chunk: no blank lines at the top, runs of blank lines folded, long lines cut."""
    kept,blank=[],False
    for line in str(text).replace('\t','    ').splitlines():
        if not line.strip():
            blank=bool(kept)
            continue
        if blank:kept.append('');blank=False
        kept.append(line.rstrip() if len(line.rstrip())<=width else line.rstrip()[:width-1]+'…')
    cut=len([l for l in kept if l is not None])>lines
    shown=kept[:lines]
    while shown and not shown[-1]:shown.pop()
    return '\n'.join(shown)+('\n…' if cut else '')

def group_evidence(rows):
    """One row per file for observed text: scrolling a file keeps many near-copies, and a list of them says nothing.
    The first (best) row is shown, with how many there were and when the latest was kept. Notes are never grouped."""
    out,seen=[],{}
    for row in rows:
        if row['provenance']!='Observed content' or not row['locator']:
            out.append(dict(row,title=None,count=1));continue
        key=(row['section'],row['locator'])
        if key in seen:
            first=seen[key];first['count']+=1
            if row['at']>first['at']:first['at']=row['at']
            continue
        first=dict(row,title=short_name(row['locator']),count=1,text=excerpt(row['text']))
        seen[key]=first;out.append(first)
    return out

def source_presentation(source):
    explanation='This is the retained snapshot. Open file shows the file as it exists today.'
    if source.get('origin_type')=='user_note' and not source.get('can_open_file',False):
        explanation='Saved project note. Its retained text is shown here; no external file is available to open.'
    elif source.get('origin_type') in {'browser_page','browser_selection','pasted_excerpt'}:
        explanation='Retained saved text. Open original shows the page as it exists today.' if source.get('url') else 'User-supplied retained text; no external file is available.'
    return dict(text=source['text'],locator=source['locator'],at=source['at'],open_enabled=source.get('can_open_file',False),
                provenance=provenance(source),truncated=source.get('truncated',False),
                open_explanation=explanation,url=source.get('url'),title=source.get('title',''))

FILLER=frozenset('a an and are as at be by for from has have how in is it its of on or that the this to was what when where which who why with'.split())
KEPT='Memory searches saved notes, excerpts, browser saves and connected VS Code text, so it may never have seen this.'

def search_note(query,rows):
    """Explain empty results or uncertain wording without judging semantic relevance.
    Low word overlap cannot distinguish paraphrases from unrelated evidence.
    """
    words=[w for w in dict.fromkeys(re.findall(r'\w+',str(query).lower())) if w not in FILLER and len(w)>2]
    if not words:return None
    primary=[r for r in rows if r['section']=='Search results']
    need=max(1,(len(words)+1)//2)
    if any(sum(w in r['text'].lower() for w in words)>=need for r in primary):return None
    if not primary:return 'No matches for “%s”. %s'%(str(query).strip(),KEPT)
    return 'Possible matches with different wording. Open a source to check whether it answers your question.'
