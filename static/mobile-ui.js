import {nativeAndroid} from './native-export.js';
import {getConnectionState} from './native-server.js';

export const phoneCamera=nativeAndroid;
if (phoneCamera) document.body.classList.add('phone-ui');
const el=id=>document.getElementById(id);
const icon=path=>`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${path}</svg>`;
const symbols={settings:'<path d="M4 7h16M4 17h16"/><circle cx="9" cy="7" r="3" fill="#181c20"/><circle cx="15" cy="17" r="3" fill="#181c20"/>',
  flip:'<path d="M5 8a8 8 0 0 1 13-2l2 2M20 3v5h-5M19 16a8 8 0 0 1-13 2l-2-2M4 21v-5h5"/>',
  image:'<rect x="3" y="3" width="18" height="18" rx="4"/><circle cx="8" cy="8" r="1.5"/><path d="m3 17 5-5 4 4 4-6 5 7"/>',
  arrow:'<path d="M5 12h14m-6-6 6 6-6 6"/>',close:'<path d="m6 6 12 12M6 18 18 6"/>'};

export function setupPhone({startCamera,search,changeZoom,rechoose}) {
  if (!phoneCamera) return {sync(){},setPhase(){},closeSheets(){}};
  const root=document.createElement('div');root.id='phone-shell';
  root.innerHTML=`
    <div id="phone-top"><button id="phone-settings" class="icon-button" aria-label="设置">${icon(symbols.settings)}</button>
      <div class="phone-brand"><span>校园人像</span><small id="phone-stage">寻找灵感</small><button id="phone-connection" type="button" aria-label="连接电脑"><i aria-hidden="true"></i><span>连接电脑</span></button></div>
      <button id="phone-reference" class="icon-button" aria-label="参考照片">${icon(symbols.image)}</button></div>
    <div id="phone-dock">
      <div id="phone-discover"><div class="discover-heading"><span>把此刻，拍成喜欢的样子</span><small>场景 × 风格</small></div>
        <form id="style-form"><label class="sr-only" for="style-request">拍摄风格需求</label><input id="style-request" maxlength="256" placeholder="想拍什么感觉？自然、胶片、青春…" autocomplete="off" enterkeyhint="search"><button id="style-submit" type="submit" aria-label="按当前场景推荐">${icon(symbols.arrow)}</button></form>
        <div id="style-chips"><button type="button">自然松弛</button><button type="button">青春校园</button><button type="button">电影氛围</button></div></div>
      <div id="phone-candidates" hidden><div class="candidate-heading"><span>为此刻挑选</span><small id="candidate-count"></small></div><div id="phone-cards"></div><button id="use-reference" type="button" disabled>就拍这张 <span>→</span></button></div>
      <div id="phone-message"></div><div id="phone-guidance"></div>
      <div id="phone-zoom-area"><button id="zoom-dial" role="slider" aria-label="变焦，左右滑动调整" aria-valuemin="1" aria-valuemax="1.6" aria-valuenow="1"><span id="dial-value">1×</span></button><div id="zoom-popover" hidden></div></div>
      <div id="phone-actions"><button id="phone-album" class="icon-button" aria-label="查看刚拍摄的照片">${icon(symbols.image)}</button><div id="phone-shutter"></div><div id="phone-flip"></div></div>
      <div class="phone-mode"><span>人像导拍</span><button id="change-style" type="button" hidden>换个风格</button></div>
    </div>
    <div id="phone-start"><span class="start-mark">◉</span><h1>你的校园摄影搭子</h1><p>从眼前的风景，找到拍摄灵感</p><button id="phone-open-camera" type="button">开启取景</button></div>
    <dialog id="phone-settings-sheet" class="phone-sheet"><div class="sheet-heading"><h2>拍摄设置</h2><button data-close class="icon-button" aria-label="关闭设置">${icon(symbols.close)}</button></div><div id="phone-settings-content"></div></dialog>
    <dialog id="phone-reference-sheet" class="phone-sheet"><div class="sheet-heading"><h2>参考照片</h2><button data-close class="icon-button" aria-label="关闭参考照片">${icon(symbols.close)}</button></div><div id="phone-reference-content"></div></dialog>
    <dialog id="phone-export-sheet" class="phone-sheet"><div class="sheet-heading"><h2>留住这一刻</h2><button data-close class="icon-button" aria-label="关闭照片">${icon(symbols.close)}</button></div><div id="phone-export-content"></div></dialog>
    <dialog id="phone-connect-sheet" class="phone-sheet" aria-labelledby="connect-title" aria-describedby="connect-description">
      <div class="sheet-heading"><span class="connect-symbol" aria-hidden="true">${icon('<rect x="3" y="3" width="18" height="13" rx="2"/><path d="M8 21h8m-4-5v5M7 10l3 3 7-7"/>')}</span><button data-close class="icon-button" aria-label="关闭连接弹窗">${icon(symbols.close)}</button></div>
      <h2 id="connect-title" tabindex="-1">连接电脑，开始导拍</h2><p id="connect-description">手机负责取景，电脑帮你识别和选图。</p>
      <ol class="connect-steps"><li><span>1</span><div>启动电脑服务<small>在电脑上双击 start-phone.cmd，保留窗口</small></div></li><li><span>2</span><div>让手机与电脑连接同一网络<small>同一 Wi-Fi，或让电脑连接手机热点</small></div></li><li><span>3</span><div>填入启动窗口显示的地址<small>例如 http://192.168.1.20:7862</small></div></li></ol>
      <div id="phone-connect-content"></div><button id="connect-later" type="button">先只拍照</button><p class="connect-note">暂不连接也能取景和拍照，AI 推荐与导拍需连接电脑。</p>
    </dialog>`;
  document.body.append(root);
  document.body.dataset.phonePhase='discover';
  const move=(target,node)=>{if(node)el(target).append(node);};
  move('phone-settings-content',el('computer-connection'));
  const connectionHome=document.createComment('computer connection home');
  el('computer-connection').before(connectionHome);
  for (const id of ['preview-controls','performance-details','camera','choose-live','restore-steps','restart-aim'])move('phone-settings-content',el(id));
  move('phone-settings-content',el('library-panel'));
  el('library-panel').open=false;
  move('phone-reference-content',document.querySelector('aside'));
  move('phone-export-content',el('export'));
  move('phone-guidance',document.querySelector('.feedback'));
  move('phone-guidance',el('demo-controls'));
  move('phone-shutter',el('shutter'));
  el('shutter').setAttribute('aria-label','拍摄');
  move('phone-flip',el('flip'));el('flip').setAttribute('aria-label','切换镜头');el('flip').innerHTML=icon(symbols.flip);
  move('zoom-popover',el('zoom-controls'));
  move('phone-cards',el('library-results'));move('phone-message',el('library-status'));
  for (const dialog of root.querySelectorAll('dialog')) {
    dialog.querySelector('[data-close]').onclick=()=>dialog.close();
    dialog.onclick=e=>{if(e.target===dialog)dialog.close();};
  }
  const closeSheets=()=>root.querySelectorAll('dialog[open]').forEach(d=>d.close());
  const connectionDialog=el('phone-connect-sheet');
  let afterConnection=null;
  const openConnection=resume=>{
    if(connectionDialog.open)return;
    closeSheets();afterConnection=resume || null;
    move('phone-connect-content',el('computer-connection'));
    connectionDialog.showModal();el('connect-title').focus({preventScroll:true});
  };
  connectionDialog.addEventListener('close',()=>{
    if(connectionDialog.open)return;
    connectionHome.after(el('computer-connection'));afterConnection=null;
  });
  el('connect-later').onclick=()=>connectionDialog.close();
  el('phone-connection').onclick=()=>openConnection();
  const syncConnection=()=>{
    const state=getConnectionState(),button=el('phone-connection');
    const label={disconnected:'连接电脑',connecting:'连接中…',connected:'电脑已连接',unreachable:'重新连接'}[state.phase];
    button.dataset.state=state.phase;button.querySelector('span').textContent=label;
    button.setAttribute('aria-label',label+'，打开连接设置');
  };
  window.addEventListener('campus-connection-confirmed',()=>{
    if(connectionDialog.open){
      const resume=afterConnection;afterConnection=null;connectionDialog.close();
      if(resume)queueMicrotask(resume);
    }
  });
  window.addEventListener('campus-connection-state',syncConnection);syncConnection();
  el('phone-settings').onclick=()=>el('phone-settings-sheet').showModal();
  el('phone-reference').onclick=()=>el('phone-reference-sheet').showModal();
  el('phone-album').onclick=()=>el(el('export').hidden?'phone-reference-sheet':'phone-export-sheet').showModal();
  el('phone-open-camera').onclick=startCamera;
  el('style-form').onsubmit=async event=>{
    event.preventDefault();el('style-request').blur();
    if(el('style-submit').disabled)return;
    if(getConnectionState().phase!=='connected'){
      openConnection(()=>el('style-form').requestSubmit());return;
    }
    el('library-query').value=el('style-request').value.trim();
    el('style-submit').disabled=true;
    // Let the soft keyboard dismiss before cropping the one-off scene snapshot.
    try {await new Promise(resolve=>setTimeout(resolve,350));await search();} finally {el('style-submit').disabled=false;}
  };
  el('style-chips').onclick=e=>{if(e.target.tagName==='BUTTON'){el('style-request').value=e.target.textContent;el('style-form').requestSubmit();}};
  el('change-style').onclick=()=>{rechoose();setPhase('discover');};
  const setPhase=phase=>{document.body.dataset.phonePhase=phase;el('change-style').hidden=phase==='discover';};
  window.addEventListener('campus-reference-loading',()=>{closeSheets();setPhase('preparing');});
  window.addEventListener('campus-reference-ready',()=>setPhase('guide'));
  window.addEventListener('campus-reference-error',()=>setPhase('choosing'));
  window.addEventListener('campus-photo-captured',()=>{el('phone-export-sheet').showModal();});
  window.addEventListener('campus-recommendations',e=>{
    el('phone-candidates').hidden=!e.detail.count;
    el('candidate-count').textContent=`${e.detail.count} 张 · 左右滑动挑选`;
    setPhase(e.detail.count?'choosing':'discover');
  });
  let drag=null;
  const dial=el('zoom-dial');
  dial.onpointerdown=e=>{drag={x:e.clientX,value:Number(dial.getAttribute('aria-valuenow')),moved:false};dial.setPointerCapture(e.pointerId);};
  dial.onpointermove=e=>{if(!drag)return;const dx=e.clientX-drag.x;if(Math.abs(dx)>4)drag.moved=true;if(drag.moved)changeZoom(drag.value+dx/90);};
  dial.onpointerup=()=>{if(drag&&!drag.moved)el('zoom-popover').hidden=!el('zoom-popover').hidden;drag=null;};
  dial.onpointercancel=()=>{drag=null;};
  dial.onkeydown=e=>{if(['ArrowLeft','ArrowDown','ArrowRight','ArrowUp'].includes(e.key)){e.preventDefault();changeZoom(Number(dial.getAttribute('aria-valuenow'))+(['ArrowRight','ArrowUp'].includes(e.key) ? .1 : -.1));}};
  const setText=(id,text)=>{if(el(id).textContent!==text)el(id).textContent=text;};
  const setAttribute=(name,value)=>{if(dial.getAttribute(name)!==String(value))dial.setAttribute(name,value);};
  // Starting preview does not start frame uploads until a reference is selected.
  if (nativeAndroid) queueMicrotask(startCamera);
  return {setPhase,closeSheets,sync({source,stage,zoom,min,max,reference,poseRound,poseDone}){
    el('phone-start').hidden=!!source;
    el('zoom-dial').disabled=!source;
    setText('dial-value',Number(zoom.toFixed(1))+'×');
    setAttribute('aria-valuenow',zoom.toFixed(2));setAttribute('aria-valuemin',min);setAttribute('aria-valuemax',max);
    const phases={background:'01 / 对准背景',distance:'02 / 摄影距离',recenter:'04 / 复核中心',framing:reference?.workflow==='composition'?'03 / 调整倍率':'02 / 匹配景别',position:reference?.workflow==='composition'?'05 / 人物位置':'03 / 人物站位',pose:reference?.workflow==='composition'?'06 / 微调姿势':'04 / 调整姿势',ready:'准备拍摄'};
    setText('phone-stage',poseDone?'准备拍摄':reference?(phases[stage]||'准备参考照'):'寻找灵感');
    el('pose-count').hidden=stage!=='pose';
    const counter=`姿势建议 ${poseRound || 1}/5`;
    if(el('pose-count').textContent!==counter)el('pose-count').textContent=counter;
  }};
}
