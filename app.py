"""本机校园人像导拍服务。python app.py 启动。"""
import hashlib
import io
import json
import os
import time
from functools import lru_cache
from pathlib import Path
from threading import Lock
from uuid import uuid4

import cv2
import numpy as np
from flask import Flask, jsonify, request
from PIL import Image, ImageOps, UnidentifiedImageError

from guide import Guide
from people import Models
from library import api as library_api
from phone_frame import crop_phone_reference, reference_preview


ROOT = Path(__file__).resolve().parent
app = Flask(__name__, static_folder="static")
app.config["MAX_CONTENT_LENGTH"] = 24 * 1024 * 1024
app.register_blueprint(library_api)
cv2.setNumThreads(2)
sessions = {}
storage_lock = Lock()
model_lock = Lock()
models = None


def get_models():
    global models
    with model_lock:
        if models is None:
            models = Models()
        return models


def decode(data):
    try:
        with Image.open(io.BytesIO(data)) as im:
            if min(im.size) < 32 or im.width * im.height > 45_000_000:
                raise ValueError("请提供尺寸合适的人像照片。")
            image = ImageOps.exif_transpose(im).convert("RGB")
            image.thumbnail((960, 960), Image.Resampling.LANCZOS)
            return cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2BGR)
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ValueError("无法读取图片，请使用 JPG、PNG 或 WebP。") from exc


@lru_cache(maxsize=1)
def campus_notes():
    # 这里只按已选图片的内容指纹读取现有机位标注。
    configured = os.environ.get("CAMPUS_REFERENCE_INDEX")
    if not configured:
        return json.loads((ROOT / "campus-viewpoints.json").read_text(encoding="utf-8"))
    path = Path(configured)
    if not path.is_file():
        return {}
    rows = json.loads(path.read_text(encoding="utf-8-sig"))
    return {r["sha256"]: " · ".join(x.replace("_", " / ") for x in
                                  (r.get("location"), r.get("viewpoint_group")) if x)
            for r in rows if r.get("sha256") and r.get("location") and "待确认" not in r["location"]}


def point(args, x="x", y="y"):
    if x not in args and y not in args:
        return None
    try:
        p = np.array([float(args[x]), float(args[y])])
    except (KeyError, ValueError, TypeError) as exc:
        raise ValueError("点选坐标无效。") from exc
    if not np.isfinite(p).all() or (p < 0).any() or (p > 1).any():
        raise ValueError("请在照片内点选主角。")
    return p


@app.errorhandler(ValueError)
def bad_input(error):
    return jsonify(error=str(error)), 400


@app.errorhandler(413)
def too_large(_):
    return jsonify(error="图片过大，请选择小于 24 MB 的图片。"), 413


@app.get("/")
def index():
    return app.send_static_file("index.html")


@app.get("/api/health")
def health():
    # Cheap identity check for the phone connection screen; no model load or image upload.
    return jsonify(status="ok", service="campus-photo-guide", version=1)


@app.post("/api/session")
def start():
    file = request.files.get("reference")
    if file is None:
        raise ValueError("请上传已选好的参考照。")
    data = file.read()
    frame = decode(data)
    mirror = request.form.get("mirror", "0")
    if mirror not in ("0", "1"):
        raise ValueError("参考照方向参数应为 0 或 1。")
    if mirror == "1":
        frame = cv2.flip(frame, 1)
    phone_aspect = request.form.get("aspect", type=float)
    if "aspect" in request.form and phone_aspect is None:
        raise ValueError("手机画幅无效")
    if phone_aspect is not None:
        frame = crop_phone_reference(frame, phone_aspect, get_models())
    note = request.form.get("viewpoint", "").strip()[:100] or campus_notes().get(hashlib.sha256(data).hexdigest(), "")
    guide = Guide(get_models(), frame, note, point(request.form),
                  composition=request.form.get("workflow") == "composition",
                  far_point=point(request.form, "farX", "farY"))
    if phone_aspect is not None:
        guide.reference["preview"] = reference_preview(frame)
    if request.form.get("client") == "android-preview":
        # Low-frequency phone sampling is intentional; a slow fresh pair can still confirm alignment.
        guide.max_sample_gap = 3.5
    sid = uuid4().hex
    now = time.monotonic()
    with storage_lock:
        for key in [key for key, (_, used) in sessions.items() if now - used > 900]:
            del sessions[key]
        if len(sessions) >= 12:
            return jsonify(error="取景会话已满，请关闭其他取景页面后重试。"), 429
        sessions[sid] = (guide, now)
    return jsonify(id=sid, reference=guide.reference), 201


@app.post("/api/session/<sid>/frame")
def analyze(sid):
    now = time.monotonic()
    with storage_lock:
        item = sessions.get(sid)
        if not item or now - item[1] > 900:
            sessions.pop(sid, None)
            return jsonify(error="参考会话已过期，请重新载入参考照。"), 404
        guide = item[0]
        sessions[sid] = (guide, now)
    seq = request.args.get("seq", type=int)
    if seq is None or seq < 0:
        raise ValueError("缺少有效帧号。")
    payload = request.get_data(cache=False)
    body_at = time.perf_counter()
    image = decode(payload)
    decoded_at = time.perf_counter()
    tap = point(request.args)
    skipped = request.args.get("skip")
    if skipped is not None:
        skipped = skipped.split(",") if skipped else []
    with guide.lock:
        acquired_at = time.perf_counter()
        result = guide.analyze(image, seq, time.monotonic(), tap, skipped, request.args.get("source", type=int))
    done_at = time.perf_counter()
    result["timing"] = {"decodeMs": round((decoded_at-body_at)*1000, 1),
                        "queueMs": round((acquired_at-decoded_at)*1000, 1),
                        "analyzeMs": round((done_at-acquired_at)*1000, 1),
                        "serverMs": round((done_at-body_at)*1000, 1)}
    return jsonify(result)


@app.delete("/api/session/<sid>")
def stop(sid):
    with storage_lock:
        sessions.pop(sid, None)
    return "", 204


if __name__ == "__main__":
    app.run(host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", "7861")), threaded=True)

