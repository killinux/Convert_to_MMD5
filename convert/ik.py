"""Step 3 — 添加 MMD IK: 足ＩＫ/つま先ＩＫ chains + knee IK limits."""

import bpy
from mathutils import Vector
from math import radians
from .. import bone_utils


def _add_ik(bone, target, subtarget, chain_count, iterations,
            ik_min_x=None, ik_max_x=None, use_ik_limit_x=False,
            use_ik_limit_y=False, use_ik_limit_z=False):
    c = bone.constraints.new(type='IK')
    c.name = "IK"
    c.target = target
    c.subtarget = subtarget
    c.chain_count = chain_count
    c.iterations = iterations
    if ik_min_x is not None:
        bone.ik_min_x = ik_min_x
    if ik_max_x is not None:
        bone.ik_max_x = ik_max_x
    bone.use_ik_limit_x = use_ik_limit_x
    bone.use_ik_limit_y = use_ik_limit_y
    bone.use_ik_limit_z = use_ik_limit_z


def _add_limit_rotation(bone, use_limit_x=False, min_x=None, max_x=None):
    c = bone.constraints.new(type='LIMIT_ROTATION')
    c.name = "mmd_ik_limit_override"
    c.influence = 1
    c.use_limit_x = use_limit_x
    c.owner_space = 'LOCAL'
    if min_x is not None:
        c.min_x = min_x
    if max_x is not None:
        c.max_x = max_x


def _add_damped_track(bone, target, subtarget):
    c = bone.constraints.new(type='DAMPED_TRACK')
    c.name = "mmd_ik_target_override"
    c.target = target
    c.subtarget = subtarget
    c.influence = 0


class OBJECT_OT_add_ik(bpy.types.Operator):
    """为骨架添加MMD IK"""
    bl_idname = "object.add_mmd_ik"
    bl_label = "Add MMD IK"

    def execute(self, context):
        obj = context.active_object
        if not obj or obj.type != 'ARMATURE':
            self.report({'ERROR'}, "没有选择骨架对象")
            return {'CANCELLED'}

        if context.mode != 'EDIT_ARMATURE':
            bpy.ops.object.mode_set(mode='EDIT')

        eb = obj.data.edit_bones
        required = ['左ひざ', '右ひざ', '左足首', '右足首', '全ての親']
        missing = [n for n in required if n not in eb]
        if missing:
            self.report({'ERROR'}, f"缺失基础骨骼: {', '.join(missing)}，请先补全骨骼")
            return {'CANCELLED'}

        bone_length = bone_utils.calculate_bone_length(eb)
        ik_bones = {
            "左足IK親": {"head": Vector((eb["左ひざ"].tail.x, eb["左ひざ"].tail.y, 0)),
                       "tail": eb["左ひざ"].tail, "parent": "全ての親"},
            "左足ＩＫ": {"head": eb["左ひざ"].tail,
                      "tail": eb["左ひざ"].tail + Vector((0, bone_length * 0.5, 0)), "parent": "左足IK親"},
            "左つま先ＩＫ": {"head": eb["左足首"].tail,
                         "tail": eb["左足首"].tail + Vector((0, 0, -bone_length * 0.4)), "parent": "左足ＩＫ"},
            "右足IK親": {"head": Vector((eb["右ひざ"].tail.x, eb["右ひざ"].tail.y, 0)),
                       "tail": eb["右ひざ"].tail, "parent": "全ての親"},
            "右足ＩＫ": {"head": eb["右ひざ"].tail,
                      "tail": eb["右ひざ"].tail + Vector((0, bone_length * 0.5, 0)), "parent": "右足IK親"},
            "右つま先ＩＫ": {"head": eb["右足首"].tail,
                         "tail": eb["右足首"].tail + Vector((0, 0, -bone_length * 0.4)), "parent": "右足ＩＫ"},
        }
        for name, p in ik_bones.items():
            bone_utils.create_or_update_bone(eb, name, p["head"], p["tail"],
                                             use_connect=False, parent_name=p["parent"], use_deform=False)

        bpy.ops.object.mode_set(mode='POSE')
        pb = obj.pose.bones
        _add_ik(pb["左ひざ"], obj, "左足ＩＫ", 2, 200, ik_min_x=radians(0), ik_max_x=radians(180),
                use_ik_limit_x=True, use_ik_limit_y=True, use_ik_limit_z=True)
        _add_limit_rotation(pb["左ひざ"], use_limit_x=True, min_x=radians(0.5), max_x=radians(180))
        _add_ik(pb["右ひざ"], obj, "右足ＩＫ", 2, 200, ik_min_x=radians(0), ik_max_x=radians(180),
                use_ik_limit_x=True, use_ik_limit_y=True, use_ik_limit_z=True)
        _add_limit_rotation(pb["右ひざ"], use_limit_x=True, min_x=radians(0.5), max_x=radians(180))
        _add_ik(pb["左足首"], obj, "左つま先ＩＫ", 1, 200)
        _add_damped_track(pb["左足首"], obj, "左ひざ")
        _add_ik(pb["右足首"], obj, "右つま先ＩＫ", 1, 200)
        _add_damped_track(pb["右足首"], obj, "右ひざ")
        return {'FINISHED'}
