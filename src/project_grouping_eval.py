"""LMemM - how good is a grouping of things into projects? Plain functions, no UI, all tested.

Step 0 of the auto-project plan (design/auto-project-creation-plan.md): before any rule or model
groups things on its own, we need a way to tell whether a grouping is right. A *labelled day* is a
set of items (the memory.json item shape) where each item also carries "label": the project paths
it truly belongs to, such as ["180DC/Pricing model"], or [] for nothing (an inbox, #general).

A grouping is scored by pairs, which does not care what the groups are called:

    a pair is "together" when the two things share at least one project.
    precision  of the pairs the grouping put together, how many really are together
    recall     of the pairs that really are together, how many the grouping found

Both are scored at two levels. "project" compares only the top of each path ("180DC"), so
putting a pricing sheet and a client email in the same project is right there even though they
are in different sub-projects. "sub-project" compares the whole path.

    E1  A thing with no label is together with nothing. Grouping it with anything is a false merge,
        which is how hub items (an inbox that mentions every project) are caught.
    E2  A thing may be in more than one project. It is together with the members of each.
    E3  No pairs predicted means precision is undefined (None), never 0 or 1 by accident.
"""

import itertools
import json

import notes

LEVELS = ("project", "sub-project")


def _at(paths, level):
    """The labels of one thing at `level`, as a set of strings."""
    out = set()
    for p in paths or ():
        parts = [x.strip() for x in str(p).split("/") if x.strip()]
        if parts:
            out.add(parts[0] if level == "project" else "/".join(parts))
    return out


def load_day(path):
    """A labelled day: {"items": {id: item-with-"label"}}. ValueError says what is wrong."""
    with open(path) as fh:
        doc = json.load(fh)
    items = doc.get("items")
    if not isinstance(items, dict) or not items:
        raise ValueError("A labelled day needs an \"items\" object with at least one thing.")
    for iid, item in items.items():
        if not isinstance(item.get("label"), list):
            raise ValueError(f"“{iid}” has no \"label\" list. Use [] for a thing that belongs to no project.")
    return items


def gold(items, level):
    """{id: set of true labels at `level`}."""
    if level not in LEVELS:
        raise ValueError(f"Level must be one of {', '.join(LEVELS)}.")
    return {iid: _at(item.get("label"), level) for iid, item in items.items()}


def pairs(memberships):
    """Every pair of ids (as a sorted tuple) that share at least one group (E1, E2)."""
    by_group = {}
    for iid, groups in memberships.items():
        for g in groups:
            by_group.setdefault(g, set()).add(iid)
    out = set()
    for ids in by_group.values():
        out.update(itertools.combinations(sorted(ids), 2))
    return out


def score(truth, predicted, level="project"):
    """Pairwise precision, recall and F1 of `predicted` against `truth`, both {id: groups}.
    Predicted groups may be paths too; they are cut to `level` the same way as the truth.
    Ids in one and not the other count as grouped with nothing."""
    pred = {iid: _at(groups, level) for iid, groups in predicted.items()}
    true_pairs, pred_pairs = pairs(truth), pairs(pred)
    hit = true_pairs & pred_pairs
    precision = len(hit) / len(pred_pairs) if pred_pairs else None          # E3
    recall = len(hit) / len(true_pairs) if true_pairs else None
    f1 = (2 * precision * recall / (precision + recall)
          if precision is not None and recall is not None and precision + recall else None)
    return {"level": level, "precision": precision, "recall": recall, "f1": f1,
            "true_pairs": len(true_pairs), "predicted_pairs": len(pred_pairs), "correct_pairs": len(hit),
            "false_merges": sorted(pred_pairs - true_pairs), "missed": sorted(true_pairs - pred_pairs)}


# ---------------------------------------------------------------- baselines to beat

def baseline_app_labels(items):
    """What LMemM does today when nothing is filed: notes.project_of() (app or title labels)."""
    return {iid: {notes.project_of(item)} for iid, item in items.items()}


def baseline_recent(items, k=4):
    """What `lmemm.py suggest` does today with no ids: the k most recently seen things as one group."""
    latest = sorted(items.values(), key=lambda i: i.get("last_seen") or "", reverse=True)[:k]
    chosen = {i["id"] for i in latest}
    return {iid: ({"recent"} if iid in chosen else set()) for iid in items}


BASELINES = {"today: app/title labels": baseline_app_labels, "today: last 4 things": baseline_recent}


# ---------------------------------------------------------------- report

def _pct(x):
    return "  n/a" if x is None else f"{x * 100:4.0f}%"


def report(items, groupings, mistakes=5):
    """Plain text: one line per grouping and level, then the first few mistakes of each."""
    titles = {iid: item.get("title") or iid for iid, item in items.items()}
    lines = [f"{len(items)} things, {sum(1 for i in items.values() if i['label'])} labelled",
             "", f"{'grouping':32} {'level':12} {'precision':>9} {'recall':>7} {'F1':>6}  pairs right/predicted/true"]
    details = []
    for name, predicted in groupings.items():
        for level in LEVELS:
            s = score(gold(items, level), predicted, level)
            lines.append(f"{name[:32]:32} {level:12} {_pct(s['precision']):>9} {_pct(s['recall']):>7} {_pct(s['f1']):>6}"
                         f"  {s['correct_pairs']}/{s['predicted_pairs']}/{s['true_pairs']}")
            if mistakes and level == "project":
                for kind, rows in (("put together wrongly", s["false_merges"]), ("missed", s["missed"])):
                    for a, b in rows[:mistakes]:
                        details.append(f"  {name}: {kind}: {titles[a]}  +  {titles[b]}")
                    if len(rows) > mistakes:
                        details.append(f"  {name}: {kind}: … and {len(rows) - mistakes} more")
    if details:
        lines += ["", "Mistakes at project level:"] + details
    return "\n".join(lines)
