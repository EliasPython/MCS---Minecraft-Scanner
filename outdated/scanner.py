import asyncio, random, socket, struct, json, aiohttp, os, sys, time
from colorama import Fore, Style, init
import config
from concurrent.futures import ThreadPoolExecutor

executor = ThreadPoolExecutor(max_workers=config.CONCURRENCY)


http_session: aiohttp.ClientSession | None = None

last_title_update = 0


init(autoreset=True)

# ========= FARBEN =========
SCAN = Fore.YELLOW
NOSRV = Fore.RED
EMPTY = Fore.RED
ONLINE = Fore.GREEN
WEBHOOK = Fore.CYAN
ERROR = Fore.MAGENTA

RAINBOW = [Fore.RED, Fore.YELLOW, Fore.GREEN, Fore.CYAN, Fore.BLUE, Fore.MAGENTA]

# ========= COUNTER =========
scanned = 0
found = 0
with_players = 0

# ========= TITLE =========
def set_title():
    global last_title_update
    now = time.time()
    if now - last_title_update < 0.5:
        return
    last_title_update = now
    os.system(
        f"title Scanned: {scanned} ^| Servers: {found} ^| With Players: {with_players}"
    )


# ========= IP =========


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

    # DigitalOcean
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

    # Google Cloud
    ("34.0.0.0", 8),
]

# ========= IP UTILS =========
def ip_to_int(ip):
    a, b, c, d = map(int, ip.split("."))
    return (a << 24) | (b << 16) | (c << 8) | d

def int_to_ip(i):
    return ".".join(str((i >> s) & 255) for s in (24, 16, 8, 0))

def random_from_cidr(base, mask):
    base_int = ip_to_int(base)
    host_bits = 32 - mask
    rand = random.randint(1, (1 << host_bits) - 2)
    return int_to_ip(base_int + rand)

# ========= FINAL GENERATOR =========
def random_ip():
    # 70% ASN, 30% full random
    if random.random() < 0.7:
        base, mask = random.choice(ASN_RANGES)
        return random_from_cidr(base, mask)

    # fallback = public random
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

# ========= RAINBOW TEXT =========
def rainbow(text):
    out = ""
    for i, c in enumerate(text):
        out += RAINBOW[i % len(RAINBOW)] + c
    return out + Style.RESET_ALL

# ========= BLINK =========
def blink(text):
    print(Style.BRIGHT + text)
    time.sleep(0.05)
    print("\033[F\033[K", end="")

# ========= WEBHOOK =========
async def webhook(msg):
    global http_session
    if http_session is None:
        http_session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=5)
        )

    try:
        async with http_session.post(
            config.WEBHOOK_URL,
            json={"content": msg}
        ) as r:
            if r.status != 204 and r.status != 200:
                print(ERROR + f"[WEBHOOK ERROR] {r.status}")
    except Exception as e:
        print(ERROR + f"[WEBHOOK FAIL] {e}")
        
# ========= SCAN =========
async def scan(ip, sem):
    global scanned, found, with_players

    async with sem:
        scanned += 1
        set_title()
        print(SCAN + f"[SCAN] {ip}", flush=True)

        data = await asyncio.to_thread(ping, ip)

        if not data:
            print(NOSRV + f"[NONE] {ip}")
            return

        found += 1
        set_title()

        players = data["players"]["online"]
        maxp = data["players"]["max"]
        version = data["version"]["name"]
        motd = data["description"]
        if isinstance(motd, dict):
            motd = motd.get("text", "")

        if players > 0:
            with_players += 1
            set_title()

            text = f"[ONLINE] {ip} {players}/{maxp} {version}"
            blink(ONLINE + text)
            print(ONLINE + rainbow(text))

            await webhook(
                f"**IP:** `{ip}:{config.PORT}`\n"
                f"**Spieler:** `{players}/{maxp}`\n"
                f"**Version:** `{version}`\n"
                f"**MOTD:** `{motd}`"
            )
            print(WEBHOOK + f"[WEBHOOK] sent")

        else:
            print(EMPTY + f"[EMPTY] {ip} 0/{maxp} {version}")

# ========= MAIN =========
async def main():
    sem = asyncio.Semaphore(config.CONCURRENCY)
    tasks = []

    print(rainbow("=== MINECRAFT SERVER SCANNER STARTED ==="))

    while True:
        tasks.append(asyncio.create_task(scan(random_ip(), sem)))
        if len(tasks) >= config.CONCURRENCY * 2:
            await asyncio.gather(*tasks)
            tasks.clear()

asyncio.run(main())
