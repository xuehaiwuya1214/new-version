"""用人物以外的地标配准，先对圆心，再从地标间距核验景别。"""
import math

import cv2
import numpy as np

from inference import Prepared, foreground_mask, synchronous
from landmarks import landmark_scale, reference_landmarks, xfeat_features_async

# 两个半径都以取景画幅短边为单位。进入站位后留一点手持抖动余量。
CENTER_TOLERANCE = .08
CENTER_RELEASE = .10
SCALE_TOLERANCE = 1.13
SCALE_RELEASE = 1.18
MIN_LEARNED_INLIERS = 24
FRAME = np.float32([[.20, .20], [.80, .20], [.80, .80], [.20, .80]])
FRAME_CENTER = np.float32([.5, .5])


class Background(Prepared):
    async def prepare(self, image, people, segmenter=None):
        self.sift = cv2.SIFT_create(nfeatures=1250, contrastThreshold=.025)
        self.matcher = cv2.BFMatcher(cv2.NORM_L2)
        self.segmenter = segmenter
        gray, mask = await self.background_image_async(image, people)
        self.landmarks = reference_landmarks(gray, mask)
        self.template = gray, mask
        self.learned = {}
        self.subject = None
        self.far_bounds = None
        self.retry_far = False
        self.reference = self.features(gray, mask)
        self.flipped = self.features(cv2.flip(gray, 1), cv2.flip(mask, 1))
        self.usable = len(self.reference[0]) >= 8 or len(self.flipped[0]) >= 8 or bool(len(self.landmarks))
        self._anchor_serial = 0
        self.reset_tracking()

    __init__ = synchronous(prepare)

    def reset_tracking(self):
        """新的取景来源必须重新定锚，不能沿用上一段画面的运动。"""
        self._scene = None
        self._reacquire = None
        self._had_anchor = False

    def set_subject(self, outline):
        # 用建筑内的匹配证据确认地标，中心仍投影参考画幅的中心，保持原构图。
        if outline and outline.get("bounds"):
            bounds = np.array(outline["bounds"])
            points = self.reference[0]
            inside = ((points >= bounds[:2]) & (points <= bounds[2:])).all(1)
            if inside.sum() >= 16:
                self.subject = bounds

    async def background_image_async(self, image, people):
        h, w = image.shape[:2]
        ratio = min(1., 720 / max(w, h))
        gray = cv2.resize(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY),
                          (max(1, round(w * ratio)), max(1, round(h * ratio))))
        h, w = gray.shape
        # 边框/角落水印与移动的人均不作为相机机位的证据。
        mask = np.zeros((h, w), np.uint8)
        border = max(3, round(.023 * min(h, w)))
        mask[border:-border, border:-border] = 255
        areas = [np.prod(box[2:4] - box[:2]) for box in people]
        probability = None
        if self.segmenter is not None and any(area >= .22 for area in areas):
            probability = cv2.resize(await foreground_mask(self.segmenter, image), (w, h))
            radius = max(5, round(.016 * min(w, h)))
            kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * radius + 1,) * 2)
            mask[cv2.dilate(np.uint8(probability > .14), kernel) != 0] = 0
        for box, area in zip(people, areas):
            if probability is not None and area >= .22:
                x, y, right, bottom = np.rint(box[:4] * [w, h, w, h]).astype(int)
                region = probability[y:bottom, x:right]
                if region.size and np.mean(region >= .8) >= .16:
                    continue
            padding = .035 * (box[2:4] - box[:2]) + .008
            lo = np.int32((box[:2] - padding) * [w, h])
            hi = np.int32((box[2:4] + padding) * [w, h])
            cv2.rectangle(mask, lo, hi, 0, -1)
        return gray, mask

    background_image = synchronous(background_image_async)

    def features(self, gray, mask):
        h, w = gray.shape
        pts, desc = self.sift.detectAndCompute(gray, mask)
        coords = np.float32([p.pt for p in pts]).reshape(-1, 2) / np.float32([w, h])
        if desc is not None:
            desc = np.sqrt(desc / np.maximum(desc.sum(axis=1, keepdims=True), 1.))
        return coords, desc

    def pairs(self, a, b):
        matches = {}
        for pair in self.matcher.knnMatch(a, b, k=2):
            if len(pair) == 2 and pair[0].distance < .83 * pair[1].distance:
                matches[pair[0].queryIdx] = pair[0].trainIdx
        return matches

    async def learned_match_async(self, observed, size, tolerance, scale_limit, flip=False):
        if len(observed[0]) < MIN_LEARNED_INLIERS:
            return None
        if flip not in self.learned:
            gray, mask = self.template
            if flip:
                gray, mask = cv2.flip(gray, 1), cv2.flip(mask, 1)
            self.learned[flip] = await xfeat_features_async(gray, mask)
        xy, desc = self.learned[flip]
        if len(xy) < MIN_LEARNED_INLIERS:
            return None
        live, live_desc = observed
        matches = cv2.BFMatcher(cv2.NORM_L2, crossCheck=True).match(desc, live_desc)
        pairs = sorted((m for m in matches if m.distance < .81), key=lambda m: m.distance)[:190]
        if len(pairs) < MIN_LEARNED_INLIERS:
            return None
        src = xy[[m.queryIdx for m in pairs]]
        dst = live[[m.trainIdx for m in pairs]]
        # 同款拱窗可能有很相似的描述子，学习特征需要更多分散的内点。
        return self.projective(src, dst, size, tolerance, scale_limit, learned=True, flipped=flip)

    learned_match = synchronous(learned_match_async)

    async def measure_async(self, image, people, tolerance=CENTER_TOLERANCE, scale_limit=SCALE_TOLERANCE):
        if not self.usable:
            return None
        gray, mask = await self.background_image_async(image, people)
        if self._scene is not None:
            tracked = self._scene.measure(gray, mask, tolerance, scale_limit)
            if tracked is not None and self.retry_far and self.far_bounds is not None and tracked.get("farScale") is None:
                # A coarse scene lock can predate a good view of the chosen landmark.
                # Retry registration periodically when that region has too few tracked corners.
                self._scene.far_misses += 1
                if self._scene.far_misses >= 3:
                    tracked = None
            elif tracked is not None:
                self._scene.far_misses = 0
            if tracked is not None:
                if tracked["zoom"] is None:
                    tracked = await self.recover_scale_async(gray, mask, tracked, tolerance, scale_limit)
                return tracked | {"sceneLocked": True, "anchorId": self._anchor_serial}
            # 丢失后先隐藏，不能立即跳到一次新的跨图估计上。
            self._scene = None
            self._reacquire = None
        observed = self.features(gray, mask)
        h, w = image.shape[:2]
        size = np.float32([w, h])
        direct = self.match(self.reference, observed, size, tolerance, scale_limit)
        if direct and direct["quad"] is not None and direct["inliers"] >= 18:
            return self.lock_scene(gray, mask, direct)
        flipped = self.match(self.flipped, observed, size, tolerance, scale_limit, flipped=True)
        if not direct and (not flipped or flipped["inliers"] < 9):
            observed = await xfeat_features_async(gray, mask)
            direct = await self.learned_match_async(observed, size, tolerance, scale_limit)
            if not direct:
                flipped = await self.learned_match_async(observed, size, tolerance, scale_limit, flip=True)
        baseline = direct["inliers"] if direct else 0
        if flipped and flipped["inliers"] >= max(9, baseline * 1.65 + 2):
            self._reacquire = None
            return flipped | {"ok": False, "tracking": False, "centerOk": False, "anchors": [],
                              "control": "mirror", "mode": "coarse",
                              "message": "两边的固定背景左右相反，可以水平翻转参考照"}
        return self.lock_scene(gray, mask, direct)

    measure = synchronous(measure_async)

    async def recover_scale_async(self, gray, mask, tracked, tolerance, scale_limit):
        # 初次局部匹配可能只确定了中心。补足景别证据时只补倍率，不能换锚点。
        scene = self._scene
        scene.scale_checks += 1
        if scene.scale_checks % 4 != 1:
            return tracked
        observed = self.features(gray, mask)
        candidate = self.match(self.reference, observed, scene.size, tolerance, scale_limit)
        if not candidate or candidate["zoom"] is None:
            observed = await xfeat_features_async(gray, mask)
            candidate = await self.learned_match_async(observed, scene.size, tolerance, scale_limit)
        return scene.accept_scale(candidate, tracked, tolerance, scale_limit)

    def lock_scene(self, gray, mask, result):
        if not result or not result["tracking"]:
            self._reacquire = None
            return result
        if result.get("trackingQuad") is None and result["quad"] is None:
            return result | {"tracking": False, "ok": False, "centerOk": False, "sceneLocked": False}
        if self._had_anchor:
            candidate = self._reacquire
            self._reacquire = result
            aspect = gray.shape[1] / gray.shape[0]
            distance = (np.asarray(result["current"]) - np.asarray(candidate["current"])) * [aspect, 1.] if candidate else None
            consistent = candidate and np.linalg.norm(distance) / min(aspect, 1.) < .035
            if not consistent:
                return result | {"ok": False, "tracking": False, "centerOk": False,
                                 "sceneLocked": False, "control": "reacquire", "mode": "search",
                                 "message": "正在找回同一处景物，请稍稳住镜头"}
        scene = SceneAnchor.create(gray, mask, result)
        if scene is None:
            # 无法建立连续跟踪时明确等待，而不是显示一个每帧更换的落点。
            return result | {"ok": False, "tracking": False, "centerOk": False,
                             "sceneLocked": False, "control": "reacquire", "mode": "search",
                             "message": "请让有清晰纹理的固定景物入镜，以便锁定目标点"}
        self._scene = scene
        self._reacquire = None
        self._had_anchor = True
        self._anchor_serial += 1
        return result | {"sceneLocked": True, "anchorId": self._anchor_serial}

    def match(self, reference, observed, size, tolerance=CENTER_TOLERANCE, scale_limit=SCALE_TOLERANCE, flipped=False):
        reference_xy, reference_desc = reference
        xy, desc = observed
        if min(len(reference_xy), len(xy)) < 8:
            return None
        forward = self.pairs(reference_desc, desc)
        reverse = self.pairs(desc, reference_desc)
        pairs = [(a, b) for a, b in forward.items() if reverse.get(b) == a]
        if len(pairs) < 8:
            return None
        a, b = np.asarray(pairs).T
        src, dst = reference_xy[a], xy[b]
        # SIFT 会在同一角点输出多个方向的描述子，只算一处空间证据。
        src, dst = distinct_pairs(src, dst, size)
        if len(src) < 8:
            return None
        result = self.projective(src, dst, size, tolerance, scale_limit, flipped=flipped)
        if result is not None:
            return result
        # 匹配集中在一侧时，用通过内点和分布检验的相似变换估计取景中心。
        matrix, mask = cv2.estimateAffinePartial2D(src * size, dst * size, method=cv2.RANSAC,
                                                   ransacReprojThreshold=max(2., .006 * max(size)))
        if matrix is None or mask is None:
            return None
        valid = mask.ravel().astype(bool)
        if valid.sum() < max(7, .40 * len(src)):
            return None
        for pts in (src[valid], dst[valid]):
            if cv2.contourArea(cv2.convexHull(pts)) < .007 or min(np.ptp(pts, axis=0)) < .075:
                return None
        scale = float(np.hypot(*matrix[:2, 0]))
        roll = math.degrees(math.atan2(matrix[1, 0], matrix[0, 0]))
        if not .35 < scale < 3 or abs(roll) > 35:
            return None
        return self.alignment(np.vstack((matrix, [0., 0., 1.])), src[valid], dst[valid],
                              size, tolerance, scale_limit, local=True, flipped=flipped)

    def projective(self, src, dst, size, tolerance, scale_limit, learned=False, flipped=False):
        if learned:
            src, dst = distinct_pairs(src, dst, size)
            if len(src) < MIN_LEARNED_INLIERS:
                return None
        error = max(2., (.0085 if learned else .004) * max(size))
        method = cv2.USAC_MAGSAC if learned else cv2.RANSAC
        transform, keep = cv2.findHomography(src * size, dst * size, method, error, maxIters=3000)
        if transform is None or keep is None:
            return None
        keep = keep.ravel().astype(bool)
        minimum = max(MIN_LEARNED_INLIERS, .125 * len(src)) if learned else max(9, .39 * len(src))
        if keep.sum() < minimum:
            return None
        ref_hits, live_hits = src[keep], dst[keep]
        coverage = min(cv2.contourArea(cv2.convexHull(p)) for p in (ref_hits, live_hits))
        spread = min(np.ptp(p, axis=0).min() for p in (ref_hits, live_hits))
        if coverage < (.04 if learned else .023) or spread < .13:
            return None
        return self.alignment(transform, ref_hits, live_hits, size, tolerance, scale_limit, flipped=flipped)

    def alignment(self, transform, ref_hits, live_hits, size, tolerance, scale_limit, local=False, flipped=False):
        if self.subject is not None:
            subject_hits = ref_hits.copy()
            if flipped:
                subject_hits[:, 0] = 1 - subject_hits[:, 0]
            inside = ((subject_hits >= self.subject[:2]) & (subject_hits <= self.subject[2:])).all(1)
            if inside.sum() < 4:
                return None
        markers = np.vstack((FRAME, FRAME_CENTER, self.landmarks))
        projected = cv2.perspectiveTransform((markers * size)[None], transform)[0] / size
        quad, point = projected[:4], projected[4]
        if (not np.isfinite(projected[:5]).all() or not cv2.isContourConvex(quad) or
                not .035 < cv2.contourArea(quad, oriented=True) < 3.2 or np.abs(projected[:5]).max() > 3):
            return None
        measured = landmark_scale(ref_hits, live_hits, size)
        result = frame_error(quad, point, size[0] / size[1], len(ref_hits), measured, tolerance, scale_limit, local)
        # 仅在固定地标附近同时有两图的内点支持时，才显示它在镜头中的落点。
        anchors = []
        for i, (ref, live) in enumerate(zip(self.landmarks, projected[5:])):
            near_ref = np.linalg.norm((ref_hits - ref) * size, axis=1)
            near_live = np.linalg.norm((live_hits - live) * size, axis=1)
            if np.any((near_ref < .12 * min(size)) & (near_live < .16 * min(size))):
                anchors.append({"id": i, "current": live.tolist()})
        if self.far_bounds is not None and not flipped:
            bounds = self.far_bounds
            inside = ((ref_hits >= bounds[:2]) & (ref_hits <= bounds[2:])).all(axis=1)
            far_scale = landmark_scale(ref_hits[inside], live_hits[inside], size)
            if far_scale is not None:
                x, y, r, b = bounds
                region = np.float32([[x,y], [r,y], [r,b], [x,b]])
                mapped = cv2.perspectiveTransform((region * size)[None], transform)[0] / size
                result.update(farScale=far_scale, farQuad=mapped.tolist())
        return result | {"anchors": anchors, "trackingQuad": quad.tolist()}


class SceneAnchor:
    """把首次确认的中心钉在一张固定关键帧上，而非每次跨图匹配都换落点。

    后续光流始终回到同一关键帧核验，避免逐帧累乘造成漂移。只使用人物之外
    的角点，并要求双向光流、分散内点和重投影同时通过；失效交回重新定锚。
    """
    @classmethod
    def create(cls, gray, mask, result):
        if not hasattr(cv2, "calcOpticalFlowPyrLK"):
            return None
        points = cv2.goodFeaturesToTrack(gray, maxCorners=180, qualityLevel=.015,
                                        minDistance=max(7, min(gray.shape)*.025), mask=mask, blockSize=5)
        if points is None or len(points) < 16:
            return None
        self = cls()
        self.gray = gray.copy()
        self.points = points.astype(np.float32)
        self.predicted = self.points.copy()
        self.base = result
        self.local = result["quad"] is None
        self.relative_zoom = None
        self.scale_checks = 0
        self.matrix = np.eye(3)
        self.size = np.float32([gray.shape[1], gray.shape[0]])
        self.far_misses = 0
        self.far_mask = None
        if result.get("farQuad") is not None:
            polygon = np.int32(np.rint(np.asarray(result["farQuad"]) * self.size))
            region = np.zeros_like(mask)
            cv2.fillConvexPoly(region, polygon, 255)
            self.far_mask = region
        self.markers = np.float32([*(result.get("trackingQuad") or result["quad"]), result["current"],
                                  *[a["current"] for a in result["anchors"]]])
        return self

    def measure(self, gray, mask, tolerance, scale_limit):
        if gray.shape != self.gray.shape:
            return None
        parameters = dict(winSize=(25, 25), maxLevel=3,
                          criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 24, .015),
                          flags=cv2.OPTFLOW_USE_INITIAL_FLOW)
        try:
            moved, forward, error = cv2.calcOpticalFlowPyrLK(self.gray, gray, self.points, self.predicted.copy(), **parameters)
            if moved is None or forward is None:
                return None
            returned, backward, _ = cv2.calcOpticalFlowPyrLK(gray, self.gray, moved, self.points.copy(), **parameters)
        except cv2.error:
            return None
        if returned is None or backward is None:
            return None
        src, dst = self.points.reshape(-1, 2), moved.reshape(-1, 2)
        finite = np.isfinite(dst).all(1)
        valid = finite & forward.ravel().astype(bool) & backward.ravel().astype(bool)
        valid &= np.linalg.norm(returned.reshape(-1, 2) - src, axis=1) < .9
        valid &= error.ravel() < 24
        pixels = np.rint(np.where(np.isfinite(dst), dst, 0)).astype(int)
        inside = (pixels >= 0).all(1) & (pixels < self.size).all(1)
        valid &= inside
        valid[inside] &= mask[pixels[inside, 1], pixels[inside, 0]] != 0
        if valid.sum() < max(14, .22*len(src)):
            return None
        src, dst = src[valid], dst[valid]
        matrix, keep = cv2.findHomography(src, dst, cv2.RANSAC, 1.7, maxIters=700, confidence=.995)
        if matrix is None or keep is None:
            return None
        keep = keep.ravel().astype(bool)
        if keep.sum() < max(12, .65*len(src)):
            return None
        src, dst = src[keep], dst[keep]
        for points in (src/self.size, dst/self.size):
            if cv2.contourArea(cv2.convexHull(points)) < .025 or np.ptp(points, axis=0).min() < .13:
                return None
        residual = np.linalg.norm(cv2.perspectiveTransform(src[None], matrix)[0]-dst, axis=1)
        if np.median(residual) > .65:
            return None
        markers = cv2.perspectiveTransform((self.markers*self.size)[None], matrix)[0]/self.size
        if (not np.isfinite(markers).all() or not cv2.isContourConvex(markers[:4]) or
                not .035 < cv2.contourArea(markers[:4], oriented=True) < 3.2 or np.abs(markers[:5]).max() > 3):
            return None
        old = cv2.perspectiveTransform((self.markers*self.size)[None], self.matrix)[0]/self.size
        # 不追逐亚像素光流噪声；真实的平移、旋转、变焦仍由整组标记的位移触发。
        movement = np.linalg.norm((markers[:5]-old[:5])*self.size, axis=1).max()
        if movement < .7:
            matrix, markers = self.matrix, old
        else:
            self.matrix = matrix
        self.predicted = cv2.perspectiveTransform(self.points, matrix)
        measured = landmark_scale(src/self.size, dst/self.size, self.size)
        self.relative_zoom = measured
        zoom = self.base["zoom"] * measured if self.base["zoom"] is not None and measured is not None else None
        result = frame_error(markers[:4], markers[4], self.size[0]/self.size[1], len(src), zoom,
                             tolerance, scale_limit, local=self.local)
        # 如无运动，锁定的倍率也不跟随光流数值噪声变化。
        if movement < .7 and hasattr(self, "last_zoom"):
            result = frame_error(markers[:4], markers[4], self.size[0]/self.size[1], len(src), self.last_zoom,
                                 tolerance, scale_limit, local=self.local)
        self.last_zoom = result["zoom"]
        if self.far_mask is not None:
            xy = np.rint(src).astype(int)
            within = self.far_mask[xy[:, 1], xy[:, 0]] != 0
            relative = landmark_scale(src[within]/self.size, dst[within]/self.size, self.size)
            if relative is not None:
                result["farScale"] = self.base["farScale"] * relative
        anchors = [{"id": a["id"], "current": p.tolist()} for a, p in zip(self.base["anchors"], markers[5:])]
        return result | {"anchors": anchors, "trackingQuad": markers[:4].tolist()}

    def accept_scale(self, candidate, tracked, tolerance, scale_limit):
        if not candidate or not candidate["tracking"] or candidate["zoom"] is None or self.relative_zoom is None:
            return tracked
        candidate_quad = candidate.get("trackingQuad") or candidate["quad"]
        if candidate_quad is None:
            return tracked
        points = np.float32([*candidate_quad, candidate["current"]])
        anchored = np.float32([*tracked["trackingQuad"], tracked["current"]])
        if np.max(np.linalg.norm((points-anchored)*self.size, axis=1)) > .035*min(self.size):
            return tracked
        recovered = frame_error(anchored[:4], anchored[4], self.size[0]/self.size[1], tracked["inliers"],
                                candidate["zoom"], tolerance, scale_limit, local=self.local)
        if recovered["zoom"] is None:
            return tracked
        self.base = self.base | {"zoom": candidate["zoom"]/self.relative_zoom}
        self.last_zoom = recovered["zoom"]
        return tracked | recovered


def distinct_pairs(src, dst, size):
    keep = np.unique(np.rint(src * size / 4), axis=0, return_index=True)[1]
    src, dst = src[keep], dst[keep]
    keep = np.unique(np.rint(dst * size / 4), axis=0, return_index=True)[1]
    return src[keep], dst[keep]


def frame_error(quad, point, aspect, inliers, measured_scale, tolerance=CENTER_TOLERANCE,
                scale_limit=SCALE_TOLERANCE, local=False):
    metric = np.array([aspect, 1.])
    measured = quad * metric
    vec = (measured[1] - measured[0] + measured[2] - measured[3]) / 2
    roll = math.atan2(vec[1], vec[0])
    zoom = math.sqrt(cv2.contourArea(np.float32(quad)) / cv2.contourArea(FRAME))
    c, s = math.cos(roll), math.sin(roll)
    expected = ((FRAME - .5) * metric) @ np.array([[c, s], [-s, c]]) * zoom + measured.mean(0)
    skew = float(np.max(np.linalg.norm(measured - expected, axis=1)))
    roll = math.degrees(roll)
    if measured_scale is not None and abs(math.log(measured_scale / zoom)) > math.log(1.16):
        measured_scale = None
    # 使用实际画幅的距离，竖屏和横屏看到的金圈边界与通过条件相同。
    aspect = float(aspect)
    dx, dy = (point - FRAME_CENTER) * [aspect, 1.]
    distance = math.hypot(dx, dy) / min(aspect, 1.)
    tracking = abs(roll) <= 15 and skew <= .12
    centered = tracking and distance <= tolerance
    scale_ok = measured_scale is not None and 1 / scale_limit <= measured_scale <= scale_limit
    if not tracking:
        mode = "coarse"
        if abs(roll) / 15 > skew / .12:
            control, cue = "roll", "将相机顺时针转正" if roll > 0 else "将相机逆时针转正"
        else:
            control, cue = "angle", "移到参考地标的同一侧，调整拍摄角度"
    elif centered:
        if measured_scale is None:
            mode, control = "fine", "coverage"
            cue = ("圆心已重合，请让两个相隔较远的固定地标同时入镜" if local else
                   "圆心已重合，请微调拍摄位置，让近处和远处的地标同时对齐")
        elif not scale_ok:
            mode, control, cue = "scale", "scale", "中心已对齐，正在按地标间距匹配景别"
        else:
            mode, control, cue = "aligned", "center", "地标中心和景别已对齐"
    else:
        mode = "coarse" if distance > 2 * tolerance else "fine"
        if abs(dx) > abs(dy):
            control, cue = "x", "镜头向右转" if dx > 0 else "镜头向左转"
        else:
            control, cue = "y", "镜头向下俯" if dy > 0 else "镜头向上仰"
        if local:
            control = "local"
    return {"ok": centered and scale_ok, "centerOk": centered, "scaleOk": scale_ok,
            "tracking": tracking, "mode": mode, "message": cue, "control": control,
            "current": point.tolist(), "target": FRAME_CENTER.tolist(), "centerDistance": round(distance, 5),
            "quad": None if local else quad.tolist(), "inliers": int(inliers),
            "zoom": round(measured_scale, 4) if measured_scale is not None else None, "roll": round(roll, 1)}

