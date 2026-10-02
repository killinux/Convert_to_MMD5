"""表情(脸骨):给脸靠骨骼驱动、没有形态键的模型算 MMD 标准表情的骨骼姿势。

这里只管「算」:表情引擎(expression/)把它当作「脸骨」来源(sources.FaceBoneSource),
再按选项写成 PMX 骨骼 morph 或烘焙成顶点 morph;按钮在 expression/ui.py(第 3 页「表情」)。

适用 UE MetaHuman 式脸骨:`FACIAL_C_FacialRoot` 下几百根 FACIAL_[LCR]_*(blender2xps 从 UE 导出的
XPS 常见;下巴骨 FACIAL_C_Jaw 可能已被改名成 XPS 标准名 `head jaw`)。这种脸骨很密,一个表情要
同时动几十上百根骨,所以不逐骨写配方,而是每个表情由几个「场」叠加,按骨头位置算出每根骨的目标变换:

- 眼皮:眼皮父骨就在眼球中心,整片绕眼球中心的横轴转。闭合角 = 上/下眼睑边缘骨的仰角差(按横向
  位置插值),再按眼皮皮肤权重里「不跟着转」的份额补足。上眼皮上方的褶/眼窝、下眼袋按高度递减跟随。
- 下巴:绕下巴骨头部的横轴转。下巴/下唇子树全跟;嘴角、脸颊、法令纹、咬肌、颌下这些平挂在脸根下
  的骨,按到「跟下巴的骨」和「连颅骨的骨」的最近距离插值跟随比例。
- 嘴角/嘴唇:平移场。唇上的骨按到中线的横向距离渐变(中线不动、嘴角全动),唇外按到嘴角的距离衰减。
- 眉:眉骨按沿眉的横向位置在 内/中/外 三段量之间插值;额头皮肤/上眼皮褶按到眉骨的距离衰减跟随。
- 脸颊(笑):颧部一带按到颧部中心的距离衰减上提。
每根骨的目标变换(骨架空间)换成相对父骨的局部偏移(位置 + 四元数)写进 mmd_tools 的骨骼 morph。
轴心本身就在骨头上的(眼皮父骨、下巴骨)只带旋转,MMD 里按权重插值时走的是圆弧,半闭时眼皮
不会陷进眼球。平移量以两眼间距为单位,角度由骨位/权重量出。
"""

import math
import re

from mathutils import Matrix, Vector

from .skirt import _skinned_meshes

_ROOT = "FACIAL_C_FacialRoot"
_NECK_UP = "FACIAL_C_Neck2Root"     # MetaHuman 上颈皮肤骨的根(挂在 首 下)
_NECK_LOW = "FACIAL_C_Neck1Root"
_EYEBALLS = ("左目", "右目", "FACIAL_L_Eye", "FACIAL_R_Eye")
# 不参与任何表情:发际线/鬓角/耳/颅骨/太阳穴
_STATIC_STEMS = {"Hair", "HairA", "HairB", "HairC", "Sideburn", "Ear", "Skull", "Temple"}
_CHEEK_STEMS = {"CheekInner", "CheekOuter", "CheekLower", "NasolabialBulge", "NasolabialB",
                "NasolabialF", "NasolabialFurrow", "EyesackLower"}
# 张嘴时按距离插值跟随下巴的(下巴/下唇子树之外、又不算连在颅骨上的)
_JAW_FREE_STEMS = _CHEEK_STEMS | {"LipCorner", "Masseter", "JawBulge", "JawRecess", "UnderChin"}
# 嘴角场在唇外衰减作用的范围
_CORNER_NEAR_STEMS = _JAW_FREE_STEMS | {"ChinS", "Chin", "ChinSide", "Jawline", "MouthInteriorUpper",
                                        "MouthInteriorLower"}
_UPPER_LIP_SKIN = {"LipUpperSkin", "LipUpperOuterSkin"}
_LOWER_LIP_SKIN = {"LipLowerSkin", "LipLowerOuterSkin"}
_BROW_NEAR_STEMS = {"Forehead", "ForeheadSkin", "ForeheadInSkin", "ForeheadMidSkin", "ForeheadOutSkin",
                    "NoseBridge", "EyesackUpper", "EyelidUpperFurrow"}
_LID_UP2_STEMS = {"EyelidUpperFurrow", "EyesackUpper"}
_LID_LOW2_STEMS = {"EyesackLower"}
_K_MAX = 1.6                        # 权重补偿上限(不跟转份额 >37% 的眼皮本身就合不上)
_EPS_LOC = 1e-7
_EPS_ROT = 1e-5


def _stem(name):
    """FACIAL_L_12IPV_NasolabialB13 -> NasolabialB;非 FACIAL_ 骨原样返回。"""
    m = re.match(r"FACIAL_[LCR]_(?:12IPV_)?(.*?)\d*$", name)
    return m.group(1) if m else name


def _side(name):
    m = re.match(r"FACIAL_([LCR])_", name)
    if m:
        return m.group(1)
    return "L" if name.startswith("左") else "R" if name.startswith("右") else "C"


def _smooth(t):
    t = min(1.0, max(0.0, t))
    return t * t * (3.0 - 2.0 * t)


def _rot(pivot, axis, angle):
    return Matrix.Translation(pivot) @ Matrix.Rotation(angle, 4, axis) @ Matrix.Translation(-pivot)


def _interp(samples, x):
    """samples: [(x, y)] 按 x 升序;线性插值,两端外推取端值。"""
    if x <= samples[0][0]:
        return samples[0][1]
    for (x0, y0), (x1, y1) in zip(samples, samples[1:]):
        if x <= x1:
            return y0 + (y1 - y0) * (x - x0) / max(x1 - x0, 1e-9)
    return samples[-1][1]


class Face:
    """MetaHuman 式脸骨的量测:轴心、坐标系、各区域骨集,以及各种场的逐骨权重。"""

    def __init__(self, arm):
        self.arm = arm
        bones = arm.data.bones
        root = bones.get(_ROOT)
        if root is None:
            raise LookupError("没有 FACIAL_C_FacialRoot:不是 MetaHuman 式脸骨")
        need = [f"FACIAL_{s}_Eyelid{p}A" for s in "LR" for p in ("Upper", "Lower")]
        miss = [n for n in need if bones.get(n) is None]
        if miss:
            raise LookupError("缺眼皮骨: " + " ".join(miss))
        self.bones = [root] + list(root.children_recursive)
        # MetaHuman 上颈(颌下到喉结)的皮肤骨挂在 首 下:张嘴时按距离跟下巴走一部分,否则颌下
        # 挤出一道褶;下颈一组不动,当张嘴插值的锚
        neck = bones.get(_NECK_UP)
        self.neck = {b.name for b in neck.children_recursive} if neck else set()
        if neck:
            self.bones += list(neck.children_recursive)
        low = bones.get(_NECK_LOW)
        self.neck_anchor = [b.head_local.copy() for b in [low] + list(low.children_recursive)] if low else []
        self.by_name = {b.name: b for b in self.bones}
        # 进出编辑模式后 Bone 引用会失效,之后只按名字用
        self.names = [b.name for b in self.bones]

        def sub(name):
            b = self.by_name.get(name)
            return {b.name} | {c.name for c in b.children_recursive} if b else set()

        self.sub = sub
        head = {b.name: b.head_local.copy() for b in self.bones}
        self.head = head

        # 坐标系:lat 指向角色左,fwd 为脸朝向,up 向上;单位 = 两眼间距
        pl, pr = head["FACIAL_L_EyelidUpperA"], head["FACIAL_R_EyelidUpperA"]
        self.pivot = {"L": pl.copy(), "R": pr.copy()}
        self.mid = (pl + pr) * 0.5
        self.unit = (pl - pr).length
        self.lat = (pl - pr).normalized()
        self.fwd = self.lat.cross(Vector((0.0, 0.0, 1.0))).normalized()
        self.up = self.fwd.cross(self.lat).normalized()

        static = set()
        for n in _EYEBALLS:
            static |= sub(n)
        static |= {n for n in head if _stem(n) in _STATIC_STEMS}
        static.add(_ROOT)
        self.static = static

        # 下巴:FACIAL_C_Chin1 的父骨(MetaHuman 原名 FACIAL_C_Jaw)
        chin = bones.get("FACIAL_C_Chin1") or bones.get("FACIAL_C_Jawline")
        jaw = chin.parent if chin and chin.parent and chin.parent.name != _ROOT else bones.get("FACIAL_C_Jaw")
        self.jaw = jaw.name if jaw else None
        self.jaw_pivot = head[self.jaw] if self.jaw else None
        jawset = sub(self.jaw) | sub("FACIAL_C_LowerLipRotation") if self.jaw else set()
        self.jawset = jawset

        corners = {s: sub(f"FACIAL_{s}_LipCorner") for s in "LR"}
        upper = (sub("FACIAL_C_MouthUpper") - corners["L"] - corners["R"] - {"FACIAL_C_MouthUpper"})
        upper |= {n for n in head if _stem(n) in _UPPER_LIP_SKIN}
        lower = sub("FACIAL_C_MouthLower") - {"FACIAL_C_MouthLower"}
        lower |= {n for n in head if _stem(n) in _LOWER_LIP_SKIN}
        self.corner_sets = corners
        self.lips = {"upper": upper, "lower": lower}

        self._init_lids()
        self._init_jaw()
        self._init_mouth()
        self._init_brows()
        self._init_cheeks()
        self.tongue = sub("FACIAL_C_Tongue1")
        self.teeth = {"up": sub("FACIAL_C_TeethUpper"), "dw": sub("FACIAL_C_TeethLower")}

    # -- 坐标 ---------------------------------------------------------------------------
    def x_out(self, s, p):
        """到中线的横向距离,朝 s 侧外为正。"""
        return (p - self.mid).dot(self.lat) * (1.0 if s == "L" else -1.0)

    def rel(self, s, p):
        d = p - self.pivot[s]
        return d.dot(self.fwd), d.dot(self.up)

    def vec(self, s, out=0.0, fwd=0.0, up=0.0):
        sign = 1.0 if s == "L" else -1.0 if s == "R" else 0.0
        return (self.lat * (out * sign) + self.fwd * fwd + self.up * up) * self.unit

    # -- 眼皮 ---------------------------------------------------------------------------
    def _elev(self, s, name):
        f, u = self.rel(s, self.head[name])
        return math.atan2(u, f)

    def _init_lids(self):
        self.lid_bones = {}             # side -> [(name, x_out, falloff, is_upper)]
        self.lid_margin = {}            # side -> {is_upper: [(x_out, elev, static_share)]}
        self._lid_sets = {}
        for s in "LR":
            ua, la = f"FACIAL_{s}_EyelidUpperA", f"FACIAL_{s}_EyelidLowerA"
            um = sorted((self.x_out(s, self.head[c.name]), self._elev(s, c.name), 0.0)
                        for c in self.by_name[ua].children)
            lm = sorted((self.x_out(s, self.head[c.name]), self._elev(s, c.name), 0.0)
                        for c in self.by_name[la].children)
            if not um or not lm:
                raise LookupError(f"{s} 侧眼睑边缘骨缺失")
            # 没有网格可量时先用边缘骨的骨头位置;calibrate() 换成皮肤网格上的实测边缘
            self.lid_margin[s] = {True: um, False: lm}
            up_set = self.sub(ua) | self.sub(f"FACIAL_{s}_EyelidUpperB")
            low_set = self.sub(la) | self.sub(f"FACIAL_{s}_EyelidLowerB")
            up2 = {n for n in self.head if _side(n) == s and _stem(n) in _LID_UP2_STEMS}
            low2 = {n for n in self.head if _side(n) == s and _stem(n) in _LID_LOW2_STEMS}
            # 跟随衰减:上眼皮以上按高度在「眼皮最高骨」到「眉骨平均高度」之间递减,下同
            z = {n: self.rel(s, self.head[n])[1] for n in up_set | low_set | up2 | low2}
            brow = [n for p in ("In", "Mid", "Out") for n in self.sub(f"FACIAL_{s}_Forehead{p}")]
            cheek = [n for n in self.head if _side(n) == s and _stem(n) in ("CheekInner", "CheekOuter")]
            z_lid_top = max(z[n] for n in up_set)
            z_lid_bot = min(z[n] for n in low_set)
            z_brow = sum(self.rel(s, self.head[n])[1] for n in brow) / len(brow) if brow else z_lid_top * 3
            z_cheek = sum(self.rel(s, self.head[n])[1] for n in cheek) / len(cheek) if cheek else z_lid_bot * 3
            rows = []
            for n in sorted(up_set | up2):
                f = 1.0 if n in up_set else min(1.0, max(0.0, (z_brow - z[n]) / max(z_brow - z_lid_top, 1e-6)))
                rows.append((n, self.x_out(s, self.head[n]), f, True))
            for n in sorted(low_set | low2):
                f = 1.0 if n in low_set else min(1.0, max(0.0, (z[n] - z_cheek) / max(z_lid_bot - z_cheek, 1e-6)))
                rows.append((n, self.x_out(s, self.head[n]), f, False))
            self.lid_bones[s] = rows
            self._lid_sets[s] = (up_set, low_set, {c.name for c in self.by_name[ua].children},
                                 {c.name for c in self.by_name[la].children})

    def calibrate(self, meshes):
        """眼睑边缘改用皮肤网格实测:以每根边缘骨为主导骨的皮肤顶点里,上眼皮取仰角最低的
        1/4、下眼皮取最高的 1/4 当边缘,记下它们的横向位置、仰角,以及权重落在眼皮旋转骨之外
        (头骨、眼角等不跟转)的份额 h。闭合角 = 要走的仰角 / (1-h),逐骨算。"""
        skin = self._skin_mesh(meshes)
        if skin is None:
            return
        to_arm = self.arm.matrix_world.inverted() @ skin.matrix_world
        groups = {g.index: g.name for g in skin.vertex_groups if g.name in self.arm.data.bones}
        found = {}
        for v in skin.data.vertices:
            ws = [(groups[g.group], g.weight) for g in v.groups if g.weight > 0.0 and g.group in groups]
            if not ws:
                continue
            dom = max(ws, key=lambda t: t[1])[0]
            for s in "LR":
                up_set, low_set, up_margin, low_margin = self._lid_sets[s]
                is_up = dom in up_margin
                if not is_up and dom not in low_margin:
                    continue
                moving = up_set if is_up else low_set
                tot = sum(w for _n, w in ws)
                p = to_arm @ v.co
                f, u = self.rel(s, p)
                found.setdefault((s, is_up, dom), []).append(
                    (math.atan2(u, f), self.x_out(s, p), sum(w for n, w in ws if n not in moving) / tot))
        for s in "LR":
            for is_up in (True, False):
                samples = []
                for (ss, up, _bone), rows in found.items():
                    if ss != s or up != is_up or len(rows) < 2:
                        continue
                    rows.sort()
                    q = max(1, len(rows) // 4)
                    edge = rows[:q] if is_up else rows[-q:]
                    samples.append((sum(r[1] for r in edge) / q, sum(r[0] for r in edge) / q,
                                    sum(r[2] for r in edge) / q))
                if len(samples) >= 2:
                    self.lid_margin[s][is_up] = sorted(samples)

    def _skin_mesh(self, meshes):
        """脸皮网格 = 以唇骨为主导骨的顶点最多的网格(睫毛/眉毛/牙网格没有唇)。"""
        lip = self.lips["upper"] | self.lips["lower"] | self.corner_sets["L"] | self.corner_sets["R"]
        best, best_n = None, 0
        for m in meshes:
            groups = {g.index: g.name for g in m.vertex_groups}
            n = 0
            for v in m.data.vertices:
                ws = [(groups.get(g.group), g.weight) for g in v.groups if g.weight > 0.0]
                ws = [t for t in ws if t[0]]
                if ws and max(ws, key=lambda t: t[1])[0] in lip:
                    n += 1
            if n > best_n:
                best, best_n = m, n
        return best

    # -- 下巴 ---------------------------------------------------------------------------
    def _init_jaw(self):
        self.jaw_w = {}
        self.ramus = None
        if not self.jaw:
            return
        # 下颌角 = 下巴子树里最宽处(横向最外 20% 的骨);下颌支中点 = 轴心与下颌角的中点。
        # 张嘴时真实下颌绕下颌支中部转、髁突前滑,只绕轴心转会把下颌角往后挤进脖子。
        wide = {n: abs((self.head[n] - self.mid).dot(self.lat)) for n in self.jawset}
        top = max(wide.values(), default=0.0)
        gon = [self.head[n] for n, x in wide.items() if top > 0.0 and x >= 0.8 * top]
        if gon:
            self.ramus = (self.jaw_pivot + sum(gon, Vector()) / len(gon)) * 0.5
        free = ({n for n in self.head if _stem(n) in _JAW_FREE_STEMS} | self.neck) - self.jawset - self.static
        skull = [self.head[n] for n in self.head if n not in self.jawset and n not in free] + self.neck_anchor
        jaw = [self.head[n] for n in self.jawset]
        for n in self.jawset:
            self.jaw_w[n] = 1.0
        for n in free:
            p = self.head[n]
            ds = min((p - q).length for q in skull)
            dj = min((p - q).length for q in jaw)
            w = ds / max(ds + dj, 1e-12)
            if w > 0.01:
                self.jaw_w[n] = w

    # -- 嘴角 / 嘴唇 ----------------------------------------------------------------------
    def _init_mouth(self):
        self.corner_w = {}
        self.lip_w = {}
        lips = self.lips["upper"] | self.lips["lower"]
        sigma = 0.3 * self.unit
        near = {n for n in self.head if _stem(n) in _CORNER_NEAR_STEMS} - self.static - lips
        xc = {}
        for s in "LR":
            c = self.by_name.get(f"FACIAL_{s}_LipCorner")
            if c is None:
                continue
            xc[s] = max(self.x_out(s, c.head_local), 1e-6)
            w = {n: 1.0 for n in self.corner_sets[s]}
            for n in lips:
                t = _smooth(self.x_out(s, self.head[n]) / xc[s])
                if t > 0.01:
                    w[n] = t
            for n in near - self.corner_sets[s] - self.corner_sets["R" if s == "L" else "L"]:
                d = (self.head[n] - c.head_local).length
                g = math.exp(-(d / sigma) ** 2)
                if g > 0.01:
                    w[n] = g
            self.corner_w[s] = w
        half = sum(xc.values()) / len(xc) if xc else self.unit * 0.4
        for part, names in self.lips.items():
            self.lip_w[part] = {n: 1.0 - _smooth(abs((self.head[n] - self.mid).dot(self.lat)) / half) for n in names}

    # -- 眉 -----------------------------------------------------------------------------
    def _init_brows(self):
        self.brow_w = {}
        sigma = 0.35 * self.unit
        for s in "LR":
            parts = [self.by_name.get(f"FACIAL_{s}_Forehead{p}") for p in ("In", "Mid", "Out")]
            if not all(parts):
                continue
            x_in, x_out = self.x_out(s, parts[0].head_local), self.x_out(s, parts[2].head_local)
            brow = set()
            for p in parts:
                brow |= self.sub(p.name)
            t_of = {n: min(1.0, max(0.0, (self.x_out(s, self.head[n]) - x_in) / max(x_out - x_in, 1e-6)))
                    for n in brow}
            rows = [(n, t_of[n], 1.0) for n in sorted(brow)]
            for n in sorted(self.head):
                if n in brow or n in self.static or _stem(n) not in _BROW_NEAR_STEMS or _side(n) not in (s, "C"):
                    continue
                nb = min(brow, key=lambda b: (self.head[b] - self.head[n]).length_squared)
                d = (self.head[nb] - self.head[n]).length
                w = math.exp(-(d / sigma) ** 2) * (0.5 if _side(n) == "C" else 1.0)
                if w > 0.01:
                    rows.append((n, t_of[nb], w))
            self.brow_w[s] = rows

    # -- 脸颊 ---------------------------------------------------------------------------
    def _init_cheeks(self):
        self.cheek_w = {}
        sigma = 0.45 * self.unit
        for s in "LR":
            parts = [self.by_name.get(f"FACIAL_{s}_Cheek{p}") for p in ("Inner", "Outer")]
            if not all(parts):
                continue
            apex = (parts[0].head_local + parts[1].head_local) * 0.5
            w = {}
            for n in self.head:
                if _side(n) != s or _stem(n) not in _CHEEK_STEMS or n in self.static:
                    continue
                g = math.exp(-((self.head[n] - apex).length / sigma) ** 2)
                if g > 0.01:
                    w[n] = g
            self.cheek_w[s] = w

    # -- 场 -----------------------------------------------------------------------------
    def lid_gap(self, s, x):
        """横向位置 x 处上下眼睑边缘的仰角差(rad)。"""
        up = [(a, b) for a, b, _h in self.lid_margin[s][True]]
        lo = [(a, b) for a, b, _h in self.lid_margin[s][False]]
        return max(0.0, _interp(up, x) - _interp(lo, x))

    def lid_gain(self, s, is_up, x):
        """1/(1-h):补足权重里不跟着转的份额。"""
        h = _interp([(a, c) for a, _b, c in self.lid_margin[s][is_up]], x)
        return min(_K_MAX, 1.0 / max(1e-3, 1.0 - h))

    def comp_lids(self, sides, close, meet=0.25, tilt=0.0):
        """close:1=上下眼睑在 meet 处合上(meet=0 只动上眼皮,1 只动下眼皮),负值=睁大。
        tilt:上眼皮外端(>0)/内端(<0)额外多合的比例(按横向位置线性变化)。"""
        out = {}
        for s in sides:
            xs = [x for x, _e, _h in self.lid_margin[s][True]]
            xc, hw = (min(xs) + max(xs)) * 0.5, max((max(xs) - min(xs)) * 0.5, 1e-6)
            for name, x, f, is_up in self.lid_bones[s]:
                gap = self.lid_gap(s, x)
                if is_up:
                    amt = close * (1.0 - meet) + tilt * min(1.0, max(-1.0, (x - xc) / hw))
                else:
                    amt = -close * meet
                ang = amt * gap * self.lid_gain(s, is_up, x) * f
                if abs(ang) > 1e-7:
                    out[name] = _rot(self.pivot[s], self.lat, ang)
        return out

    def comp_jaw(self, deg):
        """绕下巴轴心转 deg 度,再前滑到下颌支中点前后不动(髁突前滑)。"""
        res = {}
        for n, w in self.jaw_w.items():
            rot = _rot(self.jaw_pivot, self.lat, math.radians(deg) * w)
            if self.ramus is not None:
                back = (rot @ self.ramus - self.ramus).dot(self.fwd)
                rot = Matrix.Translation(self.fwd * -back) @ rot
            res[n] = rot
        return res

    def comp_corners(self, sides, out=0.0, up=0.0, fwd=0.0):
        res = {}
        for s in sides:
            v = self.vec(s, out, fwd, up)
            for n, w in self.corner_w.get(s, {}).items():
                m = Matrix.Translation(v * w)
                res[n] = m @ res[n] if n in res else m
        return res

    def comp_lip(self, part, up=0.0, fwd=0.0, flat=False):
        v = self.vec("C", 0.0, fwd, up)
        return {n: Matrix.Translation(v * (1.0 if flat else w)) for n, w in self.lip_w[part].items()
                if flat or w > 0.01}

    def comp_brows(self, sides, inner=(0, 0, 0), mid=(0, 0, 0), outer=(0, 0, 0)):
        res = {}
        for s in sides:
            for n, t, w in self.brow_w.get(s, ()):
                a, b, u = (inner, mid, 0.0) if t < 0.5 else (mid, outer, 0.5)
                k = (t - u) * 2.0
                prof = [a[i] + (b[i] - a[i]) * k for i in range(3)]
                m = Matrix.Translation(self.vec(s, prof[0], prof[1], prof[2]) * w)
                res[n] = m @ res[n] if n in res else m
        return res

    def comp_cheeks(self, sides, up=0.0, out=0.0, fwd=0.0):
        res = {}
        for s in sides:
            v = self.vec(s, out, fwd, up)
            for n, w in self.cheek_w.get(s, {}).items():
                res[n] = Matrix.Translation(v * w)
        return res

    def comp_tongue(self, fwd=0.0, down=0.0, pitch=0.0, yaw=0.0, prev=None):
        """排在张嘴之后:以舌根当前(已随下巴转过)的位置为轴心转,再沿脸朝向伸出。
        先伸再张嘴的话,舌头会跟着下巴转进下唇/下巴里。"""
        if not self.tongue:
            return {}
        root = "FACIAL_C_Tongue1"
        base = prev.get(root) if prev else None
        piv = base @ self.head[root] if base is not None else self.head[root]
        m = (Matrix.Translation(self.vec("C", 0.0, fwd, -down))
             @ _rot(piv, self.up, math.radians(yaw)) @ _rot(piv, self.lat, math.radians(pitch)))
        return {n: m for n in self.tongue}

    def comp_teeth(self, which, back=0.0, up=0.0):
        m = Matrix.Translation(self.vec("C", 0.0, -back, up))
        return {n: m for n in self.teeth[which]}

    # -- 求解 ---------------------------------------------------------------------------
    def deltas(self, comps):
        D = {}
        for kind, kw in comps:
            if kind == "tongue":
                kw = dict(kw, prev=D)
            for n, m in getattr(self, "comp_" + kind)(**kw).items():
                D[n] = m @ D[n] if n in D else m
        return D

    def offsets(self, comps):
        """bone -> (location, quaternion):相对父骨的局部偏移(pose 空间)。
        pose = rest⁻¹ · D_parent⁻¹ · D · rest;父骨动了而自己不该动的骨也会得到抵消项。"""
        D = self.deltas(comps)
        ident = Matrix.Identity(4)
        bones = self.arm.data.bones       # 按名取:进出编辑模式(断开连接骨)后旧 Bone 引用会失效
        out = {}
        for name in self.names:
            b = bones[name]
            d = D.get(b.name)
            dp = D.get(b.parent.name) if b.parent else None
            if d is None and dp is None:
                continue
            rel = (dp.inverted() if dp is not None else ident) @ (d if d is not None else ident)
            basis = b.matrix_local.inverted() @ rel @ b.matrix_local
            loc, rot = basis.to_translation(), basis.to_quaternion()
            ang = rot.angle
            if loc.length < _EPS_LOC and min(ang, 2 * math.pi - ang) < _EPS_ROT:
                continue
            out[b.name] = (loc, rot)
        return out


# -- 表情表 ------------------------------------------------------------------------------
# 名字/分类照 MMD 标准表情(Tda 式补充一并带上,动作里常见)。平移量单位 = 两眼间距,
# 眉 (out, fwd, up):out 离中线为正。角度单位:度。
def _lids(sides, close, meet=0.25, tilt=0.0):
    return ("lids", dict(sides=sides, close=close, meet=meet, tilt=tilt))


def _jaw(deg):
    return ("jaw", dict(deg=deg))


def _corners(out=0.0, up=0.0, fwd=0.0, sides="LR"):
    return ("corners", dict(sides=sides, out=out, up=up, fwd=fwd))


def _lip(part, up=0.0, fwd=0.0, flat=False):
    return ("lip", dict(part=part, up=up, fwd=fwd, flat=flat))


def _brows(sides="LR", inner=(0, 0, 0), mid=(0, 0, 0), outer=(0, 0, 0)):
    return ("brows", dict(sides=sides, inner=inner, mid=mid, outer=outer))


def _cheeks(up, sides="LR", out=0.0, fwd=0.0):
    return ("cheeks", dict(sides=sides, up=up, out=out, fwd=fwd))


def _tongue(fwd=0.0, down=0.0, pitch=0.0, yaw=0.0):
    return ("tongue", dict(fwd=fwd, down=down, pitch=pitch, yaw=yaw))


def _teeth(which, back, up):
    return ("teeth", dict(which=which, back=back, up=up))


BLINK_MEET = 0.25       # 眨眼:上下眼睑在缝隙下 1/4 处合上(上眼皮走大头)
SMILE_MEET = 0.55       # 笑眼:下眼睑被脸颊推高,过半处合上
JAW_A = 15.0            # あ 张嘴角度

_BROW = {
    "真面目": dict(inner=(-0.03, 0.0, -0.05), mid=(0.0, 0.0, -0.015), outer=(0.0, 0.0, 0.015)),
    "困る": dict(inner=(-0.02, 0.0, 0.10), mid=(0.0, 0.0, 0.04), outer=(0.0, 0.0, -0.025)),
    "にこり": dict(inner=(0.0, 0.0, 0.03), mid=(0.0, 0.0, 0.065), outer=(0.0, 0.0, 0.04)),
    "怒り": dict(inner=(-0.05, 0.0, -0.09), mid=(0.0, 0.0, -0.025), outer=(0.0, 0.0, 0.04)),
    "上": dict(inner=(0.0, 0.0, 0.11), mid=(0.0, 0.0, 0.11), outer=(0.0, 0.0, 0.10)),
    "下": dict(inner=(-0.015, 0.0, -0.07), mid=(0.0, 0.0, -0.07), outer=(0.0, 0.0, -0.06)),
}
_PERO = [_jaw(14.0), _tongue(fwd=0.7, pitch=15.0)]       # 先张嘴,舌头再伸(见 comp_tongue)
_OMEGA = [_corners(out=-0.04, up=0.04), _lip("upper", fwd=0.02), _lip("lower", fwd=0.02, up=0.012)]

MORPHS = [
    # 眉
    *[(n, e, "EYEBROW", [_brows(**_BROW[n])]) for n, e in (
        ("真面目", "serious"), ("困る", "troubled"), ("にこり", "smile_brow"), ("怒り", "anger"),
        ("上", "brow_up"), ("下", "brow_down"))],
    # 目
    ("まばたき", "blink", "EYE", [_lids("LR", 1.0, BLINK_MEET)]),
    ("笑い", "smile", "EYE", [_cheeks(0.05), _lids("LR", 1.0, SMILE_MEET)]),
    ("ウィンク", "wink", "EYE", [_cheeks(0.05, "L"), _lids("L", 1.0, SMILE_MEET)]),
    ("ウィンク右", "wink_right", "EYE", [_cheeks(0.05, "R"), _lids("R", 1.0, SMILE_MEET)]),
    ("ウィンク２", "wink2", "EYE", [_lids("L", 1.0, BLINK_MEET)]),
    ("ｳｨﾝｸ２右", "wink2_right", "EYE", [_lids("R", 1.0, BLINK_MEET)]),
    ("なごみ", "calm", "EYE", [_lids("LR", 0.8, 0.35)]),
    ("はぅ", "hau", "EYE", [_cheeks(0.03), _lids("LR", 1.08, 0.5)]),
    ("びっくり", "surprised", "EYE", [_lids("LR", -0.3, 0.3)]),
    ("じと目", "jito", "EYE", [_lids("LR", 0.35, 0.0)]),
    ("キリッ", "kiri", "EYE", [_lids("LR", 0.18, 0.5, tilt=-0.08)]),
    # 口
    ("あ", "a", "MOUTH", [_jaw(JAW_A)]),
    ("い", "i", "MOUTH", [_corners(out=0.07, up=0.01), _jaw(4.0)]),
    ("う", "u", "MOUTH", [_corners(out=-0.08, fwd=0.04), _lip("upper", fwd=0.045, flat=True),
                           _lip("lower", fwd=0.045, flat=True), _jaw(1.5)]),
    ("え", "e", "MOUTH", [_corners(out=0.04), _jaw(9.0)]),
    ("お", "o", "MOUTH", [_corners(out=-0.05, fwd=0.02), _lip("upper", fwd=0.02, flat=True),
                           _lip("lower", fwd=0.02, flat=True), _jaw(12.0)]),
    ("▲", "triangle", "MOUTH", [_corners(out=-0.04, up=-0.02), _jaw(6.0)]),
    ("∧", "frown", "MOUTH", [_corners(out=-0.02, up=-0.04), _lip("lower", up=0.015)]),
    ("ω", "omega", "MOUTH", list(_OMEGA)),
    ("ω□", "omega_open", "MOUTH", _OMEGA + [_jaw(5.0)]),
    ("にっこり", "smile_mouth", "MOUTH", [_cheeks(0.03), _corners(out=0.04, up=0.07, fwd=-0.01)]),
    ("にやり", "grin", "MOUTH", [_cheeks(0.03), _corners(out=0.05, up=0.07)]),
    ("にやり２", "grin2", "MOUTH", [_cheeks(0.04), _corners(out=0.06, up=0.10)]),
    ("ぺろっ", "tongue_out", "MOUTH", list(_PERO)),
    ("てへぺろ", "tehepero", "MOUTH", [_jaw(14.0), _tongue(fwd=0.7, pitch=15.0, yaw=20.0),
                                      _cheeks(0.05, "L"), _lids("L", 1.0, SMILE_MEET)]),
    ("てへぺろ２", "tehepero2", "MOUTH", [_jaw(14.0), _tongue(fwd=0.7, pitch=15.0, yaw=-20.0),
                                        _cheeks(0.05, "R"), _lids("R", 1.0, SMILE_MEET)]),
    ("口角上げ", "corner_up", "MOUTH", [_corners(up=0.07)]),
    ("口角下げ", "corner_down", "MOUTH", [_corners(up=-0.06)]),
    ("口横広げ", "mouth_wide", "MOUTH", [_corners(out=0.09)]),
    ("歯無し上", "no_upper_teeth", "MOUTH", [_teeth("up", back=0.25, up=0.15)]),
    ("歯無し下", "no_lower_teeth", "MOUTH", [_teeth("dw", back=0.25, up=-0.15)]),
    # Tda 式补充:别名、几个口型、单侧的眉/眼(动作里常见,缺了会找不到)
    ("ウィンク２右", "wink2_right_fw", "EYE", [_lids("R", 1.0, BLINK_MEET)]),
    ("ｷﾘｯ", "kiri_hw", "EYE", [_lids("LR", 0.18, 0.5, tilt=-0.08)]),
    ("ジト目", "jito_kana", "EYE", [_lids("LR", 0.35, 0.0)]),
    ("下眼上", "lower_lid_up", "EYE", [_lids("LR", 0.35, 1.0)]),
    ("怒り目", "angry_eyes", "EYE", [_lids("LR", 0.2, 0.3, tilt=-0.12)]),
    ("悲しむ", "sad_eyes", "EYE", [_lids("LR", 0.25, 0.2, tilt=0.12)]),
    ("なごみ左", "calm_left", "EYE", [_lids("L", 0.8, 0.35)]),
    ("なごみ右", "calm_right", "EYE", [_lids("R", 0.8, 0.35)]),
    ("あ２", "a2", "MOUTH", [_jaw(22.0)]),
    ("ん", "n", "MOUTH", [_corners(out=-0.015), _lip("lower", up=0.015), _lip("upper", up=-0.01)]),
    ("ワ", "wa", "MOUTH", [_cheeks(0.03), _corners(out=0.06, up=0.05), _jaw(13.0)]),
    ("口横狭め", "mouth_narrow", "MOUTH", [_corners(out=-0.07)]),
    *[(n + side, e + ("_left" if side == "左" else "_right"), "EYEBROW",
       [_brows("L" if side == "左" else "R", **_BROW[n])])
      for n, e in (("困る", "troubled"), ("にこり", "smile_brow"), ("怒り", "anger"),
                   ("上", "brow_up"), ("下", "brow_down"))
      for side in ("左", "右")],
]


def compute(arm, meshes=None):
    """-> (Face, {morph 名: {bone: (location, quaternion)}});不碰 mmd_tools,测试也用它。"""
    face = Face(arm)
    face.calibrate(meshes if meshes is not None else _skinned_meshes(arm))
    return face, {name: face.offsets(comps) for name, _e, _c, comps in MORPHS}
