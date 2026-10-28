"""Pixel-identical mouth blending with bounded floating-point temporaries."""
import numpy as np


class MouthBlend:
    def __init__(self, source, mask):
        self.source = source
        ys, xs = np.nonzero(mask[..., 0])
        self.roi = (slice(int(ys.min()), int(ys.max()) + 1),
                    slice(int(xs.min()), int(xs.max()) + 1)) if len(xs) else None
        if self.roi:
            self.mask = mask[self.roi].copy()
            # Exactly the same dtype, operation order and rounding as the
            # former full-frame expression. Only the unchanged term is cached.
            self.background = source[self.roi] * (1 - self.mask)

    def apply(self, generated):
        result = self.source.copy()
        if self.roi:
            result[self.roi] = np.clip(generated[self.roi] * self.mask +
                                       self.background, 0, 255).astype('uint8')
        return result
