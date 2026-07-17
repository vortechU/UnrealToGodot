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
import json
import tkinter as tk
from tkinter import filedialog
import unreal

# Import underlying exporters
import export_static_meshes_to_gltf
import export_level_to_json
import ue2g_diagnose

# Global communication queues
_command_queue = queue.Queue()
_state_queue = queue.Queue()


def _run_diagnostic(export_dir, godot_project=None, strict=False):
    """Audits an export and leaves a report beside it.

    Runs after every export so a problem is described in writing at the moment
    it is created, rather than reconstructed later from a screenshot of the
    Godot console. strict=False because each export action only produces half
    of a complete export -- the missing half is a warning here, not an error.

    Never raises: a diagnostic that breaks the export it reports on is worse
    than no diagnostic at all.
    """
    try:
        _text, rep = ue2g_diagnose.diagnose(export_dir, godot_project=godot_project,
                                            strict=strict)
    except Exception as e:
        unreal.log_warning("Unreal to Godot Exporter: diagnostic failed: %s" % str(e))
        return None

    for err in rep.errors:
        unreal.log_error("EXPORT CHECK: " + err)
    for warn in rep.warnings:
        unreal.log_warning("EXPORT CHECK: " + warn)
    if rep.written_to:
        unreal.log("Unreal to Godot Exporter: wrote %s" % rep.written_to)
    if not rep.errors and not rep.warnings:
        unreal.log("Unreal to Godot Exporter: export check found no problems.")
    return rep


def _diagnostic_status(rep):
    """A one-line verdict for the GUI status bar, or None if nothing to say."""
    if rep is None:
        return None
    if rep.errors:
        return ("Export check: %d problem(s) found -- see ue2g_report.txt"
                % len(rep.errors), "error")
    if rep.warnings:
        return ("Export check: %d warning(s) -- see ue2g_report.txt"
                % len(rep.warnings), "warning")
    return None

# Global handles
_tick_handle = None
_gui_thread = None

class UnrealToGodotApp:
    def __init__(self, project_dir):
        self.project_dir = project_dir
        self.root = tk.Tk()
        self.root.title("Unreal ➔ Godot Exporter")
        self.root.geometry("540x880")
        self.root.configure(bg="#1e1e1e")
        self.root.resizable(True, True)
        
        # Keep window permanently on top of the Unreal Editor
        self.root.attributes("-topmost", True)
        
        # Initialize UI layout
        self.init_ui()
        
        # Load persisted settings and update UI
        self.load_settings()
        self.toggle_godot_path_state()
        
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
        
        # Checkbox for Animation Sequences
        self.export_anims_var = tk.BooleanVar(value=False)
        self.export_anims_cb = tk.Checkbutton(
            mesh_inner,
            text="Export Animation Sequences (Skeletal Meshes)",
            variable=self.export_anims_var,
            fg="#ffffff",
            bg="#1e1e1e",
            activebackground="#1e1e1e",
            activeforeground="#ffffff",
            selectcolor="#121212",
            font=("Segoe UI", 9)
        )
        self.export_anims_cb.pack(anchor=tk.W, pady=(6, 0))
        
        # Checkbox for LOD Levels
        self.export_lods_var = tk.BooleanVar(value=False)
        self.export_lods_cb = tk.Checkbutton(
            mesh_inner,
            text="Export LOD Levels (Separate glTF Files)",
            variable=self.export_lods_var,
            fg="#ffffff",
            bg="#1e1e1e",
            activebackground="#1e1e1e",
            activeforeground="#ffffff",
            selectcolor="#121212",
            font=("Segoe UI", 9)
        )
        self.export_lods_cb.pack(anchor=tk.W, pady=(4, 0))
        
        # Checkbox for Separate Textures Folder
        self.separate_textures_var = tk.BooleanVar(value=True)
        self.separate_textures_cb = tk.Checkbutton(
            mesh_inner,
            text="Export Textures to Sibling Folder",
            variable=self.separate_textures_var,
            fg="#ffffff",
            bg="#1e1e1e",
            activebackground="#1e1e1e",
            activeforeground="#ffffff",
            selectcolor="#121212",
            font=("Segoe UI", 9)
        )
        self.separate_textures_cb.pack(anchor=tk.W, pady=(4, 0))
        
        # There is deliberately no "Max Texture Resolution" control here.
        #
        # There was one, and it did nothing: it set each texture's
        # max_texture_size, which drives the COOKED texture, while
        # TextureExporterPNG writes the SOURCE art. Nothing else in Unreal's
        # Python API resizes source art either, and every route to the cooked
        # pixels reads block-compressed data -- which hands back a constant 0
        # blue channel for BC5 normal maps. See docs/texture-sizing.md.
        #
        # Sizing lives in the Godot importer dock instead, where it works. The
        # note stays because someone who came looking for the dropdown needs to
        # know where it went.
        res_note = tk.Label(
            mesh_inner,
            text="Note: textures export at their source resolution (often 4K).\n"
                 "Use 'Texture size limit' in the Godot importer dock to cap them.",
            fg="#a1a1aa",
            bg="#1e1e1e",
            justify=tk.LEFT,
            font=("Segoe UI", 8)
        )
        res_note.pack(anchor=tk.W, pady=(6, 0))

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
        
        # The default action: meshes and layout have to describe the same level,
        # and exporting them separately is how they drift apart. Textures are
        # only encoded once across the two halves.
        self.export_everything_btn = tk.Button(
            layout_inner,
            text="Export Everything (Meshes + Layout)",
            bg="#15803d",
            fg="#ffffff",
            activebackground="#16a34a",
            activeforeground="#ffffff",
            bd=0,
            pady=7,
            font=("Segoe UI", 10, "bold"),
            command=self.run_full_export
        )
        self.export_everything_btn.pack(fill=tk.X, pady=(10, 0))

        self.export_layout_btn = tk.Button(
            layout_inner,
            text="Export Level Layout only",
            bg="#b45309",
            fg="#ffffff",
            activebackground="#d97706",
            activeforeground="#ffffff",
            bd=0,
            pady=5,
            font=("Segoe UI", 10, "bold"),
            command=self.run_layout_export
        )
        self.export_layout_btn.pack(fill=tk.X, pady=(6, 0))

        # --- Section 2b: Level Export Features (per-feature toggles) ---
        features_frame = tk.LabelFrame(
            self.root,
            text=" Level Export Features ",
            fg="#f59e0b",
            bg="#1e1e1e",
            bd=1,
            relief=tk.SOLID,
            font=("Segoe UI", 9, "bold")
        )
        features_frame.pack(fill=tk.X, padx=20, pady=5)

        features_inner = tk.Frame(features_frame, bg="#1e1e1e")
        features_inner.pack(fill=tk.X, padx=12, pady=8)

        features_grid = tk.Frame(features_inner, bg="#1e1e1e")
        features_grid.pack(fill=tk.X)

        self.feat_lights_var = tk.BooleanVar(value=True)
        self.feat_decals_var = tk.BooleanVar(value=True)
        self.feat_landscape_var = tk.BooleanVar(value=True)
        self.feat_foliage_var = tk.BooleanVar(value=True)
        self.feat_navigation_var = tk.BooleanVar(value=True)
        self.feat_metadata_var = tk.BooleanVar(value=True)

        feature_defs = [
            ("Lights & Post-Process", self.feat_lights_var),
            ("Decals", self.feat_decals_var),
            ("Landscape / Terrain", self.feat_landscape_var),
            ("Foliage & Instances", self.feat_foliage_var),
            ("Navigation Volumes", self.feat_navigation_var),
            ("Tags & Metadata", self.feat_metadata_var),
        ]
        for idx, (feat_label, feat_var) in enumerate(feature_defs):
            feat_cb = tk.Checkbutton(
                features_grid,
                text=feat_label,
                variable=feat_var,
                fg="#ffffff",
                bg="#1e1e1e",
                activebackground="#1e1e1e",
                activeforeground="#ffffff",
                selectcolor="#121212",
                font=("Segoe UI", 9)
            )
            feat_cb.grid(row=idx // 2, column=idx % 2, sticky="w", padx=(0, 10))
        features_grid.grid_columnconfigure(0, weight=1)
        features_grid.grid_columnconfigure(1, weight=1)

        # Direct .tscn generation (needs the Godot project path from Section 3)
        self.write_tscn_var = tk.BooleanVar(value=False)
        self.write_tscn_cb = tk.Checkbutton(
            features_inner,
            text="Generate Godot .tscn scene directly (uses Godot Project Path below)",
            variable=self.write_tscn_var,
            fg="#ffffff",
            bg="#1e1e1e",
            activebackground="#1e1e1e",
            activeforeground="#ffffff",
            selectcolor="#121212",
            font=("Segoe UI", 9)
        )
        self.write_tscn_cb.pack(anchor=tk.W, pady=(6, 0))

        tscn_row = tk.Frame(features_inner, bg="#1e1e1e")
        tscn_row.pack(fill=tk.X, pady=(4, 0))

        tscn_lbl = tk.Label(
            tscn_row,
            text="Scene Name:",
            fg="#a3a3a3",
            bg="#1e1e1e",
            font=("Segoe UI", 9)
        )
        tscn_lbl.pack(side=tk.LEFT, padx=(0, 6))

        self.tscn_name_entry = tk.Entry(
            tscn_row,
            bg="#121212",
            fg="#ffffff",
            insertbackground="#ffffff",
            bd=1,
            relief=tk.SOLID,
            font=("Segoe UI", 10)
        )
        self.tscn_name_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=2)

        # --- Section 3: Godot Integration Exporter ---
        godot_frame = tk.LabelFrame(
            self.root, 
            text=" Godot Engine Integration ", 
            fg="#f59e0b", 
            bg="#1e1e1e", 
            bd=1, 
            relief=tk.SOLID, 
            font=("Segoe UI", 9, "bold")
        )
        godot_frame.pack(fill=tk.X, padx=20, pady=5)
        
        godot_inner = tk.Frame(godot_frame, bg="#1e1e1e")
        godot_inner.pack(fill=tk.X, padx=12, pady=10)
        
        self.auto_transfer_var = tk.BooleanVar(value=False)
        self.auto_transfer_cb = tk.Checkbutton(
            godot_inner,
            text="Auto-transfer exports to Godot Project",
            variable=self.auto_transfer_var,
            fg="#ffffff",
            bg="#1e1e1e",
            activebackground="#1e1e1e",
            activeforeground="#ffffff",
            selectcolor="#121212",
            font=("Segoe UI", 9),
            command=self.toggle_godot_path_state
        )
        self.auto_transfer_cb.pack(anchor=tk.W)
        
        self.godot_path_row = tk.Frame(godot_inner, bg="#1e1e1e")
        self.godot_path_row.pack(fill=tk.X, pady=(6, 0))
        
        self.godot_path_lbl = tk.Label(
            self.godot_path_row,
            text="Godot Project Path:",
            fg="#a3a3a3",
            bg="#1e1e1e",
            font=("Segoe UI", 9)
        )
        self.godot_path_lbl.pack(side=tk.LEFT, padx=(0, 6))
        
        self.godot_path_entry = tk.Entry(
            self.godot_path_row, 
            bg="#121212", 
            fg="#ffffff", 
            insertbackground="#ffffff", 
            bd=1, 
            relief=tk.SOLID, 
            font=("Segoe UI", 10)
        )
        self.godot_path_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=3)
        
        self.browse_godot_btn = tk.Button(
            self.godot_path_row, 
            text="Browse...", 
            bg="#27272a", 
            fg="#ffffff", 
            activebackground="#3f3f46", 
            activeforeground="#ffffff", 
            bd=0, 
            padx=10, 
            font=("Segoe UI", 9, "bold"), 
            command=self.browse_godot_project
        )
        self.browse_godot_btn.pack(side=tk.LEFT, padx=(6, 0))
        
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
            latest_selection_count = None
            latest_level_name = None
            
            # Process all pending packets in the queue
            while not _state_queue.empty():
                packet = _state_queue.get_nowait()
                if not packet:
                    continue
                
                # If this packet contains a status message, show it immediately
                if "status" in packet:
                    msg, status_type = packet["status"]
                    self.show_status(msg, status_type)
                    
                # Accumulate the latest selection and level details
                if "selection_count" in packet:
                    latest_selection_count = packet["selection_count"]
                if "level_name" in packet:
                    latest_level_name = packet["level_name"]
            
            # Apply the accumulated latest state details
            if latest_selection_count is not None:
                self.selection_lbl.config(text=f"Selected Content Browser Assets: {latest_selection_count} Mesh(es)")
                if latest_selection_count > 0:
                    self.export_mesh_btn.config(state=tk.NORMAL)
                else:
                    self.export_mesh_btn.config(state=tk.DISABLED)
                    
            if latest_level_name is not None:
                self.level_lbl.config(text=f"Active Level: {latest_level_name}")
                
                current_path = self.layout_path_entry.get()
                if current_path and latest_level_name != "UntitledLevel" and "_layout.json" in current_path:
                    parent_dir = os.path.dirname(current_path)
                    expected_filename = f"{latest_level_name}_layout.json"
                    if os.path.basename(current_path) != expected_filename:
                        self.layout_path_entry.delete(0, tk.END)
                        self.layout_path_entry.insert(0, os.path.join(parent_dir, expected_filename))
                        
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

    def toggle_godot_path_state(self):
        state = tk.NORMAL if self.auto_transfer_var.get() else tk.DISABLED
        self.godot_path_entry.config(state=state)
        self.browse_godot_btn.config(state=state)

    def browse_godot_project(self):
        dir_path = filedialog.askdirectory(
            initialdir=self.godot_path_entry.get(),
            title="Select Godot Project Directory"
        )
        if dir_path:
            norm_path = os.path.normpath(dir_path)
            self.godot_path_entry.delete(0, tk.END)
            self.godot_path_entry.insert(0, norm_path)

    def get_export_feature_options(self):
        """Collects the per-feature export toggles (see docs/SCHEMA_V2.md)."""
        return {
            "lights": self.feat_lights_var.get(),
            "decals": self.feat_decals_var.get(),
            "landscape": self.feat_landscape_var.get(),
            "foliage": self.feat_foliage_var.get(),
            "navigation": self.feat_navigation_var.get(),
            "metadata": self.feat_metadata_var.get(),
            "write_tscn": self.write_tscn_var.get(),
            "tscn_scene_name": self.tscn_name_entry.get().strip(),
        }

    def load_settings(self):
        config_dir = os.path.join(self.project_dir, "Saved", "Config")
        config_path = os.path.join(config_dir, "UnrealToGodotSettings.json")
        if os.path.exists(config_path):
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    settings = json.load(f)
                if isinstance(settings, dict):
                    if "auto_transfer" in settings:
                        self.auto_transfer_var.set(settings["auto_transfer"])
                    if "godot_project_path" in settings:
                        self.godot_path_entry.delete(0, tk.END)
                        self.godot_path_entry.insert(0, settings["godot_project_path"])
                    if "export_anims" in settings:
                        self.export_anims_var.set(settings["export_anims"])
                    if "export_lods" in settings:
                        self.export_lods_var.set(settings["export_lods"])
                    if "separate_textures" in settings:
                        self.separate_textures_var.set(settings["separate_textures"])
                    if "mesh_path" in settings:
                        self.mesh_path_entry.delete(0, tk.END)
                        self.mesh_path_entry.insert(0, settings["mesh_path"])
                    if "layout_path" in settings:
                        self.layout_path_entry.delete(0, tk.END)
                        self.layout_path_entry.insert(0, settings["layout_path"])
                    # "max_texture_res" may still be in an older settings file.
                    # It is read by nothing now -- the control it fed never
                    # resized anything -- and is ignored rather than migrated.
                    if "features" in settings and isinstance(settings["features"], dict):
                        feats = settings["features"]
                        self.feat_lights_var.set(feats.get("lights", True))
                        self.feat_decals_var.set(feats.get("decals", True))
                        self.feat_landscape_var.set(feats.get("landscape", True))
                        self.feat_foliage_var.set(feats.get("foliage", True))
                        self.feat_navigation_var.set(feats.get("navigation", True))
                        self.feat_metadata_var.set(feats.get("metadata", True))
                        self.write_tscn_var.set(feats.get("write_tscn", False))
                        if feats.get("tscn_scene_name"):
                            self.tscn_name_entry.delete(0, tk.END)
                            self.tscn_name_entry.insert(0, feats["tscn_scene_name"])
            except Exception:
                pass

    def save_settings(self):
        config_dir = os.path.join(self.project_dir, "Saved", "Config")
        os.makedirs(config_dir, exist_ok=True)
        config_path = os.path.join(config_dir, "UnrealToGodotSettings.json")
        settings = {
            "godot_project_path": self.godot_path_entry.get(),
            "auto_transfer": self.auto_transfer_var.get(),
            "export_anims": self.export_anims_var.get(),
            "export_lods": self.export_lods_var.get(),
            "separate_textures": self.separate_textures_var.get(),
            "mesh_path": self.mesh_path_entry.get(),
            "layout_path": self.layout_path_entry.get(),
            "features": self.get_export_feature_options()
        }
        try:
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(settings, f, indent=4)
        except Exception:
            pass

    def get_godot_project_dir_if_valid(self):
        if not self.auto_transfer_var.get():
            return None
            
        godot_project = self.godot_path_entry.get()
        if not godot_project or not os.path.isdir(godot_project):
            self.show_status("Error: Please select a valid Godot Project Path.", "error")
            return -1
            
        if not os.path.exists(os.path.join(godot_project, "project.godot")):
            self.show_status("Error: Path does not contain project.godot.", "error")
            return -1
            
        return godot_project

    def run_mesh_export(self):
        export_dir = self.mesh_path_entry.get()
        if not export_dir:
            self.show_status("Error: Please select a valid target directory first.", "error")
            return
            
        godot_project = self.get_godot_project_dir_if_valid()
        if godot_project == -1:
            return
            
        export_anims = self.export_anims_var.get()
        export_lods = self.export_lods_var.get()
        separate_tex = self.separate_textures_var.get()

        self.save_settings()
        self.show_status("Export command sent to Unreal...")
        # Queue the export meshes command to run on main thread
        _command_queue.put(("export_meshes", [export_dir, export_anims, export_lods, separate_tex, godot_project]))

    def run_all_meshes_export(self):
        export_dir = self.mesh_path_entry.get()
        if not export_dir:
            self.show_status("Error: Please select a valid target directory first.", "error")
            return
            
        godot_project = self.get_godot_project_dir_if_valid()
        if godot_project == -1:
            return
            
        export_anims = self.export_anims_var.get()
        export_lods = self.export_lods_var.get()
        separate_tex = self.separate_textures_var.get()

        self.save_settings()
        self.show_status("Batch export command sent to Unreal...")
        # Queue the export all meshes command to run on main thread
        _command_queue.put(("export_all_meshes", [export_dir, export_anims, export_lods, separate_tex, godot_project]))

    def run_full_export(self):
        """Exports meshes and layout together.

        The two halves have to agree with each other -- the layout names meshes
        the mesh export must have written -- so the default path does both, and
        the textures are only encoded once.
        """
        export_dir = self.mesh_path_entry.get()
        save_path = self.layout_path_entry.get()
        if not export_dir:
            self.show_status("Error: Please select a valid mesh target directory first.", "error")
            return
        if not save_path:
            self.show_status("Error: Please select a valid target JSON save path first.", "error")
            return

        godot_project = self.get_godot_project_dir_if_valid()
        if godot_project == -1:
            return

        export_anims = self.export_anims_var.get()
        export_lods = self.export_lods_var.get()
        separate_tex = self.separate_textures_var.get()
        feature_options = self.get_export_feature_options()

        self.save_settings()
        self.show_status("Full export (meshes + layout) sent to Unreal...")
        _command_queue.put(("export_everything", [
            export_dir, save_path, export_anims, export_lods, separate_tex,
            godot_project, feature_options,
        ]))

    def run_layout_export(self):
        save_path = self.layout_path_entry.get()
        if not save_path:
            self.show_status("Error: Please select a valid target JSON save path first.", "error")
            return
            
        godot_project = self.get_godot_project_dir_if_valid()
        if godot_project == -1:
            return
            
        feature_options = self.get_export_feature_options()

        self.save_settings()
        if feature_options.get("write_tscn") and godot_project is None:
            self.show_status("Exporting... (.tscn skipped: enable 'Auto-transfer to Godot Project')", "warning")
        else:
            self.show_status("Export command sent to Unreal...")
        # Queue the export layout command to run on main thread
        _command_queue.put(("export_layout", [save_path, godot_project, feature_options]))

    def close(self):
        """Pushes close signal to main thread and shuts down GUI."""
        if not self.is_closed:
            self.is_closed = True
            try:
                self.save_settings()
            except Exception:
                pass
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
        
        _state_queue.put(state)
    except Exception:
        pass

    # 2. Process pending UI commands (Thread-safe write executed on main thread)
    try:
        while not _command_queue.empty():
            cmd, args = _command_queue.get_nowait()
            
            if cmd == "export_meshes":
                export_dir = args[0]
                export_anims = args[1] if len(args) > 1 else False
                export_lods = args[2] if len(args) > 2 else False
                separate_tex = args[3] if len(args) > 3 else True
                godot_project = args[4] if len(args) > 4 else None
                unreal.log("Unreal to Godot Exporter: Starting mesh export on main thread...")
                try:
                    exported, failed = export_static_meshes_to_gltf.export_selected_static_meshes(
                        export_dir=export_dir, export_animations=export_anims, export_lods=export_lods, separate_textures=separate_tex, show_dialogs=False, godot_project_dir=godot_project
                    )
                    if failed > 0:
                        _state_queue.put({"status": (f"Export completed: {exported} exported, {failed} failed.", "warning")})
                    else:
                        _state_queue.put({"status": (f"Successfully exported {exported} meshes to glTF!", "success")})
                except Exception as e:
                    _state_queue.put({"status": (f"Mesh Export Error: {str(e)}", "error")})
                    
            elif cmd == "export_all_meshes":
                export_dir = args[0]
                export_anims = args[1] if len(args) > 1 else False
                export_lods = args[2] if len(args) > 2 else False
                separate_tex = args[3] if len(args) > 3 else True
                godot_project = args[4] if len(args) > 4 else None
                unreal.log("Unreal to Godot Exporter: Starting batch level mesh export on main thread...")
                try:
                    exported, failed = export_static_meshes_to_gltf.export_all_level_meshes(
                        export_dir=export_dir, export_animations=export_anims, export_lods=export_lods, separate_textures=separate_tex, show_dialogs=False, godot_project_dir=godot_project
                    )
                    rep = _run_diagnostic(export_dir, godot_project)
                    verdict = _diagnostic_status(rep)
                    if failed > 0:
                        _state_queue.put({"status": (f"Batch export completed: {exported} exported, {failed} failed.", "warning")})
                    elif verdict:
                        _state_queue.put({"status": verdict})
                    else:
                        _state_queue.put({"status": (f"Successfully batch exported {exported} level meshes to glTF!", "success")})
                except Exception as e:
                    _state_queue.put({"status": (f"Batch Export Error: {str(e)}", "error")})
                    
            elif cmd == "export_everything":
                # Meshes and layout in one action. Keeping them as two separate
                # buttons meant a half-updated export was always one forgotten
                # click away, and the resulting mismatch surfaces much later as
                # a missing texture or a MISSING_ placeholder in Godot.
                export_dir = args[0]
                save_path = args[1]
                export_anims = args[2]
                export_lods = args[3]
                separate_tex = args[4]
                godot_project = args[5]
                feature_options = args[6]
                unreal.log("Unreal to Godot Exporter: Starting full export (meshes + layout)...")
                exported = failed = 0
                layout_ok = False
                try:
                    exported, failed = export_static_meshes_to_gltf.export_all_level_meshes(
                        export_dir=export_dir, export_animations=export_anims, export_lods=export_lods, separate_textures=separate_tex, show_dialogs=False, godot_project_dir=godot_project
                    )
                except Exception as e:
                    _state_queue.put({"status": (f"Full Export Error (meshes): {str(e)}", "error")})
                    continue

                try:
                    # The mesh export just wrote these same textures, so reuse
                    # them rather than re-encoding gigabytes of identical PNGs.
                    layout_ok = export_level_to_json.export_level_to_json(
                        save_path=save_path, show_dialogs=False, godot_project_dir=godot_project, options=feature_options,
                        skip_existing_textures=True,
                    )
                except Exception as e:
                    _state_queue.put({"status": (f"Full Export Error (layout): {str(e)}", "error")})
                    continue

                rep = _run_diagnostic(os.path.dirname(save_path), godot_project, strict=True)
                verdict = _diagnostic_status(rep)
                if failed > 0:
                    _state_queue.put({"status": (f"Full export: {exported} meshes exported, {failed} failed.", "warning")})
                elif not layout_ok:
                    _state_queue.put({"status": ("Meshes exported, but the layout export failed. Check output log.", "error")})
                elif verdict:
                    _state_queue.put({"status": verdict})
                else:
                    _state_queue.put({"status": (f"Full export complete: {exported} meshes + layout.", "success")})

            elif cmd == "export_layout":
                save_path = args[0]
                godot_project = args[1] if len(args) > 1 else None
                feature_options = args[2] if len(args) > 2 else None
                unreal.log("Unreal to Godot Exporter: Starting level layout export on main thread...")
                try:
                    success = export_level_to_json.export_level_to_json(
                        save_path=save_path, show_dialogs=False, godot_project_dir=godot_project, options=feature_options
                    )
                    if success:
                        # The layout lands next to models/, so audit the whole
                        # export: this is the point where both halves exist and
                        # can be cross-checked against each other.
                        rep = _run_diagnostic(os.path.dirname(save_path), godot_project)
                        verdict = _diagnostic_status(rep)
                        _state_queue.put({"status": verdict or ("Successfully exported level layout JSON!", "success")})
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
    except Exception as e:
        unreal.log_warning(f"Unreal to Godot Exporter: Error checking command queue: {str(e)}")


def show_window():
    """Launches the Tkinter GUI thread and registers the Slate tick queue handler."""
    global _tick_handle, _gui_thread
    
    # 0. Check if thread/GUI is already running
    if _gui_thread is not None and _gui_thread.is_alive():
        unreal.log_warning("Unreal to Godot Exporter: GUI window is already open/running.")
        return
        
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
            
        # Remove duplicate menu entry if already registered from previous import
        try:
            window_menu.remove_menu_entry("UnrealToGodotExporter")
        except Exception:
            pass
            
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

_deferred_menu_handle = None


def _try_register_menu_on_tick(_delta_seconds):
    """Slate post-tick pump: registers the menu as soon as the Window menu exists."""
    global _deferred_menu_handle
    try:
        if not unreal.ToolMenus.get().find_menu("LevelEditor.MainMenu.Window"):
            return  # not ready yet; try again next tick
        _do_register_menu()
    except Exception as e:
        unreal.log_warning(f"Unreal to Godot Exporter: deferred menu registration failed: {str(e)}")
    # Registered or permanently failed -- either way, stop pumping.
    if _deferred_menu_handle is not None:
        try:
            unreal.unregister_slate_post_tick_callback(_deferred_menu_handle)
        except Exception:
            pass
        _deferred_menu_handle = None


def register_menu_entry():
    """Registers the Window menu entry, deferring until Slate has built the menu.

    Uses register_slate_post_tick_callback: it is the only slate callback the
    Python API actually exposes. An earlier version called
    register_slate_post_init_callback, which does not exist -- so whenever the
    menu was not ready at import time, the AttributeError propagated out of
    init_unreal.py and the entire plugin failed to load.

    Commandlets (-run=pythonscript) have no Slate at all; there is no menu to
    register there, so a missing callback API is not an error.
    """
    global _deferred_menu_handle
    try:
        if unreal.ToolMenus.get().find_menu("LevelEditor.MainMenu.Window"):
            _do_register_menu()
            return
    except Exception as e:
        unreal.log_warning(f"Unreal to Godot Exporter: could not query menus: {str(e)}")
        return

    if not hasattr(unreal, "register_slate_post_tick_callback"):
        unreal.log("Unreal to Godot Exporter: no Slate available (commandlet?); skipping menu.")
        return

    unreal.log("Unreal to Godot Exporter: Slate menus not loaded yet. Deferring registration.")
    _deferred_menu_handle = unreal.register_slate_post_tick_callback(_try_register_menu_on_tick)

# Self-initialize on script import
register_menu_entry()
