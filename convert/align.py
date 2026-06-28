"""Steps 1.5–1.7 — rest-pose alignment fixes (no reference PMX, only bundled JSON).

  fix_forearm_bend          straighten the forearm collinear with the upper arm
  align_arms_to_canonical   align upper-arm / forearm / wrist direction to MMD canonical
                            (canonical measured from a real reference PMX rest pose)
  align_fingers_to_canonical align finger root direction to canonical

Each computes pose-space rotations and bakes them into a new rest pose (the mesh
follows via a duplicated armature modifier). Ported from the XPS-fixes module.
"""

import bpy
import json
import math
import os
from mathutils import Matrix, Vector

from ..bone_utils import apply_armature_transforms


_CANON_ARM_CACHE = None
_CANON_FINGER_CACHE = None


def _presets_path(name):
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(here, "presets", name)


def _load_canonical_arm_dirs():
    global _CANON_ARM_CACHE
    if _CANON_ARM_CACHE is not None:
        return _CANON_ARM_CACHE
    try:
        with open(_presets_path("canonical_arm_dirs.json"), "r", encoding="utf-8") as f:
            data = json.load(f)
        result = {}
        for side in ("L", "R"):
            arm = data["arms"][side]
            wrist = arm.get("wrist_dir")
            wrist_vec = Vector(wrist) if wrist else None
            result[side] = (
                Vector(arm["upper_dir"]).normalized(),
                Vector(arm["fore_dir"]).normalized(),
                wrist_vec.normalized() if (wrist_vec and wrist_vec.length > 1e-9) else None,
            )
        _CANON_ARM_CACHE = result
        return result
    except Exception as e:
        print(f"[align canonical-arm] 读取失败: {e}")
        return None


def _load_canonical_arm_rolls():
    """Return {mmd_bone_name: target local-Z Vector} for 腕/ひじ/手首 both sides, or {}.

    Used to set the arm bones' ROLL: a scalar bone.roll is relative to Blender's
    auto-roll basis, so a fixed value gives an inconsistent world frame across bones
    pointing different ways (it left 手首 ~26° twisted). align_roll(this_Z) fixes that.
    """
    try:
        with open(_presets_path("canonical_arm_dirs.json"), "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        print(f"[align arm-roll] 读取失败: {e}")
        return {}
    side_jp = {"L": "左", "R": "右"}
    key_suffix = {"upper_roll_z": "腕", "fore_roll_z": "ひじ", "wrist_roll_z": "手首"}
    out = {}
    for side, jp in side_jp.items():
        arm = data.get("arms", {}).get(side, {})
        for key, suffix in key_suffix.items():
            v = arm.get(key)
            if v:
                vec = Vector(v)
                if vec.length > 1e-9:
                    out[f"{jp}{suffix}"] = vec.normalized()
    return out


def align_arm_rolls(edit_bones):
    """In edit mode, set 腕/ひじ/手首 roll so their local Z matches the canonical (target)
    Z. Returns the number of bones aligned. Call AFTER bone directions/tails are final."""
    rolls = _load_canonical_arm_rolls()
    n = 0
    for bone_name, z in rolls.items():
        eb = edit_bones.get(bone_name)
        if eb:
            eb.align_roll(z)
            n += 1
    return n


def _load_canonical_finger_dirs():
    global _CANON_FINGER_CACHE
    if _CANON_FINGER_CACHE is not None:
        return _CANON_FINGER_CACHE
    try:
        with open(_presets_path("canonical_finger_dirs.json"), "r", encoding="utf-8") as f:
            data = json.load(f)
        result = {side: {name: Vector(v).normalized() for name, v in data["fingers"][side].items()}
                  for side in ("L", "R")}
        _CANON_FINGER_CACHE = result
        return result
    except Exception as e:
        print(f"[align canonical-finger] 读取失败: {e}")
        return None


def _find_arm_chain(obj, side):
    """Return (upper_arm, elbow, wrist) names across rig conventions, or None."""
    xps_side = "left" if side == "L" else "right"
    lr = "l" if side == "L" else "r"
    jp = "左" if side == "L" else "右"
    candidates = [
        (f"{jp}腕", f"{jp}ひじ", f"{jp}手首"),
        (f"arm {xps_side} shoulder 2", f"arm {xps_side} elbow", f"arm {xps_side} wrist"),
        (f"{lr}ShldrBend", f"{lr}ForearmBend", f"{lr}Hand"),
        (f"mixamorig:{xps_side.capitalize()}Arm", f"mixamorig:{xps_side.capitalize()}ForeArm", f"mixamorig:{xps_side.capitalize()}Hand"),
        (f"Upper Arm.{side}", f"Lower Arm.{side}", f"Hand.{side}"),
        (f"UpperArm_{side}", f"LowerArm_{side}", f"Hand_{side}"),
        (f"腕.{side}", f"ひじ.{side}", f"手首.{side}"),
    ]
    bones = obj.data.bones
    for u, e, w in candidates:
        if u in bones and e in bones and w in bones:
            return u, e, w
    return None


def _bake_pose_delta_to_rest(context, obj, plans, log_tag):
    """Apply (bone, pivot_world, axis_world, angle) rotations in pose mode, bake as rest."""
    if not plans:
        return 'FINISHED'

    meshes_with_arm = []
    for m in bpy.data.objects:
        if m.type != 'MESH' or m.data.shape_keys:
            continue
        if any(mod.type == 'ARMATURE' and mod.object == obj for mod in m.modifiers):
            meshes_with_arm.append(m)

    created_temp = False
    if not meshes_with_arm:
        try:
            bpy.ops.mesh.primitive_cube_add(size=0.5)
            tmp = context.active_object
            tmp.name = "XPS_FIXES_TEMP_MESH"
            mod = tmp.modifiers.new(name="Armature", type='ARMATURE')
            mod.object = obj
            tmp["is_temp_mesh"] = True
            meshes_with_arm.append(tmp)
            created_temp = True
        except Exception as e:
            print(f"[{log_tag}] 创建临时网格失败: {e}")
            return 'CANCELLED'

    for m in meshes_with_arm:
        for mod in list(m.modifiers):
            if mod.type == 'ARMATURE' and mod.object == obj and "_copy" not in mod.name:
                new_mod = m.modifiers.new(name=mod.name + "_copy", type='ARMATURE')
                new_mod.object = mod.object
                new_mod.use_vertex_groups = mod.use_vertex_groups
                new_mod.use_bone_envelopes = mod.use_bone_envelopes
                # Apply must happen on the FIRST modifier, otherwise Blender warns
                # "modifier was not first, result may not be as expected" and the baked
                # geometry can be corrupted at the joints (the cause of forearm 错位).
                # Move the copy to the top so it bakes cleanly off the base mesh.
                context.view_layer.objects.active = m
                try:
                    bpy.ops.object.modifier_move_to_index(modifier=new_mod.name, index=0)
                except Exception as e:
                    print(f"[{log_tag}] move modifier to first 失败: {e}")
                break

    context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode='POSE')
    bpy.ops.pose.select_all(action='SELECT')
    bpy.ops.pose.rot_clear()
    bpy.ops.pose.scale_clear()
    bpy.ops.pose.loc_clear()
    bpy.ops.pose.select_all(action='DESELECT')

    for bone_name, pivot, axis, angle in plans:
        pb = obj.pose.bones[bone_name]
        rot_w = Matrix.Rotation(angle, 4, axis)
        delta = Matrix.Translation(pivot) @ rot_w @ Matrix.Translation(-pivot)
        pb.matrix = delta @ pb.matrix
        context.view_layer.update()

    try:
        for m in meshes_with_arm:
            context.view_layer.objects.active = m
            for mod in list(m.modifiers):
                if mod.type == 'ARMATURE' and mod.object == obj and "_copy" in mod.name:
                    bpy.ops.object.modifier_apply(modifier=mod.name)
                    break
    except RuntimeError as e:
        for m in meshes_with_arm:
            for mod in list(m.modifiers):
                if "_copy" in mod.name:
                    m.modifiers.remove(mod)
        print(f"[{log_tag}] 应用 modifier 失败: {e}")
        return 'CANCELLED'

    context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode='POSE')
    bpy.ops.pose.select_all(action='SELECT')
    bpy.ops.pose.armature_apply()
    bpy.ops.object.mode_set(mode='OBJECT')

    if created_temp:
        for m in meshes_with_arm:
            if m.get("is_temp_mesh"):
                bpy.data.objects.remove(m, do_unlink=True)
    return 'FINISHED'


def _smooth_group_weights(context, obj, groups, factor=0.5, repeat=8):
    """Relax the given vertex groups' weights BEFORE a rest-pose rotation bake.

    A bone that rotates during the bake shears the skin where its weight blends into the
    neighbour bone: a faint crease for the small upper-arm rotation (~8°, 肩↔腕 deltoid), a hard
    lump for the large finger-root rotations (~30°, dragging 手首-blended palm verts into the
    dorsal wrist — the 小手臂和手交界处 瑕疵). Relaxing the involved groups first spreads the
    deformation so the boundary deforms smoothly. Weight-only and scoped to `groups`, so nothing
    outside the named bones is touched. Returns the number of (mesh, group) smooths performed.
    """
    from .weights.common import skinned_meshes
    n = 0
    try:
        for mesh in skinned_meshes(obj):
            names = [g for g in groups if mesh.vertex_groups.get(g)]
            if not names:
                continue
            bpy.ops.object.mode_set(mode='OBJECT')
            bpy.ops.object.select_all(action='DESELECT')
            context.view_layer.objects.active = mesh
            mesh.select_set(True)
            bpy.ops.object.mode_set(mode='WEIGHT_PAINT')
            for gn in names:
                mesh.vertex_groups.active = mesh.vertex_groups[gn]
                bpy.ops.object.vertex_group_smooth(group_select_mode='ACTIVE', factor=factor,
                                                   repeat=repeat, expand=0.0)
                n += 1
            bpy.ops.object.mode_set(mode='OBJECT')
    finally:
        # restore: armature active in OBJECT mode for the subsequent bake
        bpy.ops.object.mode_set(mode='OBJECT')
        bpy.ops.object.select_all(action='DESELECT')
        context.view_layer.objects.active = obj
        obj.select_set(True)
    return n


# 肩/腕: relaxed before the upper-arm bake so the ~8° rotation doesn't crease the deltoid.
_SHOULDER_SMOOTH_GROUPS = ["左肩", "右肩", "左腕", "右腕"]
# 手首 + the five finger roots (both sides): relaxed before the finger bake so its large
# (~30° thumb) root rotations don't drag 手首-blended wrist verts into a lump. This only
# protects the *bake geometry*; the final hand weights are still set later by palm-fix (7.5).
_HAND_SMOOTH_GROUPS = [
    "左手首", "右手首",
    "左親指０", "右親指０", "左人指１", "右人指１", "左中指１", "右中指１",
    "左薬指１", "右薬指１", "左小指１", "右小指１",
]


_FINGER_CHAINS = [
    ("親指０", "親指１", "親指２"), ("人指１", "人指２", "人指３"),
    ("中指１", "中指２", "中指３"), ("薬指１", "薬指２", "薬指３"),
    ("小指１", "小指２", "小指３"),
]


class OBJECT_OT_fix_forearm_bend(bpy.types.Operator):
    """L1 修正：把小手臂拉直到与上臂共线，然后烘焙到 rest pose。"""
    bl_idname = "object.fix_forearm_bend"
    bl_label = "L1: 修正前腕弯曲"
    bl_description = "把小手臂拉直到与上臂共线，烘焙为新 rest pose"
    ANGLE_THRESHOLD_DEG = 2.0

    def execute(self, context):
        obj = context.active_object
        if not obj or obj.type != 'ARMATURE':
            self.report({'ERROR'}, "请先选中骨架")
            return {'CANCELLED'}
        if not apply_armature_transforms(context):
            self.report({'ERROR'}, "apply_armature_transforms 失败")
            return {'CANCELLED'}
        bpy.ops.object.mode_set(mode='OBJECT')

        plans = []
        for side in ("L", "R"):
            chain = _find_arm_chain(obj, side)
            if not chain:
                continue
            u_name, e_name, w_name = chain
            u_head = obj.matrix_world @ obj.pose.bones[u_name].head
            e_head = obj.matrix_world @ obj.pose.bones[e_name].head
            w_head = obj.matrix_world @ obj.pose.bones[w_name].head
            upper_dir = (e_head - u_head).normalized()
            fore_dir = (w_head - e_head).normalized()
            if upper_dir.length == 0 or fore_dir.length == 0:
                continue
            angle = upper_dir.angle(fore_dir)
            if angle < math.radians(self.ANGLE_THRESHOLD_DEG):
                print(f"[align fix-forearm] {side}: 已共线 ({math.degrees(angle):.2f}°)，跳过")
                continue
            axis = fore_dir.cross(upper_dir)
            if axis.length < 1e-6:
                continue
            axis.normalize()
            plans.append((e_name, e_head.copy(), axis, angle))
            print(f"[align fix-forearm] {side}: {e_name} 旋转 {math.degrees(angle):.2f}° 拉直")

        if not plans:
            self.report({'INFO'}, "前腕已接近直线，无需修正")
            return {'FINISHED'}
        if _bake_pose_delta_to_rest(context, obj, plans, "align fix-forearm") != 'FINISHED':
            self.report({'ERROR'}, "烘焙到 rest pose 失败")
            return {'CANCELLED'}
        self.report({'INFO'}, f"前腕弯曲修正完成 ({len(plans)} 处)")
        return {'FINISHED'}


class OBJECT_OT_straighten_arms(bpy.types.Operator):
    """拉直手臂：把 上臂→前腕(→手) 旋转到共线（完全伸直），网格跟随烘焙为新 rest pose。

    从 Convert_to_MMD6 迁移。与 fix_forearm_bend 的区别：那个只拉直肘部(前腕→上臂)；
    本操作可同时拉直腕部(手→前腕)，把整条手臂拉成一条直线。改用本仓库统一的
    _find_arm_chain + _bake_pose_delta_to_rest（处理形态键 / 网格跟随的烘焙），
    而不是 MMD6 自带的那套 shape-key 备份逻辑。

    注意区分用途：align_arms_to_canonical 是把手臂对齐到目标 MMD A-pose（带下垂），
    用于一键转换流水线；本操作是把手臂拉成 *直线*，是手动工具，不进流水线。
    """
    bl_idname = "object.straighten_arms"
    bl_label = "拉直手臂(肘+腕)"
    bl_description = ("检测大臂→小臂(肘)与小臂→手(腕)两个环节，夹角超过阈值就拉直成共线，"
                     "网格跟随烘焙为新 rest pose。需先映射/重命名手臂骨骼")
    bl_options = {'REGISTER', 'UNDO'}

    angle_threshold: bpy.props.FloatProperty(  # type: ignore
        name="阈值(度)", description="夹角大于此值才修正", default=0.5, min=0.0, max=45.0)
    fix_wrist: bpy.props.BoolProperty(  # type: ignore
        name="同时拉直手腕", description="除肘部外，也把小臂→手拉直", default=True)

    def execute(self, context):
        obj = context.active_object
        if not obj or obj.type != 'ARMATURE':
            self.report({'ERROR'}, "请先选中骨架")
            return {'CANCELLED'}
        if not apply_armature_transforms(context):
            self.report({'ERROR'}, "apply_armature_transforms 失败")
            return {'CANCELLED'}
        bpy.ops.object.mode_set(mode='OBJECT')

        thr = math.radians(self.angle_threshold)
        plans = []          # (bone, pivot_world, axis_world, angle)，肘在前、腕在后
        report = []
        for side in ("L", "R"):
            chain = _find_arm_chain(obj, side)
            if not chain:
                continue
            u, e, w = chain
            bones = obj.data.bones
            u_head = bones[u].head_local
            e_head = bones[e].head_local
            w_head = bones[w].head_local
            upper_dir = (e_head - u_head)
            fore_dir = (w_head - e_head)
            if upper_dir.length < 1e-9 or fore_dir.length < 1e-9:
                continue
            upper_dir = upper_dir.normalized()
            fore_dir = fore_dir.normalized()

            # 1) 肘：把前腕(ひじ)方向旋到与上臂共线，绕肘关节(前腕头)旋转。
            R_e = None
            ang_e = fore_dir.angle(upper_dir)
            report.append(f"{side}肘 {math.degrees(ang_e):.2f}°")
            if ang_e >= thr:
                axis = fore_dir.cross(upper_dir)
                if axis.length >= 1e-6:
                    axis.normalize()
                    plans.append((e, e_head.copy(), axis, ang_e))
                    R_e = Matrix.Rotation(ang_e, 3, axis)
                    print(f"[align straighten] {side}: 肘 {e} 旋转 {math.degrees(ang_e):.2f}° 拉直")

            # 2) 腕：把手(手首)方向旋到与「拉直后的前腕」共线，绕腕关节(手首头)旋转。
            #    前腕若已拉直则方向 == upper_dir，否则保持原 fore_dir。手的方向用骨向量(tail-head)，
            #    并先叠加肘旋转 R_e（因为烘焙时肘先转、手会跟着动）。
            if self.fix_wrist:
                hand_dir = (bones[w].tail_local - bones[w].head_local)
                if hand_dir.length >= 1e-9:
                    fore_now = upper_dir if R_e is not None else fore_dir
                    if R_e is not None:
                        hand_dir = R_e @ hand_dir
                        w_pivot = e_head + R_e @ (w_head - e_head)
                    else:
                        w_pivot = w_head.copy()
                    hand_dir = hand_dir.normalized()
                    ang_w = hand_dir.angle(fore_now)
                    report.append(f"{side}腕 {math.degrees(ang_w):.2f}°")
                    if ang_w >= thr:
                        axis = hand_dir.cross(fore_now)
                        if axis.length >= 1e-6:
                            axis.normalize()
                            plans.append((w, w_pivot, axis, ang_w))
                            print(f"[align straighten] {side}: 腕 {w} 旋转 {math.degrees(ang_w):.2f}° 拉直")

        if not plans:
            self.report({'INFO'}, "手臂已伸直，无需修正: " + ", ".join(report))
            return {'FINISHED'}
        if _bake_pose_delta_to_rest(context, obj, plans, "align straighten") != 'FINISHED':
            self.report({'ERROR'}, "烘焙到 rest pose 失败")
            return {'CANCELLED'}
        self.report({'INFO'}, f"拉直手臂完成 ({len(plans)} 处): " + ", ".join(report))
        return {'FINISHED'}


class OBJECT_OT_align_arms_to_canonical(bpy.types.Operator):
    """L1 修正：把上臂 / 前腕 / 手首方向对齐到标准 MMD canonical（实测自参考 PMX），烘焙到 rest pose。"""
    bl_idname = "object.align_arms_to_canonical"
    bl_label = "L1: 对齐手臂到 canonical"
    bl_description = "把上臂/前腕/手首方向对齐到内置 MMD canonical（实测自参考 PMX），烘焙为新 rest pose"
    ANGLE_THRESHOLD_DEG = 0.5

    def _build_plan(self, obj, side, ref_upper_dir, ref_fore_dir, ref_wrist_dir=None):
        # Bake only 上臂(腕) and 前腕(ひじ) DIRECTIONS to the target (small ~2-8° rotations that
        # deform cleanly). The 手首 is deliberately NOT baked: a rigid ~20° hand lift about the
        # wrist shears the forearm↔hand weight boundary and tears/twists the MID-FOREARM (the
        # source forearm is smooth; the bake introduced the 撕裂). The hand keeps its source pose;
        # only its roll frame is matched to target via align_arm_rolls (roll-only, no mesh move).
        # ref_wrist_dir is accepted for signature compatibility but intentionally unused.
        plans = []
        chain = _find_arm_chain(obj, side)
        if not chain:
            return plans
        u, e, w = chain
        conv_u = obj.data.bones[u].head_local.copy()
        conv_e = obj.data.bones[e].head_local.copy()
        conv_w = obj.data.bones[w].head_local.copy()

        dir_conv_upper = (conv_e - conv_u).normalized()
        upper_angle = dir_conv_upper.angle(ref_upper_dir)
        upper_axis = None
        upper_angle_valid = upper_angle >= math.radians(self.ANGLE_THRESHOLD_DEG)
        if upper_angle_valid:
            upper_axis = dir_conv_upper.cross(ref_upper_dir)
            if upper_axis.length < 1e-6:
                upper_angle_valid = False
            else:
                upper_axis.normalize()
                plans.append((u, conv_u.copy(), upper_axis, upper_angle))
                print(f"[align align-arms] {side}: upper {u} 旋转 {math.degrees(upper_angle):.2f}°")

        if upper_angle_valid:
            R = Matrix.Rotation(upper_angle, 3, upper_axis)
            conv_e_new = conv_u + R @ (conv_e - conv_u)
            conv_w_new = conv_u + R @ (conv_w - conv_u)
        else:
            conv_e_new = conv_e
            conv_w_new = conv_w

        dir_conv_fore = (conv_w_new - conv_e_new).normalized()
        fore_angle = dir_conv_fore.angle(ref_fore_dir)
        if fore_angle >= math.radians(self.ANGLE_THRESHOLD_DEG):
            fore_axis = dir_conv_fore.cross(ref_fore_dir)
            if fore_axis.length > 1e-6:
                fore_axis.normalize()
                plans.append((e, conv_e_new.copy(), fore_axis, fore_angle))
                print(f"[align align-arms] {side}: forearm {e} 旋转 {math.degrees(fore_angle):.2f}°")
        return plans

    def execute(self, context):
        obj = context.active_object
        if not obj or obj.type != 'ARMATURE':
            self.report({'ERROR'}, "请先选中骨架")
            return {'CANCELLED'}
        if not apply_armature_transforms(context):
            self.report({'ERROR'}, "apply_armature_transforms 失败")
            return {'CANCELLED'}
        bpy.ops.object.mode_set(mode='OBJECT')

        canon = _load_canonical_arm_dirs()
        if not canon:
            self.report({'ERROR'}, "canonical_arm_dirs.json 读取失败")
            return {'CANCELLED'}

        all_plans = []
        for side in ("L", "R"):
            if side in canon:
                ref_upper, ref_fore, ref_wrist = canon[side]
                all_plans.extend(self._build_plan(obj, side, ref_upper, ref_fore, ref_wrist))

        if not all_plans:
            self.report({'INFO'}, f"已接近 canonical (<{self.ANGLE_THRESHOLD_DEG}°)，无需修正")
            return {'FINISHED'}
        # smooth 肩/腕 weights first so the upper-arm rotation deforms the deltoid cleanly (no crease)
        try:
            ns = _smooth_group_weights(context, obj, _SHOULDER_SMOOTH_GROUPS)
            if ns:
                print(f"[align align-arms] 肩部权重平滑 {ns} 组 (烘焙前, 防三角肌折痕)")
        except Exception as e:
            print(f"[align align-arms] 肩部权重平滑跳过: {e}")
        if _bake_pose_delta_to_rest(context, obj, all_plans, "align align-arms") != 'FINISHED':
            self.report({'ERROR'}, "烘焙到 rest pose 失败")
            return {'CANCELLED'}
        # NOTE: no joint-band smoothing needed here. The upper/forearm rotations are small (~2-8°)
        # and deform cleanly; the only thing that needed smoothing was the wrist-lift collar, and
        # the wrist lift is now removed (it tore the mid-forearm). Keep this surgical.
        self.report({'INFO'}, f"上臂对齐完成 ({len(all_plans)} 处)")
        return {'FINISHED'}


class OBJECT_OT_align_fingers_to_canonical(bpy.types.Operator):
    """L1 修正：把手指根段方向 (根骨 → 第1节) 对齐到 canonical (左/右 命名)。"""
    bl_idname = "object.align_fingers_to_canonical"
    bl_label = "L1: 对齐手指到 canonical"
    bl_description = "把手指根段方向对齐到内置 canonical，烘焙为新 rest pose"
    ANGLE_THRESHOLD_DEG = 1.0

    def execute(self, context):
        obj = context.active_object
        if not obj or obj.type != 'ARMATURE':
            self.report({'ERROR'}, "请先选中骨架")
            return {'CANCELLED'}
        if not apply_armature_transforms(context):
            self.report({'ERROR'}, "apply_armature_transforms 失败")
            return {'CANCELLED'}
        bpy.ops.object.mode_set(mode='OBJECT')

        canon = _load_canonical_finger_dirs()
        if not canon:
            self.report({'ERROR'}, "canonical_finger_dirs.json 读取失败")
            return {'CANCELLED'}

        plans = []
        for side in ("L", "R"):
            jp = "左" if side == "L" else "右"
            for chain in _FINGER_CHAINS:
                conv_root = obj.data.bones.get(f"{jp}{chain[0]}")
                conv_tip = obj.data.bones.get(f"{jp}{chain[1]}")
                if not conv_root or not conv_tip:
                    continue
                conv_dir = (conv_tip.head_local - conv_root.head_local)
                if conv_dir.length < 1e-6:
                    continue
                conv_dir = conv_dir.normalized()
                ref_dir = canon.get(side, {}).get(chain[0])
                if ref_dir is None:
                    continue
                angle = conv_dir.angle(ref_dir)
                if angle < math.radians(self.ANGLE_THRESHOLD_DEG):
                    continue
                axis = conv_dir.cross(ref_dir)
                if axis.length < 1e-6:
                    continue
                axis.normalize()
                plans.append((f"{jp}{chain[0]}", conv_root.head_local.copy(), axis, angle))
                print(f"[align align-fingers] {side}: {jp}{chain[0]} 旋转 {math.degrees(angle):.2f}°")

        if not plans:
            self.report({'INFO'}, "手指方向已接近 canonical，无需修正")
            return {'FINISHED'}
        # relax 手首 + finger-root weights so the (often large) finger-root rotations bake without
        # shearing the palm/wrist into a lump (the 小手臂和手交界处 瑕疵). See _smooth_group_weights.
        try:
            nh = _smooth_group_weights(context, obj, _HAND_SMOOTH_GROUPS)
            if nh:
                print(f"[align align-fingers] 手部权重平滑 {nh} 组 (烘焙前, 防手腕鼓包)")
        except Exception as e:
            print(f"[align align-fingers] 手部权重平滑跳过: {e}")
        if _bake_pose_delta_to_rest(context, obj, plans, "align align-fingers") != 'FINISHED':
            self.report({'ERROR'}, "烘焙到 rest pose 失败")
            return {'CANCELLED'}
        self.report({'INFO'}, f"手指对齐完成 ({len(plans)} 处)")
        return {'FINISHED'}
