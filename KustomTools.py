bl_info = {
    "name": "KustomTools",
    "author": "Álvaro_A",
    "version": (1, 2, 0),
    "blender": (5, 0, 0),
    "location": "View3D > Sidebar > Kustom Tools",
    "description": "Orient Cursor tools and basic color settings to improve experience",
    "category": "3D View",
}

import bpy
import bmesh
import urllib.request
import os
from mathutils.bvhtree import BVHTree
from bpy_extras import view3d_utils


addon_keymaps = []

# ------------------------------------------------------------
# VIEWPORT TOOLS
# ------------------------------------------------------------

def update_viewport_background():
    obj = bpy.context.object
    scene = bpy.context.scene

    if not obj:
        return

    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == 'VIEW_3D':
                space = area.spaces.active
                shading = space.shading

                if obj.mode == 'EDIT':
                    shading.background_type = 'VIEWPORT'
                    shading.background_color = scene.ct_edit_bg_color
                else:
                    shading.background_type = 'THEME'
                    
def viewport_mode_handler(scene):
    if not scene.ct_bg_enabled:
        return

    obj = bpy.context.object
    if not obj:
        return

    color = scene.ct_edit_bg_color

    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == 'VIEW_3D':
                for space in area.spaces:
                    if space.type == 'VIEW_3D':
                        shading = space.shading

                        if obj.mode == 'EDIT':
                            shading.background_type = 'VIEWPORT'
                            shading.background_color = color
                        else:
                            shading.background_type = 'THEME'
                            

class CT_OT_enable_dynamic_bg(bpy.types.Operator):
    bl_idname = "ct.enable_dynamic_bg"
    bl_label = "Apply Edit Background"

    def execute(self, context):

        context.scene.ct_bg_enabled = True

        # 🔹 Forzar actualización inmediata
        viewport_mode_handler(context.scene)

        self.report({'INFO'}, "Edit Mode background enabled")

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
    bl_label = "◎ → ◼"
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
            bpy.ops.script.reload()

            self.report({'INFO'}, "KustomTools updated")

        except Exception as e:

            self.report({'ERROR'}, str(e))

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
        VIEW3D_OT_cursor_orient_to_face_under_mouse.bl_idname,
        type='MIDDLEMOUSE',
        value='PRESS',
        alt=True,
        shift=True,
    )
    addon_keymaps.append((km, kmi))


def update_enabled(self, context):
    if context.scene.cursor_align_enabled:
        apply_user_setup(context)
        register_keymaps()
        set_status(context, "Use Alt+Shift+MMB to align cursor to face")
    else:
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
        # ON
        # ------------------------------------------------------------
        row = col.row()
        row.scale_y = 1.3
        row.prop(scene, "cursor_align_enabled", text="Copy cursor rot", toggle=True, icon='ORIENTATION_CURSOR')

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

        # 👉 fila  (Origins)
        row = col.row(align=True)
        row.operator("view3d.cursor_align_origin_to_geometry")
        row.operator("view3d.cursor_align_origin_to_cursor")

        # 👉 fila  (Snap SOLO)
        row = col.row(align=True)
        row.operator("view3d.cursor_align_snap_mid")
        
        # 👉 Selection / Cursor
        row = col.row(align=True)
        row.operator("view3d.selection_to_cursor")
        row.operator("view3d.cursor_to_selected")
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
            icon='PLAY'
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

        # ------------------------------------------------------------
        # UPDATER
        # ------------------------------------------------------------
        col.separator()

        col.operator(
            "kt.update_addon",
            text="Check Update",
            icon='IMPORT'
        )
        # ------------------------------------------------------------
        # SHORTCUT
        # ------------------------------------------------------------
        col.separator()
        col.label(text="Shortcut", icon='MOUSE_MMB')
        col.label(text="Alt + Shift + MMB")

        # ------------------------------------------------------------
        # EDIT CURSOR
        # ------------------------------------------------------------
        col.separator()
        col.label(text="Edit Cursor", icon='CURSOR')
        col.label(text="Tool: 3D Cursor")
        col.label(text="Snap: Vertex")
        col.label(text="Target: Closest")

        # ------------------------------------------------------------
        # USE ORIGIN
        # ------------------------------------------------------------
        col.separator()
        col.label(text="Use Origin", icon='OBJECT_ORIGIN')
        col.label(text="Tool: Transform")
        col.label(text="Orientation: Cursor")
        col.label(text="Pivot: Active Element")

        # ------------------------------------------------------------
        # USE CURSOR
        # ------------------------------------------------------------
        col.separator()
        col.label(text="Use Cursor", icon='PIVOT_CURSOR')
        col.label(text="Tool: Transform")
        col.label(text="Pivot: 3D Cursor")
        col.label(text="Snap: OFF")

        # ------------------------------------------------------------
        # ORIGIN TOOLS
        # ------------------------------------------------------------
        col.separator()
        col.label(text="Origin to Geo", icon='OBJECT_ORIGIN')
        col.label(text="Set origin to geometry")

        col.separator()
        col.label(text="Origin to Cursor", icon='CURSOR')
        col.label(text="Set origin to 3D Cursor")

        # ------------------------------------------------------------
        # SNAP POINT
        # ------------------------------------------------------------
        col.separator()
        col.label(text="Snap Point", icon='SNAP_VERTEX')
        col.label(text="Snap: Vertex")
        col.label(text="Target: Center")
        col.label(text="Orientation: Local")

        # ------------------------------------------------------------
        # SELECTION / CURSOR
        # ------------------------------------------------------------
        col.separator()
        col.label(text="Geo to Cursor", icon='MESH_CUBE')
        col.label(text="Move selection to Cursor")

        col.separator()
        col.label(text="Cursor to Selected", icon='CURSOR')
        col.label(text="Move Cursor to selection")

        # ------------------------------------------------------------
        # RESET
        # ------------------------------------------------------------
        col.separator()
        col.label(text="Reset", icon='LOOP_BACK')
        col.label(text="Tool: Transform")
        col.label(text="Orientation: Normal")
        col.label(text="Pivot: Active Element")
        col.label(text="Snap: OFF")

        # ------------------------------------------------------------
        # AUTHOR
        # ------------------------------------------------------------
        col.separator()
        col.label(text="Developed by Álvaro_A", icon='INFO')

# ------------------------------------------------------------
# Register
# ------------------------------------------------------------

classes = (
    VIEW3D_OT_cursor_orient_to_face_under_mouse,
    VIEW3D_OT_cursor_align_apply_setup,
    VIEW3D_OT_cursor_align_edit_cursor,
    VIEW3D_OT_cursor_align_use_cursor,
    VIEW3D_OT_cursor_align_reset,
    VIEW3D_OT_cursor_align_snap_mid,
    VIEW3D_OT_cursor_align_origin_to_geometry, 
    VIEW3D_PT_cursor_align_sidebar,
    VIEW3D_PT_viewport_tools,
    VIEW3D_OT_cursor_align_origin_to_cursor,
    VIEW3D_OT_selection_to_cursor,
    VIEW3D_OT_cursor_to_selected,
    VIEW3D_PT_info_panel,
    CT_OT_enable_dynamic_bg,
    CT_OT_set_active_object_color,
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
        default=hex_to_linear_rgb("#2B331B")
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

    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
        
    del bpy.types.Scene.ct_edit_bg_color
    del bpy.types.Scene.ct_active_obj_color

    if viewport_mode_handler in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(viewport_mode_handler)

    del bpy.types.Scene.ct_bg_enabled

if __name__ == "__main__":
    register()