"""布物理(刚体+关节) + 身体碰撞刚体 —— 复用已有布骨 + 复用 mmd_tools。

转换后，给现成的布骨(裙/外套/披风/披肩/纱巾/飘带/发)补 MMD 标准的刚体+关节，
使其在 VMD 动作下自然飘动。不造骨、不重刷权重；刚体/关节一律走 mmd_tools 的
Model.createRigidBody/createJoint。

自适应(在 cartilla coat / alana cloak+shawl+scart / 裙链上标定):
  * 词表识别布骨 → **按骨处理**(支持 cloak 5→6/7 这种分叉，不假设线性链)；
  * 父骨也是布骨 → 关节接父骨刚体；否则沿父链向上找第一个标准骨当锚
    (shawl 锚 肩、head scarf 锚 頭、scart 3-4 锚 足D、skirt 锚 下半身)，
    锚骨没有刚体就补一个 kinematic 的；
  * 头发单独一类:CAPSULE、更轻、限位更松，可开关。
布料穿身需要身体有碰撞刚体，见 OBJECT_OT_add_body_rigids(半径按网格顶点实测)。

尺寸按转换模型骨骼几何算，与尺度无关的参数(质量/阻尼/碰撞组/掩码/关节限位/弹簧)
抄目标 PMX 实测值。设计与调研见 docs/skirt_physics_design.md。
"""

import bpy
import re
import math
from mathutils import Vector, Matrix

# 布骨词表(scar[ft] 同时覆盖 scarf 与部分 rip 写作 scart 的飘带)
CLOTH_RE = re.compile(
    r"skirt|スカート|coat|cloak|cape|mantle|shawl|veil|scar[ft]|hangings|drape|apron|robe|frill|sash|ribbon",
    re.I)
HAIR_RE = re.compile(r"hair|ponytail|twintail|braid", re.I)

# 锚骨候选:布链根沿父链向上碰到的第一个即为锚
ANCHOR_OK = ("下半身", "上半身3", "上半身2", "上半身", "首", "頭", "左肩", "右肩",
             "左腕", "右腕", "左ひじ", "右ひじ", "左足D", "右足D", "左ひざD", "右ひざD",
             "腰", "センター")
ANCHOR_FALLBACK = "下半身"

# —— 目标 PMX 实测、与尺度无关的默认值 ——
_DYN_DYNAMIC = 1            # DYNAMIC
_DYN_STATIC = 0             # STATIC(kinematic, 跟骨)
_GROUP = 11                 # 布碰撞组(0-based)
_GROUP_NEAR = 10            # 贴身段(链根深度≤1):不与身体碰撞，防出生穿透爆炸
_NOCOLLIDE = (1, 8, 9, 10, 11, 15)      # 布不与这些组碰撞(含自身组:布片间不互撞),同参考 PMX
_BODY_NOCOLLIDE = (_GROUP_NEAR,)        # 身体刚体不与贴身段碰撞
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

# —— 头发(照抄 Purifier Inase 18 参考 PMX 实测:CAPSULE、全轴±10°、零弹簧) ——
_HAIR_MASS = 1.0
_HAIR_R_RATIO = 0.18
_HAIR_ANG_MAX = (math.radians(10), math.radians(10), math.radians(10))
_HAIR_ANG_MIN = (math.radians(-10), math.radians(-10), math.radians(-10))
_HAIR_SPRING_ANG = (0.0, 0.0, 0.0)


def _mask16(no_collide):
    return [i in no_collide for i in range(16)]


def _find_root(obj):
    o = obj
    while o:
        if getattr(o, "mmd_type", "") == "ROOT":
            return o
        o = o.parent
    return next((x for x in bpy.data.objects if getattr(x, "mmd_type", "") == "ROOT"), None)


def _box_frame(head, seg_vec, center_xy):
    """构造刚体朝向:Y=沿骨, X=径向外(厚), Z=切向(宽)。返回 (center, euler, half_len)。

    注意:mmd_tools 的刚体/关节 empty 是 rotation_mode='YXZ'(MMD 惯例),
    传给 createRigidBody/createJoint 的欧拉必须用 to_euler('YXZ');
    默认 to_euler()=XYZ 会被错序解释,水平段朝向直接歪掉。
    """
    y_axis = seg_vec.normalized()
    half_len = seg_vec.length * 0.5
    center = head + seg_vec * 0.5
    radial = Vector((center.x - center_xy[0], center.y - center_xy[1], 0.0))
    if radial.length < 1e-5:
        radial = Vector((0.0, -1.0, 0.0))
    x_axis = radial.normalized()
    z_axis = y_axis.cross(x_axis)
    if z_axis.length < 1e-5:
        z_axis = Vector((1.0, 0.0, 0.0))
    z_axis.normalize()
    x_axis = z_axis.cross(y_axis).normalized()
    mat = Matrix((x_axis, y_axis, z_axis)).transposed()
    return center, mat.to_euler('YXZ'), half_len


def _joint_frame(seg_vec, center, center_xy):
    """布关节朝向(Blender 系):X=切向, Y=径向, Z=沿骨。

    经 mmd_tools 坐标变换(B_X→PMX_X, B_Y→PMX_Z, B_Z→PMX_Y)后,PMX 里落成
    X=切向(±30 外摆)、Y=沿骨(±5 扭转)、Z=径向(±10 横摆),与参考 PMX 的裙关节
    逐轴一致。关键是把小角度的扭转轴放到 PMX Y——MMD 的老版 Bullet(2.75)对
    6DOF 限位做欧拉分解时 Y 是奇异轴(±90° asin 退化),恒等朝向会让快速转身的
    相对 yaw 直接打穿奇异区 → 限位扭矩发散 → 全场爆炸(Blender 新版 Bullet 能
    容忍,故 Blender 仿真测不出来)。恒等朝向只对各向同性限位的发关节成立。
    """
    zb = seg_vec.normalized()
    radial = Vector((center.x - center_xy[0], center.y - center_xy[1], 0.0))
    if radial.length < 1e-5:
        radial = Vector((0.0, -1.0, 0.0))
    x = radial.cross(zb)                # 切向
    if x.length < 1e-5:
        x = zb.orthogonal()
    x.normalize()
    y = zb.cross(x)                     # 径向(正交化)
    return Matrix((x, y, zb)).transposed().to_euler('YXZ')


def _bone_frame(arm, bone):
    """骨骼自身几何:返回 (head_w, vec_w, length, euler)。Y 沿骨。"""
    mw = arm.matrix_world
    h = mw @ bone.head_local
    t = mw @ bone.tail_local
    vec = t - h
    length = vec.length or 0.05
    y = vec.normalized() if vec.length > 1e-6 else Vector((0, 0, 1))
    ref = Vector((1, 0, 0)) if abs(y.x) < 0.9 else Vector((0, 0, 1))
    z = y.cross(ref).normalized()
    x = z.cross(y).normalized()
    mat = Matrix((x, y, z)).transposed()
    return h, vec, length, mat.to_euler('YXZ')


def _existing_rigids():
    out = {}
    for o in bpy.data.objects:
        if getattr(o, "mmd_type", "") == "RIGID_BODY" and o.mmd_rigid.bone:
            out[o.mmd_rigid.bone] = o
    return out


def _make_kinematic(model, arm, bone_name):
    """给骨补一个 kinematic 碰撞/锚定刚体(CAPSULE, group0, 不撞贴身段)。"""
    bone = arm.data.bones.get(bone_name)
    if not bone:
        return None
    h, vec, length, euler = _bone_frame(arm, bone)
    return model.createRigidBody(
        shape_type=2, location=h + vec * 0.5, rotation=euler,
        size=(length * 0.3, length, 0.0),
        dynamics_type=_DYN_STATIC,
        collision_group_number=0, collision_group_mask=_mask16(_BODY_NOCOLLIDE),
        name=bone_name, bone=bone_name,
        mass=_MASS, friction=0.5, linear_damping=0.5, angular_damping=0.5, bounce=0.0,
    )


class OBJECT_OT_add_skirt_physics(bpy.types.Operator):
    """给现成布骨(裙/外套/披风/披肩/飘带/发)自动加 MMD 刚体+关节(复用 mmd_tools)。

    按骨处理(支持分叉链)；父骨是布骨→接父刚体，否则沿父链找标准骨当锚。
    无布骨的模型自动跳过。建议先跑「身体碰撞刚体」，布料才不穿身。
    """
    bl_idname = "object.add_skirt_physics"
    bl_label = "布物理: 裙/外套/披风/发(自动)"
    bl_description = ("词表识别布骨(裙/coat/cloak/shawl/veil/scarf/飘带/发等)，"
                     "逐骨建 mmd 刚体+关节，锚定到就近标准骨，VMD 下自然飘动")
    bl_options = {'REGISTER', 'UNDO'}

    include_hair: bpy.props.BoolProperty(  # type: ignore
        name="含头发", description="头发链也建物理(CAPSULE、更轻、限位更松)", default=True)

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

        bones = arm.data.bones
        targets = []
        for b in bones:
            n = b.name
            if n.startswith(("unused", "_dummy_", "_shadow_")):
                continue
            if CLOTH_RE.search(n):
                targets.append((b, False))
            elif self.include_hair and HAIR_RE.search(n):
                targets.append((b, True))
        if not targets:
            self.report({'INFO'}, "未发现布骨/发骨，跳过")
            return {'FINISHED'}

        if context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        # 场景里可能残留启用状态的 rigidbody_world(带旧缓存):新刚体一创建就会被
        # 求值步进、位移出生位。创建期间必须禁用;预览物理时再用 mmd_tools Build。
        rbw = context.scene.rigidbody_world
        if rbw and rbw.enabled:
            rbw.enabled = False

        target_names = {b.name for b, _ in targets}
        # 父在前(按层级深度排序)，保证接关节时父刚体已建好
        def _depth(b):
            d = 0; cur = b.parent
            while cur:
                d += 1; cur = cur.parent
            return d
        targets.sort(key=lambda t: _depth(t[0]))

        mw = arm.matrix_world
        mask = _mask16(_NOCOLLIDE)
        existing = _existing_rigids()

        cb = bones.get(ANCHOR_FALLBACK)
        center_xy = (0.0, 0.0)
        if cb:
            ch = mw @ cb.head_local
            center_xy = (ch.x, ch.y)

        def _anchor_name(b):
            cur = b.parent
            while cur:
                if cur.name in ANCHOR_OK:
                    return cur.name
                cur = cur.parent
            return ANCHOR_FALLBACK

        def _cloth_depth(b):
            d = 0; cur = b.parent
            while cur:
                if cur.name in target_names:
                    d += 1
                cur = cur.parent
            return d

        # 身体 kinematic 胶囊几何(判定"贴身"用):中心/轴向/半高/半径
        body_shapes = []
        for o in existing.values():
            if str(o.mmd_rigid.type) not in ('0', 'STATIC'):
                continue
            sz = o.mmd_rigid.size
            mwj = o.matrix_world
            yax = (mwj.to_3x3() @ Vector((0, 1, 0))).normalized()
            body_shapes.append((mwj.translation.copy(), yax, max(sz[1] * 0.5, 0.01), sz[0]))

        def _near_body(center, half_diag, margin=0.03):
            for (c, y, hh, r) in body_shapes:
                rel = center - c
                t = max(-hh, min(hh, rel.dot(y)))
                if (rel - y * t).length < r + half_diag + margin:
                    return True
            return False

        n_rb = n_jt = n_anchor = 0
        for b, is_hair in targets:
            # 段向量:指向第一个同为目标的子骨；叶骨用自身 tail。
            # 子骨异常远(如 cloak 5→6/7 分叉的枝端相距 1.6m)会造出巨型刚体引爆整链，
            # 此时退回自身 tail。
            child = next((c for c in b.children if c.name in target_names), None)
            head = mw @ b.head_local
            tail_vec = (mw @ b.tail_local) - head
            vec = ((mw @ child.head_local) - head) if child else tail_vec
            if child and tail_vec.length > 0.015 and vec.length > 2.5 * tail_vec.length:
                vec = tail_vec
            if vec.length < 1e-6:
                vec = Vector((0, 0, -0.05))
            # 贴身段不与身体碰撞(出生时嵌在身体胶囊里会被持续顶出/弹飞):
            # 链根(深度≤1)或出生位置与任一身体胶囊重叠者 → 组10；
            # 真正悬空的段(裙摆/披风下摆)留组11去撞腿/身体。
            _hd = vec.length * 0.5 + 0.06
            group = (_GROUP_NEAR if (_cloth_depth(b) <= 1
                     or _near_body(head + vec * 0.5, _hd)) else _GROUP)

            # 段坐标系(刚体与关节共用):Y 沿骨
            if is_hair:
                length = vec.length
                y = vec.normalized()
                ref = Vector((1, 0, 0)) if abs(y.x) < 0.9 else Vector((0, 0, 1))
                z = y.cross(ref).normalized()
                x = z.cross(y).normalized()
                euler = Matrix((x, y, z)).transposed().to_euler('YXZ')
            else:
                center, euler, half_len = _box_frame(head, vec, center_xy)

            rigid = existing.get(b.name)
            if rigid is None:
                if is_hair:
                    rigid = model.createRigidBody(
                        shape_type=2,                    # CAPSULE
                        location=head + vec * 0.5, rotation=euler,
                        size=(min(0.05, max(0.015, length * _HAIR_R_RATIO)), max(0.03, length), 0.0),
                        dynamics_type=_DYN_DYNAMIC,
                        collision_group_number=group, collision_group_mask=mask,
                        name=b.name, bone=b.name,
                        mass=_HAIR_MASS, friction=_FRICTION,
                        linear_damping=_LIN_DAMP, angular_damping=_ANG_DAMP, bounce=_BOUNCE,
                    )
                else:
                    half_len = min(half_len, 0.25)      # 防异常巨箱
                    size = (max(0.012, half_len * _THICK_RATIO), max(0.015, half_len),
                            max(0.015, half_len * _WIDTH_RATIO))
                    rigid = model.createRigidBody(
                        shape_type=1,                    # BOX
                        location=center, rotation=euler, size=size,
                        dynamics_type=_DYN_DYNAMIC,
                        collision_group_number=group, collision_group_mask=mask,
                        name=b.name, bone=b.name,
                        mass=_MASS, friction=_FRICTION,
                        linear_damping=_LIN_DAMP, angular_damping=_ANG_DAMP, bounce=_BOUNCE,
                    )
                existing[b.name] = rigid
                n_rb += 1

            # 关节:父是布/发骨→接父刚体；否则接锚(没有锚刚体就补 kinematic)
            if b.parent and b.parent.name in target_names:
                parent_rigid = existing.get(b.parent.name)
            else:
                aname = _anchor_name(b)
                parent_rigid = existing.get(aname)
                if parent_rigid is None:
                    parent_rigid = _make_kinematic(model, arm, aname)
                    if parent_rigid is not None:
                        existing[aname] = parent_rigid
                        n_anchor += 1
            if parent_rigid is not None:
                amax, amin, sang = ((_HAIR_ANG_MAX, _HAIR_ANG_MIN, _HAIR_SPRING_ANG)
                                    if is_hair else (_ANG_MAX, _ANG_MIN, _SPRING_ANG))
                # 弹簧刚度按段长²缩放(基准 0.15m):质量固定时转动惯量∝L²，
                # 短段配满刚度弹簧会超出求解器稳定域(实测 6cm shawl 段 f10 即爆)。
                sk = min(1.0, max(0.02, (vec.length / 0.15) ** 2))
                sang = tuple(s * sk for s in sang)
                jrot = ((0.0, 0.0, 0.0) if is_hair
                        else _joint_frame(vec, head + vec * 0.5, center_xy))
                model.createJoint(
                    location=head, rotation=jrot,
                    rigid_a=parent_rigid, rigid_b=rigid,
                    maximum_location=_ZERO3, minimum_location=_ZERO3,
                    maximum_rotation=amax, minimum_rotation=amin,
                    spring_linear=_SPRING_LIN, spring_angular=sang,
                    name=b.name,
                )
                n_jt += 1

        # 不在此 build():build 会把布骨绑到动力学刚体上，导出前场景一经求值
        # 刚体就被重力/弹出位移、布骨跟着跑，导出的 PMX 绑定位即被污染(关节出生
        # 即违反 → 全场爆炸)。要在 Blender 里预览物理，用 mmd_tools 的 Build 按钮。

        msg = f"布物理完成: 刚体+{n_rb}, 关节+{n_jt}, 补锚+{n_anchor}"
        self.report({'INFO'}, msg)
        print(f"[cloth] {msg}")
        return {'FINISHED'}


# ---------------------------------------------------------------------------
# 身体碰撞刚体
# ---------------------------------------------------------------------------

# (骨名, 形状 0=SPHERE 2=CAPSULE, 无权重时的半径/骨长比, 量半径用的顶点组)
# 腿用 FK 骨(足→ひざ 段有真实长度；足D 是竖直短桩,沿它建胶囊盖不住腿)，
# 但顶点组在 D 骨名下，所以量半径的 VG 单独给。
_BODY_DEFS = [
    ("頭", 0, 0.55, ()),
    ("首", 2, 0.35, ()),
    ("上半身3", 2, 0.90, ()),
    ("上半身2", 2, 0.90, ()),
    ("上半身", 2, 0.95, ()),
    ("下半身", 2, 0.95, ()),
    ("左足", 2, 0.30, ("左足D",)), ("右足", 2, 0.30, ("右足D",)),
    ("左ひざ", 2, 0.28, ("左ひざD",)), ("右ひざ", 2, 0.28, ("右ひざD",)),
    ("左腕", 2, 0.25, ()), ("右腕", 2, 0.25, ()),
    ("左ひじ", 2, 0.22, ()), ("右ひじ", 2, 0.22, ()),
]


def _skinned_meshes(arm):
    return [o for o in bpy.data.objects
            if o.type == 'MESH'
            and any(m.type == 'ARMATURE' and m.object == arm for m in o.modifiers)]


def _measured_radius(arm, meshes, bone_name, vg_names=(), pct=0.85):
    """按顶点组(权重>0.3)顶点到骨轴的径向距离取分位数，估碰撞半径。"""
    bone = arm.data.bones.get(bone_name)
    if not bone:
        return None
    mw = arm.matrix_world
    h = mw @ bone.head_local
    axis = (mw @ bone.tail_local) - h
    L = axis.length or 1e-6
    axis_n = axis / L
    names = set(vg_names) or {bone_name}
    dists = []
    for m in meshes:
        gis = {vg.index for vg in m.vertex_groups if vg.name in names}
        if not gis:
            continue
        mmw = m.matrix_world
        for v in m.data.vertices:
            for g in v.groups:
                if g.group in gis and g.weight > 0.3:
                    rel = (mmw @ v.co) - h
                    r = (rel - axis_n * rel.dot(axis_n)).length
                    dists.append(r)
                    break
    if len(dists) < 8:
        return None
    dists.sort()
    r = dists[min(len(dists) - 1, int(len(dists) * pct))]
    return min(max(r, 0.015), L * 1.5)


class OBJECT_OT_add_body_rigids(bpy.types.Operator):
    """给头/躯干/四肢补 kinematic 碰撞刚体(group0)，布料/头发才不会穿身。

    半径按网格顶点实测(该骨权重>0.3 的顶点到骨轴径向距离的 85 分位)，
    测不到时按骨长比例兜底。已有刚体的骨跳过，可重复运行。
    """
    bl_idname = "object.add_body_rigids"
    bl_label = "身体碰撞刚体(自动)"
    bl_description = ("给 頭/首/上半身系/下半身/腿D/腕/ひじ 建 kinematic 碰撞刚体，"
                     "半径按网格实测。先于布物理运行")
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
        if context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        # 场景里可能残留启用状态的 rigidbody_world(带旧缓存):新刚体一创建就会被
        # 求值步进、位移出生位。创建期间必须禁用;预览物理时再用 mmd_tools Build。
        rbw = context.scene.rigidbody_world
        if rbw and rbw.enabled:
            rbw.enabled = False

        meshes = _skinned_meshes(arm)
        existing = _existing_rigids()
        n = 0
        for bone_name, shape, fb_ratio, vg_names in _BODY_DEFS:
            bone = arm.data.bones.get(bone_name)
            if not bone or bone_name in existing:
                continue
            h, vec, length, euler = _bone_frame(arm, bone)
            r = _measured_radius(arm, meshes, bone_name, vg_names) or (length * fb_ratio)
            if shape == 0:      # SPHERE
                size = (r, 0.0, 0.0)
                loc = h + vec * 0.5
            else:               # CAPSULE:height 扣掉两端半球，避免总长超出
                size = (r, max(length * 0.35, length - r), 0.0)
                loc = h + vec * 0.5
            rigid = model.createRigidBody(
                shape_type=shape, location=loc, rotation=euler, size=size,
                dynamics_type=_DYN_STATIC,
                collision_group_number=0, collision_group_mask=_mask16(_BODY_NOCOLLIDE),
                name=bone_name, bone=bone_name,
                mass=_MASS, friction=0.5, linear_damping=0.5, angular_damping=0.5, bounce=0.0,
            )
            existing[bone_name] = rigid
            n += 1
            print(f"[body-rigid] {bone_name}: shape={'SPHERE' if shape==0 else 'CAPSULE'} "
                  f"r={r:.3f} len={length:.3f}")

        # 同布物理:不 build()，避免导出前物理求值污染绑定位。

        self.report({'INFO'}, f"身体碰撞刚体完成: +{n}")
        return {'FINISHED'}
