import {prepareOffline} from "./offline.js";

let worker, nextId = 0, notes;
const pending = new Map();
function request(operation, payload, progress) {
  if (!worker) {
    worker = new Worker(new URL("./worker.js", import.meta.url), {type:"module"});
    worker.onmessage = ({data}) => {
      const task = pending.get(data.id);
      if (!task) return;
      if (data.status) {task.progress?.(data.status); return;}
      pending.delete(data.id);
      if (data.error) task.reject(new Error(data.error));
      else task.resolve(data.result);
    };
    worker.onerror = () => {
      for (const task of pending.values()) task.reject(new Error("识别引擎未能启动，请用新版 Chrome 或 Safari 重新打开"));
      pending.clear();
      worker.terminate();
      worker = null;
    };
  }
  return new Promise((resolve, reject) => {
    const id = ++nextId;
    pending.set(id, {resolve, reject, progress});
    worker.postMessage({id, operation, ...payload}, payload.pixels ? [payload.pixels.buffer] : []);
  });
}

function imagePixels(image) {
  const w = image.naturalWidth, h = image.naturalHeight;
  if (Math.min(w, h) < 32 || w*h > 45000000) throw new Error("请使用尺寸合适的照片（最多 4500 万像素）");
  const scale = Math.min(1, 960 / Math.max(w, h));
  const canvas = document.createElement("canvas");
  canvas.width = Math.round(w*scale);
  canvas.height = Math.round(h*scale);
  const ctx = canvas.getContext("2d", {willReadFrequently:true});
  ctx.drawImage(image, 0, 0, canvas.width, canvas.height);
  const {width, height, data} = ctx.getImageData(0, 0, canvas.width, canvas.height);
  return {width, height, pixels:new Uint8Array(data.buffer)};
}

function tap(params) {
  return params.has("x") ? [Number(params.get("x")), Number(params.get("y"))] : null;
}

export async function openGuide(form, image, progress) {
  const file = form.get("reference");
  if (file.size > 24*1024*1024) throw new Error("请使用小于 24 MB 的参考照");
  const frame = imagePixels(image);
  progress("正在准备手机离线包");
  await prepareOffline();
  if (!notes) notes = await (await fetch(new URL("../campus-viewpoints.json", import.meta.url))).json();
  const hash = new Uint8Array(await crypto.subtle.digest("SHA-256", await file.arrayBuffer()));
  const key = Array.from(hash, byte => byte.toString(16).padStart(2,"0")).join("");
  const options = {mirror:form.get("mirror") === "1", tap:tap(form), viewpoint:(form.get("viewpoint") || notes[key] || "").slice(0,100)};
  options.farPoint = form.has("farX") ? [Number(form.get("farX")), Number(form.get("farY"))] : null;
  return request("open", {sid:crypto.randomUUID(), ...frame, options}, progress);
}

export async function analyzeFrame(sid, query, blob) {
  const image = new Image();
  const url = URL.createObjectURL(blob);
  try {
    image.src = url;
    await image.decode();
    const options = {seq:Number(query.get("seq")), source:Number(query.get("source")), tap:tap(query),
      skip:query.get("skip").split(",").filter(Boolean)};
    return await request("frame", {sid, ...imagePixels(image), options});
  } finally {
    URL.revokeObjectURL(url);
  }
}

export function releaseGuide(sid) {
  if (worker) request("close", {sid}).catch(() => {});
}
