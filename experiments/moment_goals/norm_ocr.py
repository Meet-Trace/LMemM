import re
from statistics import median

import norm_app
from context import is_technical, usable

CELL_CAP = 40
BLOCK_CAP = 220
MAX_TABLE_ROWS = 6
NUMERIC = re.compile(r"^[\s$€£%+\-.,:/\d]+$")


def clean(text):
    return re.sub(r"\s+", " ", str(text)).strip()


def prepare(ocr_lines, image_size, browser):
    seen, items, dropped = set(), [], 0
    for line in ocr_lines:
        text, box = clean(line.get("text", "")), line.get("box")
        if not text or not box or len(box) < 4:
            continue
        if norm_app.is_chrome_text(text, box, image_size, browser):
            dropped += 1
            continue
        if not usable(text):
            continue
        key = (text.lower(), round(box[0] / 6), round(box[1] / 6))
        if key in seen:
            continue
        seen.add(key)
        items.append({"text": text, "x": box[0], "y": box[1], "w": box[2], "h": box[3]})
    return items, dropped


def rows_of(items):
    if not items:
        return []
    h = median(i["h"] for i in items) or 10
    ordered = sorted(items, key=lambda i: (i["y"] + i["h"] / 2, i["x"]))
    rows, current, anchor = [], [], None
    for item in ordered:
        centre = item["y"] + item["h"] / 2
        if anchor is None or abs(centre - anchor) <= 0.6 * h:
            current.append(item)
            anchor = centre if anchor is None else (anchor + centre) / 2
        else:
            rows.append(sorted(current, key=lambda i: i["x"]))
            current, anchor = [item], centre
    if current:
        rows.append(sorted(current, key=lambda i: i["x"]))
    return rows


def is_labelish(text):
    return not NUMERIC.match(text) and len(text) <= 32


def column_of(cell, header, tolerance):
    best, gap = None, None
    for index, head in enumerate(header):
        d = abs(cell["x"] - head["x"])
        if d <= max(tolerance, head["w"] * 0.8) and (gap is None or d < gap):
            best, gap = index, d
    return best


def find_tables(rows):
    tables, used = [], set()
    i = 0
    while i < len(rows):
        row = rows[i]
        if len(row) >= 3:
            header_ok = sum(is_labelish(c["text"]) for c in row) / len(row) >= 0.8
            tolerance = 36
            matched = []
            j = i + 1
            while j < len(rows):
                cells = rows[j]
                if len(cells) < 2:
                    break
                columns = [column_of(c, row, tolerance) for c in cells]
                mapped = [c for c in columns if c is not None]
                if len(mapped) < max(2, 0.6 * len(cells)) or len(set(mapped)) != len(mapped):
                    break
                matched.append((cells, columns))
                j += 1
            if len(matched) >= 2:
                table_rows = []
                for cells, columns in matched:
                    record = [""] * len(row)
                    for c, col in zip(cells, columns):
                        if col is not None:
                            record[col] = c["text"][:CELL_CAP]
                    table_rows.append(record)
                header = [c["text"][:CELL_CAP] for c in row] if header_ok else None
                if not header_ok:
                    table_rows.insert(0, [c["text"][:CELL_CAP] for c in row])
                tables.append({"header": header, "rows": table_rows, "total_rows": len(table_rows), "y": row[0]["y"]})
                used.update(range(i, j))
                i = j
                continue
        i += 1
    return tables, used


def structure_of(ocr, image_size, browser):
    items, dropped = prepare(ocr or [], image_size, browser)
    rows = rows_of(items)
    tables, used = find_tables(rows)
    heights = [r[0]["h"] for r in rows if r] or [10]
    base = median(heights)
    headings, blocks, labels, technical = [], [], [], []
    paragraph, last_bottom, last_h = [], None, base
    said = set()

    def flush():
        if paragraph:
            blocks.append(clean(" ".join(paragraph))[:BLOCK_CAP])
            paragraph.clear()

    for index, row in enumerate(rows):
        if index in used:
            flush()
            continue
        text = clean(" ".join(c["text"] for c in row))
        if text.lower() in said:
            continue
        said.add(text.lower())
        top, h = row[0]["y"], max(c["h"] for c in row)
        if is_technical(text):
            flush()
            technical.append(text[:BLOCK_CAP])
            continue
        if len(row) == 1 and h >= 1.3 * base and len(text) <= 90:
            flush()
            headings.append(text)
            last_bottom = None
            continue
        if len(row) >= 2 and all(len(c["text"]) <= 24 for c in row) and not paragraph:
            labels.append(text[:BLOCK_CAP])
            continue
        if last_bottom is not None and top - last_bottom > 1.8 * last_h:
            flush()
        paragraph.append(text)
        last_bottom, last_h = top + h, h
    flush()
    return {"headings": headings[:8], "tables": tables[:3], "blocks": [b for b in blocks if len(b) >= 12][:8],
            "labels": labels[:8], "technical": technical[:6], "dropped_chrome": dropped,
            "top_text": rows[0][0]["text"] if rows and rows[0] else None}
