import threading
import asyncio
import tkinter as tk
from tkinter import scrolledtext, messagebox

import scanner_v2

scanner_thread = None
stats_thread = None


def start_scanner(btn):
    global scanner_thread, stats_thread
    if scanner_thread is not None and scanner_thread.is_alive():
        return
    def run():
        try:
            asyncio.run(scanner_v2.main())
        except Exception as e:
            print(f"Scanner error: {e}")
    scanner_thread = threading.Thread(target=run, daemon=True)
    scanner_thread.start()

    # Start stats window if available
    if scanner_v2.tk is not None and (stats_thread is None or not stats_thread.is_alive()):
        stats_thread = threading.Thread(target=scanner_v2.run_stats_window, daemon=True)
        stats_thread.start()

    btn.config(state=tk.DISABLED, text="Scanner running")


def check_online(entry, output):
    ip = entry.get().strip()
    if not ip:
        messagebox.showwarning("Input required", "Please enter an IP or host:port")
        return

    output.delete(1.0, tk.END)
    output.insert(tk.END, f"Checking {ip}...\n")

    def worker():
        try:
            # allow user to specify port with colon
            host = ip
            port = None
            if ":" in ip:
                host, port = ip.split(":", 1)
                try:
                    port = int(port)
                except Exception:
                    port = None

            # temporarily override config.PORT if a port was provided
            original_port = getattr(scanner_v2.config, 'PORT', None)
            if port is not None:
                scanner_v2.config.PORT = port

            res = scanner_v2.ping(host)

            if port is not None and original_port is not None:
                scanner_v2.config.PORT = original_port

            if not res:
                out = "No Minecraft server response or timed out."
            else:
                players = res.get('players', {}).get('online', 0)
                maxp = res.get('players', {}).get('max', 0)
                version = res.get('version', {}).get('name', '-')
                motd = res.get('description', '-')
                if isinstance(motd, dict):
                    motd = motd.get('text', '-')
                out = f"IP: {host}\nPlayers: {players}/{maxp}\nVersion: {version}\nMOTD: {motd}\nFull JSON:\n{res}"
        except Exception as e:
            out = f"Error checking server: {e}"
        # write back to UI thread
        output.after(0, lambda: output.insert(tk.END, out))

    threading.Thread(target=worker, daemon=True).start()


def build_ui():
    root = tk.Tk()
    root.title("MCS Multi Tool")
    root.geometry("520x320")

    frame = tk.Frame(root)
    frame.pack(padx=8, pady=8, fill=tk.BOTH, expand=True)

    # Start scanner button
    start_btn = tk.Button(frame, text="Start Scanner", width=15, command=lambda: start_scanner(start_btn))
    start_btn.grid(row=0, column=0, sticky="w", padx=4, pady=4)

    # Online checker
    tk.Label(frame, text="Online Checker (ip or ip:port):").grid(row=1, column=0, sticky="w", padx=4)
    ip_entry = tk.Entry(frame, width=28)
    ip_entry.grid(row=1, column=1, sticky="w", padx=4)
    check_btn = tk.Button(frame, text="Check", command=lambda: check_online(ip_entry, output))
    check_btn.grid(row=1, column=2, sticky="w", padx=4)

    output = scrolledtext.ScrolledText(frame, height=12, width=60)
    output.grid(row=2, column=0, columnspan=3, pady=(8,0))

    root.mainloop()


if __name__ == '__main__':
    build_ui()
