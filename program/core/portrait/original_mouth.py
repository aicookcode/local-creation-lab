"""Conservative visible-teeth detection in the original photo's inner lips."""

import cv2
import numpy as np


def visible_teeth(rgb, landmarks):
    """Return pixel evidence for a reusable oral texture, or None."""
    mouth = np.asarray(landmarks[48:68], dtype=np.float32)
    if mouth.shape != (20, 2) or not np.isfinite(mouth).all():
        return None
    axis = mouth[6] - mouth[0]
    width = float(np.linalg.norm(axis))
    if width < 24:
        return None
    axis /= width
    normal = np.array([-axis[1], axis[0]], dtype=np.float32)
    inner = mouth[12:20]
    along = (inner - mouth[0]) @ axis
    gaps = (inner[[7, 6, 5]] - inner[[1, 2, 3]]) @ normal
    opening = float(np.mean(np.maximum(gaps, 0)))
    if opening < max(2, .024 * width) or gaps.max() < 2.5:
        return None

    # Rasterize in mouth coordinates. This excludes lips, skin and background
    # from the candidate band and keeps the test stable under head tilt.
    across = (inner - mouth[0]) @ normal
    top = int(np.floor(across.min())) - 1
    bottom = int(np.ceil(across.max())) + 2
    patch_width = int(np.ceil(width)) + 1
    transform = np.float32([axis, normal])
    matrix = np.column_stack((transform, -transform @ mouth[0] - [0, top]))
    patch = cv2.warpAffine(rgb, matrix, (patch_width, bottom - top))
    # Subpixel coverage keeps a thin but clearly exposed tooth band usable.
    points = np.rint(np.column_stack((along, across - top)) * 4 + 1.5).astype(np.int32)
    interior = np.zeros(((bottom - top) * 4, patch_width * 4), np.uint8)
    cv2.fillPoly(interior, [points], 255)
    mask = cv2.resize(interior, (patch_width, bottom - top), interpolation=cv2.INTER_AREA) >= 224
    mask[:, :round(width * .15)] = False
    mask[:, round(width * .85):] = False
    if np.count_nonzero(mask) < 24:
        return None

    colors = np.clip(patch, 0, 255).astype(np.uint8)
    lab = cv2.cvtColor(colors, cv2.COLOR_RGB2LAB)
    light = lab[:, :, 0]
    red = lab[:, :, 1]
    dark = float(np.percentile(light[mask], 20))
    candidate = mask & (light >= max(95, dark + 24)) & (red <= 151)
    # Group neighboring teeth across narrow shadows between them. Count only
    # original candidate pixels as evidence, including after grouping.
    link_width = 2 * max(1, round(width * .025)) + 1
    grouped = cv2.morphologyEx(candidate.astype(np.uint8), cv2.MORPH_CLOSE,
                               np.ones((1, link_width), np.uint8))
    grouped &= mask.astype(np.uint8)
    _, labels, components, _ = cv2.connectedComponentsWithStats(grouped)
    evidence_counts = np.bincount(labels[candidate], minlength=len(components))
    minimum_pixels = max(12, .12 * np.count_nonzero(mask))
    for index, (_, _, span, height, _) in enumerate(components[1:], 1):
        area = evidence_counts[index]
        if span >= width * .23 and height >= 2 and area >= minimum_pixels:
            return {
                'opening_photo_px': round(opening, 2),
                'tooth_band_width_fraction': round(float(span / width), 3),
                'tooth_band_pixels': int(area),
            }
    return None
