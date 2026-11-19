"""启动临时本地服务，在 Chromium 中回放校园画面，走完整的相机与快门路径。"""
import logging
import io
import os
import re
import sys
from pathlib import Path
from threading import Thread

import numpy as np
from PIL import Image
from playwright.sync_api import expect, sync_playwright
from werkzeug.serving import make_server

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
from app import app


CAMERA = """
Object.defineProperty(navigator.mediaDevices, 'getUserMedia', {value: async () => {
  const image = new Image();
  image.src = '/test-camera.webp?source=' + performance.now();
  await image.decode();
  const canvas = document.createElement('canvas');
  canvas.width = 960; canvas.height = 720;
  const ctx = canvas.getContext('2d');
  function render() {
    ctx.fillStyle = '#25342d'; ctx.fillRect(0,0,960,720);
    const w = 720 * image.naturalWidth / image.naturalHeight;
    if (window.scene !== 'blank') {
      const dx = ({right:.17, rightWide:.17, nearRight:.07, poseRight:.043, poseLeft:-.043}[window.scene] || 0) * w;
      const scale = {small:.82, centeredWide:.70, rightWide:.70, veryWide:.50}[window.scene] || 1;
      ctx.drawImage(image, (960-w*scale)/2+dx, (720-720*scale)/2, w*scale, 720*scale);
    }
  }
  render();
  const timer = setInterval(render, 50);
  const stream = canvas.captureStream(20);
  const track = stream.getVideoTracks()[0];
  const original = track.stop.bind(track);
  track.stop = () => {clearInterval(timer); original();};
  window.lastCameraTrack = track;
  return stream;
}});
"""


def expect_camera_rings(page, live_x):
    # 看取景器实际绘制的像素，同时覆盖坐标、缩放和画幅留边。
    page.wait_for_function("""expected => {
      const canvas = document.getElementById('overlay');
      const {width:w, height:h} = canvas;
      if (!w || !h || document.getElementById('legend').dataset.stage !== 'background') return false;
      const pixels = canvas.getContext('2d').getImageData(0, 0, w, h).data;
      const dot = [], white = [];
      for (let y = 0; y < h; y += 2) for (let x = 0; x < w; x += 2) {
        const i = (y*w+x)*4;
        if (pixels[i+3] < 190) continue;
        if (pixels[i] > 235 && pixels[i+1] > 235 && pixels[i+2] > 220) {
          if (Math.hypot(x-w/2,y-h/2)<22) white.push([x/w, y/h]);
          else if (Math.hypot(x-w/2,y-h/2)>35) dot.push([x/w, y/h]);
        }
      }
      function at(points, x) {
        if (points.length < 8) return false;
        return [x, .5].every((v, axis) => {
          const coords = points.map(p => p[axis]);
          return Math.abs((Math.max(...coords)+Math.min(...coords))/2-v) < .012;
        });
      }
      return at(white, .5) && (expected === null ? !dot.length : at(dot, expected));
    }""", arg=live_x, timeout=15000)


def expect_building_outline(page, paths, canvas_id="overlay"):
    # 对照线上实际画出的像素，验证实时图、留白、镜像和窄屏共用参考坐标。
    page.wait_for_function("""({paths,id}) => {
      if (document.getElementById('legend').dataset.background !== 'outline') return false;
      const c=document.getElementById(id), w=c.width, h=c.height;
      if (!w || !h) return false;
      const pixels=c.getContext('2d').getImageData(0,0,w,h).data;
      const gold=(x,y)=>{
        const i=4*(y*w+x);
        return pixels[i]>80 && pixels[i]<190 && pixels[i+1]>170 && pixels[i+2]>215 && pixels[i+3]>160;
      };
      let total=0, matched=0;
      for (const line of paths) for (let j=1; j<line.length; j++) {
        const [a,b]=line[j-1], [e,f]=line[j];
        const steps=Math.max(1, Math.ceil(Math.hypot((e-a)*w,(f-b)*h)/7));
        for (let t=0;t<=steps;t++) {
          const x=Math.round((a+(e-a)*t/steps)*w), y=Math.round((b+(f-b)*t/steps)*h);
          let found=false;
          for(let v=Math.max(0,y-2);v<Math.min(h,y+3);v++)
            for(let u=Math.max(0,x-2);u<Math.min(w,x+3);u++) if(gold(u,v)) found=true;
          total++; if(found) matched++;
        }
      }
      return total>10 && matched/total>.90;
    }""", arg={"paths":paths, "id":canvas_id}, timeout=15000)


def expect_position_box(page, box):
    page.wait_for_function("""box => {
      if (document.getElementById('legend').dataset.stage !== 'position') return false;
      const c=document.getElementById('overlay'), w=c.width, h=c.height;
      const data=c.getContext('2d').getImageData(0,0,w,h).data;
      const [l,t,r,b]=box.map((v,i)=>v*(i%2 ? h : w));
      let gold=0;
      for (let y=0;y<h;y+=2) for (let x=0;x<w;x+=2) {
        const i=4*(y*w+x);
        if (Math.abs(data[i]-227)>3 || Math.abs(data[i+1]-199)>3 || Math.abs(data[i+2]-158)>3 || data[i+3]<180) continue;
        if (Math.min(Math.abs(x-l),Math.abs(x-r),Math.abs(y-t),Math.abs(y-b))>3) return false;
        gold++;
      }
      return gold>15;
    }""", arg=box, timeout=15000)


def expect_zoom_verification(frames):
    command = next(i for i, r in enumerate(frames) if r["background"] and r["background"].get("zoomFactor"))
    assert frames[command]["stage"] == "framing" and not frames[command]["checks"][1]
    assert .68 < frames[command]["background"]["zoom"] < .72
    first_position = next(i for i, r in enumerate(frames) if r["stage"] == "position")
    assert first_position > command
    assert abs(frames[first_position]["background"]["zoom"]-1) < .04


def expect_fixed_outline(page, outline):
    # 白线应在参考人物的同一批边界坐标上，包括当前镜头还没有识别到人的时候。
    boundary = max(outline["contours"], key=len)
    page.wait_for_function("""points => {
      if (document.getElementById('legend').dataset.stage !== 'pose') return false;
      const c=document.getElementById('overlay'), w=c.width, h=c.height;
      const d=c.getContext('2d').getImageData(0,0,w,h).data;
      const hits=points.filter(([px,py])=>{
        const x=Math.round(px*w), y=Math.round(py*h);
        for (let j=Math.max(0,y-2);j<=Math.min(h-1,y+2);j++)
        for (let k=Math.max(0,x-2);k<=Math.min(w-1,x+2);k++) {
          const i=4*(j*w+k);
          if(d[i]>215 && d[i+1]>215 && d[i+2]>205 && d[i+3]>185) return true;
        }
        return false;
      });
      return hits.length > .75*points.length;
    }""", arg=boundary[::max(1,len(boundary)//35)], timeout=15000)


def main():
    print("Starting local camera replay", flush=True)
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    library = BASE.parent / "校园摄影参考库_队友分享_98张"
    photo = next(library.rglob("REF_011.webp"))
    server = make_server("127.0.0.1", 0, app, threaded=True)
    Thread(target=server.serve_forever, daemon=True).start()
    address = f"http://127.0.0.1:{server.server_port}"
    errors = []
    qa = BASE / "build/qa"
    qa.mkdir(parents=True, exist_ok=True)
    try:
        with sync_playwright() as playwright:
            cached = list((BASE.parent / ".browser").glob("chromium_headless_shell-*/**/chrome-headless-shell.exe"))
            browser = playwright.chromium.launch(headless=True, timeout=20000, executable_path=str(cached[0]) if cached else None)
            page = browser.new_page(viewport={"width": 1220, "height": 830}, accept_downloads=True)
            references = []
            frames = []
            camera_photo = photo
            def record(r):
                if r.url == address + "/api/session" and r.status == 201:
                    references.append(r.json()["reference"])
                elif "/frame?" in r.url and r.status == 200:
                    frames.append(r.json())
            page.on("response", record)
            page.on("pageerror", lambda exc: errors.append(str(exc)))
            page.route("**/test-camera.webp*", lambda route: route.fulfill(content_type="image/webp", body=camera_photo.read_bytes()))
            page.add_init_script(CAMERA)
            page.goto(address)
            shutter = page.locator("#shutter")
            expect(shutter).to_be_disabled()
            expect(page.locator("#library-count")).to_contain_text("98")
            page.locator("#library-query").fill("REF_011")
            page.locator("#library-search button[type=submit]").click()
            expect(page.locator(".photo-card")).to_have_count(1)
            page.locator("#library-panel").screenshot(path=str(qa / "00-library.png"))
            page.locator(".photo-card").click()
            expect(page.locator("#viewpoint")).to_contain_text("科学与艺术大楼")
            page.locator("#live-file").set_input_files(photo)
            expect(shutter).to_be_enabled(timeout=15000)
            with page.expect_download() as download:
                shutter.click()
            with Image.open(download.value.path()) as saved, Image.open(photo) as source:
                assert saved.format == "JPEG" and saved.size == source.size
                diff = np.abs(np.asarray(saved.convert("RGB"), dtype=float) - np.asarray(source.convert("RGB"), dtype=float))
                assert diff.mean() < 4
            with Image.open(photo) as source:
                w, h = source.size
                small = source.resize((round(.70*w), round(.70*h)), Image.Resampling.LANCZOS)
                wide = Image.new("RGB", source.size, (37,52,45))
                wide.paste(small, ((w-small.width)//2, (h-small.height)//2))
                wide_bytes, close_bytes = io.BytesIO(), io.BytesIO()
                wide.save(wide_bytes, "JPEG", quality=95)
                source.crop((round(.16*w), round(.16*h), round(.84*w), round(.84*h))).save(close_bytes, "JPEG", quality=95)
                flipped = io.BytesIO()
                source.transpose(Image.Transpose.FLIP_LEFT_RIGHT).save(flipped, "JPEG", quality=95)
            start = len(frames)
            page.locator("#live-file").set_input_files({"name":"wide.jpg", "mimeType":"image/jpeg", "buffer":wide_bytes.getvalue()})
            expect(page.locator("#source-state")).to_have_text(re.compile(r"照片试拍 · 1\.4\d×"), timeout=15000)
            expect(shutter).to_be_enabled(timeout=20000)
            expect_zoom_verification(frames[start:])
            with page.expect_download() as zoomed:
                shutter.click()
            with Image.open(zoomed.value.path()) as saved:
                assert abs(saved.width-small.width) < .015*w and abs(saved.height-small.height) < .015*h
                reference_pixels = np.asarray(small.resize(saved.size), dtype=float)
                assert np.abs(np.asarray(saved, dtype=float)-reference_pixels).mean() < 8
            page.locator("#live-file").set_input_files({"name":"close.jpg", "mimeType":"image/jpeg", "buffer":close_bytes.getvalue()})
            expect(page.locator("#hint")).to_contain_text("取景太近", timeout=15000)
            expect(shutter).to_be_disabled()
            page.locator("#skip-stage").click()
            expect(page.locator("#steps li.skipped")).to_have_count(1)
            page.locator("#restore-steps").click()
            page.locator("#live-file").set_input_files({
                "name": "reflected.jpg", "mimeType": "image/jpeg", "buffer": flipped.getvalue()})
            orientation = page.locator("#mirror-ref")
            expect(orientation).to_have_attribute("aria-pressed", "true", timeout=20000)
            expect(shutter).to_be_enabled(timeout=20000)
            orientation.click()
            expect(orientation).to_have_attribute("aria-pressed", "false")
            expect(page.locator("#hint")).to_contain_text("左右相反", timeout=15000)
            expect(shutter).to_be_disabled()
            page.evaluate("window.scene = 'rightWide'")
            page.locator("#camera").click()
            expect(page.locator("#hint")).to_contain_text("向右转", timeout=15000)
            expect(shutter).to_be_disabled()
            expect_camera_rings(page, .67)
            page.locator(".camera").screenshot(path=str(qa / "01-aim.png"))
            expect(page.locator("#source-state")).to_have_text("相机取景")
            page.evaluate("window.scene = 'nearRight'")
            expect(page.locator("#overlay")).to_have_attribute("data-aim", re.compile(r"near|aligned"), timeout=15000)
            expect(page.locator("#aim-transition")).to_be_visible(timeout=15000)
            expect(page.locator("#aim-transition strong")).to_have_text("中心已锁定")
            page.locator(".camera").screenshot(path=str(qa / "06-center-locked.png"))
            ref = references[-1]
            expect_position_box(page, ref["box"])
            page.locator(".camera").screenshot(path=str(qa / "03-position.png"))
            page.evaluate("window.scene = 'poseRight'")
            expect_fixed_outline(page, ref["outline"])
            page.locator(".camera").screenshot(path=str(qa / "04-pose.png"))
            expect(page.locator("#hint")).to_contain_text("向画面左", timeout=10000)
            page.evaluate("window.scene = 'poseLeft'")
            expect(page.locator("#hint")).to_contain_text("向画面右", timeout=10000)
            expect_fixed_outline(page, ref["outline"])
            if os.environ.get("GUIDE_QA_POSE"):
                page.locator(".camera").screenshot(path=os.environ["GUIDE_QA_POSE"])
            page.evaluate("window.scene = 'right'")
            # Center alignment is a one-time completion, not a live gate that reopens on drift.
            expect(page.locator("#restart-aim")).to_be_visible()
            expect(page.locator("#steps li").first).to_have_class("done")
            page.locator("#restart-aim").click()
            expect_camera_rings(page, .67)
            start = len(frames)
            page.evaluate("window.scene = 'centeredWide'")
            expect(page.locator("#source-state")).to_have_text(re.compile(r"相机取景 · 1\.4\d×"), timeout=15000)
            expect(shutter).to_be_enabled(timeout=20000)
            expect_zoom_verification(frames[start:])
            page.evaluate("window.scene = 'veryWide'")
            expect(page.locator("#hint")).to_contain_text("请向地标靠近", timeout=15000)
            expect(page.locator("#source-state")).to_contain_text("1.60×")
            expect(shutter).to_be_disabled()
            page.evaluate("window.scene = 'right'")
            page.locator("#restart-aim").click()
            expect(page.locator("#hint")).to_contain_text("向右转", timeout=15000)
            page.evaluate("window.scene = 'small'")
            expect(page.locator("#source-state")).to_have_text(re.compile(r"相机取景 · 1\.2\d×"), timeout=15000)
            expect(shutter).to_be_enabled(timeout=20000)
            page.evaluate("window.scene = 'right'")
            page.locator("#restart-aim").click()
            expect(page.locator("#hint")).to_contain_text("向右转", timeout=15000)
            page.evaluate("window.scene = 'same'")
            expect(shutter).to_be_enabled(timeout=20000)
            page.evaluate("window.scene = 'blank'")
            expect(shutter).to_be_disabled(timeout=5000)
            expect(page.locator("#steps li").first).to_have_class("done")
            expect(page.locator("#skip-stage")).to_have_text("跳过景别")
            page.locator("#restart-aim").click()
            expect_camera_rings(page, None)
            skip = page.locator("#skip-stage")
            expect(skip).to_have_text("跳过对准")
            skip.click()
            expect(skip).to_have_text("跳过景别")
            skip.click()
            expect(skip).to_have_text("跳过站位")
            expect(page.locator("#hint")).to_contain_text("目标框")
            expect(shutter).to_be_disabled()
            skip.click()
            expect(skip).to_have_text("跳过姿势")
            expect(page.locator("#hint")).to_contain_text("实时关节")
            expect_fixed_outline(page, ref["outline"])
            skip.click()
            expect(shutter).to_be_enabled()
            expect(page.locator("#steps li.skipped")).to_have_count(4)
            with page.expect_download() as manual:
                shutter.click()
            with Image.open(manual.value.path()) as image:
                assert np.abs(np.asarray(image).astype(int) - [37, 52, 45]).max() < 5
            page.locator("#restore-steps").click()
            expect(page.locator("#steps li.skipped")).to_have_count(0)
            expect(shutter).to_be_disabled()
            expect_camera_rings(page, None)
            # 打开新的参考文件同样恢复四个步骤。
            skip.click()
            expect(page.locator("#steps li.skipped")).to_have_count(1)
            page.locator("#ref-file").set_input_files(photo)
            expect(page.locator("#steps li.skipped")).to_have_count(0)
            expect(skip).to_have_text("跳过对准")
            page.evaluate("window.scene = 'same'")
            expect(shutter).to_be_enabled(timeout=20000)
            if os.environ.get("GUIDE_QA_SCREENSHOT"):
                page.screenshot(path=os.environ["GUIDE_QA_SCREENSHOT"])
            page.set_viewport_size({"width":390, "height":844})
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            if os.environ.get("GUIDE_QA_MOBILE"):
                page.screenshot(path=os.environ["GUIDE_QA_MOBILE"])
            page.locator("#camera").click()
            assert page.evaluate("lastCameraTrack.readyState") == "ended"
            expect(shutter).to_be_disabled()
            # 两张独立的圆塔照片经过浏览器实际裁切、JPEG 编码，给出偏移方向。
            page.set_viewport_size({"width":1220, "height":830})
            page.locator("#ref-file").set_input_files(next(library.rglob("REF_046.webp")))
            page.locator("#live-file").set_input_files(next(library.rglob("REF_095.webp")))
            expect(page.locator("#hint")).to_contain_text("向左转", timeout=15000)
            expect(page.locator("#legend")).to_have_attribute("data-background", "rings")
            expect(page.locator("#target-label")).to_have_text("参考中心")
            # 尚未对准时不画建筑轮廓；手动跳过对准后，独立核对景别。
            skip.click()
            expect(page.locator("#target-label")).to_have_text("圆塔参考轮廓")
            outline = references[-1]["buildingOutline"]
            expect_building_outline(page, outline["paths"])
            expect_building_outline(page, outline["paths"], "ref-overlay")
            expect(page.locator("#current-key")).to_be_hidden()
            expect(shutter).to_be_disabled()
            if os.environ.get("GUIDE_QA_BACKGROUND"):
                page.locator(".workspace").screenshot(path=os.environ["GUIDE_QA_BACKGROUND"])
            page.set_viewport_size({"width":390, "height":844})
            expect_building_outline(page, outline["paths"])
            # 断开匹配时轮廓仍在原位。跳过机位后切换到人物框。
            blank = io.BytesIO()
            Image.new("RGB", (640,960), (80,85,90)).save(blank, "JPEG")
            page.locator("#live-file").set_input_files({"name":"blank.jpg","mimeType":"image/jpeg","buffer":blank.getvalue()})
            expect(page.locator("#hint")).to_contain_text("继续核对景别", timeout=15000)
            expect_building_outline(page, outline["paths"])
            skip.click()
            expect_position_box(page, references[-1]["box"])
            page.set_viewport_size({"width":1220, "height":830})
            # 相机画面偏移时轮廓仍固定，对齐后自动变焦、复核并放行。
            page.locator("#restore-steps").click()
            camera_photo = next(library.rglob("REF_046.webp"))
            page.evaluate("window.scene = 'rightWide'")
            page.locator("#camera").click()
            expect(page.locator("#hint")).to_contain_text("向右转", timeout=15000)
            expect_camera_rings(page, .67)
            start = len(frames)
            page.evaluate("document.getElementById('auto-zoom').checked = false; window.scene = 'centeredWide'")
            expect_building_outline(page, outline["paths"])
            expect(page.locator("#zoom-controls")).to_be_visible()
            expect(page.locator("#source-state")).to_have_text("相机取景")
            page.locator(".camera").screenshot(path=str(qa / "02-framing.png"))
            page.locator("#auto-zoom").check()
            expect(shutter).to_be_enabled(timeout=20000)
            expect_zoom_verification(frames[start:])
            expect(page.locator("#legend")).not_to_have_attribute("data-background", "outline")
            page.locator("#camera").click()
            page.locator("#live-file").set_input_files({"name":"blank.jpg","mimeType":"image/jpeg","buffer":blank.getvalue()})
            # 切换到钟楼，近景可用钟面和墙面两边对齐。参考翻转后目标随之镜像。
            clock_photo = next(library.rglob("REF_029.webp"))
            page.locator("#ref-file").set_input_files(clock_photo)
            expect(page.locator("#skip-stage")).to_have_text("跳过对准", timeout=20000)
            skip.click()
            expect(page.locator("#target-label")).to_have_text("钟楼参考轮廓", timeout=20000)
            clock_outline = references[-1]["buildingOutline"]
            expect_building_outline(page, clock_outline["paths"])
            page.locator("#mirror-ref").click()
            expect(page.locator("#target-label")).to_have_text("钟楼参考轮廓", timeout=20000)
            expect(page.locator("#mirror-ref")).to_be_enabled()
            mirror_paths = references[-1]["buildingOutline"]["paths"]
            assert np.vstack(mirror_paths)[:,0].mean() < np.vstack(clock_outline["paths"])[:,0].mean() - .12
            expect_building_outline(page, mirror_paths)
            # 同样标注在钟楼的栏杆取景没有清晰塔楼，应恢复双圈。
            page.locator("#ref-file").set_input_files(next(library.rglob("REF_062.webp")))
            expect(page.locator("#legend")).to_have_attribute("data-background","rings",timeout=20000)
            expect(page.locator("#target-label")).to_have_text("参考中心")
            expect_camera_rings(page, None)
            assert not errors, errors
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
    print("Library selection, smoothed white target/ring, alignment feedback, cyan outline, position, pose, zoom, skips and clean JPEG verified.")


if __name__ == "__main__":
    main()

