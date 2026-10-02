# -*- coding: utf-8 -*-
"""Expression sources: where the face movement comes from.

A source is either a POSE source (it says how the face bones move; the result can become an MMD
bone morph as it is, or be baked through the skin into shape keys) or a SHAPE source (it already
has vertex offsets; the result can only become shape keys / vertex morphs).  Each one reads the
recipes of its own vocabulary (recipes.py):

  FaceBoneSource     MetaHuman FACIAL_* bones, no DNA     pose   convert/face.py MORPHS (MMD only)
  DnaSource          MetaHuman DNA + FACIAL_* bones       pose   'metahuman'
  ShapeKeySource     existing ARKit shape keys            shape  'arkit'
  PoseLibrarySource  actions / pose markers / captured    pose   'names' (a pose named like the target)
  RoleSource         bone-only face, calibrated actions   pose   'roles'

Poses are {bone: (location, rotation quaternion)} in pose (matrix_basis) space - exactly what an
mmd_tools bone morph stores and what MMD applies.  Ported from ripper_tpose's expression_kit; the
FaceBoneSource (and AUTO) are Convert_to_MMD5's own.
"""
import json
import os
import re

import bpy
import numpy as np
from mathutils import Matrix, Quaternion, Vector

from . import dna as dnalib
from . import ff7, names, roles

POSE, SHAPE = "pose", "shape"
KINDS = (("AUTO", "自动", "按模型自动选：给了 DNA 用 DNA → 有 ARKit 形态键用形态键 → FF7 脸骨 → MetaHuman 脸骨 → 骨骼脸"),
         ("FACEBONES", "脸骨(MetaHuman)", "MetaHuman 式脸骨，不需要 DNA：按骨头位置算出 59 个 MMD 表情（只做 MMD）"),
         ("FF7", "FF7 脸骨", "FF7 Remake / Rebirth 的脸骨（C_FaceBase_a）+ 表情数据 JSON：游戏的表情姿势和口型，"
                            "MMD 和 ARKit 都能做"),
         ("DNA", "MetaHuman DNA", "这张脸的 .dna 驱动 FACIAL_* 骨（游戏原始表情数据），MMD 和 ARKit 都能做"),
         ("SHAPES", "已有形态键", "用模型自带的 ARKit 形态键混合（星刃 mod、VRoid Perfect Sync、CC4 ……）"),
         ("POSES", "姿势库", "自己摆好记录的姿势，或带姿势标记的动作，名字用 MMD 名或 ARKit 名"),
         ("ROLES", "骨骼脸", "只有眼皮 / 眉 / 下巴 / 嘴唇 / 舌头骨骼的脸（Rise of Eros 式），按标定过的动作摆"))
KIND_LABELS = {k: label for k, label, _d in KINDS}
EPS_LOC = 1e-6
EPS_ROT = 1e-5
EPS_SCALE = 1e-5
ONE = Vector((1.0, 1.0, 1.0))


def _tiny(loc, quat, scale=None):
    return loc.length < EPS_LOC and abs(quat.angle) < EPS_ROT and (scale is None or (scale - ONE).length < EPS_SCALE)


def entry(loc, quat, scale=None):
    """A pose entry: (location, quaternion) or, when it scales, (location, quaternion, scale).
    PMX bone morphs cannot scale, so a scaled entry forces a vertex morph (engine.py)."""
    if scale is not None and (scale - ONE).length >= EPS_SCALE:
        return (loc, quat, scale)
    return (loc, quat)


def unpack(value):
    """(location, quaternion, scale or None) of a pose entry."""
    return (value[0], value[1], value[2] if len(value) > 2 else None)


class Source:
    key, form, vocab = "", POSE, ""

    def __init__(self, arm):
        self.arm = arm

    def recipe(self, target):
        return target.get(self.vocab) or None

    def why_not(self, target):
        return "没有 %s 配方" % self.vocab

    def against(self, target):
        return None

    def pose(self, components, strength=1.0):
        raise NotImplementedError

    def shapes(self, components, strength=1.0):
        raise NotImplementedError

    def face_bones(self):
        """Bones this source moves (the meshes weighted to them get the shape keys)."""
        return []

    def info(self):
        return {}


# --------------------------------------------------------------------------------------------------
# MetaHuman DNA
# --------------------------------------------------------------------------------------------------
_DNA_CACHE = {}

# where a MetaHuman joint ends up after an XPS export / an MMD conversion
RENAMED = {"FACIAL_L_Eye": ("head eyeball left", "左目", "目.L"), "FACIAL_R_Eye": ("head eyeball right", "右目", "目.R"),
           "FACIAL_C_Jaw": ("head jaw",)}
FACE_WORDS = ("facial", "eye", "目", "jaw", "顎", "tongue", "teeth", "lip", "brow", "cheek", "nose", "unused")


def load_dna(path):
    path = bpy.path.abspath(path)
    key = (path, os.path.getmtime(path))
    if key not in _DNA_CACHE:
        with open(path, "rb") as fh:
            face = dnalib.DnaFace(fh.read())
        _DNA_CACHE.clear()
        _DNA_CACHE[key] = face
    return _DNA_CACHE[key]


class DnaPoser:
    """Put DNA joint poses onto the rig.  Joints pair with bones by name (exact, case-insensitive,
    'unused_' prefix of a conversion, XPS / MMD renames), then a similarity fit maps DNA space (cm,
    Maya Y-up) onto the rig; joints still unpaired pair with a face bone sitting at the fitted
    position (< 1 mm) - that catches renames nobody listed."""

    def __init__(self, face, arm, min_joints=30):
        from mathutils import kdtree

        self.face, self.arm = face, arm
        bones = arm.data.bones
        lower = {b.name.lower(): b.name for b in bones}
        mmd_j = {}
        for pb in arm.pose.bones:
            mmd = getattr(pb, "mmd_bone", None)
            if mmd is not None and mmd.name_j:
                mmd_j.setdefault(mmd.name_j, pb.name)
        match, used = {}, set()
        facial = [j for j, n in enumerate(face.joints) if n.upper().startswith("FACIAL_")]
        for j in facial:
            name = face.joints[j]
            for cand in (name, "unused_" + name) + RENAMED.get(name, ()):
                bone = cand if cand in bones else lower.get(cand.lower()) or mmd_j.get(cand)
                if bone and bone not in used:
                    match[j] = bone
                    used.add(bone)
                    break
        if len(match) < min_joints:
            raise ValueError("DNA 的 %d 根面部关节只有 %d 根在 %s 上找到（至少要 %d）：DNA 选错了，"
                             "或者不是 MetaHuman 脸骨" % (len(facial), len(match), arm.name, min_joints))
        index = sorted(match)
        src = face.rest[index, :3, 3]
        dst = np.array([list(bones[match[j]].head_local) for j in index])
        self.scale, self.rot, self.shift, err = dnalib.fit_similarity(src, dst)
        self.mm = 10.0 / self.scale                            # mm per rig unit (the DNA is in cm)
        by_name = len(match)
        free = [b.name for b in bones if b.name not in used and any(w in b.name.lower() for w in FACE_WORDS)]
        if free and len(match) < len(facial):
            kd = kdtree.KDTree(len(free))
            for i, n in enumerate(free):
                kd.insert(bones[n].head_local, i)
            kd.balance()
            tol = 1.0 / self.mm
            for j in facial:
                if j in match:
                    continue
                p = self.scale * (self.rot @ face.rest[j, :3, 3]) + self.shift
                _co, i, dist = kd.find(Vector(p.tolist()))
                if i is not None and dist < tol and free[i] not in used:
                    match[j] = free[i]
                    used.add(free[i])
        self.index = sorted(match)
        self.names = [match[j] for j in self.index]
        self.fit = {"joints": len(self.index), "by_name": by_name, "by_position": len(self.index) - by_name,
                    "dna_joints": len(facial), "scale": float(self.scale),
                    "mean_mm": float(err.mean() * self.mm), "max_mm": float(err.max() * self.mm)}
        self.rest = {n: bones[n].matrix_local.copy() for n in self.names}
        self.parent = {n: (bones[n].parent.name if bones[n].parent else None) for n in self.names}
        self.parent_rest = {n: (bones[n].parent.matrix_local.copy() if bones[n].parent else Matrix())
                            for n in self.names}

    def bases(self, controls):
        """matrix_basis per facial bone for these raw control values."""
        posed = self.face.posed(controls)
        rest = self.face.rest
        want = {}
        for j, name in zip(self.index, self.names):
            d_rot = posed[j][:3, :3] @ rest[j][:3, :3].T
            move = self.scale * (self.rot @ (posed[j][:3, 3] - rest[j][:3, 3]))
            spin = Matrix((self.rot @ d_rot @ self.rot.T).tolist()).to_4x4()
            head = self.rest[name].to_translation()
            want[name] = (Matrix.Translation(head + Vector(move.tolist())) @ spin
                          @ Matrix.Translation(-head) @ self.rest[name])
        out = {}
        for name, posed_matrix in want.items():
            parent_pose = want.get(self.parent[name], self.parent_rest[name])
            local_rest = self.parent_rest[name].inverted() @ self.rest[name]
            out[name] = local_rest.inverted() @ parent_pose.inverted() @ posed_matrix
        return out


class DnaSource(Source):
    key, form, vocab = "DNA", POSE, "metahuman"

    def __init__(self, arm, path):
        super().__init__(arm)
        if not path or not os.path.isfile(bpy.path.abspath(path)):
            raise ValueError("选这张脸的 MetaHuman DNA 文件（.dna）")
        self.path = path
        self.face = load_dna(path)
        self.poser = DnaPoser(self.face, arm)

    def recipe(self, target):
        comps = target.get("metahuman")
        if not comps:
            return None
        against = target.get("metahuman_against") or {}
        if any(c not in self.face.raw_index for c in list(comps) + list(against)):
            return None
        return comps

    def why_not(self, target):
        comps = target.get("metahuman")
        if not comps:
            return "没有 MetaHuman 配方"
        missing = [c for c in comps if c not in self.face.raw_index]
        return "DNA 里没有 %s" % ", ".join(missing[:3])

    def against(self, target):
        return target.get("metahuman_against")

    def pose(self, components, strength=1.0):
        out = {}
        for name, basis in self.poser.bases({k: v * strength for k, v in components.items()}).items():
            loc, quat, scale = basis.decompose()
            if not _tiny(loc, quat, scale):
                out[name] = entry(loc, quat, scale)
        return out

    def face_bones(self):
        return list(self.poser.names)

    def info(self):
        f = self.poser.fit
        return {"dna": os.path.basename(bpy.path.abspath(self.path)), "joints": f["joints"], "dna_joints": f["dna_joints"],
                "by_position": f["by_position"], "fit_mean_mm": round(f["mean_mm"], 2),
                "fit_max_mm": round(f["max_mm"], 2), "blend_shape_channels": self.face.blend_shape_channels}


# --------------------------------------------------------------------------------------------------
# existing shape keys (ARKit under any spelling)
# --------------------------------------------------------------------------------------------------
def key_coords(key):
    co = np.empty(len(key.data) * 3)
    key.data.foreach_get("co", co)
    return co.reshape(-1, 3)


class ShapeKeySource(Source):
    key, form, vocab = "SHAPES", SHAPE, "arkit"

    def __init__(self, arm, meshes):
        super().__init__(arm)
        self.meshes = [m for m in meshes if m.data.shape_keys and len(m.data.shape_keys.key_blocks) > 1]
        self.alias = {m.name: names.match_arkit([k.name for k in m.data.shape_keys.key_blocks[1:]])
                      for m in self.meshes}
        self.available = set()
        for m in self.meshes:
            self.available.update(self.alias[m.name])
            self.available.update(k.name for k in m.data.shape_keys.key_blocks[1:])

    def recipe(self, target):
        comps = target.get("arkit")
        if not comps:
            return None
        for k in comps:
            opt, name = names.optional(k)
            if not opt and name not in self.available:
                return None
        return comps

    def why_not(self, target):
        comps = target.get("arkit")
        if not comps:
            return "没有 ARKit 配方"
        missing = [names.optional(k)[1] for k in comps if not names.optional(k)[0]
                   and names.optional(k)[1] not in self.available]
        return "模型缺 %s" % ", ".join(missing[:3])

    def shapes(self, components, strength=1.0):
        out = {}
        for m in self.meshes:
            blocks = m.data.shape_keys.key_blocks
            acc = None
            for k, w in components.items():
                _opt, name = names.optional(k)
                key = blocks.get(name) or blocks.get(self.alias[m.name].get(name, ""))
                if key is None:
                    continue
                d = (key_coords(key) - key_coords(key.relative_key)) * (w * strength)
                acc = d if acc is None else acc + d
            if acc is not None and np.abs(acc).max() > 0.0:
                out[m.name] = acc
        return out

    def info(self):
        n = max((len(a) for a in self.alias.values()), default=0)
        return {"meshes_with_keys": [m.name for m in self.meshes], "arkit_keys": n}


# --------------------------------------------------------------------------------------------------
# pose library: captured poses, actions, pose markers
# --------------------------------------------------------------------------------------------------
POSE_PROP = "expression_kit_poses"      # armature object: JSON {name: {bone: [lx, ly, lz, qw, qx, qy, qz(, sx, sy, sz)]}}
_CHANNEL = re.compile(r'^pose\.bones\["(.+)"\]\.(location|rotation_quaternion|rotation_euler|rotation_axis_angle|scale)$')


def captured(arm):
    try:
        raw = json.loads(arm.get(POSE_PROP, "{}"))
    except ValueError:
        return {}
    return {name: {b: entry(Vector(v[:3]), Quaternion(v[3:7]), Vector(v[7:10]) if len(v) >= 10 else None)
                   for b, v in pose.items()} for name, pose in raw.items()}


def capture(arm, name, selected_only=False):
    """Store the armature's current pose (bones that are not at rest) under ``name``."""
    pose = {}
    for pb in arm.pose.bones:
        if selected_only and not pb.bone.select:
            continue
        loc, quat, scale = pb.matrix_basis.decompose()
        if not _tiny(loc, quat, scale):
            pose[pb.name] = list(loc) + list(quat) + (list(scale) if len(entry(loc, quat, scale)) > 2 else [])
    if not pose:
        raise ValueError("骨架在静止姿势：先把脸摆好")
    try:
        raw = json.loads(arm.get(POSE_PROP, "{}"))
    except ValueError:
        raw = {}
    raw[name] = pose
    arm[POSE_PROP] = json.dumps(raw, ensure_ascii=False)
    return len(pose)


def store(arm, name, pose):
    """Keep a {bone: entry} pose under ``name`` (手调 / mirrored poses)."""
    if not pose:
        raise ValueError("姿势是空的：脸部骨骼都在静止位置")
    try:
        raw = json.loads(arm.get(POSE_PROP, "{}"))
    except ValueError:
        raw = {}
    raw[name] = {b: list(v[0]) + list(v[1]) + (list(v[2]) if len(v) > 2 else []) for b, v in pose.items()}
    arm[POSE_PROP] = json.dumps(raw, ensure_ascii=False)
    return len(pose)


def forget(arm, name):
    raw = json.loads(arm.get(POSE_PROP, "{}"))
    removed = raw.pop(name, None) is not None
    arm[POSE_PROP] = json.dumps(raw, ensure_ascii=False)
    return removed


def _evaluate_action(action, frame):
    """{bone: (location, quaternion)} of an action at a frame, read from its F-curves directly."""
    chans = {}
    for fc in action.fcurves:
        m = _CHANNEL.match(fc.data_path)
        if m:
            chans.setdefault(m.group(1), {}).setdefault(m.group(2), {})[fc.array_index] = fc.evaluate(frame)
    out = {}
    for bone, ch in chans.items():
        loc = Vector([ch.get("location", {}).get(i, 0.0) for i in range(3)])
        if "rotation_quaternion" in ch:
            q = ch["rotation_quaternion"]
            quat = Quaternion([q.get(0, 1.0), q.get(1, 0.0), q.get(2, 0.0), q.get(3, 0.0)]).normalized()
        elif "rotation_euler" in ch:
            from mathutils import Euler
            quat = Euler([ch["rotation_euler"].get(i, 0.0) for i in range(3)]).to_quaternion()
        elif "rotation_axis_angle" in ch:
            a = ch["rotation_axis_angle"]
            quat = Quaternion(Vector([a.get(1, 0.0), a.get(2, 1.0), a.get(3, 0.0)]), a.get(0, 0.0))
        else:
            quat = Quaternion()
        scale = Vector([ch.get("scale", {}).get(i, 1.0) for i in range(3)])
        if not _tiny(loc, quat, scale):
            out[bone] = entry(loc, quat, scale)
    return out


def action_poses(action, use_markers=True):
    """One pose per pose marker (named after the marker), or the whole action as one pose."""
    if action is None:
        return {}
    if use_markers and len(action.pose_markers):
        return {m.name: _evaluate_action(action, m.frame) for m in action.pose_markers}
    return {action.name: _evaluate_action(action, action.frame_range[0])}


def combine(poses, weights, neutral=None):
    """Weighted mix of poses in pose space, relative to a neutral pose: translations add,
    rotations are raised to the weight and multiplied (what MMD does with several bone morphs),
    scales are raised to the weight and multiplied."""
    out = {}
    for name, w in weights.items():
        for bone, value in poses[name].items():
            loc, quat, scale = unpack(value)
            scale = scale if scale is not None else ONE.copy()
            if neutral and bone in neutral:
                n_loc, n_quat, n_scale = unpack(neutral[bone])
                loc, quat = loc - n_loc, n_quat.inverted() @ quat
                if n_scale is not None:
                    scale = Vector([a / b for a, b in zip(scale, n_scale)])
            l0, q0, s0 = out.get(bone, (Vector(), Quaternion(), ONE.copy()))
            axis, angle = quat.to_axis_angle()
            s1 = Vector([a * (max(b, 1e-6) ** w) for a, b in zip(s0, scale)])
            out[bone] = (l0 + loc * w, Quaternion(axis, angle * w) @ q0, s1)
    return {b: entry(*v) for b, v in out.items() if not _tiny(*v)}


class PoseLibrarySource(Source):
    key, form, vocab = "POSES", POSE, "names"

    def __init__(self, arm, action=None, use_markers=True, neutral=""):
        super().__init__(arm)
        self.poses = action_poses(action, use_markers)
        self.poses.update(captured(arm))
        self.neutral = self.poses.get(neutral) if neutral else None
        if not self.poses:
            raise ValueError("姿势库是空的：先记录姿势，或选一个带姿势标记的动作")

    def recipe(self, target):
        comps = target.get("names")
        if comps:
            return comps if all(k in self.poses for k in comps) else None
        return {target["name"]: 1.0} if target["name"] in self.poses else None

    def why_not(self, target):
        return "姿势库里没有「%s」" % target["name"]

    def pose(self, components, strength=1.0):
        return combine(self.poses, {k: v * strength for k, v in components.items()}, self.neutral)

    def face_bones(self):
        return sorted({b for p in self.poses.values() for b in p})

    def info(self):
        return {"poses": sorted(self.poses)}


# --------------------------------------------------------------------------------------------------
# bone-only faces
# --------------------------------------------------------------------------------------------------
class RoleSource(Source):
    key, form, vocab = "ROLES", POSE, "roles"

    def __init__(self, arm):
        super().__init__(arm)
        self.face = roles.resolve(arm)

    def recipe(self, target):
        acts = target.get("roles")
        if not acts or not self.face.calibrated:
            return None
        if all(self.face.bone(role) is None for role, _k, _a in acts):
            return None
        return acts

    def why_not(self, target):
        if not self.face.calibrated:
            return "没找到成对的眼 / 眼皮 / 眉骨，无法标定"
        return "没有骨骼脸配方" if not target.get("roles") else "脸上缺这些骨"

    def pose(self, components, strength=1.0):
        return {b: v for b, v in roles.offsets(self.face, components, strength).items() if not _tiny(*v)}

    def face_bones(self):
        names_ = set(self.face.bones.values())
        for followers in self.face.followers.values():
            names_.update(followers)
        return sorted(names_)

    def info(self):
        return {"roles": len(self.face.bones), "calibrated": self.face.calibrated, "style": self.face.style,
                "eye_spacing": round(self.face.unit, 4), "describe": self.face.describe()}


def roles_usable(face):
    """A bone face worth the role recipes: calibrated, and lids or a jaw or mouth corners found
    (eyes alone - most MMD models - are not a face rig)."""
    return face.calibrated and any(face.bone(r) for r in ("chin", "upper_lid_L", "upper_lid_R", "corner_L"))


# --------------------------------------------------------------------------------------------------
# MetaHuman face bones without a DNA (convert/face.py)
# --------------------------------------------------------------------------------------------------
class FaceBoneSource(Source):
    """convert/face.py: position-driven fields over the dense FACIAL_* rig, calibrated on the skin
    mesh.  Recipes are face.MORPHS (the MMD names); there are no ARKit ones."""
    key, form, vocab = "FACEBONES", POSE, "facebones"

    def __init__(self, arm, meshes):
        super().__init__(arm)
        from ..convert import face as facemod
        try:
            self.face = facemod.Face(arm)
        except LookupError as exc:
            raise ValueError("脸骨来源要 MetaHuman 式脸骨（FACIAL_*）：%s" % exc)
        self.face.calibrate(meshes)
        self.table = {name: comps for name, _e, _c, comps in facemod.MORPHS}

    def recipe(self, target):
        return self.table.get(target["name"])

    def why_not(self, target):
        if target["name"] in names.ARKIT_52:
            return "脸骨来源只做 MMD 表情（ARKit 要 DNA）"
        return "脸骨来源没有这个表情"

    def pose(self, components, strength=1.0):
        offsets = self.face.offsets(components)          # already without negligible entries
        if strength == 1.0:
            return offsets
        out = {}
        for bone, (loc, quat) in offsets.items():       # like an MMD morph weight: translation
            axis, angle = quat.to_axis_angle()          # scales, rotation is raised to a power
            loc, quat = loc * strength, Quaternion(axis, angle * strength)
            if not _tiny(loc, quat):
                out[bone] = (loc, quat)
        return out

    def face_bones(self):
        return list(self.face.names)

    def info(self):
        return {"bones": len(self.face.names), "eye_spacing": round(self.face.unit, 4), "jaw": self.face.jaw}


# --------------------------------------------------------------------------------------------------
# FF7 Remake / Rebirth face bones + the game's expression data (ff7.py)
# --------------------------------------------------------------------------------------------------
class FF7Source(Source):
    key, form, vocab = "FF7", POSE, "ff7"

    def __init__(self, arm, meshes, data_path=""):
        super().__init__(arm)
        if not ff7.is_ff7_face(arm):
            raise ValueError("不是 FF7 脸骨（要有 C_FaceBase_a、L_Eye / R_Eye 和眼皮骨）")
        self.path = ff7.find_face_data(arm, meshes, data_path)
        if not self.path:
            raise ValueError("没找到 FF7 表情数据 JSON：在面板里选文件（ripper_tpose 的 ff7rb_face_data.py / "
                             "ff7_face_data.py 生成）")
        self.data = ff7.load_face_data(self.path)
        self.face = ff7.FaceData(self.data, arm)

    @staticmethod
    def _split(target):
        r = ff7.recipe(target["name"])
        if isinstance(r, dict):
            return r["main"], r.get("against")
        return r, None

    def recipe(self, target):
        main, against = self._split(target)
        if not main or not self.face.has(main + (against or [])):
            return None
        return main

    def why_not(self, target):
        main, against = self._split(target)
        if not main:
            return "没有 FF7 配方"
        return "表情数据里没有 %s" % ", ".join(self.face.missing(main + (against or []))[:3])

    def against(self, target):
        return self._split(target)[1]

    def pose(self, components, strength=1.0):
        deltas = ff7.evaluate(self.face, components, strength)
        return {b: v for b, v in ff7.to_basis(self.arm, deltas).items() if not _tiny(*v)}

    def face_bones(self):
        return list(self.face.bones)

    def info(self):
        return {"data": os.path.basename(self.path), "poses": len(self.data["poses"]),
                "visemes": sorted(self.data["lipmap"]["shapes"]), "poses_from": self.data.get("poses_from", ""),
                "scale": round(self.face.scale, 4)}


def metahuman_face(arm):
    bones = arm.data.bones
    return bones.get("FACIAL_C_FacialRoot") is not None and all(
        bones.get("FACIAL_%s_Eyelid%sA" % (s, p)) is not None for s in "LR" for p in ("Upper", "Lower"))


ARKIT_MIN = 20          # this many ARKit shape keys = a model built for face tracking


def own_arkit_keys(meshes):
    """Most ARKit keys on one mesh that the model came with - keys this tool baked are tagged and
    don't count (else the next AUTO run would mix our own bake instead of using the real source)."""
    from .engine import tags
    best = 0
    for m in meshes:
        if m.data.shape_keys:
            mine = set(tags(m).get("ARKIT", []))
            best = max(best, len(names.match_arkit([k.name for k in m.data.shape_keys.key_blocks[1:]
                                                    if k.name not in mine])))
    return best


def auto_kind(arm, meshes, dna_path="", ff7_data=""):
    """(kind, why) the AUTO source picks: a DNA the user gave, the model's own ARKit keys, an FF7 face
    with its expression data, MetaHuman face bones, a recognised bone face.  None when nothing fits.
    The pose library is never guessed."""
    if dna_path and os.path.isfile(bpy.path.abspath(dna_path)):
        return "DNA", "给了 DNA 文件"
    n = own_arkit_keys(meshes)
    if n >= ARKIT_MIN:
        return "SHAPES", "模型自带 %d 个 ARKit 形态键" % n
    if ff7.is_ff7_face(arm):
        path = ff7.find_face_data(arm, meshes, ff7_data)
        if path:
            return "FF7", "FF7 脸骨 + %s" % os.path.basename(path)
        return None, "FF7 脸骨，但没找到表情数据 JSON：在面板里选文件"
    if metahuman_face(arm):
        return "FACEBONES", "MetaHuman 式脸骨"
    if roles_usable(roles.resolve(arm)):
        return "ROLES", "认出骨骼脸"
    return None, "没有可用的来源：不是 MetaHuman / FF7 脸骨、没有 ARKit 形态键、也没认出骨骼脸"


def make(kind, arm, meshes, dna_path="", action=None, use_markers=True, neutral="", ff7_data=""):
    if kind == "AUTO":
        kind, why = auto_kind(arm, meshes, dna_path, ff7_data)
        if kind is None:
            raise ValueError(why)
    if kind == "FACEBONES":
        return FaceBoneSource(arm, meshes)
    if kind == "FF7":
        return FF7Source(arm, meshes, ff7_data)
    if kind == "DNA":
        return DnaSource(arm, dna_path)
    if kind == "SHAPES":
        return ShapeKeySource(arm, meshes)
    if kind == "POSES":
        return PoseLibrarySource(arm, action, use_markers, neutral)
    if kind == "ROLES":
        return RoleSource(arm)
    raise ValueError("unknown source %r" % kind)
