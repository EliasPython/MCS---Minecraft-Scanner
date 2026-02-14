import asyncio, random, socket, struct, json, aiohttp, os, sys, time
from colorama import Fore, Style, init
import config.config as config
import threading
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from instance_manager import get_instance_manager, StatsMessage


try:
    import tkinter as tk
    from tkinter import ttk
except Exception:
    tk = None
    ttk = None

executor = ThreadPoolExecutor(max_workers=max(50, config.CONCURRENCY * 2))

http_session: aiohttp.ClientSession | None = None

last_title_update = 0
last_title_scan_count = 0

# Title update thresholds (can be overridden in config.py)
TITLE_MIN_SECONDS = getattr(config, 'TITLE_MIN_SECONDS', 0.5)
TITLE_SCAN_STEP = getattr(config, 'TITLE_SCAN_STEP', 10)

# GUI references
gui_root = None
scan_log_text = None
stats_labels = {}
recent_box = None

# Scanner instance control
active_scanners = 1
scanner_instances = []  # List of running scanner tasks
stop_event = asyncio.Event()

# Multi-run control
target_runs = 0  # 0 = infinite (default), 2-10 = number of runs
current_run = 0  # Current run number
runs_completed = 0  # Completed runs counter

# Instance management
instance_mgr = get_instance_manager()
is_worker_mode = False  # True if running as worker (no GUI)
worker_stats_lock = threading.Lock()
worker_local_stats = {
    "scanned": 0,
    "found": 0,
    "with_players": 0,
    "sent_count": 0
}

# Worker callback functions
def on_worker_stats_received(message):
    """Callback when worker stats are received by master"""
    pass  # Stats are aggregated in gui_update_stats via get_all_stats()

def on_worker_disconnect(worker_id):
    """Callback when a worker disconnects"""
    gui_print(f"[MASTER] Worker {worker_id[:8]}... disconnected", "error")



init(autoreset=True)


# ========= FARBEN =========
SCAN = Fore.YELLOW
NOSRV = Fore.RED
EMPTY = Fore.GREEN
ONLINE = Fore.GREEN
WEBHOOK = Fore.CYAN
ERROR = Fore.MAGENTA

Pink = Fore.MAGENTA

RAINBOW = [Fore.RED, Fore.YELLOW, Fore.GREEN, Fore.CYAN, Fore.BLUE, Fore.MAGENTA]

PINK = [Fore.RED, Fore.LIGHTMAGENTA_EX, Fore.MAGENTA, Fore.LIGHTRED_EX]

PINK_GRAD = [Fore.LIGHTMAGENTA_EX, Fore.MAGENTA, Fore.RED]

# ========= SENT PERSISTENCE =========
SENT_FILE = "sent_servers.txt"
sent_set: set = set()
sent_lock = asyncio.Lock()

def load_sent():
    global sent_set
    try:
        with open(SENT_FILE, "r", encoding="utf-8") as f:
            for line in f:
                k = line.strip()
                if k:
                    sent_set.add(k)
    except FileNotFoundError:
        open(SENT_FILE, "a", encoding="utf-8").close()

def _append_sent_file(key: str):
    try:
        with open(SENT_FILE, "a", encoding="utf-8") as f:
            f.write(key + "\n")
    except Exception:
        pass

async def mark_sent(key: str) -> bool:
    """Mark key as sent. Returns True if newly marked, False if already present."""
    global sent_set
    async with sent_lock:
        if key in sent_set:
            return False
        sent_set.add(key)
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _append_sent_file, key)
    return True

# load existing sent entries
load_sent()

# ========= GUI OUTPUT FUNCTIONS =========
def gui_print(message: str, tag: str = None):
    global scan_log_text

    if not scan_log_text:
        return

    try:
        scan_log_text.insert("end", message + "\n", tag)
        scan_log_text.see("end")

        line_count = int(scan_log_text.index("end-1c").split(".")[0])
        if line_count > 1200:
            scan_log_text.delete("1.0", "300.0")

    except:
        pass


def gui_clear():
    """Clear the scan log."""
    global scan_log_text
    if scan_log_text:
        try:
            scan_log_text.delete('1.0', tk.END)
        except:
            pass

def gui_update_stats():
    """Update stats in GUI."""
    global stats_labels, recent_box, gui_root, active_scanners, target_runs, current_run, runs_completed
    if not gui_root:
        return
    
    try:
        # Get aggregated stats from all workers if master
        if instance_mgr.is_master:
            all_stats = instance_mgr.get_all_stats()
            total_scanned = scanned + all_stats["total_scanned"]
            total_found = found + all_stats["total_found"]
            total_with_players = with_players + all_stats["total_with_players"]
            total_sent = sent_count + all_stats["total_sent"]
            worker_count = all_stats["active_workers"]
        else:
            total_scanned = scanned
            total_found = found
            total_with_players = with_players
            total_sent = sent_count
            worker_count = 0
        
        stats_labels["Scanned"].config(text=str(total_scanned))
        stats_labels["Found"].config(text=str(total_found))
        stats_labels["With Players"].config(text=str(total_with_players))
        
        # Update rate per hour
        rate = compute_rate_per_hour(60)
        stats_labels["Server scanner per hour"].config(text=f"{rate:.0f}")
        
        # Update webhooks sent
        stats_labels["Webhooks Sent"].config(text=str(total_sent))
        
        # Update active scanners/workers
        if instance_mgr.is_master:
            stats_labels["Active Scanners"].config(text=str(worker_count + 1))  # +1 for master
        else:
            stats_labels["Active Scanners"].config(text="1")
        
        # Update run progress
        if target_runs >= 2:
            progress_text = f"{runs_completed}/{target_runs}"
            if current_run > runs_completed and current_run <= target_runs:
                progress_text = f"{current_run}/{target_runs} (running)"
            stats_labels["Run Progress"].config(text=progress_text)
        else:
            stats_labels["Run Progress"].config(text="-")
        
        if recent_box:

            recent_box.delete(0, tk.END)
            with recent_found_lock:
                for ip in list(recent_found):
                    recent_box.insert(tk.END, ip)
    except:
        pass
    
    gui_root.after(500, gui_update_stats)

# ========= COUNTER =========
scanned = 0
found = 0
with_players = 0
sent_count = 0
counter_lock = threading.Lock()  # Thread-safe counter updates


# timestamps of recent scans (for rate calculation)
scan_times: deque = deque(maxlen=10000)
scan_times_lock = threading.Lock()
# recent found servers (most-recent first)
recent_found: deque = deque(maxlen=20)
recent_found_lock = threading.Lock()

# ========= TITLE =========
def set_title():
    global last_title_update
    global last_title_scan_count
    now = time.time()
    # Only update if enough time has passed OR enough scans have occurred
    time_ok = (now - last_title_update) >= TITLE_MIN_SECONDS
    scans_ok = (scanned - last_title_scan_count) >= TITLE_SCAN_STEP
    if not (time_ok or scans_ok):
        return
    last_title_update = now
    last_title_scan_count = scanned
    # Title is now handled by GUI window title

#========= RATE CALCULATION =========
def compute_rate_per_hour(window_seconds: int = 60) -> float:
    """Compute an extrapolated servers/hour rate over last `window_seconds` seconds."""
    now = time.time()
    cutoff = now - window_seconds
    with scan_times_lock:
        count = 0
        for ts in reversed(scan_times):
            if ts >= cutoff:
                count += 1
            else:
                break
    if window_seconds == 0:
        return 0.0
    return (count / window_seconds) * 3600.0

# ========= CONFIG FUNCTIONS =========
def save_config_settings(webhook_url, port, timeout, concurrency, web_host, web_port):
    """Save settings to config.py file"""
    try:
        config_path = os.path.join(os.path.dirname(__file__), "config", "config.py")
        with open(config_path, "w", encoding="utf-8") as f:
            f.write(f'''WEBHOOK_URL = "{webhook_url}"
PORT = {port}
TIMEOUT = {timeout}
CONCURRENCY = {concurrency}
WEB_HOST = "{web_host}"
WEB_PORT = {web_port}
''')
        return True
    except Exception as e:
        print(f"[ERROR] Failed to save config: {e}")
        return False

def load_ascii_art():
    """Load ASCII art from file"""
    try:
        with open("ascii/ascii_art.txt", "r", encoding="utf-8") as f:
            return f.read()
    except:
        return """
    🌹 CYBER MCS SCANNER 🌹
    
    Version 2.0
    Created with 💜
        """

# ========= MAIN GUI WINDOW =========
def run_main_gui():
    global gui_root, scan_log_text, stats_labels, recent_box

    if tk is None:
        return

    gui_root = tk.Tk()
    gui_root.overrideredirect(True)
    gui_root.geometry("1000x700")
    gui_root.configure(bg="#000000")

    BG = "#000000"
    CARD = "#050505"
    PINK = "#ff00aa"
    PURPLE = "#8a2be2"
    NEON = "#ff4df2"

    # ================= TITLE BAR =================
    title_bar = tk.Frame(gui_root, bg="#000000", height=40)
    title_bar.pack(fill="x")
    title_bar.pack_propagate(False)

    title_label = tk.Label(
    title_bar,
    text="🌹 CYBER MCS SCANNER 🌹",
    bg="#000000",
    fg=PINK,
    font=("Segoe UI", 14, "bold")
)
    title_label.pack(side="left", padx=15)

    glow_colors = ["#ff0080", "#ff00ff", "#ff4df2", "#ff1493"]
    glow_state = [0]

    def animate_rose():
        title_label.config(fg=glow_colors[glow_state[0] % len(glow_colors)])
        glow_state[0] += 1
        gui_root.after(400, animate_rose)

    animate_rose()
    
    # ===== RIGHT (CONNECT EMBED) =====
    connect_frame = tk.Frame(
        title_bar,
        bg="#070707",
        highlightbackground="#8a2be2",
        highlightthickness=1
    )
    connect_frame.pack(side="right", padx=20, pady=8)

    connect_label = tk.Label(
        connect_frame,
        text=" CONNECT: ",
        bg="#070707",
        fg="#a020f0",
        font=("Consolas", 10, "bold")
    )
    connect_label.pack(side="left", padx=(8,4))

    connect_entry = tk.Entry(
        connect_frame,
        bg="#000000",
        fg="#ff00aa",
        insertbackground="#ff00aa",
        bd=0,
        font=("Consolas", 10),
        width=18
    )
    connect_entry.pack(side="left", padx=(0,8), pady=4)

    # ===== Focus Glow Effect =====
    def on_focus_in(e):
        connect_frame.config(highlightbackground="#ff00aa", highlightthickness=2)

    def on_focus_out(e):
        connect_frame.config(highlightbackground="#8a2be2", highlightthickness=1)

    connect_entry.bind("<FocusIn>", on_focus_in)
    connect_entry.bind("<FocusOut>", on_focus_out)

    # ENTER gedrückt - handle run command
    def on_enter(e):
        global target_runs, current_run, runs_completed
        value = connect_entry.get().strip().lower()
        
        # Parse "run X" command where X is 2-10
        if value.startswith("run "):
            try:
                num_runs = int(value.split()[1])
                if 2 <= num_runs <= 10:
                    target_runs = num_runs
                    current_run = 1
                    runs_completed = 0
                    gui_print(f"\n[CONFIG] Multi-run mode activated: {target_runs} runs", "scan")
                    gui_print(f"[CONFIG] Starting run 1/{target_runs}...\n", "scan")
                    # Clear the entry
                    connect_entry.delete(0, tk.END)
                else:
                    gui_print(f"[ERROR] Run count must be between 2 and 10 (got {num_runs})", "error")
            except (ValueError, IndexError):
                gui_print(f"[ERROR] Invalid command. Use: run 2-10", "error")
        else:
            print("CONNECT VALUE:", value)

    connect_entry.bind("<Return>", on_enter)


    close_btn = tk.Label(
        title_bar,
        text=" ✕ ",
        bg="#000000",
        fg=PINK,
        font=("Segoe UI", 12, "bold"),
        cursor="hand2"
    )
    close_btn.pack(side="right", padx=10)
    close_btn.bind("<Button-1>", lambda e: gui_root.destroy())

    # Draggable
    def start_move(e):
        gui_root.x = e.x
        gui_root.y = e.y

    def do_move(e):
        gui_root.geometry(f"+{e.x_root - gui_root.x}+{e.y_root - gui_root.y}")

    title_bar.bind("<Button-1>", start_move)
    title_bar.bind("<B1-Motion>", do_move)

    # ================= CONTENT WITH TABS =================
    content = tk.Frame(gui_root, bg=BG)
    content.pack(fill="both", expand=True, padx=10, pady=10)

    # Create Notebook (Tabs)
    style = ttk.Style()
    style.theme_use('default')
    style.configure("TNotebook", background=BG, borderwidth=0)
    style.configure("TNotebook.Tab", background=CARD, foreground=PINK, font=("Consolas", 10, "bold"), padding=[10, 5])
    style.map("TNotebook.Tab", background=[("selected", PURPLE)], foreground=[("selected", "#ffffff")])
    
    notebook = ttk.Notebook(content)
    notebook.pack(fill="both", expand=True)

    # ================= SCANNER TAB =================
    scanner_tab = tk.Frame(notebook, bg=BG)
    notebook.add(scanner_tab, text="⚡ SCANNER")

    scanner_content = tk.Frame(scanner_tab, bg=BG)
    scanner_content.pack(fill="both", expand=True, padx=5, pady=5)

    # ================= LOG PANEL =================
    log_panel = tk.Frame(scanner_content, bg=CARD, highlightbackground=PURPLE, highlightthickness=2)
    log_panel.pack(side="left", fill="both", expand=True, padx=(0, 8))

    tk.Label(
        log_panel,
        text="⚡ LIVE SCAN LOG",
        bg=CARD,
        fg=PURPLE,
        font=("Consolas", 12, "bold")
    ).pack(pady=10)

    scan_log_text = tk.Text(
        log_panel,
        bg="#020202",
        fg="#00ffea",
        font=("Consolas", 9),
        insertbackground=PINK,
        bd=0,
        highlightthickness=0,
        wrap="word"
    )
    scan_log_text.pack(fill="both", expand=True, padx=10, pady=(0,10))

    # Cyberpunk Tags
    scan_log_text.tag_config("scan", foreground="#ffff00")
    scan_log_text.tag_config("none", foreground="#ff0055")
    scan_log_text.tag_config("online", foreground="#00ff99")
    scan_log_text.tag_config("empty", foreground="#00ffaa")
    scan_log_text.tag_config("webhook", foreground="#00e1ff")
    scan_log_text.tag_config("error", foreground="#ff00ff")

    # ================= STATS PANEL =================
    stats_panel = tk.Frame(scanner_content, bg=CARD, highlightbackground=PURPLE, highlightthickness=2)
    stats_panel.pack(side="right", fill="y", ipadx=10)

    tk.Label(
        stats_panel,
        text="📊 STATS",
        bg=CARD,
        fg=PURPLE,
        font=("Consolas", 12, "bold")
    ).pack(pady=15)

    keys = ["Scanned", "Found", "With Players", "Server scanner per hour", "Webhooks Sent", "Active Scanners", "Run Progress"]

    for k in keys:

        tk.Label(
            stats_panel,
            text=k,
            bg=CARD,
            fg=PURPLE,
            font=("Segoe UI", 9)
        ).pack(pady=(5,0))

        if k == "Run Progress":
            stats_labels[k] = tk.Label(
                stats_panel,
                text="-",
                bg=CARD,
                fg=PINK,
                font=("Consolas", 14, "bold")
            )
        else:
            stats_labels[k] = tk.Label(
                stats_panel,
                text="0",
                bg=CARD,
                fg=PINK,
                font=("Consolas", 14, "bold")
            )
        stats_labels[k].pack(pady=(0,10))

    # ================= SETTINGS TAB =================
    settings_tab = tk.Frame(notebook, bg=BG)
    notebook.add(settings_tab, text="⚙️ SETTINGS")

    settings_canvas = tk.Canvas(settings_tab, bg=BG, highlightthickness=0)
    scrollbar = tk.Scrollbar(settings_tab, orient="vertical", command=settings_canvas.yview)
    scrollable_frame = tk.Frame(settings_canvas, bg=BG)

    scrollable_frame.bind(
        "<Configure>",
        lambda e: settings_canvas.configure(scrollregion=settings_canvas.bbox("all"))
    )

    settings_canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
    settings_canvas.configure(yscrollcommand=scrollbar.set)

    settings_canvas.pack(side="left", fill="both", expand=True)
    scrollbar.pack(side="right", fill="y")

    # Settings Title
    tk.Label(
        scrollable_frame,
        text="⚙️ CONFIGURATION",
        bg=BG,
        fg=PURPLE,
        font=("Consolas", 16, "bold")
    ).pack(pady=20)

    # Config fields
    config_fields = []
    
    # WEBHOOK_URL
    tk.Label(scrollable_frame, text="WEBHOOK URL", bg=BG, fg=PINK, font=("Consolas", 10, "bold")).pack(pady=(15,5))
    webhook_entry = tk.Entry(scrollable_frame, bg=CARD, fg="#00ffea", insertbackground=PINK, font=("Consolas", 9), width=70, bd=2, highlightbackground=PURPLE, highlightthickness=1)
    webhook_entry.pack(pady=5, padx=20)
    webhook_entry.insert(0, config.WEBHOOK_URL)
    config_fields.append(("webhook", webhook_entry))

    # PORT
    tk.Label(scrollable_frame, text="PORT", bg=BG, fg=PINK, font=("Consolas", 10, "bold")).pack(pady=(15,5))
    port_entry = tk.Entry(scrollable_frame, bg=CARD, fg="#00ffea", insertbackground=PINK, font=("Consolas", 10), width=20, bd=2, highlightbackground=PURPLE, highlightthickness=1)
    port_entry.pack(pady=5)
    port_entry.insert(0, str(config.PORT))
    config_fields.append(("port", port_entry))

    # TIMEOUT
    tk.Label(scrollable_frame, text="TIMEOUT (seconds)", bg=BG, fg=PINK, font=("Consolas", 10, "bold")).pack(pady=(15,5))
    timeout_entry = tk.Entry(scrollable_frame, bg=CARD, fg="#00ffea", insertbackground=PINK, font=("Consolas", 10), width=20, bd=2, highlightbackground=PURPLE, highlightthickness=1)
    timeout_entry.pack(pady=5)
    timeout_entry.insert(0, str(config.TIMEOUT))
    config_fields.append(("timeout", timeout_entry))

    # CONCURRENCY
    tk.Label(scrollable_frame, text="CONCURRENCY", bg=BG, fg=PINK, font=("Consolas", 10, "bold")).pack(pady=(15,5))
    concurrency_entry = tk.Entry(scrollable_frame, bg=CARD, fg="#00ffea", insertbackground=PINK, font=("Consolas", 10), width=20, bd=2, highlightbackground=PURPLE, highlightthickness=1)
    concurrency_entry.pack(pady=5)
    concurrency_entry.insert(0, str(config.CONCURRENCY))
    config_fields.append(("concurrency", concurrency_entry))

    # WEB_HOST
    tk.Label(scrollable_frame, text="WEB HOST", bg=BG, fg=PINK, font=("Consolas", 10, "bold")).pack(pady=(15,5))
    webhost_entry = tk.Entry(scrollable_frame, bg=CARD, fg="#00ffea", insertbackground=PINK, font=("Consolas", 10), width=20, bd=2, highlightbackground=PURPLE, highlightthickness=1)
    webhost_entry.pack(pady=5)
    webhost_entry.insert(0, config.WEB_HOST)
    config_fields.append(("webhost", webhost_entry))

    # WEB_PORT
    tk.Label(scrollable_frame, text="WEB PORT", bg=BG, fg=PINK, font=("Consolas", 10, "bold")).pack(pady=(15,5))
    webport_entry = tk.Entry(scrollable_frame, bg=CARD, fg="#00ffea", insertbackground=PINK, font=("Consolas", 10), width=20, bd=2, highlightbackground=PURPLE, highlightthickness=1)
    webport_entry.pack(pady=5)
    webport_entry.insert(0, str(config.WEB_PORT))
    config_fields.append(("webport", webport_entry))

    # Status Label
    settings_status = tk.Label(scrollable_frame, text="", bg=BG, fg="#00ff99", font=("Consolas", 10, "bold"))
    settings_status.pack(pady=10)

    # Buttons Frame
    btn_frame = tk.Frame(scrollable_frame, bg=BG)
    btn_frame.pack(pady=20)

    def save_settings():
        try:
            webhook = webhook_entry.get()
            port = int(port_entry.get())
            timeout = int(timeout_entry.get())
            concurrency = int(concurrency_entry.get())
            webhost = webhost_entry.get()
            webport = int(webport_entry.get())
            
            if save_config_settings(webhook, port, timeout, concurrency, webhost, webport):
                settings_status.config(text="✅ Settings saved! Restart required.", fg="#00ff99")
            else:
                settings_status.config(text="❌ Failed to save settings!", fg="#ff0055")
        except ValueError:
            settings_status.config(text="❌ Invalid number values!", fg="#ff0055")

    def reset_settings():
        webhook_entry.delete(0, tk.END)
        webhook_entry.insert(0, config.WEBHOOK_URL)
        port_entry.delete(0, tk.END)
        port_entry.insert(0, str(config.PORT))
        timeout_entry.delete(0, tk.END)
        timeout_entry.insert(0, str(config.TIMEOUT))
        concurrency_entry.delete(0, tk.END)
        concurrency_entry.insert(0, str(config.CONCURRENCY))
        webhost_entry.delete(0, tk.END)
        webhost_entry.insert(0, config.WEB_HOST)
        webport_entry.delete(0, tk.END)
        webport_entry.insert(0, str(config.WEB_PORT))
        settings_status.config(text="🔄 Settings reset to current values", fg="#ffff00")

    tk.Button(
        btn_frame,
        text="💾 SAVE",
        command=save_settings,
        bg=PURPLE,
        fg="#ffffff",
        font=("Consolas", 12, "bold"),
        bd=0,
        padx=20,
        pady=8,
        cursor="hand2",
        activebackground=PINK,
        activeforeground="#ffffff"
    ).pack(side="left", padx=10)

    tk.Button(
        btn_frame,
        text="🔄 RESET",
        command=reset_settings,
        bg=CARD,
        fg=PINK,
        font=("Consolas", 12, "bold"),
        bd=2,
        highlightbackground=PURPLE,
        highlightthickness=2,
        padx=20,
        pady=8,
        cursor="hand2",
        activebackground=PURPLE,
        activeforeground="#ffffff"
    ).pack(side="left", padx=10)

    # ================= CREDITS TAB =================
    credits_tab = tk.Frame(notebook, bg=BG)
    notebook.add(credits_tab, text="💜 CREDITS")

    credits_canvas = tk.Canvas(credits_tab, bg=BG, highlightthickness=0)
    credits_scrollbar = tk.Scrollbar(credits_tab, orient="vertical", command=credits_canvas.yview)
    credits_frame = tk.Frame(credits_canvas, bg=BG)

    credits_frame.bind(
        "<Configure>",
        lambda e: credits_canvas.configure(scrollregion=credits_canvas.bbox("all"))
    )

    credits_canvas.create_window((0, 0), window=credits_frame, anchor="nw")
    credits_canvas.configure(yscrollcommand=credits_scrollbar.set)

    credits_canvas.pack(side="left", fill="both", expand=True)
    credits_scrollbar.pack(side="right", fill="y")

    # ASCII Art
    ascii_text = tk.Text(
        credits_frame,
        bg=BG,
        fg=PINK,
        font=("Consolas", 8),
        bd=0,
        highlightthickness=0,
        wrap="word",
        height=80,
        width=80
    )
    ascii_text.pack(pady=20)
    ascii_text.insert("1.0", load_ascii_art())
    ascii_text.config(state="disabled")

    # Title
    tk.Label(
        credits_frame,
        text="🌹 CYBER MCS SCANNER 🌹",
        bg=BG,
        fg=PURPLE,
        font=("Consolas", 20, "bold")
    ).pack(pady=10)

    # Version
    tk.Label(
        credits_frame,
        text="Version 2.0 - Tab Edition",
        bg=BG,
        fg=NEON,
        font=("Consolas", 12)
    ).pack(pady=5)

    # Separator
    tk.Frame(credits_frame, bg=PURPLE, height=2, width=400).pack(pady=20)

    # Features
    tk.Label(
        credits_frame,
        text="✨ FEATURES",
        bg=BG,
        fg=PINK,
        font=("Consolas", 14, "bold")
    ).pack(pady=10)

    features = [
        "🔍 High-performance Minecraft server scanner",
        "🌐 Multi-instance support (Master/Worker mode)",
        "📊 Real-time statistics and monitoring",
        "🔔 Discord webhook notifications",
        "🎨 Cyberpunk-themed GUI",
        "⚙️ Configurable settings",
        "🚀 Async/await for maximum performance"
    ]

    for feature in features:
        tk.Label(
            credits_frame,
            text=feature,
            bg=BG,
            fg="#00ffea",
            font=("Segoe UI", 10)
        ).pack(pady=2)

    # Separator
    tk.Frame(credits_frame, bg=PURPLE, height=2, width=400).pack(pady=20)

    # Created by
    tk.Label(
        credits_frame,
        text="Created with 💜 for the Minecraft community",
        bg=BG,
        fg=PINK,
        font=("Consolas", 11, "bold")
    ).pack(pady=10)

    # Tip
    tk.Label(
        credits_frame,
        text="💡 Tip: Use 'run 2-10' in the CONNECT field for multi-run mode!",
        bg=CARD,
        fg="#ffff00",
        font=("Consolas", 10),
        padx=10,
        pady=5
    ).pack(pady=20)

    # ================= GLOW ANIMATION =================
    glow_state = [0]
    def animate():
        colors = [PINK, NEON, PURPLE]
        title_label.config(fg=colors[glow_state[0] % len(colors)])
        glow_state[0] += 1
        gui_root.after(500, animate)

    animate()

    gui_root.after(500, gui_update_stats)
    gui_root.mainloop()




# ========= ASN RANGES =========
ASN_RANGES = [
    # Hetzner (DE)
    ("88.198.0.0", 16),
    ("95.216.0.0", 15),
    ("116.202.0.0", 16),
    ("138.201.0.0", 16),
    ("159.69.0.0", 16),

    # OVH (EU)
    ("51.38.0.0", 16),
    ("54.36.0.0", 16),
    ("145.239.0.0", 16),

    # OVH US
    ("137.74.0.0", 16),

    # DigitalOceans
    ("142.93.0.0", 16),
    ("159.65.0.0", 16),
    ("167.99.0.0", 16),

    # Contabo
    ("5.189.0.0", 16),
    ("37.228.0.0", 16),
    ("185.228.0.0", 16),

    # Netcup
    ("89.58.0.0", 16),
    ("46.38.0.0", 16),

    # AWS
    ("18.0.0.0", 8),
    ("3.0.0.0", 8),

    # Azure
    ("20.0.0.0", 8),

    # Google Clouds
    ("34.0.0.0", 8),
]

# Configure how IPs are selected. Lower `ASN_PROB` => more full-random IPs.
ASN_PROB = getattr(config, 'ASN_PROB', 0.5)
# Expand CIDR masks by this many bits when sampling from ASN ranges (0 = no expansion).
ASN_EXPAND_BITS = getattr(config, 'ASN_EXPAND_BITS', 4)

# Additional ASN ranges to increase coverage
ASN_RANGES += [
    ("162.243.0.0", 16),  # Linode
    ("198.199.0.0", 16),  # DigitalOcean
    ("104.248.0.0", 16),  # Vultr
    ("207.148.0.0", 16),  # Vultr
    ("138.68.0.0", 16),   # DigitalOcean
    ("165.227.0.0", 16),  # DigitalOcean / Linode
    ("157.230.0.0", 16),  # DigitalOcean
    ("104.236.0.0", 16),  # DigitalOcean
    ("45.55.0.0", 16),    # DigitalOcean
    ("64.62.0.0", 16),    # Linode
    ("45.79.0.0", 16),    # Vultr
    ("149.56.0.0", 16),   # Scaleway / misc
    ("192.241.128.0", 17),
    ("185.117.0.0", 16),
    ("213.32.0.0", 16),
    ("46.105.0.0", 16),
    ("185.104.0.0", 16),
    ("91.121.0.0", 16),
    ("185.6.0.0", 16),
]

# --- Larger continental coverage ---
CONTINENTAL_RANGES = [
    ("5.39.0.0", 16),
    ("31.13.0.0", 16),
    ("46.101.0.0", 16),
    ("51.15.0.0", 16),
    ("62.75.0.0", 16),
    ("77.73.0.0", 16),
    ("80.67.0.0", 16),
    ("104.0.0.0", 16),
    ("107.170.0.0", 16),
    ("173.194.0.0", 16),
    ("74.125.0.0", 16),
    ("96.0.0.0", 16),
    ("103.4.0.0", 16),
    ("116.31.0.0", 16),
    ("119.28.0.0", 16),
    ("123.125.0.0", 16),
    ("177.53.0.0", 16),
    ("179.43.0.0", 16),
    ("181.224.0.0", 16),
    ("41.0.0.0", 16),
    ("102.66.0.0", 16),
    ("154.0.0.0", 16),
    ("103.20.0.0", 16),
    ("203.0.0.0", 16),
    ("1.0.0.0", 16),
    ("185.8.0.0", 16),
    ("185.9.0.0", 16),
    ("178.62.0.0", 16),
    ("159.203.0.0", 16),
    ("157.230.0.0", 16),
]

ASN_RANGES += CONTINENTAL_RANGES

# ========= IP UTILS =========
def ip_to_int(ip):
    a, b, c, d = map(int, ip.split("."))
    return (a << 24) | (b << 16) | (c << 8) | d

def int_to_ip(i):
    return ".".join(str((i >> s) & 255) for s in (24, 16, 8, 0))

def random_from_cidr(base, mask, expand_bits: int = 0):
    base_int = ip_to_int(base)
    new_mask = max(8, mask - expand_bits)
    host_bits = 32 - new_mask
    rand = random.randint(1, (1 << host_bits) - 2)
    return int_to_ip(base_int + rand)

def random_ip():
    if random.random() < ASN_PROB:
        base, mask = random.choice(ASN_RANGES)
        return random_from_cidr(base, mask, ASN_EXPAND_BITS)

    while True:
        a = random.randint(1, 223)
        b = random.randint(0, 255)
        c = random.randint(0, 255)
        d = random.randint(1, 254)

        if a in (10, 127, 0):
            continue
        if a == 169 and b == 254:
            continue
        if a == 172 and 16 <= b <= 31:
            continue
        if a == 192 and b == 168:
            continue
        if a >= 224:
            continue
        if a == 100 and 64 <= b <= 127:
            continue

        return f"{a}.{b}.{c}.{d}"

# ========= VARINT =========
def encode_varint(v):
    out = b""
    while True:
        b = v & 0x7F
        v >>= 7
        out += struct.pack("B", b | (0x80 if v else 0))
        if not v:
            return out

def decode_varint(sock):
    num = 0
    for i in range(5):
        b = sock.recv(1)
        if not b:
            return None
        b = b[0]
        num |= (b & 0x7F) << (7 * i)
        if not b & 0x80:
            return num
    return None

# ========= MINECRAFT PING =========
def ping(ip):
    try:
        s = socket.socket()
        s.settimeout(config.TIMEOUT)
        s.connect((ip, config.PORT))

        handshake = (
            encode_varint(0) +
            encode_varint(754) +
            encode_varint(len(ip)) + ip.encode() +
            struct.pack(">H", config.PORT) +
            encode_varint(1)
        )

        s.sendall(encode_varint(len(handshake)) + handshake)
        s.sendall(b"\x01\x00")

        decode_varint(s)
        decode_varint(s)
        length = decode_varint(s)

        data = s.recv(length)
        s.close()
        return json.loads(data.decode())
    except:
        return None

# ========= WEBHOOK =========
async def webhook(msg):
    global http_session
    if http_session is None:
        http_session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=getattr(config, 'WEBHOOK_TIMEOUT', 3))
        )
    payload = {}
    if isinstance(msg, dict):
        payload["embeds"] = [msg]
    else:
        payload["content"] = msg

    try:
        async with http_session.post(
            config.WEBHOOK_URL,
            json=payload
        ) as r:
            if r.status not in (200, 204):
                gui_print(f"[WEBHOOK ERROR] {r.status}", "error")
    except Exception as e:
        gui_print(f"[WEBHOOK FAIL] {e}", "error")
        
# ========= SCAN =========
async def scan(ip, sem):
    global scanned, found, with_players, sent_count

    async with sem:
        with counter_lock:
            scanned += 1
        try:
            with scan_times_lock:
                scan_times.append(time.time())
        except Exception:
            pass
        set_title()
        gui_print(f"[SCAN] {ip}", "scan")

        data = await asyncio.get_running_loop().run_in_executor(executor, ping, ip)

        if not data:
            gui_print(f"[NONE] {ip}", "none")
            return

        with counter_lock:
            found += 1
        try:
            with recent_found_lock:
                recent_found.appendleft(f"{ip}:{config.PORT}")
        except Exception:
            pass
        set_title()

        players = data["players"]["online"]
        maxp = data["players"]["max"]
        version = data["version"]["name"]
        motd = data["description"]
        if isinstance(motd, dict):
            motd = motd.get("text", "")

        if players > 0:
            with counter_lock:
                with_players += 1
            set_title()

            text = f"[ONLINE] {ip} {players}/{maxp} {version}"
            gui_print(text, "online")

            # Build a Discord embed payload
            motd_text = motd or "-"
            if len(motd_text) > 1020:
                motd_text = motd_text[:1017] + "..."

            embed = {
                "title": "Minecraft Server Online",
                "description": f"{ip}:{config.PORT}",
                "color": 3066993,
                "fields": [
                    {"name": "Spieler", "value": f"{players}/{maxp}", "inline": True},
                    {"name": "Version", "value": version, "inline": True},
                    {"name": "MOTD", "value": motd_text, "inline": False},
                ]
            }

            key = f"{ip}:{config.PORT}"
            if await mark_sent(key):
                asyncio.create_task(webhook(embed))
                with counter_lock:
                    sent_count += 1
                gui_print(f"[WEBHOOK] queued", "webhook")
            else:
                gui_print(f"[SKIP] {key} already sent", "webhook")

        else:
            gui_print(f"[EMPTY] {ip} 0/{maxp} {version}", "empty")

            motd_text = motd or "-"
            if len(motd_text) > 1020:
                motd_text = motd_text[:1017] + "..."

            empty_embed = {
                "title": "Minecraft Server Empty",
                "description": f"{ip}:{config.PORT}",
                "color": 15105570,
                "fields": [
                    {"name": "Spieler", "value": f"0/{maxp}", "inline": True},
                    {"name": "Version", "value": version, "inline": True},
                    {"name": "MOTD", "value": motd_text, "inline": False},
                ]
            }

            key = f"{ip}:{config.PORT}"
            if await mark_sent(key):
                asyncio.create_task(webhook(empty_embed))
                with counter_lock:
                    sent_count += 1
                gui_print(f"[WEBHOOK] queued (empty)", "webhook")
            else:
                gui_print(f"[SKIP] {key} already sent", "webhook")

        # Update worker local stats if in worker mode
        if is_worker_mode:
            with worker_stats_lock:
                worker_local_stats["scanned"] = scanned
                worker_local_stats["found"] = found
                worker_local_stats["with_players"] = with_players
                worker_local_stats["sent_count"] = sent_count


# ========= SCANNER RUN =========
async def run_scanner_instance(sem, instance_num, total_runs):
    """Run a single scanner instance with a defined number of IPs."""
    global current_run, runs_completed
    
    ips_per_run = 1000  # Number of IPs to scan per run
    
    gui_print(f"\n>>> STARTING RUN {instance_num}/{total_runs} <<<\n", "scan")
    
    tasks = []
    scanned_in_run = 0
    
    while scanned_in_run < ips_per_run:
        if stop_event.is_set():
            break
            
        tasks.append(asyncio.create_task(scan(random_ip(), sem)))
        scanned_in_run += 1
        
        if len(tasks) >= config.CONCURRENCY * 2:
            await asyncio.gather(*tasks)
            tasks.clear()
    
    # Gather remaining tasks
    if tasks:
        await asyncio.gather(*tasks)
        tasks.clear()
    
    runs_completed += 1
    gui_print(f"\n>>> RUN {instance_num}/{total_runs} COMPLETED <<<\n", "online")
    
    if instance_num < total_runs:
        current_run = instance_num + 1
        gui_print(f"Preparing run {current_run}/{total_runs}...\n", "scan")

# ========= WORKER MODE MAIN =========
async def worker_main():
    """Main loop for worker instances (no GUI)"""
    global scanned, found, with_players, sent_count, is_worker_mode
    
    is_worker_mode = True
    sem = asyncio.Semaphore(config.CONCURRENCY)
    
    print(f"[WORKER] Started worker instance (ID: {instance_mgr.instance_id})")
    print("[WORKER] Connecting to master...")
    
    if not instance_mgr.start_as_worker():
        print("[WORKER] Failed to connect to master, exiting")
        return
    
    print("[WORKER] Connected to master, starting scan...")
    
    # Start stats reporting task
    async def report_stats():
        while True:
            await asyncio.sleep(2)  # Report every 2 seconds
            with worker_stats_lock:
                instance_mgr.send_worker_stats(
                    worker_local_stats["scanned"],
                    worker_local_stats["found"],
                    worker_local_stats["with_players"],
                    worker_local_stats["sent_count"]
                )
    
    # Run scanner and stats reporter concurrently
    async def scanner_loop():
        tasks = []
        while True:
            if stop_event.is_set():
                break
            tasks.append(asyncio.create_task(scan(random_ip(), sem)))
            if len(tasks) >= config.CONCURRENCY * 2:
                await asyncio.gather(*tasks)
                tasks.clear()
    
    try:
        await asyncio.gather(scanner_loop(), report_stats())
    except KeyboardInterrupt:
        pass
    finally:
        instance_mgr.disconnect_worker()
        print("[WORKER] Disconnected from master")

# ========= MAIN =========
async def main():
    global current_run, target_runs, is_worker_mode
    
    # Check if we should run as master or worker
    is_master = instance_mgr.check_master()
    
    if not is_master:
        # Run as worker - no GUI
        is_worker_mode = True
        await worker_main()
        return
    
    # Run as master - with GUI
    is_worker_mode = False
    instance_mgr.start_as_master(on_worker_stats_received, on_worker_disconnect)
    gui_print("[MASTER] Started as master instance", "scan")
    gui_print("[MASTER] Workers can now connect to this instance", "scan")
    
    sem = asyncio.Semaphore(config.CONCURRENCY)
    gui_print("=== MINECRAFT SERVER SCANNER STARTED ===", "scan")
    gui_print("Enter 'run 2-10' in CONNECT field for multi-run mode", "scan")
    gui_print("Standard mode: infinite scan\n", "scan")

    # Wait a moment for GUI to be ready
    await asyncio.sleep(0.5)
    
    # Check if multi-run mode is activated
    if target_runs >= 2:
        # Multi-run mode: run X times sequentially
        for run_num in range(1, target_runs + 1):
            if stop_event.is_set():
                break
            await run_scanner_instance(sem, run_num, target_runs)
        
        gui_print(f"\n=== ALL {target_runs} RUNS COMPLETED ===", "online")
        gui_print("Total servers scanned: " + str(scanned), "online")
        gui_print("Total servers found: " + str(found), "online")
        gui_print("Total with players: " + str(with_players), "online")
        
        # Keep the GUI alive but stop scanning
        while not stop_event.is_set():
            await asyncio.sleep(1)
    else:
        # Standard infinite mode
        tasks = []
        while True:
            if stop_event.is_set():
                break
            tasks.append(asyncio.create_task(scan(random_ip(), sem)))
            if len(tasks) >= config.CONCURRENCY * 2:
                await asyncio.gather(*tasks)
                tasks.clear()


if __name__ == "__main__":
    # Check instance mode first
    is_master = instance_mgr.check_master()
    
    if not is_master:
        # Worker mode - no GUI, console only
        try:
            print("[WORKER] Starting in worker mode (no GUI)")
            asyncio.run(main())
        except KeyboardInterrupt:
            print("\n[WORKER] Exiting...")
        finally:
            instance_mgr.stop()
    else:
        # Master mode - with GUI
        try:
            if tk is not None:
                # Run GUI in main thread
                gui_thread = threading.Thread(target=run_main_gui, daemon=True)
                gui_thread.start()
                # Give GUI time to initialize
                time.sleep(1)
                # Start scanner in main thread
                asyncio.run(main())
            else:
                print("[ERROR] tkinter not available")
        except KeyboardInterrupt:
            print("\nExiting...")
        finally:
            instance_mgr.stop()
