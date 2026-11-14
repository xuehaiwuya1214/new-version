# 校园人像导拍

## Android 安装包：小米 HyperOS

Android 10 及以上可安装 APK，直接从桌面打开。1.0.4 的构建输出为 **`build/apk/campus-photo-guide-1.0.4.apk`**，沿用「校园人像导拍」及包名 `cn.campus.photoguide`。这是本地产物，尚未上传 GitHub Release。分享给队友时发送这个 APK 文件，GitHub 的源码 ZIP 不能直接安装到手机。

**当前 APK 是连接电脑的轻量客户端：手机负责取景、显示引导和保存照片；参考图识别、背景匹配、人物与姿势检测、图库检索都在电脑运行。** APK 不包含模型。1.0.4 默认使用「实时识别」，需持续连接电脑。「录制演示」保留为手动模式，不持续送检，不能用于证明实时识别性能。下面的 PWA 是另外保留的离线网页版本，两种运行方式不同。

1. **安装。** 把 APK 传到手机，用「文件管理」打开。若系统提示来源未授权，只给这次使用的文件管理器开启「允许安装此来源的应用」，按系统提示完成安装。
2. **启动电脑服务。** 电脑按下方说明准备好 Python 环境和模型后，双击 `start-phone.cmd`，保留启动窗口。窗口会打印 `http://电脑IP:7862`。让手机与电脑连接同一 Wi-Fi；外拍时也可以打开手机热点，让电脑连到该热点。
3. **连接与选图。** 打开 APP，在顶部电脑服务连接栏填写刚才的地址并连接，例如 `http://192.168.1.20:7862`。使用实际地址，不要填写 `localhost`、`127.0.0.1` 或 `0.0.0.0`。然后从电脑图库选参考图，或点「打开照片」选择手机相册里的原图。首次分析需要等待电脑加载模型。
4. **拍摄。** 点「开启相机」，允许相机权限。默认「实时识别」持续将抽样帧送到电脑，自动核验对准、景别、人物站位和姿势；电脑须持续开机，每步都可跳过。需要手动演示时再切换到「录制演示」。测试卡顿时保持 720p、同一场景，比较实时识别、关闭辅助线、暂停识别三种情况，并展开「卡顿排查数据」记录读数。
5. **保存与分享。** 拍摄完成后点「保存到相册」，照片写入手机 `Pictures/CampusPhotoGuide`，可在系统相册查看。点「分享照片」调起系统分享面板。保存的是不含辅助线的 JPEG。

连接失败时，先核对电脑当前 Wi-Fi/热点的 IPv4 地址；电脑有虚拟网卡时可能显示多个地址。若 Windows 防火墙弹出询问，可按需允许 Python 在**专用网络**通信。校园 Wi-Fi 可能隔离设备，此时可改用手机热点；不用把电脑的 `8000` 端口单独开放给手机。`start-phone.cmd` 监听 `0.0.0.0:7862`，原有 `start.cmd` 仍默认使用 `127.0.0.1:7861`。

若相机被拒绝，在系统「设置 → 应用 → 校园人像导拍 → 权限」中允许相机，再回到应用重试。Android 10 及以上通过系统相册接口保存本应用照片，不需要授予整个存储空间的管理权限。

**1.0.3 录制演示模式。** 为优先保证录屏取景，演示模式关闭自动截帧、编码、传图与实时匹配，只在用户按快门时读取一次相机画面保存照片。白点使用手机方向数据做近似投影，以独立页面元素移动，不因识别超时消失；方向数据不可用时仍可点按放置白点并手动确认。此方法不能跟踪平移、遮挡或真实地标，不能作为识别准确率演示。界面始终标注「演示 · 手动确认」。

演示中白点接近白环、保持约 0.65 秒会进入景别，也可手动确认。随后显示参考轮廓，并在开启「演示变焦」时播放一次 1.12× 的预设倍率变化（总倍率仍限制在 1.0～1.6×），这不是测得的建筑匹配倍率。站位框与动作轮廓来自参考图，用户分别点「站位已合适」「动作已摆好」进入拍摄；不生成虚假的实时人物检测结果。切换模式会重新开启相机，以释放旧取帧资源。参考准备完成后可以断开电脑继续本轮演示；换参考图仍需电脑。手机真机的持续录屏表现仍需验证。

**实时识别的预览优化。** 对准和景别阶段不再持续重绘没有变化的白环与轮廓；只有新结果、白点移动/消失或短暂特效时才更新。Android 辅助线动画最多约 15 帧/秒，辅助线画布采用较低像素密度，不改变相机或照片分辨率。连接请求失败后自动暂停送帧与引导，重新连接电脑后可恢复；物理断网到发现失败之间仍可能等待网络超时。

「显示辅助线」可以独立关闭绘制而继续识别，「暂停识别」会同时停掉送帧与绘制。排查时保持相同场景，比较正常导拍、只关闭辅助线和暂停识别的预览读数：只关闭辅助线即改善，说明叠层负担较大；仍卡但暂停后改善，继续检查截帧、编码与桥接。断开电脑不会立即停掉旧版手机的本地操作，不能单独据此判断是网络还是相机故障。

手机默认请求 720p、30 帧/秒的本地预览；实际帧率受相机、光线和 WebView 影响，会在取景器下方显示近似读数。实时识别最多每秒采样一次，长边 640 像素、JPEG 质量 75%，始终只允许一帧在途，上一帧完成后再取新画面；点选主角和按下快门会优先请求核验。支持时用后台 Worker 完成缩图后的编码，不在手机运行模型。超过 3.5 秒的普通识别结果不再驱动辅助线或快门。电脑响应慢时降低引导更新频率，预览继续。

本地取帧优化：支持 `VideoFrame` 的 WebView 会先转移视频帧，再在 Worker 内完成裁切、缩放、JPEG 编码和 Base64 转换；相机帧在编码前释放。不支持或拒绝该路径时回退到 `ImageBitmap`，再不可用时使用小画布。旧实现的 `createImageBitmap(video, …resizeOptions)` 在页面线程发起裁切缩图，不能因为后续 JPEG 在 Worker 中就认定取帧没有界面开销。该优化减少页面线程上的图像处理，但仍共享设备 GPU/内存资源，不能承诺所有手机不掉帧。[Chrome 的视频帧与 Worker 说明](https://developer.chrome.com/docs/web-platform/best-practices/webcodecs)。

Android 取景时可展开「卡顿排查数据」，查看实际视频尺寸、设备报告的帧率、最近一次手机图片准备耗时、请求往返、电脑处理耗时和图片大小；`video-frame` 表示新处理路径，`image-bitmap` 和 `canvas` 表示兼容回退。新增「低负载 · 480p」供对照测试，会降低预览及保存照片的分辨率。识别结果按采样时间计算有效期：即使下一请求仍在等待，旧结果超过 3.5 秒也会撤回可拍摄状态。

本地回归：`python tests/frame_pipeline_flow.py --executable "浏览器可执行文件路径"`。测试使用模拟相机和模拟原生网络接口，真实运行视频帧转移、Worker 和 JPEG，验证裁切/变焦、回退、取消、单帧在途，以及 6 秒慢响应期间预览继续和旧快门资格失效。它不测试 Android 原生桥接性能、真实相机、模型耗时或局域网延迟。修改手机静态资源后必须重新构建并安装 APK；只重启电脑服务不会更新手机里打包的页面。

相机开启后暂停图库图片加载，列表改用电脑生成的 320 像素缩略图；选作参考时仍读取原图。点「暂停识别」可关闭送帧和辅助线，直接观察纯预览是否流畅；再点「继续识别」恢复。可切换「清晰优先 · 1080p」，但设备开销更大。当前照片从预览画面保存，720p 模式也会降低保存照片的分辨率，且受参考画幅裁切和数字变焦影响。

1.0.4 的手机优化不改变电脑接口，现有兼容服务可以继续使用。覆盖升级正式应用需要沿用原签名密钥；此前独立安装的「校园导拍·优化测试」使用不同包名，不会被正式版覆盖。若电脑还运行 1.0.0 时的服务，请关闭旧服务窗口后重新运行 `start-phone.cmd`，使缩略图接口和慢采样判定生效。排查时可在开发工具查看 `window.__guidePerformance`：`mode` 区分 `demo` 与 `live`；`prepareMs` 是手机截帧/图片准备耗时，`requestMs` 是请求往返耗时，`serverMs` 是电脑解码、排队和推理耗时；两者之差还包含桥接和序列化，不能直接视为纯网络延迟。`overlayPaints`、`overlayMs`、`longTaskMs` 分别记录最近一次统计间隔内的绘制次数、绘制调用耗时、界面长任务耗时。浏览器回归验证不替代小米真机帧率测试。

这个版本用 Capacitor 将取景页面封装在 Android WebView 中，并接入原生网络请求、相册保存和分享。局域网通过原生 HTTP 请求访问电脑，无需给电脑配置 HTTPS；APP 里的相机页面仍保留本地安全来源。相机预览尚未改为 CameraX 原生取景，不能保证获得小米系统相机的全部算法、虚化或画质处理。当前没有完成小米真机验证；摄像头权限、照片保存和现场识别速度需在实际手机上测试。

### 在 Windows 构建 APK

准备 **Node.js 22 或以上、Python 3.11 或以上**，在项目根目录执行下方命令。准备脚本会自动补齐完整 JDK 21、Android SDK 和 Gradle，并校验固定下载包的 SHA-256。工具与下载缓存都放在项目的 `build/android-tools`，无需修改系统 Java 或全局安装 Android SDK。

```powershell
.\scripts\prepare_android_tools.ps1
.\scripts\build_android.ps1
```

已有完整 JDK 21 时可给准备脚本传入 `-JavaHome 'C:\path\to\jdk-21'`。脚本会同时检查 `javac`、`jlink` 和模块文件；部分 IDE 自带运行环境缺少 `jlink`，不能用于这次 Android 编译，脚本会改用项目内下载的完整 JDK。

需要本地 HTTP 代理时，两条命令都可以追加 `-ProxyHost 127.0.0.1 -ProxyPort 7897`，端口按实际配置填写。构建脚本会准备依赖、打包客户端页面并输出已签名 APK，不把模型装进 APK。安装手机只需最终 APK；无需安装 Node.js、Python 或开发工具。电脑推理环境仍按下方说明准备。

测试签名密钥保存在已忽略的 `build/android-signing` 中。**保留并私下备份这个目录**，以后用同一密钥签名才能直接覆盖升级；不要提交到 Git、公开分享或随 APK 一起发送。更换密钥后，已安装的同名应用不能直接覆盖安装。

## 推荐选图与四步导拍

现在可以在同一个页面中完成 **选参考照 → 对准中心 → 匹配景别 → 人物站位 → 姿势引导 → 拍摄**。在电脑上双击 `start.cmd`，打开 `http://127.0.0.1:7861/`。启动器会同时启动旁边 `campus-photo-recommend/.venv` 中的推荐服务，日志在 `build/logs/recommend.log`；关闭启动窗口会结束本次启动的服务。

页面上方的「筛选图库」按已有地点和描述查找原图；「AI 推荐 3 张」调用成员 2 的 SigLIP2 图文匹配、美学排序和机位去重。两者分别显示「图库筛选」「AI 推荐」，服务不可用或缺少特征时会明确提示。点击照片即载入参考图，并带入地点、机位说明。人数、景别、姿态未知的照片不会被当成满足硬条件。

当前推荐代码实际使用 `google/siglip2-base-patch16-224` 和 NIMA-VGG16，美学分及图库图向量预先缓存，查询时计算文本向量并排序；当前链路未接入 Qwen-VL 2B。

默认优先使用 `../campus-photo-recommend/data/formal/catalog.json`，图片也可从它的 `source.archive_path` 映射到 `../校园摄影参考库_队友分享_98张/`。没有正式标签表时，直接读取共享图库的 `统一参考库/sources.json`。可用 `CAMPUS_LIBRARY_INDEX` 指定其他 `catalog.json` 或 `sources.json`。推荐模型、推荐特征和图库原图均留在本机，不随源码发布，也不内置到 APK。

### 在新电脑准备推荐服务

两个仓库与共享图库放在同一父目录，导拍与推荐各用自己的虚拟环境。推荐依赖单独安装；不必把队友的整个环境覆盖到导拍环境上。下面以 Windows、Python 3.13 和 CPU 为例，特征缓存只需首次生成或更新图库后重建。

```powershell
# 在 new-version 中，先按下方说明准备导拍环境。
.\.venv\Scripts\python.exe scripts/prepare_recommend_catalog.py --output ../campus-photo-recommend/data/formal
cd ../campus-photo-recommend
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install torch==2.9.1 torchvision==0.24.1 --index-url https://download.pytorch.org/whl/cpu --timeout 120
.\.venv\Scripts\python.exe -m pip install -r ../new-version/requirements-recommend.txt
.\.venv\Scripts\python.exe -X utf8 demo/download_models.py
.\.venv\Scripts\python.exe -X utf8 -m photo_recommend.preprocess --catalog data/formal/catalog.json --device cpu
cd ../new-version
.\start.cmd
```

导出脚本保留已有的正式标签，按照片编号复制原图。如果输入仅有 `sources.json`，未经确认的人数、姿势等字段保留 `null`。已有可用的 CUDA 环境可使用队友仓库的 CUDA 安装命令，并改用 `--device cuda`。

已有推荐服务可以设置 `PHOTO_RECOMMEND_URL=http://127.0.0.1:8000`；跨电脑部署时填实际服务地址。适配层仅按图库中已校验的照片编号读取原图，忽略上游返回的任意图片路径。空结果保留上游的 `reason`、待确认数量和缺失特征信息。

### 手机端检索

**APK：** 在顶部连接电脑服务后，图库、AI 推荐和导拍识别统一使用该地址，例如 `http://192.168.1.20:7862`，无需再单独连接推荐服务。电脑上的本项目服务会在内部访问 `campus-photo-recommend` 的 `127.0.0.1:8000` 接口。`start-phone.cmd` 会尝试一并启动推荐环境。手机的 `localhost` 是手机自身，不能用来指代电脑。

**PWA 网页：** 离线导拍在手机本地运行，AI 推荐需连接可访问的 **HTTPS 后端**。在「连接图库服务」填写本项目服务的根地址；只提供推荐服务的 `8000` 端口还不够，因为还需要目录和原图接口。后台的 `CAMPUS_RECOMMEND_ORIGINS` 默认包含 `https://xuehaiwuya1214.github.io` 和 `https://localhost`，自定义时填写全部需要保留的网页来源（英文逗号分隔）。反向代理可连接本服务的 `7861` 或局域网启动的 `7862` 端口。远程公开部署使用 HTTPS，不应直接向公网开放开发服务。

没有电脑连接时，APK 无法进行模型识别或 AI 检索；只有已下载离线包的 PWA 网页可以在断网时继续本地导拍。

## 手机网页实地测试（PWA）

**[手机打开校园人像导拍](https://xuehaiwuya1214.github.io/new-version/)**。把这个链接发给队友即可。手机用可安装的网页应用（PWA），第一次联网下载约 **128 MB**。识别和拍摄都在手机上完成，可以独立带到校园使用。GitHub 的「Code → Download ZIP」是供电脑开发使用的源码。

1. **添加到主屏幕。** Android 用 Chrome 打开上面的链接，点右上角菜单 →「添加到主屏幕」或「安装应用」→「安装」。iPhone 用 Safari 打开链接，点「分享」→「添加到主屏幕」→「添加」。如果有「作为网页 App 打开」，请开启。收到微信群链接时，可先用「在浏览器打开」切到 Chrome 或 Safari。也可以直接在浏览器标签页测试。[Android 操作说明](https://support.google.com/chrome/answer/9658361?hl=zh-Hans&co=GENIE.Platform%3DAndroid) · [iPhone 操作说明](https://support.apple.com/zh-cn/guide/iphone/iphea86e5236/ios)
2. **出发前下载。** 从准备外拍时使用的桌面图标或浏览器打开应用，连 Wi-Fi 点「下载离线包」，保持页面打开，直到顶部显示「离线包已就绪 · 可断网取景」。下载中断可点「继续下载」。可以打开飞行模式，关闭后从同一图标重新进入，确认仍显示「已就绪」。清理浏览器数据、切换浏览器或重装后，需要重新下载。
3. **准备参考照。** 先把选好的校园人像原图存到手机相册或「文件」，保证离线也能打开。点「打开照片」选择 JPG、PNG 或 WebP，等待识别主角和地标。首次启动引擎和分析参考照可能需要一会儿。队友已有的参考图库可直接使用，公开仓库只附原图指纹和机位文字。也可以选自己拍的人像。
4. **取景。** 点「开启相机」，允许摄像头权限，默认使用后摄。到参考地点先把白点移入白环，再对照建筑轮廓调整景别，随后按红色站位框和白色动作轮廓调整人物。四个环节都可跳过。室内可把同一张校园人像同时选作参考照和「用照片试拍」，观察是否依次通过。
5. **保存到手机。** 当快门亮起，点「拍摄并保存」。核验成功后，取景器下方会出现本次照片。点「保存到相册或分享」选择系统的保存选项，或点「下载 JPEG」到下载目录。在 iPhone 上也可长按这张照片保存。保存的是干净原片。参考照和取景画面只在本机处理。

**常见情况**

- **开启相机被拒绝。** 在浏览器的网站权限和系统的浏览器相机权限中允许摄像头，回到上面的 HTTPS 链接重试。浏览器内的「用照片试拍」也能走完整引导。
- **导入相册照片失败。** 如果原图是 HEIC，而文件选择器没有提供兼容照片，请先导出为 JPEG。参考照限 24 MB、4500 万像素。
- **背景一直未对上。** 确认能看到参考中的同一段建筑边缘或固定景物。室内测试或遮挡较多时，可依次点「跳过对准」「跳过景别」，直接测试人物。现场反馈请记下手机型号、浏览器、参考照编号以及卡住的步骤。

## 电脑本机运行

双击 `start.cmd`，用 Edge 或 Chrome 打开[本机取景页](http://127.0.0.1:7861/)。从「打开照片」载入上游已经选好的参考照，再点「开启相机」。也可以点「用照片试拍」，直接比较另一张校园照片。多人照片可在参考照和取景画面上分别点选主角。

模型随目录提供，运行时在本机推理。启动器优先使用本目录的虚拟环境，其次使用仓库已有的 `.venv`。在新电脑上，可在本目录用 Python 3.13 建立环境。

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\start.cmd
```

## 手机网页版本如何发布

推送到 `main` 后，GitHub Actions 从 `mobile/build.py` 构建静态页面并部署到 GitHub Pages。新仓库首次部署时，在 Settings → Pages → Build and deployment 中将 Source 设为 GitHub Actions。页面、模型与运行库均来自同一站点，离线下载会核对每个文件的 SHA-256。应用更新在关闭旧页面后生效。

手机后台 Worker 用 Pyodide 运行与桌面共用的取景逻辑和 OpenCV，用 ONNX Runtime Web 的单线程 WebAssembly 执行五个模型。后台只分析一帧，完成后再采下一帧，预览和按钮继续响应。机位、站位、姿势的判定阈值与桌面相同。实际识别速度取决于设备、参考图里的人物大小和背景特征。[Pyodide](https://pyodide.org/) · [ONNX Runtime Web](https://onnxruntime.ai/docs/tutorials/web/env-flags-and-session-options.html)

本地构建手机站点需要 Python 3.11 或以上。首次构建下载固定版本的运行库，之后可用本地缓存重复构建。在项目根目录运行以下命令，再在电脑浏览器打开 `http://127.0.0.1:8080/`。手机实测使用上方 HTTPS 入口。

```sh
python mobile/build.py
python -m http.server 8080 --bind 127.0.0.1 --directory build/mobile
```

## 实时识别模式：拍摄时怎么跟

| 阶段 | 取景器里的操作 | 判定依据 |
| --- | --- | --- |
| 对准 | 首次找到参考背景后，白点锁在同一处景物上，白色小环固定在画幅中心。镜头转动时通过光流跟踪这个景物落点，不再每帧换一个中心。 | 中心误差进入画幅短边的 8% 后开始确认，确认期间超过 10% 才退出；连续确认 0.32 秒进入景别。通过后记为完成，不因移动、失配、变焦或短暂暂停重新要求对准。 |
| 景别 | 参考背景轮廓先用约 0.9 秒展开，再播放 0.45 秒轻扫特效；有可靠倍率建议后，用约 0.85 秒平滑调整 1.0～1.6× 数字倍率。轮廓固定，镜头画面缩放。拖动滑杆可随时接管。 | 动画期间暂停识别，结束后用新画面核对背景地标间距，达到参考的约 89%～113% 并保持 0.32 秒后通过；后续允许约 85%～118%。大小已经接近时不强行放大，也不重复核对中心。 |
| 站位 | 红色矩形表示参考人物的位置和大小，青色框角表示当前主角。按提示让模特大致站进目标框。 | 两个人物框的交集占并集至少 45%，持续 0.35 秒即通过。调整姿势时允许降到 35%，便于抬手、迈步。 |
| 姿势 | 白线勾出参考主角的头、身体和四肢，固定在取景画面上。模特照着轮廓摆动作，青点跟踪真人，箭头指向当前最需要靠近的参考关节。 | 逐帧比较同一画幅中对应关节的位置及头肩倾角。用参考人物尺度给关节留出容差，保持 0.50 秒即通过。半身照只考核参考中可靠的部位。 |
| 拍摄 | 出现「可以拍摄」后点「拍摄并保存」。 | 快门会用按下时的画面再核验保留的步骤，然后保存同一帧的干净 JPEG。 |

对准、景别、站位和姿势的提示旁都有「跳过」按钮，选择在本轮保留。背景一直匹配不上时，依次跳过对准和景别即可直接安排人物。四步都跳过可手动拍摄。点「恢复全部步骤」或打开新参考照会重新开始自动引导。

景别步骤支持钟楼、圆塔、灯塔及语义清晰的建筑、桥梁。SegFormer 先确定建筑区域，塔楼沿用天际线和钟面提取，其他建筑提取可见外沿；长线段提供屋檐、墙边等结构细节。人物遮挡和画幅裁切边缘不绘成建筑线。仅有钟楼地点名而实际只看见栏杆时不生成塔楼轮廓。轮廓始终使用参考画幅坐标，不跟随实时建筑移动。

其他背景结构也会生成较淡的稀疏长轮廓：在载入参考图时提取一次，去掉人物和画幅边缘，最多保留 35 条路径。没有可辨认轮廓时仍使用固定地标。手机 PWA 与电脑共用轮廓显现、轻扫和平滑变焦代码，离线包包含全部依赖。变焦期间预览与取景裁切同步变化，结束后重新识别才可通过；手动倍率、关闭自动匹配、跳过、更换图源或切到后台均会中止旧动画。

白点的底层采用固定关键帧的双向光流跟踪，排除人物区域，核对分散的角点与几何一致性；静止时忽略亚像素噪声。即使接近白环，白点也留在景物上，不再提前吸到屏幕中心。跟踪丢失时淡出，连续定位证据一致后重新显示；没有可靠景物证据不凭旧坐标通过。

确认中心后播放约 1.6 秒的金色亮环，取景器顶部同时显示“中心已锁定”和当前下一步。对准只做一次，后续不自动返回第一步；点“重新对准”、更换参考照/相机来源或“恢复全部步骤”才重新开始。普通文字提示至少保留 2.5 秒，新建议持续 650 毫秒才替换；阶段变化、用户操作和错误提示立即显示。

白点表示**参考画面中心在当前镜头中的投影**，不是建筑包围框的中心；否则会改变原参考的人像构图。主体建筑有足够特征时，背景匹配还要求建筑区域内存在匹配证据。白点落在画外时用白色箭头指向它所在的一侧。景别阶段若没有清晰建筑，则用最多四个固定地标核对位置与间距。

机位接近并稳住后，系统比较多对地标间距，按比例在 1.0～1.6× 范围内调整数字倍率，再采一帧核对景别。例如，同一段屋檐在镜头里只有参考图的七成宽，倍率会从 1× 调到约 1.43×。到达倍率边界仍有偏差时，相机会提示靠近或后退。近处和远处的地标间距变化不一致时，会提示调整拍摄位置。照片试拍、相机预览、人物引导和保存的 JPEG 共用同一套取景裁切。

动作轮廓来自用户选中的参考人物。人物分割可靠时，白线沿真实外缘描画，也保留弯臂形成的空隙和部分内部线条。分割细节不足时，根据可见关节绘制简化动作轮廓，取景器图例会标出这一点。拍摄的 JPEG 只包含原始取景画面。[鹿酱的取景演示](https://www.douyin.com/video/7641535087246702202) · [华为导入参考姿势的说明](https://consumer.huawei.com/cn/support/content/zh-cn16094218/)

室内书架和窗框也可以充当地标。近景会用 PP-HumanSeg 保留头肩两侧的真实背景。如果连续两帧的背景证据都明显支持相反的左右方向，页面会自动水平翻转参考预览，重新提取同一主角的目标姿势。参考照下方可以手动翻转或还原。拍摄结果沿用相机方向。

## 人体识别和实现

- `people.py` 用 YOLOX-Tiny HumanArt 先找人，再把主角裁成 192 × 256 的输入交给 RTMPose-m Body7，解码 17 个 COCO 关节。全图没有清晰主角时会放大局部重检。参考照常规方向无法建模时会尝试旋转，并把结果映回原图。路人进入画面时优先跟踪已选的人。[RTMPose 官方项目](https://github.com/open-mmlab/mmpose/tree/main/projects/rtmpose) · [rtmlib 的检测与姿态流程](https://github.com/Tau-J/rtmlib/blob/main/rtmlib/tools/solution/body.py)
- `outline.py` 用 PP-HumanSeg 分割参考照中选定的主角，将轮廓映射到原图坐标。每张参考照只提取一次，翻转参考照或重新点选主角时重新提取。`posture.py` 比较站位框和固定关节。参考点置信度阈值为 0.52，实时点为 0.36，参考中可靠的关节在当前帧消失时会提示露出该部位。
- `background.py` 先在人物以外比对 RootSIFT 特征，传统匹配找到的对应点不足时，再用 XFeat 和 MAGSAC 寻找同一地标。XFeat 的结果至少需有 24 个不同位置的内点，并在两幅图中都有足够分布。`landmarks.py` 用多对相隔较远的背景点测量相对倍率。`guide.py` 负责稳定判定、阶段推进和回退。[OpenCV 特征匹配与单应变换](https://docs.opencv.org/4.x/d1/de0/tutorial_py_feature_homography.html) · [XFeat 官方实现](https://github.com/verlab/accelerated_features)
- `building.py` 在载入参考照时使用量化的 SegFormer-B0 分割天空、建筑、钟面与人物。它筛选两侧有天空、比例和高度合适的塔身，在原图上用 GrabCut 细化轮廓。钟楼近景可单独提取钟面及相邻墙边。轮廓使用参考画幅的归一化坐标，翻转参考照时会从翻转后的画面重新生成。[SegFormer 模型说明与许可](https://huggingface.co/nvidia/segformer-b0-finetuned-ade-512-512)

先对圆心、稳住手机再变焦的交互参考了华为的辅助构图说明。本项目用上传的参考照提供构图目标。单帧相对深度能描述景物的远近，复拍还需要确认两张图里具体哪几处是同一地标，因此当前使用跨图匹配测量倍率。[华为操作说明](https://consumer.huawei.com/cn/support/content/zh-cn16073539/) · [Depth Anything V2](https://github.com/DepthAnything/Depth-Anything-V2)

人体检测与姿态权重取自 OpenMMLab，人像分割权重取自 OpenCV Zoo，XFeat 使用 V1.0.0 的稀疏 ONNX 导出，SegFormer 使用 ADE20K 的 B0 量化导出。模型都在本机使用 ONNX Runtime CPU 推理，文件可用 PowerShell 的 `Get-FileHash models/*.onnx` 核对。SegFormer 权重适用 NVIDIA 的非商业研究或评估许可，原文随模型提供。

| 本地模型 | SHA-256 | 官方来源 |
| --- | --- | --- |
| yolox-tiny.onnx | `ceb11c07298f95c50d7c5abeb906d03340c85f23aa79e3e66966e7fb6c307250` | [YOLOX-Tiny 模型包](https://download.openmmlab.com/mmpose/v1/projects/rtmposev1/onnx_sdk/yolox_tiny_8xb8-300e_humanart-6f3252f9.zip) |
| rtmpose-m.onnx | `5c0a4bf67953e6d2ac43ce15e77dc9d5d354ae18430a47d2c5963a7bc5683e3c` | [RTMPose-m 模型包](https://download.openmmlab.com/mmpose/v1/projects/rtmposev1/onnx_sdk/rtmpose-m_simcc-body7_pt-body7_420e-256x192-e48f03d0_20230504.zip) |
| pphumanseg.onnx | `552d8a984054e59b5d773d24b9b12022b22046ceb2bbc4c9aaeaceb36a9ddf24` | [OpenCV Zoo PP-HumanSeg](https://github.com/opencv/opencv_zoo/tree/main/models/human_segmentation_pphumanseg) |
| xfeat.onnx | `41336466bbbb09b701b815e33932c7da69cff6ab75a068230418468430482912` | [XFeat-ONNX V1.0.0](https://github.com/DavideCatto/XFeat-ONNX/releases/tag/V1.0.0) |
| segformer-b0-ade-q8.onnx | `9a98d6daf3d926869ab8cc4c2ed7374a2bc23b889bb7ca3b0915d15e3c4756bb` | [Xenova 量化导出](https://huggingface.co/Xenova/segformer-b0-finetuned-ade-512-512/blob/d3e5499fa8701ff0453ca940a8dfeae39b2f1504/onnx/model_quantized.onnx) |

## 对接上游

1. `GET /api/library` 返回目录、地点及按照片编号访问的 `image_url`。`POST /api/recommend` 接收成员 2 原有查询字段，代理到 `/recommend`，补充可加载的图片地址与机位说明；原始照片通过 `GET /api/library/<photo_id>/image` 读取。
2. `POST /api/session` 以 multipart 传入 `reference`。可附 `viewpoint`、归一化主角坐标 `x/y` 和 `mirror=1`。返回 `id/reference`，`aimPoint=[0.5,0.5]` 表示参考画幅中心；`centerTolerance` 是允许的中心距离占短边的比例；人物框、关节和轮廓都在固定参考坐标中。手机与桌面都会保留检索选图带入的机位说明。
3. `POST /api/session/<id>/frame?seq=1` 发送同画幅 JPEG，帧号递增。四个引导阶段为 `background`（对准）、`framing`（景别）、`position`（站位）、`pose`（姿势），完成为 `ready`。`skip=background,framing` 可跳过整个背景环节；省略沿用，`skip=` 恢复全部。`completed` 中的 `background` 表示中心已经确认，后续不自动撤销；重新对准时递增 `source`。`checks` 按上述四步排列，跳过项为 `null`；`background.centerOk/scaleOk` 分别表示中心与景别判断。旧版只有三个检查项，接入方需要同步更新。
4. `DELETE /api/session/<id>` 释放会话。手机浏览器需要 HTTPS 或 localhost 才能使用摄像头。安卓电脑调试可用 `adb reverse tcp:7861 tcp:7861`。

`buildingOutline` 为 `{name, kind, paths, details, bounds}` 或 `null`，外沿、结构线和边界框均是固定参考坐标。稳定后 `zoomFactor` 建议乘到当前倍率，**仅在景别步骤**应用；客户端须重新采样验证。切换来源时更新整数 `source`，重新累计稳定时间。白环大小只是视觉标记，自动通过依据实际中心距离阈值。

`backgroundOutline` 为 `{kind:"background", paths}` 或 `null`，提供排除人物后的整体背景线稿。`tests/framing_sequence_flow.py` 验证显现、扫描、变焦的时间顺序，`tests/framing_camera_flow.py` 验证手机宽度下的实际倍率插值、取消操作、终点复核与 JPEG 裁切；`tests/mobile_flow.py` 还会断网回放从约 0.7 倍景别自动调整到约 1.43× 的过程。

## 验证

本轮四步状态机和推荐适配接口的自动测试：`python -m pytest -q tests/test_guidance.py tests/test_library.py`。浏览器回放 `python tests/browser_flow.py` 覆盖图库选图、白色投影点与固定白环、建筑轮廓出现时机、红色站位框、固定动作轮廓、缩放复核、逐步跳过和干净 JPEG。景物光流测试使用 `python -m pytest -q tests/test_anchor_tracking.py`；提示节奏回放使用 `python tests/hint_pacing_flow.py`。白点防抖与成功反馈回放使用 `python tests/aim_motion_flow.py`，覆盖孤立跳点、持续移动、失配淡出、低频采样与跳过不误报成功。手机离线回放仍用 `tests/mobile_flow.py`。以下图库批量数字是上一版的测量记录，不能当作新建筑轮廓算法或现场手机识别率。

本次在独立 CPU 环境下载 SigLIP2、NIMA 及其 VGG16 骨干后，98 张正式照片的特征全部生成成功，失败数为 0；状态机、景物跟踪与接口测试通过。启动两个服务后，`python tests/recommend_flow.py` 会实际调用模型推荐钟楼照片，点击结果、走完四步导拍并保存 JPEG，结果记录在 `build/qa/recommendation-result.json`。这是模型接口与浏览器链路验证，实地跨照片对齐仍需手机测试。

在队友的 98 张校园照片中，93 张提取了参考人物的实际轮廓，5 张根据可见关节生成了简化轮廓。以同一张照片分别作参考和当前帧，97 张可同时通过机位、站位和姿势判定。另 1 张草地躺姿可以显示轮廓，可见关节不足以自动判断动作。这个检查衡量同图重复识别能力。独立照片对、已知位移、局部手腕变化、镜像和实际浏览器流程另行测试。

手机网页另有独立的浏览器回放。它从空缓存下载完整离线包，断网、关闭并重开页面，再用本地钟楼人像走完取景、四步判定、逐步跳过、恢复引导和 JPEG 下载。可在安装 Pillow、Playwright 和 Chromium 的开发环境中运行 `python tests/mobile_flow.py 参考人像.jpg`。Chromium 用模拟摄像头回放，在 Windows 的 Playwright WebKit 中用 `--browser webkit` 验证照片试拍。加 `--tower` 可核对建筑轮廓，`--other 其他场景.jpg` 可核对错误机位的快门锁定，`--url https://xuehaiwuya1214.github.io/new-version/` 可检查实际发布的站点。手机真机的摄像头权限、系统相册以及现场帧率仍需实地测试。

同一批参考照中，19 张显示建筑轮廓，79 张使用双圈。另用一张没有校园标注的灯塔实拍照测试，识别到了可叠加的塔身外沿。[Ocracoke 灯塔测试照（美国国家公园管理局）](https://npgallery.nps.gov/AssetDetail/0e62cae5-7578-4b45-82a9-880a6e8b3b96)

把已标注机位的非拼图照片居中取 3:4 画幅后，同一机位分组内共得到 103 对不同照片。RootSIFT 找到可估计几何关系的背景匹配 13 对，加入 XFeat 后共 36 对，其中 3 对在这次取景中直接满足中心和景别阈值。另用每组首张照片组成 414 对不同地点标签的对照，有 1 对给出调整角度提示，0 对通过机位判定。

- 情人坡红椅的 `REF_051.webp` 与 `REF_052.webp` 可直接用「照片试拍」复现。地标能对上，两人像的站位仍有差距。
- 录屏可用 `REF_016.webp` 看完整钟楼，`REF_029.webp` 看钟面和两条外墙线。换成同一地点标签的栏杆照 `REF_062.webp`，即可看到双圈回退。
- 以草坡朝圆塔的 `REF_046.webp` 作参考、`REF_095.webp` 试拍，系统先用白点提示向左转，进入景别步骤后再显示青色目标轮廓。两张照片的构图和人物站位都不同。
- 以国科大桥的 `REF_103.jpg` 作参考，载入 `REF_104.jpg` 试拍并跳过对准、景别，可看到站位框通过后，白色参考轮廓固定在画面里，青色箭头提示左肘的移动方向。
- 在本目录用现有环境运行以下命令。浏览器回放使用本机 Chromium 和可控视频流，能验证变焦、拍摄资格撤回、干净 JPEG 导出和手机宽度布局。校园实地光照变化、遮挡和移动速度仍需现场验证。

```powershell
..\.venv\Scripts\python.exe -m pytest -q tests/test_guidance.py
..\.venv\Scripts\python.exe tests/browser_flow.py
..\.venv\Scripts\python.exe tests/check_library.py "..\校园摄影参考库_队友分享_98张\统一参考库"
```

