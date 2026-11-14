// Image preparation only. Detection, pose and matching still run on the computer.
let canvas;
self.onmessage = async ({data: {id, bitmap, frame, crop, width, height, quality, path, mainMs}}) => {
  const source = frame || bitmap;
  try {
    const started = performance.now();
    width = width || bitmap.width;
    height = height || bitmap.height;
    if (!canvas || canvas.width !== width || canvas.height !== height)
      canvas = new OffscreenCanvas(width, height);
    const ctx = canvas.getContext("2d", {alpha:false});
    if (crop) ctx.drawImage(source, crop.x, crop.y, crop.width, crop.height, 0, 0, width, height);
    else ctx.drawImage(source, 0, 0);
    // Release the camera resource before JPEG encoding; do not retain preview buffers.
    source.close();
    const blob = await canvas.convertToBlob({type:"image/jpeg", quality});
    const base64 = new FileReaderSync().readAsDataURL(blob).split(",")[1];
    self.postMessage({id, blob, base64, path, mainMs, workerMs:performance.now()-started});
  } catch (error) {
    self.postMessage({id, error:error.message || "图片准备失败"});
  } finally {
    source?.close();
  }
};
