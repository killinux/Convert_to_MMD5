from math import radians
import bpy
DEFAULT_ROLL_VALUES = {
    "全ての親": 0.0, "センター": 0.0, "グルーブ": 0.0, "腰": 0.0, 
    "上半身": 0.0,"上半身2": 0.0, "上半身3": 0.0, "上半身4": 0.0, "上半身5": 0.0, "首": 0.0, "頭": 0.0,
    "下半身": 0.0, "左足": 0.0, "右足": 0.0,"左ひざ": 0.0, "右ひざ": 0.0, "左足首": 0.0, "右足首": 0.0, "左足先EX": 0.0,"右足先EX": 0.0, 
    "左腕": 45.0, "左ひじ": 45.0, "左手首": 45.0,
    "右腕": 135.0, "右ひじ": 135.0, "右手首": 135.0,
    "左肩": 0.0, "右肩": 180.0
}

def create_or_update_bone(edit_bones, name, head_position, tail_position, use_connect=False, parent_name=None, use_deform=True):
    bone = edit_bones.get(name)
    if bone:
        bone.head = head_position
        bone.tail = tail_position
        bone.use_connect = use_connect
        bone.parent = edit_bones.get(parent_name) if parent_name else None
        bone.use_deform = use_deform
    else:
        bone = edit_bones.new(name)
        bone.head = head_position
        bone.tail = tail_position
        bone.use_connect = use_connect
        bone.parent = edit_bones.get(parent_name) if parent_name else None
        bone.use_deform = use_deform
    return bone


def set_roll_values(edit_bones, bone_roll_mapping):
    for bone_name, roll_value in bone_roll_mapping.items():
        if bone_name in edit_bones:
            edit_bones[bone_name].roll = radians(roll_value)


def apply_armature_transforms(context, armature_obj=None):
    """自动应用骨架和网格对象的变换"""
    try:
        # 确保在对象模式
        if bpy.context.object and bpy.context.object.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        
        # 获取骨架对象
        armature = armature_obj or context.active_object
        if not armature or armature.type != 'ARMATURE':
            print("未找到骨架对象")
            return False

        # 选择骨架层级并应用变换
        bpy.ops.object.select_all(action='DESELECT')
        armature.select_set(True)
        bpy.context.view_layer.objects.active = armature
        bpy.ops.object.select_hierarchy(direction='CHILD', extend=True)
        bpy.ops.object.transform_apply(location=False, rotation=True, scale=True)
        
        # 选择回骨架
        bpy.ops.object.select_all(action='DESELECT')
        armature.select_set(True)
        bpy.context.view_layer.objects.active = armature
        
        return True

    except Exception as e:
        print(f"应用变换时出错: {str(e)}")
        return False


def calculate_skeleton_height(edit_bones):
    """计算骨架高度
    
    Args:
        edit_bones: 编辑模式下的骨骼集合
        
    Returns:
        float: 骨架高度
    """
    min_z = float('inf')
    max_z = -float('inf')
    for bone in edit_bones:
        min_z = min(min_z, bone.head.z, bone.tail.z)
        max_z = max(max_z, bone.head.z, bone.tail.z)
    return max_z - min_z


def _mesh_height(armature_data):
    """挂这个骨架的蒙皮网格在骨架局部 Z 上的跨度;没有网格返回 None。"""
    arm = next((o for o in bpy.data.objects if o.type == 'ARMATURE' and o.data == armature_data), None)
    if arm is None:
        return None
    inv = arm.matrix_world.inverted()
    zs = []
    for o in bpy.data.objects:
        if o.type == 'MESH' and any(m.type == 'ARMATURE' and m.object == arm for m in o.modifiers):
            mw = inv @ o.matrix_world
            zs.extend((mw @ v.co).z for v in o.data.vertices)
    return (max(zs) - min(zs)) if zs else None


def calculate_bone_length(edit_bones):
    """八分之一身高作为 bone_length(センター/グルーブ/腰 等控制骨按它摆放)。
    身高优先取蒙皮网格的高度:骨架里常有远离身体的骨(UE 的 Anim_Attachment 挂点在头顶
    2.4m/地下 1m、XPS root hips 的长骨尾),按骨骼跨度会把身高撑大 20%~90%,
    グルーブ 被摆到胸口、腰 被摆到身后。没有网格时退回骨骼跨度。"""
    height = _mesh_height(edit_bones.id_data) or calculate_skeleton_height(edit_bones)
    return height * 0.125


def check_and_scale_skeleton(obj):
    """检测骨架高度并自动缩放
    
    Args:
        obj: 骨架对象
        
    Returns:
        tuple: (是否进行了缩放, 缩放因子, 原始高度)
    """
    # 切换到对象模式应用当前旋转
    bpy.ops.object.mode_set(mode='OBJECT')
    # 应用当前旋转
    bpy.ops.object.transform_apply(location=False, rotation=True, scale=False)
    
    # 切换到编辑模式
    bpy.ops.object.mode_set(mode='EDIT')
    edit_bones = obj.data.edit_bones
    
    # 获取骨架高度
    skeleton_height = calculate_skeleton_height(edit_bones)
    
    scale_factor = 1.0
    scaled = False
    
    # 检查高度并计算缩放因子
    if skeleton_height > 10:
        # 计算缩放因子：10m→0.1，100m→0.01，类推
        temp_height = skeleton_height
        while temp_height > 10:
            scale_factor *= 0.1
            temp_height *= 0.1
        
        # 切换到对象模式
        bpy.ops.object.mode_set(mode='OBJECT')
        # 重置缩放
        obj.scale = (1.0, 1.0, 1.0)
        # 缩放
        obj.scale *= scale_factor
        # 清除父级
        obj.parent = None
        # 使用apply_armature_transforms应用变换
        
        context = bpy.context
        apply_armature_transforms(context, obj)


    
    return scaled, scale_factor, skeleton_height