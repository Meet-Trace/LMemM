#!/usr/bin/env python3
"""Writes tests/fixtures/project_grouping/noise_day.json: three real projects and three bouts of idle looking around.

An invented day, not real data. The bouts (reading web-server logs, browsing preferences, scrolling feeds)
are labelled as belonging to no project, so a grouping that joins their windows scores as wrong. One real
project is terminal-heavy (deploying a blog), to show that the question "is this deliberate work?" is
answered by what was done, not by which app it was done in.
"""
import json, sys
from collections import defaultdict
from datetime import datetime, timedelta

B, G, D = "Bathroom renovation", "Guitar practice", "Blog deploy"
# id: (app, kind, title, state, typed, received, read, screen, label)
T = {
 "b-chat-contractor": ("WhatsApp", "chat", "Suresh Tiles", {"chat": "Suresh Tiles"},
    ["Can you start on the 18th?"], ["Yes, two men for four days", "Waterproofing first, then the floor"], [], ["Suresh Tiles: Waterproofing first, then the floor"], [f"{B}/Contractor"]),
 "b-sheet-quotes": ("Google Sheets", "spreadsheet", "Quotes compared", {"url": "https://docs.google.com/spreadsheets/d/1Qq9"},
    ["Suresh 1.9 L", "Imtiyaz 2.3 L"], [], ["Labour | Material | Total"], ["Vendor | Labour | Material | Total"], [f"{B}/Contractor"]),
 "b-page-tiles": ("Safari", "page", "Matt porcelain 600x600 - Kajaria", {"url": "https://www.kajaria.example/matt-porcelain-600"},
    [], [], ["Anti-skid, R10", "Coverage 1.44 sq m per box"], ["Price per box 1,380"], [f"{B}/Fittings"]),
 "b-notes-measure": ("Notes", "note", "Bathroom measurements", {},
    ["Floor 6 ft x 8 ft", "Wall height 9 ft", "Shower niche 12 in"], [], [], [], [f"{B}/Fittings"]),
 "g-yt-lesson": ("YouTube", "page", "Fingerstyle for beginners - lesson 3", {"url": "https://www.youtube.com/watch?v=fs3"},
    [], [], ["Thumb on the bass strings", "Slow metronome at 60"], [], [f"{G}"]),
 "g-page-tabs": ("Safari", "page", "Blackbird - Beatles tab", {"url": "https://tabs.example/blackbird"},
    [], [], ["Intro bars 1 to 8", "Capo none, standard tuning"], [], [f"{G}"]),
 "g-notes-log": ("Notes", "note", "Fingerstyle log", {},
    ["Mon: 20 min, intro bars 1-4", "Tue: 25 min, bars 5-8 too fast"], [], [], [], [f"{G}"]),
 "g-chat-teacher": ("WhatsApp", "chat", "Neel (guitar)", {"chat": "Neel (guitar)"},
    ["Can we shift Saturday's class to 5?"], ["Sure, bring the new capo"], [], [], [f"{G}"]),
 "d-code-config": ("Visual Studio Code", "code", "deploy.yml - blog", {"file": "~/code/blog/.github/workflows/deploy.yml"},
    ["on: push: branches: [main]", "run: rsync -az public/ user@host:/var/www/blog"], [], ["jobs: build, deploy"], [], [f"{D}"]),
 "d-term-build": ("Terminal", "terminal", "hugo --minify — 110×30", {"cwd": "~/code/blog"},
    ["$ hugo --minify", "$ rsync -az public/ user@host:/var/www/blog"], [], ["Pages | 48", "Total in 214 ms"], ["Start building sites", "Total in 214 ms"], [f"{D}"]),
 "d-page-actions": ("GitHub", "page", "Deploy blog #12 · Actions · me/blog", {"url": "https://github.com/me/blog/actions/runs/12"},
    [], [], ["build passed", "deploy failed: Permission denied (publickey)"], [], [f"{D}"]),
 "d-term-ssh": ("Terminal", "terminal", "ssh-keygen -t ed25519 — 110×30", {"cwd": "~"},
    ["$ ssh-keygen -t ed25519 -C deploy@blog", "$ cat ~/.ssh/id_ed25519.pub"], [], ["Your public key has been saved"], [], [f"{D}"]),
 "n-term-nginx": ("Terminal", "terminal", "tail -f /opt/homebrew/var/log/nginx/access.log", {"cwd": "~"},
    [], [], ["127.0.0.1 - - GET /favicon.ico 404", "127.0.0.1 - - GET / 200", "::1 - - GET /robots.txt 404"], ["127.0.0.1 - - GET / 200"], []),
 "n-console-reports": ("Console", "app", "Log Reports", {},
    [], [], ["DiagnosticReports", "Spin reports", "Crash reports"], ["SafariBookmarksSyncAgent_2026-11-02.ips"], []),
 "n-term-lsof": ("Terminal", "terminal", "lsof -i -P | head — 110×30", {"cwd": "~"},
    ["$ lsof -i -P | head -20"], [], ["rapportd TCP *:49152 (LISTEN)", "ControlCe TCP *:7000 (LISTEN)"], [], []),
 "n-settings-display": ("System Settings", "app", "Displays", {},
    [], [], ["Resolution: Default", "Refresh rate: ProMotion", "Night Shift"], [], []),
 "n-settings-keyboard": ("System Settings", "app", "Keyboard", {},
    [], [], ["Key repeat rate", "Text replacements", "Dictation: Off"], [], []),
 "n-finder-prefs": ("Finder", "folder", "Preferences", {"folder": "~/Library/Preferences"},
    [], [], ["com.apple.dock.plist", "com.apple.finder.plist", "com.google.Chrome.plist"], [], []),
 "n-safari-news": ("Safari", "page", "World | The Daily Herald", {"url": "https://www.dailyherald.example/world"},
    [], [], ["Talks resume in the capital", "Flood warning for coastal towns"], [], []),
 "n-yt-home": ("YouTube", "page", "YouTube — Recommended", {"url": "https://www.youtube.com/"},
    [], [], ["Recommended", "Mix - synthwave", "You might also like"], [], []),
 "n-insta-explore": ("Instagram", "page", "Discover people and reels", {"url": "https://www.instagram.com/explore/"},
    [], [], ["Reels", "Food", "Travel"], [], []),
}
TIMELINE = [  # (item, from, minutes); idle bouts come back in three sittings, real projects too
 ("n-term-nginx", "09:00", 6), ("n-console-reports", "09:06", 5), ("b-chat-contractor", "09:11", 5), ("b-sheet-quotes", "09:16", 12),
 ("n-safari-news", "09:30", 4), ("n-yt-home", "09:34", 5), ("b-page-tiles", "09:39", 8), ("b-notes-measure", "09:47", 5),
 ("d-code-config", "11:00", 12), ("d-term-build", "11:12", 6), ("d-page-actions", "11:18", 5), ("n-settings-display", "11:24", 3),
 ("d-term-ssh", "11:27", 8), ("n-settings-keyboard", "11:35", 3), ("n-finder-prefs", "11:38", 4), ("d-term-build", "11:42", 4),
 ("g-yt-lesson", "15:00", 15), ("g-page-tabs", "15:15", 8), ("g-notes-log", "15:23", 5), ("n-term-lsof", "15:30", 4),
 ("n-term-nginx", "15:34", 4), ("n-insta-explore", "15:38", 4), ("g-chat-teacher", "15:42", 3), ("n-console-reports", "15:45", 3),
 ("b-chat-contractor", "20:00", 4), ("n-finder-prefs", "20:04", 4), ("n-settings-display", "20:08", 3), ("n-yt-home", "20:11", 6),
 ("n-safari-news", "20:17", 4), ("n-insta-explore", "20:21", 4), ("n-term-lsof", "20:25", 3), ("n-settings-keyboard", "20:28", 3),
]
day = "2026-11-06"
timeline, spans = [], defaultdict(list)
for item, start, mins in TIMELINE:
    a = datetime.fromisoformat(f"{day}T{start}:00")
    b = a + timedelta(minutes=mins)
    timeline.append({"item": item, "from": a.isoformat(), "to": b.isoformat()})
    spans[item].append((a, b))
items = {}
for iid, (app, kind, title, state, typed, received, read, screen, label) in T.items():
    s = spans[iid]
    secs = int(sum((b - a).total_seconds() for a, b in s))
    act = {}
    if typed:
        act["typing"] = {"seconds": min(secs, 30 * len(typed)), "text": typed}
    if read:
        act["reading"] = {"seconds": min(secs, 60), "text": read}
    if received:
        act["receiving"] = {"seconds": 0, "text": received, "count": len(received)}
    excerpts = [{"id": f"x{k}", "text": t, "source": {"app": app}} for k, t in enumerate(screen)]
    items[iid] = {"id": iid, "app": app, "kind": kind, "title": title, "doing": f"Working on \"{title}\"", "state": state,
                  "activity": act, "first_seen": min(a for a, _ in s).isoformat(), "last_seen": max(b for _, b in s).isoformat(),
                  "seconds": secs, "visits": len(s), "label": label,
                  **({"content": {"version": 1, "excerpts": excerpts}} if excerpts else {})}
doc = {"version": 1,
       "about": ("Fourth invented day, the noise test. Not real data. Three real projects (a bathroom renovation, guitar "
                 "practice, a terminal-heavy blog deploy) and three bouts of idle looking around (reading web-server "
                 "logs, browsing preferences, scrolling feeds) that belong to no project, each coming back in several "
                 "sittings. A good method files the real projects and leaves the idle bouts alone."),
       "items": items, "timeline": timeline}
out = sys.argv[1] if len(sys.argv) > 1 else "tests/fixtures/project_grouping/noise_day.json"
open(out, "w").write(json.dumps(doc, indent=1, ensure_ascii=False) + "\n")
print(len(items), "items", len(timeline), "visits")
