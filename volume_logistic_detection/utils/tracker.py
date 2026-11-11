"""Frame-to-frame tracking so one physical item is counted once.

Stateless on the server: the client keeps the track list and sends it back
with every frame. Each call returns the updated list. Pure Python, no Odoo.

Track dict keys: id, cls, cx, cy, w, h, hits, missed, dims (None until counted).
Detection dict keys: cls, cx, cy, w, h, confidence.
"""

MIN_HITS_TO_COUNT = 2
MAX_MISSED_FRAMES = 5
MATCH_DISTANCE_FACTOR = 0.5


def update_tracks(tracks, detections,
                  min_hits=MIN_HITS_TO_COUNT,
                  max_missed=MAX_MISSED_FRAMES,
                  distance_factor=MATCH_DISTANCE_FACTOR):
    """Match this frame's detections to existing tracks.

    A detection matches the nearest unmatched live track of the same class whose
    centre lies within distance_factor * the larger box side. Unmatched
    detections start new tracks. A live track missing for more than max_missed
    frames is retired: it no longer matches anything. Retired tracks that were
    already counted (dims set) are kept, because the item stays in the truck
    after it leaves the camera view. Returns a new list of track dicts.
    """
    updated = [dict(track) for track in tracks]
    matched = set()
    next_id = max((track["id"] for track in updated), default=0) + 1

    for det in sorted(detections, key=lambda d: d.get("confidence", 0), reverse=True):
        best_index, best_dist = None, None
        for index, track in enumerate(updated):
            if index in matched or track["cls"] != det["cls"] or track["missed"] > max_missed:
                continue
            dist = ((track["cx"] - det["cx"]) ** 2 + (track["cy"] - det["cy"]) ** 2) ** 0.5
            limit = max(track["w"], track["h"], det["w"], det["h"]) * distance_factor
            if dist <= limit and (best_dist is None or dist < best_dist):
                best_index, best_dist = index, dist

        if best_index is None:
            updated.append({
                "id": next_id, "cls": det["cls"], "cx": det["cx"], "cy": det["cy"],
                "w": det["w"], "h": det["h"], "hits": 1, "missed": 0, "dims": None,
            })
            matched.add(len(updated) - 1)
            next_id += 1
        else:
            track = updated[best_index]
            track.update(cx=det["cx"], cy=det["cy"], w=det["w"], h=det["h"],
                         hits=track["hits"] + 1, missed=0)
            matched.add(best_index)

    for index, track in enumerate(updated):
        if index not in matched and index < len(tracks):
            track["missed"] += 1

    return [track for track in updated
            if track["missed"] <= max_missed or track["dims"] is not None]


def is_countable(track, min_hits=MIN_HITS_TO_COUNT):
    return track["hits"] >= min_hits
