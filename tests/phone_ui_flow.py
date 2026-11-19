"""Full-screen phone UX, one-shot scene retrieval and bounded pose guidance.
Real Chromium/Worker with simulated camera, server and photo export.
"""
import base64
import io
import json
from functools import partial
from http.server import ThreadingHTTPServer
from threading import Thread

from PIL import Image, ImageOps
from playwright.sync_api import sync_playwright, expect

from frame_pipeline_flow import ROOT, Site, MOCK
from native_app_flow import browser_path

CONTRACTS=r"""async()=>{
 const {HintPacer,PoseBudget}=await import('/static/hints.js');
 const check=(ok,msg)=>{if(!ok)throw new Error(msg);};
 const h=new HintPacer(),cue=(message,key='background')=>({message,key,who:'摄影者',mode:'adjust'});
 h.update(cue('对准白点'),0);
 h.update(cue('调整站位','position'),100,{immediate:true});
 check(h.flush(2999)===null,'Stage change broke the 3-second minimum');
 check(h.flush(3000)?.message==='调整站位','Queued stage never appeared');
 h.update(cue('旧方向','position'),3100);h.update(cue('新方向','position'),5000);
 check(h.flush(6000)?.message==='新方向','Stale suggestion survived the latest cue');
 h.update({...cue('保存失败','position'),mode:'error'},6100,{immediate:true});
 h.update(cue('新方向','position'),6200);
 check(h.flush(9000)?.message==='保存失败','A frame erased action feedback');
 const budget=new PoseBudget();
 for(let t=0;t<=7000;t+=100)budget.tick(t,true);
 budget.tick(8000,false);budget.tick(30000,false);
 check(!budget.done&&budget.elapsed===7000,'Background time consumed the pose budget');
 for(let t=30100;t<=38100;t+=100)budget.tick(t,true);
 check(budget.done&&budget.round===5,'Five pose rounds did not grant capture');
 budget.reset();check(!budget.done&&budget.elapsed===0,'New reference inherited pose allowance');
 return {hint_minimum_ms:3000,latest_hint_wins:true,pose_five_rounds:true,pause_preserves_budget:true};
}"""

ENHANCEMENTS=r"""
qa.stage='background';qa.sceneRequests=[];qa.sessions=[];qa.saved=[];qa.thumbnails=0;qa.failConnection=false;
qa.photos=qa.assets.map((a,i)=>({photo_id:'ref_'+i,location:'校园光影',description:'自然校园人像',viewpoint:'校园',
 image_url:'api/library/ref_'+i+'/image',thumbnail_url:'api/library/ref_'+i+'/thumbnail',aesthetic_score:8.5-i*.3}));
const request=Capacitor.Plugins.CapacitorHttp.request;
Capacitor.Plugins.PhotoExport={savePhoto:async p=>{qa.saved.push(p);return {uri:'content://qa/1'};},sharePhoto:async()=>({})};
Capacitor.Plugins.CapacitorHttp.request=async o=>{
 if(o.url.endsWith('/api/health') && qa.failConnection)throw new Error('QA: computer offline');
 let r=await request(o);
 if(o.url.endsWith('/api/library'))r.data={photos:qa.photos,locations:[],count:5};
 if(o.url.endsWith('/api/recommend')) {qa.sceneRequests.push(o.data);r.data={recommendations:qa.photos,scene_used:true};}
 if(/\/api\/library\/ref_\d+\/(image|thumbnail)$/.test(o.url)) {
  const index=Number(o.url.match(/ref_(\d+)/)[1]);if(o.url.endsWith('thumbnail'))qa.thumbnails++;
  r={status:200,data:qa.assets[index],headers:{'Content-Type':'image/jpeg'}};
 }
 if(o.url.endsWith('/api/session')) {
  qa.sessions.push(o.data);
  const aspect=Number(o.data.find(p=>p.key==='aspect')?.value||.75);
  Object.assign(r.data.reference,{workflow:"composition",farBounds:[.12,.1,.45,.42],width:720*aspect,height:720,box:[.25,.4,.6,.9],
   outline:{kind:'person',contours:[[[.25,.4],[.6,.4],[.6,.9],[.25,.9]]],details:[]},
   backgroundOutline:{paths:[[[.15,.1],[.85,.1],[.85,.8]]]}});
 }
 if(o.url.includes('/frame?')) Object.assign(r.data,{stage:qa.stage,ready:qa.stage==='ready',mode:'adjust',
   message:qa.stage==='pose'?(qa.frames.length%2?'右手抬高一点':'肩膀放松一点'):'转动手机，让白点靠近白环',
   pose:qa.stage==='pose'?{actual:[],target:[],joint:null}:null,
   completed:qa.stage==='background'?[]:['background'],checks:[true,true,true,true,qa.stage!=='position',false]});
 return r;
};
const camera=navigator.mediaDevices.getUserMedia;
Object.defineProperty(navigator.mediaDevices,'getUserMedia',{value:async o=>{
 const image=new Image();image.src='data:image/jpeg;base64,'+qa.assets[0];await image.decode();
 const c=document.createElement('canvas');c.width=720;c.height=1280;const ctx=c.getContext('2d');
 const paint=()=>{const ratio=Math.max(c.width/image.width,c.height/image.height);const w=image.width*ratio,h=image.height*ratio;ctx.drawImage(image,(c.width-w)/2,(c.height-h)/2,w,h);};
 paint();const timer=setInterval(paint,1000/30),stream=c.captureStream(30),track=stream.getVideoTracks()[0],stop=track.stop.bind(track);
 track.stop=()=>{clearInterval(timer);stop();};return stream;
}});
"""

def main():
    photos=list((ROOT.parent/'campus-photo-recommend/data/formal/images').glob('*.webp'))
    preferred=[p for p in photos if p.stem in ['REF_011','REF_029','REF_014','REF_015','REF_025']]
    chosen=(preferred+photos)[:5]
    assets=[]
    for path in chosen:
        with Image.open(path) as source:
            im=ImageOps.exif_transpose(source).convert('RGB');im.thumbnail((960,1280));out=io.BytesIO();im.save(out,'JPEG',quality=85)
            assets.append(base64.b64encode(out.getvalue()).decode())
    server=ThreadingHTTPServer(('127.0.0.1',0),partial(Site,directory=ROOT))
    Thread(target=server.serve_forever,daemon=True).start()
    qa=ROOT/'build/qa';qa.mkdir(parents=True,exist_ok=True)
    report={'passed':False,'scope':'Synthetic hardware and responses; real phone-size page and Worker'}
    try:
        with sync_playwright() as pw:
            browser=pw.chromium.launch(executable_path=browser_path())
            ctx=browser.new_context(viewport={'width':390,'height':844},is_mobile=True,has_touch=True,device_scale_factor=2)
            ctx.add_init_script(MOCK.replace("'getUserMedia',{value:","'getUserMedia',{configurable:true,value:")+'qa.assets='+json.dumps(assets)+';'+ENHANCEMENTS)
            page=ctx.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto(f'http://127.0.0.1:{server.server_port}/')
            report.update(page.evaluate(CONTRACTS))
            expect(page.locator('#source-state')).to_have_text('相机取景')
            rect=page.locator('#surface').bounding_box()
            assert rect['width']==390 and rect['height']==844,rect
            assert page.evaluate('qa.frames.length')==0
            page.screenshot(path=qa/'phone-01-camera.png')
            page.locator('#style-request').fill('自然松弛的校园人像')
            page.locator('#style-submit').click()
            expect(page.locator('#phone-connect-sheet')).to_be_visible()
            page.screenshot(path=qa/'phone-00-connect.png')
            assert not page.evaluate('qa.sceneRequests.length')
            page.evaluate('qa.failConnection=true')
            page.locator('#computer-server').fill('http://127.0.0.1:7862')
            page.locator('#computer-connect-button').click()
            expect(page.locator('#computer-status')).to_contain_text('无法连接电脑')
            expect(page.locator('#phone-connect-sheet')).to_be_visible()
            page.locator('#connect-later').click()
            expect(page.locator('#phone-connect-sheet')).not_to_be_visible()
            expect(page.locator('#shutter')).to_be_enabled()
            assert not page.evaluate('qa.sceneRequests.length')
            # Reopen and connect; the pending style request resumes exactly once.
            page.locator('#style-submit').click()
            page.evaluate('qa.failConnection=false')
            page.locator('#computer-connect-button').click()
            expect(page.locator('.photo-card')).to_have_count(5)
            expect(page.locator('#phone-connect-sheet')).not_to_be_visible()
            expect(page.locator('#phone-connection')).to_contain_text('电脑已连接')
            # A failed replacement address must not dismiss the editor or submit again.
            page.locator('#phone-connection').click()
            page.locator('#computer-server').fill('http://192.168.1.99:7862')
            page.evaluate('qa.failConnection=true')
            page.locator('#computer-connect-button').click()
            expect(page.locator('#computer-status')).to_contain_text('仍使用之前连接的电脑')
            expect(page.locator('#phone-connect-sheet')).to_be_visible()
            page.locator('#connect-later').click()
            page.evaluate('qa.failConnection=false')
            page.locator('#phone-settings').click()
            expect(page.locator('#computer-server')).to_be_visible()
            page.locator('#phone-settings-sheet [data-close]').click()
            page.wait_for_function('qa.thumbnails===5')
            assert page.evaluate('qa.sceneRequests.length')==1
            payload=page.evaluate('qa.sceneRequests[0]')
            assert payload['limit']==5 and payload['query']=='自然松弛的校园人像'
            with Image.open(io.BytesIO(base64.b64decode(payload['scene_image']))) as shot:
                assert max(shot.size)<=640 and abs(shot.width/shot.height-390/844)<.01
            assert not page.evaluate('qa.sessions.length'),'Merely swiping must not prepare references'
            page.locator('.photo-card').nth(2).click()
            page.wait_for_timeout(450)
            expect(page.locator('.photo-card').nth(2)).to_have_class('photo-card is-current')
            page.screenshot(path=qa/'phone-02-references.png')
            page.locator('#use-reference').click()
            expect(page.locator('body')).to_have_attribute('data-phone-phase','guide')
            assert len(page.evaluate('qa.sessions'))==1
            assert page.evaluate("qa.sessions[0].some(p => p.key==='workflow' && p.value==='composition')")
            for stage, caption in [('distance','摄影距离'), ('framing','调整倍率'), ('recenter','复核中心'), ('position','05 / 人物位置')]:
                page.evaluate('stage => qa.stage=stage', stage)
                expect(page.locator('#phone-stage')).to_contain_text(caption,timeout=8000)
                expect(page.locator('#shutter')).to_be_disabled()
            page.screenshot(path=qa/'phone-03-guide.png')
            page.evaluate("qa.stage='pose';window.hintTrace=[];new MutationObserver(()=>{const text=document.querySelector('#hint').textContent;if(!hintTrace.length||hintTrace.at(-1).text!==text)hintTrace.push({at:performance.now(),text});}).observe(document.querySelector('#hint'),{childList:true,subtree:true,characterData:true});")
            expect(page.locator('#pose-count')).to_be_visible(timeout=6000)
            page.wait_for_timeout(3700)
            page.screenshot(path=qa/'phone-04-pose.png')
            expect(page.locator('#shutter')).to_be_enabled(timeout=22000)
            expect(page.locator('#hint')).to_have_text('可以拍摄了',timeout=4500)
            trace=page.evaluate('hintTrace')
            assert all(b['at']-a['at']>=2990 for a,b in zip(trace,trace[1:])),trace
            assert len([x for x in trace if '手' in x['text'] or '肩' in x['text']])<=5,trace
            count=page.evaluate('qa.frames.length');page.wait_for_timeout(1500)
            assert page.evaluate('qa.frames.length')==count,'Pose budget still uploaded frames'
            page.screenshot(path=qa/'phone-05-ready.png')
            page.locator('#shutter').click()
            expect(page.locator('#phone-export-sheet')).to_be_visible()
            page.locator('#download-shot').click();page.wait_for_function('qa.saved.length===1')
            with Image.open(io.BytesIO(base64.b64decode(page.evaluate('qa.saved[0].base64')))) as output:
                assert abs(output.width/output.height-390/844)<.01
            page.locator('#phone-export-sheet [data-close]').click()
            page.locator('#change-style').click()
            expect(page.locator('#style-request')).to_be_visible()
            page.evaluate('qa.failConnection=true')
            page.evaluate("async()=>{const {serverRequest}=await import('./static/native-server.js');try{await serverRequest('api/health');}catch{}}")
            expect(page.locator('#phone-connection')).to_contain_text('重新连接')
            expect(page.locator('#phone-connect-sheet')).not_to_be_visible()
            page.reload()
            expect(page.locator('#phone-connection')).to_contain_text('电脑已连接')
            expect(page.locator('#phone-connect-sheet')).not_to_be_visible()
            assert not page.evaluate('qa.sceneRequests.length'),'Restoring a connection must not replay an old search'
            assert not errors,errors
            report.update(fullscreen_preview=True,scene_and_style_sent=True,five_cards=True,explicit_reference_selection=True,
                pose_limit_allows_capture=True,hint_trace=trace,clean_capture_aspect=True,connection_dialog=True,
                offline_preview_allowed=True,failed_connection_stays_open=True,resume_search_once=True,
                reconnect_without_popup_loop=True,saved_connection_restored=True,page_errors=errors,passed=True)
            browser.close()
    finally:
        server.shutdown();server.server_close()
        (qa/'phone-ui-results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(report,ensure_ascii=False))

if __name__=='__main__':main()
