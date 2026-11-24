import base64
import io
import json
import time
import urllib.request

from .parse import build_prompt, parse_output
from .read import packet

PROMPT_VERSION = "moment-vision/1"
NOTE = ("The attached screenshots show observations {tags} of the sequence below, in that order. "
        "Use them as visual evidence for those observations only.\n\n")
DESCRIBE = {"max_images": 4, "image_side": 896, "num_ctx": 6144, "num_predict": 450, "budget": 4500, "timeout": 900}


def choose_tags(tags, limit):
    order = sorted(tags, key=lambda t: int(t[1:]))
    if limit <= 0 or len(order) <= limit:
        return order
    if limit == 1:
        return [order[len(order) // 2]]
    picks = sorted({round(i * (len(order) - 1) / (limit - 1)) for i in range(limit)})
    return [order[i] for i in picks]


def pick_screenshot(store, conn, ids):
    middle = len(ids) // 2
    for i in sorted(range(len(ids)), key=lambda i: abs(i - middle)):
        row = conn.execute("SELECT status, relative_path FROM screenshots WHERE observation_id=?", (ids[i],)).fetchone()
        if row and row[0] == "stored" and row[1]:
            try:
                return ids[i], store.assets.read(row[1])
            except Exception:
                pass
        try:
            return ids[i], store.thumbnail_bytes(ids[i])
        except Exception:
            continue
    return None, None


def visible_part(image):
    """Crop a capture that is transparent outside the front window to the area that has pixels."""
    if image.mode not in ("RGBA", "LA", "PA") and "transparency" not in image.info:
        return image
    box = image.convert("RGBA").getchannel("A").point(lambda v: 255 if v > 8 else 0).getbbox()
    return image.crop(box) if box and box != (0, 0, *image.size) else image


def encode_image(data, side):
    from PIL import Image
    with Image.open(io.BytesIO(data)) as image:
        image = visible_part(image).convert("RGB")
        image.thumbnail((side, side))
        buffer = io.BytesIO()
        image.save(buffer, "JPEG", quality=80)
    return base64.b64encode(buffer.getvalue()).decode()


class OllamaVision:
    def __init__(self, model, host="http://127.0.0.1:11434", options=None):
        self.model, self.host = model, host
        self.options = {**DESCRIBE, **(options or {})}

    def chat(self, text, images):
        body = {"model": self.model, "stream": False, "think": False, "keep_alive": "2m",
                "messages": [{"role": "user", "content": text, "images": images}],
                "options": {"num_ctx": self.options["num_ctx"], "num_predict": self.options["num_predict"]}}
        request = urllib.request.Request(self.host + "/api/chat", json.dumps(body).encode(), {"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=self.options["timeout"]) as response:
            return (json.loads(response.read()).get("message") or {}).get("content") or ""


def describe(store, conn, client, moment_id, start_ns, end_ns, options=None):
    """-> a result dict. Raises when the model could not be reached, so the Moment is tried again."""
    opts = {**DESCRIBE, **(options or {})}
    built = packet(conn, moment_id, start_ns, end_ns, opts["budget"])
    if built is None:
        return {"status": "insufficient", "errors": ["too few observations to describe"], "images_used": 0, "seconds": None}
    tags = built["tags"]
    images, used = [], []
    for tag in choose_tags(tags, opts["max_images"]):
        oid, data = pick_screenshot(store, conn, tags[tag])
        if data is not None:
            images.append(encode_image(data, opts["image_side"]))
            used.append(tag)
    if not images:
        return {"status": "insufficient", "errors": ["no stored screenshots for this Moment"], "images_used": 0, "seconds": None}
    prompt = build_prompt(built["text"])
    started = time.perf_counter()
    content = client.chat(NOTE.format(tags=", ".join(used)) + prompt, images)
    seconds = round(time.perf_counter() - started, 2)
    parsed = parse_output(content, tags)
    if not content.strip():
        parsed["errors"].append("the model produced no answer")
    return {**parsed, "images_used": len(images), "seconds": seconds}
