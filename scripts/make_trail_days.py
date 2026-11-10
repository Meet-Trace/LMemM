#!/usr/bin/env python3
"""Writes three invented test days into tests/fixtures/project_grouping/: pivot_day, versions_day, dev_day.

    python3 scripts/make_trail_days.py            # rewrites all three
    python3 scripts/make_trail_days.py pivot_day  # one of them

Not real data. Each day is built around a case the older days do not have, from the approved product
definition (a project is a piece of work with an outcome; the trail, the topic and the timing must agree):

    pivot_day     one project changes what it is about halfway through the day (a pet-sitter marketplace
                  becomes a dog-walking subscription). After the change almost no word is shared with the
                  morning; the doc and the chat with the co-founder carry on. A puppy-adoption project
                  talks about dogs and walks too, so the topic alone would join the wrong things.
    versions_day  files that go v1, v2, final, "final_FINAL (1)" in three client projects. Two projects own
                  a file with the same name (Budget_v2.xlsx) in different folders, and a Downloads listing
                  names files of all three. The same file name is not the same file.
    dev_day       three pieces of developer work. Two of them are in the SAME repository on different
                  branches (a feature and a bug fix), so the repository does not tell them apart; the
                  branch does, and a stash message in one terminal names the other branch.

Labels are "Project/Workstream"; scoring uses the part before the slash. Things with no label (inboxes,
news, music, idle log reading) belong to no project.
"""
import json
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "project_grouping"

# id: (app, kind, title, state, typed, received, read, screen, label)
# ---------------------------------------------------------------------------------------------- pivot day
WM, WS, BP, GST = "Wagtail/Marketplace idea", "Wagtail/Walking subscription", "Biscuit the puppy", "GST return Q2"
PIVOT = {
 "wg-doc-memo": ("Google Docs", "document", "Wagtail - idea memo", {"url": "https://docs.google.com/document/d/1Wt4Xq"},
    ["Wagtail is a marketplace where pet owners book trusted sitters", "Take rate: 15% on every booking",
     "New direction: a monthly dog-walking subscription, five walks a week, the same walker every time"],
    [], ["Comment from Tara: how do we stop owners going off the app?"],
    ["Wagtail: from marketplace to walking subscription"], [WM, WS]),
 "wg-chat-tara": ("WhatsApp", "chat", "Tara", {"chat": "Tara"},
    ["Marketplace cold start is brutal, call at 2?", "Yes, our own walkers on a monthly plan"],
    ["Sitters keep taking owners off the app", "What if the walkers were ours, on a monthly plan?"],
    [], ["Tara: What if the walkers were ours, on a monthly plan?"], [WM, WS]),
 "wg-page-rover": ("Safari", "page", "How Rover fees work | Rover Help", {"url": "https://help.rover.example/fees"},
    [], [], ["Service fee of 20% is taken from sitters", "Owners pay a 7% booking fee"], ["Fees and payouts"], [WM]),
 "wg-sheet-sitters": ("Google Sheets", "spreadsheet", "Sitter supply survey", {"url": "https://docs.google.com/spreadsheets/d/1Sv7Lp"},
    ["Active sitters in Pune: 140", "Repeat booking rate: 22%"], [], ["Why sitters leave: unpredictable income"],
    ["City | Active sitters | Average rate"], [WM]),
 "wg-sheet-routes": ("Google Sheets", "spreadsheet", "Route density by pincode", {"url": "https://docs.google.com/spreadsheets/d/1Rt3Wm"},
    ["411045: 38 dogs within 2 km", "One walker covers 12 dogs a day"], [], ["A walk is 30 minutes, six dogs per block"],
    ["Pincode | Dogs | Walkers needed"], [WS]),
 "wg-page-pricing": ("Safari", "page", "Stripe Billing | Subscriptions", {"url": "https://stripe.example/billing/subscriptions"},
    [], [], ["Recurring billing with monthly plans", "Free trial and proration"], ["Create a plan"], [WS]),
 "wg-notion-walkers": ("Notion", "document", "Walker onboarding", {"url": "https://www.notion.example/Walker-onboarding-8f2c"},
    ["ID check, police verification, two trial walks", "Salary: 18k a month fixed plus tips"], [], [], [], [WS]),
 "bs-mail-vet": ("Gmail", "email", "Biscuit's first vaccination - Dr. Anand Clinic",
    {"subject": "Biscuit's first vaccination - Dr. Anand Clinic", "from": "frontdesk@dranandclinic.example"},
    [], ["Vaccine due on Saturday at 10am", "Bring the adoption papers"], [], [], [BP]),
 "bs-page-crate": ("Safari", "page", "Dog crate size guide for Labradors | PetZone", {"url": "https://www.petzone.example/crate-size-labrador"},
    [], [], ["A 12 week Labrador puppy needs a 36 inch crate", "Walks: five minutes per month of age, twice a day"],
    ["Crate size by weight"], [BP]),
 "bs-notes-feeding": ("Notes", "note", "Biscuit feeding", {},
    ["Morning 1 cup, night 1 cup", "No grapes, no chocolate"], [], [], [], [BP]),
 "bs-chat-shelter": ("WhatsApp", "chat", "Paws Shelter", {"chat": "Paws Shelter"},
    ["Can we pick him up on Friday?"], ["His microchip number is on the form", "Bring a leash and a towel"], [],
    ["Paws Shelter: His microchip number is on the form"], [BP]),
 "gs-sheet": ("Microsoft Excel", "spreadsheet", "GST_Q2_input.xlsx", {"file": "~/Documents/Accounts/GST_Q2_input.xlsx", "sheet": "Purchases"},
    ["Input credit 1.82 L", "Output tax 3.1 L"], [], ["Invoice | GSTIN | Taxable | IGST"], ["Invoice | GSTIN | Taxable | IGST"], [GST]),
 "gs-page-portal": ("Safari", "page", "GST Portal - Return Dashboard", {"url": "https://gst.example/returns/dashboard"},
    [], [], ["GSTR-3B is due on 20 Nov", "Late fee: 50 per day"], ["Return filing period: Sep 2026"], [GST]),
 "gs-mail-ca": ("Gmail", "email", "Re: Q2 return - documents needed", {"subject": "Re: Q2 return - documents needed", "from": "ca@mehtaassociates.example"},
    [], ["Send the purchase invoices for September", "Reconcile with GSTR-2B first"], [], [], [GST]),
 "hub-gmail": ("Gmail", "mailbox", "Inbox (74)", {}, [],
    ["Biscuit's first vaccination - Dr. Anand Clinic", "Re: Q2 return - documents needed", "Promotions: 40% off monsoon sale"], [], [], []),
 "slack-random": ("Slack", "chat", "#random", {"chat": "#random", "workspace": "Lumen Labs"},
    [], ["who's up for lunch at 1?", "the new coffee machine is a game changer"], [], [], []),
 "yt-lofi": ("YouTube", "page", "lofi hip hop radio - beats to relax/study to", {"url": "https://www.youtube.com/watch?v=jfKfPfyJRdk"},
    [], [], ["LIVE"], [], []),
 "news-monsoon": ("Safari", "page", "Monsoon may arrive early in Mumbai this year", {"url": "https://www.dailyherald.example/weather/monsoon-early"},
    [], [], ["IMD forecasts above-normal rainfall"], [], []),
}
PIVOT_TIMELINE = [  # (item, from, minutes); four sittings, projects interleaved
 ("hub-gmail", "09:00", 3), ("wg-doc-memo", "09:03", 20), ("wg-page-rover", "09:23", 8), ("wg-sheet-sitters", "09:31", 12),
 ("wg-chat-tara", "09:43", 4), ("gs-mail-ca", "09:47", 5), ("yt-lofi", "09:52", 3),
 ("gs-sheet", "11:30", 18), ("gs-page-portal", "11:48", 6), ("slack-random", "11:54", 3), ("bs-mail-vet", "11:57", 5),
 ("wg-chat-tara", "14:00", 6), ("wg-doc-memo", "14:06", 14), ("wg-sheet-routes", "14:20", 15), ("news-monsoon", "14:35", 3),
 ("wg-page-pricing", "14:38", 8), ("wg-notion-walkers", "14:46", 12), ("wg-doc-memo", "14:58", 6),
 ("bs-page-crate", "19:00", 8), ("bs-notes-feeding", "19:08", 5), ("bs-chat-shelter", "19:13", 4), ("hub-gmail", "19:17", 3),
]

# --------------------------------------------------------------------------------------------- versions day
HB, FL, LN = "Harbour proposal", "Flat renovation", "Lantern annual report"
VERSIONS = {
 "hb-word-v1": ("Microsoft Word", "document", "Harbour_Proposal_v1.docx", {"file": "~/Documents/Harbour/Harbour_Proposal_v1.docx"},
    ["Scope: 3 workshops over 6 weeks", "Fee: 4.2 lakh plus GST"], [], ["Objective: cut dock turnaround time"], ["Harbour Logistics - proposal"], [f"{HB}/Proposal"]),
 "hb-word-v2": ("Microsoft Word", "document", "Harbour_Proposal_v2.docx", {"file": "~/Documents/Harbour/Harbour_Proposal_v2.docx"},
    ["Scope: 4 workshops over 8 weeks", "Fee: 5.0 lakh plus GST"], [], ["Objective: cut dock turnaround time", "Comment from Vikram: add a pilot phase"],
    ["Harbour Logistics - proposal"], [f"{HB}/Proposal"]),
 "hb-word-final": ("Microsoft Word", "document", "Harbour_Proposal_final.docx", {"file": "~/Documents/Harbour/Harbour_Proposal_final.docx"},
    ["Pilot phase: 2 weeks at the Nhava Sheva terminal", "Payment: 50% advance"], [], ["Scope: 4 workshops over 8 weeks", "Fee: 5.0 lakh plus GST"],
    ["Harbour Logistics - proposal"], [f"{HB}/Proposal"]),
 "hb-word-final-copy": ("Microsoft Word", "document", "Harbour_Proposal_final_FINAL (1).docx", {"file": "~/Downloads/Harbour_Proposal_final_FINAL (1).docx"},
    [], [], ["Pilot phase: 2 weeks at the Nhava Sheva terminal", "Payment: 50% advance", "Fee: 5.0 lakh plus GST"],
    ["Harbour Logistics - proposal"], [f"{HB}/Proposal"]),
 "hb-mail-vikram": ("Gmail", "email", "Re: Proposal - one more round", {"subject": "Re: Proposal - one more round", "from": "vikram@harbourlogistics.example"},
    ["Attached: Harbour_Proposal_final.docx"], ["Can you send the final with the pilot phase?", "Our board meets on the 20th"], [], [], [f"{HB}/Proposal"]),
 "hb-sheet-fee": ("Microsoft Excel", "spreadsheet", "Budget_v2.xlsx", {"file": "~/Documents/Harbour/Budget_v2.xlsx", "sheet": "Fees"},
    ["Workshop day rate 62,500", "Travel 38,000"], [], ["Line item | Days | Rate | Total"], ["Line item | Days | Rate | Total"], [f"{HB}/Fees"]),
 "fl-sheet-v1": ("Microsoft Excel", "spreadsheet", "Budget_v1.xlsx", {"file": "~/Documents/Flat/Budget_v1.xlsx", "sheet": "Rooms"},
    ["Kitchen 3.5 L", "Bathroom 1.8 L"], [], ["Item | Estimate | Paid"], ["Item | Estimate | Paid"], [f"{FL}/Budget"]),
 "fl-sheet-v2": ("Microsoft Excel", "spreadsheet", "Budget_v2.xlsx", {"file": "~/Documents/Flat/Budget_v2.xlsx", "sheet": "Rooms"},
    ["Kitchen 4.1 L", "Bathroom 1.8 L", "Contingency 10%"], [], ["Item | Estimate | Paid"], ["Item | Estimate | Paid"], [f"{FL}/Budget"]),
 "fl-chat-contractor": ("WhatsApp", "chat", "Imran (contractor)", {"chat": "Imran (contractor)"},
    ["Send me the revised quote"], ["Kitchen cabinets will take 3 weeks", "A quartz counter adds 60k"], [],
    ["Imran (contractor): A quartz counter adds 60k"], [f"{FL}/Contractor"]),
 "fl-page-quartz": ("Safari", "page", "Quartz vs granite countertops | HomeGuide", {"url": "https://www.homeguide.example/quartz-vs-granite"},
    [], [], ["Quartz: 8k to 14k per square metre", "Granite needs sealing every year"], [], [f"{FL}/Contractor"]),
 "ln-pdf-v2": ("Preview", "document", "Lantern_Report_v2.pdf", {"file": "~/Documents/Lantern/Lantern_Report_v2.pdf"},
    [], [], ["Revenue grew 18% year on year", "Chapter 3: Community programmes"], ["Lantern Foundation - annual report"], [f"{LN}/Report"]),
 "ln-pdf-final": ("Preview", "document", "Lantern_Report_final.pdf", {"file": "~/Documents/Lantern/Lantern_Report_final.pdf"},
    [], [], ["Revenue grew 18.4% year on year", "Chapter 3: Community programmes", "Auditor's note added"],
    ["Lantern Foundation - annual report"], [f"{LN}/Report"]),
 "ln-doc-notes": ("Google Docs", "document", "Review comments", {"url": "https://docs.google.com/document/d/1Lr5Qz"},
    ["Fix the chart on page 14", "Replace the cover photo"], [], [], [], [f"{LN}/Report"]),
 "ln-mail-print": ("Gmail", "email", "Print-ready file for the annual report", {"subject": "Print-ready file for the annual report", "from": "priya@lanternfoundation.example"},
    [], ["Print run of 2,000 copies", "Use the final PDF, not v2"], [], [], [f"{LN}/Report"]),
 "dl-finder": ("Finder", "folder", "Downloads", {"folder": "~/Downloads"}, [], [],
    ["Harbour_Proposal_final_FINAL (1).docx", "Lantern_Report_final.pdf", "Budget_v2.xlsx", "IMG_2210.heic"], [], []),
 "hub-gmail": ("Gmail", "mailbox", "Inbox (58)", {}, [],
    ["Re: Proposal - one more round", "Print-ready file for the annual report", "Promotions: 40% off monsoon sale"], [], [], []),
 "slack-random": ("Slack", "chat", "#random", {"chat": "#random", "workspace": "Lumen Labs"},
    [], ["who's up for lunch at 1?", "the new coffee machine is a game changer"], [], [], []),
 "news-monsoon": ("Safari", "page", "Monsoon may arrive early in Mumbai this year", {"url": "https://www.dailyherald.example/weather/monsoon-early"},
    [], [], ["IMD forecasts above-normal rainfall"], [], []),
}
VERSIONS_TIMELINE = [
 ("hub-gmail", "09:00", 3), ("hb-word-v1", "09:03", 18), ("hb-sheet-fee", "09:21", 10), ("hb-word-v2", "09:31", 14),
 ("fl-chat-contractor", "09:45", 4), ("slack-random", "09:49", 3),
 ("fl-sheet-v1", "11:00", 12), ("fl-page-quartz", "11:12", 8), ("fl-sheet-v2", "11:20", 15), ("fl-chat-contractor", "11:35", 3),
 ("news-monsoon", "11:38", 3),
 ("ln-pdf-v2", "14:00", 10), ("ln-doc-notes", "14:10", 12), ("ln-pdf-final", "14:22", 10), ("dl-finder", "14:32", 2),
 ("ln-mail-print", "14:34", 4),
 ("hb-mail-vikram", "17:30", 5), ("hb-word-final", "17:35", 15), ("dl-finder", "17:50", 2), ("hb-word-final-copy", "17:52", 6),
 ("hub-gmail", "17:58", 3),
]

# ------------------------------------------------------------------------------------------------ dev day
CX, LT, HS = "CSV export", "Login timeout bug", "Home server move"
DEV = {
 "cx-term": ("Terminal", "terminal", "ledgerly (feat/csv-export) - zsh", {"cwd": "~/code/ledgerly"},
    ["$ git checkout -b feat/csv-export", "$ pytest tests/test_export.py -q", "$ git push -u origin feat/csv-export"],
    [], ["3 passed in 0.41s"], ["Switched to a new branch 'feat/csv-export'"], [CX]),
 "cx-code": ("Visual Studio Code", "code_file", "export.py - ledgerly", {"file": "~/code/ledgerly/ledgerly/export.py"},
    ["def export_csv(rows, path):", "writer = csv.writer(f, quoting=csv.QUOTE_MINIMAL)", "# TODO: utf-8 BOM for Excel",
     "writer.writerow(['date', 'account', 'amount', 'memo'])"],
    [], ["class ExportJob", "rows = ledger.query(account=account)"], [], [CX]),
 "cx-page-so": ("Safari", "page", "python - csv.writer and unicode in Excel - Stack Overflow", {"url": "https://stackoverflow.example/questions/csv-writer-unicode-excel"},
    [], [], ["Open the file with encoding='utf-8-sig' so Excel detects UTF-8"], [], [CX]),
 "cx-page-pr": ("GitHub", "page", "Add CSV export by account #212 - tidepool/ledgerly", {"url": "https://github.com/tidepool/ledgerly/pull/212"},
    [], [], ["feat/csv-export into main", "2 files changed", "Review requested: Meera"], [], [CX]),
 "cx-chat-slack": ("Slack", "chat", "#ledgerly-dev", {"chat": "#ledgerly-dev", "workspace": "Tidepool"},
    ["Yes, adding the memo column now"], ["Meera: can the export include the memo column?"], [], [], [CX]),
 "lt-term": ("Terminal", "terminal", "ledgerly (fix/login-timeout) - zsh", {"cwd": "~/code/ledgerly"},
    ["$ git stash", "$ git checkout -b fix/login-timeout", "$ pytest tests/test_session.py -q"],
    [], ["1 failed: test_session_expires_after_idle"],
    ["Saved working directory and index state WIP on feat/csv-export", "Switched to a new branch 'fix/login-timeout'"], [LT]),
 "lt-code": ("Visual Studio Code", "code_file", "session.py - ledgerly", {"file": "~/code/ledgerly/ledgerly/session.py"},
    ["SESSION_TTL = 30 * 60", "if now - last_seen > SESSION_TTL: raise SessionExpired"], [], ["def refresh_session(token)"], [], [LT]),
 "lt-page-issue": ("GitHub", "page", "Users logged out after 5 minutes - Issue #198 - tidepool/ledgerly", {"url": "https://github.com/tidepool/ledgerly/issues/198"},
    [], [], ["Reported by 14 users", "Started after release 2.3.1"], [], [LT]),
 "lt-page-sentry": ("Safari", "page", "TimeoutError: session refresh - Sentry", {"url": "https://sentry.example/tidepool/ledgerly/issues/5531"},
    [], [], ["Seen 212 times in 24 hours", "First seen in release 2.3.1"], [], [LT]),
 "hs-notes": ("Notes", "note", "Server move checklist", {},
    ["Back up /mnt/nas before the cutover", "Update the DNS record for cloud.example"], [], [], [], [HS]),
 "hs-page-docs": ("Safari", "page", "Migrate Nextcloud to a new server - Nextcloud docs", {"url": "https://docs.nextcloud.example/admin/migrating"},
    [], [], ["Copy the data directory with rsync", "Run occ maintenance:mode --on before copying"], [], [HS]),
 "hs-code": ("Visual Studio Code", "code_file", "docker-compose.yml - homeserver", {"file": "~/homeserver/docker-compose.yml"},
    ["services: jellyfin, nextcloud", "volumes: /mnt/nas/media:/media"], [], ["restart: unless-stopped"], [], [HS]),
 "hs-term": ("Terminal", "terminal", "ssh pi@192.168.1.40 - zsh", {"cwd": "~/homeserver"},
    ["$ ssh pi@192.168.1.40", "$ docker compose up -d"], [], [], ["Container nextcloud Started"], [HS]),
 "hub-gmail": ("Gmail", "mailbox", "Inbox (31)", {}, [],
    ["[tidepool/ledgerly] Pull request #212", "Sentry alert: TimeoutError in ledgerly", "Your weekly digest"], [], [], []),
 "hn-front": ("Safari", "page", "Hacker News", {"url": "https://news.ycombinator.example/"},
    [], [], ["Show HN: a faster JSON parser", "The case against microservices", "Ask HN: how do you stay focused?"], [], []),
 "n-console": ("Console", "app", "system.log", {},
    [], [], ["Nov 11 09:14:02 kernel: AppleThunderboltNHI", "Nov 11 09:14:05 mDNSResponder: Sleep proxy"], [], []),
}
DEV_TIMELINE = [
 ("hub-gmail", "09:00", 3), ("cx-term", "09:03", 4), ("cx-code", "09:07", 25), ("cx-page-so", "09:32", 6), ("cx-code", "09:38", 10),
 ("cx-term", "09:48", 3), ("cx-chat-slack", "09:51", 3), ("hn-front", "09:54", 4),
 ("hs-notes", "11:00", 6), ("hs-page-docs", "11:06", 8), ("hs-code", "11:14", 12), ("hs-term", "11:26", 10), ("n-console", "11:36", 4),
 ("cx-page-pr", "14:00", 5), ("cx-chat-slack", "14:05", 3), ("lt-page-issue", "14:08", 4), ("lt-page-sentry", "14:12", 6),
 ("lt-term", "14:18", 5), ("lt-code", "14:23", 20), ("lt-term", "14:43", 4), ("cx-term", "14:47", 3), ("cx-code", "14:50", 8),
 ("hs-term", "18:30", 8), ("hs-code", "18:38", 6),
]

DAYS = {
 "pivot_day": ("2026-11-09", PIVOT, PIVOT_TIMELINE,
    "Fourth invented day, the pivot test. Not real data. One project (Wagtail) is a pet-sitter marketplace in the "
    "morning and a dog-walking subscription after lunch: afternoon things share almost no word with the morning's. "
    "The memo and the chat with the co-founder carry on, so the trail joins them. A puppy-adoption project talks "
    "about dogs and walks too, so the topic alone joins the wrong things. Labels name the stage inside the project."),
 "versions_day": ("2026-11-10", VERSIONS, VERSIONS_TIMELINE,
    "Fifth invented day, the version-chain test. Not real data. Three client projects each keep files that go "
    "v1, v2, final, and one is a copy named final_FINAL (1) in Downloads. Harbour and the flat renovation each own "
    "a file called Budget_v2.xlsx in different folders with different contents: the same name is not the same file. "
    "A Downloads listing names files of all three projects and belongs to none."),
 "dev_day": ("2026-11-11", DEV, DEV_TIMELINE,
    "Sixth invented day, the developer test. Not real data. Two pieces of work, a feature and a bug fix, are in the "
    "same repository, so the repository does not tell them apart; the branch in each terminal does, and the bug-fix "
    "terminal's stash message names the feature branch. A home-server move shares tools with neither. An inbox, a "
    "news front page and idle log reading belong to no project."),
}


def build(day, items_def, timeline_def, about):
    seen = {i for i, _, _ in timeline_def}
    assert seen == set(items_def), (seen ^ set(items_def))
    timeline, spans, last_end = [], defaultdict(list), None
    for item, start, mins in sorted(timeline_def, key=lambda t: t[1]):
        a = datetime.fromisoformat(f"{day}T{start}:00")
        b = a + timedelta(minutes=mins)
        assert last_end is None or a >= last_end, f"{item} starts before the previous visit ends"
        last_end = b
        timeline.append({"item": item, "from": a.isoformat(), "to": b.isoformat()})
        spans[item].append((a, b))
    items = {}
    for iid, (app, kind, title, state, typed, received, read, screen, label) in items_def.items():
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
    return {"version": 1, "about": about, "items": items, "timeline": timeline}


def main(argv):
    wanted = argv or list(DAYS)
    for name in wanted:
        day, items_def, timeline_def, about = DAYS[name]
        doc = build(day, items_def, timeline_def, about)
        (OUT / f"{name}.json").write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n")
        print(name, len(doc["items"]), "items", len(doc["timeline"]), "visits")


if __name__ == "__main__":
    main(sys.argv[1:])
