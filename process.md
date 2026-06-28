# 进度记录 process.md

接力用。每完成一块更新这里，方便后面接着做。

## 当前任务：裙子物理（刚体+关节）自动生成

设计见 `docs/skirt_physics_design.md`。目标：转换后给现成裙骨补 mmd 刚体+关节，复现目标 PMX 飘动，自适应其它模型。

### 进度

- [x] 调研 cartilla 源 XPS：16 裙骨 `skirt {left/right/back left/back right} {1..4}`，4 片×4 节。
- [x] 调研目标 PMX：16 裙骨(同名) + 16 刚体(BOX/type1/group11) + 16 关节(链式 `下半身→1→2→3→4`)，锚=下半身 CAPSULE/type0。
- [x] 确认转换后 16 裙骨全存活、链式父子完整；裙根父骨=`unused trash 17`（非下半身，锚定时处理）。
- [x] 提取目标实测参数当默认值（见设计文档 §2）。
- [x] 确认 mmd_tools API：`Model.createRigidBody/createJoint` 可用。
- [x] 实装 `object.add_skirt_physics`（`convert/skirt.py`），注册 + tab2 第二按钮。
- [x] 几何验证通过：cartilla 4 链自适应识别 → 16 BOX 刚体贴裙面、1 CAPSULE 锚(下半身)、16 关节在裙骨头部。坑:`shape_type` 是 SPHERE=0/BOX=1/CAPSULE=2(别填错);kinematic 锚 build 后被父级到骨，dump 的 loc 是局部坐标(世界位置正确)。
- [ ] **下一步**：物理飘动效果测试 —— 转换+裙子物理 → 导入目标 PMX → 缩放等高并排 → 导入 yaoxiang VMD → **bake 刚体模拟** → 渲染对比看飘动。
  - 关键点:刚体模拟要 bake(顺序模拟，不能跳帧);Blender 刚体对极小物体(米尺度下裙盒~1-2cm)模拟可能不稳，缩到 ~12× 后(~15-25cm)反而更稳——建议在缩放后再 bake。
  - 需确认 `scene.rigidbody_world` 在 `model.build()` 后就绪，设 point_cache 帧范围后 bake_all。
- [ ] 调参（宽度估法 / 碰撞 / 限位）到效果满意。

### 验证素材（cartilla）
- SRC_XPS: `/Users/bytedance/Downloads/convert_test/xps/cartilla (white rose)/xps.xps`
- TGT_PMX: `/Users/bytedance/Downloads/convert_test/mmd/Cartilla White Rose 18 tifa/Cartilla White Rose 18.pmx`
- VMD: `/Users/bytedance/Downloads/convert_test/yaoxiang/yaoxiang.vmd`
- 源是 T-pose（手臂 2°），按规则可先 0.5 转 A-pose（与裙子物理独立）。

### 注意
- 刚体**尺寸**按转换模型骨骼几何自适应（目标尺寸是它自己 ≈12× 尺度，不能照抄绝对值）；阻尼/质量/组/掩码/关节限位抄目标。
- 物理测试要让 VMD 跑起来 + 物理 bake 才看得到飘动；静态对比看不出。
