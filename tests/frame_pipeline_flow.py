"""Real Chromium video/Worker regression; simulated camera and native HTTP, no phone FPS claim.

python tests/frame_pipeline_flow.py [--executable path/to/chrome]
Requires Pillow and Playwright. No model downloads or Android build are needed.
"""
import argparse
import base64
import io
import json
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

from PIL import Image, ImageChops, ImageStat
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]


class Site(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path == "/":
            html = (ROOT / "static/index.html").read_text("utf-8").replace(
                "</head>", '<meta name="guide-native" content="android"></head>')
            data = html.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        else:
            super().do_GET()


MOCK = """
window.qa={delay:0,active:0,peak:0,frames:[],constraints:[]};
window.Capacitor={getPlatform:()=> 'android', Plugins:{CapacitorHttp:{request:async options=>{
  let data={};
  if(options.url.endsWith('/api/health')) data={status:'ok',service:'campus-photo-guide',version:1};
  else if(options.url.endsWith('/api/library')) data={photos:[],locations:[],count:0};
  else if(options.url.endsWith('/api/session')) data={id:'test',reference:{
    width:720,height:960,boxes:[],box:null,points:[],edges:[],landmarks:[],viewpoint:'',
    subjectHint:'',aimPoint:[.5,.5],outline:null,buildingOutline:null,backgroundOutline:null}};
  else if(options.url.includes('/frame?')) {
    qa.frames.push({at:performance.now(),data:options.data});qa.active++;qa.peak=Math.max(qa.peak,qa.active);
    try {await new Promise(r=>setTimeout(r,qa.delay));}
    finally {qa.active--;}
    data={stage:'ready',ready:true,mode:'ready',message:'可以拍摄',background:null,pose:null,
      boxes:[],selected:null,completed:[],checks:[true,true,true,true],skipped:[],hold:0,timing:{serverMs:5}};
  }
  return {status:200,data,headers:{}};
}}}};
Object.defineProperty(navigator.mediaDevices,'getUserMedia',{value:async options=>{
  qa.constraints.push(options);
  const c=document.createElement('canvas');c.width=1280;c.height=720;
  const ctx=c.getContext('2d');let n=0;
  const draw=()=>{
    ctx.fillStyle='#b45a28';ctx.fillRect(0,0,1280,720);
    ctx.fillStyle='#1676ab';ctx.fillRect(500,160,180,340);
    ctx.fillStyle=(n++%2)?'white':'black';ctx.fillRect(10,10,20,20);
  };
  draw();const timer=setInterval(draw,1000/30), stream=c.captureStream(30);
  const track=stream.getVideoTracks()[0],stop=track.stop.bind(track);
  track.stop=()=>{clearInterval(timer);stop();};return stream;
}});
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--executable")
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Site, directory=ROOT))
    Thread(target=server.serve_forever, daemon=True).start()
    report = {"scope": "Synthetic Chromium video and native HTTP; real Worker/JPEG, not Android hardware"}
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(executable_path=args.executable)
            context = browser.new_context(viewport={"width":390,"height":844}, is_mobile=True)
            context.add_init_script(MOCK)
            page = context.new_page()
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(f"http://127.0.0.1:{server.server_port}/")
            page.locator("#guide-mode").select_option("live")
            page.locator("#camera").click()
            expect(page.locator("#source-state")).to_have_text("相机取景")
            # Compare both Worker routes to the exact same center-crop geometry.
            result = page.evaluate("""async()=>{
              const {FrameSampler}=await import('/static/frame-sampler.js');
              const v=document.querySelector('#video'), originalBitmap=window.createImageBitmap;
              const fast=new FrameSampler();
              window.createImageBitmap=()=>{throw new Error('Fast path must not resize on main');};
              const a=await fast.capture(v,.75,1.35);fast.dispose();
              window.createImageBitmap=originalBitmap;
              const originalFrame=window.VideoFrame;
              window.VideoFrame=function(){throw new Error('Unsupported WebView');};
              const fallback=new FrameSampler();const b=await fallback.capture(v,.75,1.35);fallback.dispose();
              window.VideoFrame=originalFrame;
              const cw=Math.min(v.videoWidth,v.videoHeight*.75)/1.35,ch=cw/.75;
              const c=document.createElement('canvas');c.width=Math.round(cw);c.height=Math.round(ch);
              c.getContext('2d').drawImage(v,(v.videoWidth-cw)/2,(v.videoHeight-ch)/2,cw,ch,0,0,c.width,c.height);
              return {fast:{path:a.path,base64:a.base64,mainMs:a.mainMs,workerMs:a.workerMs},
                fallback:{path:b.path,base64:b.base64},expected:c.toDataURL('image/jpeg',.75).split(',')[1]};
            }""")
            assert result["fast"]["path"] == "video-frame"
            assert result["fallback"]["path"] == "image-bitmap"
            def decode(value):
                return Image.open(io.BytesIO(base64.b64decode(value))).convert("RGB")
            expected = decode(result["expected"])
            for key in ("fast", "fallback"):
                actual = decode(result[key]["base64"])
                assert actual.size == expected.size
                assert max(ImageStat.Stat(ImageChops.difference(actual, expected)).mean) < 4
            report["worker_paths_and_zoom_crop"] = True
            report["sample_main_ms"] = result["fast"]["mainMs"]
            report["sample_worker_ms"] = result["fast"]["workerMs"]
            # Cancellation during asynchronous bitmap preparation must release ownership
            # and never send a camera resource to a disposed Worker.
            assert page.evaluate("""async()=>{
              const {FrameSampler}=await import('/static/frame-sampler.js');
              const saved=window.createImageBitmap, sampler=new FrameSampler();sampler.videoFrames=false;
              let release;window.createImageBitmap=async(...args)=>{const b=await saved(...args);
                await new Promise(r=>release=r);return b;};
              const pending=sampler.capture(document.querySelector('#video'),.75,1);
              while(!release) await new Promise(r=>setTimeout(r,5));
              const second=await sampler.capture(document.querySelector('#video'),.75,1);
              sampler.dispose();release();const first=await pending;window.createImageBitmap=saved;
              return first===null && second===null && sampler.worker===null && !sampler.capturing;
            }""")
            report["single_flight_and_disposal"] = True

            page.locator("#computer-server").fill("http://127.0.0.1:7862")
            page.locator("#computer-connect-button").click()
            expect(page.locator("#computer-status")).to_contain_text("已连接电脑")
            photo = io.BytesIO()
            Image.new("RGB", (720,960), (180,90,40)).save(photo,"JPEG")
            page.locator("#ref-file").set_input_files({"name":"reference.jpg","mimeType":"image/jpeg","buffer":photo.getvalue()})
            expect(page.locator("#shutter")).to_be_enabled(timeout=15000)
            page.evaluate("qa.delay=6000")
            page.wait_for_function("qa.active===1")
            before = page.evaluate("({time:video.currentTime,count:qa.frames.length})")
            # Expiry must happen before the delayed next response returns.
            expect(page.locator("#shutter")).to_be_disabled(timeout=4500)
            assert page.evaluate("qa.active") == 1
            assert page.evaluate("qa.frames.length") == before["count"]
            assert page.evaluate("video.currentTime") > before["time"] + 1
            assert page.evaluate("qa.peak") == 1
            report["stale_ready_expires_during_request"] = True
            report["slow_request_keeps_preview_running"] = True
            page.locator("#performance-details summary").click()
            expect(page.locator("#performance-values")).to_contain_text("video-frame", timeout=3000)
            page.locator("#pause-analysis").click()
            count = page.evaluate("qa.frames.length")
            page.wait_for_timeout(2200)
            assert page.evaluate("qa.frames.length") == count
            report["pause_does_not_queue_frames"] = True
            page.locator("#preview-quality").select_option("480")
            page.wait_for_function("qa.constraints.at(-1).video.height.ideal===480")
            report["low_resolution_option"] = True
            assert not errors, errors
            report["page_errors"] = errors
            report["passed"] = True
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
        output = ROOT / "build/qa/frame-pipeline-results.json"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
