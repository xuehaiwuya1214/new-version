"""参考构图导拍及旧客户端兼容流程。"""
import math
from threading import Lock

import numpy as np

from background import Background, CENTER_RELEASE, CENTER_TOLERANCE, SCALE_RELEASE, SCALE_TOLERANCE
from background_shapes import background_outline
from building import reference_building_async
from inference import Prepared, synchronous
from outline import reference_outline_async
from people import EDGES, center, choose
from posture import POSITION_IOU, POSITION_RELEASE_IOU, PoseTarget, measure_position, measure_subject_center


STAGES = ("background", "framing", "position", "pose", "ready")
GUIDED_STAGES = STAGES[:-1]
COMPOSITION_STEPS = ("background", "distance", "framing", "recenter", "position", "pose")
HOLD_SECONDS = (.32, .32, .35, .50)
COMPOSITION_HOLDS = (.32, .5, .32, .32, .35, .5)
SUBJECT_CENTER_TOLERANCE = .10
SUBJECT_CENTER_RELEASE = .15
DISTANCE_TOLERANCE = 1.14
DISTANCE_RELEASE = 1.20
HOLD_HINTS = ("白点已进入中心环，请稳住镜头", "建筑位置和大小已接近参考，请稳住相机",
              "人物已进入目标框，请保持站位", "动作已对齐，请保持片刻")


class Guide(Prepared):
    async def prepare(self, models, frame, scene_hint="", tap=None, *, composition=False, far_point=None):
        self.composition = composition
        self.steps = COMPOSITION_STEPS if composition else GUIDED_STAGES
        self.stages = (*self.steps, "ready")
        self.framing_step = self.steps.index("framing")
        self.position_basis = None
        self.position_step = self.steps.index("position")
        self.pose_step = self.steps.index("pose")
        self.direction = self.direction_since = self.initial_ratio = None
        h, w = frame.shape[:2]
        self.aspect = w / h
        self.models = models
        self.scene_hint = scene_hint
        # 部分参考照像素本身就是横躺/倒置的。仅常规方向无法建模时试转向，
        # 选定的方向也用于实时人体推理，所有 UI 和背景坐标仍在原画面中。
        reason = "参考照中未能定位主角，可点选照片里的人物。"
        candidates = []
        upright_boxes = []
        for turns in (0, 1, 3, 2):
            boxes, person = await models.observe_async(frame, turns, survey=True, focus=tap, tap=tap)
            if turns == 0:
                upright_boxes = boxes
            if person is None:
                continue
            try:
                target = PoseTarget(person, w, h)
            except ValueError as exc:
                reason = str(exc)
                continue
            quality = float(person.scores[target.visible].sum())
            candidates.append((quality, turns, boxes, target))
            if turns == 0:
                break
        self.turns, boxes, self.target = 0, upright_boxes, None
        if candidates:
            _, self.turns, boxes, self.target = max(candidates, key=lambda item: item[0])
        self.reference_box = self.target.reference.box if self.target else choose(boxes, tap=tap)
        self.background = await Background.create(frame, boxes, models.foreground_async)
        building = await reference_building_async(frame, boxes, scene_hint)
        self.background.set_subject(building)
        far_bounds = None
        if composition:
            if far_point is not None:
                x, y = far_point
                far_bounds = [max(0, x-.18), max(0, y-.18), min(1, x+.18), min(1, y+.18)]
            elif building and building.get("kind") == "tower" and self.background.subject is not None:
                far_bounds = building["bounds"]
            if far_bounds is not None:
                self.background.far_bounds = np.asarray(far_bounds, dtype=float)
        points = self.target.points if self.target else []
        self.reference = {"width": w, "height": h,
                          "centerTolerance": .12 if composition else CENTER_TOLERANCE,
                          "workflow": "composition" if composition else "legacy",
                          "farBounds": far_bounds,
                          "aimPoint": [.5, .5],
                          "landmarks": self.background.landmarks.tolist(),
                          "buildingOutline": building,
                          "backgroundOutline": background_outline(frame, boxes),
                          "box": self.reference_box[:4].tolist() if self.reference_box is not None else None,
                          "anchor": center(self.reference_box).tolist() if self.reference_box is not None else None,
                          "boxes": [b[:4].tolist() for b in boxes],
                          "points": points,
                          "outline": await reference_outline_async(frame, self.reference_box, points, models.foreground_async, self.turns),
                          "subjectHint": "" if self.target else reason,
                          "edges": EDGES, "viewpoint": scene_hint, "canMatchBackground": self.background.usable}
        self.lock = Lock()
        self.stage = 0
        self.skipped = frozenset()
        # 粗对准只做一次，变焦后由 recenter 使用新画面复核。
        self.completed = set()
        self.since = None
        self.last_seq = -1
        self.last_time = None
        self.source_id = None
        self.aim_sample = None
        self.last_box = None
        self.last_seen = None
        self.pending_tap = None
        self.pinned = False

    __init__ = synchronous(prepare)

    def next_required(self, start):
        return next((i for i in range(start, len(self.steps))
                     if self.stages[i] not in self.skipped and self.stages[i] not in self.completed),
                    len(self.steps))

    def steady_background(self, bg, now):
        """稳定的地标测量才触发变焦。"""
        if self.stage not in (0, self.framing_step) or not bg or not bg["tracking"] or (self.stage == 0 and not bg["centerOk"]):
            self.aim_sample = None
            return
        x, y = bg["current"]
        zoom, roll = bg["zoom"], bg["roll"]
        if self.aim_sample:
            _, old_x, old_y, old_zoom, old_roll = self.aim_sample
            scale_drift = zoom is not None and old_zoom is not None and abs(math.log(zoom / old_zoom)) > .06
            if scale_drift or abs(roll - old_roll) > 3:
                self.aim_sample = None
        if self.aim_sample is None:
            self.aim_sample = now, x, y, zoom, roll
            # 第一关只确认中心。景别或轻微倾角的测量波动不能重置对准计时；
            # 第二关仍需稳定的原始几何关系，才能自动变焦或确认景别。
            if self.stage == self.framing_step:
                self.since = None
        if self.stage == 0 or bg["scaleOk"] or zoom is None:
            return
        elapsed = now - self.aim_sample[0]
        if elapsed >= HOLD_SECONDS[0]:
            bg["zoomFactor"] = round(1 / zoom, 4)
        else:
            bg.update(control="steady", mode="hold", message="中心已对准，稳住镜头后会自动匹配轮廓大小")
            return elapsed / HOLD_SECONDS[0]

    def distance_hint(self, distance, now):
        if self.target is None:
            return "search", "请点选清晰的参考主角，或跳过距离"
        if self.reference["farBounds"] is None:
            return "search", "请在参考照标记远景，或跳过距离"
        if distance is None:
            self.direction = None
            return "search", "请让主角和已选远景清楚入镜，或跳过距离"
        direction = distance["direction"]
        if direction != self.direction:
            self.direction = direction
            self.direction_since = now
            self.initial_ratio = distance["ratio"]
            return "hold", "保持坐位或靠点，正在核对人物与远景比例"
        if direction == "hold":
            return "hold", "人物与远景比例合适，请稳住手机"
        if now - self.direction_since < .32:
            return "hold", "保持机位，正在确认移动方向"
        if now - self.direction_since > 6 and abs(math.log(distance["ratio"])) >= abs(math.log(self.initial_ratio)) - .04:
            return "search", "请先停下，确认地标在人物远后方或跳过距离"
        return "adjust", ("摄影师请后退一点，模特保持原位" if direction == "back"
                          else "摄影师请前进一点，模特保持原位")

    async def analyze_async(self, frame, seq, now, tap=None, skipped=None, source_id=None):
        if seq <= self.last_seq:
            raise ValueError("画面已过期，请使用递增帧号。")
        h, w = frame.shape[:2]
        if abs(w / h / self.aspect - 1) > .018:
            raise ValueError("取景画幅与参考照不同，请按参考照的宽高比取景。")
        if source_id is not None and source_id != self.source_id:
            self.source_id = source_id
            self.completed.clear()
            self.direction = None
            if hasattr(self.background, "reset_tracking"):
                self.background.reset_tracking()
            self.stage, self.since, self.aim_sample = 0, None, None
            self.last_box, self.last_seen, self.pending_tap, self.pinned = None, None, None, False
        if skipped is not None:
            requested = frozenset(skipped)
            if not requested.issubset(self.steps):
                raise ValueError("可跳过的步骤与当前流程不符，请重新载入参考照。")
            if self.skipped - requested:
                # 恢复的步骤必须从新画面重新确认。
                self.stage, self.since, self.aim_sample = 0, None, None
                if hasattr(self.background, "reset_tracking"):
                    self.background.reset_tracking()
                self.completed.difference_update(self.skipped - requested)
                self.direction = None
            self.skipped = requested
        if self.last_time is not None and now - self.last_time > getattr(self, "max_sample_gap", 1.6):
            self.stage, self.since, self.aim_sample = 0, None, None
            if hasattr(self.background, "reset_tracking"):
                self.background.reset_tracking()
            self.direction = None
            self.completed.discard("distance")
        self.last_seq, self.last_time = seq, now
        if tap is not None:
            self.pinned = True
            self.pending_tap = tap
            self.last_box, self.since = None, None
            if self.composition:
                self.completed.discard("distance")
                self.direction = None
            self.stage = min(self.stage, 1 if self.composition else 2)
        if self.stages[self.stage] in self.skipped or self.stages[self.stage] in self.completed:
            self.stage, self.since = self.next_required(self.stage), None
        boxes, person = [], None
        if len(self.skipped) < len(self.steps):
            focus = self.pending_tap
            focus_box = self.last_box if self.last_box is not None else self.reference_box
            if focus is None and focus_box is not None:
                focus = center(focus_box)
            boxes, person = await self.models.observe_async(frame, self.turns, focus=focus, tap=self.pending_tap,
                                                            previous=self.last_box, target=self.reference_box)
        box = person.box if person is not None else None
        if box is not None:
            self.last_box, self.last_seen, self.pending_tap = box, now, None
        elif not self.pinned and self.last_seen is not None and now - self.last_seen > 1.2:
            self.last_box = None
        bg = None
        background_steps = {"background", "distance", "framing", "recenter"} if self.composition else {"background", "framing"}
        if not background_steps.issubset(self.skipped):
            # 进入容差后，在本次连续确认期间使用稍宽的退出阈值，避免边缘抖动
            # 反复归零；丢失、镜像或移出退出范围会使 since 清空，下帧重新进入。
            center_captured = self.stage > 0 or self.since is not None
            tolerance = CENTER_RELEASE if center_captured else CENTER_TOLERANCE
            if self.composition:
                if self.stage == 0:
                    tolerance = .15 if self.since is not None else .12
                else:
                    tolerance = CENTER_RELEASE if self.stage >= self.position_step else CENTER_TOLERANCE
            self.background.retry_far = (self.composition and "distance" not in self.skipped
                                         and self.stages[self.stage] in ("distance", "framing", "recenter"))
            bg = await self.background.measure_async(frame, boxes, tolerance,
                                                     SCALE_RELEASE if self.stage > self.framing_step else SCALE_TOLERANCE)
        if self.composition and bg and "distance" not in self.skipped:
            # Use the same distant surface for the distance ratio and the zoom command.
            scale = bg.get("farScale")
            limit = SCALE_RELEASE if self.stage > self.framing_step else SCALE_TOLERANCE
            bg = dict(bg, zoom=scale, scaleOk=scale is not None and 1 / limit <= scale <= limit)
        if self.composition:
            tolerance = SUBJECT_CENTER_RELEASE if self.stage > self.position_step else SUBJECT_CENTER_TOLERANCE
            position = measure_subject_center(self.reference_box, person, self.aspect, tolerance, self.target)
            basis = position["basis"] if position else None
            if self.stage == self.position_step and self.position_basis != basis:
                self.since = None
            self.position_basis = basis
            pose = self.target.compare_pose(person, local_pose=True) if self.target else None
        else:
            min_overlap = POSITION_RELEASE_IOU if self.stage > self.position_step else POSITION_IOU
            if self.target:
                position, pose = self.target.compare(person, min_overlap)
            else:
                position, pose = measure_position(self.reference_box, box, min_overlap), None
        aim = dict(bg, ok=bg["centerOk"]) if bg else None
        # 倍率与中心分别核验。
        framing = dict(bg, ok=bool(bg["tracking"] and bg["scaleOk"])) if bg else None
        distance = None
        if self.composition:
            if self.target:
                limit = DISTANCE_RELEASE if "distance" in self.completed else DISTANCE_TOLERANCE
                distance = self.target.distance_check(person, bg, limit)
            if (self.stages[self.stage] in ("framing", "recenter") and
                    "distance" in self.completed and not (distance and distance["ok"])):
                self.completed.remove("distance")
                self.since = self.aim_sample = None
                self.direction = None
            values = (aim, distance, framing, aim, position, pose)
            holds = COMPOSITION_HOLDS
        else:
            values = (aim, framing, position, pose)
            holds = HOLD_SECONDS
        checks = [None if stage in self.skipped else stage in self.completed or value is not None and bool(value["ok"])
                  for stage, value in zip(self.steps, values)]
        earliest = next((i for i, ok in enumerate(checks) if ok is False), len(self.steps))
        if earliest < self.stage:
            self.stage, self.since = earliest, None
        fraction = self.steady_background(bg, now) or 0.
        if self.stage < len(self.steps) and checks[self.stage]:
            if self.since is None:
                self.since = now
            fraction = min(1., (now - self.since) / holds[self.stage])
            if fraction >= 1:
                if self.stage == 0 or self.stages[self.stage] == "distance":
                    self.completed.add(self.stages[self.stage])
                self.stage = self.next_required(self.stage + 1)
                self.since = now if not self.composition and self.stage < len(self.steps) and checks[self.stage] else None
                fraction = 0.
        else:
            self.since = None
        result = {"seq": seq, "stage": self.stages[self.stage], "checks": checks,
                  "completed": [stage for stage in self.steps if stage in self.completed],
                  "skipped": [stage for stage in self.steps if stage in self.skipped], "hold": round(fraction, 2),
                  "ready": self.stage == len(self.steps), "boxes": [b[:4].tolist() for b in boxes],
                  "selected": box[:4].tolist() if box is not None else None,
                  "background": bg, "distance": distance, "position": position if "position" not in self.skipped else None,
                  "pose": pose if self.stage >= self.pose_step and "pose" not in self.skipped else None}
        if self.stage == len(self.steps):
            msg = "可以拍摄，保持现在的机位和动作"
            if len(self.skipped) == len(self.steps):
                msg = "已跳过全部引导，可以直接拍摄"
            return result | {"mode": "ready", "message": msg}
        active = values[self.stage]
        building = self.reference["buildingOutline"]
        if self.stage == 0 and checks[0]:
            return result | {"mode": "hold", "message": HOLD_HINTS[0]}
        if self.stages[self.stage] == "distance":
            mode, msg = self.distance_hint(distance, now)
            return result | {"mode": mode, "message": msg}
        if self.stages[self.stage] == "recenter":
            if not bg or not bg["tracking"]:
                msg = "重新对准参考地标，等待新画面复核"
            elif bg["centerOk"]:
                msg = "景别调整后中心已对准，请稳住手机"
            else:
                msg = bg["message"]
            return result | {"mode": "hold" if checks[self.stage] else "adjust", "message": msg}
        if self.stage == self.framing_step:
            name = building["name"] if building else "地标"
            if not bg or not bg["tracking"]:
                return result | {"mode": "search", "message": f"让{name}保持入镜，等待匹配恢复后继续核对景别"}
            if bg["control"] == "steady":
                return result | {"mode": "hold", "message": "中心步骤已完成，稳住镜头以匹配景别"}
            if bg["zoom"] is None:
                return result | {"mode": "fine", "message": f"保持{name}贴近轮廓，让两侧固定景物同时入镜以核对景别"}
            if checks[self.framing_step]:
                return result | {"mode": "hold", "message": f"{name}的大小已接近参考，请稳住相机"}
            return result | {"mode": "scale", "message": f"调整倍率，让{name}的大小接近参考轮廓"}
        if self.composition and self.stages[self.stage] == "position" and position:
            return result | {"mode": "hold" if position["ok"] else "adjust", "message": position["message"]}
        if checks[self.stage]:
            hint = HOLD_HINTS[-1] if self.stages[self.stage] == "pose" else HOLD_HINTS[self.stage]
            return result | {"mode": "hold", "message": hint}
        if active is not None:
            if self.stage < 2 and active["control"] == "angle" and self.scene_hint:
                return result | {"mode": active["mode"], "message": f"请从「{self.scene_hint}」一侧取景，调整拍摄角度"}
            return result | {"mode": active.get("mode", "adjust"), "message": active["message"]}
        if self.stage == 0:
            framing = self.target.framing_hint(person) if self.target else None
            if not self.background.usable:
                msg = "参考照中可辨认的背景太少，可以跳过无法核验的背景步骤"
            elif framing and not self.composition:
                msg = framing
            elif self.scene_hint:
                msg = "请从「" + self.scene_hint + "」取景，找到地标后将白点移入中心环"
            else:
                msg = "先让参考中的地标入镜，识别后转动镜头，将白点移入中心环"
        elif self.stage == self.pose_step and self.target is None and self.reference["outline"]:
            msg = "可照白色轮廓摆动作。参考关节不足，可以跳过姿势后拍摄"
        elif self.reference_box is None or (self.stage == self.pose_step and self.target is None):
            msg = "参考照中的人物特征不足，可点选参考主角，或跳过这一步"
        elif self.pending_tap is not None:
            msg = "点选处还未识别到人物，请让模特清楚入镜或重新点选"
        elif self.composition and self.stage == self.position_step:
            msg = "请让主角清楚入镜，以便叠加当前人物框"
        elif self.stage == self.pose_step:
            msg = "请模特照着白色参考姿势入镜，以便识别实时关节"
        else:
            msg = "请模特走进浅金色目标框，有路人时可点选主角"
        return result | {"mode": "search", "message": msg}

    analyze = synchronous(analyze_async)

