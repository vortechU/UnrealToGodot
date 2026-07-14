"""
Unreal Engine Python Script
Provides a dark-themed Tkinter GUI for the Unreal to Godot Exporter.
Uses a thread-safe Queue-based architecture to run the Tkinter message loop
in a background thread while executing Unreal APIs exclusively on the main game thread.
"""

import os
import sys
import queue
import threading
import tkinter as tk
from tkinter import filedialog
import unreal

# Import underlying exporters
import export_static_meshes_to_gltf
import export_level_to_json

# Global communication queues
_command_queue = queue.Queue()
_state_queue = queue.Queue()

# Global handles
_tick_handle = None
_gui_thread = None

class UnrealToGodotApp:
    def __init__(self, project_dir):
        self.project_dir = project_dir
        self.root = tk.Tk()
        self.root.title("Unreal ➔ Godot Exporter")
        self.root.geometry("520x450")
        self.root.configure(bg="#1e1e1e")
        self.root.resizable(False, False)
        
        # Bring window to front
        self.root.lift()
        self.root.attributes("-topmost", True)
        self.root.after_idle(self.root.attributes, "-topmost", False)
        
        # Initialize UI layout
        self.init_ui()
        
        # Bind close event
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.is_closed = False
        
        # Start the GUI polling loop for editor state updates
        self.poll_state()

    def init_ui(self):
        # Header container
        header_frame = tk.Frame(self.root, bg="#1e1e1e")
        header_frame.pack(fill=tk.X, padx=20, pady=(15, 10))
        
        title_lbl = tk.Label(
            header_frame, 
            text="Unreal Engine to Godot Exporter", 
            fg="#f59e0b", 
            bg="#1e1e1e", 
            font=("Segoe UI", 13, "bold")
        )
        title_lbl.pack(anchor=tk.W)
        
        subtitle_lbl = tk.Label(
            header_frame, 
            text="Batch export selected static meshes to glTF, and export level layouts to JSON.", 
            fg="#a3a3a3", 
            bg="#1e1e1e", 
            font=("Segoe UI", 9),
            justify=tk.LEFT
        )
        subtitle_lbl.pack(anchor=tk.W, pady=(2, 0))
        
        # --- Section 1: Static Mesh Exporter ---
        mesh_frame = tk.LabelFrame(
            self.root, 
            text=" Static Mesh glTF Exporter ", 
            fg="#f59e0b", 
            bg="#1e1e1e", 
            bd=1, 
            relief=tk.SOLID, 
            font=("Segoe UI", 9, "bold")
        )
        mesh_frame.pack(fill=tk.X, padx=20, pady=5)
        
        mesh_inner = tk.Frame(mesh_frame, bg="#1e1e1e")
        mesh_inner.pack(fill=tk.X, padx=12, pady=10)
        
        self.selection_lbl = tk.Label(
            mesh_inner, 
            text="Selected Content Browser Assets: 0 Static Meshes", 
            fg="#e5e5e5", 
            bg="#1e1e1e", 
            font=("Segoe UI", 10)
        )
        self.selection_lbl.pack(anchor=tk.W, pady=(0, 6))
        
        path_row = tk.Frame(mesh_inner, bg="#1e1e1e")
        path_row.pack(fill=tk.X)
        
        self.mesh_path_entry = tk.Entry(
            path_row, 
            bg="#121212", 
            fg="#ffffff", 
            insertbackground="#ffffff", 
            bd=1, 
            relief=tk.SOLID, 
            font=("Segoe UI", 10)
        )
        self.mesh_path_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=3)
        
        # Default mesh export path
        project_dir = self.project_dir
        default_mesh_dir = os.path.join(project_dir, "Saved", "Exports", "GLTF")
        self.mesh_path_entry.insert(0, default_mesh_dir)
        
        browse_mesh_btn = tk.Button(
            path_row, 
            text="Browse...", 
            bg="#27272a", 
            fg="#ffffff", 
            activebackground="#3f3f46", 
            activeforeground="#ffffff", 
            bd=0, 
            padx=10, 
            font=("Segoe UI", 9, "bold"), 
            command=self.browse_mesh_folder
        )
        browse_mesh_btn.pack(side=tk.LEFT, padx=(6, 0))
        
        self.export_mesh_btn = tk.Button(
            mesh_inner, 
            text="Export Selected Meshes", 
            bg="#b45309", 
            fg="#ffffff", 
            activebackground="#d97706", 
            activeforeground="#ffffff", 
            bd=0, 
            pady=5, 
            font=("Segoe UI", 10, "bold"), 
            command=self.run_mesh_export
        )
        self.export_mesh_btn.pack(fill=tk.X, pady=(10, 0))

        self.export_all_meshes_btn = tk.Button(
            mesh_inner, 
            text="Batch Export All Level Meshes", 
            bg="#27272a", 
            fg="#ffffff", 
            activebackground="#3f3f46", 
            activeforeground="#ffffff", 
            bd=0, 
            pady=5, 
            font=("Segoe UI", 10, "bold"), 
            command=self.run_all_meshes_export
        )
        self.export_all_meshes_btn.pack(fill=tk.X, pady=(6, 0))
        
        # --- Section 2: Level Layout Exporter ---
        layout_frame = tk.LabelFrame(
            self.root, 
            text=" Level Layout JSON Exporter ", 
            fg="#f59e0b", 
            bg="#1e1e1e", 
            bd=1, 
            relief=tk.SOLID, 
            font=("Segoe UI", 9, "bold")
        )
        layout_frame.pack(fill=tk.X, padx=20, pady=10)
        
        layout_inner = tk.Frame(layout_frame, bg="#1e1e1e")
        layout_inner.pack(fill=tk.X, padx=12, pady=10)
        
        self.level_lbl = tk.Label(
            layout_inner, 
            text="Active Level: [Untitled]", 
            fg="#e5e5e5", 
            bg="#1e1e1e", 
            font=("Segoe UI", 10)
        )
        self.level_lbl.pack(anchor=tk.W, pady=(0, 6))
        
        layout_path_row = tk.Frame(layout_inner, bg="#1e1e1e")
        layout_path_row.pack(fill=tk.X)
        
        self.layout_path_entry = tk.Entry(
            layout_path_row, 
            bg="#121212", 
            fg="#ffffff", 
            insertbackground="#ffffff", 
            bd=1, 
            relief=tk.SOLID, 
            font=("Segoe UI", 10)
        )
        self.layout_path_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=3)
        
        # Default layout save path
        default_layout_path = os.path.join(project_dir, "Saved", "Exports", "level_layout.json")
        self.layout_path_entry.insert(0, default_layout_path)
        
        browse_layout_btn = tk.Button(
            layout_path_row, 
            text="Browse...", 
            bg="#27272a", 
            fg="#ffffff", 
            activebackground="#3f3f46", 
            activeforeground="#ffffff", 
            bd=0, 
            padx=10, 
            font=("Segoe UI", 9, "bold"), 
            command=self.browse_layout_file
        )
        browse_layout_btn.pack(side=tk.LEFT, padx=(6, 0))
        
        self.export_layout_btn = tk.Button(
            layout_inner, 
            text="Export Level Layout", 
            bg="#b45309", 
            fg="#ffffff", 
            activebackground="#d97706", 
            activeforeground="#ffffff", 
            bd=0, 
            pady=5, 
            font=("Segoe UI", 10, "bold"), 
            command=self.run_layout_export
        )
        self.export_layout_btn.pack(fill=tk.X, pady=(10, 0))
        
        # --- Footer Status Bar ---
        self.status_lbl = tk.Label(
            self.root, 
            text="Ready", 
            fg="#a3a3a3", 
            bg="#1e1e1e", 
            font=("Segoe UI", 10)
        )
        self.status_lbl.pack(fill=tk.X, padx=20, pady=(5, 0), anchor=tk.W)

    def poll_state(self):
        """Thread-safe state poller. Runs on the Tkinter thread."""
        if self.is_closed:
            return
            
        try:
            state = None
            # Keep popping to consume the latest state packet
            while not _state_queue.empty():
                state = _state_queue.get_nowait()
                
            if state:
                # Update static mesh selection label
                if "selection_count" in state:
                    count = state["selection_count"]
                    self.selection_lbl.config(text=f"Selected Content Browser Assets: {count} Mesh(es)")
                    if count > 0:
                        self.export_mesh_btn.config(state=tk.NORMAL)
                    else:
                        self.export_mesh_btn.config(state=tk.DISABLED)
                
                # Update level label and dynamically adjust JSON path default
                if "level_name" in state:
                    world_name = state["level_name"]
                    self.level_lbl.config(text=f"Active Level: {world_name}")
                    
                    current_path = self.layout_path_entry.get()
                    if current_path and world_name != "UntitledLevel" and "_layout.json" in current_path:
                        parent_dir = os.path.dirname(current_path)
                        expected_filename = f"{world_name}_layout.json"
                        if os.path.basename(current_path) != expected_filename:
                            self.layout_path_entry.delete(0, tk.END)
                            self.layout_path_entry.insert(0, os.path.join(parent_dir, expected_filename))
                            
                # Update status message if sent from main thread
                if "status" in state:
                    msg, status_type = state["status"]
                    self.show_status(msg, status_type)
                    
        except Exception:
            pass
            
        # Re-schedule poll in 200 milliseconds (5 times per second)
        self.root.after(200, self.poll_state)

    def browse_mesh_folder(self):
        dir_path = filedialog.askdirectory(
            initialdir=self.mesh_path_entry.get(),
            title="Select glTF Export Directory"
        )
        if dir_path:
            norm_path = os.path.normpath(dir_path)
            self.mesh_path_entry.delete(0, tk.END)
            self.mesh_path_entry.insert(0, norm_path)

    def browse_layout_file(self):
        file_path = filedialog.asksaveasfilename(
            initialdir=os.path.dirname(self.layout_path_entry.get()),
            initialfile=os.path.basename(self.layout_path_entry.get()),
            defaultextension=".json",
            filetypes=[("JSON Files", "*.json"), ("All Files", "*.*")],
            title="Save Layout JSON"
        )
        if file_path:
            norm_path = os.path.normpath(file_path)
            self.layout_path_entry.delete(0, tk.END)
            self.layout_path_entry.insert(0, norm_path)

    def show_status(self, text, type_str="info"):
        self.status_lbl.config(text=text)
        if type_str == "success":
            self.status_lbl.config(fg="#22c55e")
        elif type_str == "error":
            self.status_lbl.config(fg="#ef4444")
        elif type_str == "warning":
            self.status_lbl.config(fg="#eab308")
        else:
            self.status_lbl.config(fg="#a3a3a3")

    def run_mesh_export(self):
        export_dir = self.mesh_path_entry.get()
        if not export_dir:
            self.show_status("Error: Please select a valid target directory first.", "error")
            return
            
        self.show_status("Export command sent to Unreal...")
        # Queue the export meshes command to run on main thread
        _command_queue.put(("export_meshes", [export_dir]))

    def run_all_meshes_export(self):
        export_dir = self.mesh_path_entry.get()
        if not export_dir:
            self.show_status("Error: Please select a valid target directory first.", "error")
            return
            
        self.show_status("Batch export command sent to Unreal...")
        # Queue the export all meshes command to run on main thread
        _command_queue.put(("export_all_meshes", [export_dir]))

    def run_layout_export(self):
        save_path = self.layout_path_entry.get()
        if not save_path:
            self.show_status("Error: Please select a valid target JSON save path first.", "error")
            return
            
        self.show_status("Export command sent to Unreal...")
        # Queue the export layout command to run on main thread
        _command_queue.put(("export_layout", [save_path]))

    def close(self):
        """Pushes close signal to main thread and shuts down GUI."""
        if not self.is_closed:
            self.is_closed = True
            _command_queue.put(("close", []))
            try:
                self.root.destroy()
            except Exception:
                pass


# ==============================================================================
# UNREAL MAIN THREAD QUEUE TICK MONITOR
# ==============================================================================

def check_editor_queues(delta_time):
    """
    Ticking handler running on the Unreal game thread.
    Pushes current selection/level states to the GUI, and consumes queued export tasks.
    """
    global _tick_handle
    
    # 1. Collect current editor state (Thread-safe read on main thread)
    try:
        selected = []
        try:
            selected = unreal.EditorUtilityLibrary.get_selected_assets()
        except Exception:
            pass
        supported_count = 0
        for a in selected:
            is_static = isinstance(a, unreal.StaticMesh)
            is_skeletal = isinstance(a, unreal.SkeletalMesh) if hasattr(unreal, "SkeletalMesh") else False
            if is_static or is_skeletal:
                supported_count += 1
        
        world_name = "UntitledLevel"
        try:
            world = unreal.EditorLevelLibrary.get_editor_world()
            world_name = world.get_name()
        except Exception:
            pass
            
        # Write state packet
        state = {
            "selection_count": supported_count,
            "level_name": world_name
        }
        
        # Flush older states to keep the queue fresh
        while not _state_queue.empty():
            try:
                _state_queue.get_nowait()
            except queue.Empty:
                break
                
        _state_queue.put(state)
    except Exception:
        pass

    # 2. Process pending UI commands (Thread-safe write executed on main thread)
    try:
        while not _command_queue.empty():
            cmd, args = _command_queue.get_nowait()
            
            if cmd == "export_meshes":
                export_dir = args[0]
                unreal.log("Unreal to Godot Exporter: Starting mesh export on main thread...")
                try:
                    exported, failed = export_static_meshes_to_gltf.export_selected_static_meshes(
                        export_dir=export_dir, show_dialogs=False
                    )
                    if failed > 0:
                        _state_queue.put({"status": (f"Export completed: {exported} exported, {failed} failed.", "warning")})
                    else:
                        _state_queue.put({"status": (f"Successfully exported {exported} meshes to glTF!", "success")})
                except Exception as e:
                    _state_queue.put({"status": (f"Mesh Export Error: {str(e)}", "error")})
                    
            elif cmd == "export_all_meshes":
                export_dir = args[0]
                unreal.log("Unreal to Godot Exporter: Starting batch level mesh export on main thread...")
                try:
                    exported, failed = export_static_meshes_to_gltf.export_all_level_meshes(
                        export_dir=export_dir, show_dialogs=False
                    )
                    if failed > 0:
                        _state_queue.put({"status": (f"Batch export completed: {exported} exported, {failed} failed.", "warning")})
                    else:
                        _state_queue.put({"status": (f"Successfully batch exported {exported} level meshes to glTF!", "success")})
                except Exception as e:
                    _state_queue.put({"status": (f"Batch Export Error: {str(e)}", "error")})
                    
            elif cmd == "export_layout":
                save_path = args[0]
                unreal.log("Unreal to Godot Exporter: Starting level layout export on main thread...")
                try:
                    success = export_level_to_json.export_level_to_json(
                        save_path=save_path, show_dialogs=False
                    )
                    if success:
                        _state_queue.put({"status": ("Successfully exported level layout JSON!", "success")})
                    else:
                        _state_queue.put({"status": ("Failed to export level layout. Check output log.", "error")})
                except Exception as e:
                    _state_queue.put({"status": (f"Layout Export Error: {str(e)}", "error")})
                    
            elif cmd == "close":
                # Safely unregister this ticking handler
                if _tick_handle is not None:
                    unreal.unregister_slate_post_tick_callback(_tick_handle)
                    _tick_handle = None
                unreal.log("Unreal to Godot Exporter: Unregistered Slate tick callback.")
                
            _command_queue.task_done()
    except Exception as e:
        unreal.log_warning(f"Unreal to Godot Exporter: Error checking command queue: {str(e)}")


def show_window():
    """Launches the Tkinter GUI thread and registers the Slate tick queue handler."""
    global _tick_handle, _gui_thread
    
    # 1. Clean up any previous tick hooks
    if _tick_handle is not None:
        try:
            unreal.unregister_slate_post_tick_callback(_tick_handle)
        except Exception:
            pass
        _tick_handle = None
        
    # Clear queues
    while not _command_queue.empty():
        _command_queue.get()
    while not _state_queue.empty():
        _state_queue.get()

    # 2. Start tick callback on Unreal main thread
    _tick_handle = unreal.register_slate_post_tick_callback(check_editor_queues)
    
    # 3. Spawn the Tkinter GUI in its own background thread
    project_dir = os.path.realpath(unreal.Paths.project_dir())
    def run_gui():
        app = UnrealToGodotApp(project_dir)
        app.root.mainloop()
        
    _gui_thread = threading.Thread(target=run_gui)
    _gui_thread.daemon = True
    _gui_thread.start()
    unreal.log("Unreal to Godot Exporter: Threaded Tkinter GUI started.")

def _do_register_menu():
    try:
        menus = unreal.ToolMenus.get()
        window_menu = menus.find_menu("LevelEditor.MainMenu.Window")
        if not window_menu:
            unreal.log_warning("Unreal to Godot Exporter: Could not find Window menu.")
            return
            
        # Register menu entry
        entry = unreal.ToolMenuEntry(
            name="UnrealToGodotExporter",
            type=unreal.MultiBlockType.MENU_ENTRY
        )
        entry.set_label("Unreal to Godot Exporter")
        entry.set_tool_tip("Opens the Unreal Engine to Godot asset and layout exporter GUI")
        entry.set_string_command(
            unreal.ToolMenuStringCommandType.PYTHON,
            "unreal_menu",
            "import unreal_to_godot_gui; unreal_to_godot_gui.show_window()"
        )
        
        window_menu.add_menu_entry("Default", entry)
        menus.refresh_all_widgets()
        unreal.log("Unreal to Godot Exporter: Menu entry successfully registered.")
    except Exception as e:
        unreal.log_warning(f"Unreal to Godot Exporter: Could not register menu entry: {str(e)}")

def register_menu_entry():
    """Checks if Slate is ready, otherwise defers registration to post-init callback."""
    menus = unreal.ToolMenus.get()
    window_menu = menus.find_menu("LevelEditor.MainMenu.Window")
    if window_menu:
        _do_register_menu()
    else:
        unreal.log("Unreal to Godot Exporter: Slate menus not loaded yet. Deferring registration.")
        unreal.register_slate_post_init_callback(_do_register_menu)

# Self-initialize on script import
register_menu_entry()
