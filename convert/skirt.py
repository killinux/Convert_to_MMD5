"""裙子物理(刚体+关节)自动生成 —— 复用已有裙骨 + 复用 mmd_tools。

转换后，给现成的裙骨链补 MMD 标准的刚体+关节，使裙子在 VMD 动作下自然飘动。
不造骨、不重刷权重；刚体/关节一律走 mmd_tools 的 Model.createRigidBody/createJoint。

自适应:正则识别裙骨 → 按父子关系组裙链(任意 N 片×M 节) → 每段建动力学刚体 →
逐段建关节(seg1 接下半身 kinematic 锚，其余接上一段)。尺寸按转换模型骨骼几何算，
与尺度无关的参数(质量/阻尼/碰撞组/掩码/关节限位/弹簧)抄目标 PMX 实测值。

设计与调研见 docs/skirt_physics_design.md。
"""

import bpy
import re
import math
from mathutils import Vector, Matrix

SKIRT_RE = re.compile(r"skirt|スカート", re.I)
ANCHOR_BONE = "下半身"

# —— 目标 PMX 实测、与尺度无关的默认值 ——
_DYN_DYNAMIC = 1            # DYNAMIC
_DYN_STATIC = 0            # STATIC(kinematic, 跟骨)
_GROUP = 11                # 裙子碰撞组(0-based)
_NOCOLLIDE = (1, 2, 8, 9, 10, 11, 15)   # 不与这些组碰撞
_MASS = 1.0
_LIN_DAMP = 0.9
_ANG_DAMP = 0.99
_FRICTION = 0.0
_BOUNCE = 0.0
_ANG_MAX = (math.radians(30), math.radians(10), math.radians(5))
_ANG_MIN = (math.radians(-30), math.radians(-10), math.radians(-5))
_SPRING_ANG = (30.0, 30.0, 30.0)
_SPRING_LIN = (0.0, 0.0, 0.0)
_ZERO3 = (0.0, 0.0, 0.0)
# 目标 BOX 尺寸比例 厚:长:宽 = 0.25:2.0:1.5
_THICK_RATIO = 0.125
_WIDTH_RATIO = 0.75


def _mask16(no_collide):
    return [i in no_collide for i in range(16)]


def _find_root(obj):
    o = obj
    while o:
        if getattr(o, "mmd_type", "") == "ROOT":
            return o
        o = o.parent
    return next((x for x in bpy.data.objects if getattr(x, "mmd_type", "") == "ROOT"), None)


def _skirt_chains(arm):
    """识别裙链:返回 [[seg1_bone, seg2_bone, ...], ...]（每条按 head→hem 顺序）。"""
    bones = arm.data.bones
    skirt = [b for b in bones if SKIRT_RE.search(b.name)]
    skirt_set = set(b.name for b in skirt)
    roots = [b for b in skirt if not (b.parent and b.parent.name in skirt_set)]
    chains = []
    for r in roots:
        chain = [r]
        cur = r
        while True:
            kids = [c for c in cur.children if c.name in skirt_set]
            if not kids:
                break
            cur = kids[0]      # 链状:每段一个裙子子骨
            chain.append(cur)
        chains.append(chain)
    return chains


def _seg_geom(arm, chain, idx):
    """第 idx 段的世界几何:返回 (head, seg_vec, length)。"""
    mw = arm.matrix_world
    b = chain[idx]
    head = mw @ b.head_local
    if idx + 1 < len(chain):
        nxt = mw @ chain[idx + 1].head_local
        vec = nxt - head
    else:
        vec = (mw @ b.tail_local) - head     # 末段用 head→tail
    length = vec.length
    if length < 1e-6:
        vec = Vector((0, 0, -1)); length = (mw @ b.tail_local - head).length or 0.1
    return head, vec, length


def _box_frame(head, seg_vec, center_xy):
    """构造刚体朝向:Y=沿骨, X=径向外(厚), Z=切向(宽)。返回 (center, euler, half_len)。"""
    y_axis = seg_vec.normalized()
    half_len = seg_vec.length * 0.5
    center = head + seg_vec * 0.5
    # 径向:从身体中轴(竖直线)指向刚体的水平方向
    radial = Vector((center.x - center_xy[0], center.y - center_xy[1], 0.0))
    if radial.length < 1e-5:
        radial = Vector((0.0, -1.0, 0.0))    # 退化时朝前
    x_axis = radial.normalized()
    z_axis = y_axis.cross(x_axis)
    if z_axis.length < 1e-5:
        z_axis = Vector((1.0, 0.0, 0.0))
    z_axis.normalize()
    x_axis = z_axis.cross(y_axis).normalized()   # 重新正交化
    mat = Matrix((x_axis, y_axis, z_axis)).transposed()   # 列为各轴
    return center, mat.to_euler(), half_len


class OBJECT_OT_add_skirt_physics(bpy.types.Operator):
    """给现成裙骨自动加 MMD 刚体+关节(复用 mmd_tools)，裙子可在 VMD 下飘动。

    自适应识别裙链(任意 N 片×M 节)，seg1 接下半身 kinematic 锚、其余接上一段。
    无裙骨的模型自动跳过。
    """
    bl_idname = "object.add_skirt_physics"
    bl_label = "裙子刚体/物理(自动)"
    bl_description = ("识别裙骨链，逐段创建 mmd 刚体+关节(seg1接下半身锚)，"
                     "使裙子在 VMD 下自然飘动。复用已有裙骨，自适应其它模型")
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        from mmd_tools.core.model import Model

        obj = context.active_object
        root = _find_root(obj)
        if not root:
            self.report({'ERROR'}, "未找到 mmd 模型(请先完成转换)")
            return {'CANCELLED'}
        model = Model(root)
        arm = model.armature()
        if not arm:
            self.report({'ERROR'}, "mmd 模型没有骨架")
            return {'CANCELLED'}

        chains = _skirt_chains(arm)
        if not chains:
            self.report({'INFO'}, "未发现裙骨(skirt/スカート)，跳过")
            return {'FINISHED'}

        if context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')

        mw = arm.matrix_world
        mask = _mask16(_NOCOLLIDE)

        # 现有刚体(按骨名)，避免重复建
        existing = {}
        for o in bpy.data.objects:
            if getattr(o, "mmd_type", "") == "RIGID_BODY" and o.mmd_rigid.bone:
                existing[o.mmd_rigid.bone] = o

        # 1) 下半身 kinematic 锚(没有就建)
        anchor = existing.get(ANCHOR_BONE)
        anchor_bone = arm.data.bones.get(ANCHOR_BONE)
        center_xy = (0.0, 0.0)
        if anchor_bone:
            ah = mw @ anchor_bone.head_local
            center_xy = (ah.x, ah.y)
        if anchor is None and anchor_bone:
            ah = mw @ anchor_bone.head_local
            al = (mw @ anchor_bone.tail_local - ah).length or 0.2
            anchor = model.createRigidBody(
                shape_type=2,                 # CAPSULE
                location=ah, rotation=(0, 0, 0),
                size=(al * 0.5, al, 0.0),
                dynamics_type=_DYN_STATIC,
                collision_group_number=0, collision_group_mask=_mask16(()),
                name=ANCHOR_BONE, bone=ANCHOR_BONE,
                mass=_MASS, friction=0.5, linear_damping=0.5, angular_damping=0.5, bounce=0.0,
            )

        n_rb = n_jt = 0
        for chain in chains:
            prev_rigid = anchor
            for i, b in enumerate(chain):
                head, vec, _ln = _seg_geom(arm, chain, i)
                center, euler, half_len = _box_frame(head, vec, center_xy)
                size = (half_len * _THICK_RATIO, half_len, half_len * _WIDTH_RATIO)
                rigid = existing.get(b.name)
                if rigid is None:
                    rigid = model.createRigidBody(
                        shape_type=1,            # BOX (SPHERE=0/BOX=1/CAPSULE=2)
                        location=center, rotation=euler, size=size,
                        dynamics_type=_DYN_DYNAMIC,
                        collision_group_number=_GROUP, collision_group_mask=mask,
                        name=b.name, bone=b.name,
                        mass=_MASS, friction=_FRICTION,
                        linear_damping=_LIN_DAMP, angular_damping=_ANG_DAMP, bounce=_BOUNCE,
                    )
                    n_rb += 1
                # 关节:prev_rigid → rigid，摆在本段骨头部
                if prev_rigid is not None:
                    model.createJoint(
                        location=head, rotation=(0, 0, 0),
                        rigid_a=prev_rigid, rigid_b=rigid,
                        maximum_location=_ZERO3, minimum_location=_ZERO3,
                        maximum_rotation=_ANG_MAX, minimum_rotation=_ANG_MIN,
                        spring_linear=_SPRING_LIN, spring_angular=_SPRING_ANG,
                        name=b.name,
                    )
                    n_jt += 1
                prev_rigid = rigid

        # 2) 让 mmd 物理生效
        try:
            model.build()
        except Exception as e:
            print(f"[skirt] model.build 警告: {e}")

        self.report({'INFO'}, f"裙子物理完成: {len(chains)} 链, 刚体+{n_rb}, 关节+{n_jt}")
        print(f"[skirt] chains={len(chains)} rigids+{n_rb} joints+{n_jt}")
        return {'FINISHED'}
