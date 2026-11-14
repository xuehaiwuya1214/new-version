// At most one image is being prepared. Frames are sampled on demand, never queued.
export class FrameSampler {
  constructor() {
    this.worker = null; this.pending = null; this.serial = 0; this.disabled = false;
    this.capturing = false; this.videoFrames = true;
  }

  async capture(video, aspect, zoom, edge = 640, quality = .75) {
    if (this.disabled || !globalThis.Worker || !globalThis.OffscreenCanvas) return null;
    if (this.capturing) return null;
    this.capturing = true;
    let bitmap;
    try {
      if (!this.worker) {
        this.worker = new Worker(new URL("./frame-worker.js", import.meta.url), {type:"module"});
        this.worker.onmessage = ({data}) => {
          const task = this.pending;
          if (!task || task.id !== data.id) return;
          clearTimeout(task.timer);
          this.pending = null;
          if (data.error) task.reject(new Error(data.error));
          else task.resolve(data);
        };
        this.worker.onerror = () => this.dispose();
        this.worker.onmessageerror = () => this.dispose();
      }
      const worker = this.worker;
      const w = video.videoWidth, h = video.videoHeight;
      const cw = Math.min(w, h*aspect)/zoom, ch = cw/aspect;
      const scale = Math.min(1, edge/Math.max(cw, ch));
      const width = Math.max(1, Math.round(cw*scale)), height = Math.max(1, Math.round(ch*scale));
      // Transfer the video resource before crop/resize/readback. createImageBitmap(video,
      // ..., resizeOptions) used to do that preparation on the preview's thread.
      if (this.videoFrames && globalThis.VideoFrame) {
        let frame;
        try {
          const started = performance.now();
          frame = new VideoFrame(video);
          const result = await this.send(worker, {frame, crop:{x:(w-cw)/2, y:(h-ch)/2, width:cw, height:ch},
            width, height, quality, path:"video-frame", mainMs:performance.now()-started}, frame);
          return result;
        } catch {
          frame?.close();
          this.videoFrames = false;
          if (this.disabled) return null;
          // Some WebViews expose VideoFrame but cannot transfer/draw it in a Worker.
        }
      }
      if (!globalThis.createImageBitmap) return null;
      const started = performance.now();
      bitmap = await createImageBitmap(video, Math.round((w-cw)/2), Math.round((h-ch)/2), Math.round(cw), Math.round(ch),
        {resizeWidth:width, resizeHeight:height, resizeQuality:"low"});
      if (this.disabled || this.worker !== worker) {bitmap.close(); return null;}
      return await this.send(worker, {bitmap, quality, path:"image-bitmap", mainMs:performance.now()-started}, bitmap);
    } catch {
      bitmap?.close();
      this.dispose();
      return null; // Older WebViews use a small, low-frequency canvas fallback.
    } finally {
      this.capturing = false;
    }
  }

  send(worker, payload, resource) {
    const id = ++this.serial;
    return new Promise((resolve, reject) => {
      this.pending = {id, resolve, reject, timer:setTimeout(() => this.dispose(), 6000)};
      try {worker.postMessage({id, ...payload}, [resource]);}
      catch (error) {
        clearTimeout(this.pending.timer);
        this.pending = null;
        reject(error);
      }
    });
  }

  dispose() {
    this.disabled = true;
    this.worker?.terminate();
    this.worker = null;
    if (this.pending) {
      clearTimeout(this.pending.timer);
      this.pending.reject(new Error("图片准备已取消"));
      this.pending = null;
    }
  }
}
