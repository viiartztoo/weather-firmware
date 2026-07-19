# @v 1.1.0 | 2026-07-19 | Local file-change reboot check
import machine
import os

def file_change_check():
    """
    Efficiently check if main.py has been modified by comparing file metadata.
    Uses file size and modification time instead of calculating CRC.
    Much faster and uses less CPU/memory.
    """
    check_file = "main.py"
    state_file = "file_state.txt"

    try:

        try:
            stat = os.stat(check_file)
            current_size = stat[6]
            current_mtime = stat[8] if len(stat) > 8 else 0
        except OSError:
            print(f"[FileCheck] Cannot stat {check_file}")
            return False

        saved_size = None
        saved_mtime = None
        try:
            with open(state_file, "r") as f:
                lines = f.read().strip().split('\n')
                if len(lines) >= 1:
                    saved_size = int(lines[0])
                if len(lines) >= 2:
                    saved_mtime = int(lines[1]) if lines[1] else 0
        except (OSError, ValueError, IndexError):
            saved_size = None
            saved_mtime = None

        if saved_size is not None:
            size_changed = (current_size != saved_size)
            mtime_changed = (saved_mtime > 0 and current_mtime > 0 and current_mtime != saved_mtime)

            if size_changed or mtime_changed:
                print(f"[FileCheck] File changed detected!")
                print(f"  Size: {saved_size} -> {current_size}")
                if saved_mtime > 0 and current_mtime > 0:
                    print(f"  MTime: {saved_mtime} -> {current_mtime}")
                print("[FileCheck] Saving new state and rebooting...")

                with open(state_file, "w") as f:
                    f.write(f"{current_size}\n{current_mtime}\n")

                machine.reset()
                return False
            else:

                return True
        else:

            print(f"[FileCheck] Initializing - saving file state (size: {current_size})")
            with open(state_file, "w") as f:
                f.write(f"{current_size}\n{current_mtime}\n")
            return True

    except Exception as e:
        print(f"[FileCheck] Error: {e}")
        return False

def crc_check():
    """Legacy function name - now uses efficient file change detection"""
    return file_change_check()
