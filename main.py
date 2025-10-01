# ──────────────────────────── IMPORTS ────────────────────────────

import sys
import os
import time
import threading
import ctypes
import msvcrt 
from ctypes import windll, byref, Structure, wintypes
import math

try:
    import dearpygui.dearpygui as dpg
    from pymem import Pymem
    from pymem.process import list_processes
    from pymem.exception import ProcessError
    from psutil import pid_exists
    import json
except ImportError as e:
    print(f"Missing dependency: {e}")
    input("Press Enter to exit...")
    sys.exit(1)

# ──────────────────────────── GLOBAL VARIABLES ────────────────────────────

pm = Pymem()
PID = -1
baseAddr = None
injected = False
dataModel = 0
workspace = 0
players = 0
localPlayer = 0

jujutsu_enabled = False
resize_parts_enabled = False
desync_enabled = False

# Desync variables
real_position = None
fake_position = None
desync_visualization_window = None

# Hitbox expander variables
hitbox_visualization_window = None
expanded_players = {}

# Memory addresses
WorldStepMax = 0x5FFB5A4
WorldStepsOffsetAdjustRate = 0x5FFB5A8

# Roblox Offsets
class Offsets:
    Name = 0x88
    Children = 0x68
    Parent = 0x58
    PartSize = 0x23C
    Position = 0x154
    Velocity = 0x160
    CFrame = 0x130
    Primitive = 0x178
    Health = 0x19C
    MaxHealth = 0x1BC
    WalkSpeed = 0x1DC
    Team = 0x258
    ModelInstance = 0x348
    LocalPlayer = 0x128
    Workspace = 0x180
    Camera = 0x450
    CameraSubject = 0xF0
    WalkSpeedCheck = 0x3B8
    FakeDataModelPointer = 0x7168648
    FakeDataModelToDataModel = 0x1C0
    VisualEnginePointer = 0x6EC49B0
    VisualEngineToDataModel1 = 0x700
    VisualEngineToDataModel2 = 0x1C0
    viewmatrix = 0x4B0

# Windows file dialog structures
class OPENFILENAME(Structure):
    _fields_ = [
        ('lStructSize', wintypes.DWORD),
        ('hwndOwner', wintypes.HWND),
        ('hInstance', wintypes.HINSTANCE),
        ('lpstrFilter', wintypes.LPCWSTR),
        ('lpstrCustomFilter', wintypes.LPWSTR),
        ('nMaxCustFilter', wintypes.DWORD),
        ('nFilterIndex', wintypes.DWORD),
        ('lpstrFile', wintypes.LPWSTR),
        ('nMaxFile', wintypes.DWORD),
        ('lpstrFileTitle', wintypes.LPWSTR),
        ('nMaxFileTitle', wintypes.DWORD),
        ('lpstrInitialDir', wintypes.LPCWSTR),
        ('lpstrTitle', wintypes.LPCWSTR),
        ('Flags', wintypes.DWORD),
        ('nFileOffset', wintypes.WORD),
        ('nFileExtension', wintypes.WORD),
        ('lpstrDefExt', wintypes.LPCWSTR),
        ('lCustData', wintypes.LPARAM),
        ('lpfnHook', wintypes.LPVOID),
        ('lpTemplateName', wintypes.LPCWSTR),
        ('pvReserved', wintypes.LPVOID),
        ('dwReserved', wintypes.DWORD),
        ('FlagsEx', wintypes.DWORD)
    ]

# Vector3 structure for Roblox
class Vector3:
    def __init__(self, x=0, y=0, z=0):
        self.x = x
        self.y = y
        self.z = z

    def __str__(self):
        return f"({self.x:.1f}, {self.y:.1f}, {self.z:.1f})"

    def copy(self):
        return Vector3(self.x, self.y, self.z)

    def distance_to(self, other):
        return math.sqrt((self.x - other.x)**2 + (self.y - other.y)**2 + (self.z - other.z)**2)

# ──────────────────────────── CORE FUNCTIONS ────────────────────────────

def simple_get_processes():
    return [{"Name": i.szExeFile.decode(), "ProcessId": i.th32ProcessID} for i in list_processes()]

def yield_for_program(program_name):
    global PID, baseAddr, pm
    for proc in simple_get_processes():
        if proc["Name"] == program_name:
            pm.open_process_from_id(proc["ProcessId"])
            PID = proc["ProcessId"]
            
            for module in pm.list_modules():
                if module.name == "RobloxPlayerBeta.exe":
                    baseAddr = module.lpBaseOfDll
                    print(f"Found Roblox base: 0x{baseAddr:X}")
                    return True
    return False

def is_process_dead():
    return not pid_exists(PID)

def background_process_monitor():
    global baseAddr
    while True:
        if is_process_dead():
            while not yield_for_program("RobloxPlayerBeta.exe"):
                time.sleep(0.5)
            baseAddr = get_base_addr()
        time.sleep(0.1)

threading.Thread(target=background_process_monitor, daemon=True).start()

def get_base_addr():
    return baseAddr

def DRP(address):
    """Dereference Pointer"""
    if isinstance(address, str):
        address = int(address, 16)
    return int.from_bytes(pm.read_bytes(address, 8), "little")

def GetName(instance):
    """Get name of Roblox instance"""
    try:
        name_addr = DRP(instance + Offsets.Name)
        string_length = pm.read_int(name_addr + 0x10)
        if string_length > 15:
            ptr = DRP(name_addr)
            return pm.read_string(ptr, string_length)
        return pm.read_string(name_addr, string_length)
    except:
        return "Unknown"

def GetChildren(instance):
    """Get children of Roblox instance"""
    if not instance:
        return []
    children = []
    try:
        start = DRP(instance + Offsets.Children)
        if start == 0:
            return []
        end = DRP(start + 8)
        current = DRP(start)
        for _ in range(100):
            if current == end:
                break
            children.append(pm.read_longlong(current))
            current += 0x10
    except:
        pass
    return children

def FindFirstChild(instance, child_name):
    """Find first child with specific name"""
    if not instance:
        return 0
    for child in GetChildren(instance):
        try:
            if GetName(child) == child_name:
                return child
        except:
            pass
    return 0

def GetPlayersService():
    """Get Players service directly from DataModel"""
    global dataModel
    if not dataModel:
        return 0
    
    try:
        # Try to find Players service by iterating children
        for child in GetChildren(dataModel):
            try:
                name = GetName(child)
                if name == "Players":
                    print(f"Found Players service: 0x{child:X}")
                    return child
            except:
                continue
    except Exception as e:
        print(f"Error finding Players service: {e}")
    
    return 0

def GetLocalPlayer():
    """Get LocalPlayer directly from Players service"""
    global players
    if not players:
        return 0
    
    try:
        local_player_addr = pm.read_longlong(players + Offsets.LocalPlayer)
        if local_player_addr:
            print(f"Found LocalPlayer: 0x{local_player_addr:X}")
            return local_player_addr
    except Exception as e:
        print(f"Error getting LocalPlayer: {e}")
    
    return 0

def GetCharacter():
    """Get character from LocalPlayer"""
    global localPlayer
    if not localPlayer:
        return 0
    
    try:
        character_addr = pm.read_longlong(localPlayer + Offsets.ModelInstance)
        if character_addr:
            print(f"Found Character: 0x{character_addr:X}")
            return character_addr
    except Exception as e:
        print(f"Error getting character: {e}")
    
    return 0

def init():
    global injected, dataModel, workspace, players, localPlayer
    
    try:
        if yield_for_program("RobloxPlayerBeta.exe"):
            # Get DataModel
            fake_datamodel = pm.read_longlong(baseAddr + Offsets.FakeDataModelPointer)
            dataModel = pm.read_longlong(fake_datamodel + Offsets.FakeDataModelToDataModel)
            
            # Get Workspace
            workspace = pm.read_longlong(dataModel + Offsets.Workspace)
            
            # Get Players service using direct method
            players = GetPlayersService()
            
            # Get LocalPlayer
            localPlayer = GetLocalPlayer()
            
            print('Roblox injected successfully!')
            print(f'DataModel: 0x{dataModel:X}')
            print(f'Workspace: 0x{workspace:X}')
            print(f'Players: 0x{players:X}')
            print(f'LocalPlayer: 0x{localPlayer:X}')
            
            injected = True
            
            # Create visualization windows
            create_desync_visualization()
            create_hitbox_visualization()
            
        else:
            print('Roblox not found! Open Roblox first.')
    except Exception as e:
        print(f'Error injecting into Roblox: {e}')

# ──────────────────────────── ROBLOX MEMORY FUNCTIONS ────────────────────────────

def read_vector3(address):
    """Read Vector3 from memory"""
    try:
        x = pm.read_float(address)
        y = pm.read_float(address + 4)
        z = pm.read_float(address + 8)
        return Vector3(x, y, z)
    except:
        return None

def write_vector3(address, vector):
    """Write Vector3 to memory"""
    try:
        pm.write_float(address, vector.x)
        pm.write_float(address + 4, vector.y)
        pm.write_float(address + 8, vector.z)
        return True
    except:
        return False

# ──────────────────────────── HITBOX EXPANDER SYSTEM ────────────────────────────

def create_hitbox_visualization():
    """Create hitbox expander visualization window"""
    global hitbox_visualization_window
    
    if hitbox_visualization_window:
        dpg.delete_item(hitbox_visualization_window)
    
    hitbox_visualization_window = dpg.add_window(
        label="Hitbox Expander",
        width=400,
        height=250,
        pos=[500, 50],
        show=False,
        tag="hitbox_visualization"
    )
    
    with dpg.group(parent="hitbox_visualization"):
        dpg.add_text("Hitbox Expander: INACTIVE", tag="hitbox_status")
        dpg.add_separator()
        dpg.add_text("Target: ALL PLAYERS (except you)", color=(255, 150, 50))
        dpg.add_text("Hitbox Size: 30x30x30", color=(150, 255, 150))
        dpg.add_text("Your hitbox: NORMAL", color=(100, 200, 255))
        dpg.add_separator()
        dpg.add_text("🎯 Other players have LARGE hitboxes", color=(255, 255, 100))
        dpg.add_text("🛡️ Your hitbox remains normal", color=(100, 255, 255))

def update_hitbox_visualization():
    """Update hitbox visualization window"""
    if not dpg.does_item_exist("hitbox_visualization"):
        return
    
    if resize_parts_enabled:
        dpg.configure_item("hitbox_status", default_value=f"Hitbox Expander: ACTIVE - {len(expanded_players)} players expanded")
    else:
        dpg.configure_item("hitbox_status", default_value="Hitbox Expander: INACTIVE")

def get_all_players():
    """Get all players on server except local player"""
    players_list = []
    
    try:
        if not players:
            return players_list
            
        # Get all children of Players service
        for player_addr in GetChildren(players):
            if player_addr and player_addr != localPlayer:  # Exclude local player
                players_list.append(player_addr)
                
        return players_list
    except Exception as e:
        return []

def get_player_character(player_addr):
    """Get player's character"""
    try:
        character = pm.read_longlong(player_addr + Offsets.ModelInstance)
        return character if character else 0
    except:
        return 0

def get_player_parts(character_addr):
    """Get all parts of player's character"""
    parts = []
    if not character_addr:
        return parts
    
    try:
        part_names = ["Head", "Torso", "LeftArm", "RightArm", "LeftLeg", "RightLeg", "HumanoidRootPart"]
        
        for part_name in part_names:
            part = FindFirstChild(character_addr, part_name)
            if part:
                primitive = pm.read_longlong(part + Offsets.Primitive)
                if primitive:
                    parts.append({
                        'name': part_name,
                        'address': part,
                        'primitive': primitive,
                        'size_offset': Offsets.PartSize
                    })
                    
    except Exception as e:
        pass
    
    return parts

def expand_player_hitboxes(player_addr):
    """Expand player's hitboxes to 30x30x30"""
    try:
        character = get_player_character(player_addr)
        if not character:
            return False
        
        parts = get_player_parts(character)
        if not parts:
            return False
        
        expanded_count = 0
        for part in parts:
            try:
                size_addr = part['primitive'] + part['size_offset']
                current_size = read_vector3(size_addr)
                
                # Expand only if size is smaller than target
                if current_size and (current_size.x < 30.0 or current_size.y < 30.0 or current_size.z < 30.0):
                    new_size = Vector3(300.0, 300.0, 300.0)
                    if write_vector3(size_addr, new_size):
                        expanded_count += 1
                        
            except:
                continue
        
        return expanded_count > 0
        
    except Exception as e:
        return False

def restore_player_hitboxes(player_addr):
    """Restore player's original hitbox sizes"""
    try:
        character = get_player_character(player_addr)
        if not character:
            return False
        
        parts = get_player_parts(character)
        if not parts:
            return False
        
        # Restore standard Roblox sizes
        standard_sizes = {
            "Head": Vector3(2.0, 1.0, 1.0),
            "Torso": Vector3(2.0, 2.0, 1.0),
            "LeftArm": Vector3(1.0, 2.0, 1.0),
            "RightArm": Vector3(1.0, 2.0, 1.0),
            "LeftLeg": Vector3(1.0, 2.0, 1.0),
            "RightLeg": Vector3(1.0, 2.0, 1.0),
            "HumanoidRootPart": Vector3(2.0, 2.0, 1.0)
        }
        
        restored_count = 0
        for part in parts:
            try:
                size_addr = part['primitive'] + part['size_offset']
                standard_size = standard_sizes.get(part['name'], Vector3(2.0, 2.0, 1.0))
                if write_vector3(size_addr, standard_size):
                    restored_count += 1
            except:
                continue
        
        return restored_count > 0
        
    except Exception as e:
        return False

def hitbox_expander_loop():
    """Main hitbox expander loop"""
    global expanded_players
    last_scan_time = 0
    
    while True:
        if resize_parts_enabled and injected and players:
            try:
                current_time = time.time()
                
                # Scan players every 3 seconds
                if current_time - last_scan_time > 3.0:
                    all_players = get_all_players()
                    current_expanded = set()
                    
                    # Expand hitboxes of all players
                    for player_addr in all_players:
                        if expand_player_hitboxes(player_addr):
                            current_expanded.add(player_addr)
                    
                    # Update expanded players dictionary
                    expanded_players = {addr: True for addr in current_expanded}
                    last_scan_time = current_time
                    
                    if current_expanded:
                        print(f"🎯 Expanded hitboxes for {len(current_expanded)} players")
                
                update_hitbox_visualization()
                
            except Exception as e:
                pass
        else:
            # If disabled, restore all hitboxes
            if expanded_players:
                print("🔄 Restoring original hitbox sizes...")
                for player_addr in list(expanded_players.keys()):
                    restore_player_hitboxes(player_addr)
                expanded_players.clear()
                update_hitbox_visualization()
        
        time.sleep(0.5)

def resize_parts_callback(sender, app_data):
    global resize_parts_enabled
    resize_parts_enabled = app_data
    
    if resize_parts_enabled:
        if not injected or not players:
            print("ERROR: Not injected! Click Inject first.")
            dpg.set_value("resize_parts_checkbox", False)
            return
        
        print("═" * 50)
        print("🎯 ACTIVATING HITBOX EXPANDER")
        print("═" * 50)
        print("TARGET: ALL PLAYERS (except you)")
        print("EFFECT: Other players have 30x30x30 hitboxes")
        print("YOUR HITBOX: Remains normal size")
        print("ADVANTAGE: Easy to hit enemies")
        
        dpg.configure_item("hitbox_visualization", show=True)
        
    else:
        print("🔒 HITBOX EXPANDER DISABLED")
        print("🔄 Restoring all players to normal hitboxes...")
        
        # Restore hitboxes of all players
        all_players = get_all_players()
        for player_addr in all_players:
            restore_player_hitboxes(player_addr)
        
        expanded_players.clear()
        dpg.configure_item("hitbox_visualization", show=False)

# ──────────────────────────── DESYNC SYSTEM ────────────────────────────

def create_desync_visualization():
    """Create desync visualization window"""
    global desync_visualization_window
    
    if desync_visualization_window:
        dpg.delete_item(desync_visualization_window)
    
    desync_visualization_window = dpg.add_window(
        label="Desync Visualization",
        width=400,
        height=300,
        pos=[50, 50],
        show=False,
        tag="desync_visualization"
    )
    
    with dpg.group(parent="desync_visualization"):
        dpg.add_text("Desync Status: INACTIVE", tag="desync_status")
        dpg.add_separator()
        dpg.add_text("Server Position:", tag="real_pos_label")
        dpg.add_text("X: 0.0, Y: 0.0, Z: 0.0", tag="real_position")
        dpg.add_text("Client Position:", tag="fake_pos_label")
        dpg.add_text("X: 0.0, Y: 0.0, Z: 0.0", tag="fake_position")
        dpg.add_text("Desync Distance:", tag="distance_label")
        dpg.add_text("0.0 units", tag="distance_value")
        dpg.add_separator()
        dpg.add_text("Other players see you at Server Position", color=(255, 100, 100))
        dpg.add_text("You see yourself at Client Position", color=(100, 255, 100))

def update_desync_visualization():
    """Update desync visualization window"""
    if not dpg.does_item_exist("desync_visualization"):
        return
    
    if desync_enabled and real_position and fake_position:
        dpg.configure_item("desync_status", default_value="Desync Status: ACTIVE")
        dpg.configure_item("real_position", default_value=f"X: {real_position.x:.1f}, Y: {real_position.y:.1f}, Z: {real_position.z:.1f}")
        dpg.configure_item("fake_position", default_value=f"X: {fake_position.x:.1f}, Y: {fake_position.y:.1f}, Z: {fake_position.z:.1f}")
        
        distance = real_position.distance_to(fake_position)
        dpg.configure_item("distance_value", default_value=f"{distance:.1f} units")
        
        # Color coding based on distance
        if distance > 50:
            dpg.configure_item("distance_value", color=(255, 50, 50))
        elif distance > 20:
            dpg.configure_item("distance_value", color=(255, 150, 50))
        else:
            dpg.configure_item("distance_value", color=(255, 255, 50))
    else:
        dpg.configure_item("desync_status", default_value="Desync Status: INACTIVE")
        dpg.configure_item("real_position", default_value="X: 0.0, Y: 0.0, Z: 0.0")
        dpg.configure_item("fake_position", default_value="X: 0.0, Y: 0.0, Z: 0.0")
        dpg.configure_item("distance_value", default_value="0.0 units")

def get_character_root_part(character_addr):
    """Get character's root part for position manipulation"""
    if not character_addr:
        return None
    
    try:
        # First try HumanoidRootPart
        root_part = FindFirstChild(character_addr, "HumanoidRootPart")
        if root_part:
            primitive = pm.read_longlong(root_part + Offsets.Primitive)
            if primitive:
                return primitive
        
        # If no HumanoidRootPart, try Torso
        torso_part = FindFirstChild(character_addr, "Torso")
        if torso_part:
            primitive = pm.read_longlong(torso_part + Offsets.Primitive)
            if primitive:
                return primitive
                
        return None
    except Exception as e:
        return None

def desync_callback(sender, app_data):
    global desync_enabled, real_position, fake_position
    desync_enabled = app_data
    
    if desync_enabled:
        if not injected or not localPlayer:
            print("ERROR: Not injected! Click Inject first.")
            dpg.set_value("desync_checkbox", False)
            return
        
        # Get character
        character = GetCharacter()
        if not character:
            print("ERROR: No character found! Make sure you have spawned in the game.")
            dpg.set_value("desync_checkbox", False)
            return
        
        # Get root part
        root_part = get_character_root_part(character)
        if not root_part:
            print("ERROR: No root part found in character!")
            dpg.set_value("desync_checkbox", False)
            return
        
        # Save real position (where other players see you)
        real_position = read_vector3(root_part + Offsets.Position)
        if not real_position:
            print("ERROR: Could not read character position!")
            dpg.set_value("desync_checkbox", False)
            return
        
        fake_position = real_position.copy()
        
        print(f"Desync ENABLED - Real: {real_position}, Fake: {fake_position}")
        
        # Show visualization
        dpg.configure_item("desync_visualization", show=True)
        
    else:
        # Restore real position when disabling desync
        if real_position and localPlayer:
            character = GetCharacter()
            if character:
                root_part = get_character_root_part(character)
                if root_part:
                    write_vector3(root_part + Offsets.Position, real_position)
        
        print("Desync DISABLED")
        real_position = None
        fake_position = None
        
        # Hide visualization
        dpg.configure_item("desync_visualization", show=False)

def desync_loop():
    """Main desync loop - separates local and server positions"""
    global real_position, fake_position
    
    while True:
        if desync_enabled and injected and localPlayer and real_position:
            try:
                character = GetCharacter()
                if not character:
                    continue
                    
                root_part = get_character_root_part(character)
                if not root_part:
                    continue
                
                # Read current fake position (where player is moving locally)
                current_fake = read_vector3(root_part + Offsets.Position)
                
                if current_fake:
                    # Update fake position
                    fake_position = current_fake.copy()
                    
                    # Keep writing real position to server (player appears stuck for others)
                    write_vector3(root_part + Offsets.Position, real_position)
                    
                    # Update visualization
                    update_desync_visualization()
            
            except Exception as e:
                pass
        
        time.sleep(0.05)

# ──────────────────────────── JUJUTSU FUNCTIONS ────────────────────────────

def jujutsu_callback(sender, app_data):
    global jujutsu_enabled
    jujutsu_enabled = app_data
    if jujutsu_enabled and injected and baseAddr:
        try:
            pm.write_int(baseAddr + WorldStepMax, -99999)
            pm.write_int(baseAddr + WorldStepsOffsetAdjustRate, -99999)
            print("Jujutsu values applied to Roblox!")
        except Exception as e:
            print(f"Error applying jujutsu values: {e}")

def jujutsu_loop():
    while True:
        if jujutsu_enabled and injected and baseAddr:
            try:
                pm.write_int(baseAddr + WorldStepMax, -99999)
                pm.write_int(baseAddr + WorldStepsOffsetAdjustRate, -99999)
            except Exception as e:
                pass
        time.sleep(0.1)

# ──────────────────────────── CONFIG FUNCTIONS ────────────────────────────

def get_configs_directory():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    configs_dir = os.path.join(script_dir, "configs")
    
    if not os.path.exists(configs_dir):
        os.makedirs(configs_dir)
    
    return configs_dir

def windows_save_file_dialog():
    try:
        configs_dir = get_configs_directory()
        
        filename_buffer = ctypes.create_unicode_buffer(260)
        initial_path = os.path.join(configs_dir, "config.json")
        filename_buffer.value = initial_path
        
        ofn = OPENFILENAME()
        ofn.lStructSize = ctypes.sizeof(OPENFILENAME)
        ofn.hwndOwner = None
        ofn.lpstrFilter = "JSON Files\0*.json\0All Files\0*.*\0"
        ofn.lpstrFile = ctypes.cast(filename_buffer, wintypes.LPWSTR)
        ofn.nMaxFile = 260
        ofn.lpstrInitialDir = configs_dir
        ofn.lpstrTitle = "Save Config"
        ofn.lpstrDefExt = "json"
        ofn.Flags = 0x00000002 | 0x00000004
        
        if windll.comdlg32.GetSaveFileNameW(byref(ofn)):
            selected_path = filename_buffer.value
            if not selected_path.startswith(configs_dir):
                filename = os.path.basename(selected_path)
                selected_path = os.path.join(configs_dir, filename)
            return selected_path
        return None
    except Exception as e:
        print(f"Error in save dialog: {e}")
        return None

def windows_open_file_dialog():
    try:
        configs_dir = get_configs_directory()
        
        filename_buffer = ctypes.create_unicode_buffer(260)
        
        ofn = OPENFILENAME()
        ofn.lStructSize = ctypes.sizeof(OPENFILENAME)
        ofn.hwndOwner = None
        ofn.lpstrFilter = "JSON Files\0*.json\0All Files\0*.*\0"
        ofn.lpstrFile = ctypes.cast(filename_buffer, wintypes.LPWSTR)
        ofn.nMaxFile = 260
        ofn.lpstrInitialDir = configs_dir
        ofn.lpstrTitle = "Load Config"
        ofn.Flags = 0x00001000 | 0x00000004
        
        if windll.comdlg32.GetOpenFileNameW(byref(ofn)):
            return filename_buffer.value
        return None
    except Exception as e:
        print(f"Error in open dialog: {e}")
        return None

def save_config_callback():
    try:
        file_path = windows_save_file_dialog()
        
        if file_path:
            config_data = {
                "jujutsu": {
                    "enabled": jujutsu_enabled
                },
                "resize_parts": {
                    "enabled": resize_parts_enabled
                },
                "desync": {
                    "enabled": desync_enabled
                }
            }
            
            with open(file_path, 'w') as f:
                json.dump(config_data, f, indent=4)
            
            print(f"Config saved to: {file_path}")
            
    except Exception as e:
        print(f"Error saving config: {e}")

def load_config_callback():
    try:
        file_path = windows_open_file_dialog()
        
        if file_path:
            with open(file_path, 'r') as f:
                config_data = json.load(f)
            
            if "jujutsu" in config_data:
                jujutsu_config = config_data["jujutsu"]
                global jujutsu_enabled
                if "enabled" in jujutsu_config:
                    jujutsu_enabled = jujutsu_config["enabled"]
                    dpg.set_value("jujutsu_checkbox", jujutsu_enabled)
            
            if "resize_parts" in config_data:
                resize_config = config_data["resize_parts"]
                global resize_parts_enabled
                if "enabled" in resize_config:
                    resize_parts_enabled = resize_config["enabled"]
                    dpg.set_value("resize_parts_checkbox", resize_parts_enabled)
            
            if "desync" in config_data:
                desync_config = config_data["desync"]
                global desync_enabled
                if "enabled" in desync_config:
                    desync_enabled = desync_config["enabled"]
                    dpg.set_value("desync_checkbox", desync_enabled)
            
            print(f"Config loaded from: {file_path}")
            
    except Exception as e:
        print(f"Error loading config: {e}")

# ──────────────────────────── LICENSE VERIFICATION ────────────────────────────

def check_license():
    print("[logs] License key: ", end="", flush=True)
   
    while True:
        try:
            license_key = ""
           
            while True:
                char = msvcrt.getch().decode('utf-8')
               
                if char == '\r':
                    print()
                    break
                elif char == '\b':
                    if license_key:
                        license_key = license_key[:-1]
                        print('\b \b', end="", flush=True)
                elif char.isprintable():
                    license_key += char
                    print(char, end="", flush=True)
           
            if license_key == "kankan":
                os.system('cls' if os.name == 'nt' else 'clear')
                print("[warning] updater: checking if directory exists", end="", flush=True)
                time.sleep(2)
                try:
                    ctypes.windll.user32.ShowWindow(ctypes.windll.kernel32.GetConsoleWindow(), 6)
                except Exception:
                    pass
                return True
            else:
                print("[ERROR] Invalid license key!")
                print("[logs] License key: ", end="", flush=True)
        except (EOFError, KeyboardInterrupt):
            print("\n[INFO] License verification cancelled")
            return False

# ──────────────────────────── GUI CREATION ────────────────────────────

if __name__ == "__main__":
    if not check_license():
        sys.exit(0)
    
    threading.Thread(target=background_process_monitor, daemon=True).start()
    dpg.create_context()

    with dpg.window(label="Roblox JJK Cheat", tag="Primary Window", width=500, height=450):
        dpg.add_text("Roblox Jujutsu Kaisen Cheat", color=(255, 255, 0))
        dpg.add_text("1. Open Roblox JJK game", color=(200, 200, 200))
        dpg.add_text("2. Click Inject", color=(200, 200, 200))
        dpg.add_text("3. Enable features", color=(200, 200, 200))
        
        dpg.add_spacer(height=10)
        dpg.add_button(label="Inject Roblox", callback=init, tag="inject_button")
        dpg.add_spacer(height=10)
        dpg.add_separator()
        dpg.add_spacer(height=10)
        
        # Jujutsu Section
        dpg.add_checkbox(label="Enable Jujutsu", default_value=jujutsu_enabled, 
                        callback=jujutsu_callback, tag="jujutsu_checkbox")
        dpg.add_text("Writes -99999 to WorldStep addresses", color=(150, 150, 150))
        
        dpg.add_spacer(height=10)
        
        # Hitbox Expander Section
        dpg.add_checkbox(label="Hitbox Expander (ALL PLAYERS)", default_value=resize_parts_enabled,
                        callback=resize_parts_callback, tag="resize_parts_checkbox")
        dpg.add_text("Makes OTHER players have large hitboxes", color=(150, 150, 150))
        dpg.add_text("Your hitbox remains normal - Easy PVP", color=(120, 120, 120))
        
        dpg.add_spacer(height=10)
        
        # Desync Section
        dpg.add_checkbox(label="Enable Desync", default_value=desync_enabled,
                        callback=desync_callback, tag="desync_checkbox")
        dpg.add_text("Disconnects local position from server", color=(150, 150, 150))
        dpg.add_text("Others see you in one place, you can move freely", color=(120, 120, 120))
        
        dpg.add_spacer(height=10)
        dpg.add_separator()
        dpg.add_spacer(height=10)
        
        with dpg.group(horizontal=True):
            dpg.add_button(label="Save Config", callback=save_config_callback, width=120)
            dpg.add_button(label="Load Config", callback=load_config_callback, width=120)

    # Start all threads
    threading.Thread(target=jujutsu_loop, daemon=True).start()
    threading.Thread(target=hitbox_expander_loop, daemon=True).start()
    threading.Thread(target=desync_loop, daemon=True).start()

    dpg.create_viewport(title="Roblox JJK Cheat", width=500, height=450)
    dpg.setup_dearpygui()
    dpg.set_primary_window("Primary Window", True)
    dpg.show_viewport()
    dpg.start_dearpygui()
    dpg.destroy_context()