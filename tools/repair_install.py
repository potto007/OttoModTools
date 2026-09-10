"""Make an installed mod folder match the package it claims to be.

r2modman's local-import path extracts a package without its directories, so
Translations/<Language>/*.json land at the folder root and Jotunn finds none of
them. Nothing logs a fault, because a missing translation falls back to the key.
Its Thunderstore install path keeps the tree, so this only affects a local import.

This restores the layout and nothing else:

  - it writes only paths the package defines, with the package's bytes
  - it removes a flattened duplicate only once the correct path holds the same
    content, so nothing is deleted before its replacement exists
  - it never touches mm_v2_manifest.json, mods.yml, or any file the package does
    not name, so the mod manager's records stay true

The records stay true because the version does not change. Only the layout does.

Usage: repair_install.py <package.zip> <installed folder> [--apply]
       Without --apply it reports what it would do and changes nothing.
"""
import hashlib
import os
import sys
import zipfile


def digest(data):
    return hashlib.sha256(data).hexdigest()


def main(zip_path, install_dir, apply_changes):
    if not os.path.isdir(install_dir):
        print("FAIL  install folder does not exist: %s" % install_dir)
        return 1

    with zipfile.ZipFile(zip_path) as archive:
        package = {i.filename.replace("\\", "/"): archive.read(i)
                   for i in archive.infolist() if not i.is_dir()}

    actions = []
    for name, want in sorted(package.items()):
        target = os.path.join(install_dir, name.replace("/", os.sep))
        if os.path.exists(target):
            with open(target, "rb") as handle:
                if digest(handle.read()) == digest(want):
                    continue
            actions.append(("REWRITE", name, target))
        else:
            actions.append(("RESTORE", name, target))

    # A flattened copy is only removable once the real path holds the same bytes.
    strays = []
    wanted_roots = {os.path.basename(n) for n in package if "/" in n}
    for entry in sorted(os.listdir(install_dir)):
        full = os.path.join(install_dir, entry)
        if not os.path.isfile(full) or entry not in wanted_roots:
            continue
        origin = [n for n in package if os.path.basename(n) == entry and "/" in n]
        if len(origin) != 1:
            continue
        strays.append((entry, full, origin[0]))

    if not actions and not strays:
        print("nothing to repair: the install already matches the package")
        return 0

    for kind, name, _ in actions:
        print("%-8s %s" % (kind, name))
    for entry, _, origin in strays:
        print("%-8s %s  (belongs at %s)" % ("STRAY", entry, origin))

    if not apply_changes:
        print()
        print("dry run. Re-run with --apply to make these changes.")
        return 1

    for kind, name, target in actions:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "wb") as handle:
            handle.write(package[name])

    removed = 0
    for entry, full, origin in strays:
        target = os.path.join(install_dir, origin.replace("/", os.sep))
        if not os.path.exists(target):
            print("KEEP     %s, because %s is still missing" % (entry, origin))
            continue
        with open(target, "rb") as handle:
            if digest(handle.read()) != digest(package[origin]):
                print("KEEP     %s, because %s does not match the package" % (entry, origin))
                continue
        os.remove(full)
        removed += 1

    print()
    print("repaired %d file(s), removed %d flattened duplicate(s)" % (len(actions), removed))
    return 0


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--apply"]
    if len(args) != 2:
        print(__doc__)
        raise SystemExit(2)
    raise SystemExit(main(args[0], args[1], "--apply" in sys.argv))
