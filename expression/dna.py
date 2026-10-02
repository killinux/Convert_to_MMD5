# -*- coding: utf-8 -*-
"""MetaHuman DNA v2.1: read it and evaluate expressions the way RigLogic does (numpy only).

A MetaHuman face has no expression shape keys at LOD1+: RigLogic moves the ~600 ``FACIAL_*``
joints from ~270 raw controls (``CTRL_expressions.eyeBlinkL`` ...).  The rules live in the DNA:
raw controls -> PSDs (products of inputs, clamped 0..1) -> joint deltas (one dense matrix per
joint group) -> local transform = neutral translation + delta (cm), neutral rotation * delta
rotation (Euler XYZ degrees, R = Rz Ry Rx) -> forward kinematics.

Ported from ripper_tpose's expression_kit (the same reader + evaluator); it also applies the
joint scale outputs (the pupils).  Checked on Vindictus Fiona: the neutral skeleton lands on the
exported rig's 620 facial joints with a mean error of 0.38 mm.
"""
import struct

import numpy as np

SIGNATURE = b"DNA\x00\x02\x00\x01"          # generation 2, version 1


def find_stream(buf):
    """The DNA stream inside a larger buffer (a cooked face package keeps it in its DNAAsset)."""
    start = buf.find(SIGNATURE)
    if start < 0:
        raise ValueError("no MetaHuman DNA v2.1 stream in this data")
    end = buf.find(b"AND", start)
    return buf[start:end + 3]


class _Reader:
    def __init__(self, data, pos=0):
        self.b, self.p = data, pos

    def u16(self):
        value, = struct.unpack_from(">H", self.b, self.p)
        self.p += 2
        return value

    def u32(self):
        value, = struct.unpack_from(">I", self.b, self.p)
        self.p += 4
        return value

    def u16s(self):
        n = self.u32()
        out = np.frombuffer(self.b, dtype=">u2", count=n, offset=self.p).astype(np.int64)
        self.p += 2 * n
        return out

    def f32s(self):
        n = self.u32()
        out = np.frombuffer(self.b, dtype=">f4", count=n, offset=self.p).astype(np.float64)
        self.p += 4 * n
        return out

    def text(self):
        n = self.u32()
        out = self.b[self.p:self.p + n].decode("utf-8", "replace")
        self.p += n
        return out

    def texts(self):
        return [self.text() for _ in range(self.u32())]


def read(dna):
    """Parse a DNA v2.1 stream (bytes) into plain dicts / numpy arrays."""
    if not dna.startswith(SIGNATURE):
        dna = find_stream(dna)
    r = _Reader(dna, 7)
    names = ("descriptor", "definition", "behavior", "controls", "joints", "blendShapeChannels",
             "animatedMaps", "geometry")
    sec = dict(zip(names, (r.u32() for _ in names)))
    r.p = sec["descriptor"]
    desc = {"name": r.text(), "archetype": r.u16(), "gender": r.u16(), "age": r.u16()}
    desc["metadata"] = [(r.text(), r.text()) for _ in range(r.u32())]
    desc["translationUnit"], desc["rotationUnit"] = r.u16(), r.u16()     # 0 = cm, 0 = degrees
    desc["coordinateSystem"] = (r.u16(), r.u16(), r.u16())                # (0, 2, 4) = right, up, front
    desc["lodCount"], desc["maxLOD"] = r.u16(), r.u16()
    desc["complexity"], desc["dbName"] = r.text(), r.text()

    r.p = sec["definition"]

    def lod_mapping():
        lods = r.u16s()
        return lods, [r.u16s() for _ in range(r.u32())]

    df = {"lodJoint": lod_mapping(), "lodBlend": lod_mapping(), "lodAnim": lod_mapping(), "lodMesh": lod_mapping()}
    df["gui"], df["raw"], df["joints"] = r.texts(), r.texts(), r.texts()
    df["blendShapes"], df["animatedMaps"], df["meshes"] = r.texts(), r.texts(), r.texts()
    df["meshBlend"] = (r.u16s(), r.u16s())
    df["hierarchy"] = r.u16s()
    df["neutralT"] = np.stack([r.f32s(), r.f32s(), r.f32s()], 1)
    df["neutralR"] = np.stack([r.f32s(), r.f32s(), r.f32s()], 1)

    r.p = sec["controls"]
    controls = {"psdCount": r.u16()}
    controls["conditionals"] = [r.u16s(), r.u16s(), r.f32s(), r.f32s(), r.f32s(), r.f32s()]
    controls["psd"] = {"rows": r.u16s(), "cols": r.u16s(), "values": r.f32s()}
    r.p = sec["joints"]
    joints = {"rowCount": r.u16(), "colCount": r.u16(), "groups": []}
    for _ in range(r.u32()):
        joints["groups"].append({"lods": r.u16s(), "inputs": r.u16s(), "outputs": r.u16s(),
                                 "values": r.f32s(), "joints": r.u16s()})
    return {"descriptor": desc, "definition": df, "controls": controls, "joints": joints, "sections": sec}


def euler_xyz(degrees):
    """Rotation matrices for rows of (x, y, z) degrees, R = Rz @ Ry @ Rx (Maya's xyz order)."""
    a, b, c = np.radians(np.atleast_2d(degrees)).T
    ca, sa, cb, sb, cc, sc = np.cos(a), np.sin(a), np.cos(b), np.sin(b), np.cos(c), np.sin(c)
    m = np.empty((len(a), 3, 3))
    m[:, 0] = np.stack([cc * cb, cc * sb * sa - sc * ca, cc * sb * ca + sc * sa], 1)
    m[:, 1] = np.stack([sc * cb, sc * sb * sa + cc * ca, sc * sb * ca - cc * sa], 1)
    m[:, 2] = np.stack([-sb, cb * sa, cb * ca], 1)
    return m


class DnaFace:
    def __init__(self, dna):
        data = read(dna)
        df, controls = data["definition"], data["controls"]
        self.descriptor = data["descriptor"]
        self.blend_shape_channels = len(df["blendShapes"])
        self.joints = list(df["joints"])
        self.parent = df["hierarchy"]
        self.neutral_t, self.neutral_r = df["neutralT"], df["neutralR"]
        self.raw = [name.split(".", 1)[-1] for name in df["raw"]]
        self.raw_index = {name: i for i, name in enumerate(self.raw)}
        self.psd_count = controls["psdCount"]
        psd = controls["psd"]
        self.psd = (psd["rows"], psd["cols"], psd["values"])
        self.groups = [(g["inputs"], g["outputs"], g["values"].reshape(len(g["outputs"]), len(g["inputs"])))
                       for g in data["joints"]["groups"] if len(g["inputs"]) and len(g["outputs"])]
        depth = np.zeros(len(self.joints), dtype=int)
        for j in range(len(self.joints)):
            k = j
            while self.parent[k] != k:
                k = self.parent[k]
                depth[j] += 1
        self.order = list(np.argsort(depth, kind="stable"))
        self.neutral_rot = euler_xyz(self.neutral_r)
        self.rest = self.fk(np.zeros((len(self.joints), 3)), np.zeros((len(self.joints), 3)))

    def inputs(self, controls):
        values = np.zeros(len(self.raw) + self.psd_count)
        for name, value in controls.items():
            if name not in self.raw_index:
                raise KeyError("no raw control %r in this DNA" % name)
            values[self.raw_index[name]] = value
        product = np.ones(self.psd_count)
        rows, cols, weights = self.psd
        for row, col, weight in zip(rows, cols, weights):
            product[row - len(self.raw)] *= values[col] * weight
        values[len(self.raw):] = np.clip(product, 0.0, 1.0)
        return values

    def deltas(self, controls):
        """(translation cm, rotation degrees, scale) deltas per joint."""
        values = self.inputs(controls)
        out = np.zeros(len(self.joints) * 9)
        for ins, outs, matrix in self.groups:
            np.add.at(out, outs, matrix @ values[ins])
        out = out.reshape(-1, 9)
        return out[:, 0:3], out[:, 3:6], out[:, 6:9]

    def fk(self, d_t, d_r, d_s=None):
        """World matrices; d_s = scale deltas (neutral scale 1).  Fiona's DNA scales only the two
        pupil joints (eyePupilNarrow -0.7, eyePupilWide +1.1) - that is how 瞳小 exists at all."""
        local = np.zeros((len(self.joints), 4, 4))
        local[:, :3, :3] = self.neutral_rot @ euler_xyz(d_r)
        if d_s is not None:
            local[:, :3, :3] *= (1.0 + d_s)[:, None, :]           # R @ diag(1 + s): scale in the joint's frame
        local[:, :3, 3] = self.neutral_t + d_t
        local[:, 3, 3] = 1.0
        world = np.zeros_like(local)
        for j in self.order:
            p = self.parent[j]
            world[j] = local[j] if p == j else world[p] @ local[j]
        return world

    def posed(self, controls):
        """World (DNA space, cm) joint matrices for these raw control values."""
        d_t, d_r, d_s = self.deltas(controls)
        return self.fk(d_t, d_r, d_s)


def fit_similarity(src, dst):
    """dst ~ s * R @ src + t for matched point rows (least squares, reflection allowed)."""
    cs, cd = src.mean(0), dst.mean(0)
    u, sv, vt = np.linalg.svd((src - cs).T @ (dst - cd))
    rot = (u @ vt).T
    scale = sv.sum() / ((src - cs) ** 2).sum()
    shift = cd - scale * rot @ cs
    err = np.linalg.norm((scale * (rot @ src.T)).T + shift - dst, axis=1)
    return scale, rot, shift, err
