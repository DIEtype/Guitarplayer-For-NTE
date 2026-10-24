"""Machine-local calibration; never included in source or release archives."""
import copy
import json
from pathlib import Path

from .game_profile import GameProfile


def read_layouts(directory):
    if directory is None:
        return {}
    try:
        data = json.loads((Path(directory) / 'layouts.json').read_text(encoding='utf-8'))
        result = {}
        for mode, raw in data.items():
            profile = GameProfile.from_dict(raw)
            if profile.calibrated and mode == profile.mode:
                result[mode] = profile
        return result
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return {}


def write_layout(directory, profile):
    if directory is None:
        return
    layouts = read_layouts(directory)
    layouts[profile.mode] = profile
    path = Path(directory) / 'layouts.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.json.tmp')
    temporary.write_text(json.dumps({mode: value.to_dict() for mode, value in layouts.items()},
                                    ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)


def reuse_layout(score_profile, local_profile):
    """Keep song start state; reuse matching sector coordinates for this PC."""
    profile = copy.deepcopy(score_profile)
    if local_profile is not None and local_profile.calibrated and local_profile.mode == profile.mode:
        if set(profile.chords) == set(local_profile.chords) and profile.pages == local_profile.pages:
            profile.chords = dict(local_profile.chords)
            profile.strum_point = local_profile.strum_point
            profile.reference_size = local_profile.reference_size
            profile.calibrated = True
    return profile
