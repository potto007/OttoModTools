"""Flag enum constants that a decompile may have resolved to the wrong member.

ilspycmd stores an enum constant as its ordinal and prints whatever member sits
at that ordinal in the assembly it was given. Decompiling a mod built against an
older Valheim, using the current Valheim, therefore yields a wrong but entirely
plausible member name. It compiles, it runs, it logs nothing, and it reads
correctly to a reviewer.

This happened in OttoUI: Valheim 1.0 added five members to GlobalKeys, so a
property named IsNoCraftCost read GlobalKeys.DeathDeleteItems, five ordinals low,
and a method named NoTeleport read GlobalKeys.PassiveMobs, also five low.

The surviving evidence is the surrounding identifier. Method names, property
names and local names are stored as strings and are never re-resolved, so they
still say what the original author meant. This compares the two.

Usage: check_enums.py <source root> [<enum type> ...]
"""
import os
import re
import subprocess
import sys

ILSPY = os.path.expanduser("~/.dotnet/tools/ilspycmd")
MANAGED = ("/mnt/c/Program Files (x86)/Steam/steamapps/common/Valheim/"
           "valheim_Data/Managed")
ASSEMBLIES = ["assembly_valheim", "assembly_utils", "assembly_guiutils"]

DEFAULT_ENUMS = ["GlobalKeys"]
REFERENCE = re.compile(r'\b([A-Z]\w*)\.(\w+)\b')
# A reference a person checked against vanilla says so, and why, on its line or the
# line above. A marker with no reason is ignored: the reason is what lets the next
# reader trust the skip.
MARKER = re.compile(r'check_enums:\s*verified\b\s*-?\s*(\S.*)?')
WORDS = re.compile(r'[A-Z][a-z]+|[A-Z]+(?![a-z])|[a-z]+')


def members(enum_name):
    """Ordered member names of an enum, from the installed game assemblies."""
    for assembly in ASSEMBLIES:
        path = os.path.join(MANAGED, assembly + ".dll")
        if not os.path.exists(path):
            continue
        out = subprocess.run([ILSPY, "-t", enum_name, path],
                             capture_output=True, text=True).stdout
        if "enum " + enum_name.split(".")[-1] not in out:
            continue
        body = out.split("{", 1)[1].rsplit("}", 1)[0]
        found = []
        for line in body.splitlines():
            line = line.strip().rstrip(",")
            if line and re.fullmatch(r"\w+", line):
                found.append(line)
        if found:
            return found
    return []


def tokens(text):
    return {word.lower() for word in WORDS.findall(text)}


def main(root, enum_names):
    findings = 0
    skipped = 0
    for enum_name in enum_names:
        order = members(enum_name)
        if not order:
            print("SKIP    could not read enum %s from the game assemblies" % enum_name)
            continue
        index = {name: position for position, name in enumerate(order)}
        print("enum %s: %d members" % (enum_name, len(order)))

        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in ("bin", "obj", ".git", "Libs")]
            for filename in filenames:
                if not filename.endswith(".cs"):
                    continue
                path = os.path.join(dirpath, filename)
                where = os.path.relpath(path, root)
                lines = open(path, encoding="utf-8", errors="replace").read().splitlines()
                for number, line in enumerate(lines):
                    # A comment is not code. Scanning one would count a marker's own
                    # explanation as a skipped reference and overstate the skip count.
                    if line.lstrip().startswith("//"):
                        continue
                    near = line + "\n" + (lines[number - 1] if number > 0 else "")
                    mark = MARKER.search(near)
                    if mark and mark.group(1) and REFERENCE.search(line):
                        skipped += 1
                        continue
                    for owner, member in REFERENCE.findall(line):
                        if owner != enum_name.split(".")[-1] or member not in index:
                            continue
                        # Identifiers near the reference still carry the author's intent.
                        window = "\n".join(lines[max(0, number - 12):number + 3])
                        context = tokens(window) - tokens(member)
                        # The reference is suspect when the surrounding code
                        # names a different member and does not name this one.
                        mine = tokens(member)
                        self_named = bool(mine) and mine <= context
                        for candidate in order:
                            if candidate == member or self_named:
                                continue
                            need = tokens(candidate)
                            strong = {w for w in need if len(w) >= 5}
                            if not strong:
                                continue
                            if need <= context or strong <= context:
                                shift = index[candidate] - index[member]
                                print("  SUSPECT %s:%d  %s.%s  "
                                      "but nearby code says %r (shift %+d)"
                                      % (where, number + 1, enum_name, member,
                                         candidate, shift))
                                findings += 1
                                break
    if skipped:
        print("skipped %d reference line(s) marked check_enums: verified" % skipped)
    print()
    if findings:
        print("verdict: %d SUSPECT ENUM CONSTANT(S) - check each against vanilla usage"
              % findings)
        return 1
    print("verdict: no enum constant contradicts its surrounding code")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1], sys.argv[2:] or DEFAULT_ENUMS))
