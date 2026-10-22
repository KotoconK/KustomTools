
bl_info = {
    "name": "KustomTools",
    "author": "Álvaro_A",
    "version": (2, 0, 1),
    "blender": (5, 0, 0),
    "location": "View3D > Sidebar > Kustom Tools",
    "description": "General art, modeling, rigging and integrated motion-retarget tools",
    "category": "3D View",
}

import bpy
import bmesh
import urllib.request
import os
import math
from mathutils import Matrix, Vector
from mathutils.bvhtree import BVHTree
from bpy_extras import view3d_utils
import re
import json
import difflib
import shutil
from bpy.types import Operator, Panel, PropertyGroup, UIList
from bpy.props import (
    BoolProperty,
    CollectionProperty,
    EnumProperty,
    FloatProperty,
    IntProperty,
    PointerProperty,
    StringProperty,
)

try:
    from bpy_extras.anim_utils import action_ensure_channelbag_for_slot
except Exception:
    action_ensure_channelbag_for_slot = None

ADDON_VERSION = bl_info["version"]
addon_keymaps = []

# ------------------------------------------------------------
# VIEWPORT TOOLS
# ------------------------------------------------------------

def apply_viewport_background(scene):
    """Apply the custom background in Edit Mode and restore the theme outside it."""

    obj = bpy.context.object
    is_edit_mode = obj is not None and obj.mode == 'EDIT'
    enabled = getattr(scene, "ct_bg_enabled", False)
    color = getattr(scene, "ct_edit_bg_color", (0.05, 0.05, 0.05))

    wm = bpy.context.window_manager
    if wm is None:
        return

    for window in wm.windows:
        screen = window.screen
        if screen is None:
            continue

        for area in screen.areas:
            if area.type != 'VIEW_3D':
                continue

            for space in area.spaces:
                if space.type != 'VIEW_3D':
                    continue

                shading = space.shading

                if enabled and is_edit_mode:
                    shading.background_type = 'VIEWPORT'
                    shading.background_color = color
                else:
                    shading.background_type = 'THEME'

            area.tag_redraw()


def update_viewport_background(self=None, context=None):
    """Property callback: refresh the viewport when the background color changes."""

    scene = None

    if context is not None:
        scene = context.scene
    elif isinstance(self, bpy.types.Scene):
        scene = self
    else:
        scene = getattr(bpy.context, "scene", None)

    if scene is not None:
        apply_viewport_background(scene)


def viewport_mode_handler(scene, depsgraph=None):
    """Refresh the background after depsgraph updates, including mode changes."""

    if not hasattr(scene, "ct_bg_enabled"):
        return

    apply_viewport_background(scene)


class CT_OT_enable_dynamic_bg(bpy.types.Operator):
    bl_idname = "ct.enable_dynamic_bg"
    bl_label = "Toggle Edit Background"
    bl_description = "Enable or disable the custom viewport background while in Edit Mode"

    def execute(self, context):
        scene = context.scene
        scene.ct_bg_enabled = not scene.ct_bg_enabled

        apply_viewport_background(scene)

        if scene.ct_bg_enabled:
            self.report({'INFO'}, "Edit Mode background enabled")
        else:
            self.report({'INFO'}, "Edit Mode background disabled")

        return {'FINISHED'}


class CT_OT_set_active_object_color(bpy.types.Operator):
    bl_idname = "ct.set_active_object_color"
    bl_label = "Set Active Object Color"

    def execute(self, context):
        theme = bpy.context.preferences.themes[0]
        color = context.scene.ct_active_obj_color

        theme.view_3d.object_active = color

        return {'FINISHED'}

# ------------------------------------------------------------
# Helpers
# ------------------------------------------------------------
def hex_to_linear_rgb(hex_color):
    hex_color = hex_color.lstrip('#')
    srgb = [int(hex_color[i:i+2], 16)/255 for i in (0, 2, 4)]

    def to_linear(c):
        if c <= 0.04045:
            return c / 12.92
        else:
            return ((c + 0.055) / 1.055) ** 2.4

    return tuple(to_linear(c) for c in srgb)

def set_status(context, text):
    context.scene.cursor_align_status = text


def set_cursor_rotation_from_normal(context, normal):
    cursor = context.scene.cursor
    normal = normal.normalized()

    quat = normal.to_track_quat('Z', 'Y')

    cursor.rotation_mode = 'QUATERNION'
    cursor.rotation_quaternion = quat
    
    
def set_transform_orientation_cursor(context):
    try:
        context.scene.transform_orientation_slots[0].type = 'CURSOR'
    except Exception:
        pass


def get_face_normal_under_mouse(context, event):
    obj = context.edit_object
    region = context.region
    rv3d = context.space_data.region_3d

    if obj is None or obj.type != 'MESH':
        return None, "Active object is not a mesh"

    if context.mode != 'EDIT_MESH':
        return None, "Must be in Edit Mode"

    if region is None or rv3d is None:
        return None, "No active 3D View region found"

    bm = bmesh.from_edit_mesh(obj.data)
    bm.faces.ensure_lookup_table()

    if not bm.faces:
        return None, "Mesh has no faces"

    bvh = BVHTree.FromBMesh(bm)
    mouse_co = (event.mouse_region_x, event.mouse_region_y)

    ray_origin_world = view3d_utils.region_2d_to_origin_3d(region, rv3d, mouse_co)
    ray_dir_world = view3d_utils.region_2d_to_vector_3d(region, rv3d, mouse_co).normalized()

    mw = obj.matrix_world
    mwi = mw.inverted()

    ray_origin_local = mwi @ ray_origin_world
    ray_target_local = mwi @ (ray_origin_world + ray_dir_world)
    ray_dir_local = (ray_target_local - ray_origin_local).normalized()

    hit = bvh.ray_cast(ray_origin_local, ray_dir_local)

    if hit[0] is None:
        return None, "No face under mouse"

    _hit_loc_local, hit_normal_local, _face_index, _distance = hit
    hit_normal_world = (mw.to_3x3() @ hit_normal_local).normalized()

    return hit_normal_world, "Face detected"


def apply_user_setup(context):
    scene = context.scene
    ts = scene.tool_settings

    try:
        ts.transform_pivot_point = 'ACTIVE_ELEMENT'
    except Exception:
        pass

    try:
        scene.transform_orientation_slots[0].type = 'CURSOR'
    except Exception:
        pass

    try:
        bpy.ops.wm.tool_set_by_id(name="builtin.transform")
    except Exception:
        pass


def activate_edit_cursor_mode(context):
    ts = context.scene.tool_settings

    try:
        bpy.ops.wm.tool_set_by_id(name="builtin.cursor")
    except Exception:
        pass

    try:
        ts.use_snap = True
        ts.snap_elements = {'VERTEX'}
        ts.snap_target = 'CLOSEST'
    except Exception:
        pass

    set_status(context, "Edit 3D Cursor enabled | Alt+Shift+MMB aligns rotation")

def activate_edit_pivot_mode(context):

    scene = context.scene
    ts = scene.tool_settings

    try:
        bpy.ops.wm.tool_set_by_id(
            name="builtin.transform"
        )
    except:
        pass

    try:
        scene.transform_orientation_slots[0].type = 'LOCAL'
    except:
        pass

    try:
        ts.use_transform_data_origin = True
    except:
        pass

    try:
        ts.use_snap = True
        ts.snap_elements = {
            'VERTEX',
            'EDGE_MIDPOINT',
            'FACE'
        }
        ts.snap_target = 'CENTER'
    except:
        pass

    set_status(
        context,
        "Edit Pivot enabled | Alt+Shift+MMB"
    )

def activate_use_cursor_mode(context):
    ts = context.scene.tool_settings

    try:
        bpy.ops.wm.tool_set_by_id(name="builtin.transform")
    except Exception:
        pass

    try:
        ts.transform_pivot_point = 'CURSOR'
    except Exception:
        pass

    try:
        ts.use_snap = False
    except Exception:
        pass

    set_status(context, "Use Cursor mode enabled")


def reset_transform_mode(context):
    scene = context.scene
    ts = scene.tool_settings

    try:
        bpy.ops.wm.tool_set_by_id(name="builtin.transform")
    except Exception:
        pass

    try:
        scene.transform_orientation_slots[0].type = 'NORMAL'
    except Exception:
        pass

    try:
        ts.transform_pivot_point = 'ACTIVE_ELEMENT'
    except Exception:
        pass

    try:
        ts.use_snap = False
    except Exception:
        pass

    set_status(context, "Reset applied")


def orient_cursor_from_mouse(context, event):
    normal, msg = get_face_normal_under_mouse(context, event)
    if normal is None:
        return False, msg

    set_cursor_rotation_from_normal(context, normal)

    # Nuevo: al orientar el cursor, también poner Transform Orientation > Cursor
    set_transform_orientation_cursor(context)

    return True, "Cursor orientation updated"

    
def activate_snap_mid_mode(context):
    scene = context.scene
    ts = scene.tool_settings

    # 🔹 Asegurar Object Mode para origin
    if context.mode != 'OBJECT':
        try:
            bpy.ops.object.mode_set(mode='OBJECT')
        except:
            pass

    # 🔹 Tool
    try:
        bpy.ops.wm.tool_set_by_id(name="builtin.transform")
    except:
        pass

    # 🔹 Orientation
    try:
        scene.transform_orientation_slots[0].type = 'LOCAL'
    except:
        pass

    # 🔹 Snap
    try:
        ts.use_snap = True
        ts.snap_elements = {'VERTEX'}
        ts.snap_target = 'CENTER'
    except:
        pass

    # 🔥 CLAVE: mover origin al cursor
    try:
        bpy.ops.object.origin_set(type='ORIGIN_CURSOR', center='MEDIAN')
    except Exception as e:
        print("Origin error:", e)

    set_status(context, "Snap Point (Origin→Cursor + Center)")
    
def is_edit_cursor_active(context):
    ts = context.scene.tool_settings

    return (
        ts.use_snap and
        ts.snap_elements == {'VERTEX'} and
        ts.snap_target == 'CLOSEST' and
        context.workspace.tools.from_space_view3d_mode(context.mode).idname == "builtin.cursor"
    )
# ------------------------------------------------------------
# Operators
# ------------------------------------------------------------

class VIEW3D_OT_cursor_orient_to_face_under_mouse(bpy.types.Operator):
    bl_idname = "view3d.cursor_orient_to_face_under_mouse"
    bl_label = "Orient Cursor to Face Under Mouse"
    bl_description = "Orient only the 3D Cursor rotation to the face under the mouse"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return (
            context.area is not None and
            context.area.type == 'VIEW_3D' and
            obj is not None and
            obj.type == 'MESH' and
            context.mode == 'EDIT_MESH'
        )

    def invoke(self, context, event):
        if not context.scene.cursor_align_enabled:
            set_status(context, "Disabled")
            return {'CANCELLED'}

        ok, msg = orient_cursor_from_mouse(context, event)
        set_status(context, msg)

        if not ok:
            self.report({'INFO'}, msg)
            return {'CANCELLED'}

        return {'FINISHED'}

class VIEW3D_OT_alt_shift_mmb_dispatch(
    bpy.types.Operator
):
    bl_idname = "view3d.alt_shift_mmb_dispatch"
    bl_label = "Alt Shift MMB"

    def invoke(
        self,
        context,
        event
    ):

        if (
            context.mode == 'OBJECT'
            and
            context.scene.edit_pivot_enabled
        ):

            return bpy.ops.view3d.edit_pivot_raycast(
                'INVOKE_DEFAULT'
            )

        if (
            context.mode == 'EDIT_MESH'
            and
            context.scene.cursor_align_enabled
        ):

            return bpy.ops.view3d.cursor_orient_to_face_under_mouse(
                'INVOKE_DEFAULT'
            )

        return {'CANCELLED'}
class VIEW3D_OT_cursor_align_apply_setup(bpy.types.Operator):
    bl_idname = "view3d.cursor_align_apply_setup"
    bl_label = "Use Sel Oriented"
    bl_description = "Use the current selection with Transform Orientation set to the 3D Cursor"

    def execute(self, context):
        apply_user_setup(context)
        set_status(context, "Using Selection Oriented by 3D Cursor")
        self.report({'INFO'}, "Use Selection Oriented enabled")
        return {'FINISHED'}


class VIEW3D_OT_reset_3d_cursor(bpy.types.Operator):
    bl_idname = "view3d.reset_3d_cursor"
    bl_label = "Reset 3D Cursor"
    bl_description = "Reset 3D Cursor location and rotation to zero"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        cursor = context.scene.cursor
        cursor.location = (0.0, 0.0, 0.0)
        cursor.rotation_mode = 'XYZ'
        cursor.rotation_euler = (0.0, 0.0, 0.0)

        set_status(context, "3D Cursor location and rotation reset")
        self.report({'INFO'}, "3D Cursor reset")
        return {'FINISHED'}


class VIEW3D_OT_cursor_align_use_cursor(bpy.types.Operator):
    bl_idname = "view3d.cursor_align_use_cursor"
    bl_label = "✢ Use Cursor"
    bl_description = "Activate Transform tool, set Pivot to 3D Cursor and disable Snap"

    def execute(self, context):
        activate_use_cursor_mode(context)
        self.report({'INFO'}, "Use Cursor enabled")
        return {'FINISHED'}


class VIEW3D_OT_cursor_align_use_selection(bpy.types.Operator):
    bl_idname = "view3d.cursor_align_use_selection"
    bl_label = "Use Selection"
    bl_description = "Return to normal selection transforms: Transform tool, Normal orientation, Active Element pivot and Snap OFF"

    def execute(self, context):
        scene = context.scene
        scene.edit_pivot_enabled = False

        if scene.cursor_align_enabled:
            scene.cursor_align_enabled = False

        scene.tool_settings.use_transform_data_origin = False
        unregister_keymaps()
        reset_transform_mode(context)
        set_status(context, "Using Selection")

        self.report({'INFO'}, "Use Selection enabled")
        return {'FINISHED'}
    

class VIEW3D_OT_cursor_align_origin_to_geometry(bpy.types.Operator):
    bl_idname = "view3d.cursor_align_origin_to_geometry"
    bl_label = "Origin to Geometry"
    bl_description = "Set object origin to geometry"

    def execute(self, context):
        try:
            bpy.ops.object.origin_set(type='ORIGIN_GEOMETRY', center='BOUNDS')
            self.report({'INFO'}, "Origin set to geometry")
        except Exception:
            self.report({'WARNING'}, "Failed (Object Mode required)")
        return {'FINISHED'}
    
class VIEW3D_OT_cursor_align_set_origin_geometry(bpy.types.Operator):
    bl_idname = "view3d.cursor_align_set_origin_geometry"
    bl_label = "Origin to Geometry"
    bl_description = "Set origin of active object to its geometry"

    def execute(self, context):
        try:
            bpy.ops.object.origin_set(type='ORIGIN_GEOMETRY', center='BOUNDS')
            set_status(context, "Origin set to geometry")
            self.report({'INFO'}, "Origin set to geometry")
        except Exception:
            self.report({'WARNING'}, "Failed (Object Mode required)")
        return {'FINISHED'}

class VIEW3D_OT_cursor_align_snap_mid(bpy.types.Operator):
    bl_idname = "view3d.cursor_align_snap_mid"
    bl_label = "Snap Point | ◎ → ✢"
    bl_description = "Origin to Cursor + Vertex snap (Median)"

    def execute(self, context):
        activate_snap_mid_mode(context)
        self.report({'INFO'}, "Snap Point enabled")
        return {'FINISHED'}
    
class VIEW3D_OT_cursor_align_origin_to_cursor(bpy.types.Operator):
    bl_idname = "view3d.cursor_align_origin_to_cursor"
    bl_label = "◎ → ✢"
    bl_description = "Set object origin to 3D cursor"

    def execute(self, context):
        try:
            bpy.ops.object.origin_set(type='ORIGIN_CURSOR', center='MEDIAN')
            self.report({'INFO'}, "Origin set to cursor")
        except Exception:
            self.report({'WARNING'}, "Failed (Object Mode required)")
        return {'FINISHED'}
    
class VIEW3D_OT_selection_to_cursor(bpy.types.Operator):
    bl_idname = "view3d.selection_to_cursor"
    bl_label = "◼ → ✢"
    bl_description = "Move selection to 3D Cursor"

    def execute(self, context):
        try:
            bpy.ops.view3d.snap_selected_to_cursor(use_offset=False)
            self.report({'INFO'}, "Selection moved to cursor")
        except Exception as e:
            self.report({'WARNING'}, str(e))

        return {'FINISHED'}


class VIEW3D_OT_cursor_to_selected(bpy.types.Operator):
    bl_idname = "view3d.cursor_to_selected"
    bl_label = "✢ → ▣"
    bl_description = "Move 3D Cursor to selection"

    def execute(self, context):
        try:
            bpy.ops.view3d.snap_cursor_to_selected()
            self.report({'INFO'}, "Cursor moved to selection")
        except Exception as e:
            self.report({'WARNING'}, str(e))

        return {'FINISHED'}
 

class VIEW3D_OT_edit_pivot(bpy.types.Operator):
    bl_idname = "view3d.edit_pivot"
    bl_label = "Edit Pivot"

    def execute(self, context):

        scene = context.scene
        ts = scene.tool_settings

        if scene.edit_pivot_enabled:

            scene.edit_pivot_enabled = False
            
            if not context.scene.cursor_align_enabled:
                unregister_keymaps()

            ts.use_transform_data_origin = False

            reset_transform_mode(context)

            self.report(
                {'INFO'},
                "Edit Pivot OFF"
            )

        else:

            scene.edit_pivot_enabled = True
            scene.cursor_align_enabled = False
            register_keymaps()

            activate_edit_pivot_mode(
                context
            )

            self.report(
                {'INFO'},
                "Edit Pivot ON"
            )

        return {'FINISHED'}
    
class VIEW3D_OT_edit_pivot_raycast(bpy.types.Operator):
    bl_idname = "view3d.edit_pivot_raycast"
    bl_label = "Edit Pivot Raycast"

    @classmethod
    def poll(cls, context):

        return (
            context.area is not None
            and context.area.type == 'VIEW_3D'
            and context.mode == 'OBJECT'
            and context.active_object is not None
            and context.active_object.type == 'MESH'
        )

    def invoke(self, context, event):
        if not context.scene.edit_pivot_enabled:
            return {'CANCELLED'}
        region = context.region
        rv3d = context.space_data.region_3d

        mouse_co = (
            event.mouse_region_x,
            event.mouse_region_y
        )

        ray_origin = view3d_utils.region_2d_to_origin_3d(
            region,
            rv3d,
            mouse_co
        )

        ray_dir = view3d_utils.region_2d_to_vector_3d(
            region,
            rv3d,
            mouse_co
        )

        hit, location, normal, face_index, obj, matrix = (
            context.scene.ray_cast(
                context.evaluated_depsgraph_get(),
                ray_origin,
                ray_dir
            )
        )

        if not hit:
            self.report({'WARNING'}, "No face detected")
            return {'CANCELLED'}

        print("NORMAL =", normal)

        quat = normal.to_track_quat('Z', 'Y')
        mat = quat.to_matrix()

        slot = context.scene.transform_orientation_slots[0]

        try:
            bpy.ops.transform.create_orientation(
                name="EditPivotFace",
                use=True,
                overwrite=True
            )
        except:
            pass

        slot = context.scene.transform_orientation_slots[0]

        if slot.custom_orientation:
            slot.custom_orientation.matrix = mat

        slot.type = 'EditPivotFace'

        print("NORMAL =", normal)
        print("MATRIX =", mat)

        return {'FINISHED'}
    
class KT_OT_update_addon(bpy.types.Operator):
    bl_idname = "kt.update_addon"
    bl_label = "Update KustomTools"

    def execute(self, context):

        try:

            # ------------------------------------------------
            # URL RAW GITHUB
            # ------------------------------------------------
            url = "https://raw.githubusercontent.com/KotoconK/KustomTools/main/KustomTools.py"

            # ------------------------------------------------
            # RUTA ADDON ACTUAL
            # ------------------------------------------------
            addon_path = __file__

            # ------------------------------------------------
            # DESCARGAR Y REEMPLAZAR
            # ------------------------------------------------
            urllib.request.urlretrieve(url, addon_path)

            # ------------------------------------------------
            # RECARGAR SCRIPTS
            # ------------------------------------------------
            self.report({'INFO'}, "Update installed — Reload Scripts")
            bpy.ops.script.reload()

            self.report({'INFO'}, "KustomTools updated")

        except Exception as e:

            self.report({'ERROR'}, str(e))

        return {'FINISHED'}

# ------------------------------------------------------------
# MATERIAL TOOLS
# ------------------------------------------------------------

def get_materials_used_by_scene_objects():
    """Return materials assigned to objects that are actually linked to a scene.

    This intentionally ignores references coming only from orphaned mesh/object
    datablocks. Blender's recursive Unused Data cleanup removes those orphaned
    datablocks first, which is why their materials can disappear there even when
    material.users is greater than zero.
    """
    used_materials = set()

    for scene in bpy.data.scenes:
        for obj in scene.objects:
            for slot in obj.material_slots:
                if slot.material is not None:
                    used_materials.add(slot.material)

    return used_materials


class KT_OT_delete_unused_materials(bpy.types.Operator):
    bl_idname = "kt.delete_unused_materials"
    bl_label = "Delete Unused Materials"
    bl_description = (
        "Delete materials not assigned to any object in any scene, including "
        "materials kept alive only by orphaned datablocks"
    )
    bl_options = {'REGISTER', 'UNDO'}

    def invoke(self, context, event):
        used_materials = get_materials_used_by_scene_objects()
        unused_count = sum(
            1 for mat in bpy.data.materials
            if mat not in used_materials and not mat.use_fake_user
        )

        if unused_count == 0:
            self.report({'INFO'}, "No unused materials found")
            return {'CANCELLED'}

        # Explicit confirmation protects against accidental cleanup.
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        used_materials = get_materials_used_by_scene_objects()
        unused_materials = [
            mat for mat in bpy.data.materials
            if mat not in used_materials and not mat.use_fake_user
        ]

        deleted_names = []

        for mat in unused_materials:
            deleted_names.append(mat.name)
            # do_unlink=True is important here: an orphaned Mesh datablock can
            # still count as a material user even though no scene object uses it.
            bpy.data.materials.remove(mat, do_unlink=True)

        count = len(deleted_names)

        if (
            context.scene.kt_material_name
            and bpy.data.materials.get(context.scene.kt_material_name) is None
        ):
            context.scene.kt_material_name = ""

        if (
            context.scene.kt_remove_material_name
            and bpy.data.materials.get(context.scene.kt_remove_material_name) is None
        ):
            context.scene.kt_remove_material_name = ""

        self.report(
            {'INFO'},
            f"Deleted {count} unused material{'s' if count != 1 else ''}"
        )

        if deleted_names:
            print("KustomTools - Deleted unused materials:")
            for name in deleted_names:
                print(f"  - {name}")

        return {'FINISHED'}


class KT_OT_select_by_material(bpy.types.Operator):
    bl_idname = "kt.select_by_material"
    bl_label = "Select by Material"
    bl_description = "Select objects in Object Mode or faces in Edit Mode that use the chosen material"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        material_name = context.scene.kt_material_name.strip()
        material = bpy.data.materials.get(material_name)

        if material is None:
            self.report({'WARNING'}, "Choose a valid material first")
            return {'CANCELLED'}

        # --------------------------------------------------------
        # OBJECT MODE: select every mesh object using the material
        # --------------------------------------------------------
        if context.mode == 'OBJECT':
            bpy.ops.object.select_all(action='DESELECT')

            first_obj = None
            selected_count = 0

            for obj in context.view_layer.objects:
                if obj.type != 'MESH':
                    continue

                uses_material = any(
                    slot.material == material
                    for slot in obj.material_slots
                )

                if uses_material:
                    obj.select_set(True)
                    selected_count += 1

                    if first_obj is None:
                        first_obj = obj

            if first_obj is not None:
                context.view_layer.objects.active = first_obj

            self.report(
                {'INFO'},
                f"Selected {selected_count} object{'s' if selected_count != 1 else ''} using {material.name}"
            )
            return {'FINISHED'}

        # --------------------------------------------------------
        # EDIT MODE: select every face using the material
        # Supports multi-object Edit Mode.
        # --------------------------------------------------------
        if context.mode == 'EDIT_MESH':
            context.tool_settings.mesh_select_mode = (False, False, True)

            selected_faces = 0
            edit_objects = [
                obj for obj in context.objects_in_mode_unique_data
                if obj.type == 'MESH'
            ]

            for obj in edit_objects:
                bm = bmesh.from_edit_mesh(obj.data)
                bm.faces.ensure_lookup_table()

                # Clear current face selection first.
                for face in bm.faces:
                    face.select = False

                for face in bm.faces:
                    slot_index = face.material_index

                    if slot_index >= len(obj.material_slots):
                        continue

                    slot = obj.material_slots[slot_index]

                    if slot.material == material:
                        face.select = True
                        selected_faces += 1

                bm.select_mode = {'FACE'}
                bm.select_flush_mode()
                bmesh.update_edit_mesh(
                    obj.data,
                    loop_triangles=False,
                    destructive=False
                )

            self.report(
                {'INFO'},
                f"Selected {selected_faces} face{'s' if selected_faces != 1 else ''} using {material.name}"
            )
            return {'FINISHED'}

        self.report({'WARNING'}, "Use this tool in Object Mode or Mesh Edit Mode")
        return {'CANCELLED'}


class KT_OT_remove_material_from_selected(bpy.types.Operator):
    bl_idname = "kt.remove_material_from_selected"
    bl_label = "Remove Material from Selected"
    bl_description = "Remove the chosen material slot from all selected mesh objects"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT'

    def execute(self, context):
        material_name = context.scene.kt_remove_material_name.strip()
        material = bpy.data.materials.get(material_name)

        if material is None:
            self.report({'WARNING'}, "Choose a valid material first")
            return {'CANCELLED'}

        selected_meshes = [
            obj for obj in context.selected_objects
            if obj.type == 'MESH'
        ]

        if not selected_meshes:
            self.report({'WARNING'}, "Select at least one mesh object")
            return {'CANCELLED'}

        affected_objects = 0
        removed_slots = 0
        processed_mesh_data = set()

        for obj in selected_meshes:
            matching_indices = [
                index for index, slot in enumerate(obj.material_slots)
                if slot.material == material
            ]

            if not matching_indices:
                continue

            affected_objects += 1

            # Material slot lists belong to the Mesh datablock. Avoid removing
            # the same slots twice when selected objects share mesh data.
            mesh_key = obj.data.as_pointer()
            if mesh_key in processed_mesh_data:
                continue

            processed_mesh_data.add(mesh_key)

            for index in reversed(matching_indices):
                obj.data.materials.pop(index=index)
                removed_slots += 1

        if affected_objects == 0:
            self.report(
                {'INFO'},
                f"{material.name} is not assigned to the selected objects"
            )
            return {'FINISHED'}

        self.report(
            {'INFO'},
            f"Removed {material.name} from {affected_objects} selected object"
            f"{'s' if affected_objects != 1 else ''}"
        )

        print(
            f"KustomTools - Removed {removed_slots} slot"
            f"{'s' if removed_slots != 1 else ''} for material {material.name}"
        )

        return {'FINISHED'}


# ------------------------------------------------------------
# MODELING TOOLS
# ------------------------------------------------------------

def kt_get_side_counts(edge_count):
    """Distribute all loop edges across four sides as evenly as possible."""
    base = edge_count // 4
    remainder = edge_count % 4

    if remainder == 0:
        return [base, base, base, base]
    if remainder == 1:
        return [base + 1, base, base, base]
    if remainder == 2:
        # Keep the two extra edges on opposite sides.
        return [base + 1, base, base + 1, base]

    return [base + 1, base + 1, base + 1, base]


def kt_get_ordered_edge_loop(selected_edges):
    """Return vertices ordered around one closed selected edge loop."""
    adjacency = {}

    for edge in selected_edges:
        v1, v2 = edge.verts
        adjacency.setdefault(v1, []).append(v2)
        adjacency.setdefault(v2, []).append(v1)

    if not adjacency:
        raise RuntimeError("Select a closed edge loop")

    for vert, neighbours in adjacency.items():
        if len(neighbours) != 2:
            raise RuntimeError(
                "Selection must be one closed edge loop; every selected vertex "
                "must have exactly two selected neighbours"
            )

    start = next(iter(adjacency))
    ordered = [start]
    previous = None
    current = start

    while True:
        neighbours = adjacency[current]
        next_vert = neighbours[0] if neighbours[0] != previous else neighbours[1]

        if next_vert == start:
            break

        ordered.append(next_vert)
        previous = current
        current = next_vert

        if len(ordered) > len(adjacency):
            raise RuntimeError("Could not resolve the selected edge loop")

    if len(ordered) != len(adjacency):
        raise RuntimeError("Selection contains more than one edge loop")

    return ordered


def kt_detect_axis_plane(world_positions):
    """Detect XY, XZ or YZ from the smallest world-space bounding-box range."""
    ranges = []

    for axis in range(3):
        values = [pos[axis] for pos in world_positions]
        ranges.append(max(values) - min(values))

    normal_axis = ranges.index(min(ranges))
    plane_axes = [axis for axis in range(3) if axis != normal_axis]

    return normal_axis, plane_axes[0], plane_axes[1]


def kt_rotate_list(values, offset):
    return values[offset:] + values[:offset]


def kt_get_modeling_loop_edges(context, obj, bm):
    """Return the loop edges to process from the current Edit Mode selection.

    If faces are selected, convert the selected face region to its boundary
    edges first. Otherwise use the currently selected edges as-is.
    """
    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    bm.faces.ensure_lookup_table()

    selected_faces = [face for face in bm.faces if face.select]

    if selected_faces:
        selected_face_set = set(selected_faces)
        boundary_edges = []

        for edge in bm.edges:
            selected_linked_faces = sum(
                1 for face in edge.link_faces
                if face in selected_face_set
            )

            if selected_linked_faces == 1:
                boundary_edges.append(edge)

        if not boundary_edges:
            raise RuntimeError(
                "Selected faces do not produce a usable boundary edge loop"
            )

        # Convert the visible selection from faces to boundary edges.
        for face in bm.faces:
            face.select = False

        for edge in bm.edges:
            edge.select = False

        for vert in bm.verts:
            vert.select = False

        for edge in boundary_edges:
            edge.select = True
            for vert in edge.verts:
                vert.select = True

        context.tool_settings.mesh_select_mode = (False, True, False)
        bm.select_mode = {'EDGE'}

        bmesh.update_edit_mesh(
            obj.data,
            loop_triangles=False,
            destructive=False,
        )

        return boundary_edges

    return [edge for edge in bm.edges if edge.select]


class KT_OT_quadrangulate_loop(bpy.types.Operator):
    bl_idname = "kt.quadrangulate_loop"
    bl_label = "Quadrangulate Loop"
    bl_description = (
        "Reshape one selected closed edge loop into an axis-aligned square, "
        "keeping all vertices and distributing extra edges across the four sides"
    )
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.edit_object
        return (
            context.mode == 'EDIT_MESH'
            and obj is not None
            and obj.type == 'MESH'
        )

    def execute(self, context):
        obj = context.edit_object
        bm = bmesh.from_edit_mesh(obj.data)

        bm.verts.ensure_lookup_table()
        bm.edges.ensure_lookup_table()

        try:
            selected_edges = kt_get_modeling_loop_edges(context, obj, bm)
        except RuntimeError as error:
            self.report({'WARNING'}, str(error))
            return {'CANCELLED'}

        if not selected_edges:
            self.report({'WARNING'}, "Select a closed edge loop or a face region")
            return {'CANCELLED'}

        try:
            vertices = kt_get_ordered_edge_loop(selected_edges)
        except RuntimeError as error:
            self.report({'WARNING'}, str(error))
            return {'CANCELLED'}

        vertex_count = len(vertices)

        if vertex_count < 4:
            self.report({'WARNING'}, "The loop needs at least 4 edges")
            return {'CANCELLED'}

        side_counts = kt_get_side_counts(vertex_count)

        matrix_world = obj.matrix_world
        matrix_world_inv = matrix_world.inverted()

        def world_position(vert):
            return matrix_world @ vert.co

        positions = [world_position(vert) for vert in vertices]
        normal_axis, axis_u, axis_v = kt_detect_axis_plane(positions)

        center = Vector((0.0, 0.0, 0.0))
        for pos in positions:
            center += pos
        center /= vertex_count

        u_values = [pos[axis_u] for pos in positions]
        v_values = [pos[axis_v] for pos in positions]

        half_u = (max(u_values) - min(u_values)) * 0.5
        half_v = (max(v_values) - min(v_values)) * 0.5

        if half_u <= 1.0e-8 or half_v <= 1.0e-8:
            self.report({'WARNING'}, "Selected loop is too flat or degenerate")
            return {'CANCELLED'}

        # Force a square while keeping approximately the original overall size.
        half_size = (half_u + half_v) * 0.5
        half_u = half_size
        half_v = half_size

        # Start from the existing vertex closest to the bottom-left corner
        # of the detected axis plane. This avoids changing topology.
        target_u = center[axis_u] - half_u
        target_v = center[axis_v] - half_v

        best_index = 0
        best_distance = None

        for index, pos in enumerate(positions):
            du = pos[axis_u] - target_u
            dv = pos[axis_v] - target_v
            distance = du * du + dv * dv

            if best_distance is None or distance < best_distance:
                best_distance = distance
                best_index = index

        vertices = kt_rotate_list(vertices, best_index)

        # From bottom-left, make the first side travel mainly toward +U.
        if len(vertices) > 1:
            p0 = world_position(vertices[0])
            p1 = world_position(vertices[1])
            du = p1[axis_u] - p0[axis_u]
            dv = p1[axis_v] - p0[axis_v]

            if abs(dv) > abs(du) or du < 0.0:
                vertices = [vertices[0]] + list(reversed(vertices[1:]))

        corners = [
            (-half_u, -half_v),
            ( half_u, -half_v),
            ( half_u,  half_v),
            (-half_u,  half_v),
        ]

        vertex_index = 0

        for side in range(4):
            edge_count = side_counts[side]
            start_corner = corners[side]
            end_corner = corners[(side + 1) % 4]

            for local_index in range(edge_count):
                vert = vertices[vertex_index]
                t = float(local_index) / float(edge_count)

                u = start_corner[0] + (end_corner[0] - start_corner[0]) * t
                v = start_corner[1] + (end_corner[1] - start_corner[1]) * t

                original_world = world_position(vert)
                new_world = center.copy()

                # Preserve the original coordinate perpendicular to the plane.
                new_world[normal_axis] = original_world[normal_axis]
                new_world[axis_u] = center[axis_u] + u
                new_world[axis_v] = center[axis_v] + v

                vert.co = matrix_world_inv @ new_world
                vertex_index += 1

        bmesh.update_edit_mesh(
            obj.data,
            loop_triangles=False,
            destructive=False,
        )

        axis_names = ["X", "Y", "Z"]
        plane_name = axis_names[axis_u] + axis_names[axis_v]
        distribution = " / ".join(str(value) for value in side_counts)

        self.report(
            {'INFO'},
            f"Quadrangulated {vertex_count} edges on {plane_name}: {distribution}"
        )

        return {'FINISHED'}

class KT_OT_circularize_loop(bpy.types.Operator):
    bl_idname = "kt.circularize_loop"
    bl_label = "Circularize"
    bl_description = (
        "Reshape one selected closed edge loop into an evenly spaced circle "
        "on its closest world axis plane"
    )
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.edit_object
        return (
            context.mode == 'EDIT_MESH'
            and obj is not None
            and obj.type == 'MESH'
        )

    def execute(self, context):
        obj = context.edit_object
        bm = bmesh.from_edit_mesh(obj.data)

        bm.verts.ensure_lookup_table()
        bm.edges.ensure_lookup_table()

        try:
            selected_edges = kt_get_modeling_loop_edges(context, obj, bm)
        except RuntimeError as error:
            self.report({'WARNING'}, str(error))
            return {'CANCELLED'}

        if not selected_edges:
            self.report({'WARNING'}, "Select a closed edge loop or a face region")
            return {'CANCELLED'}

        try:
            vertices = kt_get_ordered_edge_loop(selected_edges)
        except RuntimeError as error:
            self.report({'WARNING'}, str(error))
            return {'CANCELLED'}

        vertex_count = len(vertices)
        if vertex_count < 3:
            self.report({'WARNING'}, "The loop needs at least 3 edges")
            return {'CANCELLED'}

        matrix_world = obj.matrix_world
        matrix_world_inv = matrix_world.inverted()

        def world_position(vert):
            return matrix_world @ vert.co

        positions = [world_position(vert) for vert in vertices]
        normal_axis, axis_u, axis_v = kt_detect_axis_plane(positions)

        center = Vector((0.0, 0.0, 0.0))
        for pos in positions:
            center += pos
        center /= vertex_count

        u_values = [pos[axis_u] for pos in positions]
        v_values = [pos[axis_v] for pos in positions]

        half_u = (max(u_values) - min(u_values)) * 0.5
        half_v = (max(v_values) - min(v_values)) * 0.5
        radius = (half_u + half_v) * 0.5

        if radius <= 1.0e-8:
            self.report({'WARNING'}, "Selected loop is too small or degenerate")
            return {'CANCELLED'}

        # Pick a stable starting vertex close to the +U side of the loop.
        target_u = center[axis_u] + radius
        target_v = center[axis_v]
        best_index = 0
        best_distance = None

        for index, pos in enumerate(positions):
            du = pos[axis_u] - target_u
            dv = pos[axis_v] - target_v
            distance = du * du + dv * dv

            if best_distance is None or distance < best_distance:
                best_distance = distance
                best_index = index

        vertices = kt_rotate_list(vertices, best_index)

        p0 = world_position(vertices[0])
        start_angle = math.atan2(
            p0[axis_v] - center[axis_v],
            p0[axis_u] - center[axis_u],
        )

        direction = 1.0
        if vertex_count > 1:
            p1 = world_position(vertices[1])
            second_angle = math.atan2(
                p1[axis_v] - center[axis_v],
                p1[axis_u] - center[axis_u],
            )
            delta = (second_angle - start_angle + math.pi) % (2.0 * math.pi) - math.pi
            if delta < 0.0:
                direction = -1.0

        angle_step = direction * (2.0 * math.pi / float(vertex_count))

        for index, vert in enumerate(vertices):
            angle = start_angle + angle_step * index
            original_world = world_position(vert)
            new_world = center.copy()

            # Preserve depth perpendicular to the detected axis plane.
            new_world[normal_axis] = original_world[normal_axis]
            new_world[axis_u] = center[axis_u] + math.cos(angle) * radius
            new_world[axis_v] = center[axis_v] + math.sin(angle) * radius

            vert.co = matrix_world_inv @ new_world

        bmesh.update_edit_mesh(
            obj.data,
            loop_triangles=False,
            destructive=False,
        )

        axis_names = ["X", "Y", "Z"]
        plane_name = axis_names[axis_u] + axis_names[axis_v]
        self.report(
            {'INFO'},
            f"Circularized {vertex_count} edges on {plane_name}"
        )

        return {'FINISHED'}


# ------------------------------------------------------------
# ANI / RIG TOOLS
# ------------------------------------------------------------

KT_RIG_SHAPE_COLLECTION = "KustomTools_RigShapes"
KT_RIG_SHAPE_PREFIX = "KT_RigShape_"
KT_CONTROLLER_PREFIX = "CTRL_"
KT_CONTROLLER_CONSTRAINT = "KustomTools Controller"


def kt_get_selected_pose_bones(context):
    """Return selected Pose Bones from the active armature."""
    obj = context.active_object

    if (
        context.mode != 'POSE'
        or obj is None
        or obj.type != 'ARMATURE'
    ):
        return []

    return list(getattr(context, "selected_pose_bones", None) or [])


def kt_collection_contains(root_collection, target_collection):
    """Return True when target_collection is already below root_collection."""
    for child in root_collection.children:
        if child == target_collection:
            return True
        if kt_collection_contains(child, target_collection):
            return True
    return False


def kt_get_rig_shape_collection(context):
    """Create/reuse the collection that stores controller Custom Shape meshes."""
    collection = bpy.data.collections.get(KT_RIG_SHAPE_COLLECTION)

    if collection is None:
        collection = bpy.data.collections.new(KT_RIG_SHAPE_COLLECTION)

    scene_root = context.scene.collection
    if not kt_collection_contains(scene_root, collection):
        scene_root.children.link(collection)

    collection.hide_render = True
    return collection


def kt_rig_shape_geometry(shape_type):
    """Return vertices and edges for a controller shape in bone-local coordinates.

    The shape is centered at Y=0, which is the HEAD / origin of the controller
    bone. Y is the bone direction, so the 2D presets are drawn in the XZ plane.
    """
    center_y = 0.0

    if shape_type == 'CIRCLE':
        segments = 32
        radius = 0.55
        verts = [
            (
                math.cos((2.0 * math.pi * i) / segments) * radius,
                center_y,
                math.sin((2.0 * math.pi * i) / segments) * radius,
            )
            for i in range(segments)
        ]
        edges = [(i, (i + 1) % segments) for i in range(segments)]
        return verts, edges

    if shape_type == 'SQUARE':
        r = 0.55
        verts = [
            (-r, center_y, -r),
            ( r, center_y, -r),
            ( r, center_y,  r),
            (-r, center_y,  r),
        ]
        edges = [(0, 1), (1, 2), (2, 3), (3, 0)]
        return verts, edges

    if shape_type == 'DIAMOND':
        r = 0.72
        verts = [
            (0.0, center_y, -r),
            ( r, center_y, 0.0),
            (0.0, center_y,  r),
            (-r, center_y, 0.0),
        ]
        edges = [(0, 1), (1, 2), (2, 3), (3, 0)]
        return verts, edges

    if shape_type == 'BOX':
        r = 0.45
        y0 = -0.38
        y1 = 0.38
        verts = [
            (-r, y0, -r),
            ( r, y0, -r),
            ( r, y0,  r),
            (-r, y0,  r),
            (-r, y1, -r),
            ( r, y1, -r),
            ( r, y1,  r),
            (-r, y1,  r),
        ]
        edges = [
            (0, 1), (1, 2), (2, 3), (3, 0),
            (4, 5), (5, 6), (6, 7), (7, 4),
            (0, 4), (1, 5), (2, 6), (3, 7),
        ]
        return verts, edges

    if shape_type == 'CROSS':
        a = 0.18
        b = 0.58
        points = [
            (-a, -b), ( a, -b), ( a, -a), ( b, -a),
            ( b,  a), ( a,  a), ( a,  b), (-a,  b),
            (-a,  a), (-b,  a), (-b, -a), (-a, -a),
        ]
        verts = [(x, center_y, z) for x, z in points]
        edges = [(i, (i + 1) % len(verts)) for i in range(len(verts))]
        return verts, edges

    raise ValueError(f"Unknown controller shape: {shape_type}")


def kt_get_or_create_rig_shape(context, shape_type):
    """Create/reuse the hidden mesh object used by controller bones as Custom Shape."""
    object_name = f"{KT_RIG_SHAPE_PREFIX}{shape_type.title()}"
    shape_obj = bpy.data.objects.get(object_name)

    # Rebuild old v1.9 shapes because those were centered at the bone midpoint.
    if shape_obj is not None:
        if shape_obj.type != 'MESH':
            bpy.data.objects.remove(shape_obj, do_unlink=True)
            shape_obj = None
        elif not shape_obj.get("kt_origin_centered", False):
            bpy.data.objects.remove(shape_obj, do_unlink=True)
            shape_obj = None

    if shape_obj is not None:
        return shape_obj

    verts, edges = kt_rig_shape_geometry(shape_type)

    mesh = bpy.data.meshes.new(f"{object_name}_Mesh")
    mesh.from_pydata(verts, edges, [])
    mesh.update()

    shape_obj = bpy.data.objects.new(object_name, mesh)
    collection = kt_get_rig_shape_collection(context)
    collection.objects.link(shape_obj)

    shape_obj["kt_origin_centered"] = True
    shape_obj.hide_render = True
    shape_obj.hide_select = True
    shape_obj.display_type = 'WIRE'

    try:
        shape_obj.hide_set(True)
    except Exception:
        pass

    return shape_obj


def kt_lighter_color(color, factor, offset=0.0):
    return tuple(
        max(0.0, min(1.0, channel * factor + offset))
        for channel in color
    )


def kt_apply_bone_controller_color(pose_bone, color):
    """Apply a custom color to a controller Pose Bone."""
    normal = tuple(color[:3])
    selected = kt_lighter_color(normal, 1.20, 0.06)
    active = kt_lighter_color(normal, 1.42, 0.10)

    for bone_color in (
        getattr(pose_bone, "color", None),
        getattr(pose_bone.bone, "color", None),
    ):
        if bone_color is None:
            continue

        try:
            bone_color.palette = 'CUSTOM'
            bone_color.custom.normal = normal
            bone_color.custom.select = selected
            bone_color.custom.active = active
        except Exception:
            pass


def kt_configure_controller_bone(context, controller_bone, shape_type):
    """Assign shape and color to a separate non-deform controller bone."""
    shape_obj = kt_get_or_create_rig_shape(context, shape_type)

    controller_bone.custom_shape = shape_obj
    controller_bone.use_custom_shape_bone_size = True
    controller_bone.custom_shape_translation = (0.0, 0.0, 0.0)
    controller_bone.custom_shape_rotation_euler = (0.0, 0.0, 0.0)
    controller_bone.custom_shape_scale_xyz = (1.0, 1.0, 1.0)

    try:
        controller_bone.custom_shape_wire_width = 2.0
    except Exception:
        pass

    kt_apply_bone_controller_color(
        controller_bone,
        context.scene.kt_rig_controller_color,
    )


def kt_unique_controller_name(armature_obj, source_name):
    """Return a stable, non-conflicting controller-bone name for source_name."""
    source_pose = armature_obj.pose.bones.get(source_name)
    stored_name = source_pose.get("kt_controller_bone") if source_pose else None

    if stored_name:
        stored_pose = armature_obj.pose.bones.get(stored_name)
        if stored_pose and stored_pose.get("kt_source_bone") == source_name:
            return stored_name

    base_name = f"{KT_CONTROLLER_PREFIX}{source_name}"
    existing = armature_obj.pose.bones.get(base_name)
    if existing is None or existing.get("kt_source_bone") == source_name:
        return base_name

    index = 1
    while True:
        candidate = f"{base_name}.{index:03d}"
        existing = armature_obj.pose.bones.get(candidate)
        if existing is None or existing.get("kt_source_bone") == source_name:
            return candidate
        index += 1


def kt_get_selected_controller_pose_bones(context):
    """Return only selected KustomTools controller Pose Bones."""
    return [
        pose_bone
        for pose_bone in kt_get_selected_pose_bones(context)
        if pose_bone.get("kt_source_bone")
    ]


def kt_resolve_controller_pose_bones(context):
    """Resolve selected source/controller bones to their controller Pose Bones."""
    armature_obj = context.active_object
    selected = kt_get_selected_pose_bones(context)
    controllers = []
    seen = set()

    for pose_bone in selected:
        controller = None

        # The selected bone is already one of our controller bones.
        if pose_bone.get("kt_source_bone"):
            controller = pose_bone
        else:
            controller_name = pose_bone.get("kt_controller_bone")
            if controller_name:
                controller = armature_obj.pose.bones.get(controller_name)

            if controller is None:
                candidate = armature_obj.pose.bones.get(
                    f"{KT_CONTROLLER_PREFIX}{pose_bone.name}"
                )
                if candidate and candidate.get("kt_source_bone") == pose_bone.name:
                    controller = candidate

        if controller is not None and controller.name not in seen:
            seen.add(controller.name)
            controllers.append(controller)

    return controllers


def update_rig_shape_preset(self, context):
    """Auto-apply the chosen shape preset to selected controller bones."""
    obj = context.active_object
    if (
        context.mode != 'POSE'
        or obj is None
        or obj.type != 'ARMATURE'
    ):
        return

    controller_bones = kt_get_selected_controller_pose_bones(context)
    if not controller_bones:
        return

    obj.data.show_bone_custom_shapes = True
    shape_type = self.kt_rig_shape_preset

    for controller_bone in controller_bones:
        kt_configure_controller_bone(
            context,
            controller_bone,
            shape_type,
        )


def kt_add_controller_constraint(armature_obj, source_pose, controller_pose):
    """Make source_pose follow controller_pose without replacing source display."""
    constraint = source_pose.constraints.get(KT_CONTROLLER_CONSTRAINT)
    if constraint is None or constraint.type != 'COPY_TRANSFORMS':
        if constraint is not None:
            source_pose.constraints.remove(constraint)
        constraint = source_pose.constraints.new('COPY_TRANSFORMS')
        constraint.name = KT_CONTROLLER_CONSTRAINT

    constraint.target = armature_obj
    constraint.subtarget = controller_pose.name

    # Source and controller bones share the same rest transform. Copying their
    # LOCAL pose offsets keeps normal bone parenting behaviour intact.
    try:
        constraint.target_space = 'LOCAL'
        constraint.owner_space = 'LOCAL'
    except Exception:
        pass

    try:
        constraint.mix_mode = 'REPLACE'
    except Exception:
        pass


def kt_create_controller_bones(context, source_pose_bones):
    """Create separate non-deform controller bones for selected source bones."""
    armature_obj = context.active_object
    source_names = [bone.name for bone in source_pose_bones]

    controller_names = {
        source_name: kt_unique_controller_name(armature_obj, source_name)
        for source_name in source_names
    }

    source_parent_names = {
        source_name: (
            armature_obj.pose.bones[source_name].parent.name
            if armature_obj.pose.bones[source_name].parent
            else None
        )
        for source_name in source_names
    }

    # Resolve already-existing parent controllers before entering Edit Mode.
    existing_parent_controllers = {}
    for source_name, parent_source_name in source_parent_names.items():
        parent_controller_name = None
        if parent_source_name:
            parent_source_pose = armature_obj.pose.bones.get(parent_source_name)
            if parent_source_pose:
                stored_name = parent_source_pose.get("kt_controller_bone")
                if stored_name and armature_obj.pose.bones.get(stored_name):
                    parent_controller_name = stored_name
                else:
                    candidate = armature_obj.pose.bones.get(
                        f"{KT_CONTROLLER_PREFIX}{parent_source_name}"
                    )
                    if (
                        candidate
                        and candidate.get("kt_source_bone") == parent_source_name
                    ):
                        parent_controller_name = candidate.name
        existing_parent_controllers[source_name] = parent_controller_name

    # Switch briefly to Edit Mode to create real controller bones. The original
    # bones remain untouched and visible.
    bpy.ops.object.mode_set(mode='EDIT')
    edit_bones = armature_obj.data.edit_bones

    # First pass: create/copy transforms.
    for source_name in source_names:
        source_edit = edit_bones.get(source_name)
        if source_edit is None:
            continue

        controller_name = controller_names[source_name]
        controller_edit = edit_bones.get(controller_name)

        if controller_edit is None:
            controller_edit = edit_bones.new(controller_name)

        controller_edit.head = source_edit.head.copy()
        controller_edit.tail = source_edit.tail.copy()
        controller_edit.roll = source_edit.roll
        controller_edit.use_connect = False

        try:
            controller_edit.use_deform = False
        except Exception:
            pass

    # Second pass: mirror the hierarchy without parenting a controller to the
    # bone it controls (which would create a dependency cycle).
    for source_name in source_names:
        controller_edit = edit_bones.get(controller_names[source_name])
        if controller_edit is None:
            continue

        parent_source_name = source_parent_names[source_name]
        parent_controller = None

        if parent_source_name:
            if parent_source_name in controller_names:
                parent_controller = edit_bones.get(
                    controller_names[parent_source_name]
                )
            else:
                existing_name = existing_parent_controllers.get(source_name)
                if existing_name:
                    parent_controller = edit_bones.get(existing_name)

            # If the parent has no controller, inherit directly from the source
            # parent bone. This is safe because it is upstream, not the bone
            # controlled by this controller.
            if parent_controller is None:
                parent_controller = edit_bones.get(parent_source_name)

        controller_edit.parent = parent_controller
        controller_edit.use_connect = False

    bpy.ops.object.mode_set(mode='POSE')

    created_controllers = []

    for source_name in source_names:
        source_pose = armature_obj.pose.bones.get(source_name)
        controller_pose = armature_obj.pose.bones.get(controller_names[source_name])

        if source_pose is None or controller_pose is None:
            continue

        # Clear v1.9 Custom Shapes from the source bone so the original bone is
        # visible again. The Custom Shape now belongs to the separate controller.
        source_pose.custom_shape = None

        source_pose["kt_controller_bone"] = controller_pose.name
        controller_pose["kt_source_bone"] = source_pose.name
        controller_pose.bone.use_deform = False

        shape_type = getattr(context.scene, "kt_rig_shape_preset", "CIRCLE")
        kt_configure_controller_bone(context, controller_pose, shape_type)
        kt_add_controller_constraint(armature_obj, source_pose, controller_pose)
        created_controllers.append(controller_pose)

    # Select the new controllers so the animator can use them immediately.
    # Blender 5.2 moved Pose Mode selection to PoseBone.select; Bone.select
    # no longer exists on bpy.types.Bone.
    try:
        bpy.ops.pose.select_all(action='DESELECT')
    except Exception:
        for pose_bone in armature_obj.pose.bones:
            pose_bone.select = False

    for controller_pose in created_controllers:
        controller_pose.select = True

    if created_controllers:
        armature_obj.data.bones.active = created_controllers[-1].bone

    return created_controllers


class KT_OT_create_controllers(bpy.types.Operator):
    bl_idname = "kt.create_controllers"
    bl_label = "Create Controller"
    bl_description = (
        "Create a separate non-deform controller bone at the selected bone origin; "
        "the original bone stays visible and follows the controller"
    )
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return (
            context.mode == 'POSE'
            and obj is not None
            and obj.type == 'ARMATURE'
        )

    def execute(self, context):
        source_pose_bones = kt_get_selected_pose_bones(context)

        if not source_pose_bones:
            self.report({'WARNING'}, "Select at least one bone in Pose Mode")
            return {'CANCELLED'}

        # Do not create controllers from controllers selected by accident.
        source_pose_bones = [
            bone for bone in source_pose_bones
            if not bone.get("kt_source_bone")
        ]

        if not source_pose_bones:
            self.report({'WARNING'}, "Select original bones, not controller bones")
            return {'CANCELLED'}

        armature_obj = context.active_object
        armature_obj.data.show_bone_custom_shapes = True
        armature_obj.data.show_bone_colors = True
        armature_obj.show_in_front = True

        controllers = kt_create_controller_bones(context, source_pose_bones)

        self.report(
            {'INFO'},
            f"Created {len(controllers)} controller bone"
            f"{'s' if len(controllers) != 1 else ''}"
        )
        return {'FINISHED'}


class KT_OT_delete_controllers(bpy.types.Operator):
    bl_idname = "kt.delete_controllers"
    bl_label = "Delete Controller"
    bl_description = (
        "Delete the selected KustomTools controller bones and restore the "
        "original bones without deleting the rig bones they control"
    )
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return (
            context.mode == 'POSE'
            and obj is not None
            and obj.type == 'ARMATURE'
        )

    def execute(self, context):
        armature_obj = context.active_object
        controller_bones = kt_resolve_controller_pose_bones(context)

        if not controller_bones:
            self.report(
                {'WARNING'},
                "Select a controller bone or an original bone with a controller"
            )
            return {'CANCELLED'}

        controller_names = {bone.name for bone in controller_bones}
        source_names = []
        controller_to_source = {}

        # Remove the driving constraints and links before deleting the
        # controller bones. The original bones are never deleted.
        for controller_pose in controller_bones:
            source_name = controller_pose.get("kt_source_bone")
            if not source_name:
                continue

            controller_to_source[controller_pose.name] = source_name
            if source_name not in source_names:
                source_names.append(source_name)

            source_pose = armature_obj.pose.bones.get(source_name)
            if source_pose is None:
                continue

            constraint = source_pose.constraints.get(KT_CONTROLLER_CONSTRAINT)
            if constraint is not None:
                source_pose.constraints.remove(constraint)

            if source_pose.get("kt_controller_bone") == controller_pose.name:
                try:
                    del source_pose["kt_controller_bone"]
                except Exception:
                    pass

        bpy.ops.object.mode_set(mode='EDIT')
        edit_bones = armature_obj.data.edit_bones

        # If a remaining controller was parented to a controller being deleted,
        # parent it to the restored source bone instead. This preserves the
        # original rig hierarchy without creating dependency cycles.
        for controller_name in list(controller_names):
            controller_edit = edit_bones.get(controller_name)
            if controller_edit is None:
                continue

            source_name = controller_to_source.get(controller_name)
            fallback_parent = edit_bones.get(source_name) if source_name else None

            for child in list(controller_edit.children):
                if child.name not in controller_names:
                    child.parent = fallback_parent or controller_edit.parent
                    child.use_connect = False

        deleted_count = 0
        for controller_name in list(controller_names):
            controller_edit = edit_bones.get(controller_name)
            if controller_edit is not None:
                edit_bones.remove(controller_edit)
                deleted_count += 1

        bpy.ops.object.mode_set(mode='POSE')

        # Blender 5.2 stores Pose Mode selection on PoseBone.select.
        try:
            bpy.ops.pose.select_all(action='DESELECT')
        except Exception:
            for pose_bone in armature_obj.pose.bones:
                pose_bone.select = False

        restored_bones = []
        for source_name in source_names:
            source_pose = armature_obj.pose.bones.get(source_name)
            if source_pose is not None:
                source_pose.select = True
                restored_bones.append(source_pose)

        if restored_bones:
            armature_obj.data.bones.active = restored_bones[-1].bone

        self.report(
            {'INFO'},
            f"Deleted {deleted_count} controller bone"
            f"{'s' if deleted_count != 1 else ''}"
        )
        return {'FINISHED'}


class KT_OT_set_controller_shape(bpy.types.Operator):
    bl_idname = "kt.set_controller_shape"
    bl_label = "Set Controller Shape"
    bl_description = "Change the shape of selected controller bones"
    bl_options = {'REGISTER', 'UNDO'}

    shape_type: bpy.props.EnumProperty(
        name="Shape",
        items=(
            ('CIRCLE', "Circle", "Circular controller"),
            ('SQUARE', "Square", "Square controller"),
            ('DIAMOND', "Diamond", "Diamond controller"),
            ('BOX', "Box", "3D box controller"),
            ('CROSS', "Cross", "Cross controller"),
        ),
        default='CIRCLE',
    )

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return (
            context.mode == 'POSE'
            and obj is not None
            and obj.type == 'ARMATURE'
        )

    def execute(self, context):
        controller_bones = kt_get_selected_controller_pose_bones(context)

        if not controller_bones:
            self.report(
                {'WARNING'},
                "Select at least one controller bone"
            )
            return {'CANCELLED'}

        context.active_object.data.show_bone_custom_shapes = True

        for controller_bone in controller_bones:
            kt_configure_controller_bone(
                context,
                controller_bone,
                self.shape_type,
            )

        shape_name = self.shape_type.title()
        self.report(
            {'INFO'},
            f"Applied {shape_name} to {len(controller_bones)} controller"
            f"{'s' if len(controller_bones) != 1 else ''}"
        )
        return {'FINISHED'}


class KT_OT_apply_controller_color(bpy.types.Operator):
    bl_idname = "kt.apply_controller_color"
    bl_label = "Change Color"
    bl_description = "Change the color of selected controller bones"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return (
            context.mode == 'POSE'
            and obj is not None
            and obj.type == 'ARMATURE'
        )

    def execute(self, context):
        controller_bones = kt_get_selected_controller_pose_bones(context)

        if not controller_bones:
            self.report(
                {'WARNING'},
                "Select at least one controller bone"
            )
            return {'CANCELLED'}

        context.active_object.data.show_bone_colors = True
        color = context.scene.kt_rig_controller_color

        for controller_bone in controller_bones:
            kt_apply_bone_controller_color(controller_bone, color)

        self.report(
            {'INFO'},
            f"Applied controller color to {len(controller_bones)} controller"
            f"{'s' if len(controller_bones) != 1 else ''}"
        )
        return {'FINISHED'}


# ------------------------------------------------------------
# FUSIONAR HUESOS
# ------------------------------------------------------------

def kt_merge_armature_from_context(context):
    obj = context.object
    if obj and obj.type == 'ARMATURE':
        return obj
    return None


def kt_merge_get_edit_bone(arm, name):
    if not name or name == 'NONE':
        return None
    return arm.data.edit_bones.get(name)


def kt_merge_distance(a, b):
    return (a - b).length


def kt_merge_vertex_groups(arm_obj, bone_a_name, bone_b_name):
    """
    Add bone B weights to bone A on every mesh using this armature,
    then remove bone B's vertex group.
    """
    merged_objects = []

    for obj in bpy.data.objects:
        if obj.type != 'MESH':
            continue

        uses_armature = any(
            mod.type == 'ARMATURE' and mod.object == arm_obj
            for mod in obj.modifiers
        )
        if not uses_armature:
            continue

        vg_b = obj.vertex_groups.get(bone_b_name)
        if not vg_b:
            continue

        vg_a = obj.vertex_groups.get(bone_a_name)
        if not vg_a:
            vg_a = obj.vertex_groups.new(name=bone_a_name)

        weights_b = {}
        for vertex in obj.data.vertices:
            try:
                weight = vg_b.weight(vertex.index)
            except RuntimeError:
                weight = 0.0

            if weight != 0.0:
                weights_b[vertex.index] = weight

        for vertex_index, weight_b in weights_b.items():
            try:
                weight_a = vg_a.weight(vertex_index)
            except RuntimeError:
                weight_a = 0.0

            vg_a.add([vertex_index], weight_a + weight_b, 'REPLACE')

        obj.vertex_groups.remove(vg_b)
        merged_objects.append(obj.name)

    return merged_objects


class RIGTOOLS_OT_merge_bones(bpy.types.Operator):
    bl_idname = "rigtools.merge_bones"
    bl_label = "Fusionar huesos"
    bl_description = (
        "Fusiona dos huesos consecutivos: cabeza del primero + cola del segundo. "
        "Tambien suma sus pesos en las mallas asociadas."
    )
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        arm = kt_merge_armature_from_context(context)

        if not arm:
            self.report({'ERROR'}, "Selecciona un Armature.")
            return {'CANCELLED'}

        if arm.mode != 'EDIT':
            self.report({'ERROR'}, "Debes estar en Edit Mode.")
            return {'CANCELLED'}

        name_a = arm.rigtools_bone_a
        name_b = arm.rigtools_bone_b

        bone_a = kt_merge_get_edit_bone(arm, name_a)
        bone_b = kt_merge_get_edit_bone(arm, name_b)

        if not bone_a or not bone_b:
            self.report({'ERROR'}, "Selecciona los dos huesos.")
            return {'CANCELLED'}

        if bone_a == bone_b:
            self.report({'ERROR'}, "Los dos huesos deben ser diferentes.")
            return {'CANCELLED'}

        def is_direct_child(parent_bone, child_bone):
            return child_bone.parent == parent_bone

        if is_direct_child(bone_a, bone_b):
            first = bone_a
            second = bone_b
        elif is_direct_child(bone_b, bone_a):
            first = bone_b
            second = bone_a
        else:
            eps = max(bone_a.length, bone_b.length) * 0.05

            if kt_merge_distance(bone_a.tail, bone_b.head) <= eps:
                first = bone_a
                second = bone_b
            elif kt_merge_distance(bone_b.tail, bone_a.head) <= eps:
                first = bone_b
                second = bone_a
            else:
                self.report(
                    {'ERROR'},
                    "Los huesos deben ser padre e hijo directo, o estar "
                    "practicamente conectados."
                )
                return {'CANCELLED'}

        first_name = first.name
        second_name = second.name

        new_head = first.head.copy()
        new_tail = second.tail.copy()

        parent = first.parent
        first_was_connected = first.use_connect
        children = list(second.children)
        roll = first.roll

        new_name = context.scene.rigtools_new_name.strip() or first_name

        if new_name == second_name:
            new_name = first_name

        for child in children:
            child.parent = None
            child.use_connect = False

        first.head = new_head
        first.tail = new_tail
        first.roll = roll

        for child in children:
            child.parent = first
            child.use_connect = False

        first.parent = parent
        first.use_connect = bool(parent and first_was_connected)

        existing_name = arm.data.edit_bones.get(new_name)
        if existing_name and existing_name not in {first, second}:
            self.report(
                {'ERROR'},
                f"Ya existe un hueso llamado '{new_name}'. Elige otro nombre."
            )
            return {'CANCELLED'}

        first.name = new_name

        merged_meshes = kt_merge_vertex_groups(
            arm, first.name, second_name
        )

        arm.data.edit_bones.remove(second)

        arm.rigtools_bone_a = first.name
        arm.rigtools_bone_b = first.name

        msg = f"Fusionados: {first_name} + {second_name} -> {first.name}"
        if merged_meshes:
            msg += f" | Pesos transferidos en {len(merged_meshes)} malla(s)."

        self.report({'INFO'}, msg)
        return {'FINISHED'}


class KT_OT_clean_actions(bpy.types.Operator):
    bl_idname = "kt.clean_actions"
    bl_label = "Clean Actions"
    bl_description = (
        "Delete every Action in the entire .blend file that does not have "
        "Fake User enabled"
    )
    bl_options = {'REGISTER', 'UNDO'}

    def draw(self, context):
        layout = self.layout
        layout.label(
            text="This deletes ALL Actions without Fake User.",
            icon='ERROR'
        )
        layout.label(text="The cleanup affects the entire .blend file.")
        layout.label(text="Assigned and NLA Actions are deleted too if Fake User is OFF.")

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=440)

    def execute(self, context):
        actions_to_delete = [
            action for action in list(bpy.data.actions)
            if not action.use_fake_user
        ]

        for action in actions_to_delete:
            bpy.data.actions.remove(action, do_unlink=True)

        count = len(actions_to_delete)
        self.report(
            {'INFO'},
            f"Deleted {count} Action{'s' if count != 1 else ''} without Fake User"
        )
        return {'FINISHED'}


# ------------------------------------------------------------
# Keymap
# ------------------------------------------------------------

def unregister_keymaps():
    for km, kmi in addon_keymaps:
        try:
            km.keymap_items.remove(kmi)
        except Exception:
            pass
    addon_keymaps.clear()


def register_keymaps():
    unregister_keymaps()

    wm = bpy.context.window_manager
    kc = wm.keyconfigs.addon
    if kc is None:
        return

    km = kc.keymaps.new(name="3D View", space_type='VIEW_3D')
    kmi = km.keymap_items.new(
        VIEW3D_OT_alt_shift_mmb_dispatch.bl_idname,
        type='MIDDLEMOUSE',
        value='PRESS',
        alt=True,
        shift=True,
    )
    addon_keymaps.append((km, kmi))


def update_enabled(self, context):
    scene = context.scene

    if scene.cursor_align_enabled:
        # Edit 3D Cursor combines the 3D Cursor editing setup with
        # Alt+Shift+MMB rotation alignment.
        scene.edit_pivot_enabled = False
        scene.tool_settings.use_transform_data_origin = False

        activate_edit_cursor_mode(context)
        register_keymaps()
        set_status(
            context,
            "Edit 3D Cursor | Alt+Shift+MMB aligns rotation"
        )

    else:
        if not scene.edit_pivot_enabled:
            unregister_keymaps()
            reset_transform_mode(context)

        set_status(context, "Disabled")


# ------------------------------------------------------------
# INTEGRATED RETARGET TOGGLE
# ------------------------------------------------------------

class KT_OT_toggle_retarget(bpy.types.Operator):
    bl_idname = "kt.toggle_retarget"
    bl_label = "Toggle Retarget"
    bl_description = "Show or hide the integrated Retarget sidebar tab"

    def execute(self, context):
        scene = context.scene
        scene.kt_retarget_enabled = not scene.kt_retarget_enabled

        wm = context.window_manager
        for window in wm.windows:
            screen = window.screen
            if screen is None:
                continue
            for area in screen.areas:
                area.tag_redraw()

        state = "enabled" if scene.kt_retarget_enabled else "disabled"
        self.report({'INFO'}, f"Retarget tab {state}")
        return {'FINISHED'}


# ------------------------------------------------------------
# UI
# ------------------------------------------------------------

class VIEW3D_PT_cursor_align_sidebar(bpy.types.Panel):
    bl_label = "------ Cursor Align"
    bl_idname = "VIEW3D_PT_cursor_align_sidebar"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "KustomTools"

    def draw(self, context):
        layout = self.layout
        scene = context.scene

        col = layout.column(align=True)

        # ------------------------------------------------------------
        # EDIT PIVOT + ORIGIN TO GEOMETRY
        # ------------------------------------------------------------

        row = col.row(align=True)
        row.scale_y = 1.3
        row.enabled = (context.mode == 'OBJECT')

        sub = row.row(align=True)
        sub.alert = scene.edit_pivot_enabled
        sub.operator(
            "view3d.edit_pivot",
            text="Edit Pivot",
            icon='PIVOT_INDIVIDUAL'
        )

        # Compact icon-only utility beside Edit Pivot.
        row.operator(
            "view3d.cursor_align_origin_to_geometry",
            text="",
            icon='GIZMO'
        )

        # ------------------------------------------------------------
        # EDIT 3D CURSOR
        # ------------------------------------------------------------

        row = col.row()
        row.scale_y = 1.3
        row.enabled = (context.mode == 'EDIT_MESH')

        sub = row.row()
        sub.alert = scene.cursor_align_enabled
        sub.prop(
            scene,
            "cursor_align_enabled",
            text="Edit 3D Cursor",
            toggle=True,
            icon='CURSOR'
        )

        # ------------------------------------------------------------
        # RESET 3D CURSOR / USE SELECTION
        # ------------------------------------------------------------

        col.separator()

        row = col.row(align=True)
        row.operator(
            "view3d.reset_3d_cursor",
            text="Reset 3D Cursor",
            icon='LOOP_BACK'
        )
        row.operator(
            "view3d.cursor_align_use_selection",
            text="Use Selection",
            icon='FACESEL'
        )

        # ------------------------------------------------------------
        # USE SELECTION ORIENTED / USE CURSOR
        # ------------------------------------------------------------

        row = col.row(align=True)
        row.operator(
            "view3d.cursor_align_apply_setup",
            text="Use Sel Oriented",
            icon='ORIENTATION_CURSOR'
        )
        row.operator("view3d.cursor_align_use_cursor")

        col.separator()

        status_box = col.box()
        status_box.label(text="Status", icon='INFO')
        status_box.label(text=scene.cursor_align_status)

class VIEW3D_PT_viewport_tools(bpy.types.Panel):
    bl_label = "------ Viewport Tools"
    bl_idname = "VIEW3D_PT_viewport_tools"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "KustomTools"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        scene = context.scene

        col = layout.column(align=True)

        # 🎨 Edit Mode Background
        box = col.box()
        box.label(text="Edit Mode BG", icon='SHADING_RENDERED')
        row = box.row(align=True)

        row.prop(scene, "ct_edit_bg_color", text="")

        row.operator(
            "ct.enable_dynamic_bg",
            text="",
            icon='CHECKMARK' if scene.ct_bg_enabled else 'PLAY'
        )

        # 🎨 Active Object Color
        box = col.box()
        box.label(text="Active Object Color", icon='COLOR')

        row = box.row(align=True)

        row.prop(scene, "ct_active_obj_color", text="")

        row.operator(
            "ct.set_active_object_color",
            text="",
            icon='PLAY'
        )


class VIEW3D_PT_material_tools(bpy.types.Panel):
    bl_label = "------ Material Tools"
    bl_idname = "VIEW3D_PT_material_tools"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "KustomTools"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        col = layout.column(align=True)

        # --------------------------------------------------------
        # SELECT BY MATERIAL - COLLAPSIBLE
        # --------------------------------------------------------
        box = col.box()
        row = box.row(align=True)
        row.prop(
            scene,
            "kt_select_material_expanded",
            text="Select by Material",
            icon='TRIA_DOWN' if scene.kt_select_material_expanded else 'TRIA_RIGHT',
            emboss=False,
        )

        if scene.kt_select_material_expanded:
            box.prop_search(
                scene,
                "kt_material_name",
                bpy.data,
                "materials",
                text="Material"
            )

            if context.mode == 'EDIT_MESH':
                button_text = "Select Faces"
                button_icon = 'FACESEL'
            else:
                button_text = "Select Objects"
                button_icon = 'RESTRICT_SELECT_OFF'

            row = box.row(align=True)
            row.enabled = context.mode in {'OBJECT', 'EDIT_MESH'}
            row.operator(
                "kt.select_by_material",
                text=button_text,
                icon=button_icon
            )

        # --------------------------------------------------------
        # REMOVE MATERIAL FROM SELECTED - COLLAPSIBLE
        # --------------------------------------------------------
        box = col.box()
        row = box.row(align=True)
        row.prop(
            scene,
            "kt_remove_material_expanded",
            text="Remove Material from Selected",
            icon='TRIA_DOWN' if scene.kt_remove_material_expanded else 'TRIA_RIGHT',
            emboss=False,
        )

        if scene.kt_remove_material_expanded:
            box.prop_search(
                scene,
                "kt_remove_material_name",
                bpy.data,
                "materials",
                text="Material"
            )

            row = box.row(align=True)
            row.enabled = context.mode == 'OBJECT'
            row.operator(
                "kt.remove_material_from_selected",
                text="Remove Material",
                icon='X'
            )

        # Cleanup stays intentionally minimal.
        col.operator(
            "kt.delete_unused_materials",
            text="Delete Unused Materials",
            icon='TRASH'
        )


class VIEW3D_PT_modeling_tools(bpy.types.Panel):
    bl_label = "------ Modeling Tools"
    bl_idname = "VIEW3D_PT_modeling_tools"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "KustomTools"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout

        row = layout.row(align=True)
        row.enabled = context.mode == 'EDIT_MESH'
        row.operator(
            "kt.quadrangulate_loop",
            text="Quadrangulate",
            icon='MESH_GRID'
        )
        row.operator(
            "kt.circularize_loop",
            text="Circularize",
            icon='MESH_CIRCLE'
        )


class VIEW3D_PT_rig_tools(bpy.types.Panel):
    bl_label = "------ Ani/Rig Tools"
    bl_idname = "VIEW3D_PT_rig_tools"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "KustomTools"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        active_object = context.active_object

        is_pose_mode = (
            context.mode == 'POSE'
            and active_object is not None
            and active_object.type == 'ARMATURE'
        )
        has_selected_controller = (
            is_pose_mode
            and bool(kt_get_selected_controller_pose_bones(context))
        )

        col = layout.column(align=True)

        # --------------------------------------------------------
        # RETARGET TAB
        # --------------------------------------------------------
        row = col.row(align=True)
        row.scale_y = 1.25
        row.operator(
            "kt.toggle_retarget",
            text="Retarget ON" if scene.kt_retarget_enabled else "Retarget OFF",
            icon='ACTION',
            depress=scene.kt_retarget_enabled,
        )

        if scene.kt_retarget_enabled:
            col.label(text="Retarget sidebar tab enabled", icon='CHECKMARK')

        col.separator(factor=0.5)

        # --------------------------------------------------------
        # CONTROLLERS
        # --------------------------------------------------------
        controllers_box = col.box()
        row = controllers_box.row(align=True)
        row.prop(
            scene,
            "kt_rig_controllers_expanded",
            text="Controllers",
            icon=(
                'TRIA_DOWN'
                if scene.kt_rig_controllers_expanded
                else 'TRIA_RIGHT'
            ),
            emboss=False,
        )

        if scene.kt_rig_controllers_expanded:
            row = controllers_box.row(align=True)
            row.enabled = is_pose_mode
            row.operator(
                "kt.create_controllers",
                text="Create Controller",
                icon='BONE_DATA'
            )

            row = controllers_box.row(align=True)
            row.enabled = is_pose_mode
            row.operator(
                "kt.delete_controllers",
                text="Delete Controller",
                icon='TRASH'
            )

            controllers_box.separator(factor=0.5)

            row = controllers_box.row(align=True)
            row.enabled = has_selected_controller
            row.prop(
                scene,
                "kt_rig_shape_preset",
                text="Shape Preset"
            )

            row = controllers_box.row(align=True)
            row.enabled = has_selected_controller
            row.prop(scene, "kt_rig_controller_color", text="")
            row.operator(
                "kt.apply_controller_color",
                text="Change Color",
                icon='COLOR'
            )

            if not is_pose_mode:
                controllers_box.label(
                    text="Select bones in Pose Mode",
                    icon='INFO'
                )

        # --------------------------------------------------------
        # FUSIONAR HUESOS
        # --------------------------------------------------------
        merge_box = col.box()
        row = merge_box.row(align=True)
        row.prop(
            scene,
            "kt_rig_merge_bones_expanded",
            text="Fusionar huesos",
            icon=(
                'TRIA_DOWN'
                if scene.kt_rig_merge_bones_expanded
                else 'TRIA_RIGHT'
            ),
            emboss=False,
        )

        if scene.kt_rig_merge_bones_expanded:
            arm = (
                active_object
                if active_object is not None and active_object.type == 'ARMATURE'
                else None
            )

            if arm is None:
                merge_box.label(text="Selecciona un Armature", icon='INFO')
            elif arm.mode != 'EDIT':
                merge_box.label(text="Entra en Edit Mode", icon='EDITMODE_HLT')
            else:
                merge_col = merge_box.column(align=True)
                merge_col.label(text="Huesos a fusionar:", icon='BONE_DATA')
                merge_col.prop_search(
                    arm,
                    "rigtools_bone_a",
                    arm.data,
                    "edit_bones",
                    text="Primero"
                )
                merge_col.prop_search(
                    arm,
                    "rigtools_bone_b",
                    arm.data,
                    "edit_bones",
                    text="Segundo"
                )

                merge_col.separator()
                merge_col.prop(
                    scene,
                    "rigtools_new_name",
                    text="Nombre final"
                )

                merge_col.separator()
                merge_col.operator(
                    "rigtools.merge_bones",
                    text="Fusionar huesos",
                    icon='AUTOMERGE_OFF'
                )

                info_box = merge_box.box()
                info_box.label(text="Requisito", icon='INFO')
                info_box.label(text="Los huesos deben estar conectados.")
                info_box.label(text="A -> B = cabeza A + cola B.")
                info_box.label(text="Los pesos de B pasan a A.")

        # Clean Actions stays outside both subsections.
        col.separator(factor=0.7)
        col.operator(
            "kt.clean_actions",
            text="Clean Actions",
            icon='TRASH'
        )


class VIEW3D_PT_info_panel(bpy.types.Panel):
    bl_label = "------ Info"
    bl_idname = "VIEW3D_PT_info_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "KustomTools"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        col = layout.column(align=True)
        version_str = ".".join(map(str, ADDON_VERSION))

        # VERSION / UPDATE
        box = col.box()
        box.label(
            text=f"KUSTOMTOOLS {version_str}",
            icon='BLENDER'
        )
        box.separator(factor=0.3)
        box.operator(
            "kt.update_addon",
            text="Check Update",
            icon='IMPORT'
        )

        # EDIT 3D CURSOR SHORTCUT
        box = col.box()
        box.label(
            text="EDIT 3D CURSOR",
            icon='CURSOR'
        )
        box.separator(factor=0.3)
        box.label(text="Alt + Shift + MMB → Face Align")

        # AUTHOR
        box = col.box()
        box.label(
            text="AUTHOR",
            icon='INFO'
        )
        box.separator(factor=0.3)
        box.label(text="Developed by Álvaro_A")

# ============================================================================
# INTEGRATED MOTION RETARGET
# ============================================================================

# -----------------------------------------------------------------------------
# Constants / caches
# -----------------------------------------------------------------------------

KTMRT_ADDON_VERSION = 11
PRESET_FOLDER_NAME = "KustomTools_Retarget_Mappings"
IGNORE_MAPPING = ""
CURRENT_REFERENCE_SPACE = "ARMATURE_DELTA_V3"
COMPAT_REFERENCE_SPACES = {"ARMATURE_DELTA_V2", CURRENT_REFERENCE_SPACE}

_PRESET_ITEMS_CACHE = []


# -----------------------------------------------------------------------------
# Generic helpers
# -----------------------------------------------------------------------------

def addon_directory():
    return os.path.dirname(os.path.realpath(__file__))


_LEGACY_PRESET_MIGRATION_DONE = False


def preset_directory(create=False):
    path = os.path.join(addon_directory(), PRESET_FOLDER_NAME)
    if create:
        os.makedirs(path, exist_ok=True)
    return path


def migrate_legacy_retarget_presets():
    """Copy existing Motion Retarget JSON presets into the unified KustomTools folder."""
    global _LEGACY_PRESET_MIGRATION_DONE
    if _LEGACY_PRESET_MIGRATION_DONE:
        return
    _LEGACY_PRESET_MIGRATION_DONE = True

    destination = preset_directory(create=True)
    addon_dir = addon_directory()
    addon_root = addon_dir if os.path.basename(addon_dir).lower() in {'addons', 'addons_core'} else os.path.dirname(addon_dir)

    candidates = [
        os.path.join(addon_root, 'MotionRetarget', 'mappings'),
        os.path.join(addon_root, 'motion_retarget', 'mappings'),
        os.path.join(addon_root, 'Motion_Retarget', 'mappings'),
        os.path.join(addon_root, 'mappings'),
    ]

    for source_dir in candidates:
        if os.path.abspath(source_dir) == os.path.abspath(destination):
            continue
        if not os.path.isdir(source_dir):
            continue
        try:
            filenames = os.listdir(source_dir)
        except Exception:
            continue
        for filename in filenames:
            if not filename.lower().endswith('.json'):
                continue
            source_path = os.path.join(source_dir, filename)
            target_path = os.path.join(destination, filename)
            if os.path.exists(target_path):
                continue
            try:
                shutil.copy2(source_path, target_path)
            except Exception:
                pass


def safe_filename(value):
    value = (value or "mapping").strip()
    value = re.sub(r"[^A-Za-z0-9._-]+", "_", value)
    value = value.strip("._-")
    return value or "mapping"


def armature_poll(self, obj):
    return obj is not None and obj.type == 'ARMATURE'


def hierarchy_depth(bone):
    depth = 0
    parent = bone.parent
    while parent:
        depth += 1
        parent = parent.parent
    return depth


def hierarchy_order(armature_obj):
    order = {}
    counter = 0

    def walk(bone):
        nonlocal counter
        order[bone.name] = counter
        counter += 1
        for child in bone.children:
            walk(child)

    roots = [b for b in armature_obj.data.bones if b.parent is None]
    for root in roots:
        walk(root)
    return order


def armature_height(obj):
    """Approximate character height in world space.

    Prefer world Z extent (Blender's usual up axis) rather than the largest axis,
    because a T-pose arm span can be wider than the character is tall.
    """
    if not obj or obj.type != 'ARMATURE' or not obj.data.bones:
        return 1.0

    points = []
    for bone in obj.data.bones:
        points.append(obj.matrix_world @ bone.head_local)
        points.append(obj.matrix_world @ bone.tail_local)

    min_v = Vector((min(p.x for p in points), min(p.y for p in points), min(p.z for p in points)))
    max_v = Vector((max(p.x for p in points), max(p.y for p in points), max(p.z for p in points)))
    ext = max_v - min_v

    z_height = abs(ext.z)
    if z_height > 1e-6:
        return z_height
    return max(abs(ext.x), abs(ext.y), abs(ext.z), 1e-6)


def frame_sequence(start, end, step=1):
    start_i = int(math.floor(start))
    end_i = int(math.ceil(end))
    step = max(1, int(step))
    frames = list(range(start_i, end_i + 1, step))
    if not frames or frames[-1] != end_i:
        frames.append(end_i)
    return frames


def action_range(action):
    if not action:
        return 1, 1
    try:
        start, end = action.frame_range
        return int(math.floor(start)), int(math.ceil(end))
    except Exception:
        return 1, 1


def action_fcurves(action):
    """Return F-Curves from both legacy and layered/slot-based Actions."""
    if not action:
        return []

    found = []
    seen = set()

    # Legacy / compatibility API when available.
    try:
        for fc in action.fcurves:
            ptr = fc.as_pointer()
            if ptr not in seen:
                seen.add(ptr)
                found.append(fc)
    except Exception:
        pass

    # Blender layered Actions: layers -> strips -> channelbags -> fcurves.
    try:
        for layer in action.layers:
            for strip in layer.strips:
                channelbags = getattr(strip, "channelbags", None)
                if channelbags is None:
                    continue
                for channelbag in channelbags:
                    for fc in channelbag.fcurves:
                        ptr = fc.as_pointer()
                        if ptr not in seen:
                            seen.add(ptr)
                            found.append(fc)
    except Exception:
        pass

    return found


_BONE_PATH_RE = re.compile(r'pose\.bones\[(["\'])(.*?)\1\]')


def keyed_bone_names(action):
    names = set()
    for fc in action_fcurves(action):
        match = _BONE_PATH_RE.search(fc.data_path)
        if match:
            names.add(match.group(2))
    return names


def local_rest_matrix(data_bone):
    if data_bone.parent:
        return data_bone.parent.matrix_local.inverted_safe() @ data_bone.matrix_local
    return data_bone.matrix_local.copy()


def local_pose_matrix(pose_bone):
    if pose_bone.parent:
        return pose_bone.parent.matrix.inverted_safe() @ pose_bone.matrix
    return pose_bone.matrix.copy()


def evaluated_rest_delta(pose_bone):
    """Final evaluated pose relative to the bone's own rest transform and parent."""
    rest_local = local_rest_matrix(pose_bone.bone)
    pose_local = local_pose_matrix(pose_bone)
    return rest_local.inverted_safe() @ pose_local


def rest_world_rotation(armature_obj, data_bone):
    """World-space rest orientation without translation/scale."""
    matrix = armature_obj.matrix_world @ data_bone.matrix_local
    return matrix.to_quaternion().to_matrix()


def bone_world_rest_matrix(armature_obj, data_bone):
    return armature_obj.matrix_world @ data_bone.matrix_local


def bone_world_pose_matrix(armature_obj, pose_bone):
    return armature_obj.matrix_world @ pose_bone.matrix


def matrix_to_json(matrix):
    return json.dumps([float(matrix[r][c]) for r in range(4) for c in range(4)])


def matrix_from_json(value):
    if not value:
        return None
    try:
        values = json.loads(value)
        if not isinstance(values, list) or len(values) != 16:
            return None
        return Matrix((
            values[0:4], values[4:8], values[8:12], values[12:16],
        ))
    except Exception:
        return None


def pose_snapshot(obj):
    if not obj or obj.type != 'ARMATURE':
        return "{}"
    data = {}
    for pb in obj.pose.bones:
        data[pb.name] = [float(pb.matrix_basis[r][c]) for r in range(4) for c in range(4)]
    return json.dumps(data)


def restore_pose_snapshot(obj, payload):
    if not obj or obj.type != 'ARMATURE' or not payload:
        return
    try:
        data = json.loads(payload)
    except Exception:
        return
    for name, values in data.items():
        pb = obj.pose.bones.get(name)
        if not pb or not isinstance(values, list) or len(values) != 16:
            continue
        pb.matrix_basis = Matrix((values[0:4], values[4:8], values[8:12], values[12:16]))


def clear_pose_basis(obj):
    if not obj or obj.type != 'ARMATURE':
        return
    for pb in obj.pose.bones:
        pb.matrix_basis = Matrix.Identity(4)


def shortest_angle_deg(quaternion):
    try:
        angle = math.degrees(quaternion.angle) % 360.0
    except Exception:
        return 0.0
    return min(angle, 360.0 - angle)


def reference_is_compatible(item):
    return bool(item.has_reference and item.reference_space in COMPAT_REFERENCE_SPACES)


def reference_is_current(item):
    return bool(item.has_reference and item.reference_space == CURRENT_REFERENCE_SPACE)


def armature_axis_bridge(source_obj, target_obj):
    """Rotate a vector/quaternion from Source armature axes into Target armature axes.

    FBX/Mixamo rigs often arrive with a different object-level orientation than a native
    Blender rig. PoseBone.matrix is armature-local, so applying a source armature-space
    delta directly to the target only works when both armature objects use the same axes.
    """
    try:
        source_q = source_obj.matrix_world.to_quaternion()
        target_q = target_obj.matrix_world.to_quaternion()
        return target_q.inverted() @ source_q
    except Exception:
        from mathutils import Quaternion
        return Quaternion((1.0, 0.0, 0.0, 0.0))


def resolve_root_motion_target_name(settings, target, item):
    """Return the target bone that should receive a Root/Hips Location channel.

    Rotation always remains on item.target_bone. In AUTO mode, Root/Hips translation is
    routed to the highest ancestor of that mapped target. This lets a rig such as
    Bone -> spine receive Hips rotation on spine while Bone carries the character motion.
    """
    mapped_name = item.target_bone
    if not target or not mapped_name or not item.copy_location:
        return mapped_name
    if bone_class(item.source_bone) not in {'root', 'hips'}:
        return mapped_name

    mode = getattr(settings, 'root_motion_mode', 'AUTO')
    if mode == 'MAPPED':
        return mapped_name
    if mode == 'CUSTOM':
        custom = getattr(settings, 'root_motion_target', '')
        if custom and target.pose.bones.get(custom):
            return custom
        return mapped_name

    pb = target.pose.bones.get(mapped_name)
    if not pb:
        return mapped_name
    while pb.parent:
        pb = pb.parent
    return pb.name


def active_ik_constraints(target):
    """Return enabled IK/Spline-IK constraints that can override a baked pose."""
    found = []
    if not target or target.type != 'ARMATURE':
        return found
    for pb in target.pose.bones:
        for con in pb.constraints:
            if con.type not in {'IK', 'SPLINE_IK'}:
                continue
            if getattr(con, 'mute', False):
                continue
            if float(getattr(con, 'influence', 1.0)) <= 1e-8:
                continue
            found.append((pb.name, con.name))
    return found


def calibrated_target_matrix(source_obj, source_pb, target_obj, target_pb,
                             target_current_armature, source_reference_armature, target_reference_armature,
                             copy_location=True, copy_rotation=True, copy_scale=False,
                             global_root_scale=1.0):
    """Transfer motion from an equivalent captured pose in armature space.

    The source delta is first converted from Source armature axes to Target armature axes.
    This is important when an imported rig and a native Blender rig have different object
    rotations even though the characters appear upright in the viewport.
    """
    src_pose = source_pb.matrix.copy()
    src_ref = source_reference_armature
    tgt_ref = target_reference_armature
    bridge_q = armature_axis_bridge(source_obj, target_obj)

    src_pose_q = src_pose.to_quaternion()
    src_ref_q = src_ref.to_quaternion()
    tgt_ref_q = tgt_ref.to_quaternion()

    if copy_rotation:
        source_delta_q = src_pose_q @ src_ref_q.inverted()
        target_delta_q = bridge_q @ source_delta_q @ bridge_q.inverted()
        out_rot_armature = target_delta_q @ tgt_ref_q
    else:
        out_rot_armature = target_current_armature.to_quaternion()

    if copy_location:
        src_delta_armature = src_pose.translation - src_ref.translation
        src_delta_target_axes = bridge_q @ src_delta_armature
        loc_scale = mapped_location_scale(
            source_obj, source_pb, target_obj, target_pb,
            global_root_scale=global_root_scale,
        )
        out_loc_armature = tgt_ref.translation + src_delta_target_axes * loc_scale
    else:
        out_loc_armature = target_current_armature.translation.copy()

    out_scale_armature = target_current_armature.to_scale()
    if copy_scale:
        src_ref_scale = src_ref.to_scale()
        src_pose_scale = src_pose.to_scale()
        scale_delta = Vector((
            src_pose_scale.x / max(abs(src_ref_scale.x), 1e-6),
            src_pose_scale.y / max(abs(src_ref_scale.y), 1e-6),
            src_pose_scale.z / max(abs(src_ref_scale.z), 1e-6),
        ))
        out_scale_armature = Vector((
            out_scale_armature.x * scale_delta.x,
            out_scale_armature.y * scale_delta.y,
            out_scale_armature.z * scale_delta.z,
        ))

    return Matrix.LocRotScale(out_loc_armature, out_rot_armature, out_scale_armature)


def mapped_location_scale(source_obj, source_pb, target_obj, target_pb, global_root_scale=1.0):
    """Scale translation deltas without requiring source/target hierarchy to match."""
    if is_root_like(source_pb.name, source_pb.bone) or is_root_like(target_pb.name, target_pb.bone):
        return global_root_scale
    return max(target_pb.bone.length, 1e-6) / max(source_pb.bone.length, 1e-6)


def armature_local_extent(obj):
    """Character size in armature-local units, independent of Object scale.

    Retargeted PoseBone.matrix translations are expressed in armature-local units. Using a
    world-space height here is wrong for common FBX rigs where the Armature object carries a
    0.01 scale while the bones themselves are stored in centimetre-like local units.
    """
    if not obj or obj.type != 'ARMATURE' or not obj.data.bones:
        return 1.0
    points = []
    for bone in obj.data.bones:
        points.append(bone.head_local)
        points.append(bone.tail_local)
    min_v = Vector((min(p.x for p in points), min(p.y for p in points), min(p.z for p in points)))
    max_v = Vector((max(p.x for p in points), max(p.y for p in points), max(p.z for p in points)))
    ext = max_v - min_v
    return max(abs(ext.x), abs(ext.y), abs(ext.z), 1e-6)


def mapped_character_scale(source, target, mappings):
    """Robust Source-local -> Target-local unit/proportion conversion.

    Use the median length ratio of mapped body bones. This ignores detached helpers/poles and,
    unlike world-space armature height, correctly handles FBX object-scale differences.
    """
    ratios = []
    for item in mappings:
        src_bone = source.data.bones.get(item.source_bone)
        tgt_bone = target.data.bones.get(item.target_bone)
        if not src_bone or not tgt_bone:
            continue
        cls = bone_class(item.source_bone)
        if cls in {'root', 'finger', 'thumb', 'toe', 'other'}:
            continue
        src_len = float(src_bone.length)
        tgt_len = float(tgt_bone.length)
        if src_len <= 1e-8 or tgt_len <= 1e-8:
            continue
        ratio = tgt_len / src_len
        if 1e-5 < ratio < 1e5:
            ratios.append(ratio)

    if len(ratios) >= 3:
        ratios.sort()
        middle = len(ratios) // 2
        if len(ratios) % 2:
            return ratios[middle]
        return 0.5 * (ratios[middle - 1] + ratios[middle])

    return armature_local_extent(target) / max(armature_local_extent(source), 1e-6)


def world_aligned_target_matrix(source_obj, source_pb, target_obj, target_pb,
                                target_current_world,
                                copy_location=True, copy_rotation=True, copy_scale=False,
                                global_root_scale=1.0, rotation_mode='REST_LOCAL'):
    """Build an absolute target pose from source evaluated world motion.

    This does not assume that the source and target bones have the same parent, root,
    hierarchy depth, rest pose, or bone roll. A constant world-space rest alignment is
    computed per mapped bone, then applied to the evaluated source pose.

    When Location is disabled, the target bone keeps the position produced by its own
    target hierarchy. That keeps ordinary connected limbs attached while still allowing
    the target rotation to follow the source absolute motion.
    """
    src_rest = bone_world_rest_matrix(source_obj, source_pb.bone)
    src_pose = bone_world_pose_matrix(source_obj, source_pb)
    tgt_rest = bone_world_rest_matrix(target_obj, target_pb.bone)

    src_rest_q = src_rest.to_quaternion()
    src_pose_q = src_pose.to_quaternion()
    tgt_rest_q = tgt_rest.to_quaternion()

    # Rest-frame alignment used for translation conversion.
    align_q = tgt_rest_q @ src_rest_q.inverted()

    # Two mathematically useful retarget spaces. REST_LOCAL is the default because
    # it transfers the source change relative to its own rest frame onto the target
    # rest frame. WORLD_DELTA applies the source world-space change to target rest.
    # Both keep the target exactly in its own rest pose when the source is at rest.
    if copy_rotation:
        if rotation_mode == 'WORLD_DELTA':
            world_delta_q = src_pose_q @ src_rest_q.inverted()
            out_rot_world = world_delta_q @ tgt_rest_q
        else:  # REST_LOCAL
            rest_local_q = src_rest_q.inverted() @ src_pose_q
            out_rot_world = tgt_rest_q @ rest_local_q
    else:
        out_rot_world = target_current_world.to_quaternion()

    if copy_location:
        src_delta_world = src_pose.translation - src_rest.translation
        loc_scale = mapped_location_scale(
            source_obj, source_pb, target_obj, target_pb,
            global_root_scale=global_root_scale,
        )
        out_loc_world = tgt_rest.translation + (align_q @ src_delta_world) * loc_scale
    else:
        # Preserve the position generated by the target's own hierarchy. This is crucial
        # for connected arms/legs and lets rigs with different root/parent structures work.
        out_loc_world = target_current_world.translation.copy()

    out_scale_world = target_current_world.to_scale()
    if copy_scale:
        src_delta = evaluated_rest_delta(source_pb)
        src_scale = src_delta.to_scale()
        out_scale_world = Vector((
            out_scale_world.x * src_scale.x,
            out_scale_world.y * src_scale.y,
            out_scale_world.z * src_scale.z,
        ))

    desired_world = Matrix.LocRotScale(out_loc_world, out_rot_world, out_scale_world)
    return target_obj.matrix_world.inverted_safe() @ desired_world

def matrix_motion_components(matrix):
    loc, rot, scale = matrix.decompose()
    # Quaternion.angle is [0, pi].
    rot_deg = math.degrees(rot.angle)
    scale_dev = max(abs(scale.x - 1.0), abs(scale.y - 1.0), abs(scale.z - 1.0))
    return loc, rot, scale, rot_deg, scale_dev


def mapping_update(self, context):
    self.manual_override = True
    self.confidence = -1.0
    self.has_reference = False
    self.reference_source_matrix = ""
    self.reference_target_matrix = ""
    self.reference_space = ""
    try:
        settings = context.scene.kt_mrt_settings
        count = sum(1 for item in settings.mappings if reference_is_current(item))
        settings.calibration_count = count
        settings.has_calibration = count > 0
    except Exception:
        pass


# -----------------------------------------------------------------------------
# Bone naming / automap heuristics
# -----------------------------------------------------------------------------

_PREFIXES = (
    "mixamorig", "def", "org", "mch", "ctrl", "control", "rig", "jnt", "joint",
    "bip001", "bip01", "bip", "bn", "bone",
)

_ALIAS_GROUPS = {
    "hips": {"hips", "hip", "pelvis", "pelvic", "cog"},
    "spine": {"spine", "spinal", "torso"},
    "chest": {"chest", "upperchest", "thorax", "ribcage"},
    "neck": {"neck"},
    "head": {"head"},
    "clavicle": {"clavicle", "collar", "shoulder"},
    "upperarm": {"upperarm", "uparm", "armupper", "humerus"},
    "forearm": {"forearm", "lowerarm", "loarm", "armlower", "ulna"},
    "hand": {"hand", "wrist"},
    "thigh": {"thigh", "upperleg", "upleg", "legupper", "femur"},
    "calf": {"calf", "shin", "lowerleg", "loleg", "leglower", "tibia"},
    "foot": {"foot", "ankle"},
    "toe": {"toe", "toebase", "ball"},
    "thumb": {"thumb"},
    "finger": {"finger", "index", "middle", "ring", "pinky", "pinkie"},
    "root": {"root", "master", "global"},
}

_ALIAS_LOOKUP = {}
for canonical, aliases in _ALIAS_GROUPS.items():
    for alias in aliases:
        _ALIAS_LOOKUP[alias] = canonical


def detect_side(name):
    n = name.lower().replace(" ", "")
    if re.search(r"(^|[._:\-])l($|[._:\-0-9])", n) or "left" in n or n.endswith("_l") or n.endswith(".l"):
        return 'L'
    if re.search(r"(^|[._:\-])r($|[._:\-0-9])", n) or "right" in n or n.endswith("_r") or n.endswith(".r"):
        return 'R'
    return 'C'


def normalize_name(name):
    value = name.lower().strip()
    value = value.replace("left", " l ").replace("right", " r ")
    value = re.sub(r"[:|/\\.\-]+", "_", value)
    value = re.sub(r"\s+", "_", value)
    value = re.sub(r"_+", "_", value).strip("_")

    parts = [p for p in value.split("_") if p]
    while parts and any(parts[0].startswith(prefix) for prefix in _PREFIXES):
        parts.pop(0)

    # Remove isolated side tokens but retain numeric information.
    parts = [p for p in parts if p not in {"l", "r", "left", "right"}]
    return "_".join(parts)


def canonical_tokens(name):
    normalized = normalize_name(name)
    raw_tokens = re.findall(r"[a-z]+|\d+", normalized)
    tokens = []
    for token in raw_tokens:
        tokens.append(_ALIAS_LOOKUP.get(token, token))

    # Alias groups are often embedded without separators (e.g. LeftForeArm).
    joined = "".join(raw_tokens)
    for alias, canonical in sorted(_ALIAS_LOOKUP.items(), key=lambda kv: len(kv[0]), reverse=True):
        if alias in joined and canonical not in tokens:
            tokens.append(canonical)

    return tokens


def bone_class(name):
    tokens = set(canonical_tokens(name))
    priority = (
        "root", "hips", "spine", "chest", "neck", "head", "clavicle",
        "upperarm", "forearm", "hand", "thigh", "calf", "foot", "toe",
        "thumb", "finger",
    )
    for item in priority:
        if item in tokens:
            return item
    return "other"


def canonical_string(name):
    return "_".join(canonical_tokens(name)) or normalize_name(name)


def is_global_root_name(name):
    """True for a dedicated global/root-motion bone, not the pelvis/hips."""
    return bone_class(name) == "root"


def is_hips_name(name):
    """True for the anatomical pelvis/hips bone."""
    return bone_class(name) == "hips"


def token_similarity(a, b):
    ta = set(canonical_tokens(a))
    tb = set(canonical_tokens(b))
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / max(len(ta | tb), 1)


def bone_match_score(source_name, target_name, source_depth, target_depth):
    side_s = detect_side(source_name)
    side_t = detect_side(target_name)

    if side_s in {'L', 'R'} and side_t in {'L', 'R'} and side_s != side_t:
        return 0.0

    cs = canonical_string(source_name)
    ct = canonical_string(target_name)
    name_ratio = difflib.SequenceMatcher(None, cs, ct).ratio()
    token_ratio = token_similarity(source_name, target_name)

    if side_s == side_t:
        side_score = 1.0
    elif side_s == 'C' or side_t == 'C':
        side_score = 0.45
    else:
        side_score = 0.0

    depth_delta = abs(source_depth - target_depth)
    depth_score = 1.0 / (1.0 + depth_delta)

    score = (
        name_ratio * 0.52 +
        token_ratio * 0.23 +
        side_score * 0.15 +
        depth_score * 0.10
    )

    class_s = bone_class(source_name)
    class_t = bone_class(target_name)

    # Strongly protect fingers/twists/etc. from collapsing onto a hand/arm.
    if class_s != "other" and class_t != "other" and class_s != class_t:
        pair = {class_s, class_t}
        # Spine/chest can vary between rigs, so penalize them less.
        if pair <= {"spine", "chest"}:
            score *= 0.78
        else:
            score *= 0.38

    return max(0.0, min(1.0, score))


def is_root_like(name, bone):
    """Bones whose Location should use whole-character scale conversion."""
    cls = bone_class(name)
    return cls in {"root", "hips"}


def mapping_channels(item):
    channels = set()
    if item.copy_rotation:
        channels.add('R')
    if item.copy_location:
        channels.add('L')
    if item.copy_scale:
        channels.add('S')
    return channels


def chain_family(name):
    cls = bone_class(name)
    if cls in {"hips", "spine", "chest", "neck", "head"}:
        return "TORSO"
    if cls in {"clavicle", "upperarm", "forearm", "hand"}:
        return "ARM"
    if cls in {"thigh", "calf", "foot", "toe"}:
        return "LEG"
    if cls in {"thumb", "finger"}:
        return "FINGER"
    if cls == "root":
        return "ROOT"
    return "OTHER"


def bone_is_descendant(descendant, ancestor):
    parent = descendant.parent
    while parent:
        if parent == ancestor:
            return True
        parent = parent.parent
    return False


def skipped_chain_is_absorbed(item, settings, source):
    """True when an unmapped source bone feeds a mapped downstream bone.

    The bake uses evaluated world/rest motion, so a mapped descendant contains the
    rotational effect of skipped source parents. This is useful when source chains
    have more bones than the target chain.
    """
    bone = source.data.bones.get(item.source_bone)
    if not bone:
        return False
    family = chain_family(bone.name)
    if family in {"OTHER", "FINGER", "ROOT"}:
        return False
    side = detect_side(bone.name)
    for other in settings.mappings:
        if not other.target_bone:
            continue
        other_bone = source.data.bones.get(other.source_bone)
        if not other_bone or not bone_is_descendant(other_bone, bone):
            continue
        if chain_family(other_bone.name) != family:
            continue
        other_side = detect_side(other_bone.name)
        if side in {'L', 'R'} and other_side in {'L', 'R'} and side != other_side:
            continue
        if other.copy_rotation:
            return True
    return False


# -----------------------------------------------------------------------------
# Properties
# -----------------------------------------------------------------------------

class KTMRT_MappingItem(PropertyGroup):
    source_bone: StringProperty(name="Source")
    target_bone: StringProperty(name="Target", update=mapping_update)
    importance: FloatProperty(name="Importance", default=0.0, min=0.0)
    confidence: FloatProperty(name="Confidence", default=0.0, min=-1.0, max=1.0)
    hierarchy_depth: IntProperty(default=0, min=0)
    hierarchy_index: IntProperty(default=0, min=0)
    max_rotation_deg: FloatProperty(default=0.0, min=0.0)
    max_location: FloatProperty(default=0.0, min=0.0)
    max_scale_deviation: FloatProperty(default=0.0, min=0.0)
    direct_keyed: BoolProperty(default=False)
    motion_detected: BoolProperty(
        name="Moving / Keyed",
        description="The source bone has evaluated motion or direct keys in the analyzed Action",
        default=False,
    )
    significant: BoolProperty(default=False)
    manual_override: BoolProperty(default=False)
    copy_rotation: BoolProperty(name="R", default=True)
    copy_location: BoolProperty(name="L", default=False)
    copy_scale: BoolProperty(name="S", default=False)
    has_reference: BoolProperty(default=False)
    reference_source_matrix: StringProperty(default="")
    reference_target_matrix: StringProperty(default="")
    reference_space: StringProperty(default="")


class KTMRT_CopyDestinationItem(PropertyGroup):
    source_bone: StringProperty(
        name="Source Bone",
        description="Bone whose animation channels are copied from the Source Action",
        default="",
    )
    target_bone: StringProperty(
        name="Write To Bone",
        description="Destination bone on the Target Rig",
        default="",
    )
    copy_rotation: BoolProperty(name="R", default=True)
    copy_location: BoolProperty(name="L", default=False)
    copy_scale: BoolProperty(name="S", default=False)


class KTMRT_Settings(PropertyGroup):
    source_armature: PointerProperty(
        name="Source Rig",
        type=bpy.types.Object,
        poll=armature_poll,
    )
    source_action: PointerProperty(
        name="Source Action",
        type=bpy.types.Action,
    )
    target_armature: PointerProperty(
        name="Target Rig",
        type=bpy.types.Object,
        poll=armature_poll,
    )

    mappings: CollectionProperty(type=KTMRT_MappingItem)
    active_mapping_index: IntProperty(default=0)

    sort_mode: EnumProperty(
        name="Sort",
        items=[
            ('IMPORTANCE', "Importance", "Most important motion first"),
            ('HIERARCHY', "Hierarchy", "Source armature hierarchy"),
            ('NAME', "Name", "Source bone name A-Z"),
            ('CONFIDENCE', "Confidence", "Best automatic matches first"),
        ],
        default='IMPORTANCE',
    )
    filter_mode: EnumProperty(
        name="Show",
        items=[
            ('ALL', "Moving / Keyed", "Bones with evaluated movement or direct animation keys"),
            ('SIGNIFICANT', "Significant", "Only the most significant motion bones"),
            ('UNMAPPED', "Unmapped", "All source bones without a target mapping"),
            ('ALL_BONES', "All Bones", "Every bone in the source armature, including static bones"),
        ],
        default='SIGNIFICANT',
    )

    analysis_step: IntProperty(
        name="Analysis Step",
        description="Analyze every N frames. Use 1 for maximum accuracy",
        default=1,
        min=1,
        max=20,
    )
    bake_step: IntProperty(
        name="Bake Step",
        description="Bake one key every N frames",
        default=1,
        min=1,
        max=20,
    )
    significance_ratio: FloatProperty(
        name="Significance",
        description="Relative threshold used to mark the main motion bones",
        default=0.15,
        min=0.01,
        max=1.0,
        subtype='FACTOR',
    )
    automap_threshold: FloatProperty(
        name="Auto-map Threshold",
        description="Minimum confidence needed for automatic mapping",
        default=0.56,
        min=0.0,
        max=1.0,
        subtype='FACTOR',
    )
    scale_root_translation: BoolProperty(
        name="Scale Root/Hips Location",
        description="Scale copied Root/Hips location using the source/target armature size ratio",
        default=True,
    )
    root_motion_mode: EnumProperty(
        name="Root Motion",
        description="Choose where Root/Hips Location is written on the target rig",
        items=[
            ('AUTO', "Auto Parent", "Route Root/Hips Location to the highest parent of the mapped target bone"),
            ('MAPPED', "Mapped Bone", "Keep Location on the mapped target bone"),
            ('CUSTOM', "Custom Bone", "Write Root/Hips Location to a chosen target bone"),
        ],
        default='AUTO',
    )
    root_motion_target: StringProperty(
        name="Root Motion Bone",
        description="Target bone that receives Root/Hips Location when Root Motion is Custom Bone",
        default="",
    )
    rotation_mode: EnumProperty(
        name="Rotation Space",
        description="How source rest-relative rotation is transferred to the target",
        items=[
            ('REST_LOCAL', "Rest Local", "Apply source motion relative to source rest onto target rest; recommended default"),
            ('WORLD_DELTA', "World Delta", "Apply the source world-space rotation change onto target rest"),
        ],
        default='REST_LOCAL',
    )

    analysis_start: IntProperty(default=1)
    analysis_end: IntProperty(default=1)
    analyzed: BoolProperty(default=False)
    status_message: StringProperty(default="")
    validation_report: StringProperty(default="")

    preset_name: StringProperty(name="Preset Name", default="mapping")
    preset_file: EnumProperty(name="Preset", items=lambda self, context: preset_items(self, context))

    show_setup: BoolProperty(name="Setup & Analysis", default=True)
    show_mapping: BoolProperty(name="Bone Mapping", default=True)
    show_presets: BoolProperty(name="Mapping Presets", default=False)
    show_validation_log: BoolProperty(name="Validation Log", default=False)
    show_debug: BoolProperty(name="Orientation Debug", default=False)
    debug_report: StringProperty(default="")

    calibration_mode: BoolProperty(default=False)
    has_calibration: BoolProperty(default=False)
    calibration_count: IntProperty(default=0, min=0)
    calibration_source_action_name: StringProperty(default="")
    calibration_target_action_name: StringProperty(default="")
    calibration_source_pose_snapshot: StringProperty(default="")
    calibration_target_pose_snapshot: StringProperty(default="")
    calibration_frame: IntProperty(default=1)

    # Persistent equivalent-pose snapshots captured by Capture Reference Pose.
    # These are different from the temporary pre-calibration snapshots above.
    reference_source_pose_snapshot: StringProperty(default="")
    reference_target_pose_snapshot: StringProperty(default="")

    ik_disabled: BoolProperty(default=False)
    ik_restore_state: StringProperty(default="")

    rename_armature: PointerProperty(
        name="Rig",
        type=bpy.types.Object,
        poll=armature_poll,
    )
    rename_find: StringProperty(name="Find", default="mixamorig:")
    rename_replace: StringProperty(name="Replace", default="")
    rename_match_mode: EnumProperty(
        name="Match",
        items=[
            ('PREFIX', "Prefix", "Replace only when the text is at the start of the bone name"),
            ('SUFFIX', "Suffix", "Replace only when the text is at the end of the bone name"),
            ('ANYWHERE', "Anywhere", "Replace every occurrence in the bone name"),
        ],
        default='PREFIX',
    )
    rename_status: StringProperty(default="")

    offset_armature: PointerProperty(
        name="Rig",
        type=bpy.types.Object,
        poll=armature_poll,
    )
    offset_action: PointerProperty(
        name="Action",
        type=bpy.types.Action,
    )
    offset_bone: StringProperty(name="Bone", default="")
    offset_space: EnumProperty(
        name="Offset Space",
        items=[
            ('LOCAL', "Local (Bone)", "Apply the captured offset in the bone's local animation space"),
            ('GLOBAL', "Global (World)", "Apply the captured offset in world space"),
        ],
        default='LOCAL',
    )
    offset_editing: BoolProperty(default=False)
    offset_capture_local: StringProperty(default="")
    offset_capture_world: StringProperty(default="")
    offset_capture_frame: IntProperty(default=1)
    offset_capture_action_name: StringProperty(default="")
    offset_status: StringProperty(default="")

    copy_setup_expanded: BoolProperty(
        name="Source & Target",
        description="Show or hide Source/Target rig and Action settings",
        default=True,
    )
    copy_source_armature: PointerProperty(
        name="Source Rig",
        description="Rig that owns the animation curves to copy",
        type=bpy.types.Object,
        poll=armature_poll,
    )
    copy_source_action: PointerProperty(
        name="Source Action",
        description="Action containing the source-bone curves",
        type=bpy.types.Action,
    )
    copy_target_armature: PointerProperty(
        name="Target Rig",
        description="Rig whose bones receive the copied animation",
        type=bpy.types.Object,
        poll=armature_poll,
    )
    copy_target_action: PointerProperty(
        name="Target Action",
        description="Action used as the destination or as the base for a new copy",
        type=bpy.types.Action,
    )
    copy_output_mode: EnumProperty(
        name="Action Output",
        description="Choose whether to preserve the selected Target Action or modify it directly",
        items=[
            ('NEW', "New Action Copy", "Duplicate the Target Action, or create a new empty Action when none is selected"),
            ('CURRENT', "Modify Target Action", "Write directly into the selected Target Action"),
        ],
        default='NEW',
    )
    copy_destinations: CollectionProperty(type=KTMRT_CopyDestinationItem)
    copy_destination_index: IntProperty(default=0, min=0)
    copy_status: StringProperty(default="")


# -----------------------------------------------------------------------------
# Presets
# -----------------------------------------------------------------------------

def preset_items(self, context):
    global _PRESET_ITEMS_CACHE
    migrate_legacy_retarget_presets()
    path = preset_directory(create=False)
    items = []
    try:
        names = sorted(
            name for name in os.listdir(path)
            if name.lower().endswith('.json')
        )
    except Exception:
        names = []

    for index, filename in enumerate(names):
        stem = os.path.splitext(filename)[0]
        items.append((filename, stem, os.path.join(path, filename), index))

    if not items:
        items = [("__NONE__", "No presets", "No JSON presets found", 0)]

    _PRESET_ITEMS_CACHE = items
    return _PRESET_ITEMS_CACHE


def refresh_preset_cache():
    global _PRESET_ITEMS_CACHE
    _PRESET_ITEMS_CACHE = []


# -----------------------------------------------------------------------------
# UI list
# -----------------------------------------------------------------------------

class KTMRT_UL_Mappings(UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        settings = context.scene.kt_mrt_settings
        target = settings.target_armature

        row = layout.row(align=True)
        # Compact hierarchy marker. Deep chains are capped so the bone name stays readable.
        depth = item.hierarchy_depth
        if depth <= 0:
            marker = ""
        elif depth == 1:
            marker = "|"
        elif depth == 2:
            marker = "--"
        else:
            marker = "----"
        source_label = f"{marker}{item.source_bone}"

        left = row.row(align=True)
        left.scale_x = 1.15
        left.label(text=source_label)

        target_row = row.row(align=True)
        target_row.scale_x = 1.0
        if target and target.type == 'ARMATURE':
            target_row.prop_search(item, "target_bone", target.data, "bones", text="")
        else:
            target_row.prop(item, "target_bone", text="")

        clear = row.operator("kt_mrt.clear_mapping", text="", icon='X')
        clear.index = index

        row.prop(item, "copy_rotation", text="R", toggle=True)
        row.prop(item, "copy_location", text="L", toggle=True)
        row.prop(item, "copy_scale", text="S", toggle=True)

        if item.confidence < 0.0:
            row.label(text="Manual")
        elif item.target_bone:
            row.label(text=f"{round(item.confidence * 100):d}%")
        else:
            row.label(text="--")

    def filter_items(self, context, data, propname):
        settings = context.scene.kt_mrt_settings
        items = getattr(data, propname)
        helper = bpy.types.UI_UL_list

        flags = [self.bitflag_filter_item] * len(items)

        for i, item in enumerate(items):
            visible = True
            if settings.filter_mode == 'ALL':
                visible = item.motion_detected or item.direct_keyed or item.importance > 0.0
            elif settings.filter_mode == 'SIGNIFICANT':
                visible = item.significant
            elif settings.filter_mode == 'UNMAPPED':
                visible = not bool(item.target_bone)
            elif settings.filter_mode == 'ALL_BONES':
                visible = True

            if not visible:
                flags[i] &= ~self.bitflag_filter_item

        sort_data = []
        for i, item in enumerate(items):
            if settings.sort_mode == 'IMPORTANCE':
                key = (-item.importance, item.hierarchy_index, item.source_bone.lower())
            elif settings.sort_mode == 'HIERARCHY':
                key = (item.hierarchy_index,)
            elif settings.sort_mode == 'NAME':
                key = (item.source_bone.lower(),)
            else:  # CONFIDENCE
                conf = item.confidence if item.confidence >= 0.0 else 2.0
                key = (-conf, -item.importance, item.source_bone.lower())
            sort_data.append((i, key))

        neworder = helper.sort_items_helper(sort_data, key=lambda e: e[1]) if sort_data else []
        return flags, neworder


# -----------------------------------------------------------------------------
# Context / action assignment helpers
# -----------------------------------------------------------------------------

def ensure_source_action(settings):
    source = settings.source_armature
    if not source:
        return None
    if settings.source_action:
        return settings.source_action
    if source.animation_data and source.animation_data.action:
        settings.source_action = source.animation_data.action
        return settings.source_action
    return None


def set_action(obj, action):
    anim_data = obj.animation_data_create()
    anim_data.action = action


def reset_mapped_target_pose(settings):
    target = settings.target_armature
    if not target:
        return
    for item in settings.mappings:
        if not item.target_bone:
            continue
        pb = target.pose.bones.get(item.target_bone)
        if pb:
            pb.matrix_basis = Matrix.Identity(4)


def rotation_data_path(pose_bone):
    if pose_bone.rotation_mode == 'QUATERNION':
        return "rotation_quaternion"
    if pose_bone.rotation_mode == 'AXIS_ANGLE':
        return "rotation_axis_angle"
    return "rotation_euler"


def current_calibration_counts(settings):
    """Return (V1.8 current references, mapped rows)."""
    mapped = [item for item in settings.mappings if item.target_bone]
    current = sum(1 for item in mapped if reference_is_current(item))
    return current, len(mapped)


def has_complete_reference_pose(settings):
    current, mapped = current_calibration_counts(settings)
    return bool(
        mapped > 0
        and current == mapped
        and settings.reference_target_pose_snapshot
    )


def select_armature_pose_bone(context, rig, bone_name=None):
    """Make rig active and enter Pose Mode; optionally select one pose bone."""
    if not rig or rig.type != 'ARMATURE':
        return False
    try:
        if context.object and context.object.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
    except Exception:
        pass
    try:
        bpy.ops.object.select_all(action='DESELECT')
    except Exception:
        pass
    rig.select_set(True)
    context.view_layer.objects.active = rig
    try:
        bpy.ops.object.mode_set(mode='POSE')
    except Exception:
        return False

    if bone_name:
        try:
            for pb in rig.pose.bones:
                if hasattr(pb, "select"):
                    pb.select = False
                elif hasattr(pb.bone, "select"):
                    pb.bone.select = False
            pb = rig.pose.bones.get(bone_name)
            if pb:
                if hasattr(pb, "select"):
                    pb.select = True
                elif hasattr(pb.bone, "select"):
                    pb.bone.select = True
                rig.data.bones.active = pb.bone
        except Exception:
            pass
    return True


# -----------------------------------------------------------------------------
# Operators - convenience
# -----------------------------------------------------------------------------

class KTMRT_OT_UseSelectedSource(Operator):
    bl_idname = "kt_mrt.use_selected_source"
    bl_label = "Use Selected as Source"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        obj = context.active_object
        if not obj or obj.type != 'ARMATURE':
            self.report({'ERROR'}, "Select an armature")
            return {'CANCELLED'}
        settings = context.scene.kt_mrt_settings
        settings.source_armature = obj
        if obj.animation_data and obj.animation_data.action:
            settings.source_action = obj.animation_data.action
        return {'FINISHED'}


class KTMRT_OT_UseSelectedTarget(Operator):
    bl_idname = "kt_mrt.use_selected_target"
    bl_label = "Use Selected as Target"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        obj = context.active_object
        if not obj or obj.type != 'ARMATURE':
            self.report({'ERROR'}, "Select an armature")
            return {'CANCELLED'}
        context.scene.kt_mrt_settings.target_armature = obj
        return {'FINISHED'}


class KTMRT_OT_ClearMapping(Operator):
    bl_idname = "kt_mrt.clear_mapping"
    bl_label = "Ignore Bone"
    bl_options = {'INTERNAL'}

    index: IntProperty()

    def execute(self, context):
        settings = context.scene.kt_mrt_settings
        if 0 <= self.index < len(settings.mappings):
            item = settings.mappings[self.index]
            item.target_bone = IGNORE_MAPPING
            item.manual_override = True
            item.confidence = -1.0
        return {'FINISHED'}


class KTMRT_OT_ClearAllMappings(Operator):
    bl_idname = "kt_mrt.clear_all_mappings"
    bl_label = "Clear All"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        settings = context.scene.kt_mrt_settings
        for item in settings.mappings:
            item.target_bone = IGNORE_MAPPING
            item.manual_override = True
            item.confidence = -1.0
        settings.status_message = "All mappings cleared"
        return {'FINISHED'}


# -----------------------------------------------------------------------------
# Batch rename
# -----------------------------------------------------------------------------

class KTMRT_OT_UseSelectedRenameRig(Operator):
    bl_idname = "kt_mrt.use_selected_rename_rig"
    bl_label = "Use Selected Rig"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        obj = context.active_object
        if not obj or obj.type != 'ARMATURE':
            self.report({'ERROR'}, "Select an armature")
            return {'CANCELLED'}
        context.scene.kt_mrt_settings.rename_armature = obj
        return {'FINISHED'}


class KTMRT_OT_BatchRenameBones(Operator):
    bl_idname = "kt_mrt.batch_rename_bones"
    bl_label = "Rename Bones"
    bl_description = "Batch find/replace bone names on the selected rig"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        settings = context.scene.kt_mrt_settings
        rig = settings.rename_armature
        find = settings.rename_find
        replace = settings.rename_replace

        if not rig or rig.type != 'ARMATURE':
            self.report({'ERROR'}, "Choose an armature")
            return {'CANCELLED'}
        if not find:
            self.report({'ERROR'}, "Find text cannot be empty")
            return {'CANCELLED'}

        changes = {}
        all_names = {bone.name for bone in rig.data.bones}
        for bone in rig.data.bones:
            old = bone.name
            mode = settings.rename_match_mode
            if mode == 'PREFIX':
                if not old.startswith(find):
                    continue
                new = replace + old[len(find):]
            elif mode == 'SUFFIX':
                if not old.endswith(find):
                    continue
                new = old[:-len(find)] + replace
            else:
                if find not in old:
                    continue
                new = old.replace(find, replace)

            if new and new != old:
                changes[old] = new

        if not changes:
            settings.rename_status = "No bone names matched"
            self.report({'INFO'}, settings.rename_status)
            return {'FINISHED'}

        desired = list(changes.values())
        if len(desired) != len(set(desired)):
            self.report({'ERROR'}, "Rename would create duplicate bone names")
            return {'CANCELLED'}

        unchanged = all_names - set(changes.keys())
        collisions = sorted(set(desired) & unchanged)
        if collisions:
            self.report({'ERROR'}, "Rename conflicts with existing bone: " + collisions[0])
            return {'CANCELLED'}

        # Temporary names avoid collisions during swaps/chained renames.
        temp_map = {}
        for index, old in enumerate(list(changes.keys())):
            bone = rig.data.bones.get(old)
            if not bone:
                continue
            temp = f"__KTMRT_TMP_{index:04d}__"
            while rig.data.bones.get(temp):
                temp += "_"
            bone.name = temp
            temp_map[temp] = changes[old]

        for temp, final_name in temp_map.items():
            bone = rig.data.bones.get(temp)
            if bone:
                bone.name = final_name

        # Keep the current in-panel mapping consistent when renaming one of its rigs.
        if rig == settings.source_armature:
            inverse = changes
            for item in settings.mappings:
                if item.source_bone in inverse:
                    item.source_bone = inverse[item.source_bone]
        if rig == settings.target_armature:
            inverse = changes
            for item in settings.mappings:
                if item.target_bone in inverse:
                    item.target_bone = inverse[item.target_bone]

        settings.rename_status = f"Renamed {len(changes)} bones"
        self.report({'INFO'}, settings.rename_status)
        return {'FINISHED'}


# -----------------------------------------------------------------------------
# Calibration / target IK
# -----------------------------------------------------------------------------


def _action_name(obj):
    try:
        action = obj.animation_data.action if obj and obj.animation_data else None
        return action.name if action else ""
    except Exception:
        return ""


def _action_from_name(name):
    return bpy.data.actions.get(name) if name else None


def _restore_calibration_state(context, settings):
    source = settings.source_armature
    target = settings.target_armature

    if source:
        set_action(source, _action_from_name(settings.calibration_source_action_name))
        restore_pose_snapshot(source, settings.calibration_source_pose_snapshot)
    if target:
        set_action(target, _action_from_name(settings.calibration_target_action_name))
        restore_pose_snapshot(target, settings.calibration_target_pose_snapshot)

    try:
        context.scene.frame_set(settings.calibration_frame)
    except Exception:
        pass
    context.view_layer.update()

    settings.calibration_mode = False
    settings.calibration_source_action_name = ""
    settings.calibration_target_action_name = ""
    settings.calibration_source_pose_snapshot = ""
    settings.calibration_target_pose_snapshot = ""


class KTMRT_OT_ToggleCalibrationMode(Operator):
    bl_idname = "kt_mrt.toggle_calibration_mode"
    bl_label = "Calibration MODE"
    bl_description = "Temporarily disable animation and prepare both rigs for an equivalent A/T reference pose"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        settings = context.scene.kt_mrt_settings
        source = settings.source_armature
        target = settings.target_armature

        if settings.calibration_mode:
            _restore_calibration_state(context, settings)
            settings.status_message = "Calibration mode closed; animation restored"
            self.report({'INFO'}, settings.status_message)
            return {'FINISHED'}

        if not source or not target:
            self.report({'ERROR'}, "Choose Source and Target rigs first")
            return {'CANCELLED'}
        if source == target:
            self.report({'ERROR'}, "Source and Target must be different rigs")
            return {'CANCELLED'}
        if not settings.mappings:
            self.report({'ERROR'}, "Run Analyze Animation / mapping before calibration")
            return {'CANCELLED'}
        if not any(item.target_bone for item in settings.mappings):
            self.report({'ERROR'}, "Map at least one bone before calibration")
            return {'CANCELLED'}

        settings.calibration_source_action_name = _action_name(source)
        settings.calibration_target_action_name = _action_name(target)
        settings.calibration_source_pose_snapshot = pose_snapshot(source)
        settings.calibration_target_pose_snapshot = pose_snapshot(target)
        settings.calibration_frame = context.scene.frame_current

        # Remove active animation and start from each rig's own rest pose. The user then
        # adjusts only the pose controls/bones needed to make the two characters visually
        # equivalent (A/A or T/T). Rest pose data itself is never modified.
        set_action(source, None)
        set_action(target, None)
        clear_pose_basis(source)
        clear_pose_basis(target)
        context.view_layer.update()

        settings.calibration_mode = True

        # Enter Pose Mode automatically on whichever calibration rig the user currently
        # has selected. If neither is active, Target is the most useful default.
        active = context.active_object
        pose_rig = active if active in {source, target} else target
        select_armature_pose_bone(context, pose_rig)

        settings.status_message = "Calibration MODE: pose both rigs equivalently, then Capture Reference Pose"
        self.report({'INFO'}, settings.status_message)
        return {'FINISHED'}


class KTMRT_OT_ClearCalibrationPose(Operator):
    bl_idname = "kt_mrt.clear_calibration_pose"
    bl_label = "Clear Calibration Pose"
    bl_description = "Clear pose transforms without changing the armature rest pose"
    bl_options = {'REGISTER', 'UNDO'}

    which: EnumProperty(
        items=[
            ('SOURCE', "Source", "Clear Source pose"),
            ('TARGET', "Target", "Clear Target pose"),
            ('BOTH', "Both", "Clear both poses"),
        ],
        default='BOTH',
    )

    def execute(self, context):
        settings = context.scene.kt_mrt_settings
        if not settings.calibration_mode:
            self.report({'ERROR'}, "Enter Calibration MODE first")
            return {'CANCELLED'}
        if self.which in {'SOURCE', 'BOTH'}:
            clear_pose_basis(settings.source_armature)
        if self.which in {'TARGET', 'BOTH'}:
            clear_pose_basis(settings.target_armature)
        context.view_layer.update()
        return {'FINISHED'}


class KTMRT_OT_CaptureReferencePose(Operator):
    bl_idname = "kt_mrt.capture_reference_pose"
    bl_label = "Capture Reference Pose"
    bl_description = "Capture the current equivalent Source/Target pose for every mapped bone"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        settings = context.scene.kt_mrt_settings
        source = settings.source_armature
        target = settings.target_armature
        if not settings.calibration_mode:
            self.report({'ERROR'}, "Enter Calibration MODE first")
            return {'CANCELLED'}
        if not source or not target:
            self.report({'ERROR'}, "Source/Target missing")
            return {'CANCELLED'}

        context.view_layer.update()
        count = 0
        for item in settings.mappings:
            if not item.target_bone:
                item.has_reference = False
                item.reference_source_matrix = ""
                item.reference_target_matrix = ""
                item.reference_space = ""
                continue
            src_pb = source.pose.bones.get(item.source_bone)
            tgt_pb = target.pose.bones.get(item.target_bone)
            if not src_pb or not tgt_pb:
                item.has_reference = False
                item.reference_source_matrix = ""
                item.reference_target_matrix = ""
                item.reference_space = ""
                continue
            item.reference_source_matrix = matrix_to_json(src_pb.matrix.copy())
            item.reference_target_matrix = matrix_to_json(tgt_pb.matrix.copy())
            item.reference_space = CURRENT_REFERENCE_SPACE
            item.has_reference = True
            count += 1

        # Keep the complete Target calibration pose as the base for every baked frame.
        # Without this, unmapped intermediary bones were reset to Rest Pose and could break
        # rigs whose hierarchy differs from the source.
        settings.reference_source_pose_snapshot = pose_snapshot(source)
        settings.reference_target_pose_snapshot = pose_snapshot(target)
        settings.calibration_count = count
        settings.has_calibration = count > 0
        settings.status_message = f"Reference pose captured for {count} mapped rows (V1.8 hierarchy-aware)"
        self.report({'INFO'}, settings.status_message)
        return {'FINISHED'}


class KTMRT_OT_ClearReferencePose(Operator):
    bl_idname = "kt_mrt.clear_reference_pose"
    bl_label = "Clear Reference"
    bl_description = "Remove captured calibration reference data"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        settings = context.scene.kt_mrt_settings
        for item in settings.mappings:
            item.has_reference = False
            item.reference_source_matrix = ""
            item.reference_target_matrix = ""
            item.reference_space = ""
        settings.has_calibration = False
        settings.calibration_count = 0
        settings.reference_source_pose_snapshot = ""
        settings.reference_target_pose_snapshot = ""
        settings.status_message = "Reference pose calibration cleared"
        return {'FINISHED'}


class KTMRT_OT_ToggleTargetIK(Operator):
    bl_idname = "kt_mrt.toggle_target_ik"
    bl_label = "Disable Target IK"
    bl_description = "Mute/unmute IK constraints on the target rig without deleting them"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        settings = context.scene.kt_mrt_settings
        target = settings.target_armature
        if not target or target.type != 'ARMATURE':
            self.report({'ERROR'}, "Choose a Target rig first")
            return {'CANCELLED'}

        if settings.ik_disabled:
            try:
                saved = json.loads(settings.ik_restore_state or "[]")
            except Exception:
                saved = []
            restored = 0
            for entry in saved:
                pb = target.pose.bones.get(entry.get("bone", ""))
                if not pb:
                    continue
                con = pb.constraints.get(entry.get("constraint", ""))
                if not con:
                    continue
                if "mute" in entry and hasattr(con, "mute"):
                    con.mute = bool(entry["mute"])
                if "influence" in entry and hasattr(con, "influence"):
                    con.influence = float(entry["influence"])
                restored += 1
            settings.ik_disabled = False
            settings.ik_restore_state = ""
            context.view_layer.update()
            settings.status_message = f"Target IK restored ({restored} constraints)"
            self.report({'INFO'}, settings.status_message)
            return {'FINISHED'}

        saved = []
        disabled = 0
        for pb in target.pose.bones:
            for con in pb.constraints:
                if con.type not in {'IK', 'SPLINE_IK'}:
                    continue
                entry = {"bone": pb.name, "constraint": con.name}
                if hasattr(con, "mute"):
                    entry["mute"] = bool(con.mute)
                    con.mute = True
                elif hasattr(con, "influence"):
                    entry["influence"] = float(con.influence)
                    con.influence = 0.0
                saved.append(entry)
                disabled += 1

        settings.ik_restore_state = json.dumps(saved)
        settings.ik_disabled = disabled > 0
        context.view_layer.update()
        if disabled:
            settings.status_message = f"Target IK disabled ({disabled} constraints, not deleted)"
            self.report({'INFO'}, settings.status_message)
        else:
            settings.status_message = "No IK constraints found on Target"
            self.report({'INFO'}, settings.status_message)
        return {'FINISHED'}


# -----------------------------------------------------------------------------
# Analyze
# -----------------------------------------------------------------------------

class KTMRT_OT_Analyze(Operator):
    bl_idname = "kt_mrt.analyze"
    bl_label = "Analyze Animation"
    bl_description = "Evaluate the source Action and measure meaningful local bone motion"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        scene = context.scene
        settings = scene.kt_mrt_settings
        source = settings.source_armature
        target = settings.target_armature
        action = ensure_source_action(settings)

        if not source or source.type != 'ARMATURE':
            self.report({'ERROR'}, "Choose a source armature")
            return {'CANCELLED'}
        if not action:
            self.report({'ERROR'}, "Choose a source Action")
            return {'CANCELLED'}
        if target and target == source:
            self.report({'ERROR'}, "Source and target must be different armatures")
            return {'CANCELLED'}

        old_frame = scene.frame_current
        old_action = source.animation_data.action if source.animation_data else None
        keyed = keyed_bone_names(action)
        order = hierarchy_order(source)
        start, end = action_range(action)
        frames = frame_sequence(start, end, settings.analysis_step)
        source_height = armature_height(source)

        metrics = {}
        for pb in source.pose.bones:
            metrics[pb.name] = {
                "max_rot": 0.0,
                "max_loc": 0.0,
                "max_scale": 0.0,
            }

        try:
            set_action(source, action)

            for frame in frames:
                scene.frame_set(frame)
                context.view_layer.update()

                for pb in source.pose.bones:
                    delta = evaluated_rest_delta(pb)
                    loc, rot, scale, rot_deg, scale_dev = matrix_motion_components(delta)
                    data = metrics[pb.name]
                    data["max_rot"] = max(data["max_rot"], rot_deg)
                    data["max_loc"] = max(data["max_loc"], loc.length)
                    data["max_scale"] = max(data["max_scale"], scale_dev)

        except Exception as exc:
            self.report({'ERROR'}, f"Analysis failed: {exc}")
            return {'CANCELLED'}
        finally:
            set_action(source, old_action)
            scene.frame_set(old_frame)
            context.view_layer.update()

        records = []
        moving_count = 0

        for bone in source.data.bones:
            data = metrics.get(bone.name)
            if not data:
                data = {
                    "max_rot": 0.0,
                    "max_loc": 0.0,
                    "max_scale": 0.0,
                }

            bone_ref = max(bone.length, source_height * 0.01, 1e-6)
            loc_ratio = data["max_loc"] / bone_ref
            score = (
                data["max_rot"] +
                min(loc_ratio * 45.0, 180.0) +
                min(data["max_scale"] * 50.0, 100.0)
            )

            # A bone is considered animated when it has measurable evaluated motion
            # or direct keys. Every bone is still stored so the All Bones filter can
            # expose static/helper bones for manual mapping when needed.
            motion_detected = score > 0.01 or bone.name in keyed
            if motion_detected:
                moving_count += 1

            records.append((bone, data, score, motion_detected))

        settings.mappings.clear()
        settings.has_calibration = False
        settings.calibration_count = 0
        settings.reference_source_pose_snapshot = ""
        settings.reference_target_pose_snapshot = ""

        if not records:
            settings.analyzed = False
            settings.status_message = "Source armature has no bones"
            self.report({'WARNING'}, settings.status_message)
            return {'FINISHED'}

        motion_scores = [
            score for _bone, _data, score, moving in records
            if moving
        ]
        max_score = max(motion_scores, default=1.0)
        sig_cutoff = max(max_score * settings.significance_ratio, 0.5)

        for bone, data, score, motion_detected in records:
            item = settings.mappings.add()
            item.source_bone = bone.name
            item.target_bone = ""
            item.hierarchy_depth = hierarchy_depth(bone)
            item.hierarchy_index = order.get(bone.name, 0)
            item.max_rotation_deg = data["max_rot"]
            item.max_location = data["max_loc"]
            item.max_scale_deviation = data["max_scale"]
            item.importance = score / max(max_score, 1e-6) if motion_detected else 0.0
            item.direct_keyed = bone.name in keyed
            item.motion_detected = motion_detected
            item.significant = motion_detected and score >= sig_cutoff
            cls = bone_class(bone.name)
            # A dedicated Root is treated only as global translation/root motion.
            # Hips/Pelvis is part of the body and normally carries both rotation and location.
            item.copy_rotation = (cls != "root")
            item.copy_location = cls in {"root", "hips"}
            item.copy_scale = False
            item.manual_override = False
            item.confidence = 0.0

        settings.analysis_start = start
        settings.analysis_end = end
        settings.analyzed = True
        settings.show_setup = False
        settings.show_mapping = True
        significant_count = sum(1 for item in settings.mappings if item.significant)
        settings.status_message = (
            f"Analyzed {len(frames)} frames · {moving_count} moving/keyed / "
            f"{len(settings.mappings)} total bones · {significant_count} significant"
        )
        settings.validation_report = ""

        # Auto-map immediately if a target is already selected.
        if target and target.type == 'ARMATURE':
            bpy.ops.kt_mrt.auto_map()

        self.report({'INFO'}, settings.status_message)
        return {'FINISHED'}


# -----------------------------------------------------------------------------
# Auto map
# -----------------------------------------------------------------------------

class KTMRT_OT_AutoMap(Operator):
    bl_idname = "kt_mrt.auto_map"
    bl_label = "Auto Map"
    bl_description = "Suggest target bones using names, side, hierarchy, bone type and channel compatibility"
    bl_options = {'REGISTER', 'UNDO'}

    only_unmapped: BoolProperty(default=False)

    def execute(self, context):
        settings = context.scene.kt_mrt_settings
        source = settings.source_armature
        target = settings.target_armature

        if not source or not target:
            self.report({'ERROR'}, "Choose source and target armatures")
            return {'CANCELLED'}
        if not settings.mappings:
            self.report({'ERROR'}, "Analyze the source Action first")
            return {'CANCELLED'}

        target_depths = {b.name: hierarchy_depth(b) for b in target.data.bones}
        used_channels = {}

        def reserve(item):
            if item.target_bone:
                used_channels.setdefault(item.target_bone, set()).update(mapping_channels(item))

        # Preserve manual decisions first. Different source rows may share one target as long
        # as they do not write the same transform channel.
        for item in settings.mappings:
            if item.target_bone and item.manual_override:
                reserve(item)

        reserved_sources = set()

        # Root and Hips are intentionally treated as different functions.
        # Root = optional global translation/root motion. It is NEVER auto-mapped to Hips.
        # Hips/Pelvis = anatomical body bone and normally carries both Rotation + Location.
        source_roots = [
            it for it in settings.mappings
            if bone_class(it.source_bone) == 'root'
            and (it.motion_detected or it.manual_override or bool(it.target_bone))
        ]
        source_hips = [
            it for it in settings.mappings
            if bone_class(it.source_bone) == 'hips'
            and (it.motion_detected or it.manual_override or bool(it.target_bone))
        ]
        target_roots = [b for b in target.data.bones if bone_class(b.name) == 'root']
        target_hips = [b for b in target.data.bones if bone_class(b.name) == 'hips']

        ignored_root_sources = set()

        # Dedicated Root only maps to a dedicated Root. If Target has none, leave it ignored.
        if source_roots:
            for root_item in source_roots:
                if root_item.manual_override:
                    if root_item.target_bone:
                        reserve(root_item)
                    continue
                root_item.copy_rotation = False
                root_item.copy_location = True
                root_item.copy_scale = False
                if target_roots:
                    root_target = max(
                        target_roots,
                        key=lambda b: bone_match_score(
                            root_item.source_bone, b.name, root_item.hierarchy_depth, target_depths[b.name]
                        ),
                    )
                    root_item.target_bone = root_target.name
                    root_item.confidence = max(
                        bone_match_score(
                            root_item.source_bone, root_target.name,
                            root_item.hierarchy_depth, target_depths[root_target.name]
                        ),
                        0.90,
                    )
                    root_item.manual_override = False
                    reserve(root_item)
                    reserved_sources.add(root_item.source_bone)
                else:
                    root_item.target_bone = ""
                    root_item.confidence = 0.0
                    root_item.manual_override = False
                    ignored_root_sources.add(root_item.source_bone)
                    reserved_sources.add(root_item.source_bone)

        # Hips/Pelvis maps to Hips/Pelvis and owns BOTH Rotation and Location by default.
        if source_hips and target_hips:
            for hips_item in source_hips:
                if hips_item.manual_override:
                    if hips_item.target_bone:
                        reserve(hips_item)
                    continue
                hip_target = max(
                    target_hips,
                    key=lambda b: bone_match_score(
                        hips_item.source_bone, b.name, hips_item.hierarchy_depth, target_depths[b.name]
                    ),
                )
                hips_item.target_bone = hip_target.name
                hips_item.copy_rotation = True
                hips_item.copy_location = True
                hips_item.copy_scale = False
                hips_item.manual_override = False
                hips_item.confidence = max(
                    bone_match_score(
                        hips_item.source_bone, hip_target.name,
                        hips_item.hierarchy_depth, target_depths[hip_target.name]
                    ),
                    0.90,
                )
                reserve(hips_item)
                reserved_sources.add(hips_item.source_bone)

        # Main motion first so important bones get first choice.
        rows = sorted(settings.mappings, key=lambda item: (-item.importance, item.hierarchy_index))
        mapped_count = 0

        for item in rows:
            if item.source_bone in reserved_sources:
                mapped_count += 1
                continue

            # Static bones are available in Show > All Bones for manual mapping,
            # but Auto Map deliberately ignores them to avoid clutter and constant curves.
            if not (item.motion_detected or item.direct_keyed or item.importance > 0.0) and not item.manual_override:
                continue

            if self.only_unmapped and item.target_bone:
                reserve(item)
                continue
            if item.manual_override and item.target_bone:
                continue

            required_channels = mapping_channels(item)
            if not required_channels:
                continue

            best_name = ""
            best_score = 0.0
            for target_bone in target.data.bones:
                # Sharing a target is legal if this row controls different channels.
                if required_channels & used_channels.get(target_bone.name, set()):
                    continue
                score = bone_match_score(
                    item.source_bone,
                    target_bone.name,
                    item.hierarchy_depth,
                    target_depths[target_bone.name],
                )
                if score > best_score:
                    best_score = score
                    best_name = target_bone.name

            if best_score >= settings.automap_threshold:
                item.target_bone = best_name
                item.manual_override = False
                item.confidence = best_score
                reserve(item)
                mapped_count += 1
            else:
                item.target_bone = ""
                item.manual_override = False
                item.confidence = best_score

        settings.status_message = f"Auto-mapped {mapped_count} bones"
        if ignored_root_sources:
            settings.status_message += " · Source Root ignored (Target has no Root)"
        elif source_roots and target_roots:
            settings.status_message += " · Root mapped only to Root"
        if source_hips and target_hips:
            settings.status_message += " · Hips uses Rot + Loc"
        self.report({'INFO'}, settings.status_message)
        return {'FINISHED'}


# -----------------------------------------------------------------------------
# Validation
# -----------------------------------------------------------------------------

def build_validation_report(settings):
    source = settings.source_armature
    target = settings.target_armature
    action = ensure_source_action(settings)
    lines = []
    has_error = False

    if not source:
        return "ERROR: Source rig is not set", True
    if not target:
        return "ERROR: Target rig is not set", True
    if source == target:
        return "ERROR: Source and target are the same object", True
    if not action:
        return "ERROR: Source Action is not set", True
    if not settings.mappings:
        return "ERROR: Run Analyze Animation first", True
    if settings.calibration_mode:
        return "ERROR: Exit Calibration MODE before Validate/Preview/Bake", True

    valid_target_names = {b.name for b in target.data.bones}
    mapped = [item for item in settings.mappings if item.target_bone]
    invalid = [item for item in mapped if item.target_bone not in valid_target_names]
    no_channels = [item for item in mapped if not mapping_channels(item)]

    significant = [item for item in settings.mappings if item.significant]
    unmapped_significant = [item for item in significant if not item.target_bone]
    target_has_global_root = any(bone_class(b.name) == "root" for b in target.data.bones)
    ignored_global_roots = [
        item for item in unmapped_significant
        if bone_class(item.source_bone) == "root" and not target_has_global_root
    ]
    optional_unmapped = [
        item for item in unmapped_significant
        if bone_class(item.source_bone) in {"finger", "thumb", "toe"}
    ]
    non_optional = [
        item for item in unmapped_significant
        if item not in optional_unmapped and item not in ignored_global_roots
    ]
    chain_absorbed = [
        item for item in non_optional
        if skipped_chain_is_absorbed(item, settings, source)
    ]
    core_unmapped = [item for item in non_optional if item not in chain_absorbed]

    # Same target is allowed, but each actual destination channel may only have one source.
    # Root/Hips Location can be routed to a different target bone than Rotation.
    target_channel_sources = {}
    routed_locations = []
    for item in mapped:
        for channel in mapping_channels(item):
            destination = item.target_bone
            if channel == 'L':
                destination = resolve_root_motion_target_name(settings, target, item)
                if destination != item.target_bone:
                    routed_locations.append((item.source_bone, destination, item.target_bone))
            target_channel_sources.setdefault((destination, channel), []).append(item.source_bone)
    conflicts = {
        key: names for key, names in target_channel_sources.items()
        if len(names) > 1
    }

    opposite_side = []
    for item in mapped:
        ss = detect_side(item.source_bone)
        ts = detect_side(item.target_bone)
        if ss in {'L', 'R'} and ts in {'L', 'R'} and ss != ts:
            opposite_side.append(f"{item.source_bone} -> {item.target_bone}")

    constrained = set()
    for item in mapped:
        pb = target.pose.bones.get(item.target_bone)
        if pb and any(con.type not in {'IK', 'SPLINE_IK'} for con in pb.constraints):
            constrained.add(pb.name)

    lines.append(f"OK: {len(mapped)} mapped / {len(settings.mappings)} analyzed bones")
    calibrated_rows = sum(1 for item in mapped if reference_is_current(item))
    legacy_reference_rows = sum(
        1 for item in mapped
        if item.has_reference and item.reference_space != CURRENT_REFERENCE_SPACE
    )
    if calibrated_rows:
        lines.append(f"OK: V1.8 Reference Pose available for {calibrated_rows} mapped rows")
    if legacy_reference_rows:
        lines.append(f"WARNING: {legacy_reference_rows} calibration rows use an older format; recapture Calibration MODE")
    if not calibrated_rows and not legacy_reference_rows:
        lines.append("INFO: No captured reference pose; native Rest Pose conversion will be used")

    if routed_locations:
        unique_routes = []
        seen = set()
        for source_name, destination, rot_target in routed_locations:
            key = (source_name, destination, rot_target)
            if key not in seen:
                seen.add(key)
                unique_routes.append(key)
        for source_name, destination, rot_target in unique_routes[:4]:
            lines.append(f"INFO: {source_name} Loc -> {destination}; Rot -> {rot_target}")

    if settings.root_motion_mode == 'CUSTOM':
        custom = settings.root_motion_target
        if not custom or custom not in valid_target_names:
            has_error = True
            lines.append("ERROR: Custom Root Motion Bone is empty or missing")

    ik_active = active_ik_constraints(target)
    if ik_active:
        has_error = True
        lines.append(f"ERROR: {len(ik_active)} active Target IK constraint(s); press Disable Target IK before Preview/Bake")
        lines.append("  " + ", ".join(f"{bone}:{name}" for bone, name in ik_active[:5]))
    elif settings.ik_disabled:
        lines.append("INFO: Target IK constraints are currently disabled")

    core_significant_count = len(significant) - len(optional_unmapped) - len(ignored_global_roots)
    core_mapped_count = core_significant_count - len(core_unmapped) - len(chain_absorbed)
    lines.append(f"OK: {core_mapped_count} direct core mappings")

    if invalid:
        has_error = True
        lines.append(f"ERROR: {len(invalid)} mappings point to missing target bones")

    if conflicts:
        has_error = True
        lines.append(f"ERROR: {len(conflicts)} target channels receive more than one source")
        channel_labels = {'R': 'Rot', 'L': 'Loc', 'S': 'Scale'}
        for (target_name, channel), names in list(conflicts.items())[:6]:
            lines.append(f"  {target_name} {channel_labels[channel]} <- {', '.join(names)}")

    shared_targets = {}
    for item in mapped:
        shared_targets.setdefault(item.target_bone, []).append(item)
    valid_shared = [
        name for name, rows in shared_targets.items()
        if len(rows) > 1 and not any((name, ch) in conflicts for ch in {'R', 'L', 'S'})
    ]
    if valid_shared:
        lines.append(f"INFO: {len(valid_shared)} target bones use channel-split mappings")

    if opposite_side:
        lines.append(f"WARNING: {len(opposite_side)} left/right mappings look reversed")
        lines.extend("  " + value for value in opposite_side[:4])

    if core_unmapped:
        lines.append(f"WARNING: {len(core_unmapped)} core significant bones are ignored")
        lines.append("  " + ", ".join(item.source_bone for item in core_unmapped[:8]))

    if chain_absorbed:
        lines.append(f"INFO: {len(chain_absorbed)} skipped source-chain bones are absorbed downstream")
        lines.append("  " + ", ".join(item.source_bone for item in chain_absorbed[:8]))

    if ignored_global_roots:
        lines.append(
            f"INFO: {len(ignored_global_roots)} Source Root bone(s) ignored because Target has no dedicated Root"
        )
        lines.append("  Hips Location can still be routed to the Target root parent in Auto Parent mode")

    if optional_unmapped:
        lines.append(f"INFO: {len(optional_unmapped)} optional finger/toe bones are ignored")
        lines.append("  " + ", ".join(item.source_bone for item in optional_unmapped[:8]))

    if no_channels:
        lines.append(f"WARNING: {len(no_channels)} mapped rows have Rot/Loc/Scale all disabled")

    if constrained:
        lines.append(f"WARNING: {len(constrained)} mapped target bones have non-IK constraints")
        lines.append("  Constraints can modify the baked pose after retargeting")

    if not has_error and not mapped:
        has_error = True
        lines.append("ERROR: Nothing is mapped")

    if calibrated_rows and calibrated_rows != len(mapped):
        lines.append("WARNING: Calibration is partial; recapture after finishing the mapping")

    if not has_error:
        lines.append("READY: Retarget can be baked")

    return "\n".join(lines), has_error


class KTMRT_OT_DebugSelectedMapping(Operator):
    bl_idname = "kt_mrt.debug_selected_mapping"
    bl_label = "Debug Selected"
    bl_description = "Show rest/reference orientation information for the selected mapping"
    bl_options = {'REGISTER'}

    def execute(self, context):
        settings = context.scene.kt_mrt_settings
        if not settings.mappings:
            self.report({'ERROR'}, "No mapping rows")
            return {'CANCELLED'}
        idx = max(0, min(settings.active_mapping_index, len(settings.mappings) - 1))
        item = settings.mappings[idx]
        source = settings.source_armature
        target = settings.target_armature
        if not source or not target or not item.target_bone:
            self.report({'ERROR'}, "Select a mapped row first")
            return {'CANCELLED'}
        src_pb = source.pose.bones.get(item.source_bone)
        tgt_pb = target.pose.bones.get(item.target_bone)
        if not src_pb or not tgt_pb:
            self.report({'ERROR'}, "Mapped bone not found")
            return {'CANCELLED'}

        src_rest = src_pb.bone.matrix_local.copy()
        tgt_rest = tgt_pb.bone.matrix_local.copy()
        src_pose = src_pb.matrix.copy()
        src_rest_q = src_rest.to_quaternion()
        tgt_rest_q = tgt_rest.to_quaternion()
        src_pose_q = src_pose.to_quaternion()
        align_q = tgt_rest_q @ src_rest_q.inverted()
        source_motion_q = src_pose_q @ src_rest_q.inverted()
        source_local_q = src_rest_q.inverted() @ src_pose_q

        src_dir = (src_rest.to_3x3() @ Vector((0.0, 1.0, 0.0))).normalized()
        tgt_dir = (tgt_rest.to_3x3() @ Vector((0.0, 1.0, 0.0))).normalized()
        dot = max(-1.0, min(1.0, src_dir.dot(tgt_dir)))
        axis_angle = math.degrees(math.acos(dot))

        lines = [
            f"{item.source_bone} -> {item.target_bone}",
            f"Rest direction difference: {axis_angle:.2f} deg",
            f"Rest alignment rotation: {shortest_angle_deg(align_q):.2f} deg",
            f"Source world delta: {shortest_angle_deg(source_motion_q):.2f} deg",
            f"Source rest-local delta: {shortest_angle_deg(source_local_q):.2f} deg",
            f"Fallback Rotation Space: {settings.rotation_mode}",
            f"Source parent: {src_pb.parent.name if src_pb.parent else '(none)'}",
            f"Target parent: {tgt_pb.parent.name if tgt_pb.parent else '(none)'}",
        ]

        reference_is_current = item.has_reference and reference_is_compatible(item)
        src_ref = matrix_from_json(item.reference_source_matrix) if reference_is_current else None
        tgt_ref = matrix_from_json(item.reference_target_matrix) if reference_is_current else None
        if src_ref is not None and tgt_ref is not None:
            src_ref_q = src_ref.to_quaternion()
            tgt_ref_q = tgt_ref.to_quaternion()
            # Reference transfer used by Preview/Bake (V1.8 also bridges armature object axes).
            ref_armature_delta = src_pose_q @ src_ref_q.inverted()
            bridge_q = armature_axis_bridge(source, target)
            target_armature_delta = bridge_q @ ref_armature_delta @ bridge_q.inverted()
            predicted_target_q = target_armature_delta @ tgt_ref_q
            # Keep the old local-order value in the report so problematic rigs can be compared.
            old_local_order_q = tgt_ref_q @ src_ref_q.inverted() @ src_pose_q
            src_ref_dir = (src_ref.to_3x3() @ Vector((0.0, 1.0, 0.0))).normalized()
            tgt_ref_dir = (tgt_ref.to_3x3() @ Vector((0.0, 1.0, 0.0))).normalized()
            ref_dot = max(-1.0, min(1.0, src_ref_dir.dot(tgt_ref_dir)))
            lines.extend([
                "Reference Pose: CAPTURED (armature-space)",
                f"Reference direction difference: {math.degrees(math.acos(ref_dot)):.2f} deg",
                f"Source armature delta: {shortest_angle_deg(ref_armature_delta):.2f} deg",
                f"Predicted target rotation: {shortest_angle_deg(predicted_target_q):.2f} deg",
                f"Old local-order result: {shortest_angle_deg(old_local_order_q):.2f} deg",
                "Reference transfer: AXIS-BRIDGED ARMATURE DELTA (V1.8)",
            ])
        elif item.has_reference:
            lines.append("Reference Pose: OLD FORMAT - recapture calibration in V1.8")
        else:
            lines.append("Reference Pose: not captured for this row")

        settings.debug_report = "\n".join(lines)
        settings.show_debug = True
        return {'FINISHED'}


class KTMRT_OT_Validate(Operator):
    bl_idname = "kt_mrt.validate"
    bl_label = "Validate"
    bl_options = {'REGISTER'}

    def execute(self, context):
        settings = context.scene.kt_mrt_settings
        report, has_error = build_validation_report(settings)
        settings.validation_report = report
        settings.show_validation_log = True
        settings.status_message = "Validation found errors" if has_error else "Validation complete"
        if has_error:
            self.report({'WARNING'}, "Validation found errors")
        else:
            self.report({'INFO'}, "Validation complete")
        return {'FINISHED'}


# -----------------------------------------------------------------------------
# Retarget bake
# -----------------------------------------------------------------------------

def create_retarget_action(context, preview=False):
    scene = context.scene
    settings = scene.kt_mrt_settings
    source = settings.source_armature
    target = settings.target_armature
    action = ensure_source_action(settings)

    report, has_error = build_validation_report(settings)
    settings.validation_report = report
    if has_error:
        raise RuntimeError("Validation contains errors")

    mappings = [
        item for item in settings.mappings
        if item.target_bone and target.pose.bones.get(item.target_bone) and source.pose.bones.get(item.source_bone)
    ]
    if not mappings:
        raise RuntimeError("No valid mappings to bake")

    start, end = action_range(action)
    frames = frame_sequence(start, end, settings.bake_step)
    # PoseBone translations live in armature-local units. A world-height ratio can be wildly
    # wrong for FBX rigs with object scale 0.01, so derive the conversion from mapped local
    # bone lengths instead.
    root_scale = mapped_character_scale(source, target, mappings) if settings.scale_root_translation else 1.0

    old_frame = scene.frame_current
    old_source_action = source.animation_data.action if source.animation_data else None
    old_target_action = target.animation_data.action if target.animation_data else None

    old_preview_to_remove = None
    if preview and old_target_action and old_target_action.get("motion_retarget_preview", False):
        old_preview_to_remove = old_target_action

    target.animation_data_create()
    target.animation_data.action = None

    created_action = None
    complete_reference = has_complete_reference_pose(settings)
    bridge_q = armature_axis_bridge(source, target)

    # Build groups once; they do not change from frame to frame.
    groups = {}
    for item in mappings:
        groups.setdefault(item.target_bone, []).append(item)
    ordered_targets = sorted(
        groups.keys(),
        key=lambda name: hierarchy_depth(target.data.bones[name]),
    )

    # Root/Hips Location is evaluated separately so it can drive a dedicated parent/root
    # while Rotation remains on the anatomical mapped bone.
    root_location_items = [
        item for item in mappings
        if item.copy_location and bone_class(item.source_bone) in {'root', 'hips'}
    ]

    try:
        set_action(source, action)

        for frame in frames:
            scene.frame_set(frame)
            context.view_layer.update()

            # Start from the captured equivalent Target pose when the calibration is complete.
            # This preserves unmapped intermediary/helper bones instead of silently putting them
            # back into Rest Pose every frame.
            if complete_reference:
                restore_pose_snapshot(target, settings.reference_target_pose_snapshot)
            else:
                clear_pose_basis(target)
            context.view_layer.update()

            # -------------------------------------------------------------
            # 1) ROOT MOTION / HIPS LOCATION
            # -------------------------------------------------------------
            for item in root_location_items:
                src_pb = source.pose.bones[item.source_bone]
                destination_name = resolve_root_motion_target_name(settings, target, item)
                dst_pb = target.pose.bones.get(destination_name)
                if not dst_pb:
                    continue

                current_armature = dst_pb.matrix.copy()
                cur_loc, cur_rot, cur_scale = current_armature.decompose()

                if reference_is_compatible(item):
                    src_reference = matrix_from_json(item.reference_source_matrix)
                else:
                    src_reference = src_pb.bone.matrix_local.copy()

                if src_reference is None:
                    continue

                src_delta = src_pb.matrix.translation - src_reference.translation
                src_delta = bridge_q @ src_delta
                loc_scale = mapped_location_scale(
                    source, src_pb, target, dst_pb,
                    global_root_scale=root_scale,
                )
                out_loc = cur_loc + src_delta * loc_scale
                dst_pb.matrix = Matrix.LocRotScale(out_loc, cur_rot, cur_scale)
                context.view_layer.update()
                dst_pb.keyframe_insert(data_path="location", frame=frame, group=dst_pb.name)

            # -------------------------------------------------------------
            # 2) ROTATION / NON-ROOT CHANNELS
            # -------------------------------------------------------------
            for target_name in ordered_targets:
                tgt_pb = target.pose.bones[target_name]
                current_armature = tgt_pb.matrix.copy()
                current_world = bone_world_pose_matrix(target, tgt_pb)
                out_loc, out_rot, out_scale = current_world.decompose()
                key_loc = False
                key_rot = False
                key_scale = False

                for item in groups[target_name]:
                    src_pb = source.pose.bones[item.source_bone]
                    is_root_location = item.copy_location and bone_class(item.source_bone) in {'root', 'hips'}
                    # Root/Hips location was already applied to its resolved destination above.
                    copy_location_here = item.copy_location and not is_root_location

                    reference_is_valid = reference_is_compatible(item)
                    src_reference = matrix_from_json(item.reference_source_matrix) if reference_is_valid else None
                    tgt_reference = matrix_from_json(item.reference_target_matrix) if reference_is_valid else None

                    if src_reference is not None and tgt_reference is not None:
                        desired_armature = calibrated_target_matrix(
                            source, src_pb, target, tgt_pb,
                            current_armature,
                            src_reference, tgt_reference,
                            copy_location=copy_location_here,
                            copy_rotation=item.copy_rotation,
                            copy_scale=item.copy_scale,
                            global_root_scale=root_scale,
                        )
                    else:
                        desired_armature = world_aligned_target_matrix(
                            source, src_pb, target, tgt_pb,
                            current_world,
                            copy_location=copy_location_here,
                            copy_rotation=item.copy_rotation,
                            copy_scale=item.copy_scale,
                            global_root_scale=root_scale,
                            rotation_mode=settings.rotation_mode,
                        )

                    desired_world = target.matrix_world @ desired_armature
                    d_loc, d_rot, d_scale = desired_world.decompose()

                    if copy_location_here:
                        out_loc = d_loc
                        key_loc = True
                    if item.copy_rotation:
                        out_rot = d_rot
                        key_rot = True
                    if item.copy_scale:
                        out_scale = d_scale
                        key_scale = True

                final_world = Matrix.LocRotScale(out_loc, out_rot, out_scale)
                tgt_pb.matrix = target.matrix_world.inverted_safe() @ final_world
                context.view_layer.update()

                if key_loc:
                    tgt_pb.keyframe_insert(data_path="location", frame=frame, group=tgt_pb.name)
                if key_rot:
                    tgt_pb.keyframe_insert(data_path=rotation_data_path(tgt_pb), frame=frame, group=tgt_pb.name)
                if key_scale:
                    tgt_pb.keyframe_insert(data_path="scale", frame=frame, group=tgt_pb.name)

            if created_action is None and target.animation_data:
                created_action = target.animation_data.action

        created_action = target.animation_data.action if target.animation_data else created_action
        if not created_action:
            raise RuntimeError("Blender did not create a target Action")

        base_name = action.name or "Action"
        if preview:
            created_action.name = f"PREVIEW_{base_name}_to_{target.name}"
            created_action["motion_retarget_preview"] = True
        else:
            created_action.name = f"{base_name}_retarget_{target.name}"
            created_action["motion_retarget_preview"] = False

        created_action["motion_retarget_source_action"] = action.name
        created_action["motion_retarget_source_rig"] = source.name
        created_action["motion_retarget_target_rig"] = target.name
        created_action["motion_retarget_version"] = KTMRT_ADDON_VERSION
        created_action["motion_retarget_root_mode"] = settings.root_motion_mode

        for fc in action_fcurves(created_action):
            for kp in fc.keyframe_points:
                kp.interpolation = 'LINEAR'

        if old_preview_to_remove and old_preview_to_remove != created_action:
            try:
                if old_preview_to_remove.users == 0:
                    bpy.data.actions.remove(old_preview_to_remove)
            except Exception:
                pass

        return created_action, len(frames), root_scale

    except Exception:
        if target.animation_data:
            target.animation_data.action = old_target_action
        raise

    finally:
        set_action(source, old_source_action)
        scene.frame_set(old_frame)
        context.view_layer.update()


class KTMRT_OT_Preview(Operator):
    bl_idname = "kt_mrt.preview"
    bl_label = "Build Preview"
    bl_description = "Create a temporary baked Action on the target for visual inspection"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        settings = context.scene.kt_mrt_settings
        try:
            action, frame_count, scale = create_retarget_action(context, preview=True)
        except Exception as exc:
            self.report({'ERROR'}, f"Preview failed: {exc}")
            return {'CANCELLED'}

        settings.status_message = f"Preview ready: {action.name} · {frame_count} sampled frames"
        self.report({'INFO'}, settings.status_message)
        return {'FINISHED'}


class KTMRT_OT_Bake(Operator):
    bl_idname = "kt_mrt.bake"
    bl_label = "Bake Retarget"
    bl_description = "Bake a new retargeted Action on the target rig"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        settings = context.scene.kt_mrt_settings
        try:
            action, frame_count, scale = create_retarget_action(context, preview=False)
        except Exception as exc:
            self.report({'ERROR'}, f"Bake failed: {exc}")
            return {'CANCELLED'}

        settings.status_message = f"Baked: {action.name} · {frame_count} sampled frames"
        self.report({'INFO'}, settings.status_message)
        return {'FINISHED'}


# -----------------------------------------------------------------------------
# Preset operators
# -----------------------------------------------------------------------------

class KTMRT_OT_SavePreset(Operator):
    bl_idname = "kt_mrt.save_preset"
    bl_label = "Save Mapping Preset"
    bl_options = {'REGISTER'}

    def execute(self, context):
        settings = context.scene.kt_mrt_settings
        if not settings.mappings:
            self.report({'ERROR'}, "There is no mapping to save")
            return {'CANCELLED'}

        filename = safe_filename(settings.preset_name) + ".json"
        try:
            directory = preset_directory(create=True)
            path = os.path.join(directory, filename)
            payload = {
                "format": "Motion Retarget Mapping",
                "version": KTMRT_ADDON_VERSION,
                "source_rig": settings.source_armature.name if settings.source_armature else "",
                "target_rig": settings.target_armature.name if settings.target_armature else "",
                "source_action": settings.source_action.name if settings.source_action else "",
                "mappings": [
                    {
                        "source": item.source_bone,
                        "target": item.target_bone,
                        "copy_rotation": item.copy_rotation,
                        "copy_location": item.copy_location,
                        "copy_scale": item.copy_scale,
                    }
                    for item in settings.mappings
                ],
            }
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
        except Exception as exc:
            self.report({'ERROR'}, f"Could not save preset next to the addon: {exc}")
            return {'CANCELLED'}

        refresh_preset_cache()
        # Enum may reject assignment until items callback refreshes; ignore if so.
        try:
            settings.preset_file = filename
        except Exception:
            pass

        settings.status_message = f"Preset saved: mappings/{filename}"
        self.report({'INFO'}, settings.status_message)
        return {'FINISHED'}


class KTMRT_OT_LoadPreset(Operator):
    bl_idname = "kt_mrt.load_preset"
    bl_label = "Load Mapping Preset"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        settings = context.scene.kt_mrt_settings
        filename = settings.preset_file
        if not filename or filename == "__NONE__":
            self.report({'ERROR'}, "No preset selected")
            return {'CANCELLED'}
        if not settings.mappings:
            self.report({'ERROR'}, "Analyze an Action first, then load the preset")
            return {'CANCELLED'}

        path = os.path.join(preset_directory(create=False), filename)
        try:
            with open(path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except Exception as exc:
            self.report({'ERROR'}, f"Could not load preset: {exc}")
            return {'CANCELLED'}

        rows = {entry.get("source", ""): entry for entry in payload.get("mappings", [])}
        target = settings.target_armature
        valid_targets = {b.name for b in target.data.bones} if target else set()
        applied = 0
        missing_target = 0

        changed_targets = 0
        for item in settings.mappings:
            entry = rows.get(item.source_bone)
            if not entry:
                continue
            target_name = entry.get("target", "")
            if target_name and target and target_name not in valid_targets:
                target_name = ""
                missing_target += 1

            # Calibration belongs to a Source->Target pair. Reassigning the exact same
            # Target used to fire the property update and erase a perfectly valid
            # calibration. Only change the property when the mapping really changed.
            if item.target_bone != target_name:
                item.target_bone = target_name
                changed_targets += 1
            item.copy_rotation = bool(entry.get("copy_rotation", True))
            item.copy_location = bool(entry.get("copy_location", False))
            item.copy_scale = bool(entry.get("copy_scale", False))
            item.manual_override = True
            item.confidence = -1.0
            applied += 1

        current_refs, mapped_rows = current_calibration_counts(settings)
        settings.calibration_count = current_refs
        settings.has_calibration = current_refs > 0

        stem = os.path.splitext(filename)[0]
        settings.preset_name = stem
        settings.status_message = f"Preset loaded: {applied} rows"
        if changed_targets:
            settings.status_message += f" · {changed_targets} mappings changed: recapture Calibration"
        elif mapped_rows and current_refs == mapped_rows:
            settings.status_message += " · Calibration preserved"
        if missing_target:
            settings.status_message += f" · {missing_target} missing target bones ignored"
        self.report({'INFO'}, settings.status_message)
        return {'FINISHED'}


class KTMRT_OT_OpenPresetFolder(Operator):
    bl_idname = "kt_mrt.open_preset_folder"
    bl_label = "Open Preset Folder"
    bl_options = {'REGISTER'}

    def execute(self, context):
        try:
            path = preset_directory(create=True)
            bpy.ops.wm.path_open(filepath=path)
        except Exception as exc:
            self.report({'ERROR'}, f"Could not open preset folder: {exc}")
            return {'CANCELLED'}
        return {'FINISHED'}


# -----------------------------------------------------------------------------
# Bone Offset utility
# -----------------------------------------------------------------------------

class KTMRT_OT_UseSelectedOffsetRig(Operator):
    bl_idname = "kt_mrt.use_selected_offset_rig"
    bl_label = "Use Selected"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        obj = context.active_object
        if not obj or obj.type != 'ARMATURE':
            self.report({'ERROR'}, "Select an armature")
            return {'CANCELLED'}
        settings = context.scene.kt_mrt_settings
        settings.offset_armature = obj
        if obj.animation_data and obj.animation_data.action:
            settings.offset_action = obj.animation_data.action
        try:
            if obj.data.bones.active:
                settings.offset_bone = obj.data.bones.active.name
        except Exception:
            pass
        return {'FINISHED'}


class KTMRT_OT_UseActiveOffsetBone(Operator):
    bl_idname = "kt_mrt.use_active_offset_bone"
    bl_label = "Use Active Bone"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        settings = context.scene.kt_mrt_settings
        rig = settings.offset_armature or context.active_object
        if not rig or rig.type != 'ARMATURE':
            self.report({'ERROR'}, "Choose an armature first")
            return {'CANCELLED'}
        bone = rig.data.bones.active
        if not bone:
            self.report({'ERROR'}, "Select a pose bone")
            return {'CANCELLED'}
        settings.offset_armature = rig
        settings.offset_bone = bone.name
        return {'FINISHED'}


class KTMRT_OT_StartBoneOffset(Operator):
    bl_idname = "kt_mrt.start_bone_offset"
    bl_label = "Start Offset Edit"
    bl_description = "Capture the selected bone pose at the current frame, then move/rotate it to define a constant offset"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        settings = context.scene.kt_mrt_settings
        rig = settings.offset_armature
        action = settings.offset_action
        bone_name = settings.offset_bone

        if not rig or rig.type != 'ARMATURE':
            self.report({'ERROR'}, "Choose a Rig")
            return {'CANCELLED'}
        if not action:
            action = rig.animation_data.action if rig.animation_data else None
            settings.offset_action = action
        if not action:
            self.report({'ERROR'}, "Choose an Action")
            return {'CANCELLED'}
        pb = rig.pose.bones.get(bone_name)
        if not pb:
            self.report({'ERROR'}, "Choose a valid bone")
            return {'CANCELLED'}

        set_action(rig, action)
        context.view_layer.update()
        settings.offset_capture_local = matrix_to_json(pb.matrix_basis.copy())
        settings.offset_capture_world = matrix_to_json(rig.matrix_world @ pb.matrix)
        settings.offset_capture_frame = context.scene.frame_current
        settings.offset_capture_action_name = action.name
        settings.offset_editing = True
        select_armature_pose_bone(context, rig, bone_name)
        settings.offset_status = "Base captured. Move/rotate the bone, then Apply Offset to Action."
        self.report({'INFO'}, settings.offset_status)
        return {'FINISHED'}


class KTMRT_OT_CancelBoneOffset(Operator):
    bl_idname = "kt_mrt.cancel_bone_offset"
    bl_label = "Cancel Offset Edit"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        settings = context.scene.kt_mrt_settings
        rig = settings.offset_armature
        pb = rig.pose.bones.get(settings.offset_bone) if rig else None
        base = matrix_from_json(settings.offset_capture_local)
        if pb and base is not None:
            pb.matrix_basis = base
            context.view_layer.update()
        settings.offset_editing = False
        settings.offset_capture_local = ""
        settings.offset_capture_world = ""
        settings.offset_capture_action_name = ""
        settings.offset_status = "Offset edit cancelled"
        return {'FINISHED'}


def _component_scale_ratio(a, b):
    return Vector((
        b.x / a.x if abs(a.x) > 1e-8 else 1.0,
        b.y / a.y if abs(a.y) > 1e-8 else 1.0,
        b.z / a.z if abs(a.z) > 1e-8 else 1.0,
    ))


class KTMRT_OT_ApplyBoneOffset(Operator):
    bl_idname = "kt_mrt.apply_bone_offset"
    bl_label = "Apply Offset to Action"
    bl_description = "Apply the captured pose difference to the chosen bone over the whole Action, creating a new Action"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        settings = context.scene.kt_mrt_settings
        rig = settings.offset_armature
        source_action = settings.offset_action
        bone_name = settings.offset_bone

        if not settings.offset_editing:
            self.report({'ERROR'}, "Press Start Offset Edit first")
            return {'CANCELLED'}
        if not rig or rig.type != 'ARMATURE' or not source_action:
            self.report({'ERROR'}, "Rig/Action missing")
            return {'CANCELLED'}
        pb = rig.pose.bones.get(bone_name)
        if not pb:
            self.report({'ERROR'}, "Bone no longer exists")
            return {'CANCELLED'}

        base_local = matrix_from_json(settings.offset_capture_local)
        base_world = matrix_from_json(settings.offset_capture_world)
        if base_local is None or base_world is None:
            self.report({'ERROR'}, "Captured base pose is missing")
            return {'CANCELLED'}

        # Read the user's edited pose before frame changes evaluate the Action again.
        edited_local = pb.matrix_basis.copy()
        edited_world = rig.matrix_world @ pb.matrix

        if settings.offset_space == 'GLOBAL':
            b_loc, b_rot, b_scale = base_world.decompose()
            e_loc, e_rot, e_scale = edited_world.decompose()
            rot_delta = e_rot @ b_rot.inverted()
            loc_delta = e_loc - b_loc
            scale_ratio = _component_scale_ratio(b_scale, e_scale)
        else:
            b_loc, b_rot, b_scale = base_local.decompose()
            e_loc, e_rot, e_scale = edited_local.decompose()
            # Post/local rotation: preserve the offset relative to each frame's own bone orientation.
            rot_delta = b_rot.inverted() @ e_rot
            loc_delta = e_loc - b_loc
            scale_ratio = _component_scale_ratio(b_scale, e_scale)

        rot_changed = shortest_angle_deg(rot_delta) > 0.0001
        loc_changed = loc_delta.length > 1e-7
        scale_changed = any(abs(v - 1.0) > 1e-6 for v in scale_ratio)
        if not (rot_changed or loc_changed or scale_changed):
            self.report({'WARNING'}, "No offset detected; move/rotate/scale the bone after Start Offset Edit")
            return {'CANCELLED'}

        scene = context.scene
        old_frame = scene.frame_current
        old_action = rig.animation_data.action if rig.animation_data else None
        start, end = action_range(source_action)
        frames = frame_sequence(start, end, 1)

        try:
            # Restore the captured pose before evaluating the duplicated Action.
            pb.matrix_basis = base_local
            context.view_layer.update()

            new_action = source_action.copy()
            new_action.name = f"{source_action.name}_offset_{bone_name}"
            new_action["motion_retarget_bone_offset"] = True
            new_action["motion_retarget_offset_bone"] = bone_name
            new_action["motion_retarget_offset_space"] = settings.offset_space
            set_action(rig, new_action)

            for frame in frames:
                scene.frame_set(frame)
                context.view_layer.update()
                pb = rig.pose.bones.get(bone_name)

                if settings.offset_space == 'GLOBAL':
                    cur_world = rig.matrix_world @ pb.matrix
                    c_loc, c_rot, c_scale = cur_world.decompose()
                    n_loc = c_loc + loc_delta if loc_changed else c_loc
                    n_rot = (rot_delta @ c_rot) if rot_changed else c_rot
                    n_scale = Vector((
                        c_scale.x * scale_ratio.x,
                        c_scale.y * scale_ratio.y,
                        c_scale.z * scale_ratio.z,
                    )) if scale_changed else c_scale
                    desired_world = Matrix.LocRotScale(n_loc, n_rot, n_scale)
                    pb.matrix = rig.matrix_world.inverted_safe() @ desired_world
                else:
                    cur_local = pb.matrix_basis.copy()
                    c_loc, c_rot, c_scale = cur_local.decompose()
                    n_loc = c_loc + loc_delta if loc_changed else c_loc
                    n_rot = (c_rot @ rot_delta) if rot_changed else c_rot
                    n_scale = Vector((
                        c_scale.x * scale_ratio.x,
                        c_scale.y * scale_ratio.y,
                        c_scale.z * scale_ratio.z,
                    )) if scale_changed else c_scale
                    pb.matrix_basis = Matrix.LocRotScale(n_loc, n_rot, n_scale)

                context.view_layer.update()
                if loc_changed:
                    pb.keyframe_insert(data_path="location", frame=frame, group=pb.name)
                if rot_changed:
                    pb.keyframe_insert(data_path=rotation_data_path(pb), frame=frame, group=pb.name)
                if scale_changed:
                    pb.keyframe_insert(data_path="scale", frame=frame, group=pb.name)

            for fc in action_fcurves(new_action):
                if f'pose.bones["{bone_name}"]' not in fc.data_path:
                    continue
                for kp in fc.keyframe_points:
                    kp.interpolation = 'LINEAR'

            settings.offset_action = new_action
            settings.offset_editing = False
            settings.offset_capture_local = ""
            settings.offset_capture_world = ""
            settings.offset_capture_action_name = ""
            settings.offset_status = f"Created {new_action.name} with offset on {bone_name} ({len(frames)} frames)"
            scene.frame_set(old_frame)
            context.view_layer.update()
            self.report({'INFO'}, settings.offset_status)
            return {'FINISHED'}

        except Exception as exc:
            set_action(rig, old_action)
            scene.frame_set(old_frame)
            context.view_layer.update()
            self.report({'ERROR'}, f"Bone offset failed: {exc}")
            return {'CANCELLED'}


# -----------------------------------------------------------------------------
# Copy Bone Animation utility
# -----------------------------------------------------------------------------

def action_fcurve_entries(action):
    """Return unique (owner_collection, fcurve) entries for layered and legacy Actions."""
    if not action:
        return []

    entries = []
    seen = set()

    # Layered Actions (Blender 4.4+ / 5.x).
    try:
        for layer in action.layers:
            for strip in layer.strips:
                channelbags = getattr(strip, "channelbags", None)
                if channelbags is None:
                    continue
                for channelbag in channelbags:
                    collection = channelbag.fcurves
                    for fc in list(collection):
                        ptr = fc.as_pointer()
                        if ptr in seen:
                            continue
                        seen.add(ptr)
                        entries.append((collection, fc))
    except Exception:
        pass

    # Legacy / compatibility API.
    try:
        collection = action.fcurves
        for fc in list(collection):
            ptr = fc.as_pointer()
            if ptr in seen:
                continue
            seen.add(ptr)
            entries.append((collection, fc))
    except Exception:
        pass

    return entries


def action_is_layered(action):
    if not action:
        return False
    try:
        return bool(action.is_action_layered)
    except Exception:
        try:
            return len(action.slots) > 0
        except Exception:
            return False


def action_slot_for_object(action, obj):
    """Find the Action slot that belongs to obj, without changing obj's current Action."""
    if not action or not obj:
        return None

    anim_data = getattr(obj, "animation_data", None)
    if anim_data and anim_data.action == action:
        try:
            slot = anim_data.action_slot
            if slot is not None:
                return slot
        except Exception:
            pass

    try:
        slots = list(action.slots)
    except Exception:
        return None

    for slot in slots:
        try:
            if obj in slot.users():
                return slot
        except Exception:
            pass

    compatible = [
        slot for slot in slots
        if getattr(slot, "target_id_type", None) in {None, obj.id_type}
    ]
    if len(compatible) == 1:
        return compatible[0]

    return None


def action_slot_by_identifier(action, identifier):
    if not action or not identifier:
        return None
    try:
        for slot in action.slots:
            if slot.identifier == identifier:
                return slot
    except Exception:
        pass
    return None


def action_fcurve_entries_for_slot(action, slot):
    """Return F-Curves only from one layered Action slot; legacy Actions use all curves."""
    if not action:
        return []

    if slot is not None:
        entries = []
        seen = set()
        try:
            for layer in action.layers:
                for strip in layer.strips:
                    for channelbag in getattr(strip, "channelbags", ()):
                        if getattr(channelbag, "slot", None) != slot:
                            continue
                        collection = channelbag.fcurves
                        for fc in list(collection):
                            ptr = fc.as_pointer()
                            if ptr in seen:
                                continue
                            seen.add(ptr)
                            entries.append((collection, fc))
        except Exception:
            pass
        return entries

    if action_is_layered(action):
        return []

    return action_fcurve_entries(action)


def assign_action_to_object(obj, action, preferred_slot_identifier=""):
    """Assign action to obj and select/create the correct layered Action slot."""
    anim_data = obj.animation_data_create()

    if action is None:
        anim_data.action = None
        return None

    # Legacy Actions do not expose usable slots.
    try:
        if action.is_action_legacy:
            anim_data.action = action
            return None
    except Exception:
        pass

    slot = action_slot_by_identifier(action, preferred_slot_identifier)

    if slot is None:
        try:
            compatible = [
                candidate for candidate in action.slots
                if getattr(candidate, "target_id_type", None) in {None, obj.id_type}
            ]
            if len(compatible) == 1:
                slot = compatible[0]
        except Exception:
            pass

    if slot is None:
        try:
            slot = action.slots.new(id_type=obj.id_type, name=obj.name)
        except Exception:
            slot = None

    anim_data.action = action

    if slot is None:
        try:
            suitable = list(anim_data.action_suitable_slots)
            if suitable:
                slot = suitable[0]
        except Exception:
            pass

    if slot is not None:
        try:
            anim_data.action_slot = slot
        except Exception:
            pass

    return slot


def ensure_action_fcurve_collection(action, obj, preferred_slot_identifier=""):
    """Return the destination F-Curve collection for obj inside action."""
    slot = assign_action_to_object(obj, action, preferred_slot_identifier)

    if slot is not None:
        if action_ensure_channelbag_for_slot is not None:
            try:
                channelbag = action_ensure_channelbag_for_slot(action, slot)
                return channelbag.fcurves, slot
            except Exception:
                pass

        # Local fallback for Blender versions without bpy_extras helper.
        try:
            layer = action.layers[0] if len(action.layers) else action.layers.new(name="Layer")
            strip = None
            for candidate in layer.strips:
                if getattr(candidate, "type", "") == 'KEYFRAME':
                    strip = candidate
                    break
            if strip is None:
                strip = layer.strips.new(type='KEYFRAME')
            channelbag = strip.channelbag(slot, ensure=True)
            return channelbag.fcurves, slot
        except Exception as exc:
            raise RuntimeError(f"Could not create Target Action slot/channelbag: {exc}")

    try:
        return action.fcurves, None
    except Exception:
        raise RuntimeError("Target Action has no writable F-Curve collection")


def _copy_rna_writable_properties(source, target, ignored=None):
    ignored = set(ignored or ())
    ignored.update({"rna_type", "type"})

    try:
        properties = source.bl_rna.properties
    except Exception:
        return

    for prop in properties:
        identifier = prop.identifier
        if identifier in ignored or getattr(prop, "is_readonly", False):
            continue
        try:
            setattr(target, identifier, getattr(source, identifier))
        except Exception:
            pass


def clone_fcurve(owner_collection, source_fc, data_path, group_name):
    """Clone one F-Curve into a target Action slot/channelbag under a new data path."""
    try:
        new_fc = owner_collection.new(
            data_path,
            index=source_fc.array_index,
            group_name=group_name,
        )
    except TypeError:
        # Legacy Action collection.
        new_fc = owner_collection.new(
            data_path,
            index=source_fc.array_index,
            action_group=group_name,
        )

    for attr in (
        "extrapolation", "color_mode", "auto_smoothing",
        "mute", "lock", "hide", "select",
    ):
        try:
            setattr(new_fc, attr, getattr(source_fc, attr))
        except Exception:
            pass

    try:
        new_fc.color = source_fc.color
    except Exception:
        pass

    src_points = list(source_fc.keyframe_points)
    if src_points:
        new_fc.keyframe_points.add(len(src_points))

    for src, dst in zip(src_points, new_fc.keyframe_points):
        for attr in (
            "co", "handle_left", "handle_right",
            "interpolation", "easing",
            "handle_left_type", "handle_right_type",
            "amplitude", "back", "period", "type",
        ):
            try:
                setattr(dst, attr, getattr(src, attr))
            except Exception:
                pass

    # Copy modifiers when possible (Cycles, Noise, Generator, etc.).
    try:
        for src_modifier in source_fc.modifiers:
            dst_modifier = new_fc.modifiers.new(src_modifier.type)
            _copy_rna_writable_properties(src_modifier, dst_modifier)
    except Exception:
        pass

    try:
        new_fc.update()
    except Exception:
        pass

    return new_fc


def remove_fcurves_from_collection(owner_collection, data_paths):
    """Remove matching curves only from the destination Action slot/collection."""
    targets = set(data_paths)
    removed = 0
    for fc in list(owner_collection):
        if fc.data_path not in targets:
            continue
        try:
            owner_collection.remove(fc)
            removed += 1
        except Exception:
            pass
    return removed


def pose_bone_curve_property(data_path, bone_prefix):
    prefix = bone_prefix + "."
    if not data_path.startswith(prefix):
        return ""
    return data_path[len(prefix):]


def copy_item_properties(item):
    properties = set()
    if item.copy_location:
        properties.add("location")
    if item.copy_rotation:
        properties.update({
            "rotation_euler",
            "rotation_quaternion",
            "rotation_axis_angle",
        })
    if item.copy_scale:
        properties.add("scale")
    return properties


def copy_item_channel_codes(item):
    channels = []
    if item.copy_rotation:
        channels.append('R')
    if item.copy_location:
        channels.append('L')
    if item.copy_scale:
        channels.append('S')
    return channels


def active_copy_mapping(settings, create=False):
    if settings.copy_destinations:
        index = max(0, min(settings.copy_destination_index, len(settings.copy_destinations) - 1))
        settings.copy_destination_index = index
        return settings.copy_destinations[index]
    if not create:
        return None
    item = settings.copy_destinations.add()
    settings.copy_destination_index = 0
    return item


def active_bone_name_for_rig(context, rig):
    if not rig or context.active_object != rig:
        return ""
    try:
        bone = rig.data.bones.active
        return bone.name if bone else ""
    except Exception:
        return ""


class KTMRT_UL_CopyMappings(UIList):
    """Editable Source Bone -> Write To Bone rows."""

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        settings = context.scene.kt_mrt_settings
        source_rig = settings.copy_source_armature
        target_rig = settings.copy_target_armature

        if self.layout_type in {'DEFAULT', 'COMPACT'}:
            row = layout.row(align=True)

            source_col = row.row(align=True)
            source_col.scale_x = 1.0
            if source_rig and source_rig.type == 'ARMATURE':
                source_col.prop_search(item, "source_bone", source_rig.data, "bones", text="")
            else:
                source_col.prop(item, "source_bone", text="")

            row.label(text="", icon='FORWARD')

            target_col = row.row(align=True)
            target_col.scale_x = 1.0
            if target_rig and target_rig.type == 'ARMATURE':
                target_col.prop_search(item, "target_bone", target_rig.data, "bones", text="")
            else:
                target_col.prop(item, "target_bone", text="")

            row.prop(item, "copy_rotation", text="R", toggle=True)
            row.prop(item, "copy_location", text="L", toggle=True)
            row.prop(item, "copy_scale", text="S", toggle=True)
        else:
            layout.label(text=item.source_bone or item.target_bone or "Bone Copy", icon='DUPLICATE')


class KTMRT_OT_UseSelectedCopyRig(Operator):
    bl_idname = "kt_mrt.use_selected_copy_rig"
    bl_label = "Use Selected"
    bl_options = {'REGISTER', 'UNDO'}

    role: EnumProperty(
        items=[
            ('SOURCE', "Source", "Use selected armature as Source Rig"),
            ('TARGET', "Target", "Use selected armature as Target Rig"),
        ],
        default='SOURCE',
    )

    def execute(self, context):
        obj = context.active_object
        if not obj or obj.type != 'ARMATURE':
            self.report({'ERROR'}, "Select an armature")
            return {'CANCELLED'}

        settings = context.scene.kt_mrt_settings
        active_action = obj.animation_data.action if obj.animation_data else None
        active_bone = active_bone_name_for_rig(context, obj)

        if self.role == 'TARGET':
            settings.copy_target_armature = obj
            if active_action:
                settings.copy_target_action = active_action
            if active_bone:
                item = active_copy_mapping(settings, create=True)
                item.target_bone = active_bone
        else:
            settings.copy_source_armature = obj
            if active_action:
                settings.copy_source_action = active_action
            if active_bone:
                item = active_copy_mapping(settings, create=True)
                item.source_bone = active_bone

        return {'FINISHED'}


class KTMRT_OT_UseCurrentCopyAction(Operator):
    bl_idname = "kt_mrt.use_current_copy_action"
    bl_label = "Use Current Action"
    bl_options = {'REGISTER', 'UNDO'}

    role: EnumProperty(
        items=[
            ('SOURCE', "Source", "Use the Source Rig's active Action"),
            ('TARGET', "Target", "Use the Target Rig's active Action"),
        ],
        default='SOURCE',
    )

    def execute(self, context):
        settings = context.scene.kt_mrt_settings
        rig = settings.copy_target_armature if self.role == 'TARGET' else settings.copy_source_armature

        if not rig or rig.type != 'ARMATURE':
            self.report({'ERROR'}, f"Choose a {self.role.title()} Rig first")
            return {'CANCELLED'}

        action = rig.animation_data.action if rig.animation_data else None
        if not action:
            self.report({'ERROR'}, f"{self.role.title()} Rig has no active Action")
            return {'CANCELLED'}

        if self.role == 'TARGET':
            settings.copy_target_action = action
        else:
            settings.copy_source_action = action

        return {'FINISHED'}


class KTMRT_OT_AddCopyDestination(Operator):
    bl_idname = "kt_mrt.add_copy_destination"
    bl_label = "Add Bone Copy Row"
    bl_description = "Add a Source Bone -> Write To Bone row"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        settings = context.scene.kt_mrt_settings
        previous = active_copy_mapping(settings, create=False)
        item = settings.copy_destinations.add()

        if previous:
            item.source_bone = previous.source_bone
            item.copy_rotation = previous.copy_rotation
            item.copy_location = previous.copy_location
            item.copy_scale = previous.copy_scale
        else:
            item.source_bone = active_bone_name_for_rig(context, settings.copy_source_armature)
            item.target_bone = active_bone_name_for_rig(context, settings.copy_target_armature)

        settings.copy_destination_index = len(settings.copy_destinations) - 1
        return {'FINISHED'}


class KTMRT_OT_DuplicateCopyDestination(Operator):
    bl_idname = "kt_mrt.duplicate_copy_destination"
    bl_label = "Duplicate Source Row"
    bl_description = "Duplicate the active row and leave its destination empty"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        settings = context.scene.kt_mrt_settings
        source = active_copy_mapping(settings, create=False)
        if source is None:
            self.report({'ERROR'}, "Add or select a Bone Copy row first")
            return {'CANCELLED'}

        item = settings.copy_destinations.add()
        item.source_bone = source.source_bone
        item.target_bone = ""
        item.copy_rotation = source.copy_rotation
        item.copy_location = source.copy_location
        item.copy_scale = source.copy_scale
        settings.copy_destination_index = len(settings.copy_destinations) - 1
        return {'FINISHED'}


class KTMRT_OT_RemoveCopyDestination(Operator):
    bl_idname = "kt_mrt.remove_copy_destination"
    bl_label = "Remove Bone Copy Row"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        settings = context.scene.kt_mrt_settings
        index = settings.copy_destination_index
        if 0 <= index < len(settings.copy_destinations):
            settings.copy_destinations.remove(index)
            settings.copy_destination_index = min(index, max(0, len(settings.copy_destinations) - 1))
        return {'FINISHED'}


class KTMRT_OT_ClearCopyDestinations(Operator):
    bl_idname = "kt_mrt.clear_copy_destinations"
    bl_label = "Clear Bone Copy Rows"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        settings = context.scene.kt_mrt_settings
        settings.copy_destinations.clear()
        settings.copy_destination_index = 0
        settings.copy_status = "Bone Copy rows cleared"
        return {'FINISHED'}


class KTMRT_OT_CopyBoneAnimation(Operator):
    bl_idname = "kt_mrt.copy_bone_animation"
    bl_label = "Copy Bone Animation"
    bl_description = "Copy multiple Source Bone -> Write To Bone rows into a Target Action"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        settings = context.scene.kt_mrt_settings
        source_rig = settings.copy_source_armature
        source_action = settings.copy_source_action
        target_rig = settings.copy_target_armature
        target_action = settings.copy_target_action

        if not source_rig or source_rig.type != 'ARMATURE':
            self.report({'ERROR'}, "Choose a Source Rig")
            return {'CANCELLED'}
        if not target_rig or target_rig.type != 'ARMATURE':
            self.report({'ERROR'}, "Choose a Target Rig")
            return {'CANCELLED'}

        if not source_action:
            source_action = source_rig.animation_data.action if source_rig.animation_data else None
            settings.copy_source_action = source_action
        if not source_action:
            self.report({'ERROR'}, "Choose a Source Action")
            return {'CANCELLED'}

        if not target_action:
            target_action = target_rig.animation_data.action if target_rig.animation_data else None
            if target_action:
                settings.copy_target_action = target_action

        if settings.copy_output_mode == 'CURRENT' and not target_action:
            self.report({'ERROR'}, "Choose a Target Action, or use New Action Copy")
            return {'CANCELLED'}

        valid_rows = []
        channel_owners = {}
        errors = []
        seen_exact = set()

        for row_index, item in enumerate(settings.copy_destinations, start=1):
            source_name = item.source_bone.strip()
            target_name = item.target_bone.strip()

            if not source_name and not target_name:
                continue
            if not source_name or not target_name:
                errors.append(f"Row {row_index}: choose both Source and Write To bones")
                continue
            source_pb = source_rig.pose.bones.get(source_name)
            target_pb = target_rig.pose.bones.get(target_name)
            if source_pb is None:
                errors.append(f"Row {row_index}: Source bone '{source_name}' was not found")
                continue
            if target_pb is None:
                errors.append(f"Row {row_index}: Target bone '{target_name}' was not found")
                continue

            properties = copy_item_properties(item)
            channel_codes = copy_item_channel_codes(item)
            if not properties:
                errors.append(f"Row {row_index}: enable R, L and/or S")
                continue

            exact_key = (source_name, target_name, tuple(sorted(channel_codes)))
            if exact_key in seen_exact:
                continue
            seen_exact.add(exact_key)

            for code in channel_codes:
                key = (target_name, code)
                previous = channel_owners.get(key)
                if previous is not None:
                    errors.append(
                        f"Row {row_index}: {target_name} {code} already receives animation from {previous}"
                    )
                else:
                    channel_owners[key] = source_name

            valid_rows.append({
                "source_name": source_name,
                "target_name": target_name,
                "source_pb": source_pb,
                "target_pb": target_pb,
                "properties": properties,
                "channels": channel_codes,
                "copy_rotation": item.copy_rotation,
                "copy_location": item.copy_location,
                "copy_scale": item.copy_scale,
            })

        if errors:
            self.report({'ERROR'}, errors[0])
            settings.copy_status = " | ".join(errors[:3])
            return {'CANCELLED'}
        if not valid_rows:
            self.report({'ERROR'}, "Add at least one valid Bone Copy row")
            return {'CANCELLED'}

        source_slot = action_slot_for_object(source_action, source_rig)
        if action_is_layered(source_action) and source_slot is None:
            self.report({'ERROR'}, "Source Action slot is ambiguous. Assign it to the Source Rig or press Use Current")
            return {'CANCELLED'}

        source_slot_identifier = getattr(source_slot, "identifier", "") if source_slot else ""
        target_slot = action_slot_for_object(target_action, target_rig) if target_action else None
        target_slot_identifier = getattr(target_slot, "identifier", "") if target_slot else ""

        old_target_action = target_rig.animation_data.action if target_rig.animation_data else None
        old_target_slot = action_slot_for_object(old_target_action, target_rig) if old_target_action else None
        old_target_slot_identifier = getattr(old_target_slot, "identifier", "") if old_target_slot else ""

        output_action = None
        source_snapshot = None
        created_new_action = settings.copy_output_mode == 'NEW'

        try:
            source_read_action = source_action
            source_read_slot = source_slot
            if settings.copy_output_mode == 'CURRENT' and target_action == source_action:
                source_snapshot = source_action.copy()
                source_read_action = source_snapshot
                source_read_slot = action_slot_by_identifier(source_snapshot, source_slot_identifier)

            source_entries = action_fcurve_entries_for_slot(source_read_action, source_read_slot)
            curves_by_row = []

            for row in valid_rows:
                source_prefix = row["source_pb"].path_from_id()
                row_curves = []
                for _owner_collection, fc in source_entries:
                    prop_name = pose_bone_curve_property(fc.data_path, source_prefix)
                    if prop_name in row["properties"]:
                        row_curves.append(fc)
                if not row_curves:
                    raise RuntimeError(
                        f"Source bone {row['source_name']} has no curves in channels {','.join(row['channels'])}"
                    )
                curves_by_row.append((row, source_prefix, row_curves))

            if settings.copy_output_mode == 'CURRENT':
                output_action = target_action
            elif target_action:
                output_action = target_action.copy()
                output_action.name = f"{target_action.name}_bone_copies"
                output_action.use_fake_user = True
            else:
                output_action = bpy.data.actions.new(
                    f"COPY_BONES_to_{safe_filename(target_rig.name)}"
                )
                output_action.use_fake_user = True

            target_collection, _output_slot = ensure_action_fcurve_collection(
                output_action,
                target_rig,
                preferred_slot_identifier=target_slot_identifier,
            )

            destination_paths = set()
            for row in valid_rows:
                prefix = row["target_pb"].path_from_id()
                if row["copy_location"]:
                    destination_paths.add(prefix + ".location")
                if row["copy_rotation"]:
                    destination_paths.update({
                        prefix + ".rotation_euler",
                        prefix + ".rotation_quaternion",
                        prefix + ".rotation_axis_angle",
                    })
                if row["copy_scale"]:
                    destination_paths.add(prefix + ".scale")

            remove_fcurves_from_collection(target_collection, destination_paths)

            copied_curves = 0
            metadata_rows = []
            for row, source_prefix, source_curves in curves_by_row:
                destination_pb = row["target_pb"]
                destination_prefix = destination_pb.path_from_id()

                if row["copy_rotation"]:
                    destination_pb.rotation_mode = row["source_pb"].rotation_mode

                for source_fc in source_curves:
                    suffix = source_fc.data_path[len(source_prefix):]
                    clone_fcurve(
                        target_collection,
                        source_fc,
                        destination_prefix + suffix,
                        row["target_name"],
                    )
                    copied_curves += 1

                metadata_rows.append({
                    "source": row["source_name"],
                    "target": row["target_name"],
                    "channels": row["channels"],
                })

            output_action["motion_retarget_copy_source_rig"] = source_rig.name
            output_action["motion_retarget_copy_source_action"] = source_action.name
            output_action["motion_retarget_copy_target_rig"] = target_rig.name
            output_action["motion_retarget_copy_mappings"] = json.dumps(metadata_rows)
            output_action["motion_retarget_version"] = KTMRT_ADDON_VERSION

            settings.copy_target_action = output_action
            context.view_layer.update()

            verb = "Updated" if settings.copy_output_mode == 'CURRENT' else "Created"
            settings.copy_status = (
                f"{verb} {output_action.name}: {copied_curves} curves copied through "
                f"{len(valid_rows)} bone mapping{'s' if len(valid_rows) != 1 else ''}"
            )
            self.report({'INFO'}, settings.copy_status)
            return {'FINISHED'}

        except Exception as exc:
            try:
                assign_action_to_object(target_rig, old_target_action, old_target_slot_identifier)
            except Exception:
                pass
            if created_new_action and output_action is not None:
                try:
                    bpy.data.actions.remove(output_action, do_unlink=True)
                except Exception:
                    pass
            self.report({'ERROR'}, f"Copy animation failed: {exc}")
            return {'CANCELLED'}

        finally:
            if source_snapshot is not None:
                try:
                    bpy.data.actions.remove(source_snapshot, do_unlink=True)
                except Exception:
                    pass


# -----------------------------------------------------------------------------
# Panel
# -----------------------------------------------------------------------------



def kt_retarget_panels_visible(context):
    scene = getattr(context, "scene", None)
    return bool(scene and getattr(scene, "kt_retarget_enabled", False))


class KTMRT_PT_Main(Panel):
    bl_label = "Motion Retarget"
    bl_order = 0
    bl_idname = "KTMRT_PT_main"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Retarget'

    @classmethod
    def poll(cls, context):
        return kt_retarget_panels_visible(context)

    def draw_header(self, context):
        self.layout.label(text="", icon='ACTION')

    def draw(self, context):
        layout = self.layout
        settings = context.scene.kt_mrt_settings

        # 1) Setup & Analysis
        setup_area = layout.column()
        setup_area.enabled = not settings.calibration_mode
        setup = setup_area.box()
        header = setup.row(align=True)
        header.prop(
            settings, "show_setup", text="Setup & Analysis",
            icon='TRIA_DOWN' if settings.show_setup else 'TRIA_RIGHT',
            emboss=True, toggle=True,
        )

        if settings.show_setup:
            setup.label(text="Source")
            row = setup.row(align=True)
            row.prop(settings, "source_armature", text="")
            row.operator("kt_mrt.use_selected_source", text="Use Selected")
            setup.prop(settings, "source_action", text="Action")

            setup.separator(factor=0.5)
            setup.label(text="Target")
            row = setup.row(align=True)
            row.prop(settings, "target_armature", text="")
            row.operator("kt_mrt.use_selected_target", text="Use Selected")

            setup.separator(factor=0.5)
            row = setup.row(align=True)
            row.prop(settings, "analysis_step")
            row.prop(settings, "significance_ratio")
            setup.operator("kt_mrt.analyze", text="Analyze Animation", icon='ACTION')
            if settings.analyzed:
                setup.label(text=f"Range: {settings.analysis_start} - {settings.analysis_end}")

        # Mapping/presets come BEFORE Calibration because calibration depends on the final map.
        pre_cal = layout.column()
        pre_cal.enabled = not settings.calibration_mode

        if settings.mappings:
            box = pre_cal.box()
            header = box.row(align=True)
            header.prop(
                settings, "show_mapping", text="Bone Mapping",
                icon='TRIA_DOWN' if settings.show_mapping else 'TRIA_RIGHT',
                emboss=True, toggle=True,
            )

            if settings.show_mapping:
                controls = box.split(factor=0.48, align=True)
                sort_row = controls.row(align=True)
                sort_row.label(text="Sort:")
                sort_row.prop(settings, "sort_mode", text="")
                show_row = controls.row(align=True)
                show_row.label(text="Show:")
                show_row.prop(settings, "filter_mode", text="")

                table_header = box.row(align=True)
                col = table_header.row(align=True)
                col.scale_x = 1.15
                col.label(text="Source")
                col = table_header.row(align=True)
                col.scale_x = 1.0
                col.label(text="Target")
                table_header.label(text="")
                table_header.label(text="Rot")
                table_header.label(text="Loc")
                table_header.label(text="Scale")
                table_header.label(text="Match")

                box.template_list(
                    "KTMRT_UL_Mappings", "", settings, "mappings",
                    settings, "active_mapping_index", rows=10,
                )

                row = box.row(align=True)
                op = row.operator("kt_mrt.auto_map", text="Auto Map")
                op.only_unmapped = False
                op = row.operator("kt_mrt.auto_map", text="Fill Missing")
                op.only_unmapped = True
                row.operator("kt_mrt.clear_all_mappings", text="Clear")

                row = box.row(align=True)
                row.prop(settings, "automap_threshold")
                row.prop(settings, "scale_root_translation")

                root_row = box.row(align=True)
                root_row.prop(settings, "root_motion_mode", text="Root Motion")
                if settings.root_motion_mode == 'CUSTOM':
                    target = settings.target_armature
                    if target and target.type == 'ARMATURE':
                        root_row.prop_search(settings, "root_motion_target", target.data, "bones", text="")
                    else:
                        root_row.prop(settings, "root_motion_target", text="")

                if settings.has_calibration:
                    box.label(text="Rotation: captured Reference Pose where available", icon='CHECKMARK')
                box.prop(settings, "rotation_mode", text="Fallback Rotation Space")

                debug_row = box.row(align=True)
                debug_row.operator("kt_mrt.debug_selected_mapping", text="Debug Selected")
                debug_row.prop(
                    settings, "show_debug", text="Debug",
                    icon='TRIA_DOWN' if settings.show_debug else 'TRIA_RIGHT',
                    emboss=False,
                )
                if settings.show_debug and settings.debug_report:
                    debug_box = box.box()
                    for line in settings.debug_report.splitlines():
                        debug_box.label(text=line)

            presets = pre_cal.box()
            header = presets.row(align=True)
            header.prop(
                settings, "show_presets", text="Mapping Presets",
                icon='TRIA_DOWN' if settings.show_presets else 'TRIA_RIGHT',
                emboss=True, toggle=True,
            )

            if settings.show_presets:
                row = presets.row(align=True)
                row.prop(settings, "preset_file", text="")
                row.operator("kt_mrt.load_preset", text="Load")
                row.operator("kt_mrt.open_preset_folder", text="", icon='FILE_FOLDER')
                row = presets.row(align=True)
                row.prop(settings, "preset_name", text="Name")
                row.operator("kt_mrt.save_preset", text="Save JSON")

        # 3) Calibration after the final mapping/preset.
        calibration = layout.box()
        row = calibration.row(align=True)
        row.scale_y = 1.35
        row.operator(
            "kt_mrt.toggle_calibration_mode",
            text="Calibration MODE" if not settings.calibration_mode else "Calibration MODE  ON",
            icon='POSE_HLT',
            depress=settings.calibration_mode,
        )

        current_refs, mapped_rows = current_calibration_counts(settings)
        if not settings.calibration_mode:
            status = calibration.row(align=True)
            if mapped_rows and current_refs == mapped_rows:
                status.label(text=f"CALIBRATION DONE  ({current_refs}/{mapped_rows})", icon='CHECKMARK')
            elif current_refs:
                status.alert = True
                status.label(text=f"CALIBRATION PARTIAL  ({current_refs}/{mapped_rows}) - recapture", icon='ERROR')
            else:
                status.label(text="Calibration not captured", icon='INFO')
            if current_refs:
                status.operator("kt_mrt.clear_reference_pose", text="Clear", icon='X')

        if settings.calibration_mode:
            warning = calibration.box()
            warning.alert = True
            warning.label(text="CALIBRATION MODE ACTIVE", icon='INFO')
            warning.label(text="Animation is temporarily disabled on Source and Target.")
            warning.label(text="Put BOTH rigs in the same visible pose: T/T or A/A.")
            warning.label(text="Pose Mode only. Do not change Rest Pose, Bone Roll or Edit Mode.")

            row = calibration.row(align=True)
            op = row.operator("kt_mrt.clear_calibration_pose", text="Reset Source Pose")
            op.which = 'SOURCE'
            op = row.operator("kt_mrt.clear_calibration_pose", text="Reset Target Pose")
            op.which = 'TARGET'

            capture = calibration.row(align=True)
            capture.scale_y = 1.25
            capture.operator("kt_mrt.capture_reference_pose", text="Capture Reference Pose", icon='KEY_HLT')
            calibration.operator("kt_mrt.toggle_calibration_mode", text="Exit Calibration MODE / Restore Animation")
        elif not settings.has_calibration:
            calibration.label(text="Recommended when rigs use different A/T poses or bone axes.", icon='INFO')

        # Everything below calibration waits until Calibration Mode is closed.
        post = layout.column()
        post.enabled = not settings.calibration_mode

        if settings.mappings:
            # IK belongs between Calibration and Validate.
            ikbox = post.box()
            ikrow = ikbox.row(align=True)
            ikrow.operator(
                "kt_mrt.toggle_target_ik",
                text="Restore Target IK" if settings.ik_disabled else "Disable Target IK",
                icon='CONSTRAINT_BONE',
            )
            if settings.ik_disabled:
                ikrow.label(text="Target IK muted", icon='CHECKMARK')
            else:
                ikrow.label(text="Required if IK is active", icon='INFO')

            validate = post.box()
            row = validate.row(align=True)
            row.operator("kt_mrt.validate", text="Validate", icon='CHECKMARK')
            row.prop(settings, "bake_step")

            log_header = validate.row(align=True)
            log_header.prop(
                settings, "show_validation_log", text="Validation Log",
                icon='TRIA_DOWN' if settings.show_validation_log else 'TRIA_RIGHT',
                emboss=True, toggle=True,
            )

            if settings.show_validation_log and settings.validation_report:
                for line in settings.validation_report.splitlines():
                    if line.startswith("ERROR"):
                        validate.label(text=line, icon='ERROR')
                    elif line.startswith("WARNING"):
                        validate.label(text=line, icon='ERROR')
                    elif line.startswith("READY"):
                        validate.label(text=line, icon='CHECKMARK')
                    else:
                        validate.label(text=line)

            actions = post.box()
            row = actions.row(align=True)
            row.scale_y = 1.35
            row.operator("kt_mrt.preview", text="Build Preview")
            row.operator("kt_mrt.bake", text="Bake Retarget")

        if settings.status_message:
            post.separator()
            post.label(text=settings.status_message)


class KTMRT_PT_BatchRename(Panel):
    bl_label = "Batch Rename"
    bl_order = 1
    bl_idname = "KTMRT_PT_batch_rename"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Retarget'
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        return kt_retarget_panels_visible(context)

    def draw_header(self, context):
        self.layout.label(text="", icon='SORTALPHA')

    def draw(self, context):
        layout = self.layout
        settings = context.scene.kt_mrt_settings
        layout.enabled = not settings.calibration_mode

        row = layout.row(align=True)
        row.prop(settings, "rename_armature", text="Rig")
        row.operator("kt_mrt.use_selected_rename_rig", text="Use Selected")

        layout.prop(settings, "rename_find", text="Find")
        layout.prop(settings, "rename_replace", text="Replace")
        layout.prop(settings, "rename_match_mode", text="Match")
        layout.operator("kt_mrt.batch_rename_bones", text="Rename Bones")

        if settings.rename_status:
            layout.label(text=settings.rename_status)



class KTMRT_PT_BoneOffset(Panel):
    bl_label = "Add Bone Offset"
    bl_order = 2
    bl_idname = "KTMRT_PT_bone_offset"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Retarget'
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        return kt_retarget_panels_visible(context)

    def draw_header(self, context):
        self.layout.label(text="", icon='CONSTRAINT_BONE')

    def draw(self, context):
        layout = self.layout
        settings = context.scene.kt_mrt_settings
        layout.enabled = not settings.calibration_mode

        row = layout.row(align=True)
        row.prop(settings, "offset_armature", text="Rig")
        row.operator("kt_mrt.use_selected_offset_rig", text="Use Selected")
        layout.prop(settings, "offset_action", text="Action")

        rig = settings.offset_armature
        if rig and rig.type == 'ARMATURE':
            row = layout.row(align=True)
            row.prop_search(settings, "offset_bone", rig.data, "bones", text="Bone")
            row.operator("kt_mrt.use_active_offset_bone", text="", icon='BONE_DATA')
        else:
            layout.prop(settings, "offset_bone", text="Bone")

        layout.prop(settings, "offset_space", text="Space")

        if not settings.offset_editing:
            row = layout.row(align=True)
            row.scale_y = 1.2
            row.operator("kt_mrt.start_bone_offset", text="Start Offset Edit", icon='REC')
            layout.label(text="Capture current pose, then move/rotate the bone in Pose Mode.", icon='INFO')
        else:
            alert = layout.box()
            alert.alert = True
            alert.label(text="OFFSET EDIT ACTIVE", icon='INFO')
            alert.label(text="Adjust the selected bone now; do not keyframe it manually.")
            row = layout.row(align=True)
            row.scale_y = 1.2
            row.operator("kt_mrt.apply_bone_offset", text="Apply Offset to Action", icon='KEY_HLT')
            row.operator("kt_mrt.cancel_bone_offset", text="Cancel", icon='X')

        if settings.offset_status:
            layout.label(text=settings.offset_status)


class KTMRT_PT_CopyBoneAnimation(Panel):
    bl_label = "Copy Bone Animation"
    bl_order = 3
    bl_idname = "KTMRT_PT_copy_bone_animation"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Retarget'
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        return kt_retarget_panels_visible(context)

    def draw_header(self, context):
        self.layout.label(text="", icon='DUPLICATE')

    def draw(self, context):
        layout = self.layout
        settings = context.scene.kt_mrt_settings
        layout.enabled = not settings.calibration_mode

        # One collapsible, unified setup area for both rigs/actions.
        setup = layout.box()
        header = setup.row(align=True)
        header.prop(
            settings,
            "copy_setup_expanded",
            text="Source & Target",
            icon='TRIA_DOWN' if settings.copy_setup_expanded else 'TRIA_RIGHT',
            toggle=True,
        )

        if settings.copy_setup_expanded:
            source_box = setup.box()
            source_box.label(text="Source", icon='ACTION')
            row = source_box.row(align=True)
            row.prop(settings, "copy_source_armature", text="Rig")
            op = row.operator("kt_mrt.use_selected_copy_rig", text="Use Selected")
            op.role = 'SOURCE'
            row = source_box.row(align=True)
            row.prop(settings, "copy_source_action", text="Action")
            op = row.operator("kt_mrt.use_current_copy_action", text="Use Current")
            op.role = 'SOURCE'

            target_box = setup.box()
            target_box.label(text="Target", icon='ARMATURE_DATA')
            row = target_box.row(align=True)
            row.prop(settings, "copy_target_armature", text="Rig")
            op = row.operator("kt_mrt.use_selected_copy_rig", text="Use Selected")
            op.role = 'TARGET'
            row = target_box.row(align=True)
            row.prop(settings, "copy_target_action", text="Base Action")
            op = row.operator("kt_mrt.use_current_copy_action", text="Use Current")
            op.role = 'TARGET'
            target_box.prop(settings, "copy_output_mode", text="Action Output")

        mappings = layout.box()
        mappings.label(text="Bone Copies", icon='BONE_DATA')

        header = mappings.row(align=True)
        source_head = header.row(align=True)
        source_head.scale_x = 1.0
        source_head.label(text="Source Bone")
        header.label(text="")
        target_head = header.row(align=True)
        target_head.scale_x = 1.0
        target_head.label(text="Write To Bone")
        header.label(text="R")
        header.label(text="L")
        header.label(text="S")

        list_row = mappings.row(align=True)
        list_row.template_list(
            "KTMRT_UL_CopyMappings",
            "",
            settings,
            "copy_destinations",
            settings,
            "copy_destination_index",
            rows=6,
        )

        tools = list_row.column(align=True)
        tools.operator("kt_mrt.add_copy_destination", text="", icon='ADD')
        tools.operator("kt_mrt.remove_copy_destination", text="", icon='REMOVE')
        tools.separator(factor=0.4)
        tools.operator("kt_mrt.duplicate_copy_destination", text="", icon='DUPLICATE')
        tools.operator("kt_mrt.clear_copy_destinations", text="", icon='TRASH')

        button = layout.row(align=True)
        button.scale_y = 1.2
        direct_write = settings.copy_output_mode == 'CURRENT'
        button.alert = direct_write
        button.operator(
            "kt_mrt.copy_bone_animation",
            text="Copy into Target Action" if direct_write else "Copy to New Action",
            icon='ACTION',
        )

        if direct_write:
            layout.label(text="The selected Target Action will be modified directly.", icon='ERROR')
        elif settings.copy_target_action:
            layout.label(text="The Base Action is preserved; a modified copy will be created.", icon='INFO')
        else:
            layout.label(text="No Base Action selected: a new empty Action will be created.", icon='INFO')

        if settings.copy_status:
            layout.label(text=settings.copy_status)


# -----------------------------------------------------------------------------
# Registration
# -----------------------------------------------------------------------------

KTMRT_CLASSES = (
    KTMRT_MappingItem,
    KTMRT_CopyDestinationItem,
    KTMRT_Settings,
    KTMRT_UL_Mappings,
    KTMRT_UL_CopyMappings,
    KTMRT_OT_UseSelectedSource,
    KTMRT_OT_UseSelectedTarget,
    KTMRT_OT_ClearMapping,
    KTMRT_OT_ClearAllMappings,
    KTMRT_OT_UseSelectedRenameRig,
    KTMRT_OT_BatchRenameBones,
    KTMRT_OT_ToggleCalibrationMode,
    KTMRT_OT_ClearCalibrationPose,
    KTMRT_OT_CaptureReferencePose,
    KTMRT_OT_ClearReferencePose,
    KTMRT_OT_ToggleTargetIK,
    KTMRT_OT_Analyze,
    KTMRT_OT_AutoMap,
    KTMRT_OT_DebugSelectedMapping,
    KTMRT_OT_Validate,
    KTMRT_OT_Preview,
    KTMRT_OT_Bake,
    KTMRT_OT_SavePreset,
    KTMRT_OT_LoadPreset,
    KTMRT_OT_OpenPresetFolder,
    KTMRT_OT_UseSelectedOffsetRig,
    KTMRT_OT_UseActiveOffsetBone,
    KTMRT_OT_StartBoneOffset,
    KTMRT_OT_CancelBoneOffset,
    KTMRT_OT_ApplyBoneOffset,
    KTMRT_OT_UseSelectedCopyRig,
    KTMRT_OT_UseCurrentCopyAction,
    KTMRT_OT_AddCopyDestination,
    KTMRT_OT_DuplicateCopyDestination,
    KTMRT_OT_RemoveCopyDestination,
    KTMRT_OT_ClearCopyDestinations,
    KTMRT_OT_CopyBoneAnimation,
    KTMRT_PT_Main,
    KTMRT_PT_BatchRename,
    KTMRT_PT_BoneOffset,
    KTMRT_PT_CopyBoneAnimation,
)



# ------------------------------------------------------------
# Register
# ------------------------------------------------------------

classes = (
    VIEW3D_OT_cursor_orient_to_face_under_mouse,
    VIEW3D_OT_alt_shift_mmb_dispatch,
    VIEW3D_OT_cursor_align_apply_setup,
    VIEW3D_OT_reset_3d_cursor,
    VIEW3D_OT_edit_pivot,
    VIEW3D_OT_edit_pivot_raycast,
    VIEW3D_OT_cursor_align_use_cursor,
    VIEW3D_OT_cursor_align_use_selection,
    VIEW3D_OT_cursor_align_origin_to_geometry, 
    VIEW3D_PT_cursor_align_sidebar,
    VIEW3D_PT_viewport_tools,
    VIEW3D_PT_material_tools,
    VIEW3D_PT_modeling_tools,
    VIEW3D_PT_rig_tools,
    VIEW3D_PT_info_panel,
    CT_OT_enable_dynamic_bg,
    CT_OT_set_active_object_color,
    KT_OT_delete_unused_materials,
    KT_OT_select_by_material,
    KT_OT_remove_material_from_selected,
    KT_OT_quadrangulate_loop,
    KT_OT_circularize_loop,
    KT_OT_create_controllers,
    KT_OT_delete_controllers,
    KT_OT_set_controller_shape,
    KT_OT_apply_controller_color,
    RIGTOOLS_OT_merge_bones,
    KT_OT_clean_actions,
    KT_OT_toggle_retarget,
    KT_OT_update_addon,
)


def register():
    # Register the embedded Retarget RNA types first.
    for cls in KTMRT_CLASSES:
        bpy.utils.register_class(cls)

    for cls in classes:
        bpy.utils.register_class(cls)

    bpy.types.Scene.kt_mrt_settings = PointerProperty(type=KTMRT_Settings)

    bpy.types.Scene.kt_retarget_enabled = bpy.props.BoolProperty(
        name="Retarget",
        description="Show the integrated Retarget sidebar tab",
        default=False,
    )
        
    bpy.types.Scene.cursor_align_enabled = bpy.props.BoolProperty(
        name="Edit 3D Cursor",
        description="Edit the 3D Cursor with vertex snapping and use Alt+Shift+MMB to align its rotation to a face",
        default=False,
        update=update_enabled,
    )

    bpy.types.Scene.cursor_align_status = bpy.props.StringProperty(
        name="Cursor Align Status",
        default="Disabled",
    )

    bpy.types.Scene.cursor_align_info_expanded = bpy.props.BoolProperty(
        name="Info",
        default=False,
    )
    
    bpy.types.Scene.ct_edit_bg_color = bpy.props.FloatVectorProperty(
        name="Edit Background",
        subtype='COLOR',
        size=3,
        min=0.0,
        max=1.0,
        default=hex_to_linear_rgb("#2B331B"),
        update=update_viewport_background,
    )

    bpy.types.Scene.ct_active_obj_color = bpy.props.FloatVectorProperty(
        name="Active Object Color",
        subtype='COLOR',
        size=3,
        min=0.0,
        max=1.0,
        default=(0.776, 1.0, 0.478)
    )

    bpy.types.Scene.ct_bg_enabled = bpy.props.BoolProperty(
        name="Edit BG Enabled",
        default=False
    )
    
    bpy.types.Scene.edit_pivot_enabled = bpy.props.BoolProperty(
        name="Edit Pivot",
        default=False,
    )

    bpy.types.Scene.kt_material_name = bpy.props.StringProperty(
        name="Material",
        description="Material used by Select by Material",
        default="",
    )

    bpy.types.Scene.kt_remove_material_name = bpy.props.StringProperty(
        name="Material",
        description="Material to remove from selected objects",
        default="",
    )

    bpy.types.Scene.kt_select_material_expanded = bpy.props.BoolProperty(
        name="Select by Material",
        default=False,
    )

    bpy.types.Scene.kt_remove_material_expanded = bpy.props.BoolProperty(
        name="Remove Material from Selected",
        default=False,
    )

    bpy.types.Scene.kt_rig_shape_preset = bpy.props.EnumProperty(
        name="Shape Preset",
        description="Controller shape preset; changes are applied immediately to selected controller bones",
        items=(
            ('CIRCLE', "Circle", "Circular controller"),
            ('SQUARE', "Square", "Square controller"),
            ('DIAMOND', "Diamond", "Diamond controller"),
            ('BOX', "Box", "3D box controller"),
            ('CROSS', "Cross", "Cross controller"),
        ),
        default='CIRCLE',
        update=update_rig_shape_preset,
    )

    bpy.types.Scene.kt_rig_controller_color = bpy.props.FloatVectorProperty(
        name="Controller Color",
        description="Custom color applied to selected controller bones",
        subtype='COLOR',
        size=3,
        min=0.0,
        max=1.0,
        default=(0.12, 0.55, 1.0),
    )

    bpy.types.Scene.kt_rig_controllers_expanded = bpy.props.BoolProperty(
        name="Controllers",
        default=False,
    )

    bpy.types.Scene.kt_rig_merge_bones_expanded = bpy.props.BoolProperty(
        name="Fusionar huesos",
        default=False,
    )

    bpy.types.Object.rigtools_bone_a = bpy.props.StringProperty(
        name="Hueso A",
        description="Primer hueso de la cadena",
        default="",
    )

    bpy.types.Object.rigtools_bone_b = bpy.props.StringProperty(
        name="Hueso B",
        description="Segundo hueso de la cadena",
        default="",
    )

    bpy.types.Scene.rigtools_new_name = bpy.props.StringProperty(
        name="Nombre final",
        description="Nombre del hueso fusionado",
        default="",
    )
    
    if viewport_mode_handler not in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.append(viewport_mode_handler)

def unregister():
    unregister_keymaps()

    if hasattr(bpy.types.Scene, "kt_mrt_settings"):
        del bpy.types.Scene.kt_mrt_settings

    if hasattr(bpy.types.Scene, "kt_retarget_enabled"):
        del bpy.types.Scene.kt_retarget_enabled

    if hasattr(bpy.types.Scene, "cursor_align_enabled"):
        del bpy.types.Scene.cursor_align_enabled

    if hasattr(bpy.types.Scene, "cursor_align_status"):
        del bpy.types.Scene.cursor_align_status

    if hasattr(bpy.types.Scene, "cursor_align_info_expanded"):
        del bpy.types.Scene.cursor_align_info_expanded
        
    if hasattr(bpy.types.Scene, "edit_pivot_enabled"):
        del bpy.types.Scene.edit_pivot_enabled

    if hasattr(bpy.types.Scene, "kt_material_name"):
        del bpy.types.Scene.kt_material_name

    if hasattr(bpy.types.Scene, "kt_remove_material_name"):
        del bpy.types.Scene.kt_remove_material_name

    if hasattr(bpy.types.Scene, "kt_select_material_expanded"):
        del bpy.types.Scene.kt_select_material_expanded

    if hasattr(bpy.types.Scene, "kt_remove_material_expanded"):
        del bpy.types.Scene.kt_remove_material_expanded

    if hasattr(bpy.types.Scene, "kt_rig_shape_preset"):
        del bpy.types.Scene.kt_rig_shape_preset

    if hasattr(bpy.types.Scene, "kt_rig_controller_color"):
        del bpy.types.Scene.kt_rig_controller_color

    if hasattr(bpy.types.Scene, "kt_rig_controllers_expanded"):
        del bpy.types.Scene.kt_rig_controllers_expanded

    if hasattr(bpy.types.Scene, "kt_rig_merge_bones_expanded"):
        del bpy.types.Scene.kt_rig_merge_bones_expanded

    if hasattr(bpy.types.Scene, "rigtools_new_name"):
        del bpy.types.Scene.rigtools_new_name

    if hasattr(bpy.types.Object, "rigtools_bone_a"):
        del bpy.types.Object.rigtools_bone_a

    if hasattr(bpy.types.Object, "rigtools_bone_b"):
        del bpy.types.Object.rigtools_bone_b

    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)

    for cls in reversed(KTMRT_CLASSES):
        bpy.utils.unregister_class(cls)
        
    del bpy.types.Scene.ct_edit_bg_color
    del bpy.types.Scene.ct_active_obj_color

    if viewport_mode_handler in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(viewport_mode_handler)

    del bpy.types.Scene.ct_bg_enabled

if __name__ == "__main__":
    register()
