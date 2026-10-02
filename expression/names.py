# -*- coding: utf-8 -*-
"""Name tables: the 52 ARKit shapes (and how to recognise them under other spellings), the MMD
expression panels, and small helpers shared by every module.  No bpy import - usable anywhere."""
import re

# Apple's order (ARFaceAnchor.BlendShapeLocation); Faceit's ARKit table uses the same order.
ARKIT_52 = [
    "eyeBlinkLeft", "eyeLookDownLeft", "eyeLookInLeft", "eyeLookOutLeft", "eyeLookUpLeft", "eyeSquintLeft",
    "eyeWideLeft", "eyeBlinkRight", "eyeLookDownRight", "eyeLookInRight", "eyeLookOutRight", "eyeLookUpRight",
    "eyeSquintRight", "eyeWideRight", "jawForward", "jawLeft", "jawRight", "jawOpen", "mouthClose", "mouthFunnel",
    "mouthPucker", "mouthRight", "mouthLeft", "mouthSmileLeft", "mouthSmileRight", "mouthFrownRight",
    "mouthFrownLeft", "mouthDimpleLeft", "mouthDimpleRight", "mouthStretchLeft", "mouthStretchRight",
    "mouthRollLower", "mouthRollUpper", "mouthShrugLower", "mouthShrugUpper", "mouthPressLeft", "mouthPressRight",
    "mouthLowerDownLeft", "mouthLowerDownRight", "mouthUpperUpLeft", "mouthUpperUpRight", "browDownLeft",
    "browDownRight", "browInnerUp", "browOuterUpLeft", "browOuterUpRight", "cheekPuff", "cheekSquintLeft",
    "cheekSquintRight", "noseSneerLeft", "noseSneerRight", "tongueOut",
]

# PMX expression panels (mmd_tools' vertex/bone morph "category" values) and their labels
CATEGORIES = ("EYEBROW", "EYE", "MOUTH", "OTHER")
CATEGORY_LABELS = {"EYEBROW": "眉", "EYE": "目", "MOUTH": "口", "OTHER": "其他"}


def arkit_category(name):
    """The PMX panel an ARKit shape would sit in (used for strengths and for filtering)."""
    if name.startswith("brow"):
        return "EYEBROW"
    if name.startswith(("eye", "cheekSquint")):
        return "EYE"
    if name.startswith(("jaw", "mouth", "tongue", "cheekPuff")):
        return "MOUTH"
    return "OTHER"


def _norm(name):
    """'eyeBlink_L', 'EyeBlinkLeft', 'Face.eyeBlinkLeft', 'Eye_Blink_L', 'eyeBlink.L' -> 'eyeblinkleft'.
    A side is only read from a separate token (_L, .R, ' left') or a camel-case tail (...kLeft,
    ...kL), so words that merely end in l/r (mouthFunnel, mouthRollUpper) stay as they are."""
    n = name.split(".")[-1] if re.search(r"\.[A-Za-z]{3,}", name) else name     # 'Face.' style prefixes
    side = ""
    m = re.match(r"^(.*?)[\s_\-.]+(left|right|l|r)$", n, re.IGNORECASE)
    if m:
        n, side = m.group(1), m.group(2).lower()
    else:
        m = re.match(r"^(.*[a-z])(Left|Right|L|R)$", n)
        if m:
            n, side = m.group(1), m.group(2).lower()
    side = {"l": "left", "r": "right"}.get(side, side)
    return re.sub(r"[\s_\-.]", "", n).lower() + side


_KNOWN = {_norm(a): a for a in ARKIT_52}


def match_arkit(key_names):
    """{ARKit name: shape key name} for keys that are ARKit shapes under any common spelling.
    An exact ARKit name always wins over an alias."""
    out = {}
    for key in key_names:
        if key in ARKIT_52:
            out[key] = key
    for key in key_names:
        arkit = _KNOWN.get(_norm(key))
        if arkit and arkit not in out:
            out[arkit] = key
    return out


def scaled(components, factor):
    """A {name: weight} recipe with every weight multiplied (the category strength)."""
    return {k: v * factor for k, v in components.items()}


def optional(name):
    """Recipe keys starting with '?' are optional: used when the model has them, skipped otherwise
    (e.g. Stellar Blade's separate 'TeethLowerDown' key that must ride along with jawOpen)."""
    return name.startswith("?"), name.lstrip("?")
