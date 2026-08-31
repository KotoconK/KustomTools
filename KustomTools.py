
bl_info = {
    "name": "KustomTools",
    "author": "Álvaro_A",
    "version": (1, 7, 0),
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

    set_status(context, "Edit Cursor mode enabled")

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
    bl_label = "◎ Use Origin"
    bl_description = "Apply Transform tool, Individual Origins and Cursor orientation"

    def execute(self, context):
        apply_user_setup(context)
        set_status(context, "Setup applied")
        self.report({'INFO'}, "Setup applied")
        return {'FINISHED'}


class VIEW3D_OT_cursor_align_edit_cursor(bpy.types.Operator):
    bl_idname = "view3d.cursor_align_edit_cursor"
    bl_label = "Edit Cursor"

    def execute(self, context):

        if is_edit_cursor_active(context):
            reset_transform_mode(context)
            self.report({'INFO'}, "Edit Cursor OFF")
        else:
            activate_edit_cursor_mode(context)
            self.report({'INFO'}, "Edit Cursor ON")

        return {'FINISHED'}


class VIEW3D_OT_cursor_align_use_cursor(bpy.types.Operator):
    bl_idname = "view3d.cursor_align_use_cursor"
    bl_label = "✢ Use Cursor"
    bl_description = "Activate Transform tool, set Pivot to 3D Cursor and disable Snap"

    def execute(self, context):
        activate_use_cursor_mode(context)
        self.report({'INFO'}, "Use Cursor enabled")
        return {'FINISHED'}


class VIEW3D_OT_cursor_align_reset(bpy.types.Operator):
    bl_idname = "view3d.cursor_align_reset"
    bl_label = "Reset"
    bl_description = "Restore Transform tool, Normal orientation, Active Element pivot and Snap OFF"

    def execute(self, context):
        reset_transform_mode(context)
        self.report({'INFO'}, "Reset applied")
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

    if context.scene.cursor_align_enabled:

        context.scene.edit_pivot_enabled = False

        apply_user_setup(context)
        register_keymaps()
        set_status(
            context,
            "Use Alt+Shift+MMB to align cursor to face"
        )

    else:

        if not context.scene.edit_pivot_enabled:
            unregister_keymaps()

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
        # EDIT PIVOT
        # ------------------------------------------------------------

        row = col.row()
        row.scale_y = 1.3

        row.enabled = (context.mode == 'OBJECT')

        sub = row.row()
        sub.alert = scene.edit_pivot_enabled

        sub.operator(
            "view3d.edit_pivot",
            text="Edit Pivot",
            icon='PIVOT_INDIVIDUAL'
        )

        # ------------------------------------------------------------
        # COPY CURSOR ROT
        # ------------------------------------------------------------

        row = col.row()
        row.scale_y = 1.3

        row.enabled = (context.mode == 'EDIT_MESH')

        sub = row.row()
        sub.alert = scene.cursor_align_enabled

        sub.prop(
            scene,
            "cursor_align_enabled",
            text="Copy Cursor Rot",
            toggle=True,
            icon='ORIENTATION_CURSOR'
        )

        # ------------------------------------------------------------
        # USE ORIGIN / RESET
        # ------------------------------------------------------------
        col.separator()
        
        row = col.row(align=True)
        sub = row.row(align=True)
        sub.alert = is_edit_cursor_active(context)
        sub.operator("view3d.cursor_align_edit_cursor", icon='CURSOR')
        row.operator("view3d.cursor_align_reset", icon='LOOP_BACK')

        # 👉 fila 
        row = col.row(align=True)
        row.operator("view3d.cursor_align_apply_setup")
        row.operator("view3d.cursor_align_use_cursor")
        
        col.separator()

        # Origin to Geometry - compact icon-only utility button.
        row = col.row(align=True)
        row.enabled = context.mode == 'OBJECT'
        row.operator(
            "view3d.cursor_align_origin_to_geometry",
            text="",
            icon='GIZMO'
        )
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
        col = layout.column(align=True)

        col.label(text="Edge Loop", icon='MESH_GRID')

        box = col.box()
        row = box.row(align=True)
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
            text="COPY CURSOR ROT",
            icon='ORIENTATION_CURSOR'
        )
        col2.label(text="Edit Mode")
        col2.label(text="Orient Cursor from Face")
        col2.label(text="Alt+Shift+MMB → Face Align")

        box.separator()

        col2 = box.column(align=True)

        col2.label(
            text="EDIT CURSOR",
            icon='CURSOR'
        )
        col2.label(text="Tool: 3D Cursor")
        col2.label(text="Snap: Vertex")
        col2.label(text="Target: Closest")

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
            text="USE ORIGIN",
            icon='OBJECT_ORIGIN'
        )
        col2.label(text="Tool: Transform")
        col2.label(text="Orientation: Cursor")
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
        # RESET
        # ------------------------------------------------------------

        box = col.box()

        row = box.row()
        row.label(
            text="RESET",
            icon='LOOP_BACK'
        )

        box.separator(factor=0.3)

        col2 = box.column(align=True)

        col2.label(text="Tool: Transform")
        col2.label(text="Orientation: Normal")
        col2.label(text="Pivot: Active Element")
        col2.label(text="Snap: OFF")
        col2.label(text="Origins: OFF")

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
    VIEW3D_OT_cursor_align_edit_cursor,
    VIEW3D_OT_edit_pivot,
    VIEW3D_OT_edit_pivot_raycast,
    VIEW3D_OT_cursor_align_use_cursor,
    VIEW3D_OT_cursor_align_reset,
    VIEW3D_OT_cursor_align_origin_to_geometry, 
    VIEW3D_PT_cursor_align_sidebar,
    VIEW3D_PT_viewport_tools,
    VIEW3D_PT_material_tools,
    VIEW3D_PT_modeling_tools,
    VIEW3D_PT_info_panel,
    CT_OT_enable_dynamic_bg,
    CT_OT_set_active_object_color,
    KT_OT_delete_unused_materials,
    KT_OT_select_by_material,
    KT_OT_remove_material_from_selected,
    KT_OT_quadrangulate_loop,
    KT_OT_circularize_loop,
    KT_OT_update_addon,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
        
    bpy.types.Scene.cursor_align_enabled = bpy.props.BoolProperty(
        name="Cursor Align Enabled",
        description="Enable Alt+Shift+MMB to orient the 3D cursor to the face under the mouse",
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

    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
        
    del bpy.types.Scene.ct_edit_bg_color
    del bpy.types.Scene.ct_active_obj_color

    if viewport_mode_handler in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(viewport_mode_handler)

    del bpy.types.Scene.ct_bg_enabled

if __name__ == "__main__":
    register()