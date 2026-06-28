"""衣服权重转移 —— 把身体网格的骨骼权重转给贴身衣物。

适用:上衣 / 紧身裤 / 袜等贴着皮肤动的衣物。原理是 Blender 的 Data Transfer
(VGROUP_WEIGHTS, Nearest Face Interpolated):衣服每个顶点去身体表面找最近的面、
插值抄它的权重。转完做 Normalize + Limit Total≤4(MMD/PMX 硬要求),并补上
指向同一骨架的 Armature 修改器,衣服就能跟着骨骼一起动。

选择约定:**先选衣服(可多选),最后加选身体网格(身体=活动/高亮)**。
活动对象 = 权重来源(身体),其余选中的网格 = 要接收权重的衣服。

裙子等离身飘动的衣物不在此列(那需要裙骨 + 刚体物理,见 tab2 后续功能)。
"""

import bpy

from .common import skinned_meshes


def _armature_of(mesh):
    for md in mesh.modifiers:
        if md.type == 'ARMATURE' and md.object:
            return md.object
    return None


def _ensure_armature_mod(mesh, arm):
    if _armature_of(mesh) is arm:
        return
    md = mesh.modifiers.new(name="Armature", type='ARMATURE')
    md.object = arm
    md.use_vertex_groups = True


class OBJECT_OT_transfer_clothing_weights(bpy.types.Operator):
    """把身体网格的权重转移到选中的衣服网格(贴身衣物)。

    选择:先选衣服(可多选),最后加选身体(身体=活动)。
    活动=来源身体,其余选中=目标衣服。
    """
    bl_idname = "object.transfer_clothing_weights"
    bl_label = "衣服权重转移(身体→衣服)"
    bl_description = ("把身体网格的骨骼权重转给选中的贴身衣服(Nearest Face 插值)，"
                     "并归一+限制每顶点≤4骨、补 Armature 修改器。"
                     "选择:先选衣服，最后加选身体(身体=活动)")
    bl_options = {'REGISTER', 'UNDO'}

    limit_total: bpy.props.IntProperty(  # type: ignore
        name="每顶点最大骨数", description="Limit Total —— MMD/PMX 要求 ≤4", default=4, min=1, max=8)

    def execute(self, context):
        src = context.active_object
        if not src or src.type != 'MESH':
            self.report({'ERROR'}, "活动对象必须是身体网格(来源)。先选衣服，最后加选身体")
            return {'CANCELLED'}
        if not src.vertex_groups:
            self.report({'ERROR'}, "身体网格没有顶点组(权重)")
            return {'CANCELLED'}
        targets = [o for o in context.selected_objects if o.type == 'MESH' and o is not src]
        if not targets:
            self.report({'ERROR'}, "未选中衣服网格。先选衣服(可多选)，最后加选身体")
            return {'CANCELLED'}

        arm = _armature_of(src)
        if context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')

        done = []
        for t in targets:
            bpy.ops.object.select_all(action='DESELECT')
            src.select_set(True)
            t.select_set(True)
            context.view_layer.objects.active = src   # data_transfer 从活动(源)→选中(目标)
            bpy.ops.object.data_transfer(
                data_type='VGROUP_WEIGHTS',
                vert_mapping='POLYINTERP_NEAREST',     # Nearest Face Interpolated
                layers_select_src='ALL',
                layers_select_dst='NAME',
                mix_mode='REPLACE',
            )
            # 补 Armature 修改器(指向身体所用骨架)，衣服才会跟骨骼动
            if arm:
                _ensure_armature_mod(t, arm)
            # 归一 + 限制每顶点骨数(MMD 要求)
            context.view_layer.objects.active = t
            try:
                bpy.ops.object.vertex_group_normalize_all(group_select_mode='ALL', lock_active=False)
                bpy.ops.object.vertex_group_limit_total(group_select_mode='ALL', limit=self.limit_total)
                bpy.ops.object.vertex_group_normalize_all(group_select_mode='ALL', lock_active=False)
            except Exception as e:
                print(f"[clothing] {t.name} 归一/限骨跳过: {e}")
            done.append(t.name)
            print(f"[clothing] 权重转移: {src.name} → {t.name}")

        # 还原选择状态(活动设回身体)
        bpy.ops.object.select_all(action='DESELECT')
        src.select_set(True)
        context.view_layer.objects.active = src
        self.report({'INFO'}, f"衣服权重转移完成 ({len(done)} 件): {', '.join(done)}")
        return {'FINISHED'}
