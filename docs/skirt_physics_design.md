# 裙子物理（刚体 + 关节）实现设计

XPS→MMD 转换后，给裙子加 MMD 标准的「裙骨 + 刚体 + 关节」物理，使其在 VMD 动作下自然飘动。
**复用已有裙骨、复用 mmd_tools**，并对其它模型自动适配。

## 1. 调研结论（cartilla white rose 为样本）

| 项 | 源 XPS | 目标 PMX | 转换后（我们的模型） |
|---|---|---|---|
| 裙骨 | 16 根 `skirt {left/right/back left/back right} {1..4}` | **同名 16 根** | **16 根全存活**，链式父子完整（seg2→seg1…） |
| 刚体 | 无 | 16 个（每骨 1 个） | 无（本工具补） |
| 关节 | 无 | 16 个，链式 | 无（本工具补） |
| 锚 | — | `下半身` 一个 type=0 kinematic 刚体 | 需补（裙根现挂 `unused trash 17`） |

**核心洞察**：裙骨源/目标**同名且转换后存活**，所以**不造骨、不重刷权重**——只给现成裙骨补刚体+关节即可复现目标飘动。

## 2. 目标实测参数（作为默认值）

与尺度**无关**的参数直接抄目标；**尺寸**必须按转换模型的骨骼几何重算（目标尺寸是它自己 ≈12× 的 PMX 尺度）。

- **锚 `下半身`**：shape=CAPSULE，type=0(kinematic)，group=0。
- **裙刚体**：shape=BOX，type=1(dynamic)，group=11(0-based)，
  mass=1.0，linear_damping=0.9，angular_damping=0.99，friction=0，restitution=0，
  no-collision 掩码（不与这些组碰撞）= `[1,2,8,9,10,11,15]`。
  目标 BOX size=(0.25, 2.0, 1.5) 即 (厚, 长沿骨, 宽)，比例 厚:长:宽 ≈ 0.125 : 1 : 0.75。
- **关节**：线位限位全 0（锁死），角限位 X=±30° Y=±10° Z=±5°，spring_linear=0，spring_angular=(30,30,30)。
  关节摆在**子骨头部**（两刚体之间）。

## 3. 自适应算法（通用于其它模型）

1. **找裙链**（不写死 4×4）：正则 `skirt|スカート`（可扩展词表）匹配裙骨；按父子关系组链——父骨非裙骨者=链根(seg1)，顺裙骨子级取 seg2/3/4…。任意「N 片 × M 节」自动适配。
2. **锚定**：确保 `下半身` 有一个 kinematic(type0) 刚体，没有就建。裙链 seg1 的关节统一连到它，**不管裙根骨当前父级是谁**（解决 cartilla 挂 `unused trash 17` 的问题）。
3. **每根裙骨 → 1 个动力学刚体**（`Model.createRigidBody`）：BOX，沿骨朝向；尺寸由几何自适应——
   - 长(沿骨) = 该段骨长（head→child.head，末节用 head→tail）；
   - 宽 = 与同层相邻片裙骨的间距/2（测不到则 0.7×长）；
   - 厚 = 0.15×长（薄）。
   其余（mass/damp/friction/group/mask）抄目标。
4. **每根裙骨 → 1 个关节**（`Model.createJoint`）：seg1 接 `下半身锚→seg1`，seg_n 接 `seg_{n-1}→seg_n`；摆在子骨头部；角限位/弹簧抄目标。
5. **build**：`Model.build()` / `updateRigid` 让物理生效。

## 4. 复用 mmd_tools（已验证 API）

- `mmd_tools.core.model.Model(root).createRigidBody(**kw)` / `.createJoint(**kw)` 直接建标准 mmd 刚体/关节（参数：shape/location/size/dynamics_type/collision_group_number/collision_group_mask/mass/friction/bounce/(lin|ang)_damping；rigid_a/rigid_b/maximum|minimum_(location|rotation)/spring_*）。
- 不手搓 `rigid_body_constraint`，交给 mmd_tools 统一管，导出 PMX 即标准数据。

## 5. 插件落地

- tab2 第二个按钮「裙子刚体/物理(自动)」= `object.add_skirt_physics`。
- 流程：识别裙链 → 建/复用 `下半身` 锚 → 逐骨建刚体 → 逐骨建关节 → build。
- 无裙骨的模型自动跳过、报告 0。

## 6. 待调/已知点

- 源是 T-pose 的模型（cartilla 手臂 2°）按既有规则可先 0.5 转 A-pose，与裙子物理独立。
- 宽度估法首版用「相邻片间距/2」，不准再调。
- 碰撞掩码照搬目标（裙不自撞、撞腿/身体）。

## 7. 2026-07-04 v2:泛化为布物理 + 身体碰撞刚体（cartilla/alana 标定）

**功能**
- 词表从 skirt 扩为 `skirt|coat|cloak|cape|mantle|shawl|veil|scar[ft]|hangings|drape|apron|robe|frill|sash|ribbon`，
  头发另配 `hair|ponytail|twintail|braid`（CAPSULE、限位松、可开关）。
- 改**按骨处理**（支持 cloak 5→6/7 分叉），不再假设线性链。
- 锚:沿父链向上找第一个标准骨（shawl→肩、head scarf→頭、scart→足D、skirt→下半身），缺刚体自动补 kinematic。
- 新增 `add_body_rigids`:頭/首/上半身系/下半身/腿FK/腕/ひじ 的 kinematic 碰撞刚体，
  **半径按网格顶点实测**（该骨权重>0.3 顶点到骨轴径向距离 85 分位）；腿用 FK 骨（足D 是竖直短桩，盖不住腿）。
- 横关节（MMD 裙标准技法）:相邻链同层、静距 <1.2×段长的刚体互连（线锁/角±40°/无弹簧），
  防跨多链大面片被独立摆动拉扯。

**踩坑（重要）**
1. **算子里不要 `model.build()`**:build 把布骨绑上动力学刚体，导出前场景一经求值刚体就位移、
   布骨跟走，**PMX 绑定位被污染**→关节出生即违反 2m→全场爆炸。此坑也会由**场景残留的
   `rigidbody_world`（scene 级，清物件不清它）**触发，两个算子开头都要 `rbw.enabled=False`。
2. 贴身段用**几何规则**入免撞组10（出生位与身体胶囊重叠+余量），不只链根深度——大纱上半段全贴身。
3. 段向量指向子骨时要防分叉枝端异常远（>2.5×tail 回退用 tail），并钳制刚体最小/最大尺寸。
4. 弹簧刚度按段长²缩放（基准 0.15m）:惯量∝L²，短段配满刚度会超求解稳定域。
5. Blender Bullet 是烟囱测试:锁线关节在高速锚运动下会被拉伸（甩尾偏夸张），MMD 本体更稳；
   最终效果以 MMD 实测为准。

**已知限制**:alana 型「巨型多片纱+刚性挂饰(光环挂在 head scarf back 链上)」自动物理会失真，
需手动在 PMXEditor 里删挂饰刚体或改 kinematic。

## 8. 2026-07-04 v3:参考实测定参 + 权重湮灭修复（决定性结论）

**参考 PMX 实测（Purifier Inase 18 None.pmx，19 kinematic + 16 dynamic）**:
发链 CAPSULE、全轴 **±10°**、**弹簧全 0**、关节朝向恒等、无横关节、mass 1.0/ld 0.90/ad 0.99/fr 0，
动组 9 不撞 {1,8,9,10,11,15}。→ 头发参数照抄；布保留 white-rose 裙实测(±30/10/5+弹簧30)；
横关节与关节系对齐两项过度工程已删/回退。

**权重湮灭 bug（transfer.py，「乱套」主因之一）**:分类器把居中的具名饰骨(hangings cross/
shawl back middle)判 merge → 转移目标列表里包含它们自己 → 最近骨=自己 → 自转移后顶点组被删
→ 权重湮灭 → mmd_tools 导出把零权重顶点兜底绑 全ての親 → 跳舞时该块钉在原地拉成横板。
修复:①转移目标排除待清空骨；②具名布/发骨(词表命中、非 unused 前缀)不参与 merge，留权重给物理。
自愈路径说明:pelvis 类权重在 1.4 步先进无骨的 下半身 组、complete 建骨后生效，中途扫描是"假孔洞"。

**决定性实验**:把参考 PMX 本体丢进同一 Blender 舞蹈仿真 → 其头发/胸部同样横飘成直线。
证明「快速动作下布链横飘」是 Blender/mmd_tools 关节限位模拟失真(±10° 限位下 7 段链物理上
不可能横直)，非导出数据缺陷。**Blender 仿真只可用于:爆炸/穿身/结构/静置回落检验；
动态手感必须 MMD 实测。**静置 60 帧位移 <0.17m = 结构健康的判据。

## 9. 2026-07-04 v4:MMD 实测爆炸 → 布关节朝向必须对齐板面（修正 v3 的错误回退）

alana 导出后 Blender 全套检验通过（无爆炸/孔洞0/静置回落正常），但 **MMD 实测爆炸**。
用 mmd_tools 的 pmx reader 把我们的导出与已知稳定的参考模型（Bishojou Jason Inase，同作者
手调裙物理）逐字段 dump 对比，唯一的结构性差异:

- **参考裙关节逐个对齐板面朝向**（yaw 沿裙环 -180→±161→±135→±90→…→0，pitch 贴合裙锥
  坡度），PMX 轴系 = X 切向(±30 外摆) / Y 沿骨(±5 扭转) / Z 径向(±10 横摆)；
- 我们 79 个布关节全部恒等朝向 (0,0,0)——±30/5/10 各向异性限位套在世界轴上。

**机制**:MMD 用老版 Bullet 2.75（软约束），6DOF 限位做欧拉分解时 **Y 是奇异轴**（±90°
asin 退化）。恒等朝向下所有关节的 Y=世界竖直轴，快速转身（摇香有原地转体）时相对 yaw 打穿
奇异区 → 限位扭矩方向错乱、能量注入 → 全场爆炸。参考模型把永远小角度的**沿骨扭转轴(±5°)**
放在 PMX Y 上，天然避开。Blender 的新版 Bullet（硬约束+修过的欧拉分解）能容忍恒等系——
**所以 Blender 仿真测不出这一类 MMD 爆炸**（与 mmd_tools 官方「MMD 老 Bullet 软约束无法在
Blender 复现」的说明一致）。

修复（skirt.py `_joint_frame`）:布关节朝向 = 与刚体同源的板面系（Blender X=切向/Y=径向/
Z=沿骨，经 mmd_tools 轴变换后逐轴等于参考实测）；限位数值不变。**恒等朝向只对各向同性
(±10 全轴、零弹簧)的发关节成立**——v3 说「关节系对齐是过度工程」是回退错了，它是 MMD
稳定性的承重项。

**连带踩坑——欧拉序**:mmd_tools 的刚体/关节 empty 是 `rotation_mode='YXZ'`(MMD 惯例)，
`createRigidBody/createJoint(rotation=...)` 原样赋给 `rotation_euler`。传默认 `to_euler()`
(XYZ) 的值会被按 YXZ 错序解释——竖直段侥幸接近、**水平段(shawl/披肩)完全歪掉**
(数值回归:关节局部 Z 对段向量 |cos| 从 0.05 修到 1.000)。所有传 mmd_tools 的欧拉
(_box_frame/_bone_frame/_joint_frame/发胶囊)一律 `to_euler('YXZ')`。此 bug 同样歪了
布 BOX/身体胶囊/发胶囊的形状朝向,当前无碰撞未显形,开碰撞前必须已修。

**验证判据(用文件级,别用重导入量场景)**:`pmx.load` 后按 importer 公式还原
`Euler((-rx,-rz,-ry),'YXZ')`,取其 Z 轴对 `(rigidB.loc−joint.loc).xzy`,布关节应
|cos|=1.000(参考模型 ≥0.896,含刻意的锥面 pitch 偏置)。**重导入后量 matrix_world
不可靠**:mmd_tools 导入会自建 *enabled* 的 rigidbody world,ACTIVE 刚体在 depsgraph
求值时被物理挪位,读数被污染(本次曾因此误判文件损坏);另外新建对象的 matrix_world
需 view_layer.update() 后才有效。mmd_tools 的 euler 往返(YXZ 分量 .xzy*-1)代数上自逆,
导出↔导入恒忠实,怀疑对象应先排除测量方法。

排除项（同一 dump 证据）:碰撞掩码正确（布=组10 与一切互不碰撞；参考的乳奶刚体 mask=0x0000
零碰撞照样稳定，排除"没碰撞导致爆炸"）；质量/阻尼/弹簧/尺寸逐项与参考同量级。
另:参考裙用「只撞身体组0」(mask 0x0001)，隔离验证通过后放开碰撞时照抄这个,而不是全开。

遗留观察:cloak 5→6/7 分叉的枝端关节杠杆臂 6~8×刚体尺寸（参考最大 ~1×），Blender 里就是
最差帧元凶;若 MMD 修完关节系仍局部不稳,优先处理这里(加大分叉父刚体或改锚)。

MMD 侧排查清单(社区 Tips):播放前把物理演算设为「常に演算」(默认"再生時のみ"会在第 1 帧
跳变);爆炸多为 关节初始违约 / 出生穿插 / 限位轴系错误 三类。
