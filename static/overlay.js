const gold = "#ffcf69";
const mint = "#53f2c8";
const white = "#fffdf8";
const cyan = "#81e5ff";
const sand = "#e3c79e";
const staticLayers = new WeakMap();
const nativePreview = !!document.querySelector('meta[name="guide-native"][content="android"]');

function drawing(canvas) {
  const w = canvas.clientWidth, h = canvas.clientHeight;
  if (!w || !h) return null;
  // Camera pixels keep their original resolution; only the transparent guide layer is cheaper.
  const dpr = Math.min(window.devicePixelRatio || 1, nativePreview && canvas.id === "overlay" ? 1 : 2);
  if (canvas.width !== Math.round(w*dpr) || canvas.height !== Math.round(h*dpr)) {
    canvas.width = Math.round(w * dpr);
    canvas.height = Math.round(h * dpr);
  }
  const ctx = canvas.getContext("2d");
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.lineCap = ctx.lineJoin = "round";
  return {ctx, canvas, dpr, w, h, point: ([x, y]) => [x * w, y * h]};
}

function staticLayer(d, key, kind, paint) {
  let cached = staticLayers.get(d.canvas);
  if (!cached || cached.key !== key || cached.kind !== kind || cached.w !== d.canvas.width || cached.h !== d.canvas.height) {
    const layer = document.createElement("canvas");
    layer.width = d.canvas.width; layer.height = d.canvas.height;
    const ctx = layer.getContext("2d");
    ctx.setTransform(d.dpr,0,0,d.dpr,0,0);
    ctx.lineCap = ctx.lineJoin = "round";
    paint({...d,ctx});
    cached = {key,kind,w:layer.width,h:layer.height,layer};
    staticLayers.set(d.canvas,cached);
  }
  d.ctx.drawImage(cached.layer,0,0,d.w,d.h);
}
function path(d, points, color, width = 2, dash = []) {
  if (!points.length) return;
  const c = d.ctx;
  c.strokeStyle = color;
  c.lineWidth = width;
  c.setLineDash(dash);
  c.beginPath();
  c.moveTo(...d.point(points[0]));
  for (const p of points.slice(1)) c.lineTo(...d.point(p));
  c.stroke();
  c.setLineDash([]);
}

function circle(d, p, radius, color, filled = false) {
  const c = d.ctx;
  c.beginPath();
  c.arc(...d.point(p), radius, 0, Math.PI * 2);
  if (filled) {
    c.fillStyle = color;
    c.fill();
  } else {
    c.strokeStyle = color;
    c.lineWidth = 2.4;
    c.stroke();
  }
}

function corners(d, box, color) {
  const [x, y, r, b] = box;
  const dx = Math.min((r - x) / 4, 12 / d.w), dy = Math.min((b - y) / 4, 12 / d.h);
  for (const [a, v, sx, sy] of [[x,y,1,1], [r,y,-1,1], [x,b,1,-1], [r,b,-1,-1]]) {
    path(d, [[a + dx*sx,v], [a,v], [a,v + dy*sy]], color, 2);
  }
}

function arrow(d, from, to, color = gold) {
  const [x, y] = d.point(from), [r, b] = d.point(to);
  const len = Math.hypot(r - x, b - y);
  if (len < 10) return;
  const angle = Math.atan2(b - y, r - x), head = Math.min(11, len / 3);
  path(d, [from, to], color, 2);
  const c = d.ctx;
  c.beginPath(); c.moveTo(r, b);
  for (const turn of [-.5, .5]) c.lineTo(r - head*Math.cos(angle + turn), b - head*Math.sin(angle + turn));
  c.closePath(); c.fillStyle = color; c.fill();
}

function referenceJoints(d, points, edges) {
  const visible = points.filter(Boolean);
  if (!visible.length) return;
  const xs = visible.map(p => p[0]), ys = visible.map(p => p[1]);
  const span = Math.max((Math.max(...xs)-Math.min(...xs))*d.w, (Math.max(...ys)-Math.min(...ys))*d.h);
  const stroke = Math.max(1, Math.min(2.2, span*.012));
  for (const [a, b] of edges) {
    if (points[a] && points[b]) path(d, [points[a], points[b]], white, stroke, [stroke*2.6, stroke*1.9]);
  }
  for (const point of visible) circle(d, point, Math.max(1, stroke*1.45), white, span < 95);
}

function standingBox(d, box) {
  const [x, y, r, b] = box;
  path(d, [[x,y], [r,y], [r,b], [x,b], [x,y]], sand, 2);
}

function poseOutline(d, outline) {
  d.ctx.save();
  d.ctx.beginPath();
  for (const contour of outline.contours) {
    if (!contour.length) continue;
    d.ctx.moveTo(...d.point(contour[0]));
    for (const p of contour.slice(1)) d.ctx.lineTo(...d.point(p));
    d.ctx.closePath();
  }
  d.ctx.fillStyle = "#fffdf822";
  d.ctx.fill("evenodd");
  d.ctx.shadowColor = "#142322cc";
  d.ctx.shadowBlur = 3;
  for (const contour of outline.contours) path(d, [...contour, contour[0]], white, 2);
  for (const stroke of outline.details) path(d, stroke, "#fffdf8cc", 1.2);
  d.ctx.restore();
}

function buildingOutline(d, outline) {
  // 只在景别步骤出现，固定在参考构图上，不能跟着实时建筑一起移动。
  for (const stroke of outline.paths) {
    path(d, stroke, "#18251fdf", 3.8);
    path(d, stroke, cyan, 1.8);
  }
  for (const stroke of outline.details || []) path(d, stroke, "#81e5ffbd", 1.1);
}

function framingOutline(d, ref, background, visual) {
  const c = d.ctx;
  c.save();
  const reveal = visual?.reveal ?? 1;
  // The whole reference silhouette is useful immediately, before zoom starts.
  c.globalAlpha = .35 + .65*Math.min(1,reveal*3);
  if (ref.buildingOutline || ref.backgroundOutline?.paths?.length) staticLayer(d,ref,"background", layer => {
    for (const stroke of ref.backgroundOutline?.paths || []) {
      path(layer, stroke, "#142c2999", 2.6);
      path(layer, stroke, ref.buildingOutline ? "#b5edfa99" : "#b5edfadd", 1.2);
    }
    if (ref.buildingOutline) buildingOutline(layer, ref.buildingOutline);
  });
  else
    landmarkMarkers(d, ref.landmarks, background?.tracking ? background.anchors : []);
  c.restore();
  if (visual?.scan != null && !visual.prism) {
    const y = visual.scan*d.h;
    c.save();
    const glow = c.createLinearGradient(0, y-32, 0, y+8);
    glow.addColorStop(0, "#81e5ff00");
    glow.addColorStop(.8, "#81e5ff38");
    glow.addColorStop(1, "#81e5ff00");
    c.fillStyle = glow; c.fillRect(0, y-32, d.w, 40);
    path(d, [[0,visual.scan], [1,visual.scan]], "#c4f8ffb0", 1.5);
    c.restore();
  }
}

function landmarkMarkers(d, landmarks = [], anchors = []) {
  const ctx = d.ctx;
  ctx.save();
  ctx.shadowColor = "#10211d";
  ctx.shadowBlur = 3;
  ctx.font = "600 11px system-ui, sans-serif";
  for (const [id, p] of landmarks.entries()) {
    const live = anchors.find(a => a.id === id)?.current;
    if (live && live.every(v => v >= 0 && v <= 1)) {
      path(d, [p, live], "#e0f0ffab", 1, [3, 5]);
      circle(d, live, 4, "#233538", true);
      circle(d, live, 2.8, "#b6ddff", true);
    }
    const [x, y] = d.point(p);
    const a = 4 / d.w, b = 4 / d.h;
    const diamond = [[p[0]-a,p[1]], [p[0],p[1]-b], [p[0]+a,p[1]], [p[0],p[1]+b], [p[0]-a,p[1]]];
    path(d, diamond, "#20322bdb", 3);
    path(d, diamond, "#fffdf8", 1.5);
    ctx.strokeStyle = "#20322b";
    ctx.lineWidth = 2.6;
    ctx.strokeText(String(id+1), x+6, y-3);
    ctx.fillStyle = "#fffdf8";
    ctx.fillText(String(id+1), x+6, y-3);
  }
  ctx.restore();
}

function backgroundRings(d, background, aim) {
  const target = [.5, .5];
  const radius = Math.max(10, Math.min(17, .028 * Math.min(d.w, d.h)));
  const current = aim ? aim.point : background?.tracking ? background.current : null;
  d.ctx.save();
  d.ctx.shadowColor = "#10211dc9";
  d.ctx.shadowBlur = 3;
  if (current) {
    d.ctx.globalAlpha = aim?.opacity ?? 1;
    const [dx, dy] = [current[0] - target[0], current[1] - target[1]];
    if (current.every(v => v >= 0 && v <= 1)) {
      if (Math.hypot(dx*d.w, dy*d.h) > radius*2) {
        path(d, [target, current], "#ffffff50", 1, [3, 6]);
      }
      circle(d, current, Math.max(5, radius * .48), white, true);
    } else {
      // 参考中心落在画外时，只指向它所在的一侧。
      const t = Math.min((.5 - 12/d.w) / Math.abs(dx), (.5 - 12/d.h) / Math.abs(dy));
      const end = [target[0] + t*dx, target[1] + t*dy];
      const length = Math.hypot(dx*d.w, dy*d.h);
      arrow(d, [end[0] - 26*dx/length, end[1] - 26*dy/length], end, white);
    }
  }
  d.ctx.globalAlpha = 1;
  circle(d, target, radius, white);
  if (aim?.near) {
    d.ctx.shadowColor = white;
    d.ctx.shadowBlur = 10;
    circle(d, target, radius+3, "#ffffff42");
    const [x, y] = d.point(target);
    d.ctx.beginPath();
    d.ctx.arc(x, y, radius+3, -Math.PI/2, -Math.PI/2+Math.PI*2*Math.max(.035, aim.progress));
    d.ctx.lineWidth = 3;
    d.ctx.strokeStyle = white;
    d.ctx.stroke();
  }
  d.ctx.restore();
}

function alignedPulse(d, progress) {
  if (progress == null) return;
  const c = d.ctx, target = [.5, .5];
  const radius = Math.max(19, Math.min(28, .055*Math.min(d.w, d.h)));
  const pulse = Math.sin(Math.min(1, progress/.42)*Math.PI);
  c.save();
  c.globalAlpha = Math.min(1, (1-progress)/.35);
  c.shadowColor = gold;
  c.shadowBlur = 18 + 12*pulse;
  circle(d, target, radius*(1+.22*pulse), gold);
  circle(d, target, radius*(.48+.10*pulse), gold, true);
  c.shadowBlur = 2;
  c.shadowColor = "#142322";
  c.font = "600 13px system-ui, sans-serif";
  c.textAlign = "center";
  c.fillStyle = white;
  c.restore();
}

export function drawReference(canvas, ref, stage) {
  const d = drawing(canvas);
  if (!d || !ref) return;
  if (stage === "distance") {
    if (ref.farBounds) corners(d, ref.farBounds, cyan);
    if (ref.box) corners(d, ref.box, gold);
    return;
  }
  if (!stage || stage === "background" || stage === "recenter") {
    d.ctx.shadowColor = "#10211d";
    d.ctx.shadowBlur = 4;
    circle(d, ref.aimPoint || [.5, .5], Math.max(5, Math.min(d.w, d.h)*.024), white, true);
    d.ctx.shadowBlur = 0;
    return;
  }
  if (stage === "framing") {
    framingOutline(d, ref, null, null);
    return;
  }
  if (stage === "position") {
    if (ref.box) standingBox(d, ref.box);
    return;
  }
  for (const box of ref.boxes) corners(d, box, "#ffffff9c");
  if (ref.box) corners(d, ref.box, gold);
  if (ref.outline) staticLayer(d,ref.outline,"pose", layer => poseOutline(layer, ref.outline));
  else referenceJoints(d, ref.points, ref.edges);
}

function drawStage(d, result, ref, stage, aim, framing) {
  if (stage === "recenter") {
    backgroundRings(d, result?.background, null);
    return;
  }
  if (stage === "distance") {
    if (result?.selected) corners(d, result.selected, mint);
    return;
  }
  if (stage === "background") {
    backgroundRings(d, result?.background, aim);
    return;
  }
  if (stage === "framing") {
    framingOutline(d, ref, result?.background, framing);
    return;
  }
  if (result && result.boxes.length > 1) {
    for (const box of result.boxes) corners(d, box, "#ffffff80");
  }
  if (stage === "position" && ref.box) standingBox(d, ref.box);
  if (result?.selected && (stage === "position" || stage === "ready" || result.boxes.length > 1))
    corners(d, result.selected, mint);
  if (stage === "position" && result?.position?.basis && !result.position.ok) {
    circle(d, result.position.target, 4, sand + "b3");
    circle(d, result.position.current, 2, mint + "b3", true);
  }
  if (stage !== "pose") return;
  const pose = result?.pose;
  d.ctx.save();
  if (pose?.transform) {
    const {scale, offset} = pose.transform;
    d.ctx.translate(offset[0] * d.w, offset[1] * d.h);
    d.ctx.scale(scale, scale);
  }
  if (ref.outline) staticLayer(d,ref.outline,"pose", layer => poseOutline(layer, ref.outline));
  else referenceJoints(d, ref.points, ref.edges);
  d.ctx.restore();
  if (!pose) return;
  for (const p of pose.actual) {
    if (p) circle(d, p, 2, "#53f2c8c9", true);
  }
  const goal = (pose.target || ref.points)[pose.joint];
  if (!goal) return;
  circle(d, goal, 7, white);
  const current = pose.actual[pose.joint];
  if (current) {
    circle(d, current, 3.5, mint, true);
    arrow(d, current, goal, mint);
  }
}

export function drawGuide(canvas, result, ref, stage = result?.stage, aim = null, framing = null) {
  const d = drawing(canvas);
  if (!d || !ref) return;
  drawStage(d, result, ref, stage, aim, framing);
  if (!framing?.prism) alignedPulse(d, aim?.success);
}

