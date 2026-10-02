# -*- coding: utf-8 -*-
"""手调 (hand-tuned expressions): load an expression onto the armature, fix it by hand in Pose Mode,
keep it; generating then uses the kept pose instead of the source's own for that name.

The kept poses are pose-library entries named like the target (sources.POSE_PROP on the armature, the
same store the 姿势库 source reads), so they survive saving the .blend and work for every pose source
(MetaHuman face bones, DNA, FF7, bone faces).  ManualOverride wraps a source for the engine; the rest
is armature plumbing: putting a pose on the bones, reading it back, and mirroring it left <-> right.
"""
import re

from mathutils import Matrix, Vector

from . import names
from .sources import POSE, Source, _tiny, captured, combine, entry

MANUAL = "__manual__"


class ManualOverride(Source):
    """A source whose targets with a kept pose of the same name use that pose."""

    def __init__(self, base, poses):
        super().__init__(base.arm)
        self.base, self.poses = base, poses
        self.key, self.form, self.vocab = base.key, base.form, base.vocab

    def recipe(self, target):
        if target["name"] in self.poses:
            return (MANUAL, target["name"])
        return self.base.recipe(target)

    def why_not(self, target):
        return self.base.why_not(target)

    def against(self, target):
        # kept too for a hand-tuned pose: ARKit mouthClose stays measured against the source's jawOpen
        return self.base.against(target)

    def pose(self, components, strength=1.0):
        if isinstance(components, tuple) and components and components[0] == MANUAL:
            return combine(self.poses, {components[1]: strength})
        return self.base.pose(components, strength)

    def face_bones(self):
        return sorted(set(self.base.face_bones()) | {b for p in self.poses.values() for b in p})

    def info(self):
        out = dict(self.base.info())
        out["manual"] = sorted(self.poses)
        return out


def wrap(source, arm, use_manual=True):
    """The source with the armature's kept poses on top (pose sources only)."""
    if not use_manual or source.form != POSE or source.key == "POSES":
        return source
    poses = captured(arm)
    return ManualOverride(source, poses) if poses else source


# --- target names: left <-> right ---------------------------------------------------------------------
MIRROR_NAMES = {"ウィンク": "ウィンク右", "ウィンク２": "ｳｨﾝｸ２右", "てへぺろ": "てへぺろ２"}
MIRROR_NAMES.update({v: k for k, v in list(MIRROR_NAMES.items())})
MIRROR_NAMES["ウィンク２右"] = "ウィンク２"


def mirror_name(name):
    """The other side's expression name (eyeBlinkLeft -> eyeBlinkRight, ウィンク -> ウィンク右,
    困る左 -> 困る右), or "" for a symmetric one."""
    if name in MIRROR_NAMES:
        return MIRROR_NAMES[name]
    for a, b in (("Left", "Right"), ("Right", "Left"), ("左", "右"), ("右", "左")):
        if name.endswith(a):
            return name[:-len(a)] + b
    return ""


# --- bones: left <-> right ----------------------------------------------------------------------------
_BONE_SIDES = ((r"^L_", "R_"), (r"^R_", "L_"), (r"^FACIAL_L_", "FACIAL_R_"), (r"^FACIAL_R_", "FACIAL_L_"),
               (r"\.L$", ".R"), (r"\.R$", ".L"), (r"_L$", "_R"), (r"_R$", "_L"), (r"_l$", "_r"), (r"_r$", "_l"),
               (r"^左", "右"), (r"^右", "左"), (r"Left", "Right"), (r"Right", "Left"))


def mirror_bone(bones, name):
    for pattern, repl in _BONE_SIDES:
        if re.search(pattern, name):
            other = re.sub(pattern, repl, name, count=1)
            if bones.get(other) is not None:
                return other
    return name


_EYE_PAIRS = (("L_Eye", "R_Eye"), ("FACIAL_L_Eye", "FACIAL_R_Eye"), ("左目", "右目"), ("目.L", "目.R"),
              ("eye.L", "eye.R"), ("Eye_L", "Eye_R"))


def midplane(arm):
    """(point, unit normal) of the face's mirror plane in armature space: through the middle of the
    eyes and across them, else the armature's X = 0 plane."""
    bones = arm.data.bones
    for left, right in _EYE_PAIRS:
        if bones.get(left) is not None and bones.get(right) is not None:
            a, b = bones[left].head_local, bones[right].head_local
            if (a - b).length > 1e-6:
                return (a + b) * 0.5, (a - b).normalized()
    return Vector((0.0, 0.0, 0.0)), Vector((1.0, 0.0, 0.0))


def _reflection(point, normal):
    n = normal
    m = Matrix.Identity(4)
    for i in range(3):
        for j in range(3):
            m[i][j] -= 2.0 * n[i] * n[j]
    shift = 2.0 * point.dot(n) * n
    m[0][3], m[1][3], m[2][3] = shift
    return m


def _armature_matrices(arm, pose):
    """Armature-space posed matrices of every bone for a {bone: basis matrix} pose (FK)."""
    out = {}
    for bone in sorted(arm.data.bones, key=lambda b: len(b.parent_recursive)):
        basis = pose.get(bone.name, Matrix.Identity(4))
        if bone.parent is None:
            out[bone.name] = bone.matrix_local @ basis
        else:
            local = bone.parent.matrix_local.inverted() @ bone.matrix_local
            out[bone.name] = out[bone.parent.name] @ local @ basis
    return out


def _bases(pose):
    return {b: Matrix.LocRotScale(v[0], v[1], v[2] if len(v) > 2 else None) for b, v in pose.items()}


def mirror_pose(arm, pose):
    """Mirror a pose-space pose across the face's mid plane: every bone's armature-space change S·D·S
    goes to its other-side bone (centre bones mirror onto themselves)."""
    bones = arm.data.bones
    s = _reflection(*midplane(arm))
    posed = _armature_matrices(arm, _bases(pose))
    target = {}
    for name in pose:
        delta = posed[name] @ bones[name].matrix_local.inverted()
        other = mirror_bone(bones, name)
        target[other] = (s @ delta @ s) @ bones[other].matrix_local
    out, done = {}, {}
    for bone in sorted(bones, key=lambda b: len(b.parent_recursive)):
        if bone.parent is None:
            parent = Matrix.Identity(4)
            local = bone.matrix_local
        else:
            parent = done[bone.parent.name]
            local = bone.parent.matrix_local.inverted() @ bone.matrix_local
        if bone.name in target:
            done[bone.name] = target[bone.name]
            loc, quat, scale = (local.inverted() @ parent.inverted() @ target[bone.name]).decompose()
            if not _tiny(loc, quat, scale):
                out[bone.name] = entry(loc, quat, scale)
        else:
            done[bone.name] = parent @ local
    return out


# --- the armature -------------------------------------------------------------------------------------
def apply_pose(arm, pose, bones):
    """Put a pose-space pose on the armature: these bones at rest first, then the pose."""
    pbs = arm.pose.bones
    for name in bones:
        pb = pbs.get(name)
        if pb is not None:
            pb.matrix_basis = Matrix.Identity(4)
    for name, value in pose.items():
        pb = pbs.get(name)
        if pb is not None:
            if pb.rotation_mode != "QUATERNION":
                pb.rotation_mode = "QUATERNION"
            pb.matrix_basis = Matrix.LocRotScale(value[0], value[1], value[2] if len(value) > 2 else None)


def read_pose(arm, bones):
    """The armature's current pose of these bones (bones at rest left out)."""
    out = {}
    for name in bones:
        pb = arm.pose.bones.get(name)
        if pb is None:
            continue
        loc, quat, scale = pb.matrix_basis.decompose()
        if not _tiny(loc, quat, scale):
            out[name] = entry(loc, quat, scale)
    return out


def target_names(set_key):
    from . import recipes
    return [t["name"] for t in recipes.BUILTIN[set_key]] if set_key in recipes.BUILTIN else list(names.ARKIT_52)
