"""Speech timing for a portrait that already contains a visible mouth opening."""

import numpy as np

from .speech_constraints import _smooth, activity, transcript_targets

MODE = 'source_occlusion_v1'


def build_plan(pcm, frames, chars, source_opening):
    """Drive occlusion from speech and aligned syllables, within the photo's gap."""
    audio = activity(pcm, 16000, frames)
    gate, rms = audio['gate'], audio['rms']
    vowel, protected, _, _ = transcript_targets(chars, rms, frames)
    voiced = rms[gate > .8]
    normal_level = max(.006, float(np.percentile(voiced, 85))) if len(voiced) else .006
    energy = np.clip(np.log1p(rms / .002) / np.log1p(normal_level / .002), 0, 1)
    vowel = np.clip(vowel / 6., 0, 1)
    # Keep the original smile as the open pose. Vowels approach it, and only
    # short labial consonants need to bring the lip edges together.
    closure = .52 - .34 * energy - .18 * vowel
    closure[protected] = 1.
    closure = _smooth(closure, .58)
    closure[protected] = 1.
    closure = np.clip(closure, 0, 1) * gate
    # Return toward the original pose at speech boundaries so the last
    # voiced frame does not jump from a narrowed mouth to the static smile.
    audible = gate > 0
    adjacent_silence = audible & (~np.r_[False, audible[:-1]] | ~np.r_[audible[1:], False])
    closure[adjacent_silence & ~protected] *= .60
    actual_opening = source_opening * (1 - closure)
    return {
        'opening': (actual_opening * gate).astype(np.float32),
        'source_aperture': actual_opening.astype(np.float32),
        'source_closure': closure.astype(np.float32),
        'width_ratio': np.ones(frames, np.float32),
        'upper_raise': np.zeros(frames, np.float32),
        'gate': gate,
        'protected': protected,
    }
