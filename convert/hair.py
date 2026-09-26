"""头发物理:复用已有发骨链 → MMD 刚体 + 关节(参考实测参数;不造骨、不动权重)。

参数取自 11 个「18」系列参考 PMX 的发链实测(Purifier Inase / Steelhare Alana / Cartilla
Vesperthorn / Reika / Tifa Gantz / Angel / Chainsaw Man / Claire Redfield 3 / Jun Kazama /
Luna Pool Party 等),同一套约定:
  刚体  每节 1 个 CAPSULE,沿骨、中心在段中点,半径 0.2×段长(下限 0.006H)、高 = 段长;
        mass 1 / damping 0.9, 0.99 / friction 0 / bounce 0 / 物理(mode 1);
        组 9,不撞 {1,8,9,10,11,15}(= 撞身体组 0,不撞布/发/胸)。
  关节  上一节 → 本节(链首接 頭 的刚体),摆在骨头;恒等朝向、全轴 ±10°、线锁、零弹簧。
        限位各向同性,恒等系在 MMD 老 Bullet 下安全(见 docs/skirt_physics_design.md v4)。

发链识别:
  * 自动:頭 之下、名字像头发的骨(HAIR_RE);排除脸/眉/睫毛/胡子与发饰——UE 面部骨
    FACIAL_*_Hair 是发际线控制点,不是发链;布词表命中的(发带飘带等)交给布物理。
  * 选中:姿态模式选中的骨 + 其后代。骨名不规范的发链、尾巴等自选。
  * 子树分叉的骨(发根/帽盖,如 Fiona 的 hair_root 系,承担发网格 69% 权重)跟头走,
    只有单链部分进物理;链首尾不带权重的骨(组织用的根骨、「先」端点)不建刚体。
  * 链首节(贴头皮)与出生时就与身体刚体重叠的节不撞身体(组 10),同布物理链根的做法:
    发根压在颈背上时碰撞和 ±10° 限位互相顶(头后仰时 Reika 后发偏离 59°、关节拉开 25mm),
    出生即重叠的节则一开始就被顶开、一直抖。其余节撞身体,长发不穿肩背。
"""

import math
import re

import bpy
from mathutils import Vector

from .skirt import (CLOTH_RE, HAIR_RE, _BODY_DEFS, _GROUP_NEAR, _NOCOLLIDE, _capsule_frame,
                    _find_root, _make_body_rigid, _make_kinematic, _mask16, _skinned_meshes)

# 名字带 hair 但不是发链:面部/眉/睫毛/胡子,发饰(随头走即可)
NOT_HAIR_RE = re.compile(
    r"facial|brow|lash|beard|mustache|moustache|眉|睫|まつ|髭|ひげ|hairpin|hairband|headband"
    r"|(?<![a-z])(?:pin|clip|band|comb|acc|ornament|tiara|crown|flower)s?(?![a-z])", re.I)

_TAG = "c2m_hair"
_GROUP = 9                  # 发碰撞组(0 起算),同参考
_R_RATIO = 0.2              # 胶囊半径 / 段长
# 半径下限(×身高):参考作者给短刘海也用粗胶囊(Inase 0.0072H、Alana 0.0065H、Angel 0.0063H)。
# UE 发链常是 1~2cm 的碎节,按 0.2×段长只有 2~5mm,质量固定为 1 时关节守不住
# (Blender 自检 Fiona 后发离群 75°,加下限后与参考模型同量级)。
_R_MIN = 0.006
_MIN_W = 0.05               # 链首尾的骨没有任何顶点权重达到它 → 不建刚体
_MIN_SEG = 0.005            # 段长下限(×身高),防退化的零长刚体
_ANG = math.radians(10.0)
_SKIP = ("unused", "_dummy_", "_shadow_")
_BODY_HINT = ("首", "上半身2", "上半身3", "左肩", "右肩")


def _weight_stats(meshes):
    """(每个顶点组的最大权重, 蒙皮网格身高)"""
    mx = {}
    zmin, zmax = math.inf, -math.inf
    for m in meshes:
        names = [vg.name for vg in m.vertex_groups]
        mw = m.matrix_world
        for v in m.data.vertices:
            z = (mw @ v.co).z
            zmin, zmax = min(zmin, z), max(zmax, z)
            for g in v.groups:
                n = names[g.group]
                if g.weight > mx.get(n, 0.0):
                    mx[n] = g.weight
    return mx, (zmax - zmin if zmax > zmin else 0.0)


def _hair_names(arm, selected):
    B = arm.data.bones
    if selected:
        out = set()
        for b in B:
            if b.select and not b.name.startswith(_SKIP):
                out.add(b.name)
                out.update(c.name for c in b.children_recursive if not c.name.startswith(_SKIP))
        return out
    head = B.get("頭")
    if head is None:
        return set()
    return {b.name for b in head.children_recursive
            if not b.name.startswith(_SKIP) and HAIR_RE.search(b.name)
            and not NOT_HAIR_RE.search(b.name) and not CLOTH_RE.search(b.name)}


def _chains(arm, names, maxw):
    """names 组成的单链(自上而下的骨列表) + 跟头走的分叉骨数。
    子树分叉的骨不进物理;链首尾不带权重的骨去掉(中间的保留,链不能断)。"""
    B = arm.data.bones
    kids = {n: [c for c in B[n].children if c.name in names] for n in names}
    single = {}

    def is_single(n):
        if n not in single:
            single[n] = len(kids[n]) <= 1 and all(is_single(c.name) for c in kids[n])
        return single[n]

    chains = []
    for b in B:                         # 按骨骼顺序遍历,刚体顺序稳定
        n = b.name
        if n not in names or not is_single(n):
            continue
        if b.parent and b.parent.name in names and is_single(b.parent.name):
            continue                    # 不是链首
        seq = [b]
        while kids[seq[-1].name]:
            seq.append(kids[seq[-1].name][0])
        while seq and maxw.get(seq[-1].name, 0.0) < _MIN_W:
            seq.pop()
        while seq and maxw.get(seq[0].name, 0.0) < _MIN_W:
            seq.pop(0)
        if seq:
            chains.append(seq)
    return chains, sum(1 for n in names if not is_single(n))


def _segment(arm, b, names, H):
    """(骨头世界坐标, 段向量):指向链上的下一根骨(含被去掉的「先」端点),叶骨用自身 tail;
    子骨异常远(> 2.5×自身骨长)时也退回 tail。"""
    mw = arm.matrix_world
    head = mw @ b.head_local
    tail_vec = (mw @ b.tail_local) - head
    child = next((c for c in b.children if c.name in names), None)
    vec = ((mw @ child.head_local) - head) if child else tail_vec
    if vec.length < 1e-6 or (tail_vec.length > 1e-6 and vec.length > 2.5 * tail_vec.length):
        vec = tail_vec
    if vec.length < _MIN_SEG * H:
        vec = (vec.normalized() if vec.length > 1e-9 else Vector((0.0, 0.0, -1.0))) * (_MIN_SEG * H)
    return head, vec


def _seg_dist(p1, q1, p2, q2):
    """两线段最近距离(Ericson, Real-Time Collision Detection 5.1.9)。"""
    d1, d2, r = q1 - p1, q2 - p2, p1 - p2
    a, e, f = d1.dot(d1), d2.dot(d2), d2.dot(r)
    if a <= 1e-12 and e <= 1e-12:
        return r.length
    if a <= 1e-12:
        s, t = 0.0, min(1.0, max(0.0, f / e))
    else:
        c = d1.dot(r)
        if e <= 1e-12:
            s, t = min(1.0, max(0.0, -c / a)), 0.0
        else:
            b = d1.dot(d2)
            den = a * e - b * b
            s = min(1.0, max(0.0, (b * f - c * e) / den)) if den > 1e-12 else 0.0
            t = (b * s + f) / e
            if t < 0.0:
                s, t = min(1.0, max(0.0, -c / a)), 0.0
            elif t > 1.0:
                s, t = min(1.0, max(0.0, (b - c) / a)), 1.0
    return ((p1 + d1 * s) - (p2 + d2 * t)).length


def _body_shapes(model):
    """模型已有 kinematic 刚体的碰撞几何 (轴端点 a, b, 半径):球是退化段;胶囊长轴是本地 Z
    (mmd_tools 胶囊网格约定,见 skirt._capsule_frame);箱按外接球近似。"""
    out = []
    for o in model.rigidBodies():
        if o.mmd_rigid.type != '0':
            continue
        sz = o.mmd_rigid.size
        mw = o.matrix_world
        c = mw.translation.copy()
        if o.mmd_rigid.shape == 'CAPSULE':
            z = (mw.to_3x3() @ Vector((0.0, 0.0, 1.0))).normalized() * (sz[1] * 0.5)
            out.append((c - z, c + z, sz[0]))
        elif o.mmd_rigid.shape == 'SPHERE':
            out.append((c, c, sz[0]))
        else:
            out.append((c, c, Vector(sz).length))
    return out


def _anchor(model, arm, meshes, rigids, bone):
    """链首的锚:沿父链第一个有刚体的骨;到 頭 等身体刚体骨还没有就给它补同款实测刚体
    (与「身体碰撞刚体」按钮结果一致,之后再点该按钮会跳过它);都没有就给父骨补 kinematic。"""
    body_defs = {d[0]: d for d in _BODY_DEFS}
    cur = bone.parent
    while cur:
        rb = rigids.get(cur.name)
        if rb is None and cur.name in body_defs:
            rb = _make_body_rigid(model, arm, meshes, body_defs[cur.name])
        if rb is not None:
            rigids[cur.name] = rb
            return cur.name, rb
        cur = cur.parent
    if bone.parent:
        rb = _make_kinematic(model, arm, bone.parent.name)
        if rb is not None:
            rigids[bone.parent.name] = rb
            return bone.parent.name, rb
    return None, None


class OBJECT_OT_add_hair_physics(bpy.types.Operator):
    """复用已有发骨链建 MMD 刚体+关节(参考实测:胶囊、±10°、零弹簧);分叉的发根跟头走"""
    bl_idname = "object.add_hair_physics"
    bl_label = "头发物理"
    bl_options = {'REGISTER', 'UNDO'}

    selected_only: bpy.props.BoolProperty(  # type: ignore
        name="只用选中骨",
        description="只给姿态模式里选中的骨(连同其后代)建物理,骨名不规范的发链用它",
        default=False, options={'SKIP_SAVE'})

    def execute(self, context):
        root = _find_root(context.active_object)
        if not root:
            self.report({'ERROR'}, "未找到 mmd 模型(请先完成转换)")
            return {'CANCELLED'}
        from mmd_tools.core.model import Model
        model = Model(root)
        arm = model.armature()
        if not arm:
            self.report({'ERROR'}, "mmd 模型没有骨架")
            return {'CANCELLED'}
        if context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')

        names = _hair_names(arm, self.selected_only)
        if not names:
            self.report({'WARNING'}, "没有选中的骨(先在姿态模式选中发骨)" if self.selected_only else
                        "没找到发骨(名字含 hair/髪/ponytail 等);骨名不规范就选中发骨后点「只用选中骨」")
            return {'CANCELLED'}
        meshes = _skinned_meshes(arm)
        maxw, H = _weight_stats(meshes)
        if H <= 0.0:
            self.report({'ERROR'}, "找不到蒙皮网格")
            return {'CANCELLED'}
        chains, n_hub = _chains(arm, names, maxw)
        if not chains:
            self.report({'WARNING'}, "发骨都不带权重或都是分叉骨,未建物理")
            return {'CANCELLED'}

        # 场景残留的启用状态 rigidbody_world 会让新刚体一创建就被求值挪位(见 skirt.py)
        rbw = context.scene.rigidbody_world
        if rbw and rbw.enabled:
            rbw.enabled = False
        rigids = {o.mmd_rigid.bone: o for o in model.rigidBodies() if o.mmd_rigid.bone}
        anchors = {seq[0].name: _anchor(model, arm, meshes, rigids, seq[0])
                   for seq in chains if seq[0].name not in rigids}
        context.view_layer.update()     # 新建锚刚体的 matrix_world 刷新后才有效
        shapes = _body_shapes(model)
        mask = _mask16(_NOCOLLIDE)

        n_rb = n_near = 0
        for seq in chains:
            prev = None if seq[0].name in rigids else anchors[seq[0].name][1]
            if prev is None and seq[0].name not in rigids:
                continue                # 连锚都建不出来(没有父骨),整条跳过
            for i, b in enumerate(seq):
                old = rigids.get(b.name)
                if old is not None:     # 已有刚体(重复运行/模型自带):不重建,下一节接它
                    prev = old
                    continue
                head, vec = _segment(arm, b, names, H)
                L = vec.length
                r = max(_R_RATIO * L, _R_MIN * H)
                near = i == 0 or any(_seg_dist(head, head + vec, p, q) < r + R for p, q, R in shapes)
                rigid = model.createRigidBody(
                    shape_type=2, location=head + vec * 0.5, rotation=_capsule_frame(vec),
                    size=(r, L, 0.0), dynamics_type=1,
                    collision_group_number=_GROUP_NEAR if near else _GROUP,
                    collision_group_mask=mask, name=b.name, bone=b.name,
                    mass=1.0, friction=0.0, linear_damping=0.9, angular_damping=0.99, bounce=0.0,
                )
                rigid[_TAG] = 1
                joint = model.createJoint(
                    location=head, rotation=(0.0, 0.0, 0.0), rigid_a=prev, rigid_b=rigid,
                    maximum_location=(0.0, 0.0, 0.0), minimum_location=(0.0, 0.0, 0.0),
                    maximum_rotation=(_ANG, _ANG, _ANG), minimum_rotation=(-_ANG, -_ANG, -_ANG),
                    spring_linear=(0.0, 0.0, 0.0), spring_angular=(0.0, 0.0, 0.0),
                    name=b.name,
                )
                joint[_TAG] = 1
                rigids[b.name] = rigid
                prev = rigid
                n_rb += 1
                n_near += near
        # 建刚体/关节时 mmd_tools 会按需新建(启用的)rigidbody world;导出前不能让它求值
        rbw = context.scene.rigidbody_world
        if rbw:
            rbw.enabled = False

        used = sorted({a for a, _rb in anchors.values() if a})
        msg = (f"头发物理: 发链 {len(chains)} 条, 刚体+关节 {n_rb} 组"
               f"(链首/贴身不撞身体 {n_near} 节), 锚 {'/'.join(used) or '已有'}")
        if n_hub:
            msg += f";分叉的发根骨 {n_hub} 根跟头走"
        if not any(n in rigids for n in _BODY_HINT):
            msg += ";还没有身体碰撞刚体,长发会穿过肩背(先点「身体碰撞刚体」)"
        self.report({'INFO'}, msg)
        print("[hair] " + msg)
        for seq in chains:
            print(f"[hair]   {seq[0].name} ×{len(seq)}")
        return {'FINISHED'}


class OBJECT_OT_remove_hair_physics(bpy.types.Operator):
    """删除本工具建的头发刚体/关节(骨骼、权重、作锚的身体刚体都不动)"""
    bl_idname = "object.remove_hair_physics"
    bl_label = "清除头发物理"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        root = _find_root(context.active_object)
        if not root:
            self.report({'ERROR'}, "未找到 mmd 模型")
            return {'CANCELLED'}
        from mmd_tools.core.model import Model
        model = Model(root)
        if context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        objs = [o for o in list(model.rigidBodies()) + list(model.joints()) if o.get(_TAG)]
        for o in objs:
            bpy.data.objects.remove(o, do_unlink=True)
        self.report({'INFO'}, f"已清除头发物理: 刚体/关节 {len(objs)} 个")
        return {'FINISHED'}
