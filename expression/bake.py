# -*- coding: utf-8 -*-
"""Pose -> shape keys, and how far a PMX bone morph would be off.

Baking: with the whole armature at rest (constraints muted, other modifiers off, other shape keys at
0), pose the face bones, evaluate the skinned meshes and store evaluated-minus-rest as a shape key.
The skin does the work, with ALL the mesh's bone influences - so the shape is what the rig really
does, not what a 4-influence PMX would do.

PMX error: a PMX vertex keeps at most 4 bone weights (mmd_tools keeps the 4 largest and
renormalises), a MetaHuman face uses up to 12.  WeightModel runs linear blend skinning twice, with
all influences and with the PMX top 4, and returns the largest difference - the bulge a bone morph
would show in MMD.
"""
import bpy
import numpy as np
from mathutils import Matrix


def coords(obj):
    """Evaluated vertex positions (object space) of a mesh."""
    bpy.context.view_layer.update()
    ev = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    me = ev.to_mesh()
    co = np.empty(len(me.vertices) * 3)
    me.vertices.foreach_get("co", co)
    ev.to_mesh_clear()
    return co.reshape(-1, 3)


def basis_coords(obj):
    """Vertex positions of the reference shape key (or the mesh, when it has no keys)."""
    keys = obj.data.shape_keys
    src = keys.reference_key.data if keys else obj.data.vertices
    co = np.empty(len(src) * 3)
    src.foreach_get("co", co)
    return co.reshape(-1, 3)


class RestState:
    """Armature at rest, constraints muted, only Armature modifiers live, all shape keys at 0.
    Everything goes back on exit (pose, pose position, constraints, modifiers, key values)."""

    def __init__(self, arm, meshes):
        self.arm, self.meshes = arm, meshes
        self.posed = set()

    def __enter__(self):
        self.pose = {pb.name: pb.matrix_basis.copy() for pb in self.arm.pose.bones}
        self.position = self.arm.data.pose_position
        self.muted = [c for pb in self.arm.pose.bones for c in pb.constraints if not c.mute]
        self.mods = [(m, m.show_viewport) for o in self.meshes for m in o.modifiers if m.type != "ARMATURE"]
        self.keys = {o.name: {k.name: k.value for k in o.data.shape_keys.key_blocks} if o.data.shape_keys else {}
                     for o in self.meshes}
        self.show_only = {o.name: o.show_only_shape_key for o in self.meshes}
        self.arm.data.pose_position = "POSE"
        for c in self.muted:
            c.mute = True
        for m, _v in self.mods:
            m.show_viewport = False
        for o in self.meshes:
            o.show_only_shape_key = False
            if o.data.shape_keys:
                for k in o.data.shape_keys.key_blocks:
                    k.value = 0.0
        for pb in self.arm.pose.bones:
            pb.matrix_basis = Matrix()
        return self

    def set_pose(self, pose):
        """pose = {bone: (location, quaternion[, scale])}; every other bone at rest."""
        bones = self.arm.pose.bones
        for name in self.posed:
            if name in bones:
                bones[name].matrix_basis = Matrix()
        self.posed = set()
        for name, value in pose.items():
            pb = bones.get(name)
            if pb is not None:
                pb.matrix_basis = Matrix.LocRotScale(value[0], value[1], value[2] if len(value) > 2 else None)
                self.posed.add(name)
        bpy.context.view_layer.update()

    def __exit__(self, *exc):
        for pb in self.arm.pose.bones:
            if pb.name in self.pose:
                pb.matrix_basis = self.pose[pb.name]
        self.arm.data.pose_position = self.position
        for c in self.muted:
            c.mute = False
        for m, visible in self.mods:
            m.show_viewport = visible
        for o in self.meshes:
            o.show_only_shape_key = self.show_only[o.name]
            if o.data.shape_keys:
                old = self.keys.get(o.name, {})
                for k in o.data.shape_keys.key_blocks:
                    k.value = old.get(k.name, 0.0)
        bpy.context.view_layer.update()
        return False


def disconnect(arm, names):
    """Connected bones cannot translate in Blender (MMD does not care) - free the ones a pose moves."""
    connected = [n for n in names if n in arm.data.bones and arm.data.bones[n].use_connect]
    if not connected:
        return 0
    view_layer = bpy.context.view_layer
    active = view_layer.objects.active
    if bpy.context.object is not None and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    hidden = arm.hide_get()
    arm.hide_set(False)
    view_layer.objects.active = arm
    bpy.ops.object.mode_set(mode="EDIT")
    for n in connected:
        arm.data.edit_bones[n].use_connect = False
    bpy.ops.object.mode_set(mode="OBJECT")
    arm.hide_set(hidden)
    view_layer.objects.active = active
    return len(connected)


def skinned_meshes(arm, meshes, bone_names):
    """Meshes with a vertex group for any of these bones."""
    wanted = set(bone_names)
    return [m for m in meshes if any(g.name in wanted for g in m.vertex_groups)]


def mm_per_unit(arm):
    """Millimetres per Blender unit, guessed from the rig's height (UE / XPS-cm rigs are ~170 tall)."""
    zs = [b.head_local.z for b in arm.data.bones] + [b.tail_local.z for b in arm.data.bones]
    height = (max(zs) - min(zs)) * max(arm.matrix_world.to_scale()) if zs else 1.0
    return 10.0 if height > 20.0 else 1000.0


class WeightModel:
    """Linear blend skinning of one mesh, with all influences and with the PMX top 4."""

    def __init__(self, arm, mesh):
        self.arm, self.mesh = arm, mesh
        deform = {b.name for b in arm.data.bones if b.use_deform}
        group_bone = {g.index: g.name for g in mesh.vertex_groups if g.name in deform}
        self.bones = sorted(set(group_bone.values()))
        bone_index = {n: i for i, n in enumerate(self.bones)}
        rows = []
        for v in mesh.data.vertices:
            infl = sorted(((bone_index[group_bone[g.group]], g.weight) for g in v.groups
                           if g.group in group_bone and g.weight > 0.0), key=lambda x: -x[1])
            rows.append(infl)
        over = [i for i, r in enumerate(rows) if len(r) > 4]
        self.over4 = np.array(over, dtype=np.int64)
        self.max_influences = max((len(r) for r in rows), default=0)
        k = max((len(rows[i]) for i in over), default=4)
        n = len(over)
        self.idx = np.zeros((n, k), dtype=np.int64)
        self.w_full = np.zeros((n, k))
        for r, i in enumerate(over):
            for c, (b, w) in enumerate(rows[i]):
                self.idx[r, c], self.w_full[r, c] = b, w
        self.w_full /= np.maximum(self.w_full.sum(1, keepdims=True), 1e-12)
        self.w_top4 = self.w_full.copy()
        self.w_top4[:, 4:] = 0.0
        self.w_top4 /= np.maximum(self.w_top4.sum(1, keepdims=True), 1e-12)
        co = basis_coords(mesh)
        self.rest = co[self.over4] if n else np.zeros((0, 3))

    def error(self):
        """Largest |all influences - top 4| over the vertices with more than 4 (object units), for
        the armature's current pose."""
        if not len(self.over4):
            return 0.0
        to_arm = self.arm.matrix_world.inverted() @ self.mesh.matrix_world
        back = to_arm.inverted()
        mats = np.empty((len(self.bones), 4, 4))
        pose = self.arm.pose.bones
        for i, name in enumerate(self.bones):
            pb = pose[name]
            m = back @ pb.matrix @ pb.bone.matrix_local.inverted() @ to_arm
            mats[i] = np.array(m)
        h = np.c_[self.rest, np.ones(len(self.rest))]
        moved = np.einsum("nkij,nj->nki", mats[self.idx][:, :, :3, :], h)     # (n, k, 3): each influence
        full = np.einsum("nk,nki->ni", self.w_full, moved)
        top4 = np.einsum("nk,nki->ni", self.w_top4, moved)
        return float(np.linalg.norm(full - top4, axis=1).max())
