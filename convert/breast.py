"""胸部物理:胸骨(复用或按网格造) + 胸部权重(按需,位置驱动 + 守恒) + 刚体/关节。

参数取自参考 PMX 实测:Purifier Inase / Steelhare Alana / Super Moon Misaki / Tifa Gantz 等
11 个「18」系列模型是同一套约定,按身高 H 归一后与尺度无关:
  骨    每侧 1 根,挂在胸部所在的上半身系骨下;骨头在乳尖正后方 0.058H(贴胸廓),骨尾 = 乳尖。
  刚体  SPHERE r=0.029H,球心在骨头前方 0.024H;mass 1 / damping 0.5,0.5 / friction 0.5;
        DYNAMIC;组 15,与所有组都不碰撞(和胸廓刚体出生重叠也不会被顶开)。
  关节  父骨刚体 → 胸刚体,摆在骨头;恒等朝向、全轴 ±10°、线锁、零弹簧。限位各向同性,
        恒等系在 MMD 老 Bullet 下安全(见 docs/skirt_physics_design.md v4)。
  权重  乳尖距离高斯 exp(-(d/0.052H)²),拟合参考剖面(0.01~0.10H 处 0.87/0.70/0.40/0.18/0.06);
        只从胸部骨(上半身系 + 其 helper 后代)的权重里按比例分出,顶点总权重不变。
模型已有胸骨、且权重落在乳房区(XPS boob / MMD 左胸 / breast / bust / oppai …)时直接复用,
不造骨、不动权重。
"""

import math
import re

import bpy
from mathutils import Vector

from .skirt import (CLOTH_RE, HAIR_RE, _BODY_DEFS, _existing_rigids, _find_root,
                    _make_body_rigid, _make_kinematic, _region_names, _skinned_meshes)

BREAST_RE = re.compile(r"boob|breast|bust|oppai|おっぱい|乳|胸|mune", re.I)

_UPPER = ("上半身", "上半身1", "上半身2", "上半身3", "上半身4", "上半身5")
_CHEST = ("上半身2", "上半身3", "上半身4", "上半身5")   # 乳尖搜索区(排除腹部)
_SIDES = ((1.0, "左胸"), (-1.0, "右胸"))              # MMD 左 = +X

# 参考实测(×身高 H)
_DEPTH = 0.058      # 骨头在乳尖后方
_RADIUS = 0.029     # 球半径
_CENTER = 0.024     # 球心在骨头前方
_SIGMA = 0.052      # 权重高斯
_CUT = 2.3          # 高斯截断(×σ),exp(-5.3)≈0.005
_LATERAL = 0.02     # 乳尖至少离中线,排除中线饰物
_ANG = math.radians(10.0)
_TAG = "c2m_breast"


class _Scan:
    """蒙皮网格一次性扫描:每顶点 (网格, 索引, 世界坐标, [(组名, 权重)]) + 身高 H。"""

    def __init__(self, arm):
        self.meshes = _skinned_meshes(arm)
        self.verts = []
        zs = []
        for m in self.meshes:
            mw = m.matrix_world
            names = [vg.name for vg in m.vertex_groups]
            for v in m.data.vertices:
                p = mw @ v.co
                zs.append(p.z)
                gs = [(names[g.group], g.weight) for g in v.groups if g.weight > 0.0]
                if gs:
                    self.verts.append((m, v.index, p, gs))
        self.H = (max(zs) - min(zs)) if zs else 0.0


def _smooth(t):
    t = min(1.0, max(0.0, t))
    return t * t * (3.0 - 2.0 * t)


def _front_tip(pts):
    """一撮点里最靠前(-Y,模型面朝 -Y)的少数几个的均值。"""
    pts = sorted(pts, key=lambda q: q.y)
    k = min(len(pts), max(8, len(pts) // 30))
    return sum(pts[:k], Vector()) / k


def _chest_frame(arm):
    """胸段:(上半身2 起的最低骨头 z, 首骨头 z, 胸部骨骨头的平均 y ≈ 脊柱前后位置)。"""
    B = arm.data.bones
    mw = arm.matrix_world
    seeds = [n for n in _CHEST if n in B] or [n for n in _UPPER if n in B]
    heads = [mw @ B[n].head_local for n in seeds]
    zmin = min(h.z for h in heads)
    zmax = (mw @ B["首"].head_local).z if "首" in B else max(h.z for h in heads)
    return seeds, zmin, zmax, sum(h.y for h in heads) / len(heads)


def _apex(scan, chest, side, frame):
    """几何乳尖(仅造骨时用):胸段内、离中线够远、胸部骨为主(≥50%)、不挂布/发/饰物骨
    的顶点里,最靠前的一撮。"""
    _seeds, zmin, zmax, _y = frame
    H = scan.H
    pts = []
    for _m, _i, p, gs in scan.verts:
        if p.x * side < _LATERAL * H or not (zmin <= p.z <= zmax):
            continue
        if any(w > 0.05 and (CLOTH_RE.search(n) or HAIR_RE.search(n)) for n, w in gs):
            continue
        tot = sum(w for _, w in gs)
        if sum(w for n, w in gs if n in chest) < 0.5 * tot:
            continue
        pts.append(p)
    return _front_tip(pts) if pts else None


def _find_existing(arm, scan, frame):
    """已有胸骨 {侧: (骨名, 乳尖)}:名字像胸、子树有 ≥20 个 >0.3 权重的顶点、九成权重在一侧,
    且权重重心在胸前(胸段高度内、脊柱前方、离开中线);同侧多个取层级最高者(链根)。
    乳尖取该骨自身权重顶点里最靠前的一撮——不用几何乳尖,胸前饰物(蝴蝶结/腰带)骗不到它。"""
    _seeds, zmin, zmax, yspine = frame
    H = scan.H
    cands = [b for b in arm.data.bones
             if BREAST_RE.search(b.name) and not b.name.startswith(("_dummy_", "_shadow_"))]
    subtree = {b.name: {b.name} | {c.name for c in b.children_recursive} for b in cands}
    watched = set().union(*subtree.values()) if subtree else set()
    verts = [(p, gs) for _m, _i, p, gs in scan.verts if any(n in watched for n, _ in gs)]
    found = {}
    for side, _name in _SIDES:
        best = None
        for b in cands:
            names = subtree[b.name]
            sw = sside = 0.0
            strong = []
            acc = Vector()
            for p, gs in verts:
                w = sum(x for n, x in gs if n in names)
                if w <= 0.01:
                    continue
                sw += w
                acc += p * w
                if p.x * side > 0:
                    sside += w
                if w > 0.3:
                    strong.append(p)
            if len(strong) < 20 or sside < 0.9 * sw:
                continue
            c = acc / sw
            if c.x * side < 0.01 * H or not (zmin <= c.z <= zmax) or c.y > yspine - 0.02 * H:
                continue
            depth = len(b.parent_recursive)
            if best is None or depth < best[0]:
                best = (depth, b.name, _front_tip(strong))
        if best:
            found[side] = best[1:]
    return found


def _chest_parent(arm, scan, donors, A, side):
    """造骨的父骨:乳房区(高斯加权)承重最多的上半身系骨,helper 归到其上半身系祖先。"""
    B = arm.data.bones
    anc = {}
    for n in donors:
        b = B.get(n)
        while b and b.name not in _UPPER:
            b = b.parent
        anc[n] = b.name if b else None
    sig = _SIGMA * scan.H
    score = {}
    for _m, _i, p, gs in scan.verts:
        if p.x * side <= 0:
            continue
        d = (p - A).length
        if d > _CUT * sig:
            continue
        g = math.exp(-(d / sig) ** 2)
        for n, w in gs:
            c = anc.get(n)
            if c:
                score[c] = score.get(c, 0.0) + g * w
    return max(score, key=score.get) if score else None


def _split_weights(scan, donors, targets):
    """targets: {side: (骨名, 乳尖)}。每顶点按乳尖距离高斯 g 从胸部骨权重里分出 g 份给胸骨,
    两侧重叠处 g 之和封顶 1;中线 ±0.25|乳尖x| 内按侧平滑过渡,不跨到对侧。总权重不变。"""
    sig = _SIGMA * scan.H
    cut = _CUT * sig
    n = 0
    for m, idx, p, gs in scan.verts:
        dw = [(g, w) for g, w in gs if g in donors]
        if not dw:
            continue
        f = {}
        for side, (name, A) in targets.items():
            d = (p - A).length
            if d >= cut:
                continue
            r0 = max(0.25 * abs(A.x), 1e-4)
            g = math.exp(-(d / sig) ** 2) * _smooth((p.x * side + r0) / (2.0 * r0))
            if g > 1e-3:
                f[name] = g
        if not f:
            continue
        tot = sum(f.values())
        if tot > 1.0:
            f = {k: v / tot for k, v in f.items()}
            tot = 1.0
        keep = 1.0 - tot
        wd = sum(w for _, w in dw)
        for g, w in dw:
            vg = m.vertex_groups[g]
            if w * keep > 1e-4:
                vg.add([idx], w * keep, 'REPLACE')
            else:
                vg.remove([idx])
        for name, g in f.items():
            vg = m.vertex_groups.get(name) or m.vertex_groups.new(name=name)
            vg.add([idx], wd * g, 'ADD')
        n += 1
    return n


def _prepare(op, context):
    root = _find_root(context.active_object)
    if not root:
        op.report({'ERROR'}, "未找到 mmd 模型(请先完成转换)")
        return None
    from mmd_tools.core.model import Model
    model = Model(root)
    arm = model.armature()
    if not arm:
        op.report({'ERROR'}, "mmd 模型没有骨架")
        return None
    if context.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    context.view_layer.objects.active = arm
    if not any(n in arm.data.bones for n in _UPPER):
        op.report({'ERROR'}, "找不到上半身系骨")
        return None
    scan = _Scan(arm)
    if scan.H <= 0.0:
        op.report({'ERROR'}, "找不到蒙皮网格")
        return None
    return model, arm, scan, _chest_frame(arm)


def _add_bones(op, context, prep):
    """已有带权重胸骨的一侧直接复用;缺的一侧按几何乳尖造骨 + 分权重。
    左胸/右胸 名字已被别的骨占用(且没通过校验)时跳过该侧,绝不改动已有骨。"""
    model, arm, scan, frame = prep
    B = arm.data.bones
    existing = _find_existing(arm, scan, frame)
    msgs = [f"{'左' if s > 0 else '右'}: 复用 {n}" for s, (n, _A) in existing.items()]
    chest = _region_names(arm, frame[0])
    todo = {}
    for side, name in _SIDES:
        if side in existing:
            continue
        label = '左' if side > 0 else '右'
        if name in B and not B[name].get(_TAG):
            msgs.append(f"{label}: 已有 {name} 但权重不在乳房区,跳过")
            continue
        A = _apex(scan, chest, side, frame)
        if A is None:
            msgs.append(f"{label}: 未找到胸部区域,跳过")
            continue
        todo[side] = (name, A)
    if todo:
        skip = {n for n, _A in todo.values()}
        for n, _A in existing.values():
            skip |= {n} | {c.name for c in B[n].children_recursive}
        donors = {n for n in _region_names(arm, [u for u in _UPPER if u in B]) if n not in skip}
        plans = []
        for side, (name, A) in todo.items():
            parent = _chest_parent(arm, scan, donors, A, side)
            if parent:
                plans.append((side, name, A, parent))
        if plans:
            inv = arm.matrix_world.inverted()
            bpy.ops.object.mode_set(mode='EDIT')
            eb = arm.data.edit_bones
            for side, name, A, parent in plans:
                b = eb.get(name) or eb.new(name)
                b.head = inv @ (A + Vector((0.0, _DEPTH * scan.H, 0.0)))
                b.tail = inv @ A
                b.roll = 0.0
                b.parent = eb[parent]
                b.use_connect = False
                b.use_deform = True
                b[_TAG] = 1
            bpy.ops.object.mode_set(mode='OBJECT')
            for _s, name, _A, _p in plans:
                arm.pose.bones[name].lock_location = (True, True, True)
            nv = _split_weights(scan, donors, {s: (n, A) for s, n, A, _p in plans})
            for side, name, _A, parent in plans:
                msgs.append(f"{'左' if side > 0 else '右'}: 新建 {name}(挂 {parent})")
            msgs.append(f"分出权重 {nv} 顶点")
    return msgs


def _add_rigids(op, context, prep):
    model, arm, scan, frame = prep
    bones = _find_existing(arm, scan, frame)
    if not bones:
        op.report({'ERROR'}, "没有可用的胸骨,请先点「1. 胸骨 + 权重」")
        return None
    rbw = context.scene.rigidbody_world
    if rbw and rbw.enabled:
        rbw.enabled = False
    H = scan.H
    mw = arm.matrix_world
    existing = _existing_rigids()
    body_defs = {d[0]: d for d in _BODY_DEFS}
    n_rb = 0
    for name, apex in bones.values():
        if name in existing:
            continue
        b = arm.data.bones[name]
        head = mw @ b.head_local
        fwd = apex - head
        rigid = model.createRigidBody(
            shape_type=0, location=head + fwd * (_CENTER / _DEPTH), rotation=(0.0, 0.0, 0.0),
            size=(_RADIUS * H, 0.0, 0.0), dynamics_type=1,
            collision_group_number=15, collision_group_mask=[True] * 16,
            name=name, bone=name,
            mass=1.0, friction=0.5, linear_damping=0.5, angular_damping=0.5, bounce=0.0,
        )
        rigid[_TAG] = 1
        existing[name] = rigid
        # 锚:沿父链第一个有刚体的骨;到上半身系还没有就给它补身体刚体(实测胶囊)
        cur, anchor = b.parent, None
        while cur and anchor is None:
            anchor = existing.get(cur.name)
            if anchor is None and cur.name in _UPPER:
                break
            if anchor is None:
                cur = cur.parent
        if anchor is None and cur is None:
            cur = b.parent
        if anchor is None and cur is not None:
            anchor = (_make_body_rigid(model, arm, scan.meshes, body_defs[cur.name])
                      if cur.name in body_defs else _make_kinematic(model, arm, cur.name))
            existing[cur.name] = anchor
        if anchor is None:
            continue
        joint = model.createJoint(
            location=head, rotation=(0.0, 0.0, 0.0), rigid_a=anchor, rigid_b=rigid,
            maximum_location=(0.0, 0.0, 0.0), minimum_location=(0.0, 0.0, 0.0),
            maximum_rotation=(_ANG, _ANG, _ANG), minimum_rotation=(-_ANG, -_ANG, -_ANG),
            spring_linear=(0.0, 0.0, 0.0), spring_angular=(0.0, 0.0, 0.0),
            name=name,
        )
        joint[_TAG] = 1
        n_rb += 1
    # 创建刚体时 mmd_tools 会按需新建(启用的)rigidbody world;同布物理,导出前不能让它求值
    rbw = context.scene.rigidbody_world
    if rbw:
        rbw.enabled = False
    return [f"胸部刚体+关节 {n_rb} 组({'、'.join(n for n, _A in bones.values())})"]


class OBJECT_OT_add_breast_bones(bpy.types.Operator):
    """已有带权重的胸骨就复用;没有则按网格乳尖造 左胸/右胸,从胸部骨按距离分出权重(总量守恒)"""
    bl_idname = "object.add_breast_bones"
    bl_label = "胸骨 + 权重"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        prep = _prepare(self, context)
        if prep is None:
            return {'CANCELLED'}
        msgs = _add_bones(self, context, prep)
        self.report({'INFO'}, "胸骨: " + ";".join(msgs))
        return {'FINISHED'}


class OBJECT_OT_add_breast_rigids(bpy.types.Operator):
    """给胸骨建 MMD 刚体+关节(参考实测参数:球体、零碰撞、±10°、零弹簧)"""
    bl_idname = "object.add_breast_rigids"
    bl_label = "胸部刚体 + 关节"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        prep = _prepare(self, context)
        if prep is None:
            return {'CANCELLED'}
        msgs = _add_rigids(self, context, prep)
        if msgs is None:
            return {'CANCELLED'}
        self.report({'INFO'}, ";".join(msgs))
        return {'FINISHED'}


class OBJECT_OT_add_breast_physics(bpy.types.Operator):
    """胸部物理一键:胸骨 + 权重(按需)→ 刚体 + 关节"""
    bl_idname = "object.add_breast_physics"
    bl_label = "胸部物理(自动)"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        prep = _prepare(self, context)
        if prep is None:
            return {'CANCELLED'}
        msgs = _add_bones(self, context, prep)
        prep = _prepare(self, context)          # 权重已变,重扫
        more = _add_rigids(self, context, prep) if prep else None
        if more is None:
            return {'CANCELLED'}
        self.report({'INFO'}, ";".join(msgs + more))
        print("[breast] " + ";".join(msgs + more))
        return {'FINISHED'}


class OBJECT_OT_remove_breast_physics(bpy.types.Operator):
    """删除本工具建的胸部刚体/关节;本工具新建的胸骨连同权重并回父骨后删除(复用的胸骨不动)"""
    bl_idname = "object.remove_breast_physics"
    bl_label = "清除胸部物理"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        root = _find_root(context.active_object)
        if not root:
            self.report({'ERROR'}, "未找到 mmd 模型")
            return {'CANCELLED'}
        from mmd_tools.core.model import Model
        arm = Model(root).armature()
        if context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        objs = [o for o in bpy.data.objects if o.get(_TAG)]
        for o in objs:
            bpy.data.objects.remove(o, do_unlink=True)
        created = [b for b in arm.data.bones if b.get(_TAG)] if arm else []
        if created:
            parent_of = {b.name: (b.parent.name if b.parent else None) for b in created}
            for m in _skinned_meshes(arm):
                for name, pname in parent_of.items():
                    vg = m.vertex_groups.get(name)
                    if not vg:
                        continue
                    pvg = (m.vertex_groups.get(pname) or m.vertex_groups.new(name=pname)) if pname else None
                    for v in m.data.vertices:
                        for g in v.groups:
                            if g.group == vg.index:
                                if pvg and g.weight > 0.0:
                                    pvg.add([v.index], g.weight, 'ADD')
                                break
                    m.vertex_groups.remove(vg)
            context.view_layer.objects.active = arm
            bpy.ops.object.mode_set(mode='EDIT')
            eb = arm.data.edit_bones
            for name in parent_of:
                if name in eb:
                    eb.remove(eb[name])
            bpy.ops.object.mode_set(mode='OBJECT')
        self.report({'INFO'}, f"已清除胸部物理: 刚体/关节 {len(objs)} 个, 新建胸骨 {len(created)} 根")
        return {'FINISHED'}
