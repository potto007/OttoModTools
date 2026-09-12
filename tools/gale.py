"""Facts about the Gale mod manager, the profile the game loads, and the game.

Shared by the harnesses that touch an installed profile. Every path is the WSL
path to the Windows install.
"""
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile

GALE = "/mnt/c/Users/paulo/AppData/Roaming/com.kesomannen.gale"
GAME = "valheim"
PROFILES = os.path.join(GALE, GAME, "profiles")
ILSPY = os.path.expanduser("~/.dotnet/tools/ilspycmd")
DOTNET = shutil.which("dotnet") or os.path.expanduser("~/.dotnet/dotnet")
MANAGED = "/mnt/c/Program Files (x86)/Steam/steamapps/common/Valheim/valheim_Data/Managed"


def wsl_path(path):
    """C:\\Users\\x -> /mnt/c/Users/x. Anything else comes back unchanged."""
    found = re.match(r"^([A-Za-z]):[\\/](.*)$", path)
    if not found:
        return path
    return "/mnt/%s/%s" % (found.group(1).lower(), found.group(2).replace("\\", "/"))


def active_profile():
    """The profile folder Gale launches Valheim with, or None if it cannot be read.

    Gale holds its database open in write-ahead-log mode, so the latest profile
    switch may exist only in the log. It is read from a copy that includes the log,
    which also keeps this from ever writing to Gale's own file.
    """
    db = os.path.join(GALE, "data.sqlite3")
    if not os.path.exists(db):
        return None
    with tempfile.TemporaryDirectory() as work:
        for suffix in ("", "-wal", "-shm"):
            if os.path.exists(db + suffix):
                shutil.copy(db + suffix, os.path.join(work, "data.sqlite3" + suffix))
        try:
            conn = sqlite3.connect(os.path.join(work, "data.sqlite3"))
            row = conn.execute(
                "SELECT p.path FROM managed_games g "
                "JOIN profiles p ON p.id = g.active_profile_id WHERE g.slug = ?",
                (GAME,)).fetchone()
            conn.close()
        except sqlite3.Error:
            return None
    return wsl_path(row[0]) if row else None


def game_running():
    """True or False, or None when Windows cannot be asked."""
    try:
        out = subprocess.run(["tasklist.exe", "/FI", "IMAGENAME eq valheim.exe", "/NH"],
                             capture_output=True, text=True, timeout=30).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    return "valheim.exe" in out.lower()


def plugin_version(dll):
    """(guid, name, version) from the BepInPlugin attribute, or None."""
    if not os.path.exists(ILSPY) or not os.path.exists(dll):
        return None
    out = subprocess.run([ILSPY, dll], capture_output=True, text=True).stdout
    found = re.search(r'BepInPlugin\("([^"]+)",\s*"([^"]+)",\s*"([^"]+)"', out)
    return found.groups() if found else None


def live_copies(plugins, filename):
    """Folders under plugins/ holding filename live, and those holding filename.old.

    Gale disables a mod by renaming its files to *.old, and a local import lands in
    an unprefixed folder beside the prefixed one, so the folder a build writes to
    and the folder the game loads from can differ."""
    live, disabled = [], []
    for dirpath, dirnames, filenames in os.walk(plugins):
        if filename in filenames:
            live.append(dirpath)
        if filename + ".old" in filenames:
            disabled.append(dirpath)
    return sorted(live), sorted(disabled)


def project_file(repo):
    """The mod's .csproj at the top of its repo, or None."""
    candidates = sorted(name for name in os.listdir(repo) if name.endswith(".csproj"))
    named = [c for c in candidates if c.startswith(os.path.basename(os.path.abspath(repo)))]
    chosen = named or candidates
    return os.path.join(repo, chosen[0]) if chosen else None


def manifest_version(folder):
    """version_number from a plugin folder's manifest.json, or None."""
    import json
    path = os.path.join(folder, "manifest.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8-sig") as handle:
            return json.load(handle).get("version_number")
    except ValueError:
        return None
