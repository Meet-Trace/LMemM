"""Exact identity of what a Moment was about: the page (URL) or the file (accessibility document) the person had open.

Only identifiers present in at least half of a Moment's sampled observations count. They never join Moments to an
Activity by themselves: two Moments sharing one are always put in front of the judge, whatever their purposes say.
Window titles are not used (too often generic: "Untitled", the application name)."""

import json
from urllib.parse import urlsplit

from ..descriptions.read import ax_ok, core_members

SAMPLE = 40
SEGMENTS = 3


def normalise_url(value):
    """-> 'host/seg/seg/seg' without scheme, query, fragment or trailing slash; None for a bare host or a non-web value."""
    try:
        parts = urlsplit(value.strip())
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None
    segments = [s for s in parts.path.split("/") if s][:SEGMENTS]
    if not segments:
        return None
    return parts.hostname.lower().removeprefix("www.") + "/" + "/".join(segments)


def normalise_document(value):
    if not isinstance(value, str) or not value.strip():
        return None
    value = value.strip()
    if value.startswith(("http://", "https://")):
        found = normalise_url(value)
        return ("url", found) if found else None
    if value.startswith("file://"):
        value = urlsplit(value).path
    return ("document", value) if "/" in value else None


def identities_of(conn, moment_id):
    members = core_members(conn, moment_id)
    if not members:
        return []
    step = max(1, len(members) // SAMPLE)
    sampled = members[::step][:SAMPLE]
    counts = {}
    for oid in sampled:
        seen = set()
        row = conn.execute("SELECT context_json FROM observations WHERE observation_id=?", (oid,)).fetchone()
        try:
            url = ((json.loads(row[0]) or {}).get("window") or {}).get("url") if row else None
        except (ValueError, TypeError):
            url = None
        if isinstance(url, str) and normalise_url(url):
            seen.add(("url", normalise_url(url)))
        ax = ax_ok(conn, oid, "accessibility") or {}
        found = normalise_document(ax.get("document"))
        if found:
            seen.add(found)
        for key in seen:
            counts[key] = counts.get(key, 0) + 1
    need = max(1, len(sampled) // 2 + len(sampled) % 2)
    return sorted(key for key, n in counts.items() if n >= need)
