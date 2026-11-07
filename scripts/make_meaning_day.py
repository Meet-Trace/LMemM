#!/usr/bin/env python3
"""Writes tests/fixtures/project_grouping/meaning_day.json: things of one project share no telling words."""
import json, sys
from collections import defaultdict
from datetime import datetime, timedelta

W, F, R = "Sister's wedding", "Seed fundraising", "Half marathon"
# id: (app, kind, title, state, typed, received, read, screen, label)
T = {
 "w-chat-sister": ("WhatsApp", "chat", "Riya", {"chat": "Riya"},
    ["I'll sort out the food people this week"], ["Mehendi is on the 12th, haldi the morning after", "Mom wants marigolds everywhere"],
    [], ["Riya: Mehendi is on the 12th, haldi the morning after"], [f"{W}/Events"]),
 "w-sheet-guests": ("Google Sheets", "spreadsheet", "Guest list - final", {"url": "https://docs.google.com/spreadsheets/d/1Gx7qPz"},
    ["Sharma family x4", "Plus-ones confirmed: 38"], [], ["Veg / Jain / Non-veg", "RSVP column"], ["Name | Side | Meal | RSVP", "Bride's side 212, groom's side 164"], [f"{W}/Guests and food"]),
 "w-mail-caterer": ("Gmail", "email", "Quote for 300 covers - buffet", {"subject": "Quote for 300 covers - buffet", "from": "orders@spiceroutecaterers.in"},
    ["Can you also do a live chaat counter?"], ["Paneer tikka, dal makhani and two desserts included", "Advance of 40% blocks the date"], [], ["Per plate: 1,150 + GST"], [f"{W}/Guests and food"]),
 "w-page-venue": ("Safari", "page", "Sea-view lawns in Juhu | VenueLook", {"url": "https://www.venuelook.com/mumbai/juhu/lawns"},
    [], [], ["Lawn with a wooden deck, seats up to 350", "Floral stage packages from partner decorators"], ["Available dates: 12, 13, 14 Dec"], [f"{W}/Events"]),
 "w-notes-sangeet": ("Notes", "note", "Sangeet", {},
    ["Opening dance: cousins", "Songs: Gallan Goodiyaan, London Thumakda", "Book a DJ till 11pm"], [], [], [], [f"{W}/Events"]),
 "f-doc-memo": ("Google Docs", "document", "Seed memo - draft 3", {"url": "https://docs.google.com/document/d/1Mm3rKx"},
    ["We are raising 4 crore to reach 10k paying users", "Why now: on-device AI became cheap this year"], [], ["Comment from Kabir: tighten the market slide"], [], [f"{F}/Materials"]),
 "f-keynote-deck": ("Keynote", "document", "Deck v7.key", {"file": "~/Desktop/Deck v7.key"},
    ["Slide 9: use of funds", "Hiring: 2 engineers, 1 designer"], [], ["Traction: 1,200 weekly actives"], ["Problem / Solution / Traction / Team / Ask"], [f"{F}/Materials"]),
 "f-sheet-captable": ("Microsoft Excel", "spreadsheet", "Captable_post_money.xlsx", {"file": "~/Documents/Finance/Captable_post_money.xlsx", "sheet": "Round"},
    ["ESOP pool 10%", "Pre-money 16 Cr"], [], ["Founders 78.4% after dilution"], ["Shareholder | Shares | %"], [f"{F}/Materials"]),
 "f-mail-vc": ("Gmail", "email", "Intro: you <> Neha (Orchard Ventures)", {"subject": "Intro: you <> Neha (Orchard Ventures)", "from": "kabir.m@gmail.com"},
    [], ["Neha leads early-stage consumer AI bets", "Happy to take a first call next week, send over your deck"], [], [], [f"{F}/Investors"]),
 "f-calendly": ("Safari", "page", "30 min first call - Calendly", {"url": "https://calendly.com/nk-partner/30min"},
    [], [], ["Select a date & time", "Thursday 4:30 pm IST"], ["Agenda: what are you building, and why you?"], [f"{F}/Investors"]),
 "f-claude": ("Claude", "ai_conversation", "Questions angels ask pre-revenue", {"conversation": "Questions angels ask pre-revenue"},
    ["what metrics do angels care about before we charge anyone"], ["Retention cohorts, engagement depth and how fast you ship"], [], [], [f"{F}/Investors"]),
 "r-strava": ("Safari", "page", "Tempo run - 8.2 km | Strava", {"url": "https://www.strava.com/activities/12093344"},
    [], [], ["Avg pace 5:41 /km", "Heart rate zone 4 for 26 min"], ["Kudos from 3 friends"], [f"{R}/Training"]),
 "r-guide": ("Safari", "page", "Beginner 21K guide: week 6", {"url": "https://www.21kcoach.in/beginner/week-6"},
    [], [], ["Long run this week: 14 km at easy pace", "Two rest days, one interval session"], [], [f"{R}/Training"]),
 "r-mail-bib": ("Gmail", "email", "Your bib for Sunday's race", {"subject": "Your bib for Sunday's race", "from": "events@raceday.in"},
    [], ["Bib number 4471, collect it at the expo", "Expo: Jio World Centre, Saturday 10am to 7pm"], [], [], [f"{R}/Race"]),
 "r-notes-shoes": ("Notes", "note", "Shoes", {},
    ["Pegasus 41 vs Novablast 5", "Knee niggle after the weekend, ice and rest"], [], [], [], [f"{R}/Training"]),
 "hub-gmail": ("Gmail", "mailbox", "Inbox (96)", {},
    [], ["Quote for 300 covers - buffet", "Intro: you <> Neha (Orchard Ventures)", "Your bib for Sunday's race"], ["Promotions: 40% off monsoon sale"], [], []),
 "slack-random": ("Slack", "chat", "#random", {"chat": "#random", "workspace": "Lumen Labs"},
    [], ["who's up for lunch at 1?", "the new coffee machine is a game changer"], [], [], []),
 "yt-lofi": ("YouTube", "page", "lofi hip hop radio - beats to relax/study to", {"url": "https://www.youtube.com/watch?v=jfKfPfyJRdk"},
    [], [], ["LIVE"], [], []),
 "news-monsoon": ("Safari", "page", "Monsoon may arrive early in Mumbai this year", {"url": "https://www.dailyherald.in/weather/monsoon-early"},
    [], [], ["IMD forecasts above-normal rainfall"], [], []),
}
TIMELINE = [  # (item, from, minutes); four sittings, projects interleaved
 ("hub-gmail", "09:00", 4), ("f-mail-vc", "09:04", 4), ("w-mail-caterer", "09:08", 5), ("f-doc-memo", "09:13", 15),
 ("f-claude", "09:28", 10), ("f-doc-memo", "09:38", 8), ("yt-lofi", "09:46", 4),
 ("w-chat-sister", "11:00", 5), ("w-sheet-guests", "11:05", 15), ("slack-random", "11:20", 3), ("w-page-venue", "11:23", 10),
 ("w-notes-sangeet", "11:33", 7), ("news-monsoon", "11:40", 3),
 ("f-keynote-deck", "14:00", 20), ("f-sheet-captable", "14:20", 10), ("w-chat-sister", "14:30", 2), ("f-calendly", "14:32", 3),
 ("f-keynote-deck", "14:35", 10),
 ("r-strava", "19:00", 5), ("r-notes-shoes", "19:05", 5), ("w-mail-caterer", "19:10", 4), ("r-guide", "19:14", 10),
 ("r-mail-bib", "19:24", 4), ("hub-gmail", "19:28", 3),
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
       "about": ("Third invented day, the meaning test. Not real data. Things of one project share no telling word: no "
                 "client, person, file, folder or number repeats inside a project, and a few words repeat ACROSS projects "
                 "(a pitch deck and a venue's wooden deck, a 300-cover quote in the inbox). Only reading what each thing "
                 "is about can group it, so it shows what word matching cannot do; it is not a fair test of everyday data."),
       "items": items, "timeline": timeline}
out = sys.argv[1] if len(sys.argv) > 1 else "tests/fixtures/project_grouping/meaning_day.json"
open(out, "w").write(json.dumps(doc, indent=1, ensure_ascii=False) + "\n")
print(len(items), "items", len(timeline), "visits")
