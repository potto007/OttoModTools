"""Report any config section or key that the migration would rename.

The library rewrites a player's .cfg to the PascalCase spelling before anything binds.
A bind that asks for a different spelling therefore binds a name the file no longer
holds, which creates a duplicate entry and silently resets the value. Nothing at run
time says so, and the setting simply reverts.

The rule, from ADR-0007 and ADR-0008: split on any run of characters that are not
letters or digits, capitalize the first letter of each part, keep the rest of each part
as it is, join with nothing, and keep a leading underscore. A section name also loses a
leading ordinal such as "1 - ", and an empty section name becomes General.

It sees ModLib's Config.Define, and the config() and TextEntryConfig() helpers that the
ServerSync based mods wrap around Config.Bind, when the names are string literals. A
name held in a variable is invisible to it, which is why those helpers also normalize
at bind time.
"""
import os
import re
import sys

ROOT = sys.argv[1]

BIND = re.compile(
    r'(?:Config\.Define\s*(?:<[^>]*>)?\s*\(\s*(?:isAdmin\s*:\s*)?\w+\s*,'
    r'|\b(?:config|TextEntryConfig)\s*(?:<[^>]*>)?\s*\()'
    r'\s*"([^"]*)"\s*,\s*(?:"([^"]*)"|nameof\(([\w.]+)\))')
ORDINAL = re.compile(r"^\s*\d+(?:\.\d+)*\s*-\s*(?=\S)")


def normalize(name):
    leading = "_" if name.startswith("_") else ""
    parts = [p for p in re.split(r"[^A-Za-z0-9]+", name) if p]
    return leading + "".join(p[0].upper() + p[1:] for p in parts)


def section(name):
    if not name.strip():
        return "General"
    return normalize(ORDINAL.sub("", name, count=1)) or "General"


def key(name):
    return normalize(name)


bad = []
pairs = {}
count = 0
for dirpath, dirnames, filenames in os.walk(ROOT):
    dirnames[:] = [d for d in dirnames if d not in ("bin", "obj", ".git", "Libs")]
    for filename in filenames:
        if not filename.endswith(".cs"):
            continue
        path = os.path.join(dirpath, filename)
        where = os.path.relpath(path, ROOT)
        text = open(path, encoding="utf-8", errors="replace").read()
        for sec, literal_key, nameof_key in BIND.findall(text):
            count += 1
            k = literal_key if literal_key else nameof_key.split(".")[-1]
            if "{" not in sec and section(sec) != sec:
                bad.append((where, "section", sec, section(sec)))
            if literal_key and "{" not in k and key(k) != k:
                bad.append((where, "key", k, key(k)))
            if "{" in sec or "{" in k:
                continue
            slot = (section(sec), key(k))
            if slot in pairs and pairs[slot] != (sec, k):
                bad.append((where, "collision", "%s/%s" % (sec, k), "%s/%s" % pairs[slot]))
            pairs[slot] = (sec, k)

print("checked %d bind call sites" % count)
for where, label, name, want in bad:
    print("BAD %-9s %-40s -> %-40s %s" % (label, name, want, where))
print()
print("verdict:", "CONFIG NAMES WOULD BE RENAMED" if bad else "every config name is already normalized")
sys.exit(1 if bad else 0)
