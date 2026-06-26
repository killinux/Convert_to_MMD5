"""Step 2 — 补全缺失骨骼: build the missing standard MMD bones + split inserted ones.

Pure skeleton construction: control bones (全ての親/センター/グルーブ/腰), the upper/lower
body, arms, legs, toe-EX, 腰キャンセル, finger metacarpals (指０), and the inserted
上半身1 / 首1. Weight work is delegated to weights/chain.py (the inserted bones and the
armpit smoothing). Additional-transform grants (incl. 腰キャンセル) are set later by the
unified grants step — here 腰キャンセル is only created and hidden.
"""

import bpy
from mathutils import Vector

from .. import bone_utils
from .weights.chain import split_chain_weights


class OBJECT_OT_complete_missing_bones(bpy.types.Operator):
    """补充缺失的 MMD 格式骨骼"""
    bl_idname = "object.complete_missing_bones"
    bl_label = "Complete Missing Bones"

    def _connect_finger_bones(self, edit_bones):
        finger_chains = [
            ["左親指０", "左親指１", "左親指２"], ["左人指１", "左人指２", "左人指３"],
            ["左中指１", "左中指２", "左中指３"], ["左薬指１", "左薬指２", "左薬指３"],
            ["左小指１", "左小指２", "左小指３"], ["右親指０", "右親指１", "右親指２"],
            ["右人指１", "右人指２", "右人指３"], ["右中指１", "右中指２", "右中指３"],
            ["右薬指１", "右薬指２", "右薬指３"], ["右小指１", "右小指２", "右小指３"],
        ]
        for chain in finger_chains:
            if all(b in edit_bones for b in chain):
                for i in range(len(chain) - 1):
                    edit_bones[chain[i]].tail = edit_bones[chain[i + 1]].head

    def execute(self, context):
        obj = context.active_object
        if not obj or obj.type != 'ARMATURE':
            self.report({'ERROR'}, "没有选择骨架")
            return {'CANCELLED'}
        if context.mode != 'EDIT_ARMATURE':
            bpy.ops.object.mode_set(mode='EDIT')

        edit_bones = obj.data.edit_bones
        left_foot_bone = edit_bones.get("左足")
        right_foot_bone = edit_bones.get("右足")
        upper_body_bone = edit_bones.get("上半身")
        lower_body_bone = edit_bones.get("下半身")
        for b in (left_foot_bone, right_foot_bone):
            if b:
                b.use_connect = False
                b.parent = None
        for b in (upper_body_bone, lower_body_bone):
            if b and b.parent:
                b.use_connect = False
                b.parent = None
        if not upper_body_bone:
            self.report({'ERROR'}, "上半身骨骼不存在")
            return {'CANCELLED'}
        upper_body_head = upper_body_bone.head.copy()
        upper_body_tail = upper_body_bone.tail.copy()

        bone_length = bone_utils.calculate_bone_length(edit_bones)

        upper_chain_bones = [f"上半身{i}" for i in range(2, 6) if edit_bones.get(f"上半身{i}")]
        last_upper_body = upper_chain_bones[-1] if upper_chain_bones else "上半身"

        has_left_leg = bool(edit_bones.get("左足"))
        has_right_leg = bool(edit_bones.get("右足"))
        left_leg_parent = "腰キャンセル.L" if has_left_leg else "下半身"
        right_leg_parent = "腰キャンセル.R" if has_right_leg else "下半身"

        bp = {
            "全ての親": {"head": Vector((0, 0, 0)), "tail": Vector((0, 0, bone_length)), "parent": None, "use_deform": False, "use_connect": False},
            "センター": {"head": Vector((0, 0, bone_length * 2)), "tail": Vector((0, 0, bone_length * 1.1)), "parent": "全ての親", "use_deform": False, "use_connect": False},
            "グルーブ": {"head": Vector((0, 0, bone_length * 3.2)), "tail": Vector((0, 0, bone_length * 4)), "parent": "センター", "use_deform": False, "use_connect": False},
            "腰": {"head": Vector((0, upper_body_head.y + bone_length * 0.5, upper_body_head.z - bone_length * 0.5)), "tail": Vector((0, upper_body_head.y, upper_body_head.z)), "parent": "グルーブ", "use_deform": False, "use_connect": False},
            "上半身": {"head": Vector((0, upper_body_head.y, upper_body_head.z)), "tail": Vector((0, upper_body_tail.y, upper_body_head.z + bone_length)), "parent": "腰", "use_connect": False},
            "首": {"head": edit_bones["首"].head, "tail": edit_bones["頭"].head, "parent": last_upper_body, "use_connect": False},
            "頭": {"head": edit_bones["頭"].head, "tail": Vector((0, edit_bones["頭"].head.y, edit_bones["頭"].head.z + bone_length * 0.25)), "parent": "首", "use_connect": False},
            "左肩": {"head": edit_bones["左肩"].head, "tail": edit_bones["左腕"].head, "parent": last_upper_body, "use_connect": False},
            "左腕": {"head": edit_bones["左腕"].head, "tail": edit_bones["左ひじ"].head, "parent": "左肩", "use_connect": True},
            "左ひじ": {"head": edit_bones["左ひじ"].head, "tail": edit_bones["左手首"].head if edit_bones["左手首"] else edit_bones["左ひじ"].tail, "parent": "左腕", "use_connect": True},
            "左手首": {"head": edit_bones["左手首"].head, "tail": edit_bones["左中指１"].head.copy() if edit_bones.get("左中指１") else edit_bones["左手首"].tail, "parent": "左ひじ", "use_connect": False},
            "右肩": {"head": edit_bones["右肩"].head, "tail": edit_bones["右腕"].head, "parent": last_upper_body, "use_connect": False},
            "右腕": {"head": edit_bones["右腕"].head, "tail": edit_bones["右ひじ"].head, "parent": "右肩", "use_connect": True},
            "右ひじ": {"head": edit_bones["右ひじ"].head, "tail": edit_bones["右手首"].head if edit_bones["右手首"] else edit_bones["右ひじ"].tail, "parent": "右腕", "use_connect": True},
            "右手首": {"head": edit_bones["右手首"].head, "tail": edit_bones["右中指１"].head.copy() if edit_bones.get("右中指１") else edit_bones["右手首"].tail, "parent": "右ひじ", "use_connect": False},
            "下半身": {"head": Vector((0, upper_body_head.y, upper_body_head.z)), "tail": Vector((0, upper_body_head.y, upper_body_head.z - bone_length)), "parent": "腰", "use_connect": False},
        }

        # 腰キャンセル: cancels 腰 rotation for the legs (grant set later by grants step).
        if has_left_leg:
            bp["腰キャンセル.L"] = {"head": edit_bones["左足"].head.copy(), "tail": edit_bones["左足"].head + Vector((0, 0, bone_length * 0.5)), "parent": "下半身", "use_connect": False, "use_deform": False}
        if has_right_leg:
            bp["腰キャンセル.R"] = {"head": edit_bones["右足"].head.copy(), "tail": edit_bones["右足"].head + Vector((0, 0, bone_length * 0.5)), "parent": "下半身", "use_connect": False, "use_deform": False}

        bp.update({
            "左足": {"head": edit_bones["左足"].head, "tail": edit_bones["左ひざ"].head, "parent": left_leg_parent, "use_connect": False},
            "右足": {"head": edit_bones["右足"].head, "tail": edit_bones["右ひざ"].head, "parent": right_leg_parent, "use_connect": False},
            "左ひざ": {"head": edit_bones["左ひざ"].head, "tail": edit_bones["左足首"].head, "parent": "左足", "use_connect": False},
            "右ひざ": {"head": edit_bones["右ひざ"].head, "tail": edit_bones["右足首"].head, "parent": "右足", "use_connect": False},
            "左足首": {"head": edit_bones["左足首"].head, "tail": Vector((edit_bones["左足首"].head.x, edit_bones["左足首"].head.y - bone_length * 0.3, 0)), "parent": "左ひざ", "use_connect": False},
            "右足首": {"head": edit_bones["右足首"].head, "tail": Vector((edit_bones["右足首"].head.x, edit_bones["右足首"].head.y - bone_length * 0.3, 0)), "parent": "右ひざ", "use_connect": False},
            "左足先EX": {"head": edit_bones["左足首"].tail, "tail": Vector((edit_bones["左足首"].tail.x, edit_bones["左足首"].tail.y - bone_length * 0.5, 0)), "parent": "左足首", "use_connect": False},
            "右足先EX": {"head": edit_bones["右足首"].tail, "tail": Vector((edit_bones["右足首"].tail.x, edit_bones["右足首"].tail.y - bone_length * 0.5, 0)), "parent": "右足首", "use_connect": False},
        })

        # 上半身链 (上半身2..5): tail → next segment, parent → previous.
        if upper_chain_bones:
            for idx, bone_name in enumerate(upper_chain_bones):
                next_name = upper_chain_bones[idx + 1] if idx + 1 < len(upper_chain_bones) else None
                tail_ref = next_name if next_name else "首"
                bp[bone_name] = {
                    "head": Vector((0, edit_bones[bone_name].head.y, edit_bones[bone_name].head.z)),
                    "tail": Vector((0, edit_bones[tail_ref].head.y, edit_bones[tail_ref].head.z)),
                    "parent": upper_chain_bones[idx - 1] if idx > 0 else "上半身",
                    "use_connect": False,
                }

        # 上半身1 auto-insert between 上半身 and the first upper-chain segment.
        first_upper_chain = upper_chain_bones[0] if upper_chain_bones else None
        upper1_just_created = False
        if first_upper_chain and not edit_bones.get("上半身1"):
            ub_head = bp["上半身"]["head"].copy()
            ub2_head = bp[first_upper_chain]["head"].copy()
            mid = (ub_head + ub2_head) * 0.5
            if (ub2_head - ub_head).length > bone_length * 0.2:
                bp["上半身"]["tail"] = mid.copy()
                bp["上半身1"] = {"head": mid.copy(), "tail": ub2_head.copy(), "parent": "上半身", "use_connect": False, "use_deform": True}
                bp[first_upper_chain]["parent"] = "上半身1"
                upper1_just_created = True

        # 首1 auto-insert between 首 and 頭.
        neck1_just_created = False
        if edit_bones.get("首") and edit_bones.get("頭") and not edit_bones.get("首1"):
            neck_head = bp["首"]["head"].copy()
            head_head = bp["頭"]["head"].copy()
            neck_mid = (neck_head + head_head) * 0.5
            if (head_head - neck_head).length > bone_length * 0.2:
                bp["首"]["tail"] = neck_mid.copy()
                bp["首1"] = {"head": neck_mid.copy(), "tail": head_head.copy(), "parent": "首", "use_connect": False, "use_deform": True}
                bp["頭"]["parent"] = "首1"
                neck1_just_created = True

        # finger metacarpals (人指０/中指０/薬指０/小指０): pass-through, no weight split here.
        finger_root_defs = [("人指０", "人指１"), ("中指０", "中指１"), ("薬指０", "薬指１"), ("小指０", "小指１")]
        for side in ("左", "右"):
            wrist = edit_bones.get(f"{side}手首")
            if not wrist:
                continue
            for root_base, first_base in finger_root_defs:
                root_name = f"{side}{root_base}"
                first_name = f"{side}{first_base}"
                if edit_bones.get(root_name) or not edit_bones.get(first_name):
                    continue
                first_eb = edit_bones[first_name]
                bp[root_name] = {"head": (wrist.head + first_eb.head) * 0.5, "tail": first_eb.head.copy(), "parent": f"{side}手首", "use_connect": False, "use_deform": True}
                bp[first_name] = {"head": first_eb.head.copy(), "tail": first_eb.tail.copy(), "parent": root_name, "use_connect": False}

        # create/update all bones
        for bone_name, properties in bp.items():
            head = (edit_bones[bone_name].head.copy()
                    if bone_name in ("左足先EX", "右足先EX") and bone_name in edit_bones
                    else properties["head"])
            bone_utils.create_or_update_bone(edit_bones, bone_name, head, properties["tail"],
                                             properties.get("use_connect", False), properties["parent"],
                                             properties.get("use_deform", True))

        if "左足先EX" in edit_bones:
            edit_bones["左足首"].tail = edit_bones["左足先EX"].head
        if "右足先EX" in edit_bones:
            edit_bones["右足首"].tail = edit_bones["右足先EX"].head

        # second pass: fix parents (a child may have been created before its parent)
        for bone_name, properties in bp.items():
            parent_name = properties.get("parent")
            if parent_name and bone_name in edit_bones:
                parent_bone = edit_bones.get(parent_name)
                if parent_bone and edit_bones[bone_name].parent != parent_bone:
                    edit_bones[bone_name].parent = parent_bone

        pelvis_bone = edit_bones.get("unused bip001 pelvis")
        lower_body = edit_bones.get("下半身")
        if pelvis_bone and lower_body:
            pelvis_bone.parent = lower_body

        bone_utils.set_roll_values(edit_bones, bone_utils.DEFAULT_ROLL_VALUES)
        self._connect_finger_bones(edit_bones)

        # weight splits for the inserted bones (OBJECT mode for vertex-group edits)
        if upper1_just_created and first_upper_chain:
            bpy.ops.object.mode_set(mode='OBJECT')
            try:
                split_chain_weights(obj, "上半身", "上半身1", "上半身", first_upper_chain)
            except Exception as e:
                print(f"[complete] 上半身1 权重分割失败: {e}")
            bpy.ops.object.mode_set(mode='EDIT')
        if neck1_just_created:
            bpy.ops.object.mode_set(mode='OBJECT')
            try:
                split_chain_weights(obj, "首", "首1", "首", "頭")
            except Exception as e:
                print(f"[complete] 首1 权重分割失败: {e}")
            bpy.ops.object.mode_set(mode='EDIT')

        # armpit smoothing: 肩→腕 additive (src_keep_floor=1.0, don't thin 肩)
        bpy.ops.object.mode_set(mode='OBJECT')
        for side_jp in ("左", "右"):
            shoulder, arm_bone = f"{side_jp}肩", f"{side_jp}腕"
            if obj.data.bones.get(shoulder) and obj.data.bones.get(arm_bone):
                try:
                    split_chain_weights(obj, shoulder, arm_bone, shoulder, arm_bone, src_keep_floor=1.0)
                except Exception as e:
                    print(f"[complete] 腋窝平滑 {shoulder} 失败: {e}")

        # hide 腰キャンセル (grant applied later by the unified grants step)
        for side in (".L", ".R"):
            bone = obj.data.bones.get(f"腰キャンセル{side}")
            if bone:
                bone.hide = True
        bpy.ops.object.mode_set(mode='EDIT')

        bpy.ops.object.mode_set(mode='OBJECT')
        return {'FINISHED'}
