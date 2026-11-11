"""Tests for the local YOLO detector. The decoder needs no model file."""
import numpy as np

from odoo.tests.common import BaseCase, tagged

from ..utils import local_detector

CLASSES = ["box", "roll", "bag"]


def _head_output(anchors):
    """Build a YOLOv8 head output (1, 4 + nc, N) from (cx, cy, w, h, class_id, score)."""
    out = np.zeros((1, 4 + len(CLASSES), len(anchors)), dtype=np.float32)
    for i, (cx, cy, w, h, class_id, score) in enumerate(anchors):
        out[0, 0:4, i] = (cx, cy, w, h)
        out[0, 4 + class_id, i] = score
    return out


@tagged("post_install", "-at_install")
class TestLocalDetector(BaseCase):

    def test_decode_undoes_letterbox_and_picks_class(self):
        # A 1280x720 frame is scaled by 0.5 and padded 140 px top and bottom.
        output = _head_output([(320, 320, 100, 60, 2, 0.9)])
        dets = local_detector.decode_output(
            output, CLASSES, ratio=0.5, pad_x=0, pad_y=140, width=1280, height=720, confidence=0.4)
        self.assertEqual(len(dets), 1)
        det = dets[0]
        self.assertEqual(det["cls"], "bag")
        self.assertAlmostEqual(det["cx"], 640, places=3)
        self.assertAlmostEqual(det["cy"], 360, places=3)
        self.assertAlmostEqual(det["w"], 200, places=3)
        self.assertAlmostEqual(det["h"], 120, places=3)
        self.assertAlmostEqual(det["confidence"], 0.9, places=3)

    def test_decode_drops_low_confidence_and_duplicates(self):
        output = _head_output([
            (320, 320, 100, 100, 0, 0.9),
            (322, 321, 100, 100, 0, 0.8),   # same object, suppressed by NMS
            (100, 100, 40, 40, 1, 0.2),     # below the confidence threshold
        ])
        dets = local_detector.decode_output(
            output, CLASSES, ratio=1.0, pad_x=0, pad_y=0, width=640, height=640, confidence=0.4)
        self.assertEqual([d["cls"] for d in dets], ["box"])

    def test_decode_clips_boxes_to_the_frame(self):
        output = _head_output([(10, 10, 100, 100, 0, 0.9)])
        det = local_detector.decode_output(
            output, CLASSES, ratio=1.0, pad_x=0, pad_y=0, width=640, height=640, confidence=0.4)[0]
        self.assertGreaterEqual(det["cx"] - det["w"] / 2, 0)
        self.assertGreaterEqual(det["cy"] - det["h"] / 2, 0)

    def test_decode_with_no_candidates_returns_empty_list(self):
        output = _head_output([(100, 100, 40, 40, 0, 0.1)])
        self.assertEqual(local_detector.decode_output(
            output, CLASSES, 1.0, 0, 0, 640, 640, 0.4), [])

    def test_invalid_image_data_is_rejected(self):
        with self.assertRaises(local_detector.LocalDetectorError):
            local_detector.decode_image("not base64!!")
