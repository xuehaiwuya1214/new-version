import {drawReference, drawGuide} from "./overlay.js";
import {onDevice, openGuide, analyzeFrame, releaseGuide} from "./transport.js";
import {setupLibrary} from "./library.js";
import {AimMotion} from "./aim.js";
import {HintPacer} from "./hints.js";
import {FramingSequence} from "./framing.js";
import {nativeAndroid, saveNativePhoto, shareNativePhoto} from "./native-export.js";
import {FrameSampler} from "./frame-sampler.js";
import {DemoMarker} from "./demo.js";

const el = id => document.getElementById(id);
const video = el("video"), livePhoto = el("live-photo"), refPhoto = el("reference");
const surface = el("surface"), refSurface = el("ref-surface");
const stages = ["background", "framing", "position", "pose", "ready"];
const stageNames = ["对准", "景别", "站位", "姿势", "拍摄"];
const actors = ["摄影者 · 对准中心", "摄影者 · 调整景别", "模特 · 调整站位", "模特 · 调整姿势", "摄影者 · 拍摄"];
const skipped = new Set();
let shownStage = null;
let session = null, reference = null, referenceFile = null, refUrl = null;
let stream = null, liveUrl = null, source = null, facing = "environment";
let referenceVersion = 0, sourceVersion = 0, generation = 0;
let cameraOpening = null;
let sampler = new FrameSampler();
if (!nativeAndroid) el("guide-mode").value = "live";
const isDemo = () => nativeAndroid && el("guide-mode").value === "demo";
let demoZoomPlayed = false;
const demoMarker = new DemoMarker(el("demo-aim"), el("demo-dot"), () => {
  if (isDemo() && shownStage === "background") advanceDemo();
});
let analysisPaused = false, networkPaused = false, nextAnalysisAt = 0;
let measuredFrames = 0, lastMeasuredFrames = null, lastPreviewMeasure = 0, previewFrameRequest = null;
let prepareMs = 0, requestMs = 0, serverMs = 0;
let samplingPath = "canvas", sampleMainMs = 0, sampleWorkerMs = 0, sampleBytes = 0;
let latestCapturedAt = 0;
let lastAnalysisStarted = 0, completedAnalyses = 0;
const analysisCanvas = document.createElement("canvas");
let seq = 0, pendingTap = null, latest = null, busy = false, shotRequested = false;
let lastAnalyzed = 0, lastVideoTime = -1, lastMessage = "";
let cameraZoom = 1;
let refMirrored = false, manualDirection = false, refTap = null;
let mirrorFrames = 0, autoFlippedSource = -1;
let referenceNote = "";
const aim = new AimMotion();
const hints = new HintPacer();
const framingSequence = new FramingSequence();
let paintedFramingPhase = "idle";
const completed = new Set();
let guideContext = "";
let overlayRequest = 0, overlayWake = 0, lastOverlayPaint = 0;
let overlayPaints = 0, overlayMs = 0, longTaskMs = 0;
if (nativeAndroid && globalThis.PerformanceObserver?.supportedEntryTypes?.includes("longtask")) {
  new PerformanceObserver(list => {for (const entry of list.getEntries()) longTaskMs += entry.duration;})
    .observe({type:"longtask", buffered:false});
}

function paintOverlay(now = performance.now()) {
  const show = el("show-guides").checked;
  const demoAiming = isDemo() && shownStage === "background";
  demoMarker.show(demoAiming && !!reference && !!source && show && !analysisPaused && !document.hidden);
  el("overlay").hidden = analysisPaused || !show || demoAiming;
  if (analysisPaused) {
    el("aim-transition").hidden = el("framing-status").hidden = el("legend").hidden = true;
    return;
  }
  const framingVisual = updateFraming(now);
  const visual = aim.sample(now);
  if (show && !demoAiming) {
    const started = performance.now();
    drawGuide(el("overlay"), latest, reference, shownStage, visual, framingVisual);
    overlayMs += performance.now()-started;
    overlayPaints++;
  }
  el("overlay").dataset.framing = framingVisual.phase;
  const framingStatus = el("framing-status");
  const captions = {outline:"参考背景轮廓", scan:"正在匹配背景景别", zoom:"正在平滑调整倍率", verify:"正在核对调整后的画面"};
  const caption = captions[framingVisual.phase];
  framingStatus.hidden = !show || !caption || !source;
  if (caption && framingStatus.textContent !== caption) framingStatus.textContent = caption;
  el("overlay").dataset.aim = visual.success !== null ? "aligned" : visual.near ? "near" : visual.point ? "tracking" : "search";
  const transition = el("aim-transition");
  const celebrating = show && visual.success !== null && !!source;
  transition.hidden = !celebrating;
  if (celebrating) {
    transition.style.opacity = String(Math.min(1, (1-visual.success)/.2));
    el("aim-next").textContent = {framing:"下一步 · 调整景别", position:"已进入 · 人物站位", pose:"已进入 · 姿势引导", ready:"构图完成 · 可以拍摄"}[shownStage] || "下一步 · 调整景别";
  }
  lastOverlayPaint = now;
}

function animateOverlay(now) {
  overlayRequest = 0;
  if (document.hidden || !reference || !source || analysisPaused) return;
  const ending = !aim.animation(now).active && !["outline", "scan", "zoom"].includes(framingSequence.phase);
  if (ending || now-lastOverlayPaint >= (nativeAndroid ? 66 : 30)) paintOverlay(now);
  scheduleOverlay(now);
}

function cancelOverlay() {
  if (overlayRequest) cancelAnimationFrame(overlayRequest);
  clearTimeout(overlayWake);
  overlayRequest = overlayWake = 0;
}

function scheduleOverlay(now = performance.now()) {
  clearTimeout(overlayWake);
  overlayWake = 0;
  if (document.hidden || !reference || !source || analysisPaused) return;
  const motion = el("show-guides").checked ? aim.animation(now) : {active:false};
  // Zoom still advances with the guide layer hidden. Static rings/outlines do not animate.
  if (["outline", "scan", "zoom"].includes(framingSequence.phase) || motion.active) {
    if (!overlayRequest) overlayRequest = requestAnimationFrame(animateOverlay);
  } else if (motion.wakeAt !== undefined) {
    overlayWake = setTimeout(renderOverlay, Math.max(1,motion.wakeAt-now));
  }
}

function renderOverlay() {
  cancelOverlay();
  paintOverlay();
  scheduleOverlay();
}

function setCameraZoom(value) {
  cameraZoom = value;
  el("zoom").value = String(value);
  el("zoom-value").textContent = value.toFixed(2) + "×";
  video.style.transform = livePhoto.style.transform = "scale(" + value + ")";
  const name = {camera:"相机取景", photo:"照片试拍"}[source] || "尚未取景";
  el("source-state").textContent = value === 1 ? name : name + " · " + value.toFixed(2) + "×";
}

function updateFraming(now = performance.now()) {
  let state = framingSequence.sample(now);
  if (isDemo() && shownStage === "framing" && state.phase === "adjust" && !demoZoomPlayed && el("auto-zoom").checked) {
    demoZoomPlayed = true;
    framingSequence.beginZoom(cameraZoom, Math.min(1.6,cameraZoom*1.12), now);
    state = framingSequence.sample(now);
  }
  if (state.zoom !== null && Math.abs(state.zoom-cameraZoom) > .00001) setCameraZoom(state.zoom);
  if (state.phase === "verify" && paintedFramingPhase === "zoom") {
    if (isDemo()) {
      framingSequence.finishVerification();
      state = framingSequence.sample(now);
      tell("演示 · 调整景别", "演示变焦完成，可拖动倍率微调，再确认景别");
      paintedFramingPhase = state.phase;
      return state;
    }
    // Inference resumes only after the final preview/capture crop has reached its target.
    generation++;
    latest = null;
    lastVideoTime = -1;
    shotRequested = false;
    el("shutter").disabled = true;
    tell("摄影者 · 匹配景别", "倍率已调整，正在核对新的取景画面");
  }
  paintedFramingPhase = state.phase;
  return state;
}

function alignZoom(result) {
  const factor = result.background?.zoomFactor;
  if (result.stage !== "framing" || !factor || skipped.has("framing") || !el("auto-zoom").checked) return false;
  const wanted = Math.max(1, Math.min(1.6, cameraZoom * factor));
  if (Math.abs(wanted - cameraZoom) < .005) {
    if (source === "photo") {
      result.message = factor < 1 ? "这张试拍照取景太近，请换一张更广的照片，或跳过景别"
        : "放大到 1.60× 后地标仍偏小，请换一张取景更近的照片，或跳过景别";
    } else {
      result.message = factor < 1 ? "已到最广的 1×，请后退一些，让地标在画面中变小"
        : "已放大到 1.60×，请向地标靠近一些";
    }
    return false;
  }
  if (!framingSequence.beginZoom(cameraZoom, wanted, performance.now())) return false;
  invalidate("framing", null, true);
  tell("摄影者 · 匹配景别", "保持手机平稳，正在匹配参考景别");
  return true;
}

function tell(who, message, mode = "", paced = false) {
  const selected = hints.update({who, message, mode, key:shownStage || "setup"}, performance.now(), {immediate:!paced});
  if (!selected) return;
  ({who, message, mode} = selected);
  el("who").textContent = who;
  if (message !== lastMessage) {
    el("hint").textContent = message;
    lastMessage = message;
  }
  el("indicator").dataset.mode = mode;
}

function showReferenceGuide() {
  drawReference(el("ref-overlay"), reference, shownStage);
  if (!reference) return;
  let note = reference.subjectHint || (reference.boxes.length > 1
      ? "金色框标出当前主角，点按其他人物可切换参考动作。"
      : "白线是取景器中的参考动作。取景时可点按画面中的主角。");
  if (!shownStage || shownStage === "background") note = "白点锁在同一处景物上，白环固定在镜头中央。缓慢转动手机让两者接近；成功后只需继续下一步。";
  if (shownStage === "framing") note = "背景轮廓固定在参考构图上。轮廓显现后会平滑匹配倍率，也可以拖动滑杆手动调整。";
  if (shownStage === "position") note = "红框固定标出参考人物的位置和大小。先让模特走进框内，再调整动作。";
  if (isDemo()) note = "演示模式：白点按手机转动近似跟随，可点按重新放置；站位和姿势由你手动确认。参考轮廓来自这张照片。";
  el("subject-note").textContent = (refMirrored ? "参考照已水平翻转。" : "") + note;
}

function display(result, pendingStage) {
  latest = result;
  if (result?.completed) {
    completed.clear();
    for (const stage of result.completed) completed.add(stage);
  }
  const previousStage = shownStage;
  shownStage = result?.stage ?? (reference && source ? pendingStage ?? stages.find(s => !skipped.has(s) && !completed.has(s)) : null);
  if (previousStage !== shownStage && shownStage === "framing") demoZoomPlayed = false;
  if (shownStage === "framing" && reference && source) framingSequence.enter(performance.now());
  else {framingSequence.reset(); paintedFramingPhase = "idle";}
  aim.update(result, performance.now());
  renderOverlay();
  if (previousStage !== shownStage) showReferenceGuide();
  const index = stages.indexOf(shownStage);
  [...el("steps").children].forEach((li, i) => {
    const bypassed = skipped.has(stages[i]);
    li.className = bypassed ? "skipped" : i === index ? "active" : i < index ? "done" : "";
    li.setAttribute("aria-label", stageNames[i] + (bypassed ? "，已跳过" : ""));
    if (i === index) li.setAttribute("aria-current", "step");
    else li.removeAttribute("aria-current");
  });
  el("hold").hidden = !result || result.mode !== "hold";
  el("hold").value = result?.hold || 0;
  el("shutter").disabled = !result?.ready || !!shotRequested;
  const skipButton = el("skip-stage");
  skipButton.hidden = index < 0 || shownStage === "ready";
  if (!skipButton.hidden) skipButton.textContent = "跳过" + stageNames[index];
  el("restore-steps").hidden = !skipped.size || !reference;
  el("restart-aim").hidden = !completed.has("background") || !reference || !source;
  const aiming = shownStage === "background";
  const framing = shownStage === "framing";
  const background = aiming || framing;
  const building = framing && reference?.buildingOutline;
  const backgroundShape = framing && reference?.backgroundOutline;
  const standing = shownStage === "position";
  const posing = shownStage === "pose";
  el("legend").hidden = analysisPaused || !el("show-guides").checked || !(background || (standing && reference?.box) ||
    (posing && (reference?.outline || reference?.points.length)));
  el("legend").dataset.stage = shownStage || "";
  el("legend").dataset.background = building || backgroundShape ? "outline" : background ? "rings" : "";
  const poseLabel = {person:"参考动作轮廓", joints:"简化动作轮廓"}[reference?.outline?.kind] || "参考关节";
  el("target-label").textContent = building ? building.name + "参考轮廓" : backgroundShape ? "参考背景轮廓" : framing ? "参考地标" : aiming ? (isDemo() ? "演示落点" : "参考中心") : standing ? "目标站位" : poseLabel;
  el("current-label").textContent = background ? "镜头中心" : standing ? "当前人物" : "实时关节";
  el("current-key").hidden = !(aiming || standing && result?.selected ||
    posing && result?.pose?.actual.some(Boolean));
  el("zoom-controls").hidden = !framing || !source;
  el("scene-thumb").hidden = !background || result?.mode === "hold";
  updateDemoUI();
  if (result) {
    const adjustingScale = framing && ["coverage", "steady", "scale"].includes(result.background?.control);
    tell(adjustingScale ? "摄影者 · 匹配景别" : actors[index], result.message, result.mode, true);
  }
}

function demoFrame(stage) {
  const index = stages.indexOf(stage);
  const messages = {
    background:"点按地标放置白点，缓慢转动手机让它靠近白环；也可手动确认",
    framing:"对照参考轮廓调整画面，景别合适后点击确认",
    position:"请模特站进参考人物框，位置合适后点击确认",
    pose:"请模特照着参考动作摆姿势，完成后点击确认",
    ready:"演示步骤已确认，可以拍摄"
  };
  return {demo:true,stage,ready:stage === "ready",mode:stage === "ready" ? "ready" : "adjust",message:messages[stage],
    boxes:[],selected:null,background:null,pose:null,hold:0,checks:stages.slice(0,4).map((_,i)=>i<index),
    completed:index>0 && !skipped.has("background") ? ["background"] : [],skipped:[...skipped]};
}

function updateDemoUI() {
  const demo = isDemo();
  el("demo-badge").hidden = !demo || !source;
  el("demo-controls").hidden = !demo || !reference || !source;
  el("pause-analysis").hidden = demo;
  if (demo && source !== "camera") el("preview-status").textContent = "演示模式 · 手动确认";
  el("auto-zoom-label").textContent = demo ? "演示变焦" : "自动匹配";
  const captions = {background:"确认对准",framing:"景别已合适",position:"站位已合适",pose:"动作已摆好",ready:"演示完成"};
  el("demo-next").textContent = captions[shownStage] || "确认对准";
  el("demo-next").disabled = shownStage === "ready";
  el("demo-help").textContent = shownStage === "background" ? "白点按手机转动近似跟随；无方向数据时可点按放置并手动确认。"
    : "演示模式：站位和动作由你确认，保留参考轮廓作为提示。";
}

function advanceDemo() {
  if (!isDemo() || !reference || !source || shownStage === "ready") return;
  const next = stages.slice(stages.indexOf(shownStage)+1).find(s=>!skipped.has(s)) || "ready";
  invalidate(next);
}
el("demo-next").onclick = advanceDemo;
el("demo-aim").onclick = event => {if (isDemo()) demoMarker.place(positionIn(event));};

function invalidate(stage, tap = null, preserveFraming = false) {
  generation++;
  mirrorFrames = 0;
  pendingTap = tap;
  shotRequested = false;
  lastVideoTime = -1;
  const context = referenceVersion + ":" + sourceVersion;
  const preserve = context === guideContext;
  if (!preserve || !preserveFraming) {framingSequence.reset(); paintedFramingPhase = "idle";}
  if (!preserve) {completed.clear(); hints.reset(); demoMarker.reset(); demoZoomPlayed = false;}
  guideContext = context;
  aim.reset(reference ? reference.width/reference.height : 1, preserve);
  display(isDemo() && reference && source ? demoFrame(stage || (preserve ? shownStage : null) || stages.find(s=>!skipped.has(s)&&!completed.has(s)) || "ready") : null, stage);
}

function applySteps(nextStage) {
  invalidate(nextStage, pendingTap);
  if (isDemo()) return;
  if (!shownStage) {promptForInput(); return;}
  tell(actors[stages.indexOf(shownStage)], "正在读取取景画面");
  analyze();
}

el("skip-stage").onclick = () => {
  const index = stages.indexOf(shownStage);
  if (index < 0 || shownStage === "ready") return;
  skipped.add(shownStage);
  applySteps(stages.slice(index + 1).find(stage => !skipped.has(stage)));
};

el("restore-steps").onclick = () => {
  ++sourceVersion;
  skipped.clear();
  applySteps("background");
};

el("restart-aim").onclick = () => {
  ++sourceVersion;
  skipped.delete("background");
  applySteps("background");
};

function fit(element, parent, aspect) {
  const w = Math.min(parent.clientWidth, parent.clientHeight * aspect);
  element.style.width = w + "px";
  element.style.height = w / aspect + "px";
}

function layout() {
  const ratio = refPhoto.naturalWidth / refPhoto.naturalHeight;
  if (ratio) fit(refSurface, el("ref-box"), ratio);
  const sourceRatio = source === "camera" ? video.videoWidth / video.videoHeight : livePhoto.naturalWidth / livePhoto.naturalHeight;
  fit(surface, el("viewport"), ratio || sourceRatio || 4/3);
  demoMarker.configure(surface.clientWidth || 1,surface.clientHeight || 1,ratio || sourceRatio || 4/3,facing === "user");
  showReferenceGuide();
  renderOverlay();
}

const resize = new ResizeObserver(layout);
resize.observe(el("viewport"));
resize.observe(el("ref-box"));

function promptForInput() {
  if (!session) tell("准备", "从图库选一张参考照，或打开本地照片");
  else if (!source) tell("摄影者", "开启相机，或载入一张照片试拍");
  else if (isDemo()) display(demoFrame(shownStage || "background"));
  else {
    display(null);
    let message = "正在读取取景画面";
    if (shownStage === "background") message = "先让参考地标入镜，再缓慢转动镜头，让白点靠近中央白环";
    if (shownStage === "framing") message = "对照参考轮廓调整倍率，让建筑的位置和大小接近参考";
    tell(actors[stages.indexOf(shownStage)], message);
  }
}

async function loadReference(file, tap, note) {
  cancelCameraOpening();
  const version = ++referenceVersion;
  const newPhoto = file !== referenceFile;
  if (newPhoto) {
    referenceNote = note || "";
    skipped.clear();
    refMirrored = false;
    manualDirection = false;
    refTap = null;
    autoFlippedSource = -1;
  }
  if (tap !== undefined) refTap = tap;
  releaseGuide(session);
  session = null;
  reference = null;
  setCameraZoom(1);
  invalidate();
  referenceFile = file;
  el("filename").textContent = file.name;
  el("viewpoint").textContent = "";
  const mirrorButton = el("mirror-ref");
  mirrorButton.disabled = true;
  mirrorButton.setAttribute("aria-pressed", String(refMirrored));
  mirrorButton.textContent = refMirrored ? "还原参考照方向" : "水平翻转参考照";
  refPhoto.style.transform = el("scene-thumb").style.transform = refMirrored ? "scaleX(-1)" : "";
  tell("参考照", "正在定位主角和地标");
  try {
    if (newPhoto) {
      if (refUrl) URL.revokeObjectURL(refUrl);
      refUrl = URL.createObjectURL(file);
      refPhoto.src = refUrl;
      el("scene-thumb").src = refUrl;
      await refPhoto.decode();
      if (version !== referenceVersion) return;
    }
    refSurface.hidden = false;
    mirrorButton.hidden = false;
    el("ref-empty").hidden = true;
    layout();
    const form = new FormData();
    form.append("reference", file);
    form.append("mirror", refMirrored ? "1" : "0");
    form.append("viewpoint", referenceNote);
    if (refTap) {form.append("x", refTap[0]); form.append("y", refTap[1]);}
    const data = await openGuide(form, refPhoto, message => {
      if (version === referenceVersion) tell("参考照", message);
    });
    if (version !== referenceVersion) {
      releaseGuide(data.id);
      return;
    }
    session = data.id;
    reference = data.reference;
    aim.reset(reference.width/reference.height);
    document.body.dataset.reference = "loaded";
    el("viewpoint").textContent = reference.viewpoint;
    layout();
    promptForInput();
  } catch (error) {
    if (version === referenceVersion) {
      document.body.dataset.reference = "empty";
      tell("参考照", error.name === "EncodingError" ? "无法打开参考照，请使用 JPG、PNG 或 WebP" : error.message, "search");
    }
  } finally {
    if (version === referenceVersion) mirrorButton.disabled = false;
  }
}

function flipReference(manual) {
  if (!referenceFile) return;
  if (manual) manualDirection = true;
  else autoFlippedSource = sourceVersion;
  refMirrored = !refMirrored;
  const box = reference?.boxes.length > 1 ? reference.box : null;
  const selected = refTap || (box && [(box[0]+box[2])/2, (box[1]+box[3])/2]);
  loadReference(referenceFile, selected ? [1-selected[0], selected[1]] : null);
}

function positionIn(event) {
  const rect = event.currentTarget.getBoundingClientRect();
  return [(event.clientX - rect.left) / rect.width, (event.clientY - rect.top) / rect.height];
}
el("choose-ref").onclick = () => {cancelCameraOpening(); cancelLibrarySelection(); el("ref-file").click();};
el("mirror-ref").onclick = () => flipReference(true);
el("ref-file").onchange = e => {
  const file = e.target.files[0];
  if (file) {cancelLibrarySelection(); loadReference(file);}
  e.target.value = "";
};
el("ref-overlay").onclick = e => {
  if (referenceFile) loadReference(referenceFile, positionIn(e));
};
el("overlay").onclick = e => {
  if (isDemo()) return;
  if (!session || !source) return;
  if (shownStage === "background" || shownStage === "framing") return;
  invalidate();
  pendingTap = positionIn(e);
  tell("主角", "正在确认点选的人物");
};

el("zoom").oninput = e => {
  if (!reference || !source) return;
  el("auto-zoom").checked = false;
  demoZoomPlayed = true;
  framingSequence.cancelZoom();
  setCameraZoom(Number(e.target.value));
  invalidate("framing", null, true);
  tell("摄影者 · 调整景别", isDemo() ? "倍率已调整，景别合适后点击确认" : "倍率已调整，正在核对建筑轮廓");
};
el("auto-zoom").onchange = () => {
  if (isDemo()) demoZoomPlayed = !el("auto-zoom").checked;
  framingSequence.cancelZoom();
  invalidate("framing", null, true);
  tell("摄影者 · 调整景别", isDemo() ? "演示倍率可随时手动调整" : el("auto-zoom").checked ? "将根据新的取景画面自动匹配倍率" : "已切换为手动调整倍率");
};
const cancelLibrarySelection = setupLibrary((file, note) => loadReference(file, undefined, note), onDevice);
window.addEventListener("campus-server-changed", () => {
  if (networkPaused) setAnalysisPaused(false);
  if (referenceFile) loadReference(referenceFile, undefined, referenceNote);
});

function stopTracks() {
  if (previewFrameRequest !== null) video.cancelVideoFrameCallback?.(previewFrameRequest);
  previewFrameRequest = null;
  if (stream) for (const track of stream.getTracks()) track.stop();
  stream = null;
  video.srcObject = null;
}

function updateCameraControls() {
  const opening = cameraOpening !== null;
  const canCancel = opening && cameraOpening === sourceVersion;
  el("camera").textContent = canCancel ? "取消开启" : opening ? "等待相机授权" : source === "camera" ? "关闭相机" : "开启相机";
  el("camera").disabled = opening && !canCancel;
  el("flip").disabled = opening;
}

function cancelCameraOpening() {
  if (cameraOpening === null || cameraOpening !== sourceVersion) return;
  ++sourceVersion;
  stopTracks();
  showSource(null);
  // Keep the lock until getUserMedia settles; its permission request cannot be aborted.
}

function showSource(kind) {
  source = kind;
  if (nativeAndroid) {
    nextAnalysisAt = performance.now() + (kind === "camera" ? 1500 : 0);
    window.dispatchEvent(new CustomEvent("campus-camera-activity", {detail:{active:kind === "camera"}}));
  }
  setCameraZoom(1);
  invalidate();
  surface.hidden = !kind;
  el("empty").hidden = !!kind;
  video.hidden = kind !== "camera";
  livePhoto.hidden = kind !== "photo";
  updateCameraControls();
  el("flip").hidden = kind !== "camera";
  layout();
  promptForInput();
}

async function startCamera() {
  if (cameraOpening !== null) return;
  if (isDemo()) demoMarker.requestSensors();
  if (!navigator.mediaDevices?.getUserMedia) {
    tell("相机", "请在 localhost 或 HTTPS 页面开启相机，也可以用照片试拍", "search");
    return;
  }
  const version = ++sourceVersion;
  cameraOpening = version;
  stopTracks();
  showSource(null);
  if (nativeAndroid) window.dispatchEvent(new CustomEvent("campus-camera-activity", {detail:{active:true}}));
  tell("相机", "正在开启相机");
  try {
    const height = nativeAndroid ? Number(el("preview-quality").value) : 1080;
    const acquired = await navigator.mediaDevices.getUserMedia({
      audio:false, video:{facingMode:{ideal:facing}, width:{ideal:height*16/9}, height:{ideal:height}, frameRate:{ideal:30, max:30}}
    });
    if (version !== sourceVersion) {acquired.getTracks().forEach(t => t.stop()); return;}
    stream = acquired;
    video.srcObject = acquired;
    await video.play();
    if (version !== sourceVersion) return;
    const track = acquired.getVideoTracks()[0];
    track.onended = () => {
      if (stream !== acquired) return;
      stopTracks(); showSource(null); tell("相机", "相机已断开，可以重新开启", "search");
    };
    showSource("camera");
  } catch (error) {
    if (version !== sourceVersion) return;
    stopTracks(); showSource(null);
    tell("相机", error.name === "NotAllowedError" ? (nativeAndroid ? "请在应用权限中允许相机，或用照片试拍" : "请允许浏览器使用摄像头，或用照片试拍") : "未能开启摄像头，可重新连接或用照片试拍", "search");
  } finally {
    cameraOpening = null;
    updateCameraControls();
  }
}

el("camera").onclick = () => {
  if (cameraOpening !== null) {cancelCameraOpening(); return;}
  if (!stream) {startCamera(); return;}
  ++sourceVersion;
  stopTracks(); showSource(null);
};
el("flip").onclick = () => {
  if (cameraOpening !== null) return;
  facing = facing === "environment" ? "user" : "environment";
  startCamera();
};
el("choose-live").onclick = () => {cancelCameraOpening(); el("live-file").click();};
el("live-file").onchange = async e => {
  const file = e.target.files[0];
  e.target.value = "";
  if (!file) return;
  const version = ++sourceVersion;
  stopTracks(); showSource(null);
  if (liveUrl) URL.revokeObjectURL(liveUrl);
  liveUrl = URL.createObjectURL(file);
  livePhoto.src = liveUrl;
  try {
    await livePhoto.decode();
    if (version === sourceVersion) showSource("photo");
  } catch {
    if (version === sourceVersion) tell("照片试拍", "无法打开试拍照片，请使用 JPG、PNG 或 WebP", "search");
  }
};

function snapshot(maxEdge, reusable = null) {
  const input = source === "camera" ? video : livePhoto;
  const w = input.videoWidth || input.naturalWidth, h = input.videoHeight || input.naturalHeight;
  const ratio = reference.width / reference.height;
  const cw = Math.min(w, h * ratio) / cameraZoom, ch = cw / ratio;
  const scale = Math.min(1, maxEdge / Math.max(cw, ch));
  const canvas = reusable || document.createElement("canvas");
  const width = Math.max(1, Math.round(cw * scale)), height = Math.max(1, Math.round(ch * scale));
  if (canvas.width !== width) canvas.width = width;
  if (canvas.height !== height) canvas.height = height;
  canvas.getContext("2d").drawImage(input, (w-cw)/2, (h-ch)/2, cw, ch, 0, 0, canvas.width, canvas.height);
  return canvas;
}

function jpeg(canvas, quality) {
  return new Promise((resolve, reject) => canvas.toBlob(blob => blob ? resolve(blob) : reject(new Error("无法读取取景画面")), "image/jpeg", quality));
}

function sampledShot(frozen) {
  // Verify the exact shutter image at the same resolution as ordinary phone samples.
  // Changing analysis size here can invalidate optical flow and withdraw the ready state.
  const scale = Math.min(1, 640/Math.max(frozen.width, frozen.height));
  analysisCanvas.width = Math.max(1, Math.round(frozen.width*scale));
  analysisCanvas.height = Math.max(1, Math.round(frozen.height*scale));
  analysisCanvas.getContext("2d").drawImage(frozen,0,0,analysisCanvas.width,analysisCanvas.height);
  return analysisCanvas;
}

async function analyze() {
  if (isDemo() || busy || !session || !source || document.hidden || analysisPaused) return;
  if (nativeAndroid && !shotRequested && !pendingTap && performance.now() < nextAnalysisAt) return;
  if (updateFraming().blockAnalysis) return;
  if (!shotRequested && source === "camera" && (video.readyState < 2 || video.currentTime === lastVideoTime)) return;
  if (source === "photo" && latest?.ready && !shotRequested && !pendingTap && performance.now() - lastAnalyzed < 650) return;
  const version = generation, id = session, tap = pendingTap, taking = shotRequested;
  shotRequested = false;
  busy = true;
  lastAnalysisStarted = performance.now();
  if (nativeAndroid) nextAnalysisAt = lastAnalysisStarted + 1000;
  if (source === "camera") lastVideoTime = video.currentTime;
  try {
    // 快门请求分析和下载使用同一次采样，辅助线画在独立的 canvas 上。
    const prepared = nativeAndroid && source === "camera" && !taking
      ? await sampler.capture(video, reference.width/reference.height, cameraZoom) : null;
    const blob = prepared?.blob || await jpeg(taking && nativeAndroid ? sampledShot(taking)
      : taking || snapshot(nativeAndroid ? 640 : 960, analysisCanvas), nativeAndroid ? .75 : taking ? .94 : .9);
    prepareMs = performance.now()-lastAnalysisStarted;
    samplingPath = prepared?.path || "canvas";
    sampleMainMs = prepared?.mainMs ?? prepareMs;
    sampleWorkerMs = prepared?.workerMs ?? 0;
    sampleBytes = blob.size;
    if (version !== generation) return;
    const query = new URLSearchParams({seq:++seq, source:sourceVersion, skip:[...skipped].join(",")});
    if (tap) {query.set("x", tap[0]); query.set("y", tap[1]);}
    const requestStarted = performance.now();
    const result = await analyzeFrame(id, query, blob, prepared?.base64);
    requestMs = performance.now()-requestStarted;
    serverMs = result.timing?.serverMs || 0;
    completedAnalyses++;
    if (version !== generation) return;
    if (nativeAndroid && source === "camera" && !taking && performance.now()-lastAnalysisStarted > 3500) {
      // Never steer the camera or enable capture using a result from several seconds ago.
      nextAnalysisAt = performance.now()+500;
      display(null); tell("相机", "预览继续，正在等待较新的识别结果", "search");
      return;
    }
    pendingTap = null;
    lastAnalyzed = performance.now();
    latestCapturedAt = lastAnalysisStarted;
    framingSequence.finishVerification();
    mirrorFrames = ["background", "framing"].includes(result.stage) && result.background?.control === "mirror" ? mirrorFrames + 1 : 0;
    if (mirrorFrames >= 2 && !taking && !manualDirection && autoFlippedSource !== sourceVersion) {
      flipReference(false);
      return;
    }
    if (!taking && alignZoom(result)) return;
    display(result);
    if (taking && result.ready) {
      const photo = nativeAndroid ? await jpeg(taking, .94) : blob;
      if (version === generation) exportShot(photo);
    }
  } catch (error) {
    if (nativeAndroid) nextAnalysisAt = performance.now()+1500;
    if (version === generation) {
      if (nativeAndroid && error.code === "SERVER_UNREACHABLE") {
        setAnalysisPaused(true, true);
        const status = el("computer-status");
        if (status) status.textContent = "电脑连接已中断，请重新连接；相机预览继续";
        return;
      }
      if (error.status === 404) {loadReference(referenceFile); return;}
      display(null); tell("取景", error.message, "search");
    }
  } finally {
    busy = false;
  }
}

let lastShotUrl, lastShotFile;
function exportShot(blob) {
  if (lastShotUrl) URL.revokeObjectURL(lastShotUrl);
  lastShotUrl = URL.createObjectURL(blob);
  lastShotFile = new File([blob], "校园人像-" + Date.now() + ".jpg", {type:"image/jpeg"});
  const link = el("download-shot");
  link.href = lastShotUrl;
  link.download = lastShotFile.name;
  if (onDevice || nativeAndroid) {
    el("last-shot").src = lastShotUrl;
    el("export").hidden = false;
    el("share-shot").hidden = !nativeAndroid && !navigator.canShare?.({files:[lastShotFile]});
    if (nativeAndroid) {
      el("share-shot").textContent = "分享照片";
      link.textContent = "保存到相册";
    }
    tell("摄影者 · 拍摄", "已拍好，可保存下方的照片");
  } else {
    link.click();
    tell("摄影者 · 拍摄", "照片已保存");
  }
}
el("share-shot").onclick = async () => {
  try {
    if (nativeAndroid) await shareNativePhoto(lastShotFile);
    else await navigator.share({files:[lastShotFile]});
  }
  catch (error) {
    if (error.name !== "AbortError") tell("保存照片", nativeAndroid ? (error.message || "无法分享，请先保存到相册") : "可以点击下载 JPEG，或长按刚拍摄的照片保存");
  }
};

let savingNative = false;
el("download-shot").onclick = async event => {
  if (!nativeAndroid) return;
  event.preventDefault();
  if (savingNative || !lastShotFile) return;
  savingNative = true;
  try {
    await saveNativePhoto(lastShotFile);
    tell("保存照片", "已保存到相册 · CampusPhotoGuide");
  } catch (error) {tell("保存照片", error.message || "保存失败，请重试", "error");}
  finally {savingNative = false;}
};

el("shutter").onclick = async () => {
  if (!latest?.ready || shotRequested) return;
  if (nativeAndroid && !isDemo() && performance.now()-latestCapturedAt > 3500) {
    display(null); tell("相机", "等待新的取景画面", "search");
    return;
  }
  shotRequested = snapshot(Infinity);
  el("shutter").disabled = true;
  if (isDemo()) {
    const version = generation, frozen = shotRequested;
    try {
      const blob = await jpeg(frozen,.94);
      if (version === generation) exportShot(blob);
    } catch (error) {if (version === generation) tell("拍摄",error.message,"error");}
    finally {if (version === generation) {shotRequested=false;el("shutter").disabled=!latest?.ready;}}
    return;
  }
  analyze();
};

el("preview-controls").hidden = !nativeAndroid;
el("guide-mode").onchange = () => {
  generation++;
  analysisPaused = networkPaused = false;
  el("pause-analysis").textContent = "暂停识别";
  el("pause-analysis").setAttribute("aria-pressed","false");
  sampler.dispose();sampler = new FrameSampler();
  skipped.clear();completed.clear();demoMarker.reset();
  updateDemoUI();
  // Reopen the stream to release any textures/readback work left by live sampling.
  if (source === "camera") startCamera();
  else {++sourceVersion;invalidate();promptForInput();}
};
updateDemoUI();
function setAnalysisPaused(paused, disconnected = false) {
  analysisPaused = paused;
  networkPaused = paused && disconnected;
  el("pause-analysis").textContent = analysisPaused ? "继续识别" : "暂停识别";
  el("pause-analysis").setAttribute("aria-pressed", String(analysisPaused));
  el("preview-status").textContent = analysisPaused ? "识别已暂停，预览继续" : "识别已恢复，最多每秒一次";
  framingSequence.cancelZoom();
  invalidate(undefined, null, true);
  nextAnalysisAt = performance.now()+300;
  tell("相机", disconnected ? "电脑连接已中断，已暂停导拍；相机预览继续" : analysisPaused ? "识别已暂停，当前只显示相机预览" : "识别已恢复，预览优先", "search");
}
el("pause-analysis").onclick = () => setAnalysisPaused(!analysisPaused);
el("show-guides").onchange = () => {
  el("legend").hidden = !el("show-guides").checked || analysisPaused || !source || !reference || shownStage === "ready";
  renderOverlay();
};
el("preview-quality").onchange = () => {if (source === "camera") startCamera();};
video.addEventListener("resize", layout);
function countPreviewFrames(_time, info) {
  previewFrameRequest = null;
  measuredFrames = info.presentedFrames;
  if (lastMeasuredFrames === null) {lastMeasuredFrames = measuredFrames; lastPreviewMeasure = performance.now();}
  if (stream) previewFrameRequest = video.requestVideoFrameCallback(countPreviewFrames);
}
video.addEventListener("playing", () => {
  if (previewFrameRequest !== null) video.cancelVideoFrameCallback?.(previewFrameRequest);
  measuredFrames = 0; lastMeasuredFrames = null;
  lastPreviewMeasure = performance.now();
  if (nativeAndroid && video.requestVideoFrameCallback) previewFrameRequest = video.requestVideoFrameCallback(countPreviewFrames);
});
if (nativeAndroid) setInterval(() => {
  if (source !== "camera" || document.hidden) return;
  const now = performance.now();
  const quality = video.getVideoPlaybackQuality?.();
  const frames = video.requestVideoFrameCallback ? measuredFrames : quality ? quality.totalVideoFrames-quality.droppedVideoFrames : null;
  const fps = frames === null || lastMeasuredFrames === null ? null : Math.max(0,(frames-lastMeasuredFrames)*1000/Math.max(1,now-lastPreviewMeasure));
  lastMeasuredFrames = frames || 0;
  lastPreviewMeasure = now;
  el("preview-status").textContent = (fps === null ? "本地预览" : "预览约 " + Math.round(fps) + " 帧/秒") +
    (isDemo() ? " · 演示模式 · 手动确认" : analysisPaused ? " · 识别已暂停" : " · 识别最多每秒一次");
  window.__guidePerformance = {previewFps:fps,prepareMs,requestMs,serverMs,completedAnalyses,worker:!!sampler.worker,busy,analysisPaused,
    samplingPath,sampleMainMs,sampleWorkerMs,sampleBytes,
    cameraSettings:stream?.getVideoTracks()[0]?.getSettings(),
    guidesVisible:el("show-guides").checked,overlayPaints,overlayMs,longTaskMs,mode:isDemo()?"demo":"live"};
  const details = el("performance-details");
  details.hidden = false;
  if (details.open) {
    const settings = window.__guidePerformance.cameraSettings || {};
    el("performance-values").textContent = `相机 ${settings.width || "?"}×${settings.height || "?"} · 设备报告 ${Math.round(settings.frameRate || 0)} 帧/秒\n` +
      `最近一次取样 ${Math.round(prepareMs)} ms（前端准备 ${Math.round(sampleMainMs)} ms，后台处理 ${Math.round(sampleWorkerMs)} ms）\n` +
      `请求往返 ${Math.round(requestMs)} ms · 电脑处理 ${Math.round(serverMs)} ms · 图片 ${(sampleBytes/1024).toFixed(0)} KB\n` +
      `处理路径 ${samplingPath} · 本秒界面长任务 ${Math.round(longTaskMs)} ms\n` +
      "往返减去电脑处理仍包含桥接、上传和下载，不等于纯 Wi-Fi 延迟。";
  }
  overlayPaints = overlayMs = longTaskMs = 0;
}, 1000);

setInterval(() => {
  if (isDemo()) return;
  // Expire the previous result even while the next request is still running.
  // A response received recently may itself describe an old camera frame.
  if (nativeAndroid && source === "camera" && latest && performance.now()-latestCapturedAt > 3500) {
    display(null); tell("相机", "等待新的取景画面", "search");
  }
  if (source === "camera" && latest && !busy && !framingSequence.sample(performance.now()).blockAnalysis && performance.now() - lastAnalyzed > 2500) {
    display(null);
    tell("相机", "等待新的取景画面", "search");
  }
  analyze();
}, 110);

document.addEventListener("visibilitychange", () => {
  if (document.hidden) {cancelOverlay(); cancelCameraOpening(); framingSequence.cancelZoom(); invalidate(undefined, null, true);}
  else if (source === "camera" && stream && cameraOpening === null) {
    video.play().catch(() => tell("相机", "预览已暂停，请关闭后重新开启相机", "search"));
    renderOverlay();
  }
});
window.addEventListener("pagehide", () => {demoMarker.dispose();cancelOverlay(); sampler.dispose(); cancelCameraOpening(); releaseGuide(session); stopTracks();});
window.addEventListener("pageshow", e => {if (e.persisted) location.reload();});

