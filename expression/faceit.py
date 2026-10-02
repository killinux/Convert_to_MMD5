# -*- coding: utf-8 -*-
"""Hook a model with ARKit shape keys into Faceit for live capture (Face Cap, Live Link Face, ...).

Does what Faceit's Setup -> Register Selected Object and Shapes -> Smart Match would do, but works
headless too (those operators touch the UI area) and also matches ARKit shapes spelled another way
(eyeBlink_L, EyeBlinkLeft, Eye_Blink_L ...); turns a model that doesn't face -Y (Faceit's head
rotation assumes it does).  Checked against Faceit 2.3.40.
Ported from ripper_tpose's expression_kit.  Faceit itself stays optional: nothing here imports it
until a model is registered."""
import importlib
import math
import socket

import addon_utils
import bpy
import numpy as np
from mathutils import Matrix, Vector

from . import names

HEAD_NAMES = ("head", "Head", "Bip001 Head", "Bip001_Head", "Bip01 Head", "J_Bip_C_Head", "頭",
              "mixamorig:Head", "CC_Base_Head", "DEF-spine.006", "c_head.x", "HEAD")
SOURCES = (("FACECAP", "Face Cap", "bannaflak Face Cap (iOS), OSC"),
           ("EPIC", "Live Link Face", "Epic Live Link Face (iOS)"),
           ("IFACIALMOCAP", "iFacialMocap", "iFacialMocap (iOS)"),
           ("TILE", "Hallway Tile", "Hallway Tile / Cube"))


def faceit_package():
    """Module name of the installed Faceit add-on (the folder may not be called 'faceit')."""
    for mod in addon_utils.modules(refresh=False):
        if mod.bl_info.get("name", "").upper() == "FACEIT":
            return mod.__name__
    return None


def faceit_state():
    name = faceit_package()
    if name is None:
        return "missing", None
    enabled = name in bpy.context.preferences.addons
    return ("enabled" if enabled else "disabled"), name


def _faceit(sub):
    name = faceit_package()
    if name is None:
        raise RuntimeError("没有安装 Faceit")
    return importlib.import_module(name + "." + sub)


def arkit_targets(meshes):
    """{ARKit name: sorted shape key names across the meshes} (any common spelling)."""
    out = {}
    for obj in meshes:
        if obj.data.shape_keys is None:
            continue
        for ark, key in names.match_arkit([k.name for k in obj.data.shape_keys.key_blocks[1:]]).items():
            out.setdefault(ark, set()).add(key)
    return {k: sorted(v) for k, v in out.items()}


def guess_head_bone(arm, meshes):
    bones = arm.data.bones
    for face_root in ("FACIAL_C_FacialRoot", "C_FaceBase_a"):        # MetaHuman, FF7 Remake / Rebirth
        root = bones.get(face_root)
        if root is not None and root.parent is not None:
            return root.parent.name
    for name in HEAD_NAMES:
        if name in bones:
            return name
    # else the non-facial bone carrying the most weight on the face meshes
    totals = {}
    for obj in meshes:
        group_names = {g.index: g.name for g in obj.vertex_groups}
        for v in obj.data.vertices:
            for g in v.groups:
                n = group_names.get(g.group, "")
                if n in bones and not n.upper().startswith("FACIAL_"):
                    totals[n] = totals.get(n, 0.0) + g.weight
    return max(totals, key=totals.get) if totals else ""


def _moved_centre(obj, key):
    """World centre of what a shape key moves, weighted by how far each vertex moves."""
    keys = obj.data.shape_keys
    n = len(obj.data.vertices)
    base = np.empty(n * 3)
    co = np.empty(n * 3)
    keys.reference_key.data.foreach_get("co", base)
    keys.key_blocks[key].data.foreach_get("co", co)
    base, co = base.reshape(-1, 3), co.reshape(-1, 3)
    d = np.linalg.norm(co - base, axis=1)
    if d.sum() <= 0.0:
        return None
    return obj.matrix_world @ Vector((base * d[:, None]).sum(0) / d.sum())


def facing(meshes):
    """The character's forward direction in world space (horizontal), told from where eyeBlinkLeft
    and eyeBlinkRight move the mesh: left eye minus right eye is the character's left, and
    forward = left x up.  None when no mesh has both keys."""
    for obj in meshes:
        if obj.data.shape_keys is None:
            continue
        ark = names.match_arkit([k.name for k in obj.data.shape_keys.key_blocks[1:]])
        if "eyeBlinkLeft" not in ark or "eyeBlinkRight" not in ark:
            continue
        left_eye, right_eye = _moved_centre(obj, ark["eyeBlinkLeft"]), _moved_centre(obj, ark["eyeBlinkRight"])
        if left_eye is None or right_eye is None:
            continue
        left = left_eye - right_eye
        left.z = 0.0
        if left.length < 1e-6:
            continue
        return left.normalized().cross(Vector((0.0, 0.0, 1.0)))
    return None


def heading_name(deg):
    for name, axis in (("+X", 0.0), ("+Y", 90.0), ("-X", 180.0), ("-Y", -90.0)):
        if abs((deg - axis + 180.0) % 360.0 - 180.0) < 10.0:
            return name
    return "%.0f°" % deg


def face_front(arm, meshes):
    """Turn the model about world Z until it faces -Y.  Faceit turns the phone's head rotation into
    Blender's as if the character faced -Y (front view), so a model facing +X (UE exports such as
    FF7) would tilt its head when nodding and nod when tilting.  Rotates the top parents of the
    armature and meshes about the armature's origin (object transforms only, nothing is applied).
    Returns (degrees turned, heading it had in degrees or None when it can't be told)."""
    fwd = facing(meshes)
    if fwd is None:
        return 0.0, None
    heading = math.degrees(math.atan2(fwd.y, fwd.x))
    turn = (-90.0 - heading + 180.0) % 360.0 - 180.0
    if abs(turn - 90.0 * round(turn / 90.0)) < 10.0:         # axis-aligned models turn by quarter turns
        turn = 90.0 * round(turn / 90.0)
    if turn == 0.0:
        return 0.0, heading
    roots = []
    for obj in [arm] + list(meshes):
        while obj.parent is not None:
            obj = obj.parent
        if obj not in roots:
            roots.append(obj)
    pivot = arm.matrix_world.translation.copy()
    rot = Matrix.Translation(pivot) @ Matrix.Rotation(math.radians(turn), 4, "Z") @ Matrix.Translation(-pivot)
    for obj in roots:
        obj.matrix_world = rot @ obj.matrix_world
    bpy.context.view_layer.update()
    return turn, heading


def is_registered(scene, meshes):
    names = {i.name for i in getattr(scene, "faceit_face_objects", [])}
    targets = sum(1 for e in getattr(scene, "faceit_arkit_retarget_shapes", []) if len(e.target_shapes))
    return all(o.name in names for o in meshes) and targets > 0, targets


def register(scene, arm, meshes, head_bone="", source="FACECAP", require_enabled=True):
    """Register the meshes + ARKit targets + head bone with Faceit and set the live source.
    Returns a report dict.  Needs Faceit enabled (it reads its own preferences while streaming);
    a headless run that only enabled it for the session passes require_enabled=False."""
    state, _name = faceit_state()
    if state == "missing" or (require_enabled and state != "enabled"):
        raise RuntimeError("Faceit %s：先在 偏好设置 → 插件 里启用" % {"missing": "未安装", "disabled": "未启用"}.get(state, state))
    fdata = _faceit("core.faceit_data")
    regions = _faceit("core.retarget_list_utils")
    handlers = _faceit("core.faceit_handlers")

    targets = arkit_targets(meshes)
    if not targets:
        raise RuntimeError("%s 都没有 ARKit 形态键：先生成" % ", ".join(o.name for o in meshes))
    with_keys = [o for o in meshes if o.data.shape_keys is not None]
    turned, heading = face_front(arm, with_keys)

    # Setup tab: registered objects (kept if already there) + the body armature
    items = scene.faceit_face_objects
    for obj in with_keys:
        if obj.name not in items:
            item = items.add()
            item.name = obj.name
            item.obj_pointer = obj
    scene.faceit_body_armature = arm

    # Shapes tab: the ARKit list with its targets (what Smart Match fills)
    if scene.faceit_retargeting_naming_scheme != "ARKIT":
        scene.faceit_retargeting_naming_scheme = "ARKIT"
    lst = scene.faceit_arkit_retarget_shapes
    lst.clear()
    missing = []
    for name, data in fdata.get_arkit_shape_data().items():
        entry = lst.add()
        entry.name = name
        entry.display_name = data["name"]
        for key in targets.get(name, []):
            entry.target_shapes.add().name = key
        if name not in targets:
            missing.append(name)
    regions.set_base_regions_from_dict(lst)

    # Mocap tab: head rotation on the head bone, eyes through the eyeLook shape keys (they turn
    # the eyeball AND move the lids; rotating the eye bones as well would turn the eyes twice)
    head_bone = head_bone or guess_head_bone(arm, meshes)
    scene.faceit_head_target_object = arm
    scene.faceit_head_sub_target = head_bone
    handlers.register_mocap_engine_defaults(scene)
    for engine in scene.faceit_live_mocap_settings:
        if engine.name == "A2F":
            continue
        engine.animate_shapes = True
        engine.animate_head_rotation = True
        engine.animate_head_location = False
        engine.animate_eye_rotation_shapes = True
        engine.animate_eye_rotation_bones = False
    scene.faceit_live_source = source
    settings = scene.faceit_live_mocap_settings.get(source)
    return {"objects": [o.name for o in with_keys], "targets": 52 - len(missing), "missing": missing,
            "head": "%s / %s" % (arm.name, head_bone), "source": source,
            "port": settings.port if settings else None, "lan_ip": lan_ip(),
            "turned": turned, "facing": heading_name(heading) if heading is not None else ""}


def lan_ip():
    """This PC's address on the local network (what the phone app needs); nothing is sent."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()
