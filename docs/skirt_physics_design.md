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
