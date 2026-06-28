"""One-click XPS→MMD conversion: auto-identify + the whole bone pipeline.

Stage order (the why of each is in its module):
  PRE-convert : correct → rename → transfer-unused → forearm-straighten (1.5) →
                complete → transfer-unused(2) → IK → bone-group → mmd_tools convert
                (arm/finger A-pose alignment removed — arms keep source XPS pose)
  VG cleanup  : merge any stranded old-name vertex groups
  POST-convert: leg-D → twist → palm-fix → shoulder-P   (geometry only, finalize=False)
  finalize    : set every 付与 grant once, then apply_additional_transform once
                → mmd_tools builds all relays (direct or _dummy_/_shadow_) uniformly.
"""

import bpy
import time

from . import grants


# (step, operator_id, label, critical)
PIPELINE_PRE_D = [
    ("0.5", "object.correct_bones", "归正骨架位置", False),
    ("1", "object.rename_to_mmd", "重命名为 MMD", True),
    ("1.4", "object.transfer_unused_weights", "转移 unused 权重 (第一次)", False),
    # 1.45 MUST precede the rest-pose bake (1.5). transfer_unused (1.4) writes twist weight into
    # XPS-named groups (e.g. "arm left wrist") that have NO matching bone after rename — so a
    # rest-pose bake can't move those verts and the wrist tears (the 错位). Merging the stranded
    # old-name groups into their MMD bone groups FIRST gives the bake a single, bone-backed wrist
    # group, so the hand follows the rotation cleanly. (Also runs post-convert as a safety net for
    # groups re-stranded by transfer #2.)
    ("1.45", "_vg_cleanup_early", "VG 残留清理 (烘焙前)", False),
    # 1.5 straightens the forearm collinear with the upper arm (skipped if already collinear).
    # NOTE: the A-pose alignment steps (former 1.6 align_arms_to_canonical / 1.7 align_fingers)
    # were REMOVED — the arms/fingers now keep their source XPS pose; only the forearm bend (1.5)
    # and the arm roll frames (align_arm_rolls in complete) are normalized. The operators still
    # exist in align.py as optional manual tools, just no longer in the auto pipeline.
    ("1.5", "object.fix_forearm_bend", "修正前腕弯曲", False),
    ("2", "object.complete_missing_bones", "补全缺失骨骼", True),
    ("2.5", "object.transfer_unused_weights", "清理控制骨权重 (第二次)", False),
    ("3", "object.add_mmd_ik", "添加 MMD IK", True),
    ("4", "object.create_bone_group", "创建骨骼集合", True),
    ("5", "object.use_mmd_tools_convert", "mmd_tools 转换", True),
]

# POST-convert geometry builders — finalize deferred to the single finalize below.
PIPELINE_POST_D = [
    ("6", "object.add_leg_d_bones", "添加腿部 D 骨", {"finalize": False}, False),
    ("7", "object.add_twist_bone", "添加捩骨", {"finalize": False}, False),
    ("7.5", "object.fix_palm_weights", "手部权重修正(拇指+掌骨)", {}, False),
    ("8", "object.add_shoulder_p_bones", "添加肩P骨", {"finalize": False}, False),
]


def _find_armature():
    for o in bpy.data.objects:
        if o.type == 'ARMATURE' and 'backup' not in o.name.lower():
            return o
    return None


def _call(op_id, **kwargs):
    parts = op_id.split('.')
    return getattr(getattr(bpy.ops, parts[0]), parts[1])(**kwargs)


class OBJECT_OT_one_click_convert(bpy.types.Operator):
    """一键完成 XPS→MMD 全流程转换（自动识别 + 全部步骤）"""
    bl_idname = "object.one_click_convert"
    bl_label = "一键转换 XPS→MMD"
    bl_description = "自动识别骨架并依次执行重命名/修正/补全/IK/集合/mmd_tools转换/次标准骨"
    bl_options = {'REGISTER', 'UNDO'}

    auto_identify: bpy.props.BoolProperty(name="自动识别骨架", default=True)  # type: ignore

    def execute(self, context):
        t_start = time.time()
        obj = context.active_object
        if not obj or obj.type != 'ARMATURE':
            self.report({'ERROR'}, "请先选中骨架")
            return {'CANCELLED'}

        results = []

        if self.auto_identify:
            try:
                bpy.ops.object.auto_identify_skeleton()
                results.append(("0", "自动识别骨架", "OK"))
            except Exception as e:
                results.append(("0", "自动识别骨架", f"WARN: {e}"))

        # XPS→MMD name map BEFORE rename (for post-convert VG cleanup)
        from ..bone_map_and_group import mmd_bone_map
        scene = context.scene
        xps_to_mmd_map = {}
        for prop_name, mmd_name in mmd_bone_map.items():
            xps_name = getattr(scene, prop_name, None)
            if xps_name and xps_name != mmd_name:
                xps_to_mmd_map[xps_name] = mmd_name

        for step_num, op_id, label, critical in PIPELINE_PRE_D:
            arm = obj if (obj and obj.name in bpy.data.objects) else _find_armature()
            if arm:
                context.view_layer.objects.active = arm
                arm.select_set(True)
            try:
                t = time.time()
                # 1.45 is not an operator: merge stranded XPS-name VGs into MMD groups before the
                # alignment bakes, so the wrist/elbow have a single bone-backed group and the bake
                # deforms cleanly instead of tearing (see PIPELINE_PRE_D note on 1.45).
                if op_id == "_vg_cleanup_early":
                    self._vg_cleanup(obj, xps_to_mmd_map, results, step="1.45")
                    continue
                result = _call(op_id)
                status = "OK" if result == {'FINISHED'} else str(result)
                results.append((step_num, label, f"{status} ({time.time() - t:.1f}s)"))
            except Exception as e:
                results.append((step_num, label, f"FAIL: {e}"))
                if critical:
                    self._print_summary(results, time.time() - t_start)
                    self.report({'ERROR'}, f"Step {step_num} {label} 失败: {e}")
                    return {'CANCELLED'}

        # post-convert safety net: clean any groups re-stranded by transfer #2 / complete.
        self._vg_cleanup(obj, xps_to_mmd_map, results)

        for step_num, op_id, label, kwargs, critical in PIPELINE_POST_D:
            arm = obj if (obj and obj.name in bpy.data.objects) else _find_armature()
            if arm:
                context.view_layer.objects.active = arm
                arm.select_set(True)
            try:
                t = time.time()
                result = _call(op_id, **kwargs)
                status = "OK" if result == {'FINISHED'} else str(result)
                results.append((step_num, label, f"{status} ({time.time() - t:.1f}s)"))
            except Exception as e:
                results.append((step_num, label, f"FAIL: {e}"))

        # Single finalize: set every 付与 grant, then build all relays once.
        arm = obj if (obj and obj.name in bpy.data.objects) else _find_armature()
        if arm:
            if context.mode != 'OBJECT':
                bpy.ops.object.mode_set(mode='OBJECT')
            try:
                ng = grants.apply_grants(arm)
                results.append(("8.4", f"设置标准付与 ({ng})", "OK"))
            except Exception as e:
                results.append(("8.4", "设置标准付与", f"WARN: {e}"))
            try:
                bpy.ops.mmd_tools.apply_additional_transform()
                results.append(("8.5", "apply_additional_transform", "OK"))
            except Exception as e:
                results.append(("8.5", "apply_additional_transform", f"WARN: {e}"))

        arm = obj if (obj and obj.name in bpy.data.objects) else _find_armature()
        if arm:
            self._arm_selfcheck(arm)

        total = time.time() - t_start
        self._print_summary(results, total)
        ok = sum(1 for _, _, s in results if s.startswith("OK"))
        self.report({'INFO'}, f"一键转换完成: {ok}/{len(results)} 步成功 ({total:.1f}s)")
        return {'FINISHED'}

    def _vg_cleanup(self, obj, xps_to_mmd_map, results, step="5.5"):
        """Merge stranded old-name vertex groups into their MMD-renamed counterpart."""
        arm = obj if (obj and obj.name in bpy.data.objects) else _find_armature()
        if not arm or not xps_to_mmd_map:
            return
        from .weights.common import skinned_meshes
        merged = 0
        for mesh in skinned_meshes(arm):
            for old_name, new_name in xps_to_mmd_map.items():
                old_vg = mesh.vertex_groups.get(old_name)
                if not old_vg:
                    continue
                new_vg = mesh.vertex_groups.get(new_name)
                if not new_vg:
                    old_vg.name = new_name
                    merged += 1
                    continue
                for v in mesh.data.vertices:
                    for g in v.groups:
                        if g.group == old_vg.index and g.weight > 0.001:
                            new_vg.add([v.index], g.weight, 'ADD')
                            break
                mesh.vertex_groups.remove(old_vg)
                merged += 1
        if merged:
            results.append((step, f"VG 残留清理 ({merged})", "OK"))

    def _arm_selfcheck(self, arm):
        """Post-convert self-check: report arm bone down-angles. align_arms (step 1.6) bakes
        上臂(腕)/前腕(ひじ)/手首 DIRECTION to the target MMD A-pose, so we assert 腕/ひじ land near
        target (~37°/39° down). 手首 is also aligned, but after the twist-bone split (step 7) its
        tail points at the 手捩 bone, so its world down-angle no longer equals the bake target —
        it is only reported here; the real wrist check is the FK world-direction diff in the VMD
        test. Target down-angles are read from canonical_arm_dirs."""
        import math
        from .align import _load_canonical_arm_dirs
        canon = _load_canonical_arm_dirs() or {}
        def _down(v):
            v = v.normalized()
            return math.degrees(math.atan2(-v.z, math.sqrt(v.x * v.x + v.y * v.y)))
        # target down-angle per bone from canonical upper/fore dirs
        tgt = {}
        for side, jp in (("L", "左"), ("R", "右")):
            if side in canon:
                up, fore, _w = canon[side]
                tgt[f"{jp}腕"] = _down(up)
                tgt[f"{jp}ひじ"] = _down(fore)
        bones = ["左腕", "左ひじ", "左手首", "右腕", "右ひじ", "右手首"]
        def ang_dn(b):
            v = arm.matrix_world.to_3x3() @ (b.tail_local - b.head_local)
            if v.length < 1e-9:
                return None
            return _down(v)
        # Arms are no longer aligned to the target A-pose (1.6/1.7 removed) — they keep their
        # source XPS pose. So this is report-only now: print each bone's down-angle and, for
        # reference, how far it sits from the (no-longer-enforced) canonical target. No PASS/FAIL.
        print("\n[自检] 手臂向下角(已不再对齐目标, 保留源 A-pose; 仅供观察偏差):")
        for name in bones:
            b = arm.data.bones.get(name)
            if not b:
                continue
            a = ang_dn(b)
            if a is None:
                continue
            if name in tgt:
                print(f"   · {name}: {a:5.1f}° (参考目标 {tgt[name]:.1f}°, 偏差 {a - tgt[name]:+.1f}°)")
            else:
                print(f"   · {name}: {a:5.1f}° (源 A-pose)")
        print("   >>> 手臂保留源姿态(未对齐目标)，以上偏差仅供参考")

    def _print_summary(self, results, total):
        from ..ui import BUILD_STAMP
        print("\n" + "=" * 60)
        print(f"[Convert_to_MMD] 一键转换结果  ({BUILD_STAMP})")
        print("=" * 60)
        for step, label, status in results:
            mark = "✓" if status.startswith("OK") else ("⚠" if "WARN" in status else "✗")
            print(f"  {mark} Step {step:<5} {label:<28} {status}")
        print(f"\n  总耗时: {total:.1f}s")
        print("=" * 60)
