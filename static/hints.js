// One readable cue; the newest sustained suggestion replaces it after >= 3 seconds.
export const MIN_DISPLAY_MS = 3000;
const same=(a,b)=>a && b && a.message===b.message && a.key===b.key && a.mode===b.mode && a.who===b.who;

export class HintPacer {
  constructor() {this.reset();}
  reset() {this.current=null;this.candidate=null;this.shownAt=0;this.lastUpdate=null;}
  update(hint,now,{immediate=false}={}) {
    if (this.lastUpdate!==null && now<this.lastUpdate) this.reset();
    this.lastUpdate=now;
    hint={who:"",message:"",mode:"",key:"",...hint};
    if (!this.current) return this.show(hint,now);
    if(this.candidate?.immediate && !immediate && this.candidate.hint.key===hint.key) return this.flush(now);
    if (same(hint,this.current)) {this.candidate=null;return null;}
    if (!same(hint,this.candidate?.hint)) this.candidate={hint:{...hint},since:now,immediate};
    return this.flush(now);
  }
  flush(now) {
    if (!this.candidate || now-this.shownAt<MIN_DISPLAY_MS) return null;
    const {hint,since,immediate}=this.candidate;
    if (!immediate && hint.key===this.current.key && now-since<650) return null;
    return this.show(hint,now);
  }
  show(hint,now) {this.current={...hint};this.shownAt=now;this.candidate=null;return {...hint};}
}

export function conciseHint(message,stage) {
  const text=String(message || '').trim();
  if (stage==='ready' && /拍摄|拍好|保持/.test(text)) return '可以拍摄了';
  const defaults={background:'转动手机，让白点靠近白环',framing:'稳住手机，匹配背景大小',distance:'保持坐位或靠点，核对人与远景比例',recenter:'保持倍率，复核中心',position:'对照人物框核对位置',pose:'跟随轮廓，放松调整动作'};
  if (/等待新的取景|正在读取取景|等待较新的识别/.test(text)) return '保持取景，正在识别';
  if (/保持手机平稳.*匹配/.test(text)) return '稳住手机，正在调整倍率';
  if (/白点.*白环|白点.*中心环/.test(text)) return defaults.background;
  if (/定位.*主角|定位.*地标/.test(text)) return '正在准备参考照';
  const first=text.split(/[。；！\n]/)[0];
  if (first.length<=26) return first;
  const clause=first.split(/[，,]/)[0];
  return clause.length<=24 ? clause : defaults[stage] || first.slice(0,25)+'…';
}

// Five readable 3-second rounds, including a repeated cue held steadily on screen.
export class PoseBudget {
  constructor() {this.reset();}
  reset() {this.elapsed=0;this.lastAt=null;this.done=false;}
  tick(now,active) {
    if (!active || this.done) {this.lastAt=null;return this.done;}
    if (this.lastAt!==null) this.elapsed+=Math.min(500,Math.max(0,now-this.lastAt));
    this.lastAt=now;this.done=this.elapsed>=15000;
    return this.done;
  }
  get round() {return Math.min(5,Math.floor(this.elapsed/3000)+1);}
}
