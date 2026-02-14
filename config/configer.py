#!/usr/bin/env python3
"""Simple interactive config editor with ASCII art header.

Run this script from the project root (or from the `config` folder).
It edits the project's `config.py` by showing top-level assignments and
allowing you to change values (safe-validated via `ast.literal_eval`).
"""
import ast
import os
import shutil
import sys
import time
from datetime import datetime
from colorama import Fore, Style, init
import types

# Initialize colorama; on Windows use convert to translate ANSI to Win32 calls
try:
    init(autoreset=True, convert=True)
except TypeError:
    init(autoreset=True)

# If stdout is not a TTY, disable color codes to avoid visible escape sequences
USE_COLOR = sys.stdout.isatty()
if not USE_COLOR:
    Fore = types.SimpleNamespace(
        RED="",
        YELLOW="",
        CYAN="",
        MAGENTA="",
        GREEN="",
        LIGHTMAGENTA_EX="",
        LIGHTRED_EX="",
    )
    Style = types.SimpleNamespace(
        BRIGHT="",
        RESET_ALL="",
    )

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.normpath(os.path.join(THIS_DIR, ".."))
CONFIG_PATH = os.path.join(THIS_DIR, "config.py")

ASCII = """

     __
  .-'  /
.'    /   /`.
|    /   /  |
|    \__/   |
`.         .'
  `.     .'
    | ][ |
  M | ][ |
  S | ][ |
  C | ][ |
    | ][ |
    | ][ |
    | ][ |
    | ][ |
    | ][ |
  .'  __  `.
  |  /  \  |
  |  \__/  |
  `.      .'
    `----'
    
    
"""

def read_config_lines(path: str) -> list:
    with open(path, "r", encoding="utf-8") as f:
        return f.readlines()


def parse_assignments(lines: list) -> dict:
    """Parse top-level NAME = VALUE assignments. Returns {name: (line_idx, raw_value)}."""
    assigns = {}
    for idx, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        # naive but practical: find NAME = <rest>
        if "=" in line and not line.lstrip().startswith("def ") and not line.lstrip().startswith("class "):
            parts = line.split("=", 1)
            name = parts[0].strip()
            # only simple NAME (no attribute access)
            if name.isidentifier():
                raw = parts[1].rstrip("\n")
                assigns[name] = (idx, raw)
    return assigns


def show_header():
    print(Style.BRIGHT + Fore.MAGENTA + ASCII + Style.RESET_ALL)
    print(Fore.CYAN + f"Editing: {CONFIG_PATH}\n")


def display_settings(assigns: dict):
    if not assigns:
        print("No simple assignments found in config.py.")
        return
    print(Fore.CYAN + f"Current settings:\n")
    for i, (k, (idx, raw)) in enumerate(sorted(assigns.items()), 1):
        try:
            val = ast.literal_eval(raw)
        except Exception:
            val = raw.strip()
        print(f"{i:2d}. {k} = {val}")


def backup(original_path: str) -> str:
    ts = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    bak = original_path + ".bak." + ts
    shutil.copyfile(original_path, bak)
    return bak


def write_updated(lines: list, assigns: dict, updates: dict, path: str):
    # apply updates to lines by line index; if a name is new, append
    for name, new_raw in updates.items():
        if name in assigns:
            idx, _ = assigns[name]
            lines[idx] = f"{name} = {new_raw}\n"
        else:
            lines.append(f"\n# Added by configer on {time.asctime()}\n")
            lines.append(f"{name} = {new_raw}\n")
    with open(path, "w", encoding="utf-8") as f:
        f.writelines(lines)


def prompt_edit(assigns: dict) -> dict:
    updates = {}
    keys = sorted(assigns.keys())
    while True:
        display_settings({k: assigns[k] for k in keys})
        print(Fore.GREEN + f"\nOptions: [number] edit, 'a' add new, 's' save & exit, 'q' quit without saving")
        choice = input(Fore.YELLOW + "> " + Style.RESET_ALL).strip()
        if choice.lower() == "q":
            return {}
        if choice.lower() == "s":
            return updates
        if choice.lower() == "a":
            name = input("New name: ").strip()
            if not name.isidentifier():
                print(Fore.RED + "Invalid identifier.")
                continue
            val_in = input("New value (Python literal, e.g. True, 42, 'text', [1,2]): ")
            try:
                ast.literal_eval(val_in)
            except Exception:
                ok = input(Fore.YELLOW + "Value not a valid literal. Save as string? (y/N): " + Style.RESET_ALL)
                if ok.lower() != "y":
                    continue
                val_in = repr(val_in)
            updates[name] = val_in
            keys.append(name)
            continue
        if not choice.isdigit():
            print(Fore.RED + "Invalid option.")
            continue
        idx = int(choice) - 1
        if idx < 0 or idx >= len(keys):
            print(Fore.RED + "Nummber out of range.")
            continue
        name = keys[idx]
        raw = assigns[name][1]
        try:
            cur = ast.literal_eval(raw)
        except Exception:
            cur = raw.strip()
        print(f"Current {name} = {cur}")
        new_val = input("New value (Python literal): ").strip()
        if new_val == "":
            print("Skipped.")
            continue
        try:
            ast.literal_eval(new_val)
        except Exception:
            ok = input(Fore.YELLOW + "Value not a valid literal. Save as string? (y/N): " + Style.RESET_ALL)
            if ok.lower() != "y":
                continue
            new_val = repr(new_val)
        updates[name] = new_val


def main():
    if not os.path.exists(CONFIG_PATH):
        print(Fore.RED + f"config.py not found at {CONFIG_PATH}")
        sys.exit(1)
    show_header()
    lines = read_config_lines(CONFIG_PATH)
    assigns = parse_assignments(lines)
    updates = prompt_edit(assigns)
    if not updates:
        print(Fore.CYAN + "No changes made. Exiting.")
        return
    print(Fore.YELLOW + "Backing up current config...")
    bak = backup(CONFIG_PATH)
    try:
        write_updated(lines, assigns, updates, CONFIG_PATH)
        print(Fore.GREEN + f"Saved changes to {CONFIG_PATH} (backup: {bak})")
    except Exception as e:
        print(Fore.RED + f"Failed to write config: {e}")
        print(Fore.YELLOW + f"Restoring backup {bak}...")
        shutil.copyfile(bak, CONFIG_PATH)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nAborted.")
