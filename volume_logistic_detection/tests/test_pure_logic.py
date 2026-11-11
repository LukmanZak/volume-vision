"""Tests for the pure logic in utils/. They need no Odoo database."""
import base64
from unittest import mock

from odoo.tests.common import BaseCase

from ..utils import roboflow_client
from ..utils.packer import fill_ratio, pack
from ..utils.tracker import is_countable, update_tracks
from ..utils.volume_estimator import SOURCE_BBOX, SOURCE_CLASS, estimate_dimensions, volume_cm3


def _overlaps(a, b):
    return not (
        a["x"] + a["l"] <= b["x"] or b["x"] + b["l"] <= a["x"]
        or a["y"] + a["w"] <= b["y"] or b["y"] + b["w"] <= a["y"]
        or a["z"] + a["h"] <= b["z"] or b["z"] + b["h"] <= a["z"]
    )


class TestVolumeEstimator(BaseCase):
    def test_class_dims_are_used_as_is(self):
        dims = estimate_dimensions(100, 100, px_per_cm=5, class_dims=(40, 30, 20))
        self.assertEqual(dims, (40.0, 30.0, 20.0, SOURCE_CLASS))

    def test_bbox_scaled_by_calibration(self):
        length, width, height, source = estimate_dimensions(
            300, 150, px_per_cm=10, default_height_cm=25)
        self.assertEqual((length, width, height, source), (30.0, 15.0, 25.0, SOURCE_BBOX))

    def test_rejects_missing_calibration(self):
        with self.assertRaises(ValueError):
            estimate_dimensions(100, 100, px_per_cm=0)

    def test_volume(self):
        self.assertEqual(volume_cm3(10, 20, 30), 6000.0)


class TestPacker(BaseCase):
    def test_single_item_fits_at_origin(self):
        result = pack(100, 100, 100, [{"id": 1, "l": 50, "w": 50, "h": 50}])
        self.assertTrue(result[0]["fitted"])
        self.assertEqual((result[0]["x"], result[0]["y"], result[0]["z"]), (0.0, 0.0, 0.0))

    def test_items_do_not_overlap(self):
        items = [{"id": i, "l": 30, "w": 20, "h": 15} for i in range(20)]
        result = pack(100, 100, 100, items)
        fitted = [p for p in result if p["fitted"]]
        self.assertGreater(len(fitted), 1)
        for i, a in enumerate(fitted):
            for b in fitted[i + 1:]:
                self.assertFalse(_overlaps(a, b), f"{a['id']} overlaps {b['id']}")

    def test_items_stay_inside_truck(self):
        items = [{"id": i, "l": 25, "w": 25, "h": 25} for i in range(60)]
        for p in pack(100, 80, 60, items):
            if p["fitted"]:
                self.assertLessEqual(p["x"] + p["l"], 100)
                self.assertLessEqual(p["y"] + p["w"], 80)
                self.assertLessEqual(p["z"] + p["h"], 60)

    def test_overflow_marked_not_fitted(self):
        result = pack(50, 50, 50, [{"id": 1, "l": 50, "w": 50, "h": 50},
                                   {"id": 2, "l": 10, "w": 10, "h": 10}])
        self.assertTrue(result[0]["fitted"])
        self.assertFalse(result[1]["fitted"])

    def test_item_taller_than_truck_rejected(self):
        result = pack(100, 100, 50, [{"id": 1, "l": 10, "w": 10, "h": 60}])
        self.assertFalse(result[0]["fitted"])

    def test_rotation_used_when_needed(self):
        # 80 x 30 footprint does not fit a 30-wide truck unless rotated to 30 x 80.
        result = pack(30, 80, 50, [{"id": 1, "l": 80, "w": 30, "h": 10}])
        self.assertTrue(result[0]["fitted"])
        self.assertEqual((result[0]["l"], result[0]["w"]), (30, 80))

    def test_fill_ratio(self):
        placements = pack(100, 100, 100, [{"id": 1, "l": 50, "w": 50, "h": 50}])
        self.assertAlmostEqual(fill_ratio(100, 100, 100, placements), 0.125)

    def test_fits_empty_truck_considers_rotation_and_height(self):
        from ..utils.packer import fits_empty_truck
        item = {"l": 200, "w": 100, "h": 50}
        self.assertTrue(fits_empty_truck(230, 140, 120, item))
        self.assertTrue(fits_empty_truck(100, 230, 120, {**item}))       # fits when turned
        self.assertFalse(fits_empty_truck(230, 90, 120, item))            # too wide either way
        self.assertFalse(fits_empty_truck(230, 140, 40, item))            # too tall

    def test_items_stack_once_the_floor_is_full(self):
        # Each 100 x 100 item covers the whole floor, so the rest must go on top.
        items = [{"id": i, "l": 100, "w": 100, "h": 30} for i in range(4)]
        result = pack(100, 100, 100, items)
        self.assertEqual([p["z"] for p in result[:3]], [0.0, 30.0, 60.0])
        self.assertFalse(result[3]["fitted"])  # a fourth layer would reach 120 > 100

    def test_floor_fills_across_the_width_before_going_deeper(self):
        # In a 240-wide truck, items load as a wall across the width from the front.
        items = [{"id": i, "l": 40, "w": 60, "h": 20} for i in range(8)]
        result = pack(600, 240, 240, items)
        first_four = result[:4]
        self.assertEqual({p["x"] for p in first_four}, {0.0})
        self.assertEqual(sorted(p["y"] for p in first_four), [0.0, 60.0, 120.0, 180.0])
        self.assertTrue(all(p["x"] == 40.0 for p in result[4:]))
        self.assertTrue(all(p["z"] == 0.0 for p in result))

    def test_item_is_not_left_hanging_over_a_small_box(self):
        # Only a quarter of the big item would rest on the small one: not stable.
        items = [{"id": 1, "l": 50, "w": 50, "h": 20}, {"id": 2, "l": 100, "w": 100, "h": 20}]
        result = pack(100, 100, 60, items)
        self.assertTrue(result[0]["fitted"])
        self.assertFalse(result[1]["fitted"])

    def test_small_item_rests_on_a_larger_one(self):
        items = [{"id": 1, "l": 100, "w": 100, "h": 20}, {"id": 2, "l": 40, "w": 40, "h": 20}]
        result = pack(100, 100, 60, items)
        self.assertEqual((result[1]["x"], result[1]["y"], result[1]["z"]), (0.0, 0.0, 20.0))

    def test_every_raised_item_has_enough_support(self):
        import random
        rnd = random.Random(7)
        items = [{"id": i, "l": rnd.uniform(15, 90), "w": rnd.uniform(15, 70),
                  "h": rnd.choice([10, 20, 30, 45])} for i in range(60)]
        fitted = [p for p in pack(230, 140, 120, items) if p["fitted"]]
        self.assertGreater(len(fitted), 10)
        for i, a in enumerate(fitted):
            for b in fitted[i + 1:]:
                self.assertFalse(_overlaps(a, b), f"{a['id']} overlaps {b['id']}")
            if a["z"] > 0:
                covered = sum(
                    max(0, min(a["x"] + a["l"], b["x"] + b["l"]) - max(a["x"], b["x"]))
                    * max(0, min(a["y"] + a["w"], b["y"] + b["w"]) - max(a["y"], b["y"]))
                    for b in fitted if abs(b["z"] + b["h"] - a["z"]) < 1e-6)
                self.assertGreaterEqual(covered / (a["l"] * a["w"]), 0.7 - 1e-6,
                                        f"item {a['id']} is floating")

    def test_earlier_items_never_move_when_more_arrive(self):
        import random
        rnd = random.Random(11)
        items = [{"id": i, "l": rnd.uniform(15, 90), "w": rnd.uniform(15, 70), "h": 20}
                 for i in range(40)]
        full = pack(430, 200, 200, items)
        for count in (5, 12, 25):
            self.assertEqual(pack(430, 200, 200, items[:count]), full[:count])


class TestTracker(BaseCase):
    def _det(self, cx, cy, cls="box"):
        return {"cls": cls, "cx": cx, "cy": cy, "w": 40, "h": 40, "confidence": 0.9}

    def test_same_item_across_frames_counted_once(self):
        tracks = []
        for cx in (100, 104, 108):
            tracks = update_tracks(tracks, [self._det(cx, 100)])
        self.assertEqual(len(tracks), 1)
        self.assertEqual(tracks[0]["hits"], 3)
        self.assertTrue(is_countable(tracks[0]))

    def test_far_detections_are_separate_items(self):
        tracks = update_tracks([], [self._det(100, 100), self._det(400, 100)])
        self.assertEqual(len(tracks), 2)

    def test_different_class_never_matches(self):
        tracks = update_tracks([], [self._det(100, 100, "box")])
        tracks = update_tracks(tracks, [self._det(100, 100, "sack")])
        self.assertEqual(len(tracks), 2)

    def test_track_dropped_after_missing(self):
        tracks = update_tracks([], [self._det(100, 100)])
        for _ in range(6):
            tracks = update_tracks(tracks, [])
        self.assertEqual(tracks, [])

    def test_counted_track_kept_after_leaving_view(self):
        tracks = update_tracks([], [self._det(100, 100)])
        tracks = update_tracks(tracks, [self._det(100, 100)])
        tracks[0]["dims"] = {"l": 1, "w": 1, "h": 1, "source": "bbox", "color": "#000"}
        for _ in range(10):
            tracks = update_tracks(tracks, [])
        self.assertEqual(len(tracks), 1)
        self.assertIsNotNone(tracks[0]["dims"])

    def test_retired_track_does_not_rematch(self):
        tracks = update_tracks([], [self._det(100, 100)])
        for _ in range(6):
            tracks = update_tracks(tracks, [])
        tracks = update_tracks(tracks, [self._det(100, 100)])
        self.assertEqual(len(tracks), 1)
        self.assertEqual(tracks[0]["hits"], 1)  # fresh track, not revived

    def test_single_sighting_not_countable(self):
        tracks = update_tracks([], [self._det(100, 100)])
        self.assertFalse(is_countable(tracks[0]))


class TestRoboflowClient(BaseCase):
    def setUp(self):
        self.image = base64.b64encode(b"fake-jpeg").decode()

    def test_parses_predictions(self):
        response = mock.Mock(status_code=200)
        response.json.return_value = {"predictions": [
            {"class": "box", "x": 10, "y": 20, "width": 30, "height": 40, "confidence": 0.8},
            {"class": "bad"},  # missing coordinates: skipped
        ]}
        session = mock.Mock()
        session.post.return_value = response

        result = roboflow_client.detect("key", "model", "1", self.image, session=session)

        self.assertEqual(result, [{"cls": "box", "cx": 10.0, "cy": 20.0,
                                   "w": 30.0, "h": 40.0, "confidence": 0.8}])
        self.assertEqual(session.post.call_args.kwargs["params"]["api_key"], "key")

    def test_http_error_raises(self):
        session = mock.Mock()
        session.post.return_value = mock.Mock(status_code=403)
        with self.assertRaises(roboflow_client.RoboflowError):
            roboflow_client.detect("key", "model", "1", self.image, session=session)

    def test_missing_config_raises(self):
        with self.assertRaises(roboflow_client.RoboflowError):
            roboflow_client.detect("", "model", "1", self.image)

    def test_invalid_base64_raises(self):
        with self.assertRaises(roboflow_client.RoboflowError):
            roboflow_client.detect("key", "model", "1", "!!!not-base64!!!")

    def test_strip_data_url(self):
        self.assertEqual(roboflow_client.strip_data_url("data:image/jpeg;base64,QUJD"), "QUJD")
