# CLAUDE.md

Convert to MMD 2 — XPS/XNALara 骨架一键转 MMD 的 Blender 插件(骨骼管理引擎重构版)。

## 测试约定

- **测试的高度都以目标对齐**:验证转换结果时,把转换出来的模型**缩放到与目标 PMX 等高**(用根 empty 的 scale,缩放比 = 目标身高 / 转换身高,本测试集约 11.91×),再并排对比。不要用米尺度的小模型比,位移/偏差数值会太小看不清。
- 直接后果——**VMD 导入尺度**:转换模型是「米」尺度,给它导入 VMD 必须用 `scale = 1 / 缩放比`(≈0.084);目标 PMX 用 `scale = 1.0`。否则 IK 腿会崩到 60~100°(FK 旋转与尺度无关,不受影响)。
- VMD 从干净场景**一次性按正确 scale 导入**,不要先 1.0 再清空重导(会污染足IK首帧)。
- 骨名映射:转换模型 MMD 名在 `bone.name`(如 `左腕`);目标 PMX 在 `mmd_bone.name_j`。对比时分别取。
- 端到端测试脚本:`test/vmd_compare_test.py`(顶部 CONFIG 放素材绝对路径),详见 `test/README.md`。

## 远端 Blender

- 通过 BlenderMCP(`mcp__blender__*`)在远端 Windows Blender 3.6.15 执行 Python。
- 机器相关值(主机/密码/路径/素材)在 `docs/remote.local.md`(已 gitignore);连接与部署流程见 `docs/TESTING.md`。

## 权重处理原则

- 重分配一律**位置驱动 + 守恒**(twist 按轴向 t、palm 按手掌深度 d、debleed 按拇指轴 u + 前腕位置斜坡),**不要 per-target 魔数**。
- 手部权重链:`twist`(步7,切 腕/ひじ 捩骨 + 回收手首前腕段)→ `palm`(步7.5,debleed 親指０ + 掌部分掌骨)。手部权重的最后一次编辑在 7.5,改手部分法就改这里,避免反复改同一批顶点。
