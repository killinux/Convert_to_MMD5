# -*- coding: utf-8 -*-
"""Script entry points - the panel's buttons call exactly these.

    from Convert_to_MMD5.expression import api
    api.analyze(obj, dna_path="SK_Fiona_Face01.dna")
    api.build(obj, "MMD", "AUTO")                                                      # source picked per model
    api.build(obj, "MMD", "FACEBONES", output="BONE")                                  # MetaHuman bones, no DNA
    api.build(obj, "MMD", "DNA", output="VERTEX", dna_path="SK_Fiona_Face01.dna")     # MMD vertex morphs
    api.build(obj, "ARKIT", "DNA", dna_path=...)                                       # 52 ARKit keys
    api.register_faceit(obj)                                                           # Faceit live capture
    api.export_pmx(obj, "out/model.pmx", include_arkit=False)

``obj`` is any object of the model: its armature, one of its meshes or its mmd_tools root.
Ported from ripper_tpose's expression_kit.
"""
import collections
import os

import bpy

from . import bake, engine, faceit, ff7, manual, mmd, names, recipes, sources, ue_weights


def model(obj):
    """(armature, meshes deformed by it in this scene, mmd_tools root or None)."""
    root = mmd.find_root(obj)
    arm = None
    if obj is not None:
        if obj.type == "ARMATURE":
            arm = obj
        elif obj.type == "MESH":
            arm = obj.find_armature()
            if arm is None and obj.parent is not None and obj.parent.type == "ARMATURE":
                arm = obj.parent
    if arm is None and root is not None:
        from mmd_tools.core.model import Model
        arm = Model(root).armature()
    if arm is None:
        raise ValueError("先选中模型（骨架、它的网格或 mmd_tools 根对象）")
    root = root or mmd.find_root(arm)
    meshes = [o for o in bpy.context.scene.objects if o.type == "MESH" and o.find_armature() == arm]
    return arm, meshes, root


def _object_mode():
    if bpy.context.object is not None and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")


def targets(set_key, categories=None, extras=True, recipe_file=""):
    custom = recipes.load_json(bpy.path.abspath(recipe_file)) if recipe_file else None
    return recipes.targets(set_key, categories, extras, custom)


def source(obj, kind, dna_path="", action=None, use_markers=True, neutral="", ff7_data="", use_manual=False):
    """The source of this kind for the model (with its kept hand-tuned poses on top if use_manual)."""
    arm, meshes, _root = model(obj)
    src = sources.make(kind, arm, meshes, dna_path, action, use_markers, neutral, ff7_data)
    return manual.wrap(src, arm, use_manual)


def build(obj, set_key, kind, output="VERTEX", categories=None, extras=True, strengths=None, replace=True,
          threshold_mm=0.3, recipe_file="", dna_path="", action=None, use_markers=True, neutral="", ff7_data="",
          use_manual=True, log=print):
    """Make the expressions of a set (recipes.MMD / recipes.ARKIT) from a source kind (AUTO / FACEBONES /
    FF7 / DNA / SHAPES / POSES / ROLES); with use_manual a target that has a kept hand-tuned pose of
    the same name uses it.  Returns the engine report (made / skipped / removed / PMX errors)."""
    _object_mode()
    arm, meshes, root = model(obj)
    src = sources.make(kind, arm, meshes, dna_path, action, use_markers, neutral, ff7_data)
    src = manual.wrap(src, arm, use_manual)
    tg = targets(set_key, categories, extras, recipe_file)
    report = engine.build(arm, meshes, root, src, set_key, tg, output, strengths, replace, threshold_mm, log)
    report["source_info"] = src.info()
    kept = getattr(src, "poses", {})
    report["manual"] = [n for n in report["bone"] + report["vertex"] if n in kept]
    return report


def estimate(obj, kind, set_key=recipes.MMD, categories=None, extras=True, strengths=None, recipe_file="",
             dna_path="", action=None, use_markers=True, neutral="", ff7_data="", use_manual=True):
    """PMX bone-morph error per expression (mm) without writing anything."""
    _object_mode()
    src = source(obj, kind, dna_path, action, use_markers, neutral, ff7_data, use_manual)
    arm, meshes, _root = model(obj)
    if src.form != "pose":
        raise ValueError("这个来源只有形态键、没有骨骼动作，不用预估")
    return engine.estimate(arm, meshes, src, targets(set_key, categories, extras, recipe_file), strengths)


# --- 手调: load an expression onto the bones, fix it by hand, keep it ---------------------------------
def _edit_source(obj, kind, use_manual=True, **src_args):
    """(armature, source or None, face bones) for the editor; a source that cannot be built (an empty
    pose library) leaves the face bones open: then the posed bones of the armature count."""
    arm = model(obj)[0]
    try:
        src = source(obj, kind, use_manual=use_manual, **src_args)
    except ValueError:
        return arm, None, None
    return arm, src, src.face_bones()


def edit_load(obj, set_key, name, kind, use_manual=True, strength=1.0, **src_args):
    """Put a target's pose on the armature: the kept hand-tuned one, else the source's draft."""
    _object_mode()
    arm, src, bones = _edit_source(obj, kind, use_manual, **src_args)
    if src is None:
        source(obj, kind, use_manual=use_manual, **src_args)          # raise the source's own message
    target = next((t for t in recipes.BUILTIN[set_key] if t["name"] == name), None)
    if target is None:
        raise ValueError("没有这个表情：%s" % name)
    comps = src.recipe(target)
    if comps is None:
        raise ValueError("%s：%s（可以自己摆好后点「保存手调」）" % (name, src.why_not(target)))
    pose = src.pose(comps, strength)
    try:
        bake.disconnect(arm, {b for b, v in pose.items() if v[0].length > 1e-9})
    except RuntimeError:
        pass
    manual.apply_pose(arm, pose, bones)
    kept = name in getattr(src, "poses", {})
    return {"bones": len(pose), "kept": kept, "against": bool(src.against(target))}


def edit_save(obj, name, kind, **src_args):
    """Keep the armature's current face pose as the hand-tuned `name`."""
    _object_mode()
    arm, _src, bones = _edit_source(obj, kind, False, **src_args)
    pose = manual.read_pose(arm, bones if bones is not None else [b.name for b in arm.data.bones])
    return sources.store(arm, name, pose)


def edit_forget(obj, name):
    return sources.forget(model(obj)[0], name)


def edit_mirror(obj, name, kind, **src_args):
    """Mirror the armature's current face pose onto the other side's expression: kept under that
    name and left on the bones.  Returns the other name."""
    other = manual.mirror_name(name)
    if not other:
        raise ValueError("%s 不分左右，没有对侧" % name)
    _object_mode()
    arm, _src, bones = _edit_source(obj, kind, False, **src_args)
    bones = bones if bones is not None else [b.name for b in arm.data.bones]
    mirrored = manual.mirror_pose(arm, manual.read_pose(arm, bones))
    sources.store(arm, other, mirrored)
    manual.apply_pose(arm, mirrored, bones)
    return other


def edit_reset(obj, kind, **src_args):
    arm, _src, bones = _edit_source(obj, kind, False, **src_args)
    manual.apply_pose(arm, {}, bones if bones is not None else [b.name for b in arm.data.bones])


def kept_names(obj):
    return sorted(sources.captured(model(obj)[0]))


def clear(obj, set_key):
    arm, meshes, root = model(obj)
    engine.reset(arm, meshes, root)
    return engine.clear(meshes, root, set_key)


def made(obj):
    arm, meshes, root = model(obj)
    return {"BONE": engine.made_bone_morphs(root), recipes.MMD: engine.made_keys(meshes, recipes.MMD),
            recipes.ARKIT: engine.made_keys(meshes, recipes.ARKIT)}


def preview(obj, name, weight=1.0):
    arm, meshes, root = model(obj)
    return engine.preview(arm, meshes, root, name, weight)


def reset(obj):
    arm, meshes, root = model(obj)
    engine.reset(arm, meshes, root)


def _influences(arm, meshes):
    """Vertices per influence count, counting only groups of deforming bones (mmd_tools adds
    helper groups such as mmd_edge_scale / mmd_vertex_order to its meshes)."""
    deform = {b.name for b in arm.data.bones if b.use_deform}
    counts = collections.Counter()
    for m in meshes:
        bone_groups = {g.index for g in m.vertex_groups if g.name in deform}
        for v in m.data.vertices:
            counts[sum(1 for g in v.groups if g.weight > 0.0 and g.group in bone_groups)] += 1
    return counts


def analyze(obj, dna_path="", action=None, use_markers=True, ff7_data=""):
    """What the model has and which sources / outputs will work on it."""
    arm, meshes, root = model(obj)
    info = {"armature": arm.name, "meshes": len(meshes), "mmd_model": root is not None,
            "bone_morphs": len(root.mmd_root.bone_morphs) if root else 0}
    keyed = [m for m in meshes if m.data.shape_keys and len(m.data.shape_keys.key_blocks) > 1]
    arkit = {}
    for m in keyed:
        arkit.update(names.match_arkit([k.name for k in m.data.shape_keys.key_blocks[1:]]))
    info["arkit_keys"] = len(arkit)
    all_keys = {k.name for m in keyed for k in m.data.shape_keys.key_blocks[1:]}
    mmd_names = {t["name"] for t in recipes.MMD_TARGETS}
    info["mmd_vertex_morphs"] = len(all_keys & mmd_names)
    info["mmd_bone_morphs"] = len(set(mmd.bone_morph_names(root)) & mmd_names) if root else 0
    info["made"] = {k: len(v) for k, v in made(obj).items()}
    info["kept"] = sorted(sources.captured(arm))
    src = {}
    facial = [b.name for b in arm.data.bones if b.name.upper().startswith("FACIAL_")]
    src["facial_bones"] = len(facial)
    if dna_path:
        try:
            src["DNA"] = sources.DnaSource(arm, dna_path).info()
        except (ValueError, OSError, KeyError) as exc:
            src["DNA"] = {"error": str(exc)}
    src["METAHUMAN"] = sources.metahuman_face(arm)
    src["FF7"] = {"rig": ff7.is_ff7_face(arm),
                  "data": os.path.basename(ff7.find_face_data(arm, meshes, ff7_data)) if ff7.is_ff7_face(arm) else ""}
    face_meshes = bake.skinned_meshes(arm, meshes, facial) if facial else []
    if not face_meshes and src["FF7"]["rig"]:
        face_meshes = bake.skinned_meshes(arm, meshes, [b.name for b in arm.data.bones[ff7.FACE_ROOT].children])
    role = sources.RoleSource(arm)
    src["ROLES"] = role.info()
    src["ROLES"]["usable"] = sources.roles_usable(role.face)
    if not face_meshes and role.face.calibrated:
        face_meshes = bake.skinned_meshes(arm, meshes, role.face_bones())
    poses = sources.captured(arm)
    poses.update(sources.action_poses(action, use_markers))
    src["POSES"] = len(poses)
    info["sources"] = src
    info["auto"] = sources.auto_kind(arm, meshes, dna_path, ff7_data)
    counts = _influences(arm, face_meshes)
    total = sum(counts.values()) or 1
    over4 = sum(n for k, n in counts.items() if k > 4)
    info["face_meshes"] = [m.name for m in face_meshes]
    info["max_influences"] = max(counts) if counts else 0
    info["over4_vertices"] = over4
    # UE Viewer's cut: most vertices at exactly 4, almost none above
    info["four_capped"] = len(facial) > 100 and counts.get(4, 0) / total > 0.5 and over4 / total < 0.05
    state, _pkg = faceit.faceit_state()
    info["faceit"] = state
    if state == "enabled" and keyed:
        info["faceit_registered"], info["faceit_targets"] = faceit.is_registered(bpy.context.scene, keyed)
    info["stashed"] = mmd.stashed(meshes)
    return info


def check(obj):
    """[(level, message)] - what would go wrong in MMD / Faceit with the model as it is."""
    arm, meshes, root = model(obj)
    issues = []
    keyed = [m for m in meshes if m.data.shape_keys and len(m.data.shape_keys.key_blocks) > 1]
    keys = {k.name for m in keyed for k in m.data.shape_keys.key_blocks[1:]}
    if root is not None:
        both = sorted(keys & set(mmd.bone_morph_names(root)))
        if both:
            issues.append(("ERROR", "同名的骨骼表情和顶点表情（MMD 会叠加两次）：%s" % " ".join(both[:8])))
        arkit = sorted(set(names.match_arkit(sorted(keys)).values()))
        if arkit:
            issues.append(("INFO", "%d 个 ARKit 形态键会作为「其他」表情进 PMX（导出时可排除）" % len(arkit)))
        # a VMD stores a morph name in 15 bytes of Shift-JIS: longer or unencodable names are never driven
        arkit_names = set(names.ARKIT_52)
        unreachable = []
        for n in sorted(set(mmd.bone_morph_names(root)) | {v.name for v in root.mmd_root.vertex_morphs} | keys):
            if n in arkit_names:
                continue
            try:
                if len(n.encode("shift_jis")) > 15:
                    unreachable.append(n)
            except UnicodeEncodeError:
                unreachable.append(n + "（非 Shift-JIS）")
        if unreachable:
            issues.append(("WARNING", "VMD 驱动不到的表情名（超过 15 字节或不是 Shift-JIS）：%s" % " ".join(unreachable[:6])))
        posed = [b for b in mmd.morph_bones(root) if b in arm.pose.bones and
                 not arm.pose.bones[b].matrix_basis.is_identity]
        if posed:
            issues.append(("WARNING", "表情骨骼还摆着姿势（导出前先归零）：%s" % " ".join(posed[:5])))
    elif keyed:
        issues.append(("WARNING", "还没转成 MMD 模型且带形态键：一键转换的「修正前腕弯曲」会跳过带形态键的网格，"
                                  "先「转换前暂存形态键」"))
    if mmd.stashed(meshes):
        issues.append(("WARNING", "有暂存的形态键还没恢复：%s" % " ".join(mmd.stashed(meshes))))
    state, _pkg = faceit.faceit_state()
    arkit_n = len(names.match_arkit(sorted(keys)))
    if arkit_n:
        if state != "enabled":
            issues.append(("INFO", "有 %d 个 ARKit 形态键；Faceit %s" % (arkit_n, {"disabled": "未启用",
                                                                               "missing": "未安装"}.get(state, state))))
        else:
            registered, n = faceit.is_registered(bpy.context.scene, keyed)
            if not registered:
                issues.append(("INFO", "ARKit 形态键还没注册到 Faceit"))
    if not issues:
        issues.append(("INFO", "没发现问题"))
    return issues


def register_faceit(obj, head_bone="", source="FACECAP", require_enabled=True):
    arm, meshes, _root = model(obj)
    keyed = [m for m in meshes if m.data.shape_keys and names.match_arkit(
        [k.name for k in m.data.shape_keys.key_blocks[1:]])]
    if not keyed:
        raise ValueError("模型上没有 ARKit 形态键：先生成")
    return faceit.register(bpy.context.scene, arm, keyed, head_bone=head_bone, source=source,
                           require_enabled=require_enabled)


def _rename_map(arm, game_bones):
    """Game bone -> rig bone for bones a conversion renamed (unused_ prefix, XPS / MMD eye and jaw names)."""
    bones = arm.data.bones
    lower = {b.name.lower(): b.name for b in bones}
    mmd_j = {}
    for pb in arm.pose.bones:
        mmd_bone = getattr(pb, "mmd_bone", None)
        if mmd_bone is not None and mmd_bone.name_j:
            mmd_j.setdefault(mmd_bone.name_j, pb.name)
    out = {}
    for b in game_bones:
        if b in bones:
            continue
        for cand in (b, "unused_" + b) + sources.RENAMED.get(b, ()):
            hit = cand if cand in bones else lower.get(cand.lower()) or mmd_j.get(cand)
            if hit:
                out[b] = hit
                break
    return out


def restore_weights(obj, package_path, dna_path="", force=False):
    """Put a cooked UE5 face package's full skin weights back (UE Viewer keeps 4 per vertex).
    Works on the UE-named rig and on a converted one (renamed eye / jaw bones are mapped)."""
    arm, meshes, _root = model(obj)
    with open(bpy.path.abspath(package_path), "rb") as fh:
        pkg = ue_weights.read_package(fh.read())
    rename = _rename_map(arm, pkg["bones"])
    facial = [b.name for b in arm.data.bones if b.name.upper().startswith("FACIAL_")]
    targets_ = bake.skinned_meshes(arm, meshes, facial) if facial else meshes
    reports = {}
    for m in targets_:
        try:
            reports[m.name] = ue_weights.restore(m, pkg, force=force, rename=rename)
        except ValueError as exc:                # a mesh of another package (hair, body)
            reports[m.name] = {"skipped": str(exc)}
    return {"package": {k: pkg[k] for k in ("sections", "max_influences", "total_influences", "per_vertex")},
            "meshes": reports, "renamed": len(rename)}


def companion_package(dna_path):
    """<name>.uasset.bin next to <name>.dna, if the extractor wrote one."""
    if not dna_path:
        return ""
    stem = os.path.splitext(bpy.path.abspath(dna_path))[0]
    for cand in (stem + ".uasset.bin", stem + ".bin"):
        if os.path.isfile(cand):
            return cand
    return ""


def stash(obj):
    _arm, meshes, _root = model(obj)
    return mmd.stash(meshes)


def restore(obj):
    """Put parked keys back; on an mmd_tools model (i.e. after the conversion) also list this
    add-on's expressions in its morph panels: MMD names with their panel, ARKit keys after them."""
    _arm, meshes, root = model(obj)
    report = mmd.restore(meshes)
    if root is not None and any(isinstance(r, dict) for r in report.values()):
        info = {t["name"]: (t["name_e"], t["category"]) for t in recipes.MMD_TARGETS}
        mine = engine.made_keys(meshes, recipes.MMD)
        with mmd.SliderState(root):
            mmd.register_vertex_morphs(root, [(n,) + info.get(n, (n, "OTHER")) for n in mine])
            arkit = engine.made_keys(meshes, recipes.ARKIT)
            have = {v.name for v in root.mmd_root.vertex_morphs}
            mmd.register_vertex_morphs(root, [(n, n, "OTHER") for n in arkit if n not in have], at_top=False)
            try:
                mmd.refresh_facial_frame(root)
            except Exception:
                pass
        report["_registered"] = len(mine)
    return report


def export_pmx(obj, path, include_arkit=False, scale=12.5, copy_textures=True):
    """mmd_tools PMX export; the ARKit keys stay out of the PMX unless include_arkit."""
    arm, meshes, root = model(obj)
    if root is None:
        raise ValueError("不是 mmd_tools 模型：先完成转换")
    engine.reset(arm, meshes, root)
    arkit_keys = []
    for m in meshes:
        if m.data.shape_keys:
            arkit_keys += names.match_arkit([k.name for k in m.data.shape_keys.key_blocks[1:]]).values()
    arkit_keys = list(dict.fromkeys(arkit_keys))
    park = set()
    if not include_arkit:
        park.update(arkit_keys)
    else:       # unlisted keys would head the PMX morph list: list them after the MMD morphs
        have = {v.name for v in root.mmd_root.vertex_morphs}
        mmd.register_vertex_morphs(root, [(k, k, "OTHER") for k in arkit_keys if k not in have], at_top=False)
    os.makedirs(os.path.dirname(bpy.path.abspath(path)) or ".", exist_ok=True)
    return mmd.export_pmx(root, path, scale=scale, copy_textures=copy_textures, park=park)


def capture(obj, name, selected_only=False):
    arm, _meshes, _root = model(obj)
    return sources.capture(arm, name, selected_only)


def forget(obj, name):
    arm, _meshes, _root = model(obj)
    return sources.forget(arm, name)


def captured_names(obj):
    arm, _meshes, _root = model(obj)
    return sorted(sources.captured(arm))


def save_recipes(set_key, path):
    recipes.save_json(set_key, bpy.path.abspath(path))
    return bpy.path.abspath(path)
