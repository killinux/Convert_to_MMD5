# -*- coding: utf-8 -*-
"""Bone-only faces with no expression data: find the face bones by ROLE, then describe every
expression as a few calibrated bone actions.  Ported from mmd_face_morphs (faces.py, expressions.py,
build.offsets), where it was tuned on Rise of Eros heads (32 skinned face bones, no shape keys).

A role is what a bone does (upper_lid_L, chin, corner_R, tongue_0 ...), found by the spellings seen
so far with the ``Bip001 `` / ``Bip000 `` prefix stripped and case ignored.  ``AC `` bones are 3ds Max
helper duplicates: skipped, except a helper parented OUTSIDE its control's subtree (``AC jaw`` under
the neck), which becomes a *follower* that gets the control's rotation about the control's pivot.

An action is ``(role, kind, amount)``:

``pitch`` deg   rotate about the lateral axis; positive = the front of the bone goes DOWN
``roll``  deg   rotate about the front axis; positive = the OUTER end goes UP (mirrored per side)
``yaw``   deg   rotate about the vertical axis; positive = the front swings OUTWARD
                (a central bone: toward the character's left, +X)
``move``  (out, fwd, up)  translation in eye spacings; ``out`` away from the midline (mirrored per
                side, a central bone: +X), ``fwd`` toward the face

The signs are measured, not guessed: a probe on the bone is turned 1 degree and must move the
wanted way, so one recipe works for both sides and for bones with any rest orientation.  All
distances are fractions of the eye spacing, so the same numbers fit any head size.
"""
import math
import re

from mathutils import Matrix, Quaternion, Vector

PREFIX = re.compile(r"^(?:Bip\d+|AC)\s+", re.I)
HELPER = re.compile(r"^(?:AC\s+|_dummy_|_shadow_)", re.I)     # Max helpers, mmd_tools proxies

# role -> spellings, in preference order (prefix stripped, lower case)
ROLE_NAMES = {
    "eye_L": ("左目", "目.l", "eyeball_l", "eye_l", "eyeball.l", "eye.l", "l_eye"),
    "eye_R": ("右目", "目.r", "eyeball_r", "eye_r", "eyeball.r", "eye.r", "r_eye"),
    "upper_lid_L": ("eyelid_ul", "eyelid_lt", "eyelid_up_l", "eyelid_l_up", "uppereyelid_l"),
    "upper_lid_R": ("eyelid_ur", "eyelid_rt", "eyelid_up_r", "eyelid_r_up", "uppereyelid_r"),
    "lower_lid_L": ("eyelid_bl", "eyelid_lb", "eyelid_dn_l", "eyelid_l_dn", "lowereyelid_l"),
    "lower_lid_R": ("eyelid_br", "eyelid_rb", "eyelid_dn_r", "eyelid_r_dn", "lowereyelid_r"),
    "brow_L": ("eyebrow_l", "brow_l"),
    "brow_R": ("eyebrow_r", "brow_r"),
    "chin": ("chin", "jaw"),
    "corner_L": ("lip_l", "lips_l", "mouth_l"),
    "corner_R": ("lip_r", "lips_r", "mouth_r"),
    "upper_lip_C": ("lip_uc", "lips_tc", "lips_up", "lip_u", "lips_uc"),
    "upper_lip_L": ("lip_ucl", "lips_tl", "lips_up_l"),
    "upper_lip_R": ("lip_ucr", "lips_tr", "lips_up_r"),
    "lower_lip_C": ("lip_bc", "lips_bc", "lips_dn", "lip_b", "lip_dc", "lips_dc"),
    "lower_lip_L": ("lip_bcl", "lips_bl", "lips_dn_l"),
    "lower_lip_R": ("lip_bcr", "lips_br", "lips_dn_r"),
    "tongue_0": ("tongue", "tongue01", "tongue_01", "tongue1"),
    "teeth_up": ("teeth_up", "teeth_u", "teeth_t", "teeth_upper", "upperteeth"),
    "teeth_dw": ("teeth_dw", "teeth_b", "teeth_d", "teeth_down", "teeth_lower", "lowerteeth"),
}
STYLE_OF = {"eyelid_ul": "lowercase (eyelid_UL / lip_UC / chin)",
            "eyelid_lt": "capitalised (Eyelid_LT / Lips_TC / Chin)",
            "eyelid_up_l": "b01 (eyelid_UP_L / Lips_UP / Jaw)"}
PAIRED = ("eye", "upper_lid", "lower_lid", "brow", "corner")


def strip(name):
    return PREFIX.sub("", name).lower()


class Face:
    """The roles found on one armature plus the frame the recipes need."""

    def __init__(self, arm):
        self.arm = arm
        self.bones = {}          # role -> bone name
        self.followers = {}      # role -> helper bones that must move with it
        self.unit = 0.06         # eye spacing in armature units
        self.calibrated = False  # True when ``unit`` was measured on a real pair
        self.front = -1.0        # sign of the armature Y axis that points forward
        self.centre_x = 0.0
        self.head = None
        self.style = "unknown"

    def bone(self, role):
        return self.bones.get(role)

    def side_of(self, bone_name):
        """+1 for the character's left (+X in mmd_tools models), -1 right."""
        x = self.arm.data.bones[bone_name].head_local.x - self.centre_x
        return -1.0 if x < -1e-6 else 1.0

    def missing(self):
        return [role for role in ROLE_NAMES if role not in self.bones]

    def describe(self):
        return ("style %s, unit %.4f%s, front %+d, %d roles | missing: %s" % (
            self.style, self.unit, "" if self.calibrated else " (GUESSED, no eye/lid/brow pair)",
            int(self.front), len(self.bones), " ".join(self.missing()) or "-"))


def _index(arm):
    index = {}
    for bone in arm.data.bones:
        if not HELPER.match(bone.name):
            index.setdefault(strip(bone.name), bone.name)
    return index


def _mmd_named(arm, japanese):
    for pose_bone in arm.pose.bones:
        if HELPER.match(pose_bone.name):
            continue
        mmd = getattr(pose_bone, "mmd_bone", None)
        if mmd is not None and mmd.name_j == japanese:
            return pose_bone.name
    return None


def _brow_segments(face, side):
    root = face.bone("brow_%s" % side)
    if not root:
        return
    bone = face.arm.data.bones[root]
    segments = [c for c in bone.children
                if not HELPER.match(c.name) and strip(c.name).startswith(("eyebrow", "brow"))]
    if not segments:
        face.bones["brow_%s_tilt" % side] = root          # a single brow bone: tilted instead
        return
    segments.sort(key=lambda c: abs(c.head_local.x - face.centre_x))
    if len(segments) == 1:
        names = {"centre": segments[0].name}
    elif len(segments) == 2:
        names = {"inner": segments[0].name, "outer": segments[1].name}
    else:
        names = {"inner": segments[0].name, "centre": segments[len(segments) // 2].name,
                 "outer": segments[-1].name}
    for key, name in names.items():
        face.bones["brow_%s_%s" % (side, key)] = name


def _tongue_chain(face):
    root = face.bone("tongue_0")
    if not root:
        return
    bone = face.arm.data.bones[root]
    chain = [bone]
    while True:
        children = [c for c in bone.children if not HELPER.match(c.name)]
        if not children:
            break
        bone = max(children, key=lambda c: len(c.children_recursive))     # the longest branch
        chain.append(bone)
    for i, link in enumerate(chain[1:], 1):
        face.bones["tongue_%d" % i] = link.name


def _followers(face):
    by_spelling = {}
    for role, spellings in ROLE_NAMES.items():
        for spelling in spellings:
            by_spelling.setdefault(spelling, role)
    for bone in face.arm.data.bones:
        if not bone.name.lower().startswith("ac "):
            continue
        role = by_spelling.get(strip(bone.name))
        control = face.bone(role) if role else None
        if control is None or control == bone.name:
            continue
        if face.arm.data.bones[control] in bone.parent_recursive:
            continue
        face.followers.setdefault(role, []).append(bone.name)


def resolve(arm):
    """A Face for the armature (roles may be missing)."""
    face = Face(arm)
    index = _index(arm)
    for role, spellings in ROLE_NAMES.items():
        for spelling in spellings:
            if spelling in index:
                face.bones[role] = index[spelling]
                if role == "upper_lid_L":
                    face.style = STYLE_OF.get(spelling, spelling)
                break
    for role, japanese in (("eye_L", "左目"), ("eye_R", "右目")):
        name = _mmd_named(arm, japanese)
        if name:
            face.bones[role] = name
    head = _mmd_named(arm, "頭") or index.get("head") or index.get("頭")
    face.head = head
    bones = arm.data.bones
    xs = []
    for stem in PAIRED:
        left, right = face.bone(stem + "_L"), face.bone(stem + "_R")
        if left and right:
            xs.append((bones[left].head_local.x + bones[right].head_local.x) * 0.5)
    face.centre_x = sum(xs) / len(xs) if xs else (bones[head].head_local.x if head else 0.0)
    for stem in ("eye", "upper_lid", "brow"):
        left, right = face.bone(stem + "_L"), face.bone(stem + "_R")
        if left and right:
            spacing = (bones[left].head_local - bones[right].head_local).length
            if spacing > 1e-6:
                face.unit = spacing
                face.calibrated = True
                break
    else:
        if head:
            face.unit = max(bones[head].length * 0.5, 1e-3)
    if head:
        origin = bones[head].head_local
        ys = [bones[name].head_local.y - origin.y for role, name in face.bones.items()
              if role.startswith(("upper_lid", "lower_lid", "eye", "chin", "corner"))]
        if ys and abs(sum(ys)) > 1e-6:
            face.front = -1.0 if sum(ys) < 0 else 1.0
    for side in "LR":
        _brow_segments(face, side)
    _tongue_chain(face)
    _followers(face)
    return face


# -- actions (the vocabulary recipes.py writes role recipes in) ------------------------------------
BLINK_DEG = 33.0      # upper lid, closed (pivot at the eyeball centre)
LOWER_SHARE = 0.45    # the lower lid's share of a blink (it rises); keep upper + 0.45 * lower <= 1
JAW_DEG = 18.0        # あ


def lids(sides, upper, lower):
    """upper / lower as fractions of a blink (lower 1.0 = the lower lid's blink share)."""
    acts = []
    for side in sides:
        if upper:
            acts.append(("upper_lid_%s" % side, "pitch", upper * BLINK_DEG))
        if lower:
            acts.append(("lower_lid_%s" % side, "pitch", -lower * BLINK_DEG * LOWER_SHARE))
    return acts


def lid_roll(sides, degrees):
    return [("upper_lid_%s" % side, "roll", degrees) for side in sides]


def brows(inner=None, centre=None, outer=None, root=None, tilt=0.0, sides="LR"):
    """Segment moves (three brow bones per side); ``tilt`` only applies to single-bone brows."""
    acts = []
    for side in sides:
        for segment, amount in (("inner", inner), ("centre", centre), ("outer", outer)):
            if amount:
                acts.append(("brow_%s_%s" % (side, segment), "move", amount))
        if root:
            acts.append(("brow_%s" % side, "move", root))
        if tilt:
            acts.append(("brow_%s_tilt" % side, "roll", tilt))
    return acts


def jaw(degrees):
    return [("chin", "pitch", degrees)]


def corners(out=0.0, up=0.0, fwd=0.0, sides="LR"):
    return [("corner_%s" % side, "move", (out, fwd, up)) for side in sides]


def lip(role, out=0.0, fwd=0.0, up=0.0):
    return [(role, "move", (out, fwd, up))]


def tongue(fwd=0.0, down=0.0, pitch=0.0, yaw=0.0, curl=0.0):
    acts = []
    if fwd or down:
        acts.append(("tongue_0", "move", (0.0, fwd, -down)))
    if pitch:
        acts.append(("tongue_0", "pitch", pitch))
    if yaw:
        acts.append(("tongue_0", "yaw", yaw))
    if curl:
        acts.append(("tongue_1", "pitch", curl))
        acts.append(("tongue_2", "pitch", curl * 0.7))
    return acts


def teeth(which, back, up):
    return [("teeth_%s" % which, "move", (0.0, -back, up))]


def look(sides, down=0.0, out=0.0):
    """Eyeball turn in degrees (down > 0 looks down, out > 0 looks away from the nose)."""
    acts = []
    for side in sides:
        if down:
            acts.append(("eye_%s" % side, "pitch", down))
        if out:
            acts.append(("eye_%s" % side, "yaw", out))
    return acts


def shift_x(amount, roles):
    """Move central / paired bones toward the character's left (+X) by ``amount`` eye spacings:
    ``move`` mirrors ``out`` per side, so right-side roles get the opposite sign."""
    return [(role, "move", (-amount if role.endswith("_R") else amount, 0.0, 0.0)) for role in roles]


LIPS = ("upper_lip_C", "upper_lip_L", "upper_lip_R", "lower_lip_C", "lower_lip_L", "lower_lip_R")
SMILE = lids("LR", 0.35, 2.2)            # lower lid up, upper down a little: ^ ^
# the tongue tip sits 4-8 mm behind the lip surface at rest: it has to be carried forward bodily
PERO = jaw(16.0) + tongue(fwd=0.45, down=0.08, pitch=6.0, curl=6.0)
OMEGA = (corners(out=-0.08, up=0.06) + lip("upper_lip_C", fwd=0.03, up=-0.03)
         + lip("lower_lip_C", fwd=0.03, up=0.02) + lip("upper_lip_L", up=0.02) + lip("upper_lip_R", up=0.02))


# -- actions -> per-bone pose -----------------------------------------------------------------------
AXES = {"pitch": Vector((1.0, 0.0, 0.0)), "roll": Vector((0.0, 1.0, 0.0)), "yaw": Vector((0.0, 0.0, 1.0))}


def _rest(arm, bone_name):
    return arm.data.bones[bone_name].matrix_local.to_3x3()


def _local_matrix(arm, bone_name, rotation):
    rest = _rest(arm, bone_name)
    return (rest.inverted() @ rotation @ rest).to_quaternion()


def _local_translation(arm, bone_name, vector):
    return _rest(arm, bone_name).inverted() @ vector


def rotation_sign(axis, probe, want):
    """+1 if a small positive rotation about ``axis`` moves ``probe`` along ``want``, else -1."""
    moved = Matrix.Rotation(math.radians(1.0), 3, axis) @ probe
    return 1.0 if (moved - probe).dot(want) >= 0.0 else -1.0


def offsets(face, actions, strength=1.0, missing=None):
    """{bone: (location, rotation quaternion)} in pose space.  Rotations and translations add up per
    ROLE in armature space first (in recipe order), then go to each bone's pose space.  A role's
    followers turn about the ROLE's pivot: ``(R - I)(h_follower - h_role)`` becomes a translation."""
    arm = face.arm
    fwd = Vector((0.0, face.front, 0.0))
    per_role = {}
    for role, kind, amount in actions:
        bone_name = face.bone(role)
        if not bone_name:
            if missing is not None:
                missing.add(role)
            continue
        side = face.side_of(bone_name)
        entry = per_role.setdefault(role, [Matrix.Identity(3), Vector((0.0, 0.0, 0.0))])
        if kind == "move":
            out, forward, up = amount
            entry[1] += Vector((out * side, forward * face.front, up)) * face.unit * strength
            continue
        axis = AXES[kind]
        if kind == "pitch":
            probe, want = fwd, Vector((0.0, 0.0, -1.0))
        elif kind == "roll":
            probe, want = Vector((side, 0.0, 0.0)), Vector((0.0, 0.0, 1.0))
        else:
            probe, want = fwd, Vector((side, 0.0, 0.0))
        degrees = rotation_sign(axis, probe, want) * amount * strength
        if abs(degrees) > 1e-9:
            entry[0] = Matrix.Rotation(math.radians(degrees), 3, axis) @ entry[0]
    out = {}
    identity = Matrix.Identity(3)
    for role, (rotation, translation) in per_role.items():
        bone_name = face.bone(role)
        out[bone_name] = (_local_translation(arm, bone_name, translation), _local_matrix(arm, bone_name, rotation))
        pivot = arm.data.bones[bone_name].head_local
        for follower in face.followers.get(role, ()):
            shift = (rotation - identity) @ (arm.data.bones[follower].head_local - pivot)
            out[follower] = (_local_translation(arm, follower, translation + shift),
                             _local_matrix(arm, follower, rotation))
    return out


def unit_quaternion():
    return Quaternion((1.0, 0.0, 0.0, 0.0))
