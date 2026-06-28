# CLAUDE.md

Convert to MMD 5 — XPS/XNALara 骨架一键转 MMD 的 Blender 插件(骨骼管理引擎重构版)。

## 测试约定

- **测试的高度都以目标对齐**:验证转换结果时,把转换出来的模型**缩放到与目标 PMX 等高**(用根 empty 的 scale,缩放比 = 目标身高 / 转换身高,本测试集约 11.91×),再并排对比。不要用米尺度的小模型比,位移/偏差数值会太小看不清。
- 直接后果——**VMD 导入尺度**:转换模型是「米」尺度,给它导入 VMD 必须用 `scale = 1 / 缩放比`(≈0.084);目标 PMX 用 `scale = 1.0`。否则 IK 腿会崩到 60~100°(FK 旋转与尺度无关,不受影响)。
- VMD 从干净场景**一次性按正确 scale 导入**,不要先 1.0 再清空重导(会污染足IK首帧)。
- 骨名映射:转换模型 MMD 名在 `bone.name`(如 `左腕`);目标 PMX 在 `mmd_bone.name_j`。对比时分别取。
- 端到端测试脚本:`test/vmd_compare_test.py`(顶部 CONFIG 放素材绝对路径),详见 `test/README.md`。

## 远端 Blender

- 通过 BlenderMCP(`mcp__blender__*`)在远端 Windows Blender 3.6.15 执行 Python。
- 机器相关值(主机/密码/路径/素材)在 `docs/remote.local.md`(已 gitignore);连接与部署流程见 `docs/TESTING.md`。

## 本地 Blender(macOS)

- 通过 BlenderMCP(`mcp__blender-local__*`)在本机 Blender 3.6 执行 Python(注意是 `-local` 这套工具,不是远端的 `mcp__blender__*`)。依赖 `mmd_tools` + `XNALaraMesh` 需已启用。
- **装插件**:把仓库目录符号链接进本机 addons 目录,再启用,这样改了仓库代码、重载即生效:
  - `ln -s <repo> ~/Library/Application\ Support/Blender/3.6/scripts/addons/Convert_to_MMD5`
  - 启用前先 `addon_utils.disable` 掉同类旧插件(如 `Convert_to_MMD4`),清 `sys.modules` 里所有 `Convert_to_MMD5*`,再 `addon_utils.enable("Convert_to_MMD5")` + `save_userpref()`。
  - **必须确认生效引擎**:`bpy.types.OBJECT_OT_one_click_convert.__module__` 应为 `Convert_to_MMD5.convert.pipeline`,否则跑的是别的版本。
- **端到端脚本**:`test_local.py`(仓库 `test/vmd_compare_test.py` 的本机版,只改顶部 4 个 CONFIG 路径),`exec` 整段后 `run()` 自动跑全流程并打印 PASS/FAIL,渲染 `compare_f{1,60,150}.png`。
- **macOS Downloads 权限坑**:我的 Bash/Read 工具读 `~/Downloads/...` 会 `Operation not permitted`,但 Blender 进程本身能读写。所以:① 用 Blender 的 `os.listdir`/`open` 核对那里的素材;② 渲染图写到 Downloads 后,用 Blender 的 `shutil.copy2` 拷到 scratchpad 再 Read 查看。
- 已验证基线(`xps-b.xps` → `Purifier Inase 18 V1.pmx`,摇香 VMD):转换 19/19 步、缩放比 11.91×、**FK σ 全 0.000、腿部 IK 最差帧 4.4° → PASS**。
- **A-Pose 工具(从 MMD6 移植,`object.convert_to_apose`,UI 手动分步「0.5 转换为 A-Pose」)**:源判定为 T-Pose 时用它先转 A-pose 再走流程(identify 后、one_click 前),源已是 A-pose 则跳过。上臂绕全局 Y 倒到目标角(默认36°,实测落到~54°)+ 拉直肘,网格用复制骨架修改器烘焙、形态键退避复原。**不强求与目标手臂完全对齐**(A-pose 是为模型规范,不是贴某个目标 PMX)。已跨 inase arbiter / keleira 验证:转换 16/16、腿 IK PASS;FK σ≈0.5–0.8 的小抖动是该朴素法固有,视觉/动作无碍。

## 协作原则(Karpathy guidelines 提炼)

- **先想再写**:不做隐性假设;遇到不清楚的地方,把困惑和取舍显式说出来,而不是默默猜一个方向往下做。
- **简单优先**:写满足当前需求的最小代码,不加投机性功能、不过度工程化。
- **外科手术式改动**:只改必要的地方,不顺手重构无关代码(呼应权重链「避免反复改同一批顶点」)。
- **目标驱动**:动手前先定义可验证的成功标准(如本项目的 FK σ / IK 最差帧 → PASS),然后迭代到达成为止。
- 来源:https://github.com/multica-ai/andrej-karpathy-skills

## 权重处理原则

- 重分配一律**位置驱动 + 守恒**(twist 按轴向 t、palm 按手掌深度 d、debleed 按拇指轴 u + 前腕位置斜坡),**不要 per-target 魔数**。
- 手部权重链:`twist`(步7,切 腕/ひじ 捩骨 + 回收手首前腕段)→ `palm`(步7.5,debleed 親指０ + 掌部分掌骨)。手部权重的最后一次编辑在 7.5,改手部分法就改这里,避免反复改同一批顶点。
