import base64
from unittest import mock

from odoo.tests.common import HttpCase, tagged

CTRL = "odoo.addons.volume_logistic_detection.utils.roboflow_client"
FRAME = "data:image/jpeg;base64," + base64.b64encode(b"fake-jpeg").decode()


@tagged("post_install", "-at_install")
class TestVolumeDetectionController(HttpCase):

    def setUp(self):
        super().setUp()
        self.env["res.users"].create({
            "name": "VLD Tester",
            "login": "vld_tester",
            "password": "vld_tester_pw",
            "group_ids": [(6, 0, [self.env.ref("base.group_user").id])],
        })
        self.truck = self.env["vld.truck.box"].create({
            "name": "Box", "length_cm": 600, "width_cm": 240, "height_cm": 240,
        })
        # These tests mock Roboflow, so keep the bundled local model out of the way.
        self.env["ir.config_parameter"].sudo().set_str("volume_logistic_detection.detector", "roboflow")
        self.authenticate("vld_tester", "vld_tester_pw")

    def _detect(self, session_id, tracks, detections, **extra):
        with mock.patch(f"{CTRL}.detect", return_value=detections):
            return self.make_jsonrpc_request("/volume_detection/detect_frame", {
                "session_id": session_id,
                "image_data": FRAME,
                "tracks": tracks,
                **extra,
            })

    def _start(self):
        return self.make_jsonrpc_request("/volume_detection/session/start", {
            "truck_id": self.truck.id, "px_per_cm": 10, "default_height_cm": 20,
        })["session_id"]

    def _count(self, sid, det):
        """Send the same detection twice so it gets counted; return the second reply."""
        first = self._detect(sid, [], det)
        return self._detect(sid, first["tracks"], det)

    def test_item_bigger_than_the_truck_does_not_end_the_run(self):
        sid = self._start()  # truck 600 x 240 x 240, 10 px per cm
        # 700 x 300 cm on the floor: too big in either orientation (a measuring error).
        det = [{"cls": "box", "cx": 400, "cy": 400, "w": 7000, "h": 3000, "confidence": 0.9}]
        result = self._count(sid, det)
        self.assertEqual(len(result["items"]), 1)
        self.assertFalse(result["items"][0]["fitted"])
        self.assertEqual(result["state"], "running")

    def test_truck_is_full_when_a_fitting_item_has_no_room_left(self):
        sid = self.make_jsonrpc_request("/volume_detection/session/start", {
            "truck_id": self.truck.id, "px_per_cm": 10, "default_height_cm": 200,
        })["session_id"]
        # Each item is 600 x 240 x 200 cm: one fills the truck, a second cannot fit.
        big = {"cls": "box", "cx": 100, "cy": 100, "w": 6000, "h": 2400, "confidence": 0.9}
        other = {"cls": "bag", "cx": 5000, "cy": 5000, "w": 6000, "h": 2400, "confidence": 0.9}
        first = self._detect(sid, [], [big, other])
        result = self._detect(sid, first["tracks"], [big, other])
        self.assertEqual(sorted(i["fitted"] for i in result["items"]), [False, True])
        self.assertEqual(result["state"], "full")

    def test_scale_maps_boxes_back_to_full_frame_pixels(self):
        sid = self._start()
        det = [{"cls": "box", "cx": 50, "cy": 60, "w": 100, "h": 40, "confidence": 0.9}]
        result = self._detect(sid, [], det, scale=2.0)
        box = result["boxes"][0]
        self.assertEqual((box["cx"], box["cy"], box["w"], box["h"]), (100, 120, 200, 80))

    def test_track_frame_counts_only_after_steady_sighting(self):
        sid = self._start()
        box = {"cls": "box", "cx": 100, "cy": 100, "w": 200, "h": 100, "confidence": 0.9}
        tracks = []
        for sighting in range(1, 5):
            result = self.make_jsonrpc_request("/volume_detection/track_frame", {
                "session_id": sid, "boxes": [box], "tracks": tracks,
            })
            self.assertEqual(result["status"], "ok")
            tracks = result["tracks"]
            # A box seen only 1 to 3 times may be a flicker, so it is not counted yet.
            self.assertEqual(len(result["items"]), 1 if sighting == 4 else 0)

    def test_track_frame_ignores_malformed_boxes(self):
        sid = self._start()
        result = self.make_jsonrpc_request("/volume_detection/track_frame", {
            "session_id": sid, "boxes": [{"cls": "box"}, "nonsense", None], "tracks": [],
        })
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["boxes"], [])

    def test_analyze_unknown_video_is_not_found(self):
        result = self.make_jsonrpc_request("/volume_detection/analyze", {"video": "nope.mp4"})
        self.assertEqual(result["code"], "not_found")

    def test_analyze_runs_once_and_is_cached(self):
        video = self.make_jsonrpc_request("/volume_detection/videos", {})[0]
        fake = {"fps": 24.0, "width": 1280, "height": 720, "frames": [[], []]}
        analyzer = "odoo.addons.volume_logistic_detection.utils.local_detector"
        with mock.patch(f"{analyzer}.is_available", return_value=True), \
                mock.patch(f"{analyzer}.analyze_video", return_value=fake) as analyze:
            controller = "odoo.addons.volume_logistic_detection.controllers.main._analysis_cache"
            with mock.patch.dict(controller, {}, clear=True):
                first = self.make_jsonrpc_request("/volume_detection/analyze", {"video": video})
                second = self.make_jsonrpc_request("/volume_detection/analyze", {"video": video})
        self.assertEqual(first["status"], "ok")
        self.assertEqual(len(second["frames"]), 2)
        self.assertEqual(analyze.call_count, 1)

    def test_full_detection_flow(self):
        sid = self.make_jsonrpc_request("/volume_detection/session/start", {
            "truck_id": self.truck.id, "px_per_cm": 10, "default_height_cm": 20,
        })["session_id"]
        det = [{"cls": "box", "cx": 100, "cy": 100, "w": 200, "h": 100, "confidence": 0.9}]

        first = self._detect(sid, [], det)
        self.assertEqual(first["status"], "ok")
        self.assertEqual(first["items"], [])  # one sighting is not counted yet

        second = self._detect(sid, first["tracks"], det)
        self.assertEqual(len(second["items"]), 1)
        item = second["items"][0]
        self.assertTrue(item["fitted"])
        self.assertEqual(item["source"], "bbox")
        self.assertAlmostEqual(item["volume"], 20 * 10 * 20)  # 200px x 100px at 10px/cm, 20cm high

        # The item leaves the view: it must stay counted.
        third = self._detect(sid, second["tracks"], [])
        self.assertEqual(len(third["items"]), 1)

        session = self.env["vld.session"].browse(sid)
        self.assertEqual(session.item_count, 1)
        self.assertAlmostEqual(session.used_volume_cm3, 4000.0)

    def test_roboflow_not_configured_returns_error(self):
        start = self.make_jsonrpc_request("/volume_detection/session/start", {
            "truck_id": self.truck.id, "px_per_cm": 10,
        })
        result = self.make_jsonrpc_request("/volume_detection/detect_frame", {
            "session_id": start["session_id"],
            "image_data": FRAME,
            "tracks": [],
        })
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["code"], "detector_error")

    def test_unknown_video_is_not_found(self):
        response = self.url_open("/volume_detection/video/does-not-exist.mp4")
        self.assertEqual(response.status_code, 404)

    def test_video_list_contains_sample_clips(self):
        names = self.make_jsonrpc_request("/volume_detection/videos", {})
        self.assertTrue(any(name.endswith(".mp4") for name in names))
