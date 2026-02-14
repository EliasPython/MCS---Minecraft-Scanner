import os
import sys
import subprocess
import json
from pathlib import Path

ROOT = Path(__file__).parent
REPO_URL = "https://github.com/kgurchiek/Minecraft-Whitelist-Scanner.git"
REPO_DIR = ROOT / "Minecraft-Whitelist-Scanner"
RESULTS = "results.json"
SENT_FILE = Path(__file__).resolve().parents[1] / "ressources" / "sent_servers.txt"


def clone_repo():
    if REPO_DIR.exists():
        print("Repo already present:", REPO_DIR)
        return True
    print("Cloning repo... this requires git to be installed")
    try:
        subprocess.check_call(["git", "clone", REPO_URL, str(REPO_DIR)])
        return True
    except Exception as e:
        print("Failed to clone:", e)
        return False


def find_entrypoint():
    # look for likely python entrypoints
    candidates = ["whitelist_scanner.py", "scanner.py", "main.py", "run.py"]
    for c in candidates:
        p = REPO_DIR / c
        if p.exists():
            return p
    # fallback: any top-level .py
    for p in REPO_DIR.glob("*.py"):
        return p
    return None


def run_entrypoint(entry, extra_args=None, capture_results=True):
    cmd = [sys.executable, str(entry)]
    if extra_args:
        cmd += extra_args

    print("Running:", " ".join(cmd))
    if capture_results:
        with subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True) as proc:
            out_lines = []
            for line in proc.stdout:
                print(line, end="")
                out_lines.append(line)
            proc.wait()
        # write results (best-effort)
        try:
            RESULTS.write_text(json.dumps({"output": out_lines}, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass
        # try to append IP-like lines to sent file
        try:
            SENT_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(SENT_FILE, "a", encoding="utf-8") as f:
                for ln in out_lines:
                    s = ln.strip()
                    if s.count('.') == 3 and ':' in s or s.count('.') == 3:
                        f.write(s + "\n")
        except Exception:
            pass
    else:
        subprocess.check_call(cmd)


def main():
    auto = "--run" in sys.argv
    if not clone_repo():
        print("Could not obtain repository. Clone it manually:")
        print(REPO_URL)
        return

    entry = find_entrypoint()
    if not entry:
        print("No python entrypoint found in repository. Open the folder and inspect files:")
        print(REPO_DIR)
        return

    print("Found entrypoint:", entry)
    print("To run the whitelist scanner interactively, call:")
    print(f"  python {entry}")
    print("Or run this script with --run to execute and capture output to whitelist/results.json")

    if auto:
        run_entrypoint(entry)


if __name__ == '__main__':
    main()
