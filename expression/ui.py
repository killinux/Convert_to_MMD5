# -*- coding: utf-8 -*-
"""第 3 页「表情」:设置、按钮和面板内容(画在 Convert to MMD 主面板里)。按钮只调 api.py。

「生成表情」按勾选一次做完:MMD 表情、Faceit(52 个 ARKit 形态键 + 注册),两项都可选。
按钮 ID 沿用旧版的 object.add_face_morphs / object.remove_face_morphs,其余是 object.c2m_expr_*。
"""
import json

import bpy

from . import api, faceit, ff7, manual, recipes, sources

EDIT_SETS = ((recipes.ARKIT, "ARKit 52", "Faceit 用的 52 个形态键"), (recipes.MMD, "MMD 表情", "MMD 标准表情"))
OUTPUTS = (("AUTO", "自动", "逐个表情选：PMX（每个顶点只留 4 个权重）里和 Blender 相差不超过阈值的做骨骼表情，"
                           "否则做顶点表情"),
           ("BONE", "骨骼", "全部做骨骼表情：体积小，半程时眼皮、下巴走圆弧；MMD 里每个顶点只跟最大的 4 个权重"),
           ("VERTEX", "顶点", "全部做顶点表情（形态键）：MMD 里和 Blender 一模一样，半程时走直线"))
CLEAR = (("MMD", "MMD 表情", "删本工具做的 MMD 表情"),
         ("ARKIT", "ARKit 形态键", "删本工具做的 ARKit 形态键"),
         ("ALL", "全部", "两种都删"))
FACEIT_STATE = {"enabled": "已启用", "disabled": "未启用（偏好设置 → 插件）", "missing": "未安装"}
_ERRORS = (ValueError, OSError, KeyError, RuntimeError)
_PREVIEW_ITEMS = []                     # EnumProperty callbacks must keep their strings alive


def _settings(context):
    return context.scene.c2m_expr


def _say(context, lines):
    _settings(context).report = "\n".join(lines)
    for window in context.window_manager.windows:          # also when a script ran the button
        for area in window.screen.areas:
            if area.type == "VIEW_3D":
                area.tag_redraw()


def _armature(obj):
    try:
        return api.model(obj)[0] if obj is not None else None
    except ValueError:
        return None


def _preview_items(self, context):
    _PREVIEW_ITEMS.clear()
    try:
        made = api.made(context.active_object)
    except (ValueError, AttributeError):
        made = {}
    seen = []
    for kind, label in (("BONE", "骨"), (recipes.MMD, "顶"), (recipes.ARKIT, "AR")):
        for n in made.get(kind, []):
            if n not in seen:
                seen.append(n)
                _PREVIEW_ITEMS.append((n, "%s  [%s]" % (n, label), ""))
    if not _PREVIEW_ITEMS:
        _PREVIEW_ITEMS.append(("", "（还没有生成的表情）", ""))
    return _PREVIEW_ITEMS


def _preview_update(self, context):
    if self.preview_name:
        try:
            api.preview(context.active_object, self.preview_name, self.preview_value)
        except ValueError:
            pass


_EDIT_ITEMS = []


def _kept(context):
    try:
        return set(api.kept_names(context.active_object))
    except (ValueError, AttributeError):
        return set()


def _edit_items(self, context):
    _EDIT_ITEMS.clear()
    kept = _kept(context)
    for n in manual.target_names(self.edit_set):
        _EDIT_ITEMS.append((n, ("✎ " if n in kept else "") + n, "已手调" if n in kept else ""))
    return _EDIT_ITEMS


class C2M_ExprSettings(bpy.types.PropertyGroup):
    source: bpy.props.EnumProperty(name="来源", items=sources.KINDS, default="AUTO")  # type: ignore
    dna_path: bpy.props.StringProperty(  # type: ignore
        name="DNA", subtype="FILE_PATH",
        description="这张脸的 MetaHuman DNA 文件（.dna）。给了就用游戏原始表情数据，MMD 和 ARKit 都能做")
    action: bpy.props.PointerProperty(  # type: ignore
        name="动作", type=bpy.types.Action,
        description="可选：姿势标记（或动作本身）按表情名命名的动作")
    use_markers: bpy.props.BoolProperty(  # type: ignore
        name="按姿势标记", default=True, description="每个姿势标记一个姿势（否则整个动作算一个）")
    neutral: bpy.props.StringProperty(  # type: ignore
        name="中性姿势", description="可选：其余姿势都减掉它（游戏的「无表情」不一定是绑定姿势）")
    capture_name: bpy.props.StringProperty(name="名字", default="まばたき")  # type: ignore
    recipe_file: bpy.props.StringProperty(  # type: ignore
        name="配方文件", subtype="FILE_PATH",
        description="可选：覆盖 / 增加配方的 JSON（用「导出配方」写一份改）")
    do_mmd: bpy.props.BoolProperty(  # type: ignore
        name="MMD 表情", default=True,
        description="MMD 标准表情（まばたき、あいうえお、眉……），进 PMX 给 MMD 动作驱动")
    mmd_output: bpy.props.EnumProperty(name="方式", items=OUTPUTS, default="AUTO")  # type: ignore
    cat_eye: bpy.props.BoolProperty(name="目", default=True)  # type: ignore
    cat_brow: bpy.props.BoolProperty(name="眉", default=True)  # type: ignore
    cat_mouth: bpy.props.BoolProperty(name="口", default=True)  # type: ignore
    cat_other: bpy.props.BoolProperty(name="其他", default=True)  # type: ignore
    extras: bpy.props.BoolProperty(  # type: ignore
        name="扩展表情", default=True, description="也做不太常用的名字（别名、单侧眉、あ２、ん……，动作里常见）")
    s_eye: bpy.props.FloatProperty(name="目", default=1.0, min=0.0, max=3.0)  # type: ignore
    s_brow: bpy.props.FloatProperty(name="眉", default=1.0, min=0.0, max=3.0)  # type: ignore
    s_mouth: bpy.props.FloatProperty(name="口", default=1.0, min=0.0, max=3.0)  # type: ignore
    replace: bpy.props.BoolProperty(  # type: ignore
        name="替换同名", default=False,
        description="模型自带 / 别人做的同名表情也重做（默认跳过它们；本工具做的总是重做）")
    threshold: bpy.props.FloatProperty(  # type: ignore
        name="误差阈值 mm", default=0.3, min=0.0, max=10.0,
        description="自动：骨骼表情在 PMX（4 权重）里允许的最大偏差")
    do_arkit: bpy.props.BoolProperty(  # type: ignore
        name="Faceit（ARKit 52）", default=False,
        description="52 个 ARKit 形态键，注册到 Faceit 给 iPhone 面捕实时驱动（MMD 用不上，导出 PMX 默认不带）")
    faceit_register: bpy.props.BoolProperty(  # type: ignore
        name="生成后注册到 Faceit", default=True,
        description="Faceit 已启用时，生成完直接注册（形态键、头骨、实时源）")
    head_bone: bpy.props.StringProperty(name="头骨", description="空 = 自动找")  # type: ignore
    live_source: bpy.props.EnumProperty(name="实时源", items=faceit.SOURCES, default="FACECAP")  # type: ignore
    package_path: bpy.props.StringProperty(  # type: ignore
        name="游戏包", subtype="FILE_PATH",
        description="带完整蒙皮权重的 UE5 脸部网格包（.uasset.bin）；UE Viewer 每个顶点只导出 4 个权重")
    preview_name: bpy.props.EnumProperty(name="表情", items=_preview_items, update=_preview_update)  # type: ignore
    preview_value: bpy.props.FloatProperty(  # type: ignore
        name="权重", default=1.0, min=0.0, max=1.0, update=_preview_update)
    pmx_path: bpy.props.StringProperty(name="PMX", subtype="FILE_PATH")  # type: ignore
    pmx_arkit: bpy.props.BoolProperty(  # type: ignore
        name="带 ARKit 形态键", default=False, description="把 52 个 ARKit 形态键也写进 PMX（「其他」表情）")
    pmx_scale: bpy.props.FloatProperty(name="缩放", default=12.5, min=0.01, max=1000.0)  # type: ignore
    ff7_data: bpy.props.StringProperty(  # type: ignore
        name="FF7 表情数据", subtype="FILE_PATH",
        description="FF7 Remake / Rebirth 角色的表情数据 JSON（ripper_tpose 的 ff7rb_face_data.py / "
                    "ff7_face_data.py 生成）；空 = 在 .blend 上层的 _meta/face 里按角色编号找")
    use_manual: bpy.props.BoolProperty(  # type: ignore
        name="手调优先", default=True,
        description="生成时，手调过的表情用手调的姿势（同名的姿势库条目），其余用来源算的")
    edit_set: bpy.props.EnumProperty(name="表情组", items=EDIT_SETS, default=recipes.ARKIT)  # type: ignore
    edit_name: bpy.props.EnumProperty(name="表情", items=_edit_items)  # type: ignore
    show_edit: bpy.props.BoolProperty(default=False)  # type: ignore
    show_mmd_more: bpy.props.BoolProperty(default=False)  # type: ignore
    show_preview: bpy.props.BoolProperty(default=False)  # type: ignore
    show_tools: bpy.props.BoolProperty(default=False)  # type: ignore
    report: bpy.props.StringProperty()  # type: ignore
    phone_hint: bpy.props.StringProperty()  # type: ignore


def _src_args(s):
    return dict(dna_path=s.dna_path, action=s.action, use_markers=s.use_markers, neutral=s.neutral,
                ff7_data=s.ff7_data)


def _categories(s):
    return [c for c, on in (("EYE", s.cat_eye), ("EYEBROW", s.cat_brow), ("MOUTH", s.cat_mouth),
                            ("OTHER", s.cat_other)) if on]


def _strengths(s):
    return {"EYE": s.s_eye, "EYEBROW": s.s_brow, "MOUTH": s.s_mouth, "OTHER": 1.0}


def _quiet(_line):
    pass


def _report_lines(title, rep):
    made = []
    if rep.get("bone"):
        made.append("骨骼表情 %d" % len(rep["bone"]))
    if rep.get("vertex"):
        made.append(("形态键 %d" if rep["set"] == recipes.ARKIT else "顶点表情 %d") % len(rep["vertex"]))
    src = sources.KIND_LABELS.get(rep.get("source"), rep.get("source"))
    if made:
        head = "%s（%s）：%s" % (title, src, "、".join(made))
    elif rep.get("note"):
        head = "%s（%s）" % (title, src)
    else:
        head = "! %s（%s）：没做出来" % (title, src)
    lines = [head]
    if rep.get("note"):
        lines.append("  " + rep["note"])
    if rep.get("errors_mm"):
        worst = max(rep["errors_mm"].items(), key=lambda kv: kv[1])
        lines.append("  PMX 误差最大 %.2f mm（%s）" % (worst[1], worst[0]))
    if rep.get("skipped"):
        names_ = [n for n, _r in rep["skipped"]]
        lines.append("  跳过 %d 个：%s%s" % (len(names_), "、".join(names_[:4]), "……" if len(names_) > 4 else ""))
        if not made:
            lines.append("  原因：%s" % "；".join(sorted({r for _n, r in rep["skipped"]})[:2]))
    if rep.get("manual"):
        lines.append("  其中手调 %d 个：%s" % (len(rep["manual"]), "、".join(rep["manual"][:4])))
    if rep.get("removed"):
        lines.append("  替换掉 %d 个同名旧表情" % len(rep["removed"]))
    if rep.get("disconnected"):
        lines.append("  断开了 %d 根相连的骨骼（Blender 里才能平移）" % rep["disconnected"])
    info = rep.get("source_info", {})
    if "fit_mean_mm" in info:
        lines.append("  DNA 对位误差 %.2f mm（%d 根骨）" % (info["fit_mean_mm"], info["joints"]))
    return lines


def _register_faceit(s, obj):
    try:
        rep = api.register_faceit(obj, head_bone=s.head_bone, source=s.live_source)
    except (ValueError, RuntimeError) as exc:
        msg = str(exc)
        return ["! " + (msg if msg.startswith("Faceit") else "Faceit：" + msg)]
    if not s.head_bone:
        s.head_bone = rep["head"].split(" / ")[-1]
    s.phone_hint = "手机填 %s 端口 %s" % (rep["lan_ip"], rep["port"])
    lines = ["Faceit：目标 %d/52，头骨 %s" % (rep["targets"], s.head_bone)]
    if rep["missing"]:
        lines.append("  缺：%s" % "、".join(rep["missing"][:4]))
    if rep["turned"]:
        lines.append("  模型原来朝 %s，已绕 Z 轴转 %+.0f° 改成朝 -Y（Faceit 按朝 -Y 换算头部转动）"
                     % (rep["facing"], rep["turned"]))
    return lines + ["  " + s.phone_hint + "，然后 FACEIT → Mocap → Live → Start"]


class _Base:
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return context.active_object is not None

    def fail(self, exc):
        self.report({"ERROR"}, str(exc))
        return {"CANCELLED"}


class OBJECT_OT_add_face_morphs(_Base, bpy.types.Operator):
    """按勾选生成表情：MMD 表情（骨骼 / 顶点），以及 Faceit 用的 52 个 ARKit 形态键"""
    bl_idname = "object.add_face_morphs"
    bl_label = "生成表情"

    def execute(self, context):
        s = _settings(context)
        if not (s.do_mmd or s.do_arkit):
            return self.fail("至少勾一项：MMD 表情 / Faceit（ARKit 52）")
        obj = context.active_object
        lines, failed, done = [], [], 0
        if s.do_mmd:
            try:
                rep = api.build(obj, recipes.MMD, s.source, output=s.mmd_output, categories=_categories(s),
                                extras=s.extras, strengths=_strengths(s), replace=s.replace,
                                threshold_mm=s.threshold, recipe_file=s.recipe_file, use_manual=s.use_manual,
                                log=_quiet, **_src_args(s))
            except _ERRORS as exc:
                failed.append("MMD 表情：%s" % exc)
            else:
                done += 1
                lines += _report_lines("MMD 表情", rep)
                print("C2M_EXPR_MMD=" + json.dumps(rep, ensure_ascii=False, default=str))
        if s.do_arkit:
            try:
                rep = api.build(obj, recipes.ARKIT, s.source, strengths=_strengths(s), recipe_file=s.recipe_file,
                                use_manual=s.use_manual, log=_quiet, **_src_args(s))
            except _ERRORS as exc:
                failed.append("ARKit：%s" % exc)
            else:
                done += 1
                lines += _report_lines("ARKit 52", rep)
                print("C2M_EXPR_ARKIT=" + json.dumps(rep, ensure_ascii=False, default=str))
                if s.faceit_register and (rep["vertex"] or rep["source"] == "SHAPES"):
                    lines += _register_faceit(s, obj)
        lines += ["! " + f for f in failed]
        _say(context, lines)
        if not done:
            self.report({"ERROR"}, "；".join(failed))
            return {"CANCELLED"}
        warn = any(line.startswith("!") for line in lines)
        self.report({"WARNING"} if warn else {"INFO"}, "；".join(line for line in lines if not line.startswith(" ")))
        return {"FINISHED"}


class OBJECT_OT_remove_face_morphs(_Base, bpy.types.Operator):
    """删除本工具做的表情（模型自带的、别人做的同名表情不动）"""
    bl_idname = "object.remove_face_morphs"
    bl_label = "清除表情"
    which: bpy.props.EnumProperty(name="清除", items=CLEAR, default="MMD")  # type: ignore

    def execute(self, context):
        sets = (recipes.MMD, recipes.ARKIT) if self.which == "ALL" else (self.which,)
        bones = keys = 0
        try:
            for set_key in sets:
                rep = api.clear(context.active_object, set_key)
                bones += len(rep["bone_morphs"])
                keys += len(rep["shape_keys"])
        except ValueError as exc:
            return self.fail(exc)
        msg = "清除了 骨骼表情 %d 个 · 形态键 %d 个" % (bones, keys)
        _say(context, [msg])
        self.report({"INFO"}, msg)
        return {"FINISHED"}


class OBJECT_OT_c2m_expr_analyze(_Base, bpy.types.Operator):
    """看模型有什么、「自动」会选哪个来源、权重对 PMX 意味着什么"""
    bl_idname = "object.c2m_expr_analyze"
    bl_label = "分析模型"
    bl_options = {"REGISTER"}

    def execute(self, context):
        s = _settings(context)
        try:
            info = api.analyze(context.active_object, s.dna_path, s.action, s.use_markers, s.ff7_data)
        except _ERRORS as exc:
            return self.fail(exc)
        src = info["sources"]
        kind, why = info["auto"]
        lines = ["%s（%s）" % (info["armature"], "MMD 模型" if info["mmd_model"] else "未转 MMD"),
                 "自动来源：%s（%s）" % (sources.KIND_LABELS.get(kind, "无"), why) if kind else "! " + why,
                 "脸部网格 %d 个 · 最多 %d 个权重" % (len(info["face_meshes"]), info["max_influences"])]
        if info["four_capped"]:
            lines.append("! 脸部权重被截成 4 个：用 DNA 时先「恢复完整权重」")
        elif info["over4_vertices"]:
            lines.append("超过 4 个权重的顶点 %d（骨骼表情在 PMX 里有误差）" % info["over4_vertices"])
        lines.append("MetaHuman 脸骨 %s · FACIAL 骨 %d · ARKit 形态键 %d/52" % (
            "有" if src["METAHUMAN"] else "无", src["facial_bones"], info["arkit_keys"]))
        if src["FF7"]["rig"]:
            lines.append("FF7 脸骨 · 表情数据：%s" % (src["FF7"]["data"] or "! 没找到（选 JSON 文件）"))
        if info["kept"]:
            lines.append("手调过 %d 个：%s" % (len(info["kept"]), "、".join(info["kept"][:5])))
        roles_ = src["ROLES"]
        lines.append("骨骼脸：%s · 姿势库 %d" % ("%d 个角色" % roles_["roles"] if roles_["usable"] else "无", src["POSES"]))
        if "DNA" in src:
            d = src["DNA"]
            lines.append(("! DNA：%s" % d["error"]) if "error" in d else
                         "DNA 对位 %d/%d · %.2f mm" % (d["joints"], d["dna_joints"], d["fit_mean_mm"]))
        lines.append("已有 MMD 表情：顶点 %d · 骨骼 %d" % (info["mmd_vertex_morphs"], info["mmd_bone_morphs"]))
        m = info["made"]
        lines.append("本工具做的：骨骼 %d · 顶点 %d · ARKit %d" % (m["BONE"], m[recipes.MMD], m[recipes.ARKIT]))
        state = FACEIT_STATE[info["faceit"]]
        if info.get("faceit_registered") is not None:
            state += "，%s" % ("已注册" if info["faceit_registered"] else "未注册")
        lines.append("Faceit：%s" % state)
        if info["stashed"]:
            lines.append("! 有暂存的形态键：%d 个网格（转换后点「恢复」）" % len(info["stashed"]))
        _say(context, lines)
        print("C2M_EXPR_ANALYZE=" + json.dumps(info, ensure_ascii=False, default=str))
        return {"FINISHED"}


class OBJECT_OT_c2m_expr_check(_Base, bpy.types.Operator):
    """模型现在这样进 MMD / Faceit 会出什么问题"""
    bl_idname = "object.c2m_expr_check"
    bl_label = "兼容性检查"
    bl_options = {"REGISTER"}

    def execute(self, context):
        try:
            issues = api.check(context.active_object)
        except ValueError as exc:
            return self.fail(exc)
        _say(context, [("! " if level != "INFO" else "") + msg for level, msg in issues])
        return {"FINISHED"}


class OBJECT_OT_c2m_expr_estimate(_Base, bpy.types.Operator):
    """每个表情做成骨骼表情时，PMX（每个顶点 4 个权重）里会偏多少；什么也不写"""
    bl_idname = "object.c2m_expr_estimate"
    bl_label = "骨骼表情误差预估"
    bl_options = {"REGISTER"}

    def execute(self, context):
        s = _settings(context)
        try:
            rep = api.estimate(context.active_object, s.source, categories=_categories(s), extras=s.extras,
                               strengths=_strengths(s), recipe_file=s.recipe_file, use_manual=s.use_manual,
                               **_src_args(s))
        except _ERRORS as exc:
            return self.fail(exc)
        errs = sorted(rep["errors_mm"].items(), key=lambda kv: -kv[1])
        lines = ["超过 4 个权重的顶点 %d（最多 %d 个）" % (rep["over4_vertices"], rep["max_influences"])]
        if errs:
            bad = [n for n, e in errs if e > s.threshold]
            lines.append("超过 %.2f mm 的：%d / %d" % (s.threshold, len(bad), len(errs)))
            lines += ["  %s  %.2f mm" % (n, e) for n, e in errs[:6]]
        else:
            lines.append("PMX 和 Blender 里一样：骨骼表情没有误差")
        if rep.get("scaled"):
            lines.append("要缩放骨骼、只能做顶点表情：%s" % "、".join(rep["scaled"]))
        _say(context, lines)
        return {"FINISHED"}


class OBJECT_OT_c2m_expr_register_faceit(_Base, bpy.types.Operator):
    """把带 ARKit 形态键的网格、52 个目标、头骨和实时源注册到 Faceit"""
    bl_idname = "object.c2m_expr_register_faceit"
    bl_label = "注册到 Faceit"

    def execute(self, context):
        lines = _register_faceit(_settings(context), context.active_object)
        _say(context, lines)
        if lines[0].startswith("!"):
            self.report({"ERROR"}, lines[0][2:])
            return {"CANCELLED"}
        return {"FINISHED"}


class OBJECT_OT_c2m_expr_reset(_Base, bpy.types.Operator):
    """本工具做的表情全部归零"""
    bl_idname = "object.c2m_expr_reset"
    bl_label = "归零"

    def execute(self, context):
        try:
            api.reset(context.active_object)
        except ValueError as exc:
            return self.fail(exc)
        return {"FINISHED"}


class OBJECT_OT_c2m_expr_stash(_Base, bpy.types.Operator):
    """把形态键暂存进一个隐藏副本（一键转换的「修正前腕弯曲」会跳过带形态键的网格）"""
    bl_idname = "object.c2m_expr_stash"
    bl_label = "转换前暂存形态键"

    def execute(self, context):
        try:
            out = api.stash(context.active_object)
        except ValueError as exc:
            return self.fail(exc)
        _say(context, ["暂存了 %d 个网格的形态键" % len(out), "转换完点「转换后恢复」"])
        return {"FINISHED"}


class OBJECT_OT_c2m_expr_restore(_Base, bpy.types.Operator):
    """把暂存的形态键放回去（转换把模型转向、缩放成米也跟着换算）"""
    bl_idname = "object.c2m_expr_restore"
    bl_label = "转换后恢复"

    def execute(self, context):
        try:
            rep = api.restore(context.active_object)
        except ValueError as exc:
            return self.fail(exc)
        lines = []
        for name, r in rep.items():
            if name == "_registered":
                lines.append("登记到 MMD 表情面板：%d 个" % r)
            else:
                lines.append("%s：%s" % (name, ("%d 个形态键" % r["keys"]) if isinstance(r, dict) else "! " + r))
        _say(context, lines or ["没有暂存的形态键"])
        return {"FINISHED"}


class OBJECT_OT_c2m_expr_restore_weights(_Base, bpy.types.Operator):
    """从 UE5 游戏包恢复脸部完整蒙皮权重（UE Viewer 每个顶点只导出 4 个）"""
    bl_idname = "object.c2m_expr_restore_weights"
    bl_label = "恢复完整权重"

    def execute(self, context):
        s = _settings(context)
        path = s.package_path or api.companion_package(s.dna_path)
        if not path:
            return self.fail("选游戏包（.uasset.bin），或把它放在 DNA 旁边、同名")
        try:
            rep = api.restore_weights(context.active_object, path)
        except _ERRORS as exc:
            return self.fail(exc)
        s.package_path = s.package_path or path
        lines = ["游戏包：最多 %d 个权重" % rep["package"]["max_influences"]]
        for name, r in rep["meshes"].items():
            lines.append("%s：%s" % (name, "对不上，跳过" if "skipped" in r else "补全 %d 个顶点" % r["rewritten"]))
        _say(context, lines)
        return {"FINISHED"}


class OBJECT_OT_c2m_expr_export_pmx(_Base, bpy.types.Operator):
    """mmd_tools 导出 PMX（默认 ×12.5、拷贝贴图）；ARKit 形态键不勾就不写进去"""
    bl_idname = "object.c2m_expr_export_pmx"
    bl_label = "导出 PMX"

    def execute(self, context):
        s = _settings(context)
        if not s.pmx_path:
            return self.fail("先选 PMX 输出路径")
        path = bpy.path.abspath(s.pmx_path)
        if not path.lower().endswith(".pmx"):
            path += ".pmx"
        try:
            rep = api.export_pmx(context.active_object, path, include_arkit=s.pmx_arkit, scale=s.pmx_scale)
        except _ERRORS as exc:
            return self.fail(exc)
        lines = ["导出：%s" % rep["path"]]
        if rep["parked"]:
            lines.append("（%d 个 ARKit 形态键没写进去）" % rep["parked"])
        _say(context, lines)
        return {"FINISHED"}


class OBJECT_OT_c2m_expr_capture(_Base, bpy.types.Operator):
    """把骨架当前姿势按名字记进姿势库（来源：姿势库）"""
    bl_idname = "object.c2m_expr_capture"
    bl_label = "记录当前姿势"

    def execute(self, context):
        s = _settings(context)
        if not s.capture_name:
            return self.fail("先填名字（MMD 名或 ARKit 名）")
        try:
            n = api.capture(context.active_object, s.capture_name)
        except ValueError as exc:
            return self.fail(exc)
        _say(context, ["记录「%s」：%d 根骨" % (s.capture_name, n),
                       "姿势库现有：%s" % "、".join(api.captured_names(context.active_object)[:8])])
        return {"FINISHED"}


class OBJECT_OT_c2m_expr_forget(_Base, bpy.types.Operator):
    """从姿势库删掉这个名字的姿势"""
    bl_idname = "object.c2m_expr_forget"
    bl_label = "删除该姿势"

    def execute(self, context):
        s = _settings(context)
        try:
            ok = api.forget(context.active_object, s.capture_name)
        except ValueError as exc:
            return self.fail(exc)
        _say(context, ["删除了「%s」" % s.capture_name if ok else "姿势库里没有「%s」" % s.capture_name])
        return {"FINISHED"}


class OBJECT_OT_c2m_expr_save_recipes(bpy.types.Operator):
    """把内置配方写成 JSON，改完当「配方文件」载入"""
    bl_idname = "object.c2m_expr_save_recipes"
    bl_label = "导出配方"
    filepath: bpy.props.StringProperty(subtype="FILE_PATH")  # type: ignore
    set_key: bpy.props.EnumProperty(items=[(k, v, "") for k, v in recipes.SETS.items()])  # type: ignore

    def invoke(self, context, event):
        self.filepath = "expression_recipes_%s.json" % self.set_key.lower()
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        _say(context, ["配方写到 %s" % api.save_recipes(self.set_key, self.filepath)])
        return {"FINISHED"}


# -- 手调: one expression at a time on the bones ----------------------------------------------------------
class _EditBase(_Base):
    def edit_args(self, context):
        s = _settings(context)
        return s, context.active_object, _src_args(s)


class OBJECT_OT_c2m_expr_edit_load(_EditBase, bpy.types.Operator):
    """把这个表情摆到骨架上（有手调用手调，没有用来源算的初稿），接着在姿势模式里改"""
    bl_idname = "object.c2m_expr_edit_load"
    bl_label = "载入到骨架"

    def execute(self, context):
        s, obj, args = self.edit_args(context)
        if not s.edit_name:
            return self.fail("先选一个表情")
        try:
            rep = api.edit_load(obj, s.edit_set, s.edit_name, s.source, use_manual=s.use_manual, **args)
        except _ERRORS as exc:
            return self.fail(exc)
        lines = ["「%s」%s：%d 根骨" % (s.edit_name, "手调版" if rep["kept"] else "初稿", rep["bones"]),
                 "在姿势模式里调脸部骨骼，满意后点「保存手调」"]
        if rep["against"]:
            lines.append("这个表情生成时会减去 jawOpen（ARKit 规定），这里摆的是「张嘴 + 合唇」的样子")
        _say(context, lines)
        return {"FINISHED"}


class OBJECT_OT_c2m_expr_edit_save(_EditBase, bpy.types.Operator):
    """把骨架上现在的脸部姿势保存为这个表情的手调版（生成时优先用它）"""
    bl_idname = "object.c2m_expr_edit_save"
    bl_label = "保存手调"

    def execute(self, context):
        s, obj, args = self.edit_args(context)
        if not s.edit_name:
            return self.fail("先选一个表情")
        try:
            n = api.edit_save(obj, s.edit_name, s.source, **args)
        except _ERRORS as exc:
            return self.fail(exc)
        _say(context, ["保存了「%s」的手调：%d 根骨" % (s.edit_name, n), "点「生成表情」重新烘焙后生效"])
        return {"FINISHED"}


class OBJECT_OT_c2m_expr_edit_forget(_EditBase, bpy.types.Operator):
    """删掉这个表情的手调版，回到来源算的初稿"""
    bl_idname = "object.c2m_expr_edit_forget"
    bl_label = "删除手调"

    def execute(self, context):
        s, obj, _args = self.edit_args(context)
        try:
            ok = api.edit_forget(obj, s.edit_name)
        except _ERRORS as exc:
            return self.fail(exc)
        _say(context, ["删除了「%s」的手调" % s.edit_name if ok else "「%s」没有手调" % s.edit_name])
        return {"FINISHED"}


class OBJECT_OT_c2m_expr_edit_mirror(_EditBase, bpy.types.Operator):
    """把骨架上现在的姿势左右镜像，保存为另一侧的表情（eyeBlinkLeft → eyeBlinkRight）"""
    bl_idname = "object.c2m_expr_edit_mirror"
    bl_label = "镜像到另一侧"

    def execute(self, context):
        s, obj, args = self.edit_args(context)
        try:
            other = api.edit_mirror(obj, s.edit_name, s.source, **args)
        except _ERRORS as exc:
            return self.fail(exc)
        s.edit_name = other
        _say(context, ["镜像保存为「%s」，已摆在骨架上" % other])
        return {"FINISHED"}


class OBJECT_OT_c2m_expr_edit_reset(_EditBase, bpy.types.Operator):
    """脸部骨骼回到静止姿势"""
    bl_idname = "object.c2m_expr_edit_reset"
    bl_label = "复位"

    def execute(self, context):
        s, obj, args = self.edit_args(context)
        try:
            api.edit_reset(obj, s.source, **args)
        except _ERRORS as exc:
            return self.fail(exc)
        return {"FINISHED"}


class OBJECT_OT_c2m_expr_edit_step(_EditBase, bpy.types.Operator):
    """上一个 / 下一个表情，并载入到骨架"""
    bl_idname = "object.c2m_expr_edit_step"
    bl_label = "上一个 / 下一个"
    step: bpy.props.IntProperty(default=1)  # type: ignore

    def execute(self, context):
        s, obj, args = self.edit_args(context)
        order = manual.target_names(s.edit_set)
        i = order.index(s.edit_name) if s.edit_name in order else -1
        s.edit_name = order[(i + self.step) % len(order)]
        try:
            api.edit_load(obj, s.edit_set, s.edit_name, s.source, use_manual=s.use_manual, **args)
        except _ERRORS as exc:
            _say(context, ["! %s" % exc])
        return {"FINISHED"}


# -- 面板内容(Convert to MMD 主面板第 3 页调用) ----------------------------------------------------
_SOURCE_HINT = {
    "AUTO": "给了 DNA 用 DNA（MMD + ARKit）；否则已有形态键 → 脸骨 → 骨骼脸",
    "FACEBONES": "MetaHuman 脸骨，不需要 DNA；只做 MMD 表情",
    "FF7": "游戏的表情姿势和口型，其余按骨骼算；数据在 <游戏>/_meta/face 会自动找到",
    "DNA": "游戏原始表情数据；脸部权重只剩 4 个时先「恢复完整权重」",
    "SHAPES": "混合模型自带的 ARKit 形态键；MMD 表情做成顶点表情",
    "ROLES": "按眼皮 / 眉 / 下巴 / 嘴唇 / 舌头骨骼自动摆",
}


def _fold(layout, s, prop, text):
    row = layout.row(align=True)
    row.prop(s, prop, icon="TRIA_DOWN" if getattr(s, prop) else "TRIA_RIGHT", icon_only=True, emboss=False)
    row.label(text=text)
    return getattr(s, prop)


def draw(layout, context):
    s = _settings(context)
    obj = context.active_object

    arm = _armature(obj)
    is_ff7 = arm is not None and ff7.is_ff7_face(arm)
    box = layout.box()
    box.label(text="来源", icon="SHAPEKEY_DATA")
    box.prop(s, "source", text="")
    if s.source == "DNA" or (s.source == "AUTO" and not is_ff7):
        box.prop(s, "dna_path", text="DNA" if s.source == "DNA" else "DNA(可选)")
    if s.source == "FF7" or (s.source == "AUTO" and is_ff7):
        box.prop(s, "ff7_data", text="表情数据")
    if s.source in _SOURCE_HINT:
        box.label(text=_SOURCE_HINT[s.source], icon="INFO")
    if s.source == "POSES":
        box.prop(s, "action")
        row = box.row()
        row.prop(s, "use_markers")
        row.prop(s, "neutral", text="中性")
        row = box.row(align=True)
        row.prop(s, "capture_name", text="")
        row.operator(OBJECT_OT_c2m_expr_capture.bl_idname, text="记录当前姿势", icon="REC")
        row.operator(OBJECT_OT_c2m_expr_forget.bl_idname, text="", icon="TRASH")
    row = box.row(align=True)
    row.operator(OBJECT_OT_c2m_expr_analyze.bl_idname, icon="VIEWZOOM")
    row.operator(OBJECT_OT_c2m_expr_check.bl_idname, icon="CHECKMARK")

    box = layout.box()
    box.label(text="输出（可多选）", icon="EXPORT")
    row = box.row(align=True)
    row.prop(s, "do_mmd")
    sub = row.row(align=True)
    sub.enabled = s.do_mmd
    sub.prop(s, "mmd_output", expand=True)
    if s.do_mmd:
        col = box.column(align=True)
        row = col.row(align=True)
        for p in ("cat_eye", "cat_brow", "cat_mouth", "cat_other"):
            row.prop(s, p, toggle=True)
        row = col.row(align=True)
        row.prop(s, "extras", toggle=True)
        row.prop(s, "replace", toggle=True)
        if _fold(box, s, "show_mmd_more", "强度 / 阈值 / 配方 / 误差预估"):
            col = box.column(align=True)
            for p, label in (("s_eye", "目 强度"), ("s_brow", "眉 强度"), ("s_mouth", "口 强度")):
                col.prop(s, p, text=label, slider=True)
            if s.mmd_output == "AUTO":
                box.prop(s, "threshold")
            box.prop(s, "recipe_file", text="配方")
            box.operator(OBJECT_OT_c2m_expr_estimate.bl_idname, icon="DRIVER_DISTANCE")
    box.prop(s, "do_arkit")
    if s.do_arkit:
        state, _pkg = faceit.faceit_state()
        if state != "enabled":
            box.label(text="Faceit %s：形态键照样生成，启用后再注册" % FACEIT_STATE[state], icon="ERROR")
        row = box.row(align=True)
        row.prop(s, "faceit_register")
        row.prop(s, "live_source", text="")
        if arm is not None:
            box.prop_search(s, "head_bone", arm.data, "bones")
        box.operator(OBJECT_OT_c2m_expr_register_faceit.bl_idname, icon="LINKED")
        if s.phone_hint:
            box.label(text=s.phone_hint, icon="INFO")

    row = layout.row(align=True)
    row.scale_y = 1.4
    row.operator(OBJECT_OT_add_face_morphs.bl_idname, text="生成表情", icon="PLAY")
    row = layout.row(align=True)
    row.operator(OBJECT_OT_remove_face_morphs.bl_idname, text="清除 MMD 表情", icon="TRASH").which = "MMD"
    row.operator(OBJECT_OT_remove_face_morphs.bl_idname, text="清除 ARKit", icon="TRASH").which = "ARKIT"
    if s.report:
        box = layout.box()
        for line in s.report.split("\n"):
            if line:
                box.label(text=line, icon="ERROR" if line.startswith("!") else "NONE")

    box = layout.box()
    kept = _kept(context)
    if _fold(box, s, "show_edit", "手调：逐个表情手动修改（已手调 %d 个）" % len(kept)):
        box.row().prop(s, "edit_set", expand=True)
        row = box.row(align=True)
        row.operator(OBJECT_OT_c2m_expr_edit_step.bl_idname, text="", icon="TRIA_LEFT").step = -1
        row.prop(s, "edit_name", text="")
        row.operator(OBJECT_OT_c2m_expr_edit_step.bl_idname, text="", icon="TRIA_RIGHT").step = 1
        row = box.row(align=True)
        row.operator(OBJECT_OT_c2m_expr_edit_load.bl_idname, icon="POSE_HLT")
        row.operator(OBJECT_OT_c2m_expr_edit_save.bl_idname, icon="FILE_TICK")
        row.operator(OBJECT_OT_c2m_expr_edit_forget.bl_idname, text="", icon="TRASH")
        row = box.row(align=True)
        sub = row.row(align=True)
        sub.enabled = bool(manual.mirror_name(s.edit_name))
        sub.operator(OBJECT_OT_c2m_expr_edit_mirror.bl_idname, icon="MOD_MIRROR")
        row.operator(OBJECT_OT_c2m_expr_edit_reset.bl_idname, icon="LOOP_BACK")
        box.prop(s, "use_manual")
        box.label(text="载入 → 姿势模式里调脸骨 → 保存手调 → 生成表情", icon="INFO")

    box = layout.box()
    if _fold(box, s, "show_preview", "预览"):
        row = box.row(align=True)
        row.prop(s, "preview_name", text="")
        row.prop(s, "preview_value", text="", slider=True)
        row.operator(OBJECT_OT_c2m_expr_reset.bl_idname, text="", icon="LOOP_BACK")
        box.label(text="导出前点归零（↺）", icon="INFO")

    box = layout.box()
    if _fold(box, s, "show_tools", "工具：恢复权重 / 暂存形态键 / 导出 PMX"):
        box.label(text="DNA 的脸权重只剩 4 个时（UE Viewer）：")
        box.prop(s, "package_path")
        box.operator(OBJECT_OT_c2m_expr_restore_weights.bl_idname, icon="GROUP_VERTEX")
        box.separator()
        box.label(text="模型本来带形态键：一键转换前暂存，转换后恢复")
        row = box.row(align=True)
        row.operator(OBJECT_OT_c2m_expr_stash.bl_idname, icon="EXPORT")
        row.operator(OBJECT_OT_c2m_expr_restore.bl_idname, icon="IMPORT")
        box.separator()
        box.prop(s, "pmx_path")
        row = box.row()
        row.prop(s, "pmx_arkit")
        row.prop(s, "pmx_scale")
        box.operator(OBJECT_OT_c2m_expr_export_pmx.bl_idname, icon="EXPORT")
        row = box.row(align=True)
        row.label(text="导出配方：")
        row.operator(OBJECT_OT_c2m_expr_save_recipes.bl_idname, text="MMD").set_key = recipes.MMD
        row.operator(OBJECT_OT_c2m_expr_save_recipes.bl_idname, text="ARKit").set_key = recipes.ARKIT


CLASSES = (C2M_ExprSettings, OBJECT_OT_add_face_morphs, OBJECT_OT_remove_face_morphs, OBJECT_OT_c2m_expr_analyze,
           OBJECT_OT_c2m_expr_check, OBJECT_OT_c2m_expr_estimate, OBJECT_OT_c2m_expr_register_faceit,
           OBJECT_OT_c2m_expr_reset, OBJECT_OT_c2m_expr_stash, OBJECT_OT_c2m_expr_restore,
           OBJECT_OT_c2m_expr_restore_weights, OBJECT_OT_c2m_expr_export_pmx, OBJECT_OT_c2m_expr_capture,
           OBJECT_OT_c2m_expr_forget, OBJECT_OT_c2m_expr_save_recipes, OBJECT_OT_c2m_expr_edit_load,
           OBJECT_OT_c2m_expr_edit_save, OBJECT_OT_c2m_expr_edit_forget, OBJECT_OT_c2m_expr_edit_mirror,
           OBJECT_OT_c2m_expr_edit_reset, OBJECT_OT_c2m_expr_edit_step)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.c2m_expr = bpy.props.PointerProperty(type=C2M_ExprSettings)


def unregister():
    del bpy.types.Scene.c2m_expr
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
