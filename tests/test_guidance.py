from pathlib import Path
import sys

import cv2
import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import decode, get_models
from background import Background, CENTER_RELEASE, CENTER_TOLERANCE, FRAME, frame_error
from building import reference_building
from guide import Guide
from landmarks import landmark_scale, xfeat_features
from outline import reference_outline
from people import Person, choose
from posture import PoseTarget


def portrait():
    points = np.array([[.51,.23], [.53,.22], [.49,.22], [.55,.24], [.47,.24],
                       [.59,.32], [.43,.32], [.64,.43], [.37,.44], [.70,.31], [.32,.52],
                       [.57,.55], [.45,.55], [.59,.72], [.43,.70], [.61,.91], [.41,.89]])
    return Person(np.array([.29,.16,.73,.95,.92]), points, np.full(17, .85))


def test_pose_goals_stay_in_the_reference_frame_when_the_subject_moves_and_scales():
    ref = portrait()
    target = PoseTarget(ref, 960, 720)
    current = portrait()
    current.xy += [.065, 0]
    current.box[:4] += [.065, 0, .065, 0]
    pos, pose = target.compare(current)
    assert pos["ok"] and not pose["ok"]
    assert pose["target"][9] == ref.xy[9].tolist() and pose["actual"][9] != pose["target"][9]
    current.xy = current.xy * .81 + [.17, .03]
    assert target.compare(current)[1]["target"] == pose["target"]
    current = portrait()
    current.xy[9] += [-.10, .22]
    pos, pose = target.compare(current)
    assert not pose["ok"] and pose["joint"] == 9 and "手腕" in pose["message"]
    current.scores[9] = .1
    _, pose = target.compare(current)
    assert not pose["ok"] and pose["actual"][9] is None and "入镜" in pose["message"]
    current.scores[:] = 0.
    pos, pose = target.compare(current)
    assert pos["ok"] and not pose["ok"] and all(p is None for p in pose["actual"])
    assert pose["target"] == target.points


def test_close_circles_use_screen_distance_in_portrait_and_landscape():
    for aspect in (.64, 1.6):
        for radius, ok in [(CENTER_TOLERANCE - .001, True), (CENTER_TOLERANCE + .001, False)]:
            offset = np.array([.6, .8]) * radius * min(aspect, 1.) / [aspect, 1.]
            point = .5 + offset
            quad = np.float32((FRAME - .5) * 1.22 + point)
            measured = frame_error(quad, point, aspect, 20, 1.22)
            assert measured["tracking"] and measured["centerOk"] == ok
            assert not measured["ok"] and not measured["scaleOk"]
            assert measured["centerDistance"] == pytest.approx(radius)
    angle = np.deg2rad(25)
    rotation = np.array([[np.cos(angle), np.sin(angle)], [-np.sin(angle), np.cos(angle)]])
    tilted = frame_error(np.float32((FRAME-.5) @ rotation + .5), np.array([.5, .5]), 1, 25, 1.)
    assert not tilted["tracking"] and not tilted["ok"] and tilted["control"] == "roll"


def test_outline_uses_only_the_selected_person_and_preserves_arm_gaps():
    frame = np.zeros((180, 300, 3), np.uint8)
    cv2.rectangle(frame, (115,38), (152,154), (240,240,240), -1)
    cv2.ellipse(frame, (115,80), (15,21), 0, 0, 360, (240,240,240), 5)
    cv2.rectangle(frame, (163,48), (188,144), (240,240,240), -1)
    box = np.array([98/300,38/180,153/300,155/180])
    mask = lambda square: np.float32(square[:, :, 0] > 200)
    result = reference_outline(frame, box, [[.45,.32], [.45,.61]], mask)
    assert result and len(result["contours"]) == 2
    outer = np.vstack(result["contours"])
    assert outer[:,0].max() <= 153/300 and outer[:,0].min() == pytest.approx(98/300, abs=.012)
    assert reference_outline(frame, box, [], lambda sq: np.zeros(sq.shape[:2], np.float32)) is None


def test_weak_segmentation_draws_a_simplified_outline_of_visible_limbs_only():
    ref = portrait()
    ref.scores[11:] = .1
    target = PoseTarget(ref, 640, 480)
    frame = np.zeros((480,640,3), np.uint8)
    shape = reference_outline(frame, ref.box, target.points, lambda crop: np.zeros(crop.shape[:2], np.float32))
    assert shape["kind"] == "joints" and shape["contours"] and not shape["details"]
    assert np.vstack(shape["contours"])[:,1].max() < .60


def test_half_body_and_head_angle_are_observable_independently():
    ref = portrait()
    ref.scores[11:] = .12
    target = PoseTarget(ref, 700, 950)
    assert target.basis == "shoulders"
    current = Person(ref.box, ref.xy.copy(), ref.scores.copy())
    assert target.compare(current)[1]["ok"]
    current.xy[1, 1] += .033
    _, pose = target.compare(current)
    assert not pose["ok"] and "头顶" in pose["message"]
    assert all(p is None for p in pose["target"][11:])


def test_passerby_does_not_steal_the_selected_subject():
    left = np.array([.02,.18,.24,.93,.86])
    right = np.array([.55,.31,.72,.8,.95])
    assert choose([left, right], tap=[.62,.52]) is right
    assert choose([left, right], previous=right) is right
    assert choose([left], previous=right) is None
    assert choose([left, right], target=right) is right


def test_only_the_background_can_confirm_the_landmark():
    rng = np.random.default_rng(27)
    bg = np.full((480,640,3), 95, np.uint8)
    bg[95:385,180:460] = rng.integers(0,255,(290,280,3),np.uint8)
    box = np.array([.25,.14,.76,.84,.95])
    tracker = Background(bg, [box])
    assert tracker.measure(bg, [box]) is None


def test_mirrored_building_keeps_its_own_matching_evidence():
    rng = np.random.default_rng(17)
    image = np.full((480, 640, 3), 180, np.uint8)
    image[40:440, 410:615] = cv2.GaussianBlur(rng.integers(0, 255, (400, 205, 3), np.uint8), (3, 3), .6)
    tracker = Background(image, [])
    tracker.set_subject({"bounds": [.63, .07, .97, .94]})
    assert tracker.subject is not None
    result = tracker.measure(cv2.flip(image, 1), [])
    assert result and result["control"] == "mirror" and not result["ok"]


def test_large_portrait_preserves_side_background_and_detects_reflected_only_in_background():
    rng = np.random.default_rng(12)
    shape = (480, 360)
    person_mask = np.zeros(shape, np.uint8)
    cv2.ellipse(person_mask, (180, 212), (68, 90), 0, 0, 360, 1, -1)
    cv2.ellipse(person_mask, (180, 460), (188, 145), 0, 0, 360, 1, -1)
    reference = np.full((*shape, 3), 205, np.uint8)
    background_texture = rng.integers(0, 255, (190, 360, 3), dtype=np.uint8)
    reference[126:316] = cv2.GaussianBlur(background_texture, (3, 3), .6)
    reference[person_mask != 0] = rng.integers(0, 255, (person_mask.sum(), 3), dtype=np.uint8)
    box = np.array([0., .25, 1., 1., .96])
    # 原版矩形正好同时盖住左右两段背景。
    assert Background(reference, [box]).measure(reference, [box]) is None
    tracker = Background(reference, [box], lambda _: person_mask.astype(np.float32))
    assert tracker.measure(reference, [box])["ok"]
    mirrored = tracker.measure(cv2.flip(reference, 1), [box])
    assert mirrored and mirrored["control"] == "mirror" and not mirrored["ok"]
    no_background = np.full_like(reference, 205)
    no_background[person_mask != 0] = reference[person_mask != 0]
    assert tracker.measure(no_background, [box]) is None


def test_close_framing_hint_cannot_unlock_the_background_stage():
    ref = portrait()
    current = portrait()
    current.xy = (current.xy - .5) * 1.6 + .5
    assert "拉开" in PoseTarget(ref, 640, 480).framing_hint(current)


def test_local_matches_on_either_side_project_the_same_reference_center():
    # 两帧看见不同侧的一小片地标，机位和投影却完全相同。
    offsets = np.float32([[x, y] for x in (0, .045, .09, .135) for y in (0, .05, .10)])
    ref_xy = np.vstack((offsets + [.10, .20], offsets + [.70, .61])).astype(np.float32)
    desc = np.eye(len(ref_xy), dtype=np.float32)
    size = np.float32([720, 960])
    tracker = Background(np.zeros((120, 90, 3), np.uint8), [])
    for visible in (slice(0, 12), slice(12, 24)):
        current = ((ref_xy[visible] - .5) * 1.12 + [.58, .46]).astype(np.float32)
        result = tracker.match((ref_xy, desc), (current, desc[visible]), size)
        assert result["control"] == "local" and not result["ok"]
        assert result["target"] == [.5, .5]
        np.testing.assert_allclose(result["current"], [.58, .46], atol=1e-5)
        assert "右转" in result["message"]
        current = ((ref_xy[visible] - .5) * 1.12 + [.55, .48]).astype(np.float32)
        close = tracker.match((ref_xy, desc), (current, desc[visible]), size)
        assert close["quad"] is None and close["tracking"] and close["ok"]


def shared_photos():
    root = Path(__file__).resolve().parents[2]
    shared = root / "校园摄影参考库_队友分享_98张"
    return shared if shared.is_dir() else root / "campus-photo-recommend/data/formal/images"


@pytest.fixture(scope="module")
def campus_photo():
    photo = next(shared_photos().rglob("REF_011.webp"), None)
    if photo is None:
        pytest.skip("需要共享参考库中的 REF_011.webp")
    return decode(photo.read_bytes())


def test_real_campus_landmark_coarse_fine_and_loss(campus_photo):
    f = campus_photo
    h, w = f.shape[:2]
    guide = Guide(get_models(), f, "拱廊正面")
    shifts = [(.18, 0, "coarse", "向右转"), (.11, 0, "fine", "向右转"),
              (-.11, 0, "fine", "向左转"), (0, .10, "fine", "向下俯"), (0, -.10, "fine", "向上仰")]
    seq = 0
    for dx, dy, mode, cue in shifts:
        mat = np.float32([[1,0,w*dx],[0,1,h*dy]])
        moved = cv2.warpAffine(f, mat, (w,h))
        # 跳切到另一侧时，景物锚点允许先失配，再用连续观测确认同一处位置。
        for _ in range(3):
            res = guide.analyze(moved, seq, 1 + .2*seq)
            seq += 1
            assert not res["ready"]
            if res["background"] and res["background"]["tracking"]:
                break
        assert res["stage"] == "background" and res["mode"] == mode
        assert cue in res["message"]
        assert res["background"]["target"] == [.5, .5]
        np.testing.assert_allclose(res["background"]["current"], [.5+dx, .5+dy], atol=.006)
    res = guide.analyze(np.zeros_like(f), seq, 1 + .2*seq)
    assert res["mode"] == "search" and "拱廊正面" in res["message"] and not res["ready"]


def test_near_alignment_advances_and_small_camera_jitter_keeps_standing(campus_photo):
    guide = Guide(get_models(), campus_photo)
    assert guide.reference["outline"]["contours"] and guide.reference["centerTolerance"] == CENTER_TOLERANCE
    h, w = campus_photo.shape[:2]
    for seq, dx in enumerate([.067, .067, .067, .067, .067, .091, .12]):
        live = cv2.warpAffine(campus_photo, np.float32([[1,0,dx*w],[0,1,0]]), (w,h))
        step = guide.analyze(live, seq, seq*.20)
        assert step["stage"] == ["background","background","framing","framing","position","position","position"][seq]
        if seq >= 2:
            assert step["completed"] == ["background"] and step["checks"][0]


@pytest.fixture
def measured_background(campus_photo, monkeypatch):
    """Script valid geometry observations while retaining the real guide and person checks."""
    def create(samples):
        guide = Guide(get_models(), campus_photo)
        observations = iter(samples)
        thresholds = []

        async def measure(image, boxes, tolerance, scale_limit):
            thresholds.append(tolerance)
            sample = next(observations)
            if sample is None:
                return None
            mirrored = sample == "mirror"
            distance, zoom, roll = (0., 1., 0.) if mirrored else sample
            point = np.array([.5 + distance * min(guide.aspect, 1.) / guide.aspect, .5])
            quad = np.float32((FRAME - .5) * zoom + point)
            result = frame_error(quad, point, guide.aspect, 30, zoom, tolerance, scale_limit)
            result["roll"] = roll
            result["anchors"] = []
            if mirrored:
                result.update(ok=False, centerOk=False, tracking=False, control="mirror")
            return result

        monkeypatch.setattr(guide.background, "measure_async", measure)
        return guide, thresholds
    return create


def test_center_hold_tolerates_boundary_jitter_and_independent_scale_noise(campus_photo, measured_background):
    guide, thresholds = measured_background([(.079, .70, -6.), (.091, .90, 6.), (.099, .72, 0.)])
    for seq, now in enumerate((0., .16, .34)):
        result = guide.analyze(campus_photo, seq, now)
        assert result["background"]["centerOk"]
        assert "zoomFactor" not in result["background"]
        assert not result["ready"]
    assert result["stage"] == "framing" and not result["checks"][1]
    assert thresholds == [CENTER_TOLERANCE, CENTER_RELEASE, CENTER_RELEASE]


@pytest.mark.parametrize("invalid", [None, "mirror", (.101, 1., 0.)])
def test_center_capture_must_reenter_after_loss_mirror_or_exit(campus_photo, measured_background, invalid):
    guide, thresholds = measured_background([(.09, 1., 0.), (.079, 1., 0.), invalid,
                                             (.09, 1., 0.), (.079, 1., 0.), (.09, 1., 0.)])
    first = guide.analyze(campus_photo, 0, 0.)
    assert first["stage"] == "background" and not first["checks"][0]
    assert guide.analyze(campus_photo, 1, .2)["checks"][0]
    lost = guide.analyze(campus_photo, 2, .4)
    assert lost["stage"] == "background" and not lost["checks"][0] and not lost["ready"]
    outside_entry = guide.analyze(campus_photo, 3, .6)
    assert not outside_entry["checks"][0]
    assert guide.analyze(campus_photo, 4, .8)["stage"] == "background"
    assert guide.analyze(campus_photo, 5, 1.15)["stage"] == "framing"
    assert thresholds[0] == thresholds[3] == CENTER_TOLERANCE


def test_center_confirmation_needs_fresh_frames_and_resets_after_pause(campus_photo, measured_background):
    guide, _ = measured_background([(0., 1., 0.)] * 4)
    assert guide.analyze(campus_photo, 0, 0.)["stage"] == "background"
    with pytest.raises(ValueError):
        guide.analyze(campus_photo, 0, .5)
    assert guide.analyze(campus_photo, 1, .2)["stage"] == "background"
    assert guide.analyze(campus_photo, 2, 2.)["stage"] == "background"
    # A slow but continuous camera can confirm using its next observation.
    assert guide.analyze(campus_photo, 3, 2.5)["stage"] == "framing"


def test_confirmed_center_survives_drift_loss_pause_and_zoom(campus_photo, measured_background):
    guide, _ = measured_background([(0., 1., 0.), (0., 1., 0.), (.24, 1., 0.),
                                     None, None, (.23, .70, 0.), (.25, .70, 0.)])
    assert guide.analyze(campus_photo, 0, 0.)["completed"] == []
    confirmed = guide.analyze(campus_photo, 1, .4)
    assert confirmed["stage"] == "framing" and confirmed["completed"] == ["background"]
    drifting = guide.analyze(campus_photo, 2, .8)
    assert drifting["stage"] == "position" and drifting["checks"][:2] == [True, True]
    assert not drifting["background"]["centerOk"]
    for seq, now in ((3, 1.), (4, 3.)):
        lost = guide.analyze(campus_photo, seq, now)
        assert lost["stage"] == "framing" and lost["completed"] == ["background"]
        assert lost["checks"][:2] == [True, False] and not lost["ready"]
        assert "白点" not in lost["message"] and "中心环" not in lost["message"]
    guide.analyze(campus_photo, 5, 3.2)
    zooming = guide.analyze(campus_photo, 6, 3.6)
    assert zooming["stage"] == "framing" and zooming["completed"] == ["background"]
    assert not zooming["background"]["centerOk"]
    assert zooming["background"]["zoomFactor"] == pytest.approx(1/.70, abs=.001)


@pytest.mark.parametrize("gap, stage", [(2.2, "framing"), (3.6, "background")])
def test_phone_sampling_can_confirm_slow_pairs_but_not_a_long_pause(campus_photo, measured_background, gap, stage):
    guide, _ = measured_background([(0., 1., 0.)] * 2)
    guide.max_sample_gap = 3.5
    assert guide.analyze(campus_photo, 0, 0.)["stage"] == "background"
    assert guide.analyze(campus_photo, 1, gap)["stage"] == stage


def test_center_completion_resets_only_for_new_source_not_unskipping(campus_photo, measured_background):
    guide, _ = measured_background([(0., 1., 0.), (0., 1., 0.), None,
                                     None, (.09, 1., 0.), (.079, 1., 0.), (.079, 1., 0.)])
    guide.analyze(campus_photo, 0, 0., source_id=1)
    assert guide.analyze(campus_photo, 1, .4, source_id=1)["completed"] == ["background"]
    skip = guide.analyze(campus_photo, 2, .6, skipped=["framing"], source_id=1)
    assert skip["completed"] == ["background"] and skip["stage"] == "position"
    restore = guide.analyze(campus_photo, 3, .8, skipped=[], source_id=1)
    assert restore["completed"] == ["background"] and restore["stage"] == "framing"
    restart = guide.analyze(campus_photo, 4, 1., source_id=2)
    assert restart["completed"] == [] and restart["stage"] == "background" and not restart["checks"][0]
    assert guide.analyze(campus_photo, 5, 1.2, source_id=2)["stage"] == "background"
    again = guide.analyze(campus_photo, 6, 1.6, source_id=2)
    assert again["completed"] == ["background"] and again["stage"] == "framing"


def test_skipping_center_does_not_report_completion_and_framing_is_independent(campus_photo, measured_background):
    guide, _ = measured_background([(.25, 1., 0.), (.25, 1., 0.), (.25, 1., 0.)])
    first = guide.analyze(campus_photo, 0, 0., skipped=["background"])
    assert first["completed"] == [] and first["checks"][:2] == [None, True]
    second = guide.analyze(campus_photo, 1, .4, skipped=["background"])
    assert second["stage"] == "position" and second["completed"] == []
    restored = guide.analyze(campus_photo, 2, .8, skipped=[])
    assert restored["stage"] == "background" and restored["completed"] == [] and not restored["checks"][0]


def test_landmark_spacing_needs_distant_pairs_in_agreement():
    points = np.float32([(x, y) for x in (.1, .3, .55, .85) for y in (.15, .45, .8)])
    size = np.float32([720, 960])
    assert landmark_scale(points, points * .71 + [.04, .2], size) == pytest.approx(.71)
    assert landmark_scale(points * .06, points * .04, size) is None
    assert landmark_scale(points, points * [1.4, .76], size) is None


def test_aim_before_zoom_and_verify_the_changed_framing(campus_photo):
    guide = Guide(get_models(), campus_photo)
    h, w = campus_photo.shape[:2]
    def view(scale, offset=0.):
        matrix = cv2.getRotationMatrix2D((w/2, h/2), 0, scale)
        matrix[0, 2] += w * offset
        return cv2.warpAffine(campus_photo, matrix, (w, h))
    for seq, time in enumerate((0., .4)):
        result = guide.analyze(view(.70, .16), seq, time)
        bg = result["background"]
        assert result["stage"] == "background" and not bg["centerOk"] and "zoomFactor" not in bg
        for anchor in bg["anchors"]:
            target = np.array(guide.reference["landmarks"][anchor["id"]])
            np.testing.assert_allclose(anchor["current"], .7*(target-.5)+[.66,.5], atol=.008)
        assert bg["anchors"]
    # 中心在容差内即可通过第一关；景别仍需稳定后才能发出变焦命令。
    for seq, (time, dx) in enumerate(((.55, 0.), (.79, .06), (.96, .06)), 2):
        bg = guide.analyze(view(.7, dx), seq, time)["background"]
        assert bg["centerOk"] and not bg["scaleOk"] and "zoomFactor" not in bg
    assert guide.stage == 1
    result = guide.analyze(view(.70, .06), 5, 1.16)
    assert result["stage"] == "framing" and result["checks"][0] and not result["checks"][1]
    assert result["background"]["zoomFactor"] == pytest.approx(1/.70, abs=.015)
    # 请求变焦之后收到旧景别，仍不能进入站位。真正变化后的帧再核验。
    command = guide.analyze(view(.70, .06), 6, 1.4)
    assert command["stage"] == "framing"
    assert command["background"]["zoomFactor"] == pytest.approx(1/.70, abs=.015)
    assert guide.analyze(campus_photo, 7, 1.6)["stage"] == "framing"
    # 大幅数字变焦可能使光流失配；先重新确认景物，再对新景别累计稳定时间。
    seq, now = 8, 2.
    for _ in range(4):
        aligned = guide.analyze(campus_photo, seq, now)
        seq, now = seq + 1, now + .4
        assert aligned["stage"] != "background" and aligned["completed"] == ["background"]
        if aligned["stage"] == "position":
            break
    assert aligned["stage"] == "position" and aligned["checks"][1]
    for _ in range(3):
        slight = guide.analyze(view(1.15), seq, now)
        seq, now = seq + 1, now + .2
        if slight["background"]:
            break
    assert slight["checks"][1]
    too_close = guide.analyze(view(1.28), seq, now)
    assert too_close["stage"] == "framing" and not too_close["ready"]
    restarted = guide.analyze(view(1.28), seq + 1, now + .35, source_id=2)
    assert "zoomFactor" not in restarted["background"]


def test_learned_landmarks_map_to_the_same_frame_coordinates(campus_photo):
    h, w = campus_photo.shape[:2]
    size = np.float32([w, h])
    dx, dy = -.09, .13
    live = cv2.warpAffine(campus_photo, np.float32([[1,0,w*dx],[0,1,h*dy]]), (w,h))
    models = get_models()
    tracker = Background(campus_photo, models.observe(campus_photo, 0)[0], models.foreground)
    observed = xfeat_features(*tracker.background_image(live, models.observe(live, 0)[0]))
    bg = tracker.learned_match(observed, size, CENTER_TOLERANCE, 1.13)
    assert bg and bg["tracking"] and bg["anchors"]
    np.testing.assert_allclose(bg["current"], [.5+dx, .5+dy], atol=.008)
    assert bg["zoom"] == pytest.approx(1., abs=.012)


def test_independent_arcade_view_and_unrelated_lawn(campus_photo):
    library = shared_photos()
    files = [next(library.rglob(f"REF_{key}.webp"), None) for key in ("025", "026", "051", "046", "095")]
    if None in files:
        pytest.skip("需要共享参考库中的拱廊和草坪样例")
    reference, different_take, lawn, tower, second_tower = (decode(path.read_bytes()) for path in files)
    guide = Guide(get_models(), reference)
    # 试拍入口按参考照画幅居中取景，这张原图比参考宽一像素。
    different_take = different_take[:, :reference.shape[1]]
    result = guide.analyze(different_take, 0, 0.)
    bg = result["background"]
    assert bg and bg["inliers"] >= 24 and not bg["scaleOk"] and not bg["ok"]
    assert result["stage"] == "background" and "zoomFactor" not in bg
    other = Guide(get_models(), tower).analyze(second_tower, 0, 0.)["background"]
    assert other and other["anchors"] and other["control"] == "x"
    assert other["zoom"] == pytest.approx(.80, abs=.06) and not other["centerOk"]
    h,w = campus_photo.shape[:2]
    unrelated = Guide(get_models(), campus_photo).analyze(cv2.resize(lawn, (w,h)), 0, 0.)
    assert unrelated["background"] is None and not unrelated["checks"][0]


def test_building_outline_uses_visible_geometry_on_an_unlabelled_reference():
    library = shared_photos()
    photo = next(library.rglob("REF_046.webp"), None)
    if photo is None:
        pytest.skip("需要共享参考库中的草坪圆塔照片")
    im = decode(photo.read_bytes())
    h, w = im.shape[:2]
    # 用重新构图并编码的图像，避免把文件名或原图坐标当作检测结果。
    dx = .13
    translated = cv2.warpAffine(im, np.float32([[1, 0, dx*w], [0, 1, 0]]), (w, h),
                                borderMode=cv2.BORDER_REPLICATE)
    ok, encoded = cv2.imencode(".jpg", translated, [cv2.IMWRITE_JPEG_QUALITY, 88])
    assert ok
    other = decode(encoded.tobytes())
    base = reference_building(im, [])
    shifted = reference_building(other, [])
    assert base and shifted
    for operation in (np.amin, np.amax):
        np.testing.assert_allclose(operation(np.vstack(shifted["paths"]), axis=0),
                                   operation(np.vstack(base["paths"]), axis=0) + [dx, 0], atol=.045)
    # 有同名地点提示但只拍到栏杆时，不能制造建筑轮廓。
    rail = decode(next(library.rglob("REF_062.webp")).read_bytes())
    assert reference_building(rail, get_models().observe(rail, 0)[0], "钟楼") is None



def test_each_stage_can_be_skipped_during_loss_and_restored(campus_photo):
    guide = Guide(get_models(), campus_photo)
    blank = np.zeros_like(campus_photo)
    step = guide.analyze(blank, 0, 0., skipped=["background"])
    assert step["stage"] == "framing" and step["checks"] == [None, False, False, False]
    step = guide.analyze(blank, 1, .2, skipped=["background", "framing"])
    assert step["stage"] == "position" and step["checks"] == [None, None, False, False]
    step = guide.analyze(blank, 2, .4, skipped=["background", "framing", "position"])
    assert step["stage"] == "pose" and "姿势" in step["message"]
    step = guide.analyze(blank, 3, .6, skipped=["position", "background", "framing", "pose"])
    assert step["ready"] and step["checks"] == [None, None, None, None]
    assert step["skipped"] == ["background", "framing", "position", "pose"]
    with pytest.raises(ValueError, match="过期"):
        guide.analyze(blank, 1, .5, skipped=[])
    assert guide.analyze(blank, 4, 3., tap=[.4, .5])["ready"]
    with pytest.raises(ValueError, match="可跳过"):
        guide.analyze(blank, 5, 3.2, skipped=["ready"])
    step = guide.analyze(blank, 5, 3.2, skipped=[])
    assert step["stage"] == "background" and step["skipped"] == [] and not step["ready"]


def test_skipping_only_position_keeps_background_and_pose_gated(campus_photo):
    guide = Guide(get_models(), campus_photo)
    for seq in range(9):
        step = guide.analyze(campus_photo, seq, seq*.22, skipped=["position"])
        assert step["stage"] != "position"
    assert step["ready"] and step["checks"] == [True, True, None, True]
    lost = guide.analyze(np.zeros_like(campus_photo), 9, 1.98)
    assert lost["stage"] == "framing" and lost["completed"] == ["background"] and not lost["ready"]


def test_reference_without_a_detectable_subject_can_still_use_manual_capture():
    blank = np.zeros((160, 200, 3), np.uint8)
    guide = Guide(get_models(), blank)
    assert guide.reference["box"] is None and guide.reference["subjectHint"]
    assert guide.analyze(blank, 0, 0.)["stage"] == "background"
    result = guide.analyze(blank, 1, .2, skipped=["background", "framing"])
    assert result["stage"] == "position" and not result["ready"] and "参考" in result["message"]
    assert guide.analyze(blank, 2, .4, skipped=["background", "framing", "position", "pose"])["ready"]


def test_camera_ready_is_withdrawn_if_person_or_background_leaves(campus_photo):
    f = campus_photo
    guide = Guide(get_models(), f)
    stages = []
    for seq in range(10):
        result = guide.analyze(f, seq, seq*.22)
        stages.append(result["stage"])
    assert list(dict.fromkeys(stages)) == ["background","framing","position","pose","ready"]
    assert result["ready"] and all(result["checks"])
    no_model = f.copy()
    h, w = f.shape[:2]
    x,y,r,b = np.rint(guide.target.reference.box[:4] * [w,h,w,h]).astype(int)
    cv2.rectangle(no_model, (x-5,y-5), (r+5,b+5), (92,95,80), -1)
    result = guide.analyze(no_model, 10, 2.2)
    assert result["stage"] == "position" and not result["ready"] and result["checks"][0]
    result = guide.analyze(np.zeros_like(f), 11, 2.4)
    assert result["stage"] == "framing" and result["completed"] == ["background"] and not result["ready"]
    with pytest.raises(ValueError, match="过期"):
        guide.analyze(f, 11, 2.6)

def test_image_inference_finds_a_lowered_wrist_and_keeps_background_and_position():
    # 只改变手臂附近的原图像素。实际人体检测和姿态模型都走生产入口。
    sample = Path(__file__).resolve().parents[2] / "samples" / "pose.jpg"
    if not sample.exists():
        pytest.skip("需要已有的公开姿态样例 pose.jpg")
    frame = decode(sample.read_bytes())
    h, w = frame.shape[:2]
    yy, xx = np.mgrid[:h, :w].astype(np.float32)
    drop = .12 * np.clip((xx / w - .545) / .155, 0, 1) * np.clip((.86 - xx / w) / .08, 0, 1)
    weight = np.clip(np.minimum((yy / h - .30) / (.15 + drop), (.635 - yy / h) / (.145 - drop)), 0, 1)
    changed = cv2.remap(frame, xx, yy - h * drop * weight, cv2.INTER_LINEAR)
    guide = Guide(get_models(), frame)
    for seq in range(7):
        result = guide.analyze(changed, seq, 3 + .24 * seq)
    assert result["checks"] == [True, True, True, False] and result["stage"] == "pose"
    assert result["pose"]["joint"] == 9 and "左手腕向画面上移" in result["message"]
    assert result["pose"]["actual"][9][1] - result["pose"]["target"][9][1] > .08


def test_two_independent_lawn_photos_share_landmark_but_need_different_stances():
    library = shared_photos()
    photos = [next(library.rglob(f"REF_{key}.webp"), None) for key in ("051", "052")]
    if None in photos:
        pytest.skip("需要共享参考库红椅机位的 REF_051、REF_052")
    reference, current = (decode(path.read_bytes()) for path in photos)
    guide = Guide(get_models(), reference)
    for seq in range(5):
        result = guide.analyze(current, seq, 5 + .2 * seq)
    assert result["stage"] == "position" and result["checks"][:3] == [True, True, False]


