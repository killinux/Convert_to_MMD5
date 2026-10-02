# -*- coding: utf-8 -*-
"""Full skin weights of a cooked UE5 skeletal mesh, read from the package bytes, put back on a mesh
that UE Viewer exported with only 4 influences per vertex.

UE Viewer keeps the 4 largest influences; a MetaHuman face has up to 12 (Vindictus Fiona: 418000
influences over 66305 render vertices), so every expression built on the truncated weights comes
out lumpy.  The package is found by signature, no usmap needed (UE 5.x zen package, LOD0):

  name map        zen summary (5.3: 52 bytes, 5.2: 44) + name batch
  ref skeleton    TArray<FMeshBoneInfo> = count, (FName index, number 0, parent) - root parent -1
  sections        ... bCastShadow, bVisibleInRayTracing, BaseVertexIndex, ClothMappingDataLODs (0),
                  BoneMap (count + u16), NumVertices, MaxBoneInfluences - chained from vertex 0
  positions       Stride 12, N, bulk(12, N), float xyz
  weights         bVariableBonesPerVertex 1, MaxBoneInfluences, NumInfluences, N, bUse16BitBoneIndex,
                  bUse16BitBoneWeight, bulk(1, count); per vertex k indices then k weights (sum 255)
  lookup          2 strip bytes, N, bulk(4, N): u32 = offset << 8 | k

Ported from ripper_tpose's expression_kit; restore() also takes a rename map (game bone -> rig
bone) for rigs a conversion renamed (eyes -> 左目 / 目.L, jaw -> head jaw ...).
"""
import collections
import struct

import numpy as np


def _u32_views(buf):
    """uint32 views of the buffer at the 4 byte alignments: [(alignment, array)]."""
    return [(a, np.frombuffer(buf, dtype="<u4", count=(len(buf) - a) // 4, offset=a)) for a in range(4)]


def _names(buf):
    for summary in (52, 44):                              # UE 5.3+, 5.2
        try:
            pos = summary
            has_ver, = struct.unpack_from("<I", buf, 0)
            if has_ver:
                pos += 16
                ncv, = struct.unpack_from("<i", buf, pos)
                pos += 4 + 20 * ncv
            count, nbytes = struct.unpack_from("<II", buf, pos)
            if not (0 < count < 200000):
                continue
            pos += 16 + 8 * count
            headers = [struct.unpack_from(">H", buf, pos + 2 * i)[0] for i in range(count)]
            pos += 2 * count
            names = []
            for h in headers:
                n = h & 0x7FFF
                if h >> 15:
                    names.append(buf[pos:pos + 2 * n].decode("utf-16-le"))
                    pos += 2 * n
                else:
                    names.append(buf[pos:pos + n].decode("utf-8"))
                    pos += n
            return names
        except (struct.error, UnicodeDecodeError):
            continue
    raise ValueError("not a UE5 zen package (no name map)")


def _ref_skeleton(buf, views, names):
    best = None
    for a, arr in views:
        cond = ((arr[3:-3] == 0xFFFFFFFF) & (arr[2:-4] == 0) & (arr[5:-1] == 0) & (arr[6:] == 0)
                & (arr[:-6] >= 20) & (arr[:-6] < 5000) & (arr[1:-5] < len(names)) & (arr[4:-2] < len(names)))
        for i in np.nonzero(cond)[0]:
            off = a + 4 * int(i)
            c = int(arr[i])
            if off + 4 + 12 * c > len(buf):
                continue
            e = np.frombuffer(buf, dtype="<i4", count=3 * c, offset=off + 4).reshape(c, 3)
            if (e[:, 0] < 0).any() or (e[:, 0] >= len(names)).any() or (e[:, 1] != 0).any():
                continue
            if e[0, 2] != -1 or (e[1:, 2] < 0).any() or (e[1:, 2] >= np.arange(1, c)).any():
                continue
            if best is None or c > best[1]:
                best = (off, c, [names[k] for k in e[:, 0]])
    if best is None:
        raise ValueError("no reference skeleton found")
    return best[2]


def _positions(views):
    for a, arr in views:
        cond = (arr[:-3] == 12) & (arr[2:-1] == 12) & (arr[1:-2] == arr[3:]) & (arr[1:-2] > 100) & (arr[1:-2] < 5000000)
        hits = np.nonzero(cond)[0]
        if len(hits):
            i = int(hits[0])
            return a + 4 * i + 16, int(arr[i + 1])
    raise ValueError("no position buffer found")


def _weights(views, n):
    for a, arr in views:
        cond = ((arr[:-8] == 1) & (arr[1:-7] >= 1) & (arr[1:-7] <= 16) & (arr[3:-5] == n) & (arr[4:-4] <= 1)
                & (arr[5:-3] <= 1) & (arr[6:-2] == 1))
        for i in np.nonzero(cond)[0]:
            isz = 2 if arr[i + 4] else 1
            wsz = 2 if arr[i + 5] else 1
            if int(arr[i + 7]) == int(arr[i + 2]) * (isz + wsz):
                return {"data": a + 4 * int(i) + 32, "size": int(arr[i + 7]), "max": int(arr[i + 1]),
                        "influences": int(arr[i + 2]), "isz": isz, "wsz": wsz}
    raise ValueError("no variable-influence skin weight buffer for %d vertices (fixed 4/8 influences "
                     "meshes are exported complete, nothing to restore)" % n)


def _section_candidates(buf, views, base, remaining, nbones):
    out = []
    for a, arr in views:
        cond = ((arr[2:-2] == base) & (arr[3:-1] == 0) & (arr[:-4] <= 1) & (arr[1:-3] <= 1)
                & (arr[4:] >= 1) & (arr[4:] <= nbones))
        for i in np.nonzero(cond)[0]:
            off = a + 4 * int(i) + 16                          # BoneMap count
            c = int(arr[i + 4])
            if off + 4 + 2 * c + 8 > len(buf):
                continue
            bm = np.frombuffer(buf, dtype="<u2", count=c, offset=off + 4)
            nv, mi = struct.unpack_from("<Ii", buf, off + 4 + 2 * c)
            if (bm < nbones).all() and len(set(bm.tolist())) == c and 1 <= nv <= remaining and 1 <= mi <= 16:
                out.append((base, nv, bm.astype(np.int64)))
    return out


def _sections(buf, views, n, nbones):
    """Render sections chained by BaseVertexIndex from 0 to n: [(base, count, bonemap)].
    Tries every candidate at each step, so a look-alike byte pattern cannot derail the chain."""
    def chain(base, depth):
        if base == n:
            return []
        if depth > 64:
            return None
        for cand in _section_candidates(buf, views, base, n - base, nbones):
            rest = chain(base + cand[1], depth + 1)
            if rest is not None:
                return [cand] + rest
        return None

    out = chain(0, 0)
    if out is None:
        raise ValueError("render sections do not chain from vertex 0 to %d" % n)
    return out


def read_package(buf):
    """LOD0 of a cooked UE5 skeletal mesh package: positions (N, 3) in UE units and, per render
    vertex, [(bone name, weight 0-255)]."""
    views = _u32_views(buf)
    names = _names(buf)
    bones = _ref_skeleton(buf, views, names)
    pos_off, n = _positions(views)
    positions = np.frombuffer(buf, dtype="<f4", count=3 * n, offset=pos_off).reshape(-1, 3).astype(np.float64)
    w = _weights(views, n)
    look = None
    end = w["data"] + w["size"]
    for off in range(end, end + 64):
        if struct.unpack_from("<III", buf, off) == (n, 4, n):
            look = off + 12
            break
    if look is None:
        raise ValueError("no skin weight lookup buffer behind the weights")
    lookup = np.frombuffer(buf, dtype="<u4", count=n, offset=look).astype(np.int64)
    sections = _sections(buf, views, n, len(bones))
    data = np.frombuffer(buf, dtype=np.uint8, count=w["size"], offset=w["data"])
    idx_type = "<u2" if w["isz"] == 2 else "u1"
    influences, bad = [], 0
    for base, count, bonemap in sections:
        for v in range(base, base + count):
            k = int(lookup[v] & 0xFF)
            o = int(lookup[v] >> 8)
            idx = np.frombuffer(data[o:o + w["isz"] * k].tobytes(), dtype=idx_type)
            wts = data[o + w["isz"] * k:o + (w["isz"] + w["wsz"]) * k]
            if w["wsz"] == 2:
                wts = np.frombuffer(wts.tobytes(), dtype="<u2") / 257.0
            if abs(int(np.sum(wts)) - 255) > 1:
                bad += 1
            influences.append([(bones[bonemap[i]], int(x)) for i, x in zip(idx, wts) if x > 0])
    if bad > n // 100:
        raise ValueError("%d of %d vertices have weights that do not sum to 255 - layout not understood" % (bad, n))
    counts = collections.Counter(len(x) for x in influences)
    return {"positions": positions, "influences": influences, "bones": bones, "sections": len(sections),
            "max_influences": w["max"], "total_influences": w["influences"], "per_vertex": dict(sorted(counts.items()))}


def restore(mesh_obj, pkg, force=False, rename=None):
    """Put the package's weights on ``mesh_obj`` by vertex position.  A vertex is rewritten only
    when its current weights are exactly the package's 4 largest renormalised (what UE Viewer left),
    so hand-edited areas stay untouched - ``force`` rewrites every matched vertex.

    Also works on a converted copy of the model: the mesh may be one piece of the package (an XPS
    export splits eyes / teeth / lashes into meshes of their own), and game bones the rig renamed are
    found by weight overlap on the matched vertices (``head`` -> 頭, ``FACIAL_L_Eye`` -> 目.L ...);
    ``rename`` (game -> rig) forces pairs.  Vertices that still need an unknown bone are left alone."""
    from mathutils import kdtree

    rename = dict(rename or {})
    me = mesh_obj.data
    n = len(me.vertices)
    co = np.empty(n * 3)
    me.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3)
    kd = kdtree.KDTree(n)
    for i, c in enumerate(co):
        kd.insert(c, i)
    kd.balance()
    P0 = pkg["positions"]
    sample = P0[:: max(1, len(P0) // 4000)]
    best = None
    for scale in (1.0, 0.01, 100.0):
        tol = 1e-3 * scale
        for swap in (False, True):
            for sx in (1, -1):
                for sy in (1, -1):
                    p = (sample[:, [1, 0, 2]] if swap else sample) * np.array([sx, sy, 1.0]) * scale
                    hits = sum(1 for q in p if kd.find(q)[2] < tol)
                    if best is None or hits > best[0]:
                        best = (hits, scale, swap, sx, sy)
    hits, scale, swap, sx, sy = best
    if hits < 5:
        raise ValueError("no package vertex sits on a vertex of %s - wrong mesh or package?" % mesh_obj.name)
    P = (P0[:, [1, 0, 2]] if swap else P0) * np.array([sx, sy, 1.0]) * scale
    tol = 1e-3 * scale
    per_vert, unmatched = {}, 0
    for v, q in enumerate(P):
        found = kd.find_range(q, tol)
        if not found:
            unmatched += 1
            continue
        for _c, bi, _d in found:
            per_vert.setdefault(bi, pkg["influences"][v])
    if len(per_vert) < 0.5 * n:
        raise ValueError("only %d of %d vertices of %s sit on a package vertex - wrong mesh or package?"
                         % (len(per_vert), n, mesh_obj.name))
    groups = {g.index: g.name for g in mesh_obj.vertex_groups}
    arm = mesh_obj.find_armature()
    rig = set(arm.data.bones.keys()) if arm is not None else set(groups.values())
    game_names = set(pkg["bones"])
    current = {bi: {groups[g.group]: g.weight for g in me.vertices[bi].groups if g.weight > 0.0} for bi in per_vert}

    # game bones the rig does not have: pair them with the rig-only bone that carries their weight
    unknown = {b for infl in per_vert.values() for b, _w in infl if rename.get(b, b) not in rig}
    overlap = collections.defaultdict(collections.Counter)
    for bi, infl in per_vert.items():
        rig_only = {r: w for r, w in current[bi].items() if r not in game_names}
        if not rig_only:
            continue
        for b, w in infl:
            if b in unknown:
                for r, cw in rig_only.items():
                    overlap[b][r] += min(w / 255.0, cw)
    guessed = {b: c.most_common(1)[0][0] for b, c in overlap.items() if c and c.most_common(1)[0][1] > 0.5}
    rename = dict(guessed, **rename)

    mapped, unmapped_bones, unmapped_verts = {}, set(), 0
    for bi, infl in per_vert.items():
        acc = collections.defaultdict(int)
        missing = [b for b, _w in infl if rename.get(b, b) not in rig]
        if missing:
            unmapped_bones.update(missing)
            unmapped_verts += 1
            continue
        for b, w in infl:
            acc[rename.get(b, b)] += w
        mapped[bi] = tuple(sorted(acc.items()))
    rewrite, complete, kept = [], 0, 0
    for bi, infl in mapped.items():
        cur = current[bi]
        game = dict(infl)
        if not force and len(infl) <= 4 and set(cur) == set(game):
            complete += 1
            continue
        # UE Viewer's cut: a subset of the game's bones (the largest ones; on ties either may stay),
        # renormalised.  Anything else was edited after the export (e.g. a neck blend) - leave it.
        total = float(sum(game.get(b, 0) for b in cur))
        truncated = (bool(cur) and set(cur) <= set(game) and total > 0
                     and all(abs(cur[b] - game[b] / total) < 0.03 for b in cur))
        if force or truncated:
            rewrite.append(bi)
        else:
            kept += 1
    need = {b for bi in rewrite for b, _w in mapped[bi]}
    for b in sorted(need):
        if b not in mesh_obj.vertex_groups:
            mesh_obj.vertex_groups.new(name=b)
    if rewrite:
        for vg in mesh_obj.vertex_groups:
            vg.remove(rewrite)
        batches = collections.defaultdict(list)
        for bi in rewrite:
            for b, w in mapped[bi]:
                batches[(b, w)].append(bi)
        for (b, w), verts in batches.items():
            mesh_obj.vertex_groups[b].add(verts, min(w, 255) / 255.0, "REPLACE")
    after = collections.Counter(sum(1 for g in v.groups if g.weight > 0) for v in me.vertices)
    return {"axis": {"scale": scale, "swap_xy": swap, "sign_x": sx, "sign_y": sy},
            "matched_vertices": len(per_vert), "vertices": n, "package_vertices_unmatched": unmatched,
            "rewritten": len(rewrite), "already_complete": complete, "left_alone": kept,
            "renamed": {b: r for b, r in rename.items() if b in {x for i in per_vert.values() for x, _w in i}},
            "unmapped_bones": sorted(unmapped_bones), "unmapped_vertices": unmapped_verts,
            "max_influences_now": max(after), "over_4_now": sum(c for k, c in after.items() if k > 4)}
