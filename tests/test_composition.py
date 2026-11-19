"""Photographer distance, independent zoom, fresh recentering and local pose."""
from pathlib import Path
import sys
import io

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import app
from guide import Guide
from posture import PoseTarget, measure_subject_center
from test_guidance import portrait, shared_photos
from test_anchor_tracking import scenery, move
from background import Background


def test_distance_is_zoom_invariant_and_pose_ignores_whole_body_translation():
    ref = portrait()
    target = PoseTarget(ref, 640, 640)
    live = portrait()
    live.xy = (live.xy - .5) * .75 + .5
    bg = {"tracking": True, "farScale": .75}
    assert target.distance_check(live, bg, 1.14)["ok"]
    assert target.distance_check(ref, bg, 1.14)["direction"] == "back"
    assert target.distance_check(live, dict(bg, farScale=1), 1.14)["direction"] == "forward"
    live.xy += [.08, .04]
    assert target.compare(live, local_pose=True)[1]["ok"]
    live.xy[9] += [.12, .05]
    assert target.compare(live, local_pose=True)[1]["joint"] == 9
    live.scores[:] = .1
    assert target.distance_check(live, bg, 1.14) is None
    assert target.distance_check(ref, dict(bg, tracking=False), 1.14) is None


@pytest.mark.parametrize("aspect", [.5, 1., 2.])
def test_missing_torso_uses_both_box_centers_in_short_edge_units(aspect):
    ref, live = portrait(), portrait()
    live.box[[0, 2]] += .07 * min(aspect, 1.) / aspect

    def check(target):
        return measure_subject_center(ref.box, live, aspect, .06, target)

    target = PoseTarget(ref, 100 * aspect, 100)
    assert check(target)["basis"] == "torso" and check(target)["ok"]
    live.scores[5] = .1
    for t in (target, None):
        result = check(t)
        assert result["basis"] == "box" and not result["ok"]
        assert result["centerDistance"] == pytest.approx(.07)
    ref.scores[6] = .1
    live.scores[5] = .85
    assert check(PoseTarget(ref, 100 * aspect, 100))["basis"] == "box"
    assert measure_subject_center(ref.box, None, aspect, .06, target) is None


def test_far_region_supplies_its_own_observed_scale(scenery):
    tracker = Background(scenery, [])
    tracker.far_bounds = np.array([.12,.10,.53,.52])
    assert tracker.measure(scenery, [])["farScale"] == pytest.approx(1., abs=.01)
    moved = tracker.measure(move(scenery, x=6, scale=1.06), [])
    assert moved["farScale"] == pytest.approx(1.06, abs=.025)
    tiny = Background(scenery, [])
    tiny.far_bounds = np.array([.12,.10,.13,.11])
    assert tiny.measure(scenery, []).get("farScale") is None


def test_new_session_selects_far_landmark_and_traverses_all_six_steps():
    photo = shared_photos() / "REF_046.webp"
    if not photo.exists():
        pytest.skip("需要校园圆塔参考照")
    data = photo.read_bytes()
    with app.test_client() as client:
        reply = client.post("/api/session", data={"reference": (io.BytesIO(data), photo.name),
                            "workflow": "composition", "farX": ".5", "farY": ".35"})
        assert reply.status_code == 201, reply.data
        ref = reply.json["reference"]
        assert ref["workflow"] == "composition" and ref["farBounds"] is not None
        from app import sessions
        guide = sessions[reply.json["id"]][0]
        from app import decode
        frame = decode(data)
        stages = []
        for seq in range(15):
            result = guide.analyze(frame, seq, float(seq))
            if not stages or stages[-1] != result["stage"]:
                stages.append(result["stage"])
            if result["ready"]:
                break
        assert stages == ["background", "distance", "framing", "recenter", "position", "pose", "ready"], (stages, result)
        client.delete("/api/session/" + reply.json["id"])


def test_camera_distance_precedes_zoom_and_recenter(monkeypatch):
    person = portrait()
    frame = np.zeros((160, 160, 3), np.uint8)

    class Models:
        async def observe_async(self, *args, **kwargs):
            return [person.box], person
        async def foreground_async(self, crop):
            return np.zeros(crop.shape[:2], np.float32)

    async def no_building(*args):
        return None

    monkeypatch.setattr("guide.reference_building_async", no_building)
    guide = Guide(Models(), frame, composition=True, far_point=[.25, .25])
    person = portrait()  # Independent live observation after reference preparation.
    state = {"far": .7, "center": .10}

    async def geometry(frame, boxes, tolerance, scale_limit):
        return {"tracking": True, "centerOk": state["center"] <= tolerance, "scaleOk": True,
                "zoom": 1., "farScale": state["far"], "roll": 0., "current": [.5+state["center"],.5],
                "control":"x", "message":"镜头向右转"}

    monkeypatch.setattr(guide.background, "measure_async", geometry)
    seq = 0

    def step(**kwargs):
        nonlocal seq
        seq += 1
        return guide.analyze(frame, seq, seq * .6, **kwargs)

    assert step()["stage"] == "background"
    assert step()["stage"] == "distance"
    result = step()
    assert "后退" in result["message"] and "zoomFactor" not in result["background"]
    state["far"] = None
    assert step()["mode"] == "search"
    state["far"] = 1.3
    step()
    assert "前进" in step()["message"]
    # The composition now has the right relative proportions but is too small overall.
    state["far"] = .7
    person.xy = (portrait().xy - .5) * .7 + .5
    step()
    assert step()["stage"] == "framing"
    step()
    assert step()["background"]["zoomFactor"] == pytest.approx(1/.7, abs=.001)
    person.xy = portrait().xy
    state.update(far=1., center=.13)
    step()
    result = step()
    assert result["stage"] == "recenter" and result["message"] == "镜头向右转"
    assert step()["stage"] == "recenter"
    state["center"] = .02
    person.xy[:, 0] += .12
    step()
    for _ in range(3):
        result = step()
        assert result["stage"] == "position" and result["pose"] is None
        assert result["position"]["basis"] == "torso" and "偏右" in result["message"]
    person.xy[:, 0] -= .036  # An 8.4% offset is close enough to begin holding.
    assert step()["stage"] == "position"
    # Switching to box centers starts a fresh hold, even if both metrics pass.
    person.scores[11] = .1
    result = step()
    assert result["stage"] == "position" and result["position"]["basis"] == "box"
    assert step()["stage"] == "pose"
    person.scores[11] = .85
    person.xy[:, 0] += .05  # 13.4% stays within the pose-stage release tolerance.
    assert step()["stage"] == "pose"
    person.xy[:, 0] += .03
    assert step()["stage"] == "position"
    person.xy[:, 0] -= .08
    assert step()["stage"] == "position"
    assert step()["stage"] == "pose"
    step()
    assert step()["ready"]
    state["center"] = .2
    assert step()["stage"] == "recenter"
    state["far"] = None
    assert step()["stage"] == "distance"
    assert step(skipped=["distance"])["stage"] in ("framing", "recenter")
    assert step(source_id=2, skipped=[])["stage"] == "background"
