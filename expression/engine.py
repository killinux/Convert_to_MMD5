# -*- coding: utf-8 -*-
"""source + recipe set -> MMD bone morphs and/or shape keys (MMD vertex morphs, ARKit 52).

Outputs of the MMD set:
  BONE    every expression an mmd_tools bone morph (needs a pose source and an mmd_tools model)
  VERTEX  every expression a shape key = PMX vertex morph (any source; registered in the model's
          morph panels when it is an mmd_tools model, else just keys - convert later)
  AUTO    per expression: a bone morph when a 4-weight PMX shows it within ``threshold_mm`` of the
          real rig, otherwise a vertex morph (pose source + mmd_tools model)
The ARKit set always writes shape keys (that is what Faceit and face trackers drive).

One name, one kind: writing a bone morph removes shape keys of the same name and vice versa
(``replace``), otherwise MMD would apply both and the face would move twice.

Ported from ripper_tpose's expression_kit; the tags are the same, so either add-on recognises
what the other made.
"""
import json

import numpy as np

from . import bake, mmd, recipes

TAG = "expression_kit"        # mesh: JSON {set: [shape keys made]}; root: JSON {"BONE": [bone morphs made]}
LEGACY_TAG = "c2m_face"       # root: names made by the old 表情(脸骨) operator (before this engine)


def tags(obj):
    try:
        return json.loads(obj.get(TAG, "{}"))
    except (TypeError, ValueError):
        return {}


def _add_tags(obj, key, names_):
    t = tags(obj)
    have = t.get(key, [])
    t[key] = have + [n for n in names_ if n not in have]
    obj[TAG] = json.dumps(t, ensure_ascii=False)


def _drop_tags(obj, key, names_):
    t = tags(obj)
    if key in t:
        t[key] = [n for n in t[key] if n not in set(names_)]
        obj[TAG] = json.dumps(t, ensure_ascii=False)


def made_keys(meshes, set_key=None):
    out = []
    for m in meshes:
        for k, names_ in tags(m).items():
            if set_key in (None, k):
                out += [n for n in names_ if n not in out]
    return out


def made_bone_morphs(root):
    if root is None:
        return []
    out = list(tags(root).get("BONE", []))
    if LEGACY_TAG in root:          # read-only here (draw callbacks); adopt_legacy() converts the tag
        out += [str(n) for n in root[LEGACY_TAG] if str(n) not in out]
    return out


def adopt_legacy(root):
    """Bone morphs the old 表情(脸骨) operator made count as made here (rebuilt / cleared like ours)."""
    if root is None or LEGACY_TAG not in root:
        return 0
    old = [str(n) for n in root[LEGACY_TAG] if root.mmd_root.bone_morphs.find(str(n)) >= 0]
    _add_tags(root, "BONE", old)
    del root[LEGACY_TAG]
    return len(old)


def _remove_keys(meshes, names_):
    removed = []
    wanted = set(names_)
    for m in meshes:
        if not m.data.shape_keys:
            continue
        for name in [kb.name for kb in m.data.shape_keys.key_blocks][1:]:     # by name: removal
            if name in wanted:                                                # invalidates refs
                m.shape_key_remove(m.data.shape_keys.key_blocks[name])
                removed.append(name)
        for set_key in list(tags(m)):
            _drop_tags(m, set_key, wanted)
        if m.data.shape_keys and len(m.data.shape_keys.key_blocks) == 1:     # only Basis left
            m.shape_key_clear()
    return sorted(set(removed))


def _foreign(meshes, root, name, set_key, writing):
    """``name`` already exists and was not made here - with replace off it is left alone.  For the
    MMD set that includes the model's own material / UV / group morphs (a PMX keeps one per name)."""
    for m in meshes:
        if m.data.shape_keys and name in m.data.shape_keys.key_blocks and name not in tags(m).get(set_key, []):
            return True
    if root is None or not (writing == "bone" or set_key == recipes.MMD):
        return False
    mr = root.mmd_root
    if mr.bone_morphs.find(name) >= 0 and name not in made_bone_morphs(root):
        return True
    return set_key == recipes.MMD and any(getattr(mr, c).find(name) >= 0
                                          for c in ("material_morphs", "uv_morphs", "group_morphs"))


def _write_key(mesh, name, base, delta, eps):
    delta = delta.copy()
    delta[np.linalg.norm(delta, axis=1) < eps] = 0.0
    if not delta.any():
        return 0
    if mesh.data.shape_keys is None:
        mesh.shape_key_add(name="Basis", from_mix=False)
    blocks = mesh.data.shape_keys.key_blocks
    key = blocks.get(name) or mesh.shape_key_add(name=name, from_mix=False)
    key.relative_key = mesh.data.shape_keys.reference_key
    key.data.foreach_set("co", (base + delta).ravel())
    key.slider_min, key.slider_max, key.value = 0.0, 1.0, 0.0
    return int(np.count_nonzero(np.any(delta != 0.0, axis=1)))


def plan(source, targets):
    """[(target, components, against)] this source can make, and [(name, reason)] it cannot."""
    doable, skipped = [], []
    for t in targets:
        comps = source.recipe(t)
        if comps is None:
            skipped.append((t["name"], source.why_not(t)))
        else:
            doable.append((t, comps, source.against(t)))
    return doable, skipped


def build(arm, meshes, root, source, set_key, targets, output="VERTEX", strengths=None, replace=True,
          threshold_mm=0.3, log=print):
    strengths = strengths or {}
    report = {"set": set_key, "source": source.key, "output": output, "bone": [], "vertex": [], "skipped": [],
              "removed": [], "errors_mm": {}, "disconnected": 0}
    if set_key == recipes.ARKIT:
        output = "VERTEX"
        if source.key == "SHAPES":
            report["note"] = "模型本来就有 ARKit 形态键，直接注册到 Faceit 即可"
            return report
    if output in ("BONE", "AUTO") and source.form != "pose":
        output = report["output"] = "VERTEX"            # shape keys have no bone movement to keep
        report["note"] = "这个来源只有形态键，MMD 表情做成顶点表情"
    elif output == "AUTO" and root is None:             # a game model before the conversion
        output = report["output"] = "VERTEX"
        report["note"] = "还不是 mmd_tools 模型：MMD 表情先做成形态键（转换、导出 PMX 后就是顶点表情）"
    if output in ("BONE", "AUTO") and root is None:
        raise ValueError("骨骼表情要 mmd_tools 模型：先完成转换（或改用顶点表情）")
    if set_key == recipes.MMD:
        adopt_legacy(root)
    doable, report["skipped"] = plan(source, targets)
    if not replace:
        keep = []
        for t, comps, against in doable:
            if _foreign(meshes, root, t["name"], set_key, "bone" if output == "BONE" else "vertex"):
                report["skipped"].append((t["name"], "已有同名表情（不是本工具做的，没开替换同名）"))
            else:
                keep.append((t, comps, against))
        doable = keep
    if not doable:
        return report
    unit = bake.mm_per_unit(arm)
    eps = 0.005 / unit                                     # offsets below 0.005 mm are rounding noise

    poses, against_poses = {}, {}
    if source.form == "pose":
        for t, comps, against in doable:
            s = strengths.get(t["category"], 1.0)
            poses[t["name"]] = source.pose(comps, s)
            if against:
                against_poses[t["name"]] = source.pose(against, s)
        moving = {b for p in list(poses.values()) + list(against_poses.values())
                  for b, value in p.items() if value[0].length > 1e-9}
        try:
            report["disconnected"] = bake.disconnect(arm, moving)
        except RuntimeError as exc:     # e.g. the armature can't enter edit mode: only Blender's preview
            log("connected bones not freed: %s" % exc)      # misses those moves, the morphs are right

    with mmd.SliderState(root):
        if output == "BONE":
            for t, _comps, _a in doable:
                pose = poses[t["name"]]
                if not pose:
                    report["skipped"].append((t["name"], "没有动作"))
                    continue
                if _scales(pose):
                    report["skipped"].append((t["name"], "要缩放骨骼，骨骼表情做不了（用顶点 / 自动）"))
                    continue
                report["removed"] += _clear_other_kind(meshes, root, t["name"], "bone")
                mmd.write_bone_morph(root, t["name"], t["name_e"], t["category"], pose, arm)
                report["bone"].append(t["name"])
        elif source.form == "shape":
            for t, comps, _a in doable:
                deltas = source.shapes(comps, strengths.get(t["category"], 1.0))
                moved = 0
                for m in source.meshes:
                    if m.name in deltas:
                        moved += _write_key(m, t["name"], bake.basis_coords(m), deltas[m.name], eps)
                if not moved:
                    report["skipped"].append((t["name"], "没有动作"))
                    continue
                if set_key == recipes.MMD:
                    report["removed"] += _clear_other_kind(meshes, root, t["name"], "vertex")
                for m in source.meshes:
                    if m.data.shape_keys and t["name"] in m.data.shape_keys.key_blocks:
                        _add_tags(m, set_key, [t["name"]])
                report["vertex"].append(t["name"])
                log("  %-12s %6d verts" % (t["name"], moved))
        else:
            _bake_poses(arm, meshes, root, source, set_key, doable, poses, against_poses, output,
                        threshold_mm, unit, eps, report, log)
        if set_key == recipes.ARKIT and root is not None and report["vertex"]:
            # listed after the MMD morphs as 'other' - unlisted keys would head the PMX morph list
            mmd.register_vertex_morphs(root, [(n, n, "OTHER") for n in report["vertex"]], at_top=False)
        if set_key == recipes.MMD and root is not None:
            by_name = {t["name"]: t for t, _c, _a in doable}
            if report["vertex"]:
                mmd.register_vertex_morphs(root, [(n, by_name[n]["name_e"], by_name[n]["category"])
                                                  for n in report["vertex"]])
            if report["bone"]:
                _add_tags(root, "BONE", report["bone"])
            if report["bone"] or report["vertex"] or report["removed"]:
                try:
                    mmd.refresh_facial_frame(root)
                except Exception as exc:            # cosmetic; the morphs are in
                    log("facial display frame: %s" % exc)
    return report


def _scales(pose):
    return any(len(value) > 2 for value in pose.values())


def _clear_other_kind(meshes, root, name, writing):
    """Before writing a bone morph remove same-named shape keys, before a shape key the bone morph.
    With replace off, build() already skipped names something else made, so what is left is ours."""
    removed = []
    if writing == "bone":
        clash = [m for m in meshes if m.data.shape_keys and name in m.data.shape_keys.key_blocks]
        if clash:
            removed += ["%s (shape key)" % n for n in _remove_keys(clash, [name])]
            if root is not None:
                mmd.remove_vertex_morph_entries(root, [name])
    elif root is not None and root.mmd_root.bone_morphs.find(name) >= 0:
        removed += ["%s (bone morph)" % n for n in mmd.remove_bone_morphs(root, [name])]
        _drop_tags(root, "BONE", [name])
    return removed


def _bake_poses(arm, meshes, root, source, set_key, doable, poses, against_poses, output,
                threshold_mm, unit, eps, report, log):
    targets_meshes = bake.skinned_meshes(arm, meshes, source.face_bones())
    if not targets_meshes:
        raise ValueError("%s 没有网格绑在这个来源要动的骨头上" % arm.name)
    models = [bake.WeightModel(arm, m) for m in targets_meshes] if output == "AUTO" else []
    with bake.RestState(arm, targets_meshes) as state:
        rest = {m.name: bake.coords(m) for m in targets_meshes}
        base = {m.name: bake.basis_coords(m) for m in targets_meshes}
        for m in targets_meshes:
            if len(rest[m.name]) != len(base[m.name]):
                raise ValueError("%s：有修改器改变了顶点数，烘焙不了" % m.name)
        for t, _comps, _a in doable:
            name = t["name"]
            pose = poses[name]
            if not pose:
                report["skipped"].append((name, "没有动作"))
                continue
            state.set_pose(pose)
            if output == "AUTO":        # the error needs only the pose: decide before evaluating meshes
                err = max((wm.error() for wm in models), default=0.0) * unit
                report["errors_mm"][name] = round(err, 3)
                if err <= threshold_mm and not _scales(pose):
                    report["removed"] += _clear_other_kind(meshes, root, name, "bone")
                    mmd.write_bone_morph(root, name, t["name_e"], t["category"], pose, arm)
                    report["bone"].append(name)
                    log("  %-12s bone morph  (PMX error %.2f mm)" % (name, err))
                    continue
            posed = {m.name: bake.coords(m) for m in targets_meshes}
            ref = rest
            if name in against_poses:
                state.set_pose(against_poses[name])
                ref = {m.name: bake.coords(m) for m in targets_meshes}
            moved = 0
            for m in targets_meshes:
                moved += _write_key(m, name, base[m.name], posed[m.name] - ref[m.name], eps)
            if not moved:
                report["skipped"].append((name, "没有动作"))
                continue
            if set_key == recipes.MMD:
                report["removed"] += _clear_other_kind(meshes, root, name, "vertex")
            for m in targets_meshes:
                if m.data.shape_keys and name in m.data.shape_keys.key_blocks:
                    _add_tags(m, set_key, [name])
            report["vertex"].append(name)
            log("  %-12s %6d verts%s" % (name, moved, ("  (PMX error %.2f mm)" % report["errors_mm"][name])
                                         if name in report["errors_mm"] else ""))


def estimate(arm, meshes, source, targets, strengths=None):
    """{name: largest PMX (4-weight) error in mm} of each expression as a bone morph - nothing written."""
    strengths = strengths or {}
    doable, _skipped = plan(source, targets)
    targets_meshes = bake.skinned_meshes(arm, meshes, source.face_bones())
    models = [bake.WeightModel(arm, m) for m in targets_meshes]
    unit = bake.mm_per_unit(arm)
    out = {"max_influences": max((wm.max_influences for wm in models), default=0),
           "over4_vertices": int(sum(len(wm.over4) for wm in models)), "errors_mm": {}, "scaled": []}
    with bake.RestState(arm, targets_meshes) as state:
        for t, comps, _a in doable:
            pose = source.pose(comps, strengths.get(t["category"], 1.0))
            if _scales(pose):                       # no bone morph possible at all
                out["scaled"].append(t["name"])
                continue
            if out["over4_vertices"]:
                state.set_pose(pose)
                out["errors_mm"][t["name"]] = round(max(wm.error() for wm in models) * unit, 3)
    return out


def clear(meshes, root, set_key):
    """Remove what this add-on made for a set (never anything else)."""
    names_ = made_keys(meshes, set_key)
    removed = _remove_keys(meshes, names_) if names_ else []
    bones = []
    if root is not None:
        with mmd.SliderState(root):
            if set_key == recipes.MMD:
                adopt_legacy(root)
                bones = mmd.remove_bone_morphs(root, made_bone_morphs(root))
                _drop_tags(root, "BONE", bones)
            mmd.remove_vertex_morph_entries(root, removed)
            try:
                mmd.refresh_facial_frame(root)
            except Exception:
                pass
    return {"shape_keys": removed, "bone_morphs": bones}


def preview(arm, meshes, root, name, weight=1.0):
    """Show one expression (bone morph pose or shape key value); everything else we made at 0."""
    reset(arm, meshes, root)
    if root is not None and name in made_bone_morphs(root):
        mmd.pose_bone_morph(root, arm, name, weight)
        return "bone"
    for m in meshes:
        if m.data.shape_keys and name in m.data.shape_keys.key_blocks:
            m.data.shape_keys.key_blocks[name].value = weight
    return "shape"


def reset(arm, meshes, root):
    if root is not None:
        from mathutils import Matrix
        for b in mmd.morph_bones(root):
            pb = arm.pose.bones.get(b)
            if pb is not None:
                pb.matrix_basis = Matrix()
    mine = set(made_keys(meshes))
    for m in meshes:
        if m.data.shape_keys:
            for kb in m.data.shape_keys.key_blocks:
                if kb.name in mine:
                    kb.value = 0.0
