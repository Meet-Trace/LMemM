import io
import math

import numpy as np
from PIL import Image

ALGORITHM = "gsa-structure/1"
SCHEMA = 1
WORK_MAX = 720
EDGE_T = 24
LINE_T = 8
UNIFORM_STD = 10.0
LINE_MIN_PX = 20
LONG_LINE_FRAC = 0.25
GAP_MIN = 4
MIN_REGION = 12
MAX_DEPTH = 6
CELL = 4
CELL_MIN_EDGES = 2
MIN_ELEMENT_EDGES = 4
FG_DIST = 48
MAX_REGIONS = 300
MAX_ELEMENTS = 400
MAX_LINES = 300
MAX_TEXT = 2000
NEIGHBOURS = 3
NEIGHBOUR_FRAC = 0.05
VISIBLE_ALPHA = 250

_LUMA = np.array([0.299, 0.587, 0.114], np.float32)


class StructureError(ValueError):
    def __init__(self, code, message=""):
        super().__init__(message or code)
        self.code = code


def _chmax(a):
    return np.maximum(np.maximum(a[..., 0], a[..., 1]), a[..., 2])


def working_scale(width, height, work_max=WORK_MAX):
    return max(1, math.ceil(max(width, height) / work_max))


def _r(value, digits=3):
    return round(float(value), digits)


def _hex(rgb):
    if rgb is None:
        return None
    r, g, b = (int(max(0, min(255, round(float(c))))) for c in rgb)
    return f"#{r:02x}{g:02x}{b:02x}"


class _Frame:
    def __init__(self, image):
        self.width, self.height = image.size
        self.scale = working_scale(self.width, self.height)
        base = image if image.mode in ("RGB", "RGBA") else image.convert("RGBA")
        work = base.reduce(self.scale) if self.scale > 1 else base
        if work.mode != "RGBA":
            work = work.convert("RGBA")
        a = np.asarray(work, dtype=np.uint8)
        self.h, self.w = a.shape[:2]
        self.rgb = a[..., :3].astype(np.int16)
        self.visible = a[..., 3] >= VISIBLE_ALPHA
        self.luma = self.rgb.astype(np.float32) @ _LUMA
        dx = _chmax(np.abs(np.diff(self.rgb, axis=1)))
        dy = _chmax(np.abs(np.diff(self.rgb, axis=0)))
        dx[~(self.visible[:, 1:] & self.visible[:, :-1])] = 0
        dy[~(self.visible[1:, :] & self.visible[:-1, :])] = 0
        self.hb = dy > EDGE_T
        self.vb = dx > EDGE_T
        self.hl = dy > LINE_T
        self.vl = dx > LINE_T
        self.edges = np.zeros((self.h, self.w), bool)
        self.edges[1:, :] |= self.hb
        self.edges[:, 1:] |= self.vb
        q = (self.rgb >> 3).astype(np.int32)
        self.qkey = (q[..., 0] << 10) | (q[..., 1] << 5) | q[..., 2]

    def box_out(self, x0, y0, x1, y1):
        s = self.scale
        x0, y0 = x0 * s, y0 * s
        x1, y1 = min(self.width, x1 * s), min(self.height, y1 * s)
        return [int(x0), int(y0), int(max(1, x1 - x0)), int(max(1, y1 - y0))]

    def box_in(self, x, y, w, h):
        s = self.scale
        x0 = max(0, min(self.w, int(math.floor(x / s))))
        y0 = max(0, min(self.h, int(math.floor(y / s))))
        x1 = max(0, min(self.w, int(math.ceil((x + w) / s))))
        y1 = max(0, min(self.h, int(math.ceil((y + h) / s))))
        return x0, y0, x1, y1

    def background(self, x0, y0, x1, y1):
        keys = self.qkey[y0:y1, x0:x1]
        vis = self.visible[y0:y1, x0:x1]
        if not vis.any():
            return None
        mode = int(np.bincount(keys[vis], minlength=1).argmax())
        return np.array([((mode >> 10) & 31) * 8 + 4, ((mode >> 5) & 31) * 8 + 4, (mode & 31) * 8 + 4], np.float32)

    def colour_stats(self, x0, y0, x1, y1, bg=None):
        vis = self.visible[y0:y1, x0:x1]
        n = int(vis.sum())
        if n == 0:
            return {"visible_fraction": 0.0}
        pix = self.rgb[y0:y1, x0:x1][vis].astype(np.float32)
        if bg is None:
            bg = self.background(x0, y0, x1, y1)
        dist = _chmax(np.abs(pix - bg))
        fg_mask = dist > FG_DIST
        fg_n = int(fg_mask.sum())
        lum = self.luma[y0:y1, x0:x1][vis]
        area = max(1, (y1 - y0) * (x1 - x0))
        hb = self.hb[max(0, y0 - 1):max(0, y1 - 1), x0:x1].sum() if y1 - y0 > 1 else 0
        vb = self.vb[y0:y1, max(0, x0 - 1):max(0, x1 - 1)].sum() if x1 - x0 > 1 else 0
        edges = int(self.edges[y0:y1, x0:x1].sum())
        return {
            "visible_fraction": _r(n / area),
            "background": _hex(bg),
            "background_fraction": _r(float((dist <= 8).mean())),
            "foreground": _hex(pix[fg_mask].mean(axis=0)) if fg_n else None,
            "foreground_fraction": _r(fg_n / n),
            "mean_colour": _hex(pix.mean(axis=0)),
            "luminance_std": _r(float(lum.std()), 2),
            "edge_density": _r(edges / area),
            "edge_orientation": _r(float(hb) / float(hb + vb)) if hb + vb else None,
        }


def _runs(mask, min_len):
    padded = np.zeros((mask.shape[0], mask.shape[1] + 2), np.int8)
    padded[:, 1:-1] = mask
    d = np.diff(padded, axis=1)
    rows, starts = np.nonzero(d == 1)
    _, ends = np.nonzero(d == -1)
    keep = (ends - starts) >= min_len
    return rows[keep], starts[keep], ends[keep]


def _uniform(luma, rows, starts, ends):
    if len(rows) == 0:
        return np.zeros(0, bool)
    c1 = np.zeros((luma.shape[0], luma.shape[1] + 1), np.float64)
    c2 = np.zeros_like(c1)
    c1[:, 1:] = np.cumsum(luma, axis=1)
    c2[:, 1:] = np.cumsum(luma.astype(np.float64) ** 2, axis=1)
    n = ends - starts
    s1 = c1[rows, ends] - c1[rows, starts]
    s2 = c2[rows, ends] - c2[rows, starts]
    var = np.maximum(s2 / n - (s1 / n) ** 2, 0)
    return np.sqrt(var) <= UNIFORM_STD


def _line_candidates(frame, orientation):
    if orientation == "h":
        mask, luma = frame.hl, frame.luma
    else:
        mask, luma = frame.vl.T, frame.luma.T
    rows, starts, ends = _runs(mask, LINE_MIN_PX)
    ok = _uniform(luma, rows, starts, ends) & _uniform(luma, rows + 1, starts, ends)
    return sorted(zip(rows[ok].tolist(), starts[ok].tolist(), ends[ok].tolist()))


def _merge_lines(cands):
    groups = []
    for row, a, b in cands:
        for g in groups:
            if row - g["r1"] <= 2 and min(b, g["b"]) - max(a, g["a"]) >= 0.5 * min(b - a, g["b"] - g["a"]):
                g["r1"] = row
                g["a"], g["b"] = min(a, g["a"]), max(b, g["b"])
                break
        else:
            groups.append({"r0": row, "r1": row, "a": a, "b": b})
    return groups


def detect_lines(frame):
    out = []
    for orientation in ("h", "v"):
        for g in _merge_lines(_line_candidates(frame, orientation)):
            r0, r1, a, b = g["r0"], g["r1"] + 2, g["a"], g["b"]
            if orientation == "h":
                x0, y0, x1, y1 = a, r0, b, min(frame.h, r1)
                before = frame.rgb[r0, a:b].mean(axis=0)
                after = frame.rgb[min(frame.h - 1, r1 - 1), a:b].mean(axis=0)
                span = frame.w
            else:
                x0, y0, x1, y1 = r0, a, min(frame.w, r1), b
                before = frame.rgb[a:b, r0].mean(axis=0)
                after = frame.rgb[a:b, min(frame.w - 1, r1 - 1)].mean(axis=0)
                span = frame.h
            out.append({"orientation": orientation, "work": (x0, y0, x1, y1),
                        "length_fraction": (b - a) / span, "before": before, "after": after})
    out.sort(key=lambda l: (-(l["work"][2] - l["work"][0]) * (l["work"][3] - l["work"][1]), l["work"]))
    truncated = len(out) > MAX_LINES
    return out[:MAX_LINES], truncated


def _cuts(profile, boundary, length, lo, hi):
    n = hi - lo
    cand = []
    empty = profile == 0
    i = 0
    while i < n:
        if empty[i]:
            j = i
            while j < n and empty[j]:
                j += 1
            if j - i >= GAP_MIN and i > 0 and j < n:
                cand.append(lo + (i + j) // 2)
            i = j
        else:
            i += 1
    strong = np.nonzero(boundary >= 0.9 * length)[0]
    cand.extend((lo + int(k) + 1) for k in strong)
    cuts, last = [], lo
    for c in sorted(set(cand)):
        if c - last >= MIN_REGION and hi - c >= MIN_REGION:
            cuts.append(c)
            last = c
    return cuts


def _split(frame, x0, y0, x1, y1, axis):
    e = frame.edges[y0:y1, x0:x1]
    if axis == 0:
        profile = e.sum(axis=1)
        boundary = frame.hl[y0:max(y0, y1 - 1), x0:x1].sum(axis=1) if y1 - y0 > 1 else np.zeros(0)
        cuts = _cuts(profile, boundary, x1 - x0, y0, y1)
        edges = [y0, *cuts, y1]
        return [(x0, a, x1, b) for a, b in zip(edges, edges[1:])]
    profile = e.sum(axis=0)
    boundary = frame.vl[y0:y1, x0:max(x0, x1 - 1)].sum(axis=0) if x1 - x0 > 1 else np.zeros(0)
    cuts = _cuts(profile, boundary, y1 - y0, x0, x1)
    edges = [x0, *cuts, x1]
    return [(a, y0, b, y1) for a, b in zip(edges, edges[1:])]


def _visible_bbox(frame):
    ys, xs = np.nonzero(frame.visible)
    if len(ys) == 0:
        return None
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def detect_regions(frame, root):
    nodes = [{"work": root, "parent": None, "depth": 0}]
    queue = [0]
    truncated = False
    while queue:
        idx = queue.pop(0)
        node = nodes[idx]
        x0, y0, x1, y1 = node["work"]
        if node["depth"] >= MAX_DEPTH or (x1 - x0 < 2 * MIN_REGION and y1 - y0 < 2 * MIN_REGION):
            continue
        first = 0 if node["depth"] % 2 == 0 else 1
        parts = _split(frame, x0, y0, x1, y1, first)
        if len(parts) == 1:
            parts = _split(frame, x0, y0, x1, y1, 1 - first)
        if len(parts) == 1:
            continue
        if len(nodes) + len(parts) > MAX_REGIONS:
            truncated = True
            continue
        for p in parts:
            nodes.append({"work": p, "parent": idx, "depth": node["depth"] + 1})
            queue.append(len(nodes) - 1)
    children = {}
    for i, n in enumerate(nodes):
        if n["parent"] is not None:
            children.setdefault(n["parent"], []).append(i)
    for i, n in enumerate(nodes):
        n["leaf"] = i not in children
    return nodes, truncated


def _label_cells(active):
    h, w = active.shape
    labels = np.where(active, np.arange(1, h * w + 1, dtype=np.int32).reshape(h, w), 0)
    for _ in range(h + w + 2):
        p = np.pad(labels, 1)
        m = labels.copy()
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                if dy or dx:
                    np.maximum(m, p[1 + dy:1 + dy + h, 1 + dx:1 + dx + w], out=m)
        m[~active] = 0
        if np.array_equal(m, labels):
            break
        labels = m
    return labels


def detect_elements(frame, text_boxes, long_lines):
    mask = frame.edges.copy()
    for x0, y0, x1, y1 in text_boxes:
        mask[max(0, y0 - 1):y1 + 1, max(0, x0 - 1):x1 + 1] = False
    for x0, y0, x1, y1 in long_lines:
        mask[max(0, y0 - 1):y1 + 1, max(0, x0 - 1):x1 + 1] = False
    gh, gw = math.ceil(frame.h / CELL), math.ceil(frame.w / CELL)
    padded = np.zeros((gh * CELL, gw * CELL), np.int32)
    padded[:frame.h, :frame.w] = mask
    counts = padded.reshape(gh, CELL, gw, CELL).sum(axis=(1, 3))
    labels = _label_cells(counts >= CELL_MIN_EDGES)
    ys, xs = np.nonzero(mask)
    lab = labels[ys // CELL, xs // CELL]
    keep = lab > 0
    ys, xs, lab = ys[keep], xs[keep], lab[keep]
    if len(lab) == 0:
        return [], False
    order = np.argsort(lab, kind="stable")
    ys, xs, lab = ys[order], xs[order], lab[order]
    starts = np.concatenate(([0], np.nonzero(np.diff(lab))[0] + 1))
    n = np.diff(np.concatenate((starts, [len(lab)])))
    bx0 = np.minimum.reduceat(xs, starts)
    by0 = np.minimum.reduceat(ys, starts)
    bx1 = np.maximum.reduceat(xs, starts) + 1
    by1 = np.maximum.reduceat(ys, starts) + 1
    out = []
    for i in range(len(starts)):
        w, h = int(bx1[i] - bx0[i]), int(by1[i] - by0[i])
        if n[i] < MIN_ELEMENT_EDGES or w < 2 or h < 2:
            continue
        out.append({"work": (int(bx0[i]), int(by0[i]), int(bx1[i]), int(by1[i])), "edge_pixels": int(n[i])})
    out.sort(key=lambda e: (-(e["work"][2] - e["work"][0]) * (e["work"][3] - e["work"][1]), e["work"]))
    truncated = len(out) > MAX_ELEMENTS
    return out[:MAX_ELEMENTS], truncated


def _element_features(frame, el, region_bg):
    x0, y0, x1, y1 = el["work"]
    stats = frame.colour_stats(x0, y0, x1, y1, region_bg)
    w, h = x1 - x0, y1 - y0
    e = frame.edges[y0:y1, x0:x1]
    border = np.concatenate((e[0, :], e[-1, :], e[:, 0], e[:, -1]))
    keys = frame.qkey[y0:y1, x0:x1][frame.visible[y0:y1, x0:x1]] >> 3
    colours = int(len(np.unique(keys))) if len(keys) else 0
    return {
        **stats,
        "aspect_ratio": _r(w / h),
        "border_edge_fraction": _r(float(border.mean())) if border.size else 0.0,
        "colour_count": colours,
        "edge_pixels": el["edge_pixels"],
    }


def _text_items(frame, ocr_raw):
    if not ocr_raw:
        return [], [], False
    lines = ocr_raw.get("lines") or []
    size = ocr_raw.get("image_size") or [frame.width, frame.height]
    try:
        sx, sy = frame.width / float(size[0]), frame.height / float(size[1])
    except (TypeError, ValueError, ZeroDivisionError, IndexError):
        sx = sy = 1.0
    items, work = [], []
    for i, line in enumerate(lines):
        if len(items) >= MAX_TEXT:
            return items, work, True
        box = line.get("box") if isinstance(line, dict) else None
        if not box or len(box) != 4:
            continue
        try:
            x, y, w, h = (float(v) for v in box)
        except (TypeError, ValueError):
            continue
        if not all(math.isfinite(v) for v in (x, y, w, h)) or w <= 0 or h <= 0:
            continue
        x, y, w, h = x * sx, y * sy, w * sx, h * sy
        ox0, oy0 = max(0, math.floor(x)), max(0, math.floor(y))
        ox1, oy1 = min(frame.width, math.ceil(x + w)), min(frame.height, math.ceil(y + h))
        if ox1 <= ox0 or oy1 <= oy0:
            continue
        wb = frame.box_in(ox0, oy0, ox1 - ox0, oy1 - oy0)
        item = {"order": len(items), "source_line": i, "text": line.get("text"),
                "confidence": None if line.get("confidence") is None else _r(line["confidence"], 4),
                "bbox": [ox0, oy0, ox1 - ox0, oy1 - oy0]}
        if (sx, sy) != (1.0, 1.0):
            item["source_box"] = [_r(v, 2) for v in box]
        items.append(item)
        work.append(wb)
    return items, work, False


def _text_colours(frame, box):
    x0, y0, x1, y1 = box
    x0p, y0p = max(0, x0 - 1), max(0, y0 - 1)
    x1p, y1p = min(frame.w, x1 + 1), min(frame.h, y1 + 1)
    if x1p <= x0p or y1p <= y0p:
        return None, None
    patch = frame.rgb[y0p:y1p, x0p:x1p].reshape(-1, 3).astype(np.float32)
    vis = frame.visible[y0p:y1p, x0p:x1p].reshape(-1)
    if not vis.any():
        return None, None
    ring = np.ones((y1p - y0p, x1p - x0p), bool)
    if ring.shape[0] > 2 and ring.shape[1] > 2:
        ring[1:-1, 1:-1] = False
    ring = ring.reshape(-1) & vis
    bg = np.median(patch[ring if ring.any() else vis], axis=0)
    dist = _chmax(np.abs(patch[vis] - bg))
    top = dist.max()
    fg = patch[vis][dist >= 0.6 * top].mean(axis=0) if top > 16 else None
    return _hex(bg), _hex(fg)


def _gap(boxes):
    b = np.asarray(boxes, np.float32)
    x0, y0, x1, y1 = b[:, 0], b[:, 1], b[:, 0] + b[:, 2], b[:, 1] + b[:, 3]
    dx = np.maximum(0, np.maximum(x0[:, None] - x1[None, :], x0[None, :] - x1[:, None]))
    dy = np.maximum(0, np.maximum(y0[:, None] - y1[None, :], y0[None, :] - y1[:, None]))
    return np.hypot(dx, dy)


def _neighbours(items, limit):
    if len(items) < 2:
        return
    ids = [it["id"] for it in items]
    d = _gap([it["bbox"] for it in items])
    np.fill_diagonal(d, np.inf)
    k = min(NEIGHBOURS, len(items) - 1)
    near = np.argsort(d, axis=1, kind="stable")[:, :k]
    for i, it in enumerate(items):
        it["neighbours"] = [[ids[j], int(round(float(d[i, j])))] for j in near[i] if d[i, j] <= limit]


def _centres(items):
    if not items:
        return np.zeros((0, 2), np.float64)
    b = np.asarray([it["bbox"] for it in items], np.float64)
    return np.stack((b[:, 0] + b[:, 2] / 2.0, b[:, 1] + b[:, 3] / 2.0), axis=1)


def _inside(boxes, points):
    if len(boxes) == 0 or len(points) == 0:
        return np.zeros((len(points), len(boxes)), bool)
    b = np.asarray(boxes, np.float64)
    px, py = points[:, 0:1], points[:, 1:2]
    return (px >= b[:, 0]) & (px < b[:, 0] + b[:, 2]) & (py >= b[:, 1]) & (py < b[:, 1] + b[:, 3])


def _assign_regions(regions, items):
    leaves = [r for r in regions if r["leaf"]]
    if not items:
        return
    hit = _inside([r["bbox"] for r in leaves], _centres(items))
    area = np.asarray([r["bbox"][2] * r["bbox"][3] for r in leaves], np.float64)
    for i, it in enumerate(items):
        row = np.nonzero(hit[i])[0]
        it["region"] = leaves[int(row[np.argmin(area[row])])]["id"] if len(row) else None


def decode(data):
    try:
        image = Image.open(io.BytesIO(bytes(data)))
        image.load()
    except Exception as error:
        raise StructureError("ScreenshotCorrupt", f"{type(error).__name__}: {error}") from None
    return image


def analyze(data, ocr=None, source=None):
    image = decode(data)
    try:
        return analyze_image(image, ocr, source)
    finally:
        image.close()


def analyze_image(image, ocr=None, source=None):
    frame = _Frame(image)
    ocr = ocr or {}
    ocr_raw = ocr.get("raw") if ocr.get("status") == "succeeded" else None
    text, text_work, text_truncated = _text_items(frame, ocr_raw)
    for i, t in enumerate(text):
        t["id"] = f"t{i}"
        t["background"], t["foreground"] = _text_colours(frame, text_work[i])

    vbox = _visible_bbox(frame)
    regions, lines, elements = [], [], []
    truncated = {"text": text_truncated, "regions": False, "elements": False, "lines": False}
    if vbox is not None:
        raw_lines, truncated["lines"] = detect_lines(frame)
        for i, l in enumerate(raw_lines):
            x0, y0, x1, y1 = l["work"]
            lines.append({"id": f"l{i}", "orientation": l["orientation"], "bbox": frame.box_out(x0, y0, x1, y1),
                          "length_fraction": _r(l["length_fraction"]),
                          "colour_before": _hex(l["before"]), "colour_after": _hex(l["after"]),
                          "contrast": _r(float(np.abs(l["before"] - l["after"]).max()), 1)})
        nodes, truncated["regions"] = detect_regions(frame, vbox)
        bgs = []
        for i, n in enumerate(nodes):
            x0, y0, x1, y1 = n["work"]
            bg = frame.background(x0, y0, x1, y1)
            bgs.append(bg)
            features = frame.colour_stats(x0, y0, x1, y1, bg) if n["leaf"] else {"background": _hex(bg)}
            regions.append({"id": f"r{i}", "parent": None if n["parent"] is None else f"r{n['parent']}",
                            "depth": n["depth"], "leaf": n["leaf"], "bbox": frame.box_out(x0, y0, x1, y1),
                            **features})
        long_lines = [l["work"] for l in raw_lines if l["length_fraction"] >= LONG_LINE_FRAC]
        raw_elements, truncated["elements"] = detect_elements(frame, text_work, long_lines)
        leaf_nodes = [(i, n) for i, n in enumerate(nodes) if n["leaf"]]
        for i, el in enumerate(raw_elements):
            x0, y0, x1, y1 = el["work"]
            cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
            host = None
            for j, n in leaf_nodes:
                a0, b0, a1, b1 = n["work"]
                if a0 <= cx < a1 and b0 <= cy < b1:
                    host = j
                    break
            region_bg = bgs[host] if host is not None else None
            elements.append({"id": f"e{i}", "bbox": frame.box_out(x0, y0, x1, y1),
                             **_element_features(frame, el, region_bg)})

    _assign_regions(regions, text + elements + lines)
    inside = _inside([e["bbox"] for e in elements], _centres(text)).sum(axis=0)
    for i, el in enumerate(elements):
        el["text_inside"] = int(inside[i]) if len(text) else 0
    text_counts, element_counts = {}, {}
    for t in text:
        text_counts[t["region"]] = text_counts.get(t["region"], 0) + 1
    for e in elements:
        element_counts[e["region"]] = element_counts.get(e["region"], 0) + 1
    for r in regions:
        if r["leaf"]:
            r["text_count"] = text_counts.get(r["id"], 0)
            r["element_count"] = element_counts.get(r["id"], 0)
    _neighbours(text + elements, NEIGHBOUR_FRAC * math.hypot(frame.width, frame.height))

    src = dict(source or {})
    src["image_size"] = [frame.width, frame.height]
    src["ocr"] = ({k: ocr.get(k) for k in ("extraction_id", "revision", "status", "reader_version")}
                  if ocr else None)
    return {
        "schema": SCHEMA,
        "algorithm": ALGORITHM,
        "source": src,
        "coordinates": {"space": "screenshot_pixels", "origin": "top_left", "bbox": "x,y,w,h",
                        "working_size": [frame.w, frame.h], "working_scale": frame.scale},
        "visible_bbox": frame.box_out(*vbox) if vbox else None,
        "visible_fraction": _r(float(frame.visible.mean())),
        "text": text,
        "regions": regions,
        "elements": elements,
        "lines": lines,
        "counts": {"text": len(text), "regions": len(regions), "elements": len(elements), "lines": len(lines)},
        "truncated": truncated,
    }
