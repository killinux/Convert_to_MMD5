# -*- coding: utf-8 -*-
"""mmd_tools side: bone morphs, vertex morph registration, the 表情 display frame, the morph
slider, shape keys parked around a Convert_to_MMD5 conversion, and a PMX export wrapper.

mmd_tools facts this relies on (UuuNyaa fork 1.0.x for Blender 3.6):
  * every shape key of the model's meshes becomes a PMX vertex morph (except Basis, mmd_bind*,
    SDEF); its panel and English name come from root.mmd_root.vertex_morphs, else 'other';
  * a bone morph item stores location + rotation in the bone's pose space;
  * MMD's expression panel lists the 表情 display frame, not the morph lists;
  * the morph slider mirrors morphs into drivers - unbind before changing morphs, rebuild after.
"""
import bpy
import numpy as np

STASH_TAG = "expression_kit_stash"          # mesh property: the fake-user mesh holding parked keys
STASH_PREFIX = "ExpressionKit_stash_"


def find_root(obj):
    """The mmd_tools root of obj's model, or None (also None when mmd_tools is not installed)."""
    if obj is None or not hasattr(bpy.types.Object, "mmd_type"):     # mmd_tools not enabled
        return None
    try:
        from mmd_tools.core.model import Model
    except ImportError:
        return None
    return Model.findRoot(obj)


class SliderState:
    """Suspend mmd_tools' morph slider while the morph lists change (else Blender previews the old
    offsets and the PMX carries the new ones)."""

    def __init__(self, root):
        self.slider = None
        if root is not None:
            from mmd_tools.core.model import Model
            self.slider = Model(root).morph_slider
        self.existed = self.slider is not None and self.slider.placeholder() is not None
        self.bound = self.existed and self.slider.placeholder(binded=True) is not None

    def __enter__(self):
        if self.bound:
            self.slider.unbind()
        return self

    def __exit__(self, *exc):
        if self.existed:
            try:
                self.slider.create()
                if self.bound:
                    self.slider.bind()
            except Exception as error:            # a preview aid: never lose morphs over it
                print("expression_kit: morph slider not restored: %s" % error)
        return False


def refresh_facial_frame(root):
    from mmd_tools.core.model import Model
    from mmd_tools.operators.display_item import DisplayItemQuickSetup

    frames = root.mmd_root.display_item_frames
    if "表情" not in frames:
        Model(root).initialDisplayFrames(reset=False)
    frames["表情"].data.clear()                    # else it keeps the old order
    DisplayItemQuickSetup.load_facial_items(root.mmd_root)


def bone_morph_names(root):
    return [m.name for m in root.mmd_root.bone_morphs]


def write_bone_morph(root, name, name_e, category, pose, arm=None):
    """Replace / create the bone morph ``name`` from {bone: (location, quaternion)} (a PMX bone morph
    has no scale; engine.py sends scaled expressions to vertex morphs instead).  Bones the morph
    translates get their location unlocked: mmd_tools exports a fully locked bone as not movable."""
    morphs = root.mmd_root.bone_morphs
    index = morphs.find(name)
    if index >= 0:
        morph = morphs[index]
        morph.data.clear()
    else:
        morph = morphs.add()
        morph.name = name
    morph.name_e, morph.category = name_e, category
    for bone, value in pose.items():
        item = morph.data.add()
        item.bone, item.location, item.rotation = bone, value[0], value[1]
        pb = arm.pose.bones.get(bone) if arm is not None else None
        if pb is not None and value[0].length > 1e-7 and any(pb.lock_location):
            pb.lock_location = (False, False, False)
    return morph


def remove_bone_morphs(root, names):
    morphs = root.mmd_root.bone_morphs
    removed = []
    for name in names:
        index = morphs.find(name)
        if index >= 0:
            morphs.remove(index)
            removed.append(name)
    if removed:
        root.mmd_root.active_morph = max(0, min(root.mmd_root.active_morph, len(morphs) - 1))
    return removed


def register_vertex_morphs(root, entries, at_top=True):
    """[(name, name_e, category)] -> mmd_root.vertex_morphs, in this order at the top (the PMX morph
    list follows this collection; keys not listed in it would even go FIRST)."""
    morphs = root.mmd_root.vertex_morphs
    for target, (name, name_e, category) in enumerate(entries):
        index = morphs.find(name)
        item = morphs[index] if index >= 0 else morphs.add()
        item.name, item.name_e, item.category = name, name_e, category
        if at_top:
            morphs.move(morphs.find(name), target)


def remove_vertex_morph_entries(root, names):
    morphs = root.mmd_root.vertex_morphs
    for name in names:
        index = morphs.find(name)
        if index >= 0:
            morphs.remove(index)


def pose_bone_morph(root, arm, name, weight=1.0):
    """Put the bones where the bone morph says, scaled by weight (like mmd_tools' View)."""
    from mathutils import Quaternion, Vector
    morph = root.mmd_root.bone_morphs.get(name)
    if morph is None:
        return 0
    for item in morph.data:
        pb = arm.pose.bones.get(item.bone)
        if pb is None:
            continue
        axis, angle = Quaternion(item.rotation).to_axis_angle()
        pb.rotation_mode = "QUATERNION"
        pb.rotation_quaternion = Quaternion(axis, angle * weight)
        pb.location = Vector(item.location) * weight
    return len(morph.data)


def morph_bones(root):
    return {item.bone for m in root.mmd_root.bone_morphs for item in m.data if item.bone}


# --- parking shape keys around a Convert_to_MMD5 conversion ----------------------------------------
def stash(meshes):
    """Move every shape key of these meshes into a fake-user copy of the mesh data (saved with the
    .blend) and take them off.  Convert_to_MMD5's pose bakes (A-pose, arm and finger alignment)
    skip meshes that carry shape keys.  Returns {object: stash mesh}."""
    out = {}
    for m in meshes:
        if not m.data.shape_keys or len(m.data.shape_keys.key_blocks) < 2:
            continue
        old = bpy.data.meshes.get(m.get(STASH_TAG, ""))
        if old is not None:
            bpy.data.meshes.remove(old)
        copy = m.data.copy()
        copy.name = STASH_PREFIX + m.name
        copy.use_fake_user = True
        m[STASH_TAG] = copy.name
        m.shape_key_clear()
        out[m.name] = copy.name
    return out


def stashed(meshes):
    return [m.name for m in meshes if bpy.data.meshes.get(m.get(STASH_TAG, "")) is not None]


def _fit_linear(src, dst):
    a, b = src - src.mean(axis=0), dst - dst.mean(axis=0)
    u, s, vt = np.linalg.svd(a.T @ b)
    d = np.sign(np.linalg.det(u @ vt))
    rot = (u @ np.diag([1.0, 1.0, d]) @ vt).T
    scale = (s * [1.0, 1.0, d]).sum() / max((a * a).sum(), 1e-12)
    return rot * scale


def restore(meshes, relative_tolerance=1e-4):
    """Put parked keys back.  If the conversion moved the face rigidly (turned, scaled to metres)
    the offsets follow it; if it deformed the face, that mesh's keys are dropped and reported."""
    report = {}
    for m in meshes:
        copy = bpy.data.meshes.get(m.get(STASH_TAG, ""))
        if copy is None:
            continue
        n = len(m.data.vertices)
        keys = copy.shape_keys.key_blocks if copy.shape_keys else []
        if len(copy.vertices) != n or len(keys) < 2:
            report[m.name] = "vertex count changed (%d -> %d) - keys not restored" % (len(copy.vertices), n)
            continue
        base = np.empty(n * 3)
        keys[0].data.foreach_get("co", base)
        base = base.reshape(-1, 3)
        deltas, touched = [], np.zeros(n, dtype=bool)
        for kb in list(keys)[1:]:
            co = np.empty(n * 3)
            kb.data.foreach_get("co", co)
            d = co.reshape(-1, 3) - base
            idx = np.nonzero(np.any(d != 0.0, axis=1))[0]
            deltas.append((kb.name, idx, d[idx], kb.slider_min, kb.slider_max))
            touched[idx] = True
        now = np.empty(n * 3)
        m.data.vertices.foreach_get("co", now)
        now = now.reshape(-1, 3)
        ref = np.nonzero(touched)[0]
        lin = np.eye(3)
        moved = float(np.abs(now[ref] - base[ref]).max()) if len(ref) else 0.0
        size = float(np.abs(now[ref] - now[ref].mean(axis=0)).max()) if len(ref) else 1.0
        tolerance = max(size, 1e-9) * relative_tolerance
        if moved > tolerance:
            lin = _fit_linear(base[ref], now[ref])
            fitted = (base[ref] - base[ref].mean(axis=0)) @ lin.T + now[ref].mean(axis=0)
            if float(np.abs(fitted - now[ref]).max()) > tolerance:
                report[m.name] = "the face deformed during the conversion - keys not restored"
                continue
        if m.data.shape_keys is None:
            m.shape_key_add(name="Basis", from_mix=False)
        live = m.data.shape_keys.key_blocks
        for name, idx, d, lo, hi in deltas:
            kb = live.get(name) or m.shape_key_add(name=name, from_mix=False)
            buf = now.copy()
            buf[idx] += d @ lin.T
            kb.data.foreach_set("co", buf.ravel())
            kb.slider_min, kb.slider_max, kb.value = lo, hi, 0.0
        del m[STASH_TAG]
        bpy.data.meshes.remove(copy)
        report[m.name] = {"keys": len(deltas), "face_moved": round(moved, 6)}
    return report


# --- PMX export --------------------------------------------------------------------------------------
def export_pmx(root, path, scale=12.5, copy_textures=True, park=()):
    """mmd_tools' PMX export with some shape keys parked for the duration (e.g. the 52 ARKit keys,
    which would otherwise go into the PMX as 52 'other' vertex morphs).  ``park`` = key names."""
    from mmd_tools.core.model import Model
    meshes = list(Model(root).meshes())
    parked = []
    park = set(park)
    for m in meshes:
        if not m.data.shape_keys:
            continue
        for kb in list(m.data.shape_keys.key_blocks)[1:]:
            if kb.name in park:
                co = np.empty(len(kb.data) * 3)
                kb.data.foreach_get("co", co)
                parked.append((m, kb.name, co, kb.relative_key.name, kb.slider_min, kb.slider_max))
                m.shape_key_remove(kb)
    try:
        view_layer = bpy.context.view_layer
        view_layer.objects.active = root
        root.select_set(True)
        bpy.ops.mmd_tools.export_pmx(filepath=bpy.path.abspath(path), scale=scale, copy_textures=copy_textures)
    finally:
        # back at the end of each mesh's key list (Faceit and the PMX go by name, not by order)
        for m, name, co, rel, lo, hi in parked:
            kb = m.shape_key_add(name=name, from_mix=False)
            kb.data.foreach_set("co", co)
            kb.relative_key = m.data.shape_keys.key_blocks.get(rel, m.data.shape_keys.reference_key)
            kb.slider_min, kb.slider_max, kb.value = lo, hi, 0.0
    return {"parked": len({p[1] for p in parked}), "path": bpy.path.abspath(path)}     # distinct key names
