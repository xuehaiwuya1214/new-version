import json

import cv2
import numpy as np
from guide import Guide
from people import Models


models = Models()
sessions = {}


def frame(pixels, width, height):
    rgba = np.frombuffer(pixels.to_py(), np.uint8).reshape(height, width, 4)
    return cv2.cvtColor(rgba, cv2.COLOR_RGBA2BGR)


async def open_reference(sid, pixels, width, height, options_json):
    options = json.loads(options_json)
    image = frame(pixels, width, height)
    if options["mirror"]:
        image = cv2.flip(image, 1)
    tap = options["tap"]
    guide = await Guide.create(models, image, options["viewpoint"], tap, composition=True, far_point=options.get("farPoint"))
    sessions[sid] = guide
    return json.dumps({"id": sid, "reference": guide.reference}, ensure_ascii=False)


async def analyze(sid, pixels, width, height, options_json):
    opt = json.loads(options_json)
    result = await sessions[sid].analyze_async(frame(pixels, width, height), opt["seq"], opt["now"],
                                               opt["tap"], opt["skip"], opt["source"])
    return json.dumps(result, ensure_ascii=False)


def close(sid):
    sessions.pop(sid, None)
