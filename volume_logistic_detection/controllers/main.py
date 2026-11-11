import logging
import os
import threading

from werkzeug.exceptions import NotFound
from werkzeug.utils import send_file

from odoo import http
from odoo.http import request
from odoo.addons.volume_logistic_detection.utils import local_detector, roboflow_client
from odoo.addons.volume_logistic_detection.utils.packer import fill_ratio, fits_empty_truck, pack
from odoo.addons.volume_logistic_detection.utils.tracker import (
    MIN_HITS_TO_COUNT,
    is_countable,
    update_tracks,
)
from odoo.addons.volume_logistic_detection.utils.volume_estimator import (
    DEFAULT_HEIGHT_CM,
    SOURCE_BBOX,
    SOURCE_CLASS,
    estimate_dimensions,
)

_logger = logging.getLogger(__name__)

ASSET_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "asset"))
DEFAULT_CLASS_COLOR = "#4a90d9"
MAX_FRAME_CHARS = 4 * 1024 * 1024  # base64 text, about 3 MB of JPEG

# Sightings (at ~4 per second) before an item is counted during playback.
PLAYBACK_MIN_HITS = 4

# Per-frame detections of each analysed video (about 100 KB each), kept in memory.
_analysis_cache = {}
_analysis_lock = threading.Lock()


def _error(code, message):
    return {"status": "error", "code": code, "message": message}


def _video_names():
    if not os.path.isdir(ASSET_DIR):
        return []
    return sorted(f for f in os.listdir(ASSET_DIR) if f.lower().endswith(".mp4"))


def _clean_tracks(raw):
    """Keep only well-formed track entries sent back by the browser."""
    cleaned = []
    for item in raw or []:
        try:
            track = {
                "id": int(item["id"]),
                "cls": str(item["cls"]),
                "cx": float(item["cx"]),
                "cy": float(item["cy"]),
                "w": float(item["w"]),
                "h": float(item["h"]),
                "hits": int(item["hits"]),
                "missed": int(item["missed"]),
                "dims": None,
            }
        except (KeyError, TypeError, ValueError):
            continue
        dims = item.get("dims")
        if isinstance(dims, dict) and dims.get("source") in (SOURCE_CLASS, SOURCE_BBOX):
            try:
                track["dims"] = {
                    "l": float(dims["l"]),
                    "w": float(dims["w"]),
                    "h": float(dims["h"]),
                    "source": dims["source"],
                    "color": str(dims.get("color") or DEFAULT_CLASS_COLOR)[:9],
                }
            except (KeyError, TypeError, ValueError):
                track["dims"] = None
        cleaned.append(track)
    return cleaned


class VolumeDetectionController(http.Controller):

    @http.route("/volume_detection/videos", type="jsonrpc", auth="user")
    def videos(self):
        return _video_names()

    @http.route("/volume_detection/video/<string:filename>", type="http", auth="user")
    def video(self, filename):
        if filename not in _video_names():
            raise NotFound()
        return send_file(
            os.path.join(ASSET_DIR, filename),
            request.httprequest.environ,
            mimetype="video/mp4",
            conditional=True,
        )

    @http.route("/volume_detection/session/start", type="jsonrpc", auth="user", methods=["POST"])
    def session_start(self, truck_id, px_per_cm, default_height_cm=DEFAULT_HEIGHT_CM, name=None):
        session = request.env["vld.session"].create({
            "name": name or "Session",
            "truck_id": int(truck_id),
            "px_per_cm": float(px_per_cm),
            "default_height_cm": float(default_height_cm),
        })
        return {"status": "ok", "session_id": session.id}

    @http.route("/volume_detection/session/reset", type="jsonrpc", auth="user", methods=["POST"])
    def session_reset(self, session_id):
        session = request.env["vld.session"].browse(int(session_id)).exists()
        if not session:
            return _error("not_found", "Session not found.")
        session.item_ids.unlink()
        session.state = "running"
        return {"status": "ok"}

    @http.route("/volume_detection/detect_frame", type="jsonrpc", auth="user", methods=["POST"])
    def detect_frame(self, session_id, image_data, tracks=None, scale=1.0):
        session = request.env["vld.session"].browse(int(session_id)).exists()
        if not session:
            return _error("not_found", "Session not found.")
        if session.state == "done":
            return _error("closed", "This session is closed.")

        frame_b64 = roboflow_client.strip_data_url(image_data or "")
        if not frame_b64:
            return _error("no_frame", "No frame received.")
        if len(frame_b64) > MAX_FRAME_CHARS:
            return _error("frame_too_large", "Frame is too large. Lower the capture size.")

        icp = request.env["ir.config_parameter"].sudo()
        confidence = icp.get_float("volume_logistic_detection.confidence", 0.4)
        use_local = (
            local_detector.is_available()
            and icp.get_str("volume_logistic_detection.detector") != "roboflow"
        )
        try:
            if use_local:
                detections = local_detector.detect(frame_b64, confidence=confidence)
            else:
                detections = roboflow_client.detect(
                    api_key=icp.get_str("volume_logistic_detection.roboflow_api_key"),
                    model_id=icp.get_str("volume_logistic_detection.roboflow_model_id"),
                    version=icp.get_str("volume_logistic_detection.roboflow_version"),
                    image_b64=frame_b64,
                    confidence=confidence,
                )
        except (roboflow_client.RoboflowError, local_detector.LocalDetectorError) as exc:
            return _error("detector_error", str(exc))

        # The browser may send a downscaled frame; report boxes in full-frame pixels.
        try:
            scale = float(scale)
        except (TypeError, ValueError):
            scale = 1.0
        if scale > 0 and scale != 1.0:
            for det in detections:
                for key in ("cx", "cy", "w", "h"):
                    det[key] *= scale

        return self._process(session, detections, tracks)

    @http.route("/volume_detection/track_frame", type="jsonrpc", auth="user", methods=["POST"])
    def track_frame(self, session_id, boxes, tracks=None):
        """Track and pack boxes the browser already has (from /analyze), no image needed."""
        session = request.env["vld.session"].browse(int(session_id)).exists()
        if not session:
            return _error("not_found", "Session not found.")
        if session.state == "done":
            return _error("closed", "This session is closed.")
        detections = []
        for box in boxes or []:
            try:
                detections.append({
                    "cls": str(box["cls"]),
                    "cx": float(box["cx"]), "cy": float(box["cy"]),
                    "w": float(box["w"]), "h": float(box["h"]),
                    "confidence": float(box.get("confidence", 0)),
                })
            except (KeyError, TypeError, ValueError):
                continue
        # Boxes arrive many times a second, so require a longer, steadier sighting
        # before counting; this filters out detections that flicker for a moment.
        return self._process(session, detections, tracks, min_hits=PLAYBACK_MIN_HITS)

    @http.route("/volume_detection/analyze", type="jsonrpc", auth="user", methods=["POST"])
    def analyze(self, video):
        """Detect items in every frame of a bundled video once, so playback can draw
        each frame's boxes exactly in sync. The result is cached per video."""
        if video not in _video_names():
            return _error("not_found", "Video not found.")
        if not local_detector.is_available():
            return _error("no_local_model", "The local detector model is not installed.")
        icp = request.env["ir.config_parameter"].sudo()
        confidence = icp.get_float("volume_logistic_detection.confidence", 0.4)
        path = os.path.join(ASSET_DIR, video)
        key = (video, os.path.getmtime(path), os.path.getmtime(local_detector.MODEL_PATH), confidence)
        with _analysis_lock:
            result = _analysis_cache.get(key)
            if result is None:
                try:
                    result = local_detector.analyze_video(path, confidence)
                except local_detector.LocalDetectorError as exc:
                    return _error("detector_error", str(exc))
                _analysis_cache[key] = result
        return {"status": "ok", **result}

    def _process(self, session, detections, tracks, min_hits=MIN_HITS_TO_COUNT):
        tracks = update_tracks(_clean_tracks(tracks), detections, min_hits=min_hits)

        item_classes = {c.name: c for c in request.env["vld.item.class"].search([])}
        for track in tracks:
            if track["dims"] is None and is_countable(track, min_hits=min_hits):
                item_class = item_classes.get(track["cls"])
                length, width, height, source = estimate_dimensions(
                    track["w"], track["h"],
                    px_per_cm=session.px_per_cm,
                    default_height_cm=session.default_height_cm,
                    class_dims=item_class._standard_dims() if item_class else None,
                )
                track["dims"] = {
                    "l": length, "w": width, "h": height, "source": source,
                    "color": item_class.color if item_class else DEFAULT_CLASS_COLOR,
                }

        counted = [t for t in tracks if t["dims"] is not None]
        truck = session.truck_id
        placements = pack(
            truck.length_cm, truck.width_cm, truck.height_cm,
            [{"id": t["id"], "l": t["dims"]["l"], "w": t["dims"]["w"], "h": t["dims"]["h"]}
             for t in counted],
        )
        by_id = {t["id"]: t for t in counted}

        session.item_ids.unlink()
        vals = []
        for seq, place in enumerate(placements, start=1):
            track = by_id[place["id"]]
            vals.append({
                "session_id": session.id,
                "sequence": seq * 10,
                "track_ref": track["id"],
                "class_name": track["cls"],
                "length_cm": place["l"],
                "width_cm": place["w"],
                "height_cm": place["h"],
                "source": track["dims"]["source"],
                "color": track["dims"]["color"],
                "pos_x": place["x"] or 0.0,
                "pos_y": place["y"] or 0.0,
                "pos_z": place["z"] or 0.0,
                "fitted": place["fitted"],
            })
        request.env["vld.session.item"].create(vals)

        # The truck is full when an item that could fit in an empty truck has no room left.
        # An item bigger than the truck itself (usually a measuring error) is only marked
        # "does not fit"; it must not end the run.
        any_overflow = any(
            not p["fitted"] and fits_empty_truck(truck.length_cm, truck.width_cm, truck.height_cm, p)
            for p in placements
        )
        session.state = "full" if any_overflow else "running"

        return {
            "status": "ok",
            "tracks": tracks,
            "detections": len(detections),
            "boxes": detections,
            "truck": {"l": truck.length_cm, "w": truck.width_cm, "h": truck.height_cm},
            "items": [
                {
                    "id": p["id"], "class": by_id[p["id"]]["cls"],
                    "l": p["l"], "w": p["w"], "h": p["h"],
                    "x": p["x"], "y": p["y"], "z": p["z"],
                    "fitted": p["fitted"],
                    "source": by_id[p["id"]]["dims"]["source"],
                    "color": by_id[p["id"]]["dims"]["color"],
                    "volume": p["l"] * p["w"] * p["h"],
                }
                for p in placements
            ],
            "fill_ratio": fill_ratio(truck.length_cm, truck.width_cm, truck.height_cm, placements),
            "state": session.state,
        }
