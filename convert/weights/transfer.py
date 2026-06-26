"""REUSE path: move XPS helper/unused-bone weights onto valid MMD deform bones.

This is the *default* weight policy (req 1: "reuse XPS weights first"). It never
synthesises weight — it only re-homes weight that XPS already painted onto bones
that MMD doesn't keep (control bones, `unused *` helpers, pelvis helpers), onto
the nearest valid deform bone so the existing skin is preserved.

The single position-aware *exception* living here is the **deltoid route**: XPS
binds the shoulder cap (xtra07/xtra07pp) to the upper arm, while MMD binds it
across 肩 (top) and 腕 (lower). That is still pure reuse — the same vertices, the
same weight — just routed to two bones by position instead of one. The harder
*synthesis* splits (twist τ, palm, chain) live in their own modules.
"""

import bpy

from .common import skinned_meshes
from ...skeleton_identifier import identify_skeleton
from ...helper_classifier import classify_helpers
from ...skeleton_identifier import clear_cache


# Deltoid (shoulder-cap) routing ramp along the 腕→ひじ axis (t=0 arm head/shoulder
# joint, 1=elbow). Top of the cap (t<=LO) → 肩; lower (t>=HI) → 腕 base; linear
# between. HI=0.25 is remote-calibrated so the 肩↔腕 hand-off sits at ~t0.4 along
# 肩→ひじ, matching the target PMX (肩≈腕≈8% at t0.4, 肩→0 by t0.5). The lower part
# lands on the 腕 base (low t) and barely twists (twist TAU_LO=0.20) → no candy-wrap.
DELTOID_SH_T_LO = 0.0
DELTOID_SH_T_HI = 0.25


def deltoid_shoulder_fraction(t):
    """Fraction of a deltoid vertex (at axis param t) that goes to 肩; rest to 腕."""
    if t <= DELTOID_SH_T_LO:
        return 1.0
    if t >= DELTOID_SH_T_HI:
        return 0.0
    return (DELTOID_SH_T_HI - t) / (DELTOID_SH_T_HI - DELTOID_SH_T_LO)


def _detect_arm_deltoid(obj, meshes, candidates):
    """Identify shoulder-cap helper bones (e.g. XPS xtra07/xtra07pp) and return
    {bone_name: (肩名, 腕名, origin, axis, L2)} so transfer can split them by
    position along the 腕→ひじ axis.

    Arm bones are resolved by topology (identify_skeleton), not Japanese names:
    the first transfer pass runs *before* rename, while bones still carry XPS
    names — a hard 左腕/左ひじ lookup would miss. identify_skeleton is name-agnostic
    and routes to the shoulder bone's *current* name; when complete later renames
    that bone to 左肩, the vertex group rides along.
    """
    try:
        smap = identify_skeleton(obj.data)
    except Exception:
        smap = {}
    mw = obj.matrix_world
    sides = []  # (origin, axis, L2, armlen, shoulder_name, arm_name)
    for side, jp in (('left', '左'), ('right', '右')):
        arm = obj.data.bones.get(smap.get(f"{side}_upper_arm_bone") or f"{jp}腕")
        el = obj.data.bones.get(smap.get(f"{side}_lower_arm_bone") or f"{jp}ひじ")
        sh = obj.data.bones.get(smap.get(f"{side}_shoulder_bone") or f"{jp}肩")
        if arm and el and sh:
            o = mw @ arm.head_local
            ax = (mw @ el.head_local) - o
            L2 = ax.length_squared
            if L2 > 1e-9:
                sides.append((o, ax, L2, L2 ** 0.5, sh.name, arm.name))
    if not sides:
        return {}
    cand_names = {b.name for b in candidates}
    acc = {}  # name -> [sum(w*pos) Vector, sum(w)]
    for m in meshes:
        idx2name = {}
        for name in cand_names:
            vg = m.vertex_groups.get(name)
            if vg:
                idx2name[vg.index] = name
        if not idx2name:
            continue
        mmw = m.matrix_world
        for v in m.data.vertices:
            wp = None
            for g in v.groups:
                nm = idx2name.get(g.group)
                if nm and g.weight > 0.001:
                    if wp is None:
                        wp = mmw @ v.co
                    s = acc.get(nm)
                    if s is None:
                        acc[nm] = [wp * g.weight, g.weight]
                    else:
                        s[0] += wp * g.weight
                        s[1] += g.weight
    dest = {}
    for nm, (sw, w) in acc.items():
        if w <= 0:
            continue
        c = sw / w
        best = None
        for o, ax, L2, alen, shname, armname in sides:
            t = (c - o).dot(ax) / L2
            proj = o + ax * max(0.0, min(1.0, t))
            lat = (c - proj).length
            if best is None or lat < best[0]:
                best = (lat, shname, t, alen, armname, o, ax, L2)
        lat, shname, t, alen, armname, o, ax, L2 = best
        # Deltoid: centroid at 0.05<=t<=0.55 (proximal-to-mid upper arm) and
        # laterally within half an upper-arm length (hugging the arm). Excludes
        # head/neck/root behind the shoulder (t<0), elbow-side twist (t>0.55),
        # and laterally-distant chest/control bones.
        if 0.05 <= t <= 0.55 and lat < 0.5 * alen:
            dest[nm] = (shname, armname, o, ax, L2)
    return dest


class OBJECT_OT_transfer_unused_weights(bpy.types.Operator):
    """Move unused/control-bone weights onto the nearest valid deform bone."""
    bl_idname = "object.transfer_unused_weights"
    bl_label = "转移 unused 骨权重"
    bl_options = {'REGISTER', 'UNDO'}

    SKIP_PATTERNS = ('foretwist', 'muscle')
    CONTROL_BONES = ('全ての親', 'センター', 'グルーブ', '操作中心')
    STANDARD_MMD_BONES = frozenset((
        '上半身', '上半身1', '上半身2', '上半身3', '下半身', '首', '首1', '頭', '腰',
        '左肩', '右肩', '左腕', '右腕', '左ひじ', '右ひじ', '左手首', '右手首',
        '左足', '右足', '左ひざ', '右ひざ', '左足首', '右足首', '左足先EX', '右足先EX',
        '左目', '右目', '腰キャンセル.L', '腰キャンセル.R',
        '左人指０', '右人指０', '左中指０', '右中指０', '左薬指０', '右薬指０', '左小指０', '右小指０',
    ))

    def _auto_classify(self, armature):
        try:
            clear_cache()
            smap = identify_skeleton(armature.data)
            if sum(1 for v in smap.values() if v) < 5:
                return None
            return classify_helpers(armature.data, smap)
        except Exception:
            return None

    def execute(self, context):
        obj = context.active_object
        if not obj or obj.type != 'ARMATURE':
            self.report({'ERROR'}, "请先选中骨架")
            return {'CANCELLED'}

        mesh_objects = skinned_meshes(obj)
        if not mesh_objects:
            self.report({'ERROR'}, "未找到挂此 armature 的 mesh")
            return {'CANCELLED'}

        cls = self._auto_classify(obj)

        if cls:
            # "unused*" are XPS helper leftovers. Bones the classifier calls
            # 'twist' (e.g. the deltoid 'unused bip001 xtra07pp') would normally be
            # preserved, but this pipeline builds its OWN 腕捩1/2/3 for twist and
            # does not consume XPS twist/deltoid helpers; keeping them would let
            # their shoulder weight ride the arm twist and deform the shoulder.
            # So twist/other 'unused' bones are merged into the nearest deform bone.
            # ('preserve' thigh/breast helpers are off the arm chain → kept.)
            unused_bones = [
                b for b in obj.data.bones
                if (cls.get(b.name) == 'merge'
                    or (b.name.startswith('unused') and cls.get(b.name) in ('twist', 'other')))
                and b.name not in self.STANDARD_MMD_BONES
            ]
            control_bones = [b for b in obj.data.bones if b.name in self.CONTROL_BONES]
            print("\n[Transfer unused] 使用 auto-classifier")
        else:
            unused_bones = [
                b for b in obj.data.bones
                if b.name.startswith('unused')
                and not any(p in b.name.lower() for p in self.SKIP_PATTERNS)
            ]
            control_bones = [b for b in obj.data.bones if b.name in self.CONTROL_BONES]
            print("\n[Transfer unused] 使用硬编码 patterns (fallback)")

        bones_to_transfer = unused_bones + control_bones
        valid_deform_bones = [
            b for b in obj.data.bones
            if not b.name.startswith('unused')
            and not b.name.startswith('_shadow')
            and not b.name.startswith('_dummy')
            and b.use_deform
        ]
        if not valid_deform_bones:
            self.report({'ERROR'}, "无有效变形骨")
            return {'CANCELLED'}

        valid_heads = [(b, obj.matrix_world @ b.head_local) for b in valid_deform_bones]

        # Deltoid (shoulder cap): split by position to 肩 (top) / 腕 (lower) base —
        # reproduce the target hand-off instead of dumping the whole cap to one bone.
        deltoid_dest = _detect_arm_deltoid(obj, mesh_objects, bones_to_transfer)
        if deltoid_dest:
            print(f"[Transfer unused] 三角肌按位置分肩/腕: { {k: (v[0], v[1]) for k, v in deltoid_dest.items()} }")

        def _add(mesh, dest_name, vidx, wt):
            tvg = mesh.vertex_groups.get(dest_name) or mesh.vertex_groups.new(name=dest_name)
            tvg.add([vidx], wt, 'ADD')

        total_transferred = 0
        for mesh in mesh_objects:
            for ubone in bones_to_transfer:
                vg = mesh.vertex_groups.get(ubone.name)
                if not vg:
                    continue
                forced = deltoid_dest.get(ubone.name)
                n = 0
                for v in mesh.data.vertices:
                    for g in v.groups:
                        if g.group == vg.index and g.weight > 0.001:
                            if forced:
                                sh_name, arm_name, o, ax, L2 = forced
                                vert_pos = obj.matrix_world @ v.co
                                t = (vert_pos - o).dot(ax) / L2
                                sf = deltoid_shoulder_fraction(t)
                                if sf > 1e-6:
                                    _add(mesh, sh_name, v.index, g.weight * sf)
                                if sf < 1.0 - 1e-6:
                                    _add(mesh, arm_name, v.index, g.weight * (1.0 - sf))
                            else:
                                vert_pos = obj.matrix_world @ v.co
                                dest_name = min(valid_heads, key=lambda bh: (bh[1] - vert_pos).length)[0].name
                                _add(mesh, dest_name, v.index, g.weight)
                            n += 1
                            break
                if n > 0:
                    total_transferred += n
                if ubone.name.startswith('unused') or (cls and cls.get(ubone.name) == 'merge'):
                    mesh.vertex_groups.remove(vg)
                else:
                    vg.remove(list(range(len(mesh.data.vertices))))

        # pelvis helpers map straight to 下半身
        if cls:
            pelvis_bone_names = [b.name for b in obj.data.bones if cls.get(b.name) == 'pelvis']
        else:
            pelvis_bone_names = [
                b.name for b in obj.data.bones
                if b.name.startswith('unused') and 'pelvis' in b.name.lower()
            ]
        if pelvis_bone_names:
            for mesh in mesh_objects:
                lb_vg = mesh.vertex_groups.get('下半身') or mesh.vertex_groups.new(name='下半身')
                for pname in pelvis_bone_names:
                    vg = mesh.vertex_groups.get(pname)
                    if not vg:
                        continue
                    for v in mesh.data.vertices:
                        for g in v.groups:
                            if g.group == vg.index and g.weight > 0.001:
                                lb_vg.add([v.index], g.weight, 'ADD')
                                total_transferred += 1
                                break
                    mesh.vertex_groups.remove(vg)

        self.report({'INFO'}, f"转移 {total_transferred} 顶点权重")
        return {'FINISHED'}
