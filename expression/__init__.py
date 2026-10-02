# -*- coding: utf-8 -*-
"""表情:多种来源、两种输出(都是可选项)的角色表情,对应面板第 3 页「表情」。

输出:
  MMD 表情        PMX 骨骼 morph / 顶点 morph,或逐个表情按 PMX 4 权重误差自动选
  Faceit(ARKit)  52 个 ARKit 形态键,注册到 Faceit 给 iPhone 面捕实时驱动
来源:MetaHuman 脸骨(convert/face.py,不需要 DNA)、MetaHuman DNA、模型自带的 ARKit 形态键、
姿势库、骨骼脸自动配方;默认「自动」按模型选。

除「脸骨」来源和「自动」外,移植自 ripper_tpose 的 expression_kit(引擎、配方、DNA 求值、
UE5 完整权重恢复、Faceit 注册、PMX 导出),对象上的标记与它通用。设计与验证见
docs/expression_design.md。
"""
from . import ui


def register():
    ui.register()


def unregister():
    ui.unregister()
