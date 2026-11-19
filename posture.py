"""用人物框比对大致站位，在取景坐标中比较固定参考关节与实时关节。"""
import math

import numpy as np

from people import NAMES, area, center, overlap


REF_CONFIDENCE = .52
LIVE_CONFIDENCE = .36
POSITION_IOU = .45
POSITION_RELEASE_IOU = .35
JOINTS = (5, 6, 11, 12, 0, 7, 8, 9, 10, 13, 14, 15, 16)


def points_json(xy, mask):
    return [p.round(4).tolist() if visible else None for p, visible in zip(xy, mask)]


def orientation(a, b):
    return math.degrees(math.atan2(b[1] - a[1], b[0] - a[0]))


def direction(delta):
    x, y = delta
    mag = max(abs(x), abs(y))
    horizontal = ("右" if x > 0 else "左") if abs(x) >= mag * .45 else ""
    vertical = ("下" if y > 0 else "上") if abs(y) >= mag * .45 else ""
    return horizontal + vertical


def measure_position(reference, current, min_overlap=POSITION_IOU):
    if reference is None or current is None:
        return None
    target, actual = center(reference), center(current)
    shift = target - actual
    size = np.maximum(reference[2:4] - reference[:2], .001)
    ratio = math.sqrt(max(area(current), 1e-8) / max(area(reference), 1e-8))
    iou = overlap(reference, current)
    axis = int(np.argmax([abs(shift[0]) / size[0], abs(shift[1]) / size[1], abs(math.log(ratio))]))
    actions = ["请模特向画面" + ("右" if shift[0] > 0 else "左") + "移，靠近目标框",
               "请让人物框向画面" + ("下" if shift[1] > 0 else "上") + "移，靠近目标框",
               "请模特离镜头远一点，让人物框变小" if ratio > 1 else "请模特靠近镜头，让人物框变大"]
    ok = iou >= min_overlap
    return {"ok": ok, "mode": "aligned" if ok else "coarse" if iou < .2 else "fine",
            "control": ("x", "y", "size")[axis], "message": "人物已进入目标框" if ok else actions[axis],
            "target": target.tolist(), "current": actual.tolist(), "ratio": round(ratio, 3), "iou": round(iou, 4),
            "targetBox": reference[:4].tolist(), "box": current[:4].tolist()}


def measure_subject_center(reference_box, person, aspect, tolerance, target=None):
    if reference_box is None or person is None:
        return None
    basis = "box"
    wanted, actual = center(reference_box), center(person.box)
    if target is not None and target.basis == "torso":
        live = target.anchor(person, person.visible(LIVE_CONFIDENCE), "torso")
        if live is not None:
            basis = "torso"
            wanted = target.anchor(target.reference, target.visible, "torso")[0]
            actual = live[0]
    delta = (actual - wanted) * [aspect, 1.] / min(aspect, 1.)
    distance = float(np.linalg.norm(delta))
    ok = distance <= tolerance
    message = ("人物位置已接近，请保持片刻" if ok else
               f"人物位置偏{direction(delta)}，请对照浅金色参考框调整")
    return {"ok": ok, "basis": basis, "target": wanted.tolist(), "current": actual.tolist(),
            "centerDistance": round(distance, 4), "tolerance": tolerance, "message": message,
            "targetBox": reference_box[:4].tolist(), "box": person.box[:4].tolist()}


class PoseTarget:
    def __init__(self, person, width, height):
        self.metric = np.array([width / height, 1.])
        self.reference = person
        self.visible = person.visible(REF_CONFIDENCE)
        self.required = [j for j in JOINTS if self.visible[j]]
        self.basis = None
        self.angles = []
        if self.visible.sum() < 4:
            raise ValueError("参考人物的可见关节太少。请选择画面中更清楚的主角或人像参考照。")
        for basis in ("torso", "shoulders", "face", "box"):
            anchor = self.anchor(person, self.visible, basis)
            if anchor is not None:
                self.basis = basis
                _, self.scale = anchor
                break
        if self.basis is None:
            raise ValueError("参考人物尺度太小，无法稳定定位。")
        for pair, name, limit in [((1, 2), "头部", 14.), ((5, 6), "双肩", 14.)]:
            a, b = pair
            length = np.linalg.norm((person.xy[a] - person.xy[b]) * self.metric)
            if self.visible[a] and self.visible[b] and length * height >= 7 and length > .18 * self.scale:
                self.angles.append((a, b, name, limit))
        if len(self.required) < 3 and not self.angles:
            raise ValueError("参考人物太小或遮挡太多，无法建立可靠的目标姿势。")
        self.keypoints = np.zeros_like(self.visible)
        self.keypoints[self.required] = True
        for a, b, _, _ in self.angles:
            self.keypoints[[a, b]] = True
        self.required.extend(j for j in (1, 2) if self.keypoints[j])
        self.goals = person.xy.copy()
        self.points = points_json(self.goals, self.keypoints)

    def anchor(self, person, visible, basis):
        xy = person.xy
        if basis == "torso" and all(visible[[5, 6, 11, 12]]):
            top, bottom = xy[[5, 6]].mean(0), xy[[11, 12]].mean(0)
            origin, vector = (top + bottom) / 2, top - bottom
        elif basis == "shoulders" and all(visible[[5, 6]]):
            origin, vector = xy[[5, 6]].mean(0), xy[5] - xy[6]
        elif basis == "face" and all(visible[[0, 1, 2]]):
            origin, vector = xy[[1, 2]].mean(0), (xy[1] - xy[2]) * 2.4
        elif basis == "box" and visible.sum() >= 4:
            origin = center(person.box)
            vector = .44 * (person.box[2:4] - person.box[:2])
        else:
            return None
        scale = float(np.linalg.norm(vector * self.metric))
        if scale < .018:
            return None
        return origin, scale

    def distance_check(self, person, bg, tolerance):
        if person is None or not bg or not bg.get("tracking") or not bg.get("farScale"):
            return None
        # Boxes vary with arms and legs. Compare the same anatomical span in both frames.
        visible = person.visible(LIVE_CONFIDENCE)
        for basis in ("torso", "shoulders", "face"):
            ref = self.anchor(self.reference, self.visible, basis)
            live = self.anchor(person, visible, basis)
            if ref is None or live is None:
                continue
            person_scale = live[1] / ref[1]
            ratio = person_scale / bg["farScale"]
            aligned = 1 / tolerance <= ratio <= tolerance
            direction = "hold" if aligned else "back" if ratio > 1 else "forward"
            return {"ok": aligned, "ratio": round(ratio, 3), "basis": basis,
                    "personScale": round(person_scale, 3), "farScale": round(bg["farScale"], 3),
                    "direction": direction}
        return None

    def compare(self, person, min_overlap=POSITION_IOU, *, local_pose=False):
        if person is None:
            return None, None
        position = measure_position(self.reference.box, person.box, min_overlap)
        return position, self.compare_pose(person, local_pose=local_pose)

    def compare_pose(self, person, *, local_pose=False):
        if person is None:
            return None
        visible = person.visible(LIVE_CONFIDENCE) & self.keypoints
        pose = {"ok": False, "actual": points_json(person.xy, visible), "target": self.points, "joint": None}
        goals = self.goals
        pose_scale = self.scale
        if local_pose:
            ref = self.anchor(self.reference, self.visible, self.basis)
            live = self.anchor(person, person.visible(LIVE_CONFIDENCE), self.basis)
            if live is None:
                return pose | {"message": "请让主角的脸、肩部或躯干清楚入镜"}
            origin, pose_scale = live
            factor = pose_scale / self.scale
            offset = origin - factor * ref[0]
            goals = factor * self.goals + offset
            pose.update(target=points_json(goals, self.keypoints),
                        transform={"scale": factor, "offset": offset.tolist()})
        issues, missing = [], []
        for j in self.required:
            if not visible[j]:
                missing.append(j)
                continue
            delta = (goals[j] - person.xy[j]) * self.metric
            tolerance = .23 if j in (0, 1, 2, 5, 6, 11, 12) else .32
            error = np.linalg.norm(delta) / max(.012, tolerance * pose_scale)
            issues.append((error, j, f"请模特将{NAMES[j]}向画面{direction(delta)}移，靠近目标点"))
        for a, b, name, limit in self.angles:
            if not (visible[a] and visible[b]):
                missing.append(a if not visible[a] else b)
                continue
            wanted = orientation(self.goals[a] * self.metric, self.goals[b] * self.metric)
            current = orientation(person.xy[a] * self.metric, person.xy[b] * self.metric)
            diff = (wanted - current + 90) % 180 - 90
            side = "右" if diff > 0 else "左"
            hint = f"请模特将头顶向画面{side}稍倾" if name == "头部" else "请模特调整双肩倾斜方向，贴合目标肩线"
            issues.append((abs(diff) / limit, a, hint))
        if missing:
            j = missing[0]
            return pose | {"joint": j, "message": f"请让参考中可见的{NAMES[j]}清楚入镜"}
        if not issues:
            return pose | {"message": "请让模特的脸或肩臂清楚入镜"}
        worst, joint, message = max(issues, key=lambda entry: entry[0])
        if worst <= 1:
            return pose | {"ok": True, "message": "关键点已进入参考姿势范围"}
        return pose | {"joint": joint, "message": message}

    def framing_hint(self, person):
        """背景尚未定位时，用可见的人像解释取景差异，不参与机位通过判定。"""
        if person is None:
            return None
        ref = self.anchor(self.reference, self.visible, "face")
        now = self.anchor(person, person.visible(LIVE_CONFIDENCE), "face")
        if ref is None or now is None or ref[1] < .06:
            return None
        if now[1] > 1.35 * ref[1]:
            return "当前人像比参考大，请先拉开相机与人的距离，让人物两侧的固定背景入镜"
        if now[0][1] > ref[0][1] + .18:
            return "当前脸在画面里偏低，请将镜头稍向下俯，并对照参考露出两侧的背景"
        return None

