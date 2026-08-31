
bl_info = {
    "name": "KustomTools",
    "author": "Álvaro_A",
    "version": (1, 9, 4),
    "blender": (5, 0, 0),
    "location": "View3D > Sidebar > Kustom Tools",
    "description": "Orient Cursor tools and basic color settings to improve experience",
    "category": "3D View",
}

import bpy
import bmesh
import urllib.request
import os
import math
from mathutils import Vector
from mathutils.bvhtree import BVHTree
from bpy_extras import view3d_utils

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
# RIG TOOLS
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
# UI
# ------------------------------------------------------------

class VIEW3D_PT_cursor_align_sidebar(bpy.types.Panel):
    bl_label = "Cursor Align"
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
    bl_label = "Viewport Tools"
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
    bl_label = "Material Tools"
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
    bl_label = "Modeling Tools"
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
    bl_label = "Rig Tools"
    bl_idname = "VIEW3D_PT_rig_tools"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "KustomTools"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        is_pose_mode = (
            context.mode == 'POSE'
            and context.active_object is not None
            and context.active_object.type == 'ARMATURE'
        )
        has_selected_controller = (
            is_pose_mode
            and bool(kt_get_selected_controller_pose_bones(context))
        )

        col = layout.column(align=True)

        row = col.row(align=True)
        row.enabled = is_pose_mode
        row.operator(
            "kt.create_controllers",
            text="Create Controller",
            icon='BONE_DATA'
        )

        row = col.row(align=True)
        row.enabled = is_pose_mode
        row.operator(
            "kt.delete_controllers",
            text="Delete Controller",
            icon='TRASH'
        )

        col.separator(factor=0.5)

        row = col.row(align=True)
        row.enabled = has_selected_controller
        row.prop(
            scene,
            "kt_rig_shape_preset",
            text="Shape Preset"
        )

        row = col.row(align=True)
        row.enabled = has_selected_controller
        row.prop(scene, "kt_rig_controller_color", text="")
        row.operator(
            "kt.apply_controller_color",
            text="Change Color",
            icon='COLOR'
        )

        if not is_pose_mode:
            col.label(text="Select bones in Pose Mode", icon='INFO')


class VIEW3D_PT_info_panel(bpy.types.Panel):
    bl_label = "Info"
    bl_idname = "VIEW3D_PT_info_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "KustomTools"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):

        layout = self.layout
        col = layout.column(align=True)
        version_str = ".".join(map(str, ADDON_VERSION))

        # ------------------------------------------------------------
        # VERSION / UPDATE
        # ------------------------------------------------------------

        box = col.box()

        row = box.row()
        row.label(
            text=f"KUSTOMTOOLS {version_str}",
            icon='BLENDER'
        )

        box.separator(factor=0.3)

        box.operator(
            "kt.update_addon",
            text="Check Update",
            icon='IMPORT'
        )

        # ------------------------------------------------------------
        # GLOBAL SHORTCUT
        # ------------------------------------------------------------

        box = col.box()

        row = box.row()
        row.label(
            text="GLOBAL SHORTCUT",
            icon='MOUSE_MMB'
        )

        box.separator(factor=0.3)

        col2 = box.column(align=True)

        col2.label(text="Alt + Shift + MMB")
        col2.label(text="Context sensitive")

        # ------------------------------------------------------------
        # TOOLS
        # ------------------------------------------------------------

        box = col.box()

        row = box.row()
        row.label(
            text="TOOLS",
            icon='TOOL_SETTINGS'
        )

        box.separator(factor=0.3)

        col2 = box.column(align=True)

        col2.label(
            text="EDIT PIVOT",
            icon='PIVOT_INDIVIDUAL'
        )
        col2.label(text="Object Mode")
        col2.label(text="Affect Only Origins")
        col2.label(text="Snap: Vertex / Edge / Face")
        col2.label(text="Alt+Shift+MMB → Face Align")

        box.separator()

        col2 = box.column(align=True)

        col2.label(
            text="EDIT 3D CURSOR",
            icon='CURSOR'
        )
        col2.label(text="Edit Mode")
        col2.label(text="Tool: 3D Cursor")
        col2.label(text="Snap: Vertex / Closest")
        col2.label(text="Alt+Shift+MMB → Face Align")

        # ------------------------------------------------------------
        # ORIGIN TOOLS
        # ------------------------------------------------------------

        box = col.box()

        row = box.row()
        row.label(
            text="ORIGIN TOOLS",
            icon='OBJECT_ORIGIN'
        )

        box.separator(factor=0.3)

        col2 = box.column(align=True)

        col2.label(
            text="USE SEL ORIENTED",
            icon='ORIENTATION_CURSOR'
        )
        col2.label(text="Tool: Transform")
        col2.label(text="Selection oriented by 3D Cursor")
        col2.label(text="Pivot: Active Element")

        box.separator()

        col2 = box.column(align=True)

        col2.label(
            text="ORIGIN TO GEOMETRY",
            icon='MESH_CUBE'
        )
        col2.label(text="Set origin to geometry")

        # ------------------------------------------------------------
        # CURSOR TOOLS
        # ------------------------------------------------------------

        box = col.box()

        row = box.row()
        row.label(
            text="CURSOR TOOLS",
            icon='CURSOR'
        )

        box.separator(factor=0.3)

        col2 = box.column(align=True)

        col2.label(
            text="USE CURSOR",
            icon='PIVOT_CURSOR'
        )
        col2.label(text="Tool: Transform")
        col2.label(text="Pivot: 3D Cursor")
        col2.label(text="Snap: OFF")

        # ------------------------------------------------------------
        # USE SELECTION / RESET 3D CURSOR
        # ------------------------------------------------------------

        box = col.box()

        row = box.row()
        row.label(
            text="USE SELECTION",
            icon='FACESEL'
        )

        box.separator(factor=0.3)

        col2 = box.column(align=True)
        col2.label(text="Tool: Transform")
        col2.label(text="Orientation: Normal")
        col2.label(text="Pivot: Active Element")
        col2.label(text="Snap: OFF")
        col2.label(text="Origins: OFF")

        box.separator()

        col2 = box.column(align=True)
        col2.label(
            text="RESET 3D CURSOR",
            icon='LOOP_BACK'
        )
        col2.label(text="Location: 0, 0, 0")
        col2.label(text="Rotation: 0, 0, 0")

        # ------------------------------------------------------------
        # RIG TOOLS
        # ------------------------------------------------------------

        box = col.box()
        row = box.row()
        row.label(text="RIG TOOLS", icon='BONE_DATA')
        box.separator(factor=0.3)

        col2 = box.column(align=True)
        col2.label(text="Pose Mode: selected bones")
        col2.label(text="Create Controller → separate control bone")
        col2.label(text="Delete Controller → restore original bone")
        col2.label(text="Original bone stays visible")
        col2.label(text="Shape centered at bone origin/head")
        col2.label(text="Shape dropdown auto-applies to selected CTRL")
        col2.label(text="Color changes only selected CTRL bones")

        # ------------------------------------------------------------
        # AUTHOR
        # ------------------------------------------------------------

        box = col.box()

        row = box.row()
        row.label(
            text="AUTHOR",
            icon='INFO'
        )

        box.separator(factor=0.3)

        col2 = box.column(align=True)

        col2.label(text="Developed by Álvaro_A")

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
    KT_OT_update_addon,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
        
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
    
    if viewport_mode_handler not in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.append(viewport_mode_handler)

def unregister():
    unregister_keymaps()

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

    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
        
    del bpy.types.Scene.ct_edit_bg_color
    del bpy.types.Scene.ct_active_obj_color

    if viewport_mode_handler in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(viewport_mode_handler)

    del bpy.types.Scene.ct_bg_enabled

if __name__ == "__main__":
    register()