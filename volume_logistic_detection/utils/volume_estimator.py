"""Estimate the box-shaped volume of one detected item.

Pure Python on purpose: no Odoo import, so it can be unit tested directly.
All lengths are centimetres, all pixel values are image pixels.
"""

DEFAULT_HEIGHT_CM = 20.0

SOURCE_CLASS = "class"
SOURCE_BBOX = "bbox"


def estimate_dimensions(bbox_w_px, bbox_h_px, px_per_cm, default_height_cm=DEFAULT_HEIGHT_CM,
                        class_dims=None):
    """Return (length_cm, width_cm, height_cm, source).

    class_dims: optional (length_cm, width_cm, height_cm) standard size of the
    detected class. When given, it is used as-is, because the class size is
    more trustworthy than an estimate from a 2D image.

    Otherwise the footprint comes from the bounding box scaled by px_per_cm
    (the calibrated scale of the conveyor). The bounding box is treated as the
    footprint with the longer side as length, which ignores the true
    orientation. Height cannot be seen in a 2D box, so default_height_cm is used.
    Irregular items are therefore approximated as a box that bounds them.
    """
    if class_dims is not None:
        length, width, height = class_dims
        _require_positive(length, width, height)
        return float(length), float(width), float(height), SOURCE_CLASS

    if not px_per_cm or px_per_cm <= 0:
        raise ValueError("px_per_cm must be a positive calibration value.")
    _require_positive(bbox_w_px, bbox_h_px, default_height_cm)

    side_a = bbox_w_px / px_per_cm
    side_b = bbox_h_px / px_per_cm
    return max(side_a, side_b), min(side_a, side_b), float(default_height_cm), SOURCE_BBOX


def volume_cm3(length_cm, width_cm, height_cm):
    return float(length_cm) * float(width_cm) * float(height_cm)


def _require_positive(*values):
    for value in values:
        if value is None or value <= 0:
            raise ValueError("Dimensions must be positive numbers.")
