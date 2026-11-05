#!/usr/bin/env python3
"""
LMemM - what is the user doing on this screen? Deterministic rules, no model.

    describe(res, meta) -> {
        "app":      "Gmail",                       friendly name (site or app)
        "action":   "writing_email",               machine-readable verb
        "doing":    "Writing an email to ...",     one plain sentence
        "target":   "...",                         the thing acted on (chat, file, email subject)
        "details":  {...},                         only fields that mean something for this action
        "evidence": ["URL has compose=new", ...]   why the rules decided that
    }

The tracker turns a stream of these into a timeline: a new event only when
(app, action, target) changes, so "inbox -> compose opened -> typing" is three
events, and 40 screenshots of the same draft are one.
"""

import re
from urllib.parse import urlparse, parse_qs, unquote_plus

CHROME = {"tab", "address_bar", "bookmark", "menu_bar"}

SITES = {
    "mail.google.com": "Gmail", "web.whatsapp.com": "WhatsApp", "gemini.google.com": "Gemini",
    "chatgpt.com": "ChatGPT", "chat.openai.com": "ChatGPT", "claude.ai": "Claude",
    "docs.google.com": "Google Docs", "sheets.google.com": "Google Sheets",
    "calendar.google.com": "Google Calendar", "meet.google.com": "Google Meet",
    "www.youtube.com": "YouTube", "youtube.com": "YouTube", "github.com": "GitHub",
    "www.linkedin.com": "LinkedIn", "www.notion.so": "Notion", "app.slack.com": "Slack",
    "www.google.com": "Google Search", "drive.google.com": "Google Drive",
}
EDITORS = re.compile(r"vscode|visualstudio|cursor|xcode|jetbrains|pycharm|intellij|sublime|zed", re.I)
TERMINALS = re.compile(r"terminal|iterm|warp|ghostty|alacritty|kitty", re.I)
CHAT_APPS = re.compile(r"^(Messages|WhatsApp|Slack|Discord|Telegram|Signal|Microsoft Teams)$")
AI_APPS = re.compile(r"^(Claude|ChatGPT)$")
PLACEHOLDER = re.compile(r"^(?:\W{1,3}|[QO] )?\s?(type a message|message|ask|enter a prompt|"
                         r"reply|write|send a message|imessage|ask gemini|how can i help)", re.I)


# ---------------------------------------------------------------- helpers

def lines(res):
    return [o for o in res["objects"] if o["text"] and o["kind"] not in CHROME]


def chrome_bottom(res):
    ys = [o["box"][1] + o["box"][3] for o in res["objects"] if o["kind"] in CHROME and o["text"]]
    return max(ys) if ys else 0


def clean_title(t, *suffixes):
    t = re.sub(r"^\(\d[\d,]*\)\s*", "", t or "")          # "(24) WhatsApp" -> "WhatsApp"
    for s in suffixes:
        t = re.sub(r"\s*[-–—|]\s*" + re.escape(s) + r"$", "", t)
    return t.strip() or None


def short(t, n=80):
    t = re.sub(r"\s+", " ", t or "").strip()
    return t if len(t) <= n else t[: n - 1] + "…"


def biggest(objs):
    """The visually largest line: usually the page's main heading."""
    objs = [o for o in objs if len(o["text"]) > 3 and re.search(r"[A-Za-z]{3}", o["text"])]
    return max(objs, key=lambda o: o["box"][3], default=None)


KINDS = {
    "composing_email": "email_draft", "writing_email": "email_draft", "reading_email": "email",
    "browsing_inbox": "mailbox", "searching_email": "email_search",
    "chatting": "chat", "typing_message": "chat", "browsing_chats": "chat_list",
    "using_ai": "ai_conversation", "prompting_ai": "ai_conversation",
    "editing_code": "code_file", "searching_web": "web_search", "watching_video": "video",
    "searching_video": "video_search", "editing_document": "document", "in_meeting": "meeting",
    "reading_page": "web_page", "using_terminal": "terminal", "browsing_files": "folder",
    "using_app": "app_window",
}
TITLE_FIELDS = ("subject", "document", "chat", "conversation", "file", "video", "page",
                "query", "folder")


def state(app, action, doing, target=None, details=None, evidence=None, doing_typing=None):
    d = {k: v for k, v in (details or {}).items() if v not in (None, "", [], {})}
    title = next((d[k] for k in TITLE_FIELDS if d.get(k)), None)
    return {"app": app, "action": action, "kind": KINDS.get(action, action), "title": title,
            "doing": doing, "target": target,
            "details": d, "evidence": evidence or [],
            # how to say it when the pixels show you typing (activity.py decides that)
            "doing_typing": doing_typing or doing}


# ---------------------------------------------------------------- per-app rules

def gmail(res, meta):
    url = meta.get("url") or ""
    frag = urlparse(url).fragment
    objs = lines(res)

    anchor = next((o for o in objs if re.fullmatch(r"(New Message|Draft saved|Saving…?|Saved)", o["text"])), None)
    send = next((o for o in objs if o["kind"] == "button" and o["text"].lower().startswith("send")), None)
    if "compose=" in frag or anchor or send:
        ev = (["URL has compose="] if "compose=" in frag else []) + \
             ([f"'{anchor['text']}' window"] if anchor else []) + (["'Send' button"] if send else [])
        # Gmail's compose window, top to bottom: header, To row (~+26px),
        # Subject row (~+52px), body, signature (indented), toolbar, Send
        if send:
            # The compose window's header is the topmost line in the Send button's
            # column. It reads "New Message" until a subject is typed, then it shows
            # the subject itself, so match it by position, not by its text.
            sx, sy = send["box"][0], send["box"][1]
            col = [o for o in objs if sx - 40 <= o["box"][0] <= sx + 5 and sy - 430 < o["box"][1] < sy - 20]
            anchor = min(col, key=lambda o: o["box"][1], default=anchor)
        m = re.search(r"compose=([\w-]+)", frag)
        draft_ref = "draft:" + (m.group(1) if m else "new")
        if not (anchor or send):              # compose URL, but the window isn't drawn yet
            return state("Gmail", "composing_email", "Opened a new email", target=draft_ref,
                         evidence=ev)
        x0 = (anchor or send)["box"][0]
        y0 = anchor["box"][1] if anchor else (send["box"][1] - 380)
        y1 = send["box"][1] - 45 if send else 10 ** 6
        box = [o for o in objs if o["box"][0] >= x0 - 10 and y0 < o["box"][1] < y1 and o is not anchor]
        to, subject, body, sig_y = [], None, [], None
        for o in sorted(box, key=lambda o: o["box"][1]):
            t, dy = o["text"], o["box"][1] - y0
            if t in ("Subject", "To", "Recipients", "Cc", "Bcc", "Cc Bcc", "From"):
                continue
            if dy < 40:
                to.append(re.sub(r"^To\s+", "", t))
            elif dy < 70:
                subject = t
            elif t.strip() == "--" or o["box"][0] > x0 + 60 or (sig_y and o["box"][1] > sig_y):
                sig_y = sig_y or o["box"][1]           # signature: indented block, or after "--"
            elif len(re.findall(r"[A-Za-z]", t)) >= 3:
                body.append(t)
        draft = short(" ".join(body), 200) if body else None
        typing = bool(draft or to or subject)
        doing = ("Writing an email" if typing else "Opened a new email") + \
                (f" to {', '.join(to)}" if to else "") + \
                (f', subject "{short(subject, 60)}"' if subject else " (no subject yet)")
        return state("Gmail", "writing_email" if typing else "composing_email", doing,
                     target=draft_ref, details={"to": to, "subject": subject, "draft": draft},
                     evidence=ev)

    if re.match(r"(inbox|starred|sent|all|label/[^/]+|category/[^/]+|search/[^/]+)/[A-Za-z0-9]{16,}", frag):
        content = [o for o in objs if o["box"][1] > chrome_bottom(res)]
        subj_o = biggest(content)
        subj = subj_o["text"] if subj_o else clean_title(meta.get("tab_title"), "Gmail")
        sender = next((o["text"] for o in content
                       if "<" in o["text"] or "«" in o["text"] or "(via" in o["text"]), None)
        sender = re.split(r"\s*[<«]", sender)[0] if sender else None
        return state("Gmail", "reading_email", f'Reading the email "{short(subj, 70)}"'
                     + (f" from {sender}" if sender else ""),
                     target="message:" + frag.split("?")[0].rsplit("/", 1)[-1],
                     details={"subject": subj, "from": sender},
                     evidence=["URL points at one message"])

    if frag.startswith("search/"):
        q = unquote_plus(frag.split("/", 1)[1])
        return state("Gmail", "searching_email", f'Searching email for "{q}"', target=q,
                     details={"query": q}, evidence=["URL is a Gmail search"])

    folder = (frag.split("?")[0].split("/")[0] or "inbox")
    unread = re.search(r"\((\d[\d,]*)\)", meta.get("tab_title") or "")
    return state("Gmail", "browsing_inbox", f"Looking through the {folder}"
                 + (f" ({unread.group(1)} unread)" if unread else ""),
                 target=folder, details={"folder": folder, "unread": unread and unread.group(1)},
                 evidence=[f"URL is the {folder} list"])


def chat(res, meta, app):
    objs = lines(res)
    w = res["image_size"]["w"]
    top = chrome_bottom(res)
    if app == "WhatsApp":
        # the open chat's name sits at the top of the right-hand pane
        head = next((o for o in objs if o["box"][0] > w * 0.33 and top < o["box"][1] < top + 45
                     and len(o["text"]) > 1), None)
        if head is None and meta.get("quick"):     # a strip: no toolbar found, take the first line of the right pane
            head = next((o for o in sorted(objs, key=lambda o: o["box"][1])
                         if o["box"][0] > w * 0.33 and len(o["text"]) > 1), None)
        who = head["text"] if head else None
    else:
        who = meta.get("window") if meta.get("window") not in (None, app) else None
    ev = [f"open chat header: {who}" if who else "no chat header found"]
    if meta.get("trail_chat"):         # the app itself named the chat (accessibility): exact, no OCR noise
        return chat_state(app, meta["trail_chat"])
    if not who:
        return state(app, "browsing_chats", f"Looking through chats in {app}", target=None, evidence=ev)
    return state(app, "chatting", f"In the chat with {who}", target=who, details={"chat": who},
                 evidence=ev, doing_typing=f"Typing a message to {who}")


def chat_state(app, who):
    """The state of being in chat `who`, when something other than OCR (the app's accessibility
    labels, via the event trail) named it. Same shape and same ref as chat() gives."""
    return state(app, "chatting", f"In the chat with {who}", target=who, details={"chat": who},
                 evidence=["chat named by the app (accessibility)"], doing_typing=f"Typing a message to {who}")


def ai(res, meta, app):
    title = clean_title(meta.get("tab_title") or meta.get("window"), "Google Gemini", "Gemini",
                        "ChatGPT", "Claude")
    if title == app:
        title = None
    path = urlparse(meta.get("url") or "").path
    conv = re.search(r"/(?:app|c|chat)/([\w-]{6,})", path)
    about = f' about "{short(title, 70)}"' if title else ""
    return state(app, "using_ai", f"Talking to {app}{about}", doing_typing=f"Writing a prompt to {app}{about}",
                 target=conv.group(1) if conv else title, details={"conversation": title},
                 evidence=["conversation title from window"] if title else [])


def editor(res, meta, app):
    t = meta.get("window") or ""
    unsaved = t.startswith("● ")
    t = t.lstrip("● ").strip()
    parts = [p.strip() for p in re.split(r"\s+[—–-]\s+", t) if p.strip()]
    f = parts[0] if parts else None
    project = parts[1] if len(parts) > 1 else None
    hexname = re.fullmatch(r"(.+?)\s+[0-9a-f]{16,}", f or "")
    if hexname and not project:          # "LMemM f2f9…": an editor panel named by an id, not a file
        f, project = None, hexname.group(1)
    terminal_open = any(o["text"] and "TERMINAL" in o["text"] for o in res["objects"])
    doing = (f"Editing {f}" if f else f"Coding in {app}") + (f" in {project}" if project else "") \
        + (" (unsaved changes)" if unsaved else "")
    return state(app, "editing_code", doing, target=f"{project}/{f}" if project and f else (f or project),
                 details={"file": f, "project": project, "unsaved": unsaved or None,
                          "terminal_panel": terminal_open or None},
                 evidence=["file and project from window title"] if f else [])


def browser_page(res, meta, app):
    url = meta.get("url") or ""
    u = urlparse(url)
    site = SITES.get(u.netloc, u.netloc.replace("www.", ""))
    title = clean_title(meta.get("tab_title"), site, "YouTube", "Google Docs", "Google Search")
    q = parse_qs(u.query)
    if "google." in u.netloc and u.path == "/search" or "bing.com" in u.netloc or "duckduckgo" in u.netloc:
        query = (q.get("q") or [None])[0]
        return state("Web search", "searching_web", f'Searching the web for "{query}"', target=query,
                     details={"query": query}, evidence=["search URL"])
    if site == "YouTube" and u.path == "/watch":
        return state("YouTube", "watching_video", f'Watching "{short(title, 70)}"',
                     target=(q.get("v") or [title])[0],
                     details={"video": title}, evidence=["YouTube watch URL"])
    if site == "YouTube" and u.path == "/results":
        query = (q.get("search_query") or [None])[0]
        return state("YouTube", "searching_video", f'Searching YouTube for "{query}"', target=query,
                     details={"query": query}, evidence=["YouTube search URL"])
    if site in ("Google Docs", "Google Sheets") or "/document/" in u.path or "/spreadsheets/" in u.path:
        doc = re.search(r"/d/([\w-]{10,})", u.path)
        result = state(site, "editing_document", f'Working on "{short(title, 70)}"',
                       target=doc.group(1) if doc else title,
                       details={"document": title}, evidence=["Docs URL"])
        # A source document ID survives edits, renames, scrolling and app restarts.
        # Title-only references and arbitrary sites with similar paths do not.
        result["stable_identity"] = bool(
            u.hostname == "docs.google.com" and doc
            and re.match(r"^/document/(?:u/\d+/)?d/[\w-]{10,}(?:/|$)", u.path))
        return result
    if site == "Google Meet":
        return state(site, "in_meeting", "In a Google Meet call", target=u.path,
                     evidence=["Meet URL"])
    return state(site or app, "reading_page", f'Reading "{short(title, 70)}" on {site}' if title
                 else f"Browsing {site}", target=f"{u.netloc}{u.path}".rstrip("/"),
                 details={"page": title, "url": url}, evidence=["browser tab"])


def describe(res, meta):
    app = meta.get("app") or "?"
    bundle = meta.get("bundle_id") or ""
    site = meta.get("site") or ""
    friendly = SITES.get(site)

    if friendly == "Gmail":
        return gmail(res, meta)
    if friendly == "WhatsApp" or CHAT_APPS.match(app):
        return chat(res, meta, friendly or app)
    if friendly in ("Gemini", "ChatGPT", "Claude") or AI_APPS.match(app):
        return ai(res, meta, friendly or app)
    if meta.get("url"):
        return browser_page(res, meta, app)
    if EDITORS.search(bundle) or EDITORS.search(app):
        return editor(res, meta, "VS Code" if "vscode" in bundle.lower() else app)
    if TERMINALS.search(app):
        t = terminal_folder(meta.get("window"))
        return state(app, "using_terminal", "In the terminal" + (f": {short(t, 60)}" if t else ""),
                     target=t, details={"folder": t}, evidence=["terminal app"])
    if app == "Finder":
        t = meta.get("window")
        return state("Finder", "browsing_files", f"Browsing files in {t}" if t else "Browsing files",
                     target=t, details={"folder": t}, evidence=["Finder window title"])
    # unknown app: the window title is the stable "where"; the main heading
    # only flavours the sentence, so moving between screens of one app is one event
    t = meta.get("window")
    head = biggest([o for o in lines(res) if o["kind"] == "heading"])
    shown = t if t and t != app else (head["text"] if head else None)
    return state(app, "using_app", f"Using {app}" + (f": {short(shown, 70)}" if shown else ""),
                 target=t, details={"document": t if t and t != app else None},
                 evidence=["no specific rule for this app"])


def place_name(item):
    """A name for a remembered thing whose title is empty: what its window was called (kept in its
    ref as "app|kind|target"), cleaned the same way capture now cleans it. None when the window had
    no name of its own (only the app's name, an id, or nothing)."""
    if item.get("title"):
        return item["title"]
    parts = str(item.get("ref") or "").split("|", 2)
    target = parts[2].strip() if len(parts) == 3 else ""
    if item.get("kind") == "terminal":
        target = terminal_folder(target) or ""
    if item.get("kind") == "code_file":
        target = re.sub(r"/?(?:[^/\s]+\s+)?[0-9a-f]{16,}$|/None$", "", target).strip("/")
    if not target or target.casefold() == str(item.get("app") or "").casefold() or re.fullmatch(r"[\w-]{20,}", target):
        return None
    return target


def terminal_folder(title):
    """"LMemM — -zsh — 202×60" -> "LMemM": the folder, without the shell and the window size, so
    resizing the window or running a command does not make it a different thing."""
    if not title:
        return None
    parts = [p.strip() for p in re.split(r"\s+[—–-]\s+", title) if p.strip()]
    keep = [p for p in parts if not re.fullmatch(r"-?\w*sh|\d+\s*[×x]\s*\d+", p)]
    return keep[0] if keep else title


def ref(s):
    """Stable identity of the thing on screen: the same draft, document, chat or
    page gets the same ref every time you come back to it, however it has changed."""
    return f'{s["app"]}|{s["kind"]}|{s["target"] or ""}'
