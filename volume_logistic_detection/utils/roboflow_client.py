"""Call a Roboflow hosted model on one video frame.

Pure HTTP code with no Odoo import. The caller passes the API key, so the key
never leaves the server. Returns detections in the shape tracker.py expects.
"""
import base64
import logging

import requests

_logger = logging.getLogger(__name__)

ROBOFLOW_URL = "https://serverless.roboflow.com/{model_id}/{version}"
TIMEOUT_SECONDS = 10


class RoboflowError(Exception):
    """Raised when the Roboflow request fails or returns an unexpected reply."""


def strip_data_url(image_data):
    """Return the raw base64 payload from a 'data:image/...;base64,' URL."""
    if image_data.startswith("data:"):
        _, _, image_data = image_data.partition(",")
    return image_data


def detect(api_key, model_id, version, image_b64, confidence=0.4, session=None):
    """Run the model on one base64 JPEG frame and return detection dicts.

    Each dict: cls, cx, cy, w, h, confidence (cx/cy is the box centre in pixels).
    """
    if not api_key or not model_id or not version:
        raise RoboflowError("Roboflow API key, model id, and version must be configured.")

    try:
        base64.b64decode(image_b64, validate=True)
    except (ValueError, TypeError) as exc:
        raise RoboflowError("Frame is not valid base64 image data.") from exc

    http = session or requests
    try:
        response = http.post(
            ROBOFLOW_URL.format(model_id=model_id, version=version),
            params={"api_key": api_key, "confidence": int(round(confidence * 100))},
            data=image_b64,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        _logger.warning("Roboflow request failed: %s", exc)
        raise RoboflowError("Could not reach Roboflow.") from exc

    if response.status_code != 200:
        _logger.warning("Roboflow returned HTTP %s", response.status_code)
        raise RoboflowError(f"Roboflow returned HTTP {response.status_code}.")

    try:
        predictions = response.json().get("predictions", [])
    except ValueError as exc:
        raise RoboflowError("Roboflow returned a non-JSON response.") from exc

    return [
        {
            "cls": pred["class"],
            "cx": float(pred["x"]),
            "cy": float(pred["y"]),
            "w": float(pred["width"]),
            "h": float(pred["height"]),
            "confidence": float(pred.get("confidence", 0)),
        }
        for pred in predictions
        if "class" in pred and "x" in pred and "y" in pred
        and "width" in pred and "height" in pred
    ]
