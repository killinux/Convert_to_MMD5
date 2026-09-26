import bpy, os, math

# alana 最小干预转换:源权重一律不动(跳过 transfer/腕捩切分/手掌修正),
# 只做结构(改名/补骨/IK/D骨/肩P/付与) + 衣服物理(无胸部物理)。
SRC = r"E:\Downloads\lezisell\alana (white veil)\xps.xps"
OUT = r"E:\Downloads\lezisell\alana (white veil)\pmx_export\alana_mmd.pmx"

def clean():
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o, do_unlink=True)
    for coll in (bpy.data.armatures, bpy.data.meshes, bpy.data.actions,
                 bpy.data.materials, bpy.data.images, bpy.data.cameras, bpy.data.lights):
        for d in list(coll):
            if d.users == 0:
                coll.remove(d)

def act():
    a = next(o for o in bpy.data.objects if o.type == 'ARMATURE' and 'backup' not in o.name.lower())
    try:
        bpy.ops.object.mode_set(mode='OBJECT')
    except Exception:
        pass
    bpy.ops.object.select_all(action='DESELECT')
    a.select_set(True)
    bpy.context.view_layer.objects.active = a
    return a

clean()
bpy.ops.xps_tools.import_model(filepath=SRC)
arm = act()
bpy.ops.object.clear_bone_selection()
bpy.ops.object.load_preset(preset_name="xna_lara")
bpy.ops.object.convert_to_apose()
print('[ok] apose')

act(); bpy.ops.object.correct_bones()
act(); bpy.ops.object.rename_to_mmd()
# —— 跳过 transfer_unused(不动任何源权重) ——
act(); bpy.ops.object.complete_missing_bones()
print('[ok] complete')

# 盆骨 helper 重挂到 下半身(权重不动,只让臀部跟随下半身旋转)
arm = act()
bpy.ops.object.mode_set(mode='EDIT')
eb = arm.data.edit_bones
pv = eb.get('unused bip001 pelvis')
lb = eb.get('下半身')
if pv and lb:
    pv.use_connect = False
    pv.parent = lb
    print('[ok] pelvis -> 下半身')
bpy.ops.object.mode_set(mode='OBJECT')

act(); bpy.ops.object.add_mmd_ik()
act(); bpy.ops.object.mode_set(mode='POSE'); bpy.ops.object.create_bone_group(); bpy.ops.object.mode_set(mode='OBJECT')
act(); bpy.ops.object.use_mmd_tools_convert()
act(); bpy.ops.object.add_leg_d_bones()
# —— 跳过 add_twist_bone / fix_palm_weights(不重刷手臂/手权重) ——
act(); bpy.ops.object.add_shoulder_p_bones()
try:
    act(); bpy.ops.object.setup_mmd_grants()
except Exception as e:
    print('grants:', e)
print('[ok] structure done')

# 物理:身体碰撞 + 衣服/头发(词表不含胸,无胸部物理)
act(); bpy.ops.object.add_body_rigids()
act(); bpy.ops.object.add_skirt_physics()
act(); bpy.ops.object.add_hair_physics()

# 衣服/身体先彻底分组隔离(用户要求):所有布刚体归组10=身体免碰组,
# 布与身体、布与布互不碰撞,纯关节悬挂;后续整体OK再逐步开碰撞。
n_iso = 0
for o in bpy.data.objects:
    if getattr(o, 'mmd_type', '') == 'RIGID_BODY' and str(o.mmd_rigid.type) in ('1', 'DYNAMIC'):
        if o.mmd_rigid.collision_group_number in (10, 11):
            o.mmd_rigid.collision_group_number = 10
            n_iso += 1
print('[ok] 布刚体全部隔离到组10:', n_iso)

# —— 验证 ——
arm = act()
bone_names = set(arm.data.bones.keys())
meshes = [o for o in bpy.data.objects if o.type == 'MESH' and
          any(md.type == 'ARMATURE' and md.object == arm for md in o.modifiers)]
holes = 0
for m in meshes:
    deform_idx = {vg.index for vg in m.vertex_groups if vg.name in bone_names}
    holes += sum(1 for v in m.data.vertices
                 if sum(g.weight for g in v.groups if g.group in deform_idx) < 0.05)
print('[verify] 孔洞:', holes)
for nm in ('unused bip001 l foretwist1', 'unused bip001 luparmtwist',
           'unused bip001 pelvis', 'unused shoulder_l'):
    c = 0; w = 0.0
    for m in meshes:
        vg = m.vertex_groups.get(nm)
        if not vg:
            continue
        gi = vg.index
        for v in m.data.vertices:
            for g in v.groups:
                if g.group == gi and g.weight > 0.01:
                    c += 1; w += g.weight
                    break
    print(f'[verify] {nm:30s} verts={c} totw={w:.1f}')
rigids = [o for o in bpy.data.objects if getattr(o, 'mmd_type', '') == 'RIGID_BODY']
chest = [o.mmd_rigid.bone for o in rigids if '胸' in (o.mmd_rigid.bone or '') or 'boob' in (o.mmd_rigid.bone or '')]
print('[verify] 胸部刚体:', chest if chest else '无 ✓', ' 刚体总数:', len(rigids))

# 导出
root = next(o for o in bpy.data.objects if getattr(o, 'mmd_type', '') == 'ROOT')

def descendants(o):
    out, st = [], list(o.children)
    while st:
        x = st.pop(); out.append(x); st.extend(x.children)
    return out

bpy.ops.object.select_all(action='DESELECT')
root.select_set(True)
for o in descendants(root):
    try: o.select_set(True)
    except Exception: pass
bpy.context.view_layer.objects.active = root
r = bpy.ops.mmd_tools.export_pmx(filepath=OUT, scale=12.5, copy_textures=True, log_level='ERROR')
print('[export]', r, os.path.getsize(OUT))
