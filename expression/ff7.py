# -*- coding: utf-8 -*-
"""FF7 Remake / Rebirth face rig: a face-data JSON -> bone poses, with MMD and ARKit recipes.

The face is ~103 bones directly under C_FaceBase_a (upper lids Ulid A-E, lower lids Dlid A-C, lashes,
folds, eye bags, brows, forehead, glabella, cheeks, zygoma, nose, laugh lines, lips in / out, corners,
chin, teeth, tongue root / tip, throat, gonion), no shape keys.  How the game moves it (Rebirth's
Anim Blueprint, see docs/expression_design.md):
  * Facial00/F_*: single-frame face poses (F_Idle01 = the neutral face) played in a FacialSlot and mixed
    by BlendSpaces - F_Emotion01 (a 2D emotion grid of ten poses), F_Brow, F_Eyelid_move01 (the lids
    follow the gaze: F_Eyelid_up01 at 13 degrees up, down01 at 20 down, left01 / right01 at 22);
  * HSFLipMap: the lip-sync visemes aa ee oo sh fv ln bmp as per-bone Maya channels, driven per voice
    line by HSFLipSyncDataPack keyframes.
A face-data JSON (ripper_tpose scripts/final: ff7_face_data.py for Remake, ff7rb_face_data.py for
Rebirth) keeps the poses as deltas in a face frame (origin C_FaceBase_a, X forward, Y left, Z up, cm)
and the lip map as channels.  FaceData puts them on a target armature, scaled by the eye spacing.

Recipe components (weights multiply; rotations are raised to the weight, translations scale):
  ("pose", F_*, regions, weight, side)   a game pose relative to F_Idle01, restricted to regions
  ("lip", viseme, regions, weight, side)  a lip-map viseme relative to its DefaultShape
  ("move", regions, (fwd, left, up), side) a translation in eye spacings (face frame)
  ("gaze", lid pose, side, factor)        the eye bone turned to the gaze angle that lid pose belongs to
side "L" / "R" keeps that side's bones and half of the centre (C_) bones.  FaceData, the regions and
the MMD recipes are ported from ripper_tpose's ff7_face_morphs; the ARKit recipes are new.
"""
import glob
import json
import math
import os
import re

import bpy
from mathutils import Euler, Matrix, Quaternion, Vector

FACE_ROOT = "C_FaceBase_a"
SKIP = re.compile(r"^C_FaceBase|^C_Ex_")
CENTRE = 0.5                   # a one-sided component moves the centre bones half way
_REGIONS = {
    "lid_u": r"^S_(Ulid|Ulash|Fold)_",
    "lid_d": r"^S_(Dlid|Eyebag)_",
    "eye": r"^S_Eye$",
    "brow": r"^(S_(Brow_[A-Z]|Forehead|Glabella)|C_(Forehead|Glabella))$",
    "cheek": r"^S_(Cheek_A|Zygoma)$",
    "cheek_low": r"^S_Cheek_[BC]$",
    "nose": r"^(S_Nose_[AB]|C_Nose_A)$",
    "laugh": r"^S_Laughline_[AB]$",
    "corner_u": r"^S_(Ucor|Ucorin)$",
    "corner_d": r"^S_(Dcor|Dcorin)$",
    "ulip": r"^(S_Ulip(in)?_[AB]|C_Ulip(in|out)?)$",
    "dlip": r"^(S_Dlip(in|out)?_[AB]|C_Dlip(in|out)?)$",
    "jaw": r"^(C_Chin|C_Dteeth|C_Throat_A|S_Gonion)$",
    "uteeth": r"^C_Uteeth$",
    "tongue": r"^C_Tong(root|tip)$",
}
REGIONS = {k: re.compile(v.replace("S_", "[LR]_")) for k, v in _REGIONS.items()}
GROUPS = {
    "lid": ("lid_u", "lid_d"),
    "corner": ("corner_u", "corner_d", "laugh"),
    "lips": ("ulip", "dlip", "corner_u", "corner_d"),
    "mouth": ("corner_u", "corner_d", "laugh", "ulip", "dlip", "jaw", "uteeth", "tongue", "cheek_low", "nose"),
}
DEFAULT_GAZE = {"F_Eyelid_up01": (0.0, 13.0), "F_Eyelid_down01": (0.0, -20.0),
                "F_Eyelid_left01": (22.0, 0.0), "F_Eyelid_right01": (-22.0, 0.0)}


def _expand(regions):
    out = []
    for r in regions:
        out += list(GROUPS.get(r, (r,)))
    return out


def side_weight(bone, side):
    if side is None:
        return 1.0
    if bone.startswith(side + "_"):
        return 1.0
    if bone.startswith(("L_", "R_")):
        return 0.0
    return CENTRE


def region_weight(bone, regions, side):
    if SKIP.search(bone):
        return 0.0
    for r in _expand(regions):
        pattern = REGIONS.get(r)
        if (pattern.search(bone) if pattern else re.search(r, bone)):
            return side_weight(bone, side)
    return 0.0


# --- the rig ------------------------------------------------------------------------------------------
def is_ff7_face(arm):
    bones = arm.data.bones
    return all(bones.get(n) is not None for n in (FACE_ROOT, "L_Eye", "R_Eye", "L_Ulid_A", "R_Ulid_A"))


def face_frame(arm):
    """Armature-space face frame (origin C_FaceBase_a, X forward, Y left, Z up) and the eye spacing."""
    bones = arm.data.bones
    left = bones["L_Eye"].head_local - bones["R_Eye"].head_local
    spacing = left.length
    left.normalize()
    up = Vector((0.0, 0.0, 1.0))
    up = (up - left * up.dot(left)).normalized()
    fwd = left.cross(up)
    frame = Matrix((fwd, left, up)).transposed().to_4x4()
    frame.translation = bones[FACE_ROOT].head_local
    return frame, spacing


# --- face data ----------------------------------------------------------------------------------------
def character_code(arm, meshes=()):
    for name in [arm.name, arm.data.name] + [m.name for m in meshes] + [bpy.path.basename(bpy.data.filepath)]:
        m = re.search(r"PC\d{4}", name or "", re.IGNORECASE)
        if m:
            return m.group(0).upper()
    return ""


def data_dirs():
    """FF7_FACE_DATA_DIR, then a _meta/face folder next to or above the .blend (the export layout
    <game>/<character>/blend/<model>/<model>.blend keeps the data in <game>/_meta/face)."""
    dirs = [d for d in os.environ.get("FF7_FACE_DATA_DIR", "").split(os.pathsep) if d]
    here = os.path.dirname(bpy.path.abspath(bpy.data.filepath)) if bpy.data.filepath else ""
    for _ in range(6):
        if not here:
            break
        cand = os.path.join(here, "_meta", "face")
        if os.path.isdir(cand):
            dirs.append(cand)
        parent = os.path.dirname(here)
        here = "" if parent == here else parent
    return dirs


def find_face_data(arm, meshes=(), explicit=""):
    """The face-data JSON for this character: the given file, else <code>*.json in data_dirs()."""
    if explicit:
        path = bpy.path.abspath(explicit)
        return path if os.path.isfile(path) else ""
    code = character_code(arm, meshes)
    found = []
    for folder in data_dirs():
        found += sorted(glob.glob(os.path.join(folder, "*.json")))
    for path in found:
        if code and os.path.basename(path).upper().startswith(code):
            return path
    return found[0] if len(found) == 1 else ""


_CACHE = {}


def load_face_data(path):
    key = (path, os.path.getmtime(path))
    if key not in _CACHE:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        if "poses" not in data or "lipmap" not in data:
            raise ValueError("不是 FF7 表情数据（ff7_face_data.py / ff7rb_face_data.py 生成的 JSON）：%s" % path)
        _CACHE.clear()
        _CACHE[key] = data
    return _CACHE[key]


def _mat(flat):
    return Matrix([flat[0:4], flat[4:8], flat[8:12], flat[12:16]])


def _join(head, move, rot):
    return Matrix.Translation(head + move) @ rot.to_matrix().to_4x4() @ Matrix.Translation(-head)


def _powered(rot, weight):
    axis, angle = rot.to_axis_angle()
    return Quaternion(axis, angle * weight)


class FaceData:
    """Poses and lip shapes of one character, re-expressed on a target armature."""

    MAYA_TO_FACE = Matrix(((0, 0, 1), (1, 0, 0), (0, 1, 0)))   # (X left, Y up, Z fwd) -> (fwd, left, up)

    def __init__(self, data, arm):
        self.data, self.arm = data, arm
        self.frame, self.spacing = face_frame(arm)
        self.rot = self.frame.to_3x3()
        if data.get("eye_spacing"):
            self.scale = self.spacing / data["eye_spacing"]
        else:                                   # lip map only: centimetres onto a cm or a metre rig
            zs = [b.head_local.z for b in arm.data.bones]
            self.scale = 1.0 if (max(zs) - min(zs)) > 20.0 else 0.01
        root = arm.data.bones[FACE_ROOT]
        self.bones = [b.name for b in root.children_recursive if not SKIP.search(b.name)]
        self.gaze_angles = dict(DEFAULT_GAZE)
        for name, value in (data.get("lid_gaze") or {}).items():
            self.gaze_angles[name] = tuple(value)

    def has(self, components):
        poses, shapes = self.data["poses"], self.data["lipmap"]["shapes"]
        for c in components:
            if c[0] == "pose" and c[1] not in poses:
                return False
            if c[0] == "lip" and c[1] not in shapes:
                return False
            if c[0] == "gaze" and c[1] not in poses:
                return False
        return True

    def missing(self, components):
        poses, shapes = self.data["poses"], self.data["lipmap"]["shapes"]
        return sorted({c[1] for c in components if (c[0] in ("pose", "gaze") and c[1] not in poses)
                       or (c[0] == "lip" and c[1] not in shapes)})

    def _to_arm(self, d_face):
        s = Matrix.Diagonal((self.scale,) * 3).to_4x4()
        return self.frame @ s @ d_face @ s.inverted() @ self.frame.inverted()

    def pose(self, name):
        """{bone: armature-space delta} of a game pose relative to F_Idle01."""
        poses = self.data["poses"]
        idle = poses.get("F_Idle01", {})
        bones = self.arm.data.bones
        out = {}
        for bone, flat in poses[name].items():
            if bones.get(bone) is None or SKIP.search(bone):
                continue
            d = self._to_arm(_mat(flat))
            if bone in idle:
                d = d @ self._to_arm(_mat(idle[bone])).inverted()
            out[bone] = d
        return out

    def lip(self, name):
        """{bone: armature-space delta} of a lip-map viseme relative to DefaultShape."""
        lipmap = self.data["lipmap"]
        default = lipmap["DefaultShape"]
        p = self.MAYA_TO_FACE
        bones = self.arm.data.bones
        out = {}
        for bone, ch in lipmap["shapes"][name].items():
            if bones.get(bone) is None or SKIP.search(bone):
                continue
            base = default.get(bone, {})
            t = Vector([ch.get("Translate" + k, base.get("Translate" + k, 0.0)) - base.get("Translate" + k, 0.0)
                        for k in "XYZ"])
            r = Euler([math.radians(ch.get("Rotate" + k, 0.0) - base.get("Rotate" + k, 0.0)) for k in "XYZ"],
                      "XYZ").to_matrix()
            head = bones[bone].head_local
            move = self.rot @ (p @ t) * self.scale
            rot = (self.rot @ p @ r @ p.transposed() @ self.rot.transposed()).to_quaternion()
            out[bone] = _join(head, move, rot)
        return out

    def vector(self, vec):
        """(fwd, left, up) in eye spacings -> armature space."""
        return self.rot @ Vector(vec) * self.spacing

    def gaze(self, lid_pose, side, factor=1.0):
        """The eye bone of `side` turned to the gaze of a lid pose: horizontal + = the character's
        left (yaw about the face's up axis), vertical + = up (pitch about the face's left axis)."""
        h, v = self.gaze_angles.get(lid_pose, (0.0, 0.0))
        eye = side + "_Eye"
        head = self.arm.data.bones[eye].head_local
        yaw = Matrix.Rotation(math.radians(h * factor), 4, self.rot @ Vector((0.0, 0.0, 1.0)))
        pitch = Matrix.Rotation(math.radians(-v * factor), 4, self.rot @ Vector((0.0, 1.0, 0.0)))
        return eye, Matrix.Translation(head) @ yaw @ pitch @ Matrix.Translation(-head)


def evaluate(face, components, strength=1.0):
    """Components -> {bone: armature-space delta}."""
    bones = face.arm.data.bones
    moves, rots = {}, {}

    def add(bone, d, w):
        head = bones[bone].head_local
        moves[bone] = moves.get(bone, Vector()) + ((d @ head) - head) * w
        rots[bone] = _powered(d.to_quaternion(), w) @ rots.get(bone, Quaternion())

    for comp in components:
        kind = comp[0]
        if kind in ("pose", "lip"):
            _k, source, regions, weight, side = comp
            deltas = face.pose(source) if kind == "pose" else face.lip(source)
            for bone, d in deltas.items():
                w = region_weight(bone, regions, side) * weight * strength
                if w:
                    add(bone, d, w)
        elif kind == "move":
            _k, regions, vec, side = comp
            offset = face.vector(vec)
            for bone in face.bones:
                w = region_weight(bone, regions, side) * strength
                if w:
                    moves[bone] = moves.get(bone, Vector()) + offset * w
                    rots.setdefault(bone, Quaternion())
        elif kind == "gaze":
            _k, lid_pose, side, factor = comp
            eye, d = face.gaze(lid_pose, side, factor)
            if bones.get(eye) is not None:
                add(eye, d, strength)
        else:
            raise ValueError("unknown FF7 recipe component %r" % (kind,))
    return {b: _join(bones[b].head_local, moves[b], rots[b]) for b in moves}


def to_basis(arm, deltas):
    """Armature-space deltas of C_FaceBase_a's children -> pose-space (location, quaternion)."""
    bones = arm.data.bones
    out = {}
    for name, d in deltas.items():
        rest = bones[name].matrix_local
        loc, quat, _scale = (rest.inverted() @ d @ rest).decompose()
        out[name] = (loc, quat)
    return out


# --- recipes ------------------------------------------------------------------------------------------
def _p(source, regions, weight=1.0, side=None):
    return ("pose", source, tuple(regions), weight, side)


def _v(viseme, regions, weight=1.0, side=None):
    return ("lip", viseme, tuple(regions), weight, side)


def _m(regions, fwd=0.0, left=0.0, up=0.0, side=None):
    return ("move", tuple(regions), (fwd, left, up), side)


# MMD (ported from ff7_face_morphs: the vowels are the game's lip-sync shapes)
MMD = {
    "まばたき": [_p("F_Eyelid_blink01", ["lid"])],
    "笑い": [_p("F_Eyelid_blink01", ["lid"]), _p("F_Glad01", ["cheek"])],
    "ウィンク": [_p("F_Eyelid_blink01", ["lid"], side="L"), _p("F_Glad01", ["cheek"], side="L")],
    "ウィンク右": [_p("F_Eyelid_blink01", ["lid"], side="R"), _p("F_Glad01", ["cheek"], side="R")],
    "ウィンク２": [_p("F_Eyelid_blink01", ["lid"], side="L")],
    "ｳｨﾝｸ２右": [_p("F_Eyelid_blink01", ["lid"], side="R")],
    "ウィンク２右": [_p("F_Eyelid_blink01", ["lid"], side="R")],
    "じと目": [_p("F_Eyelid_down01", ["lid"])],
    "ジト目": [_p("F_Eyelid_down01", ["lid"])],
    "びっくり": [_p("F_Surprise01", ["lid"])],
    "はぅ": [_p("F_Dmg02", ["lid", "cheek"])],          # F_Dmg01 squeezes one eye only
    "なごみ": [_p("F_Tired01", ["lid"])],
    "真面目": [_p("F_Serious01", ["brow"])],
    "困る": [_p("F_Sad01", ["brow"])],
    "にこり": [_p("F_Glad01", ["brow"])],
    "怒り": [_p("F_Angry01", ["brow"])],
    "上": [_p("F_Brow_up01", ["brow"])],
    "下": [_p("F_Brow_down01", ["brow"])],
    "あ": [_v("aa", ["mouth"])],
    "い": [_v("ee", ["mouth"])],
    "う": [_v("oo", ["mouth"])],
    "え": [_v("aa", ["mouth"], 0.5), _v("ee", ["mouth"], 0.6)],
    "お": [_v("oo", ["mouth"]), _v("aa", ["mouth"], 0.45)],
    "ん": [_v("bmp", ["mouth"])],
    "ワ": [_p("F_Glad01", ["mouth"]), _v("aa", ["mouth"], 0.4)],
    "にっこり": [_p("F_Smile01", ["mouth"])],
    "にやり": [_p("F_Sneer01", ["mouth"])],
    "∧": [_p("F_Sad01", ["mouth"])],
    "口角上げ": [_p("F_Smile01", ["mouth"], 0.6)],
    "口角下げ": [_p("F_Disgust01", ["mouth"])],
    "口横広げ": [_v("ee", ["mouth"], 0.6)],
}

# ARKit 52.  Game data where the game has the movement, procedural moves for the rest; every one of
# them is a draft to correct by hand (手调).  LOOK: the lid pose of a gaze toward the character's
# own left / right (F_Eyelid_left01 sits at +22 = left in the gaze BlendSpace, and its lids shift
# toward the character's left).  The game's poses are mild while a face tracker sends 1.0 for the
# extreme, so several weights are above 1 (checked by rendering every key on Rebirth Tifa).
LOOK = {"L": "F_Eyelid_left01", "R": "F_Eyelid_right01"}
BROW_UP = {"L": "F_Brow_up_left01", "R": "F_Brow_up_right01"}
JAW_OPEN = 1.4          # aa opens the jaw to about 70 % of a full jawOpen
JAW_PARTS = ["jaw", "tongue", "dlip", "corner_d", "cheek_low"]


def _arkit():
    out = {}
    for side, word, sign in (("L", "Left", 1.0), ("R", "Right", -1.0)):
        other = "R" if side == "L" else "L"
        out["eyeBlink" + word] = [_p("F_Eyelid_blink01", ["lid"], side=side)]
        out["eyeLookUp" + word] = [_p("F_Eyelid_up01", ["lid"], side=side), ("gaze", "F_Eyelid_up01", side, 1.0)]
        out["eyeLookDown" + word] = [_p("F_Eyelid_down01", ["lid"], side=side),
                                     ("gaze", "F_Eyelid_down01", side, 1.0)]
        out["eyeLookOut" + word] = [_p(LOOK[side], ["lid"], side=side), ("gaze", LOOK[side], side, 1.0)]
        out["eyeLookIn" + word] = [_p(LOOK[other], ["lid"], side=side), ("gaze", LOOK[other], side, 1.0)]
        out["eyeSquint" + word] = [_p("F_Glad01", ["lid_d"], 2.0, side), _p("F_Eyelid_blink01", ["lid_u"], 0.2, side)]
        out["eyeWide" + word] = [_p("F_Surprise01", ["lid"], 1.5, side)]
        out["cheekSquint" + word] = [_p("F_Glad01", ["cheek"], 1.5, side)]
        out["browDown" + word] = [_p("F_Brow_down01", ["brow"], 1.3, side)]
        out["browOuterUp" + word] = [_p(BROW_UP[side], ["brow"], side=side)]
        out["noseSneer" + word] = [_p("F_Disgust01", ["nose", "cheek"], 1.3, side)]
        out["mouthSmile" + word] = [_p("F_Smile01", ["lips", "laugh", "cheek_low"], 1.5, side)]
        # no game pose pulls the corners down (Disgust01 lifts them, Sad01 barely moves them): moved
        out["mouthFrown" + word] = [_m(["corner"], up=-0.06, side=side), _p("F_Sad01", ["dlip"], side=side)]
        out["mouthDimple" + word] = [_m(["corner"], fwd=-0.07, left=0.025 * sign, side=side)]
        # ee lifts the corners (a smile): stretch = corners out and a little down, lower lip after them
        out["mouthStretch" + word] = [_m(["corner"], left=0.08 * sign, up=-0.025, side=side),
                                      _v("ee", ["dlip"], 0.6, side)]
        out["mouthPress" + word] = [_v("bmp", ["lips"], 1.5, side)]
        out["mouthLowerDown" + word] = [_v("ee", ["dlip"], 1.2, side)]
        out["mouthUpperUp" + word] = [_p("F_Disgust01", ["ulip"], 1.3, side)]
    jaw_open = [_v("aa", JAW_PARTS, JAW_OPEN)]
    out.update({
        "browInnerUp": [_p("F_Sad01", ["brow"])],
        "jawOpen": jaw_open,
        "jawForward": [_m(["jaw", "dlip", "corner_d"], fwd=0.1)],
        "jawLeft": [_m(["jaw", "dlip", "corner_d", "tongue"], left=0.1)],
        "jawRight": [_m(["jaw", "dlip", "corner_d", "tongue"], left=-0.1)],
        # the lips closing over an open jaw: (jaw alone) measured against (jaw + following lips)
        "mouthClose": {"main": [_v("aa", ["jaw", "tongue"], JAW_OPEN)], "against": jaw_open},
        "mouthFunnel": [_v("oo", ["mouth"])],
        "mouthPucker": [_v("oo", ["lips", "laugh"]), _v("bmp", ["ulip", "dlip"], 0.3)],
        "mouthLeft": [_m(["lips", "laugh"], left=0.12)],
        "mouthRight": [_m(["lips", "laugh"], left=-0.12)],
        "mouthRollLower": [_v("fv", ["dlip"])],
        "mouthRollUpper": [_m(["ulip"], fwd=-0.04, up=-0.03)],
        "mouthShrugLower": [_m(["dlip"], fwd=0.01, up=0.035)],
        "mouthShrugUpper": [_m(["ulip"], up=0.035)],
        "cheekPuff": [_m(["cheek_low"], fwd=0.05, left=0.18, side="L"), _m(["cheek_low"], fwd=0.05, left=-0.18, side="R"),
                      _v("bmp", ["ulip", "dlip"], 0.4)],
        "tongueOut": [_m([r"^C_Tongtip$"], fwd=0.6, up=-0.1), _m([r"^C_Tongroot$"], fwd=0.3, up=-0.05)],
    })
    return out


ARKIT = _arkit()


def recipe(name):
    """The FF7 recipe of a target name (MMD or ARKit), or None.  A dict = {"main", "against"}."""
    return MMD.get(name) or ARKIT.get(name)
