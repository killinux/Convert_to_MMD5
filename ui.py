"""Slim 'Convert to MMD' panel — bone management only (no physics/import/dev-tools).

Keeps the original 主骨骼管理 layout and every bl_idname so the operation surface is
unchanged (req 5). The 次标准 tab lists only the bone operators kept in this build.
"""

import bpy

# Bump this on every code update so you can SEE in the panel that Blender actually
# reloaded the new code (Blender caches Python modules — a stale build means the
# addon was not re-enabled/restarted). 改动后改这里。
BUILD_STAMP = "build 2026-07-05 left-align"


def _step_btn(box, idname, text, icon=None, off=False):
    """清单式按钮:左对齐(Blender 按钮文字默认居中且不可改,惯用做法是
    row.alignment='LEFT' 让按钮收缩为文字宽、贴左排列)。
    off=True 时变灰停用并标注(精简路线的权重步),编号保留以便对照标准路线。"""
    row = box.row(align=True)
    row.alignment = 'LEFT'
    row.enabled = not off
    kw = {"icon": icon} if icon else {}
    row.operator(idname, text=text + ("(精简:跳过)" if off else ""), **kw)


def _bone_row(layout, scene, obj, label_text, prop_name):
    row = layout.row(align=True)
    split = row.split(factor=0.1, align=True)
    split.label(text=label_text)
    act = split.split(factor=1)
    sub = act.split(factor=(0.49 * 0.1), align=True)
    sub.operator("object.fill_from_selection_specific", text="", icon='ZOOM_SELECTED').bone_property = prop_name
    sub.prop_search(scene, prop_name, obj.data, "bones", text="")


def _symmetric_row(layout, scene, obj, label_text, left_prop, right_prop):
    row = layout.row(align=True)
    split = row.split(factor=0.1, align=True)
    split.label(text=label_text)
    act = split.split(factor=1, align=True)
    left = act.split(factor=0.49, align=True)
    lrow = left.column(align=True).row(align=True)
    lb = lrow.split(factor=0.1, align=True)
    lb.operator("object.fill_from_selection_specific", text="", icon='ZOOM_SELECTED').bone_property = left_prop
    lb.prop_search(scene, left_prop, obj.data, "bones", text="")
    divider = left.split(factor=(0.02 / (0.02 + 0.49)), align=True)
    divider.label(text="|")
    right = divider.split(factor=1, align=True)
    rrow = right.column(align=True).row(align=True)
    rb = rrow.split(factor=0.1, align=True)
    rb.operator("object.fill_from_selection_specific", text="", icon='ZOOM_SELECTED').bone_property = right_prop
    rb.prop_search(scene, right_prop, obj.data, "bones", text="")


def _finger_row(layout, scene, obj, label_text, p1, p2, p3):
    divider_ratio = 0.02
    split_ratio = (1 - 2 * divider_ratio) / 3
    row = layout.row(align=True)
    split = row.split(factor=0.1, align=True)
    split.label(text=label_text)
    act = split.split(factor=1, align=True)

    first = act.split(factor=split_ratio, align=True)
    f1 = first.column(align=True).row(align=True).split(factor=0.1, align=True)
    f1.operator("object.fill_from_selection_specific", text="", icon='ZOOM_SELECTED').bone_property = p1
    f1.prop_search(scene, p1, obj.data, "bones", text="")
    d1 = first.split(factor=divider_ratio / (1 - split_ratio), align=True)
    d1.label(text="|")
    second = d1.split(factor=split_ratio / (1 - split_ratio - divider_ratio), align=True)
    f2 = second.column(align=True).row(align=True).split(factor=0.1, align=True)
    f2.operator("object.fill_from_selection_specific", text="", icon='ZOOM_SELECTED').bone_property = p2
    f2.prop_search(scene, p2, obj.data, "bones", text="")
    d2 = second.split(factor=divider_ratio / (1 - split_ratio * 2 - divider_ratio), align=True)
    d2.label(text="|")
    third = d2.split(factor=1, align=True)
    f3 = third.column(align=True).row(align=True).split(factor=0.1, align=True)
    f3.operator("object.fill_from_selection_specific", text="", icon='ZOOM_SELECTED').bone_property = p3
    f3.prop_search(scene, p3, obj.data, "bones", text="")


class OBJECT_PT_skeleton_hierarchy(bpy.types.Panel):
    bl_label = "Convert to MMD"
    bl_idname = "OBJECT_PT_convert_to_mmd"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Convert to MMD"

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        obj = context.active_object
        # build stamp — always visible at the very top so you can confirm the reload took
        layout.label(text=BUILD_STAMP, icon='FILE_REFRESH')
        if not obj or obj.type != 'ARMATURE':
            layout.label(text="请选中骨架对象", icon='INFO')
            layout.menu("TOPBAR_MT_file_import", text="导入模型", icon='IMPORT')
            return

        row = layout.row()
        row.prop(scene, "my_enum", expand=True)

        if scene.my_enum == 'option1':
            # 转换路线:标准=重建权重(全流程) / 精简=保源权重(权重四步 3/6/11/12 停用)。
            # 一键按钮与下方手动分步都跟随此开关。
            row = layout.row(align=True)
            row.prop(scene, "convert_route", expand=True)
            minimal = scene.convert_route == 'MINIMAL'

            box = layout.box()
            box.label(text="自动 / 一键", icon='AUTO')
            r = box.row(align=True)
            r.scale_y = 1.35
            op = r.operator(
                "object.one_click_convert",
                text=("一键转换 XPS→MMD(精简·保源权重)" if minimal
                      else "一键转换 XPS→MMD(标准)"),
                icon='PLAY')
            op.minimal = minimal
            box.operator("object.auto_identify_skeleton", text="自动识别骨架（填充下方槽位）", icon='ZOOM_SELECTED')

            r = layout.row(align=True)
            r.prop(scene, "preset_enum", text="")
            r.operator("object.import_preset", text="导入预设")
            r.operator("object.export_preset", text="导出预设")
            r.operator("object.clear_bone_selection", text="", icon='X')

            main_col = layout.column(align=True)
            col = main_col.box().column()
            _bone_row(col, scene, obj, "操作中心", "control_center_bone")
            _bone_row(col, scene, obj, "全ての親", "all_parents_bone")
            _bone_row(col, scene, obj, "センター", "center_bone")
            _bone_row(col, scene, obj, "グルーブ", "groove_bone")
            _bone_row(col, scene, obj, "腰", "hip_bone")

            col = main_col.box().column()
            _bone_row(col, scene, obj, "上半身*", "upper_body_bone")
            _bone_row(col, scene, obj, "首*", "neck_bone")
            _bone_row(col, scene, obj, "頭*", "head_bone")
            _symmetric_row(col, scene, obj, "目", "left_eye_bone", "right_eye_bone")
            _symmetric_row(col, scene, obj, "肩*", "left_shoulder_bone", "right_shoulder_bone")
            _symmetric_row(col, scene, obj, "腕*", "left_upper_arm_bone", "right_upper_arm_bone")
            _symmetric_row(col, scene, obj, "ひじ*", "left_lower_arm_bone", "right_lower_arm_bone")
            _symmetric_row(col, scene, obj, "手首*", "left_hand_bone", "right_hand_bone")

            col = main_col.box().column()
            _bone_row(col, scene, obj, "下半身", "lower_body_bone")
            _symmetric_row(col, scene, obj, "足*", "left_thigh_bone", "right_thigh_bone")
            _symmetric_row(col, scene, obj, "ひざ*", "left_calf_bone", "right_calf_bone")
            _symmetric_row(col, scene, obj, "足首*", "left_foot_bone", "right_foot_bone")
            _symmetric_row(col, scene, obj, "足先EX", "left_toe_bone", "right_toe_bone")

            col = main_col.box().column()
            for label, a, b, c in (
                ("左親指", "left_thumb_0", "left_thumb_1", "left_thumb_2"),
                ("左人指", "left_index_1", "left_index_2", "left_index_3"),
                ("左中指", "left_middle_1", "left_middle_2", "left_middle_3"),
                ("左薬指", "left_ring_1", "left_ring_2", "left_ring_3"),
                ("左小指", "left_pinky_1", "left_pinky_2", "left_pinky_3"),
                ("右親指", "right_thumb_0", "right_thumb_1", "right_thumb_2"),
                ("右人指", "right_index_1", "right_index_2", "right_index_3"),
                ("右中指", "right_middle_1", "right_middle_2", "right_middle_3"),
                ("右薬指", "right_ring_1", "right_ring_2", "right_ring_3"),
                ("右小指", "right_pinky_1", "right_pinky_2", "right_pinky_3"),
            ):
                _finger_row(col, scene, obj, label, a, b, c)

            # 手动分步：按编号从上到下依次点，即可复现「一键转换」的全流程（1.6 对齐手臂 /
            # 1.7 对齐手指 已从流程中删除）。自动识别(上方按钮)算第 0 步。
            box = layout.box()
            if minimal:
                box.label(text="手动分步 — 精简路线:保源权重,3/6/11/12 停用", icon='LOCKED')
            else:
                box.label(text="手动分步 — 标准路线（自动识别后，从上到下依次点）", icon='SORTSIZE')
            # 0.5 可选：源是 T-Pose 时先转 A-Pose（从 MMD6 移植，上臂绕全局Y倒到~36°+拉直肘）。
            #     源已是 A-Pose 则跳过。放在归正/重命名之前。
            _step_btn(box, "object.convert_to_apose", "0.5 转换为 A-Pose（源为T时可选）", icon='OUTLINER_OB_ARMATURE')
            _step_btn(box, "object.correct_bones", "1. 归正骨架位置")
            _step_btn(box, "object.rename_to_mmd", "2. 重命名为 MMD")
            _step_btn(box, "object.transfer_unused_weights", "3. 转移 unused 骨权重 ①", off=minimal)
            _step_btn(box, "object.fix_forearm_bend", "4. 修正前腕弯曲")
            _step_btn(box, "object.complete_missing_bones", "5. 补全缺失骨骼")
            _step_btn(box, "object.transfer_unused_weights", "6. 转移 unused 骨权重 ②", off=minimal)
            _step_btn(box, "object.add_mmd_ik", "7. 添加 MMD IK")
            _step_btn(box, "object.create_bone_group", "8. 创建骨骼集合")
            _step_btn(box, "object.use_mmd_tools_convert", "9. 使用 mmd_tools 转换格式")
            box.label(text="—— 上为转换前 / 下为转换后 ——", icon='DOT')
            _step_btn(box, "object.add_leg_d_bones", "10. 添加腿部 D 骨骼")
            _step_btn(box, "object.add_twist_bone", "11. 添加捩骨骼", off=minimal)
            _step_btn(box, "object.fix_palm_weights", "12. 手部权重修正(拇指+掌骨)", off=minimal)
            _step_btn(box, "object.add_shoulder_p_bones", "13. 添加肩P骨骼")
            _step_btn(box, "object.setup_mmd_grants", "14. 设置标准付与")
            _step_btn(box, "mmd_tools.apply_additional_transform", "15. 应用付与变换")

            opt = layout.box()
            opt.label(text="可选工具（不在流程内）", icon='TOOL_SETTINGS')
            _step_btn(opt, "object.straighten_arms", "拉直手臂(肘+腕)", icon='BONE_DATA')

        else:
            # 衣服 / 刚体处理。原「次标准骨骼 / XPS 专项修正」全部是 tab1 手动分步的重复，
            # 已删除（功能仍在 tab1：腿D=步10、捩骨=步11、肩P=步13、转移unused=步3/6、
            # 修正前腕=步4、拉直手臂=可选工具、手部权重=步12、设置付与=步14）。
            # 本 tab 改为放衣服与刚体相关处理，具体算子待规划。
            box = layout.box()
            box.label(text="衣服权重", icon='MOD_CLOTH')
            _step_btn(box, "object.transfer_clothing_weights", "衣服权重转移(身体→衣服)", icon='MOD_VERTEX_WEIGHT')
            box.label(text="先选衣服(可多选)，最后加选身体", icon='INFO')

            box = layout.box()
            box.label(text="刚体 / 物理", icon='PHYSICS')
            _step_btn(box, "object.add_body_rigids", "1. 身体碰撞刚体(自动)", icon='MESH_CAPSULE')
            _step_btn(box, "object.add_skirt_physics", "2. 布物理: 裙/外套/披风/发(自动)", icon='PHYSICS')
            box.label(text="先建身体刚体再建布物理，布料才不穿身", icon='INFO')


def register():
    bpy.utils.register_class(OBJECT_PT_skeleton_hierarchy)


def unregister():
    bpy.utils.unregister_class(OBJECT_PT_skeleton_hierarchy)
