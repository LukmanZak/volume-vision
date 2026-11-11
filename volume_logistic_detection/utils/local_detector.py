"""Run the bundled YOLOv8 ONNX model on one video frame, fully on the server.

Uses OpenCV's DNN module, so it needs no extra Python package and no network
call. Returns detections in the same shape as roboflow_client.detect().
"""
import base64
import json
import logging
import os
import threading

import cv2
import numpy as np

_logger = logging.getLogger(__name__)

MODEL_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "detector"))
MODEL_PATH = os.path.join(MODEL_DIR, "conveyor_items.onnx")
CLASSES_PATH = os.path.join(MODEL_DIR, "classes.json")
INPUT_SIZE = 640
NMS_IOU = 0.45

_lock = threading.Lock()
_cache = {}


class LocalDetectorError(Exception):
    """Raised when the local model is missing or the frame cannot be decoded."""


def is_available():
    return os.path.isfile(MODEL_PATH) and os.path.isfile(CLASSES_PATH)


def _load():
    with _lock:
        if "net" not in _cache:
            if not is_available():
                raise LocalDetectorError("Local detector model is not installed.")
            net = cv2.dnn.readNetFromONNX(MODEL_PATH)
            net.setPreferableBackend(cv2.dnn.DNN_BACKEND_OPENCV)
            net.setPreferableTarget(cv2.dnn.DNN_TARGET_CPU)
            with open(CLASSES_PATH, encoding="utf-8") as handle:
                _cache["classes"] = json.load(handle)
            _cache["net"] = net
        return _cache["net"], _cache["classes"]


def decode_image(image_b64):
    try:
        raw = base64.b64decode(image_b64, validate=True)
    except (ValueError, TypeError) as exc:
        raise LocalDetectorError("Frame is not valid base64 image data.") from exc
    image = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise LocalDetectorError("Frame could not be decoded as an image.")
    return image


def _letterbox(image):
    height, width = image.shape[:2]
    ratio = min(INPUT_SIZE / width, INPUT_SIZE / height)
    new_w, new_h = int(round(width * ratio)), int(round(height * ratio))
    resized = cv2.resize(image, (new_w, new_h), interpolation=cv2.INTER_LINEAR)
    pad_x = (INPUT_SIZE - new_w) // 2
    pad_y = (INPUT_SIZE - new_h) // 2
    canvas = np.full((INPUT_SIZE, INPUT_SIZE, 3), 114, dtype=np.uint8)
    canvas[pad_y:pad_y + new_h, pad_x:pad_x + new_w] = resized
    return canvas, ratio, pad_x, pad_y


def decode_output(output, classes, ratio, pad_x, pad_y, width, height, confidence):
    """Turn a YOLOv8 head output of shape (1, 4 + nc, N) into detection dicts."""
    preds = np.squeeze(output, axis=0).T  # (N, 4 + nc)
    scores = preds[:, 4:4 + len(classes)]
    class_ids = scores.argmax(axis=1)
    conf = scores[np.arange(len(scores)), class_ids]
    keep = conf >= confidence
    preds, class_ids, conf = preds[keep], class_ids[keep], conf[keep]
    if not len(preds):
        return []

    cx = (preds[:, 0] - pad_x) / ratio
    cy = (preds[:, 1] - pad_y) / ratio
    w = preds[:, 2] / ratio
    h = preds[:, 3] / ratio
    boxes = np.stack([cx - w / 2, cy - h / 2, w, h], axis=1)
    idx = cv2.dnn.NMSBoxes(boxes.tolist(), conf.tolist(), confidence, NMS_IOU)
    detections = []
    for i in np.array(idx).flatten():
        x0 = max(0.0, float(boxes[i, 0]))
        y0 = max(0.0, float(boxes[i, 1]))
        x1 = min(float(width), float(boxes[i, 0] + boxes[i, 2]))
        y1 = min(float(height), float(boxes[i, 1] + boxes[i, 3]))
        if x1 <= x0 or y1 <= y0:
            continue
        detections.append({
            "cls": classes[int(class_ids[i])],
            "cx": (x0 + x1) / 2,
            "cy": (y0 + y1) / 2,
            "w": x1 - x0,
            "h": y1 - y0,
            "confidence": float(conf[i]),
        })
    return detections


def detect(image_b64, confidence=0.4):
    """Detect items in one base64 JPEG frame. Coordinates are in that frame's pixels."""
    return detect_image(decode_image(image_b64), confidence)


def detect_image(image, confidence=0.4):
    """Detect items in a BGR numpy image. Coordinates are in that image's pixels."""
    net, classes = _load()
    height, width = image.shape[:2]
    canvas, ratio, pad_x, pad_y = _letterbox(image)
    blob = cv2.dnn.blobFromImage(canvas, 1 / 255.0, (INPUT_SIZE, INPUT_SIZE), swapRB=True, crop=False)
    with _lock:  # cv2.dnn nets are not thread safe
        net.setInput(blob)
        output = net.forward()
    return decode_output(output, classes, ratio, pad_x, pad_y, width, height, confidence)


def analyze_video(path, confidence=0.4):
    """Run the model on every frame of a video file.

    Returns {"fps", "width", "height", "frames": [[detection, ...], ...]} where
    frames[i] holds the detections of frame i, in the video's own pixels.
    """
    capture = cv2.VideoCapture(path)
    if not capture.isOpened():
        raise LocalDetectorError("Video could not be opened.")
    fps = capture.get(cv2.CAP_PROP_FPS) or 24.0
    width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
    frames = []
    try:
        while True:
            ok, image = capture.read()
            if not ok:
                break
            dets = detect_image(image, confidence)
            for det in dets:
                for key in ("cx", "cy", "w", "h"):
                    det[key] = round(det[key], 1)
                det["confidence"] = round(det["confidence"], 3)
            frames.append(dets)
    finally:
        capture.release()
    return {"fps": fps, "width": width, "height": height, "frames": frames}
