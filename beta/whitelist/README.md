Whitelist scanner integration
=============================

Usage
-----

1. Ensure `git` and Python are installed.
2. From project root run:

```powershell
python whitelist\run_whitelist.py    # clones the repo and prints run instructions
python whitelist\run_whitelist.py --run    # clones (if needed) and runs detected entrypoint
```

Notes
-----
- The script will clone `https://github.com/kgurchiek/Minecraft-Whitelist-Scanner.git` into `whitelist/Minecraft-Whitelist-Scanner`.
- Running is best done manually first to verify the original project's dependencies and entrypoint.
- When executed with `--run` this wrapper will capture stdout into `whitelist/results.json` and append any IP-like lines to `ressources/sent_servers.txt` (best-effort).

Customize
---------
Open `whitelist/run_whitelist.py` to change repository URL, result paths, or the run behavior.
