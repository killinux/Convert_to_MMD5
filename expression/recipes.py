# -*- coding: utf-8 -*-
"""Recipe sets: which expressions to make and how each source vocabulary makes them.

A target is a dict: name (the exact MMD / ARKit name - VMD motions and face trackers match these
byte for byte), name_e, category (the PMX panel), and one recipe per vocabulary:

  arkit      {ARKit shape: weight}        - mixed from existing ARKit shape keys ('?Name' = optional key)
  metahuman  {raw control: weight}        - evaluated through the MetaHuman DNA (RigLogic)
  roles      [(role, kind, amount), ...]  - calibrated bone actions for bone-only faces (roles.py)
  names      {pose / key name: weight}    - poses of the pose library (identity by default)

A source uses the recipe of its own vocabulary; a target without one is skipped for that source
and reported.  Weights were tuned by rendering: the ARKit mixes on the Stellar Blade Fiona mod, the
MetaHuman ones on Vindictus Fiona, the role ones on Rise of Eros heads.  瞳小 scales the pupil joints,
so it only exists as a vertex morph.  Targets marked ``extra``
are the less common names a Tda-style motion also keys (aliases, one-sided brows ...).
"""
import json

from . import roles as R
from .names import ARKIT_52, arkit_category

MMD, ARKIT = "MMD", "ARKIT"
SETS = {MMD: "MMD 表情", ARKIT: "ARKit 52"}


def T(name, name_e, category, arkit=None, metahuman=None, roles=None, extra=False, **more):
    t = {"name": name, "name_e": name_e, "category": category, "arkit": arkit, "metahuman": metahuman,
         "roles": roles, "extra": extra}
    t.update(more)
    return t


def mix(*parts):
    out = {}
    for part in parts:
        for k, v in part.items():
            out[k] = out.get(k, 0.0) + v
    return out


def ak(stem, v, sides="LR"):
    """ARKit pair: ak('eyeBlink', 1) -> eyeBlinkLeft / eyeBlinkRight."""
    return {stem + {"L": "Left", "R": "Right"}[s]: v for s in sides}


def mh(stem, v, sides="LR"):
    """MetaHuman pair: mh('eyeBlink', 1) -> eyeBlinkL / eyeBlinkR."""
    return {stem + s: v for s in sides}


def quad(stem, v):
    return {stem + k: v for k in ("UL", "UR", "DL", "DR")}


TEETH = "?TeethLowerDown"        # Stellar Blade keeps the lower teeth in a key of their own


def jaw_ak(v, **more):
    return mix({"jawOpen": v, TEETH: v}, more)


# ------------------------------------------------------------------------------------------------
# MMD: the standard expression set (+ extras).  Sides: ウィンク / ～左 = the character's own left.
# ------------------------------------------------------------------------------------------------
BROW_ONE_SIDED = {   # role recipes of the one-sided brow extras (same as the two-sided ones)
    "困る": dict(inner=(-0.02, 0.0, 0.09), centre=(0.0, 0.0, 0.03), outer=(0.0, 0.0, -0.05),
               root=(0.0, 0.0, 0.02), tilt=-12.0),
    "にこり": dict(inner=(0.0, 0.0, 0.02), centre=(0.0, 0.0, 0.05), outer=(0.0, 0.0, 0.03), root=(0.0, 0.0, 0.03)),
    "怒り": dict(inner=(-0.05, 0.0, -0.09), centre=(0.0, 0.0, -0.03), outer=(0.0, 0.0, 0.05),
               root=(0.0, 0.0, -0.01), tilt=14.0),
    "上": dict(root=(0.0, 0.0, 0.10)),
    "下": dict(root=(0.0, 0.0, -0.08)),
}

MMD_TARGETS = [
    # --- 目
    T("まばたき", "blink", "EYE", ak("eyeBlink", 1.0), mh("eyeBlink", 1.0), R.lids("LR", 1.0, 1.0)),
    T("笑い", "smile", "EYE", mix(ak("eyeBlink", 0.8), ak("eyeSquint", 0.5), ak("cheekSquint", 0.5)),
      mix(mh("eyeBlink", 0.85), mh("eyeSquintInner", 0.5), mh("eyeCheekRaise", 0.6)), R.SMILE),
    T("ウィンク", "wink", "EYE", mix(ak("eyeBlink", 1.0, "L"), ak("eyeSquint", 0.4, "L"), ak("cheekSquint", 0.4, "L")),
      mix(mh("eyeBlink", 1.0, "L"), mh("eyeSquintInner", 0.4, "L"), mh("eyeCheekRaise", 0.4, "L")),
      R.lids("L", 1.0, 1.0)),
    T("ウィンク右", "wink_R", "EYE", mix(ak("eyeBlink", 1.0, "R"), ak("eyeSquint", 0.4, "R"), ak("cheekSquint", 0.4, "R")),
      mix(mh("eyeBlink", 1.0, "R"), mh("eyeSquintInner", 0.4, "R"), mh("eyeCheekRaise", 0.4, "R")),
      R.lids("R", 1.0, 1.0)),
    T("ウィンク２", "wink2", "EYE", ak("eyeBlink", 1.0, "L"), mh("eyeBlink", 1.0, "L"), R.lids("L", 0.45, 2.2)),
    T("ｳｨﾝｸ２右", "wink2_R", "EYE", ak("eyeBlink", 1.0, "R"), mh("eyeBlink", 1.0, "R"), R.lids("R", 0.45, 2.2)),
    T("なごみ", "calm", "EYE", mix(ak("eyeBlink", 0.5), ak("cheekSquint", 0.2)), mh("eyeBlink", 0.5),
      R.lids("LR", 0.5, 0.5)),
    T("はぅ", "close><", "EYE", mix(ak("eyeBlink", 1.0), ak("eyeSquint", 1.0), ak("cheekSquint", 0.6)),
      mix(mh("eyeBlink", 1.0), mh("eyeSquintInner", 1.0), mh("eyeCheekRaise", 0.6)), R.lids("LR", 0.85, 1.3)),
    T("びっくり", "surprised", "EYE", ak("eyeWide", 1.0), mix(mh("eyeWiden", 1.0), mh("eyeUpperLidUp", 0.5)),
      R.lids("LR", -0.25, -0.4)),
    T("じと目", "jito-eye", "EYE", ak("eyeBlink", 0.45), mh("eyeBlink", 0.45), R.lids("LR", 0.55, 0.0)),
    T("キリッ", "kiri", "EYE", mix(ak("eyeSquint", 0.5), ak("eyeBlink", 0.1)),
      mix(mh("eyeSquintInner", 0.5), mh("eyeBlink", 0.1)), R.lid_roll("LR", 10.0) + R.lids("LR", 0.12, 0.2)),
    # 瞳小 is keyed by ~20% of MMD motions (a count over 483); a MetaHuman DNA scales the pupil joints
    # (eyePupilNarrow -0.7 at 1.0) - scale, so it can only be a vertex morph
    T("瞳小", "pupil_small", "EYE", None, mh("eyePupilNarrow", 0.6), None),
    # --- 眉
    T("真面目", "serious", "EYEBROW", ak("browDown", 0.35), mh("browDown", 0.35),
      R.brows(inner=(-0.02, 0.0, -0.04), centre=(0.0, 0.0, -0.01), outer=(0.0, 0.0, 0.02), tilt=5.0)),
    T("困る", "trouble", "EYEBROW", {"browInnerUp": 1.0}, mh("browRaiseIn", 1.0), R.brows(**BROW_ONE_SIDED["困る"])),
    T("にこり", "cheerful", "EYEBROW", ak("browOuterUp", 0.6), mh("browRaiseOuter", 0.6),
      R.brows(**BROW_ONE_SIDED["にこり"])),
    T("怒り", "anger", "EYEBROW", ak("browDown", 1.0), mix(mh("browDown", 1.0), mh("browLateral", 0.6)),
      R.brows(**BROW_ONE_SIDED["怒り"])),
    T("上", "brow_up", "EYEBROW", mix({"browInnerUp": 0.6}, ak("browOuterUp", 0.8)),
      mix(mh("browRaiseIn", 0.6), mh("browRaiseOuter", 0.8)), R.brows(**BROW_ONE_SIDED["上"])),
    T("下", "brow_down", "EYEBROW", ak("browDown", 0.6), mh("browDown", 0.6), R.brows(**BROW_ONE_SIDED["下"])),
    # --- 口
    T("あ", "a", "MOUTH", jaw_ak(0.6, **ak("mouthLowerDown", 0.3), **ak("mouthUpperUp", 0.2)),
      mix({"jawOpen": 0.6}, mh("mouthLowerLipDepress", 0.3), mh("mouthUpperLipRaise", 0.2)), R.jaw(R.JAW_DEG)),
    T("い", "i", "MOUTH", jaw_ak(0.08, **ak("mouthStretch", 0.8), **ak("mouthUpperUp", 0.3), **ak("mouthLowerDown", 0.3)),
      mix({"jawOpen": 0.08}, mh("mouthStretch", 0.8), mh("mouthUpperLipRaise", 0.3), mh("mouthLowerLipDepress", 0.3)),
      R.jaw(6.0) + R.corners(out=0.14)),
    T("う", "u", "MOUTH", jaw_ak(0.05, mouthPucker=1.0, mouthFunnel=0.3),
      mix({"jawOpen": 0.05}, quad("mouthLipsPurse", 1.0), quad("mouthFunnel", 0.3)),
      R.jaw(7.0) + R.corners(out=-0.12, fwd=0.04)),
    T("え", "e", "MOUTH", jaw_ak(0.3, **ak("mouthStretch", 0.5), **ak("mouthLowerDown", 0.3)),
      mix({"jawOpen": 0.3}, mh("mouthStretch", 0.5), mh("mouthLowerLipDepress", 0.3)),
      R.jaw(12.0) + R.corners(out=0.07)),
    T("お", "o", "MOUTH", jaw_ak(0.45, mouthFunnel=0.8, mouthPucker=0.3),
      mix({"jawOpen": 0.45}, quad("mouthFunnel", 0.8), quad("mouthLipsPurse", 0.3)),
      R.jaw(13.0) + R.corners(out=-0.10, fwd=0.03)),
    T("▲", "triangle", "MOUTH", jaw_ak(0.12, mouthFunnel=0.4, **ak("mouthFrown", 0.3)),
      mix({"jawOpen": 0.12}, quad("mouthFunnel", 0.4), mh("mouthCornerDepress", 0.3)),
      R.jaw(7.0) + R.corners(out=-0.09, up=-0.04)),
    T("∧", "mouth_∧", "MOUTH", ak("mouthFrown", 1.0), mh("mouthCornerDepress", 1.0),
      R.corners(out=-0.04, up=-0.07) + R.lip("lower_lip_C", up=0.02) + R.lip("lower_lip_L", up=0.01)
      + R.lip("lower_lip_R", up=0.01)),
    T("ω", "omega", "MOUTH", None, None, R.OMEGA),
    T("ω□", "omega_open", "MOUTH", None, None, R.OMEGA + R.jaw(6.0)),
    T("にっこり", "smile_mouth", "MOUTH", ak("mouthSmile", 0.6), mh("mouthCornerPull", 0.6), R.corners(out=0.03, up=0.07)),
    # a full mouthCornerPull bunched the cheeks on the 4-weight Fiona - 0.8 (fine with full weights)
    T("にやり", "grin", "MOUTH", ak("mouthSmile", 1.0), mh("mouthCornerPull", 0.8), R.corners(out=0.05, up=0.07)),
    T("にやり２", "grin2", "MOUTH", mix(ak("mouthSmile", 1.0), ak("mouthDimple", 0.4), ak("cheekSquint", 0.3)),
      mix(mh("mouthCornerPull", 1.0), mh("mouthDimple", 0.4)), R.corners(out=0.06, up=0.10)),
    T("ぺろっ", "tongue_out", "MOUTH", jaw_ak(0.25, tongueOut=1.0), {"jawOpen": 0.25, "tongueOut": 1.0}, R.PERO),
    T("てへぺろ", "tehepero", "MOUTH", jaw_ak(0.2, tongueOut=0.8, mouthLeft=0.3, eyeBlinkLeft=1.0),
      {"jawOpen": 0.2, "tongueOut": 0.8, "tongueLeft": 0.5, "eyeBlinkL": 1.0},
      R.PERO + R.tongue(yaw=25.0) + R.lids("L", 1.0, 1.0)),
    T("てへぺろ２", "tehepero2", "MOUTH", jaw_ak(0.2, tongueOut=0.8, mouthRight=0.3, eyeBlinkRight=1.0),
      {"jawOpen": 0.2, "tongueOut": 0.8, "tongueRight": 0.5, "eyeBlinkR": 1.0},
      R.PERO + R.tongue(yaw=-25.0) + R.lids("R", 1.0, 1.0)),
    T("口角上げ", "mouth_corner_up", "MOUTH", ak("mouthSmile", 0.45), mh("mouthCornerPull", 0.45), R.corners(up=0.08)),
    T("口角下げ", "mouth_corner_down", "MOUTH", ak("mouthFrown", 0.6), mh("mouthCornerDepress", 0.6),
      R.corners(up=-0.08)),
    T("口横広げ", "mouth_wide", "MOUTH", ak("mouthStretch", 0.6), mh("mouthStretch", 0.6), R.corners(out=0.15)),
    T("歯無し上", "no_upper_teeth", "MOUTH", None, None, R.teeth("up", back=0.20, up=0.18)),
    T("歯無し下", "no_lower_teeth", "MOUTH", None, None, R.teeth("dw", back=0.20, up=-0.18)),
    # --- extras: aliases, more eyes / mouths, one-sided brows
    T("ウィンク２右", "wink2_R_fw", "EYE", ak("eyeBlink", 1.0, "R"), mh("eyeBlink", 1.0, "R"), R.lids("R", 0.45, 2.2),
      extra=True),
    T("ジト目", "jito_kana", "EYE", ak("eyeBlink", 0.45), mh("eyeBlink", 0.45), R.lids("LR", 0.55, 0.0), extra=True),
    T("ｷﾘｯ", "kiri_hw", "EYE", mix(ak("eyeSquint", 0.5), ak("eyeBlink", 0.1)),        # half-width: what motions key
      mix(mh("eyeSquintInner", 0.5), mh("eyeBlink", 0.1)), R.lid_roll("LR", 10.0) + R.lids("LR", 0.12, 0.2), extra=True),
    T("下眼上", "lower_lid_up", "EYE", mix(ak("eyeSquint", 0.8), ak("cheekSquint", 0.3)),
      mix(mh("eyeLowerLidUp", 0.8), mh("eyeCheekRaise", 0.3)), R.lids("LR", 0.0, 1.0), extra=True),
    T("怒り目", "angry_eyes", "EYE", mix(ak("eyeSquint", 0.6), ak("eyeBlink", 0.2)),
      mix(mh("eyeSquintInner", 0.6), mh("eyeBlink", 0.2)), R.lid_roll("LR", 12.0) + R.lids("LR", 0.25, 0.3), extra=True),
    T("悲しむ", "sad_eyes", "EYE", mix(ak("eyeBlink", 0.3), {"browInnerUp": 0.5}),
      mix(mh("eyeBlink", 0.3), mh("browRaiseIn", 0.5)), R.lid_roll("LR", -10.0) + R.lids("LR", 0.2, 0.2), extra=True),
    T("なごみ左", "calm_L", "EYE", ak("eyeBlink", 0.5, "L"), mh("eyeBlink", 0.5, "L"), R.lids("L", 0.5, 0.5), extra=True),
    T("なごみ右", "calm_R", "EYE", ak("eyeBlink", 0.5, "R"), mh("eyeBlink", 0.5, "R"), R.lids("R", 0.5, 0.5), extra=True),
    T("あ２", "a2", "MOUTH", jaw_ak(1.0, **ak("mouthLowerDown", 0.4), **ak("mouthUpperUp", 0.2)),
      mix({"jawOpen": 1.0}, mh("mouthLowerLipDepress", 0.4), mh("mouthUpperLipRaise", 0.2)), R.jaw(24.0), extra=True),
    T("ん", "n", "MOUTH", mix(ak("mouthPress", 0.5), {"mouthRollLower": 0.3, "mouthShrugLower": 0.2}),
      mix(mh("mouthPressU", 0.5), mh("mouthPressD", 0.5), mh("mouthLowerLipRollIn", 0.3)),
      R.corners(out=-0.03) + R.lip("lower_lip_C", up=0.03) + R.lip("upper_lip_C", up=-0.02), extra=True),
    T("ワ", "wa", "MOUTH", jaw_ak(0.45, **ak("mouthSmile", 0.7), **ak("mouthLowerDown", 0.3)),
      mix({"jawOpen": 0.45}, mh("mouthCornerPull", 0.7), mh("mouthLowerLipDepress", 0.3)),
      R.jaw(14.0) + R.corners(out=0.08, up=0.06), extra=True),
    T("口横狭め", "mouth_narrow", "MOUTH", {"mouthPucker": 0.5}, quad("mouthLipsPurse", 0.5), R.corners(out=-0.10),
      extra=True),
]
for _name, _kw in BROW_ONE_SIDED.items():
    _base = next(t for t in MMD_TARGETS if t["name"] == _name)
    for _side, _jp in (("L", "左"), ("R", "右")):
        _ark = {k: v for k, v in (_base["arkit"] or {}).items() if k.endswith({"L": "Left", "R": "Right"}[_side])}
        _mhu = {k: v for k, v in (_base["metahuman"] or {}).items() if k.endswith(_side)}
        MMD_TARGETS.append(T(_name + _jp, "%s_%s" % (_base["name_e"], _side), "EYEBROW", _ark or None, _mhu or None,
                             R.brows(sides=_side, **_kw), extra=True))


# ------------------------------------------------------------------------------------------------
# ARKit 52.  MetaHuman: Epic's Live Link Face mapping (ARKit Left/Right = MetaHuman L/R = the
# character's own side).  mouthClose is the lips closing OVER an open jaw: measured against jawOpen.
# Role recipes are for bone-only faces (eyelids / brows / jaw / lips / tongue bones); a face without
# cheek or nose bones gets no cheekPuff / noseSneer.
# ------------------------------------------------------------------------------------------------
def _arkit_metahuman():
    out = {}
    for side, s in (("Left", "L"), ("Right", "R")):
        inward, outward = ("Right", "Left") if s == "L" else ("Left", "Right")
        out["eyeBlink" + side] = {"eyeBlink" + s: 1.0}
        out["eyeLookDown" + side] = {"eyeLookDown" + s: 1.0}
        out["eyeLookIn" + side] = {"eyeLook" + inward + s: 1.0}
        out["eyeLookOut" + side] = {"eyeLook" + outward + s: 1.0}
        out["eyeLookUp" + side] = {"eyeLookUp" + s: 1.0}
        out["eyeSquint" + side] = {"eyeSquintInner" + s: 1.0}
        out["eyeWide" + side] = {"eyeWiden" + s: 1.0}
        out["mouthSmile" + side] = {"mouthCornerPull" + s: 1.0}
        out["mouthFrown" + side] = {"mouthCornerDepress" + s: 1.0}
        out["mouthDimple" + side] = {"mouthDimple" + s: 1.0}
        out["mouthStretch" + side] = {"mouthStretch" + s: 1.0}
        out["mouthPress" + side] = {"mouthPressU" + s: 1.0, "mouthPressD" + s: 1.0}
        out["mouthLowerDown" + side] = {"mouthLowerLipDepress" + s: 1.0}
        out["mouthUpperUp" + side] = {"mouthUpperLipRaise" + s: 1.0}
        out["browDown" + side] = {"browDown" + s: 1.0, "browLateral" + s: 1.0}
        out["browOuterUp" + side] = {"browRaiseOuter" + s: 1.0}
        out["cheekSquint" + side] = {"eyeCheekRaise" + s: 1.0}
        out["noseSneer" + side] = {"noseWrinkle" + s: 1.0}
    out.update({
        "jawForward": {"jawFwd": 1.0}, "jawLeft": {"jawLeft": 1.0}, "jawRight": {"jawRight": 1.0},
        "jawOpen": {"jawOpen": 1.0},
        "mouthClose": mix({"jawOpen": 1.0}, quad("mouthLipsTogether", 1.0)),
        "mouthFunnel": quad("mouthFunnel", 1.0), "mouthPucker": quad("mouthLipsPurse", 1.0),
        "mouthLeft": {"mouthLeft": 1.0}, "mouthRight": {"mouthRight": 1.0},
        "mouthRollLower": mh("mouthLowerLipRollIn", 1.0), "mouthRollUpper": mh("mouthUpperLipRollIn", 1.0),
        "mouthShrugLower": {"jawChinRaiseDL": 1.0, "jawChinRaiseDR": 1.0},
        "mouthShrugUpper": {"jawChinRaiseUL": 1.0, "jawChinRaiseUR": 1.0},
        "browInnerUp": mh("browRaiseIn", 1.0), "cheekPuff": mh("mouthCheekBlow", 1.0), "tongueOut": {"tongueOut": 1.0},
    })
    return out


def _arkit_roles():
    out = {}
    for side, s in (("Left", "L"), ("Right", "R")):
        out["eyeBlink" + side] = R.lids(s, 1.0, 1.0)
        out["eyeLookDown" + side] = R.look(s, down=20.0) + R.lids(s, 0.25, 0.0)
        out["eyeLookUp" + side] = R.look(s, down=-18.0) + R.lids(s, -0.15, 0.0)
        out["eyeLookIn" + side] = R.look(s, out=-25.0)
        out["eyeLookOut" + side] = R.look(s, out=25.0)
        out["eyeSquint" + side] = R.lids(s, 0.1, 1.5)
        out["eyeWide" + side] = R.lids(s, -0.25, -0.4)
        out["cheekSquint" + side] = R.lids(s, 0.0, 0.6)
        out["mouthSmile" + side] = R.corners(out=0.03, up=0.07, sides=s)
        out["mouthFrown" + side] = R.corners(out=-0.04, up=-0.07, sides=s)
        out["mouthDimple" + side] = R.corners(out=0.04, fwd=-0.02, sides=s)
        out["mouthStretch" + side] = R.corners(out=0.12, up=-0.02, sides=s)
        out["mouthPress" + side] = (R.lip("upper_lip_" + s, up=-0.01) + R.lip("lower_lip_" + s, up=0.01)
                                    + R.corners(out=0.01, sides=s))
        out["mouthLowerDown" + side] = R.lip("lower_lip_" + s, up=-0.05) + R.lip("lower_lip_C", up=-0.02)
        out["mouthUpperUp" + side] = R.lip("upper_lip_" + s, up=0.05) + R.lip("upper_lip_C", up=0.02)
        out["browDown" + side] = R.brows(inner=(-0.03, 0.0, -0.07), centre=(0.0, 0.0, -0.04), outer=(0.0, 0.0, -0.02),
                                         root=(0.0, 0.0, -0.02), tilt=8.0, sides=s)
        out["browOuterUp" + side] = R.brows(outer=(0.0, 0.0, 0.08), centre=(0.0, 0.0, 0.03), tilt=8.0, sides=s)
    mouth = ("corner_L", "corner_R") + R.LIPS
    out.update({
        "jawForward": [("chin", "move", (0.0, 0.06, 0.0))],
        "jawLeft": [("chin", "yaw", 6.0)], "jawRight": [("chin", "yaw", -6.0)],
        "jawOpen": R.jaw(26.0),
        "mouthFunnel": (R.corners(out=-0.08, fwd=0.04) + R.lip("upper_lip_C", fwd=0.04) + R.lip("lower_lip_C", fwd=0.04)
                        + R.jaw(4.0)),
        "mouthPucker": R.corners(out=-0.12, fwd=0.04) + R.lip("upper_lip_C", fwd=0.03) + R.lip("lower_lip_C", fwd=0.03),
        "mouthLeft": R.shift_x(0.06, mouth), "mouthRight": R.shift_x(-0.06, mouth),
        "mouthRollLower": (R.lip("lower_lip_C", fwd=-0.03, up=0.02) + R.lip("lower_lip_L", fwd=-0.02, up=0.01)
                           + R.lip("lower_lip_R", fwd=-0.02, up=0.01)),
        "mouthRollUpper": (R.lip("upper_lip_C", fwd=-0.03, up=-0.02) + R.lip("upper_lip_L", fwd=-0.02, up=-0.01)
                           + R.lip("upper_lip_R", fwd=-0.02, up=-0.01)),
        "mouthShrugLower": (R.lip("lower_lip_C", fwd=0.02, up=0.03) + R.lip("lower_lip_L", up=0.02)
                            + R.lip("lower_lip_R", up=0.02)),
        "mouthShrugUpper": (R.lip("upper_lip_C", fwd=0.01, up=0.02) + R.lip("upper_lip_L", up=0.015)
                            + R.lip("upper_lip_R", up=0.015)),
        "browInnerUp": R.brows(inner=(0.0, 0.0, 0.09), centre=(0.0, 0.0, 0.03), root=(0.0, 0.0, 0.01), tilt=-8.0),
        "tongueOut": R.tongue(fwd=0.45, down=0.08, pitch=6.0, curl=6.0),
    })
    return out


def _arkit_targets():
    metahuman, role = _arkit_metahuman(), _arkit_roles()
    out = []
    for name in ARKIT_52:
        t = T(name, name, arkit_category(name), {name: 1.0}, metahuman.get(name), role.get(name))
        if name == "mouthClose":        # lips closing over an open jaw: the shape is (jaw + lips) - jaw
            t["metahuman_against"] = {"jawOpen": 1.0}
        out.append(t)
    return out


ARKIT_TARGETS = _arkit_targets()
BUILTIN = {MMD: MMD_TARGETS, ARKIT: ARKIT_TARGETS}


def targets(set_key, categories=None, extras=True, custom=None):
    """The targets of a set, filtered by PMX panel and 'extra'; a custom recipe file (same dict
    layout, see save_json) overrides built-in targets of the same name and adds new ones."""
    items = [dict(t) for t in BUILTIN[set_key]]
    if custom:
        by_name = {t["name"]: i for i, t in enumerate(items)}
        for t in custom:
            t = dict(T(t["name"], t.get("name_e", t["name"]), t.get("category", "OTHER")), **t)
            if t["name"] in by_name:
                merged = dict(items[by_name[t["name"]]])
                merged.update({k: v for k, v in t.items() if v is not None})
                items[by_name[t["name"]]] = merged
            else:
                items.append(t)
    return [t for t in items if (categories is None or t["category"] in categories) and (extras or not t.get("extra"))]


def _jsonable(t):
    out = dict(t)
    if out.get("roles"):
        out["roles"] = [[role, kind, list(amount) if isinstance(amount, tuple) else amount]
                        for role, kind, amount in out["roles"]]
    return out


def save_json(set_key, path):
    """Write a set as an editable recipe file (the same layout load_json reads)."""
    with open(path, "w", encoding="utf-8") as fh:
        json.dump([_jsonable(t) for t in BUILTIN[set_key]], fh, ensure_ascii=False, indent=1)


def load_json(path):
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    for t in data:
        if t.get("roles"):
            t["roles"] = [(role, kind, tuple(amount) if isinstance(amount, list) else amount)
                          for role, kind, amount in t["roles"]]
    return data
