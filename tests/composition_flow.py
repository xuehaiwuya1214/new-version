"""Replay a real reference and a wider shot in the desktop UI using the PC models."""
import argparse
import io
import logging
import os
import re
import sys
from pathlib import Path
from threading import Thread

from PIL import Image
from playwright.sync_api import expect, sync_playwright
from werkzeug.serving import make_server

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app import app


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("photo", type=Path)
    args = parser.parse_args()
    with Image.open(args.photo) as raw:
        image = raw.convert("RGB")
    wider = Image.new("RGB", image.size)
    inset = image.resize(tuple(round(v * .82) for v in image.size))
    wider.paste(inset, tuple((a-b)//2 for a, b in zip(image.size, inset.size)))
    data = io.BytesIO()
    wider.save(data, "JPEG", quality=95)
    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    server = make_server("127.0.0.1", 0, app, threaded=True)
    Thread(target=server.serve_forever, daemon=True).start()
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(executable_path=os.getenv("CHROMIUM_PATH"))
            page = browser.new_page()
            errors = []
            page.on("pageerror", lambda exc: errors.append(str(exc)))
            page.goto(f"http://127.0.0.1:{server.server_port}/")
            page.locator("#ref-file").set_input_files(args.photo)
            expect(page.locator("#mark-far")).to_be_visible(timeout=30000)
            page.locator("#mark-far").click()
            ref = page.locator("#ref-overlay").bounding_box()
            with page.expect_response(lambda res: res.url.endswith("/api/session") and res.status == 201):
                page.locator("#ref-overlay").click(position={"x": ref["width"]*.5, "y": ref["height"]*.35})
            expect(page.locator("body")).to_have_attribute("data-reference", "loaded")
            page.evaluate("""() => {
              window.stageTrace = [];
              const record = () => {
                const step = document.querySelector('#legend').dataset.stage;
                if (step === 'position') {
                  const canvas = document.querySelector('#overlay');
                  const ctx = canvas.getContext('2d');
                  const pixels = ctx.getImageData(0, 0, canvas.width, canvas.height).data;
                  let reference = 0, current = 0;
                  for (let i = 0; i < pixels.length; i += 4) {
                    if (pixels[i+3] < 200) continue;
                    if (pixels[i] === 227 && pixels[i+1] === 199 && pixels[i+2] === 158) reference++;
                    if (pixels[i] === 83 && pixels[i+1] === 242 && pixels[i+2] === 200) current++;
                  }
                  if (reference > 20 && current > 20) window.sawBothBoxes = true;
                }
                if (step && window.stageTrace.at(-1) !== step)
                  window.stageTrace.push(step);
              };
              new MutationObserver(record).observe(document.querySelector('#legend'),
                {attributes:true, attributeFilter:['data-stage']});
            }""")
            page.locator("#live-file").set_input_files(
                {"name":"wider.jpg", "mimeType":"image/jpeg", "buffer":data.getvalue()})
            expect(page.locator("#shutter")).to_be_enabled(timeout=60000)
            trace = page.evaluate("window.stageTrace")
            assert trace == ["background","distance","framing","recenter","position","pose","ready"], trace
            assert page.evaluate("window.sawBothBoxes === true"), "Both subject overlays should be visible"
            zoom = float(page.locator("#zoom").input_value())
            assert 1.14 < zoom < 1.30, zoom
            page.locator("#shutter").click()
            expect(page.locator("#download-shot")).to_have_attribute("href", re.compile("^blob:"))
            assert not errors, errors
            print(f"{trace}, zoom={zoom}, JPEG exported")
            browser.close()
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
