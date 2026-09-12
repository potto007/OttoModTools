"""Compare an installed mod folder against the package it claims to be.

This is the check that was missing. A package can be correct and the install
still wrong, because something else writes into that folder: a build target, a
hand copy, or the mod manager reconciling against its own stored file list.

Every difference is reported with the shape of the failure, not just a mismatch:

  MISSING    a file in the package is absent from the install
  FLATTENED  a file that belongs in a subdirectory sits at the folder root
  CONTENT    the file is present at the right path and the bytes differ
  DISABLED   the mod manager has renamed files to *.old
  EXTRA      a file in the install that the package does not contain

Notes describe the folder without failing it:

  LINKED     files are hard links, shared with Gale's cache and other profiles.
             Anything that writes one in place changes all of them.
  FOREIGN    a record left by another mod manager (r2modman's mm_v2_manifest.json)
  SWAPPED    with --debug-dll, the DLL differs because a Debug build replaced it

Usage: verify_install.py <package.zip> <installed folder> [--debug-dll]
       --debug-dll accepts a DLL that differs from the package, which is what a
       Debug build's copy leaves, and reports the version each side declares.
"""
import hashlib
import os
import sys
import zipfile

import gale


def digest(data):
    return hashlib.sha256(data).hexdigest()[:12]


def main(zip_path, install_dir, debug_dll=False):
    if not os.path.isdir(install_dir):
        print("FAIL  install folder does not exist: %s" % install_dir)
        return 1

    package = {}
    with zipfile.ZipFile(zip_path) as archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            package[info.filename.replace("\\", "/")] = digest(archive.read(info))

    installed = {}
    linked = []
    for dirpath, dirnames, filenames in os.walk(install_dir):
        for filename in filenames:
            full = os.path.join(dirpath, filename)
            rel = os.path.relpath(full, install_dir).replace(os.sep, "/")
            with open(full, "rb") as handle:
                installed[rel] = digest(handle.read())
            if os.stat(full).st_nlink > 1:
                linked.append(rel)

    # The mod manager's own record is not part of the package.
    ignored = {"mm_v2_manifest.json"}

    problems = []
    notes = []

    if linked:
        notes.append(("LINKED", "%d of %d files are hard links. Unlink before writing, "
                      "never overwrite in place" % (len(linked), len(installed))))
    if "mm_v2_manifest.json" in installed and install_dir.startswith(gale.GALE):
        notes.append(("FOREIGN", "mm_v2_manifest.json is r2modman's record, left in a "
                      "Gale profile"))

    disabled = sorted(name for name in installed if name.endswith(".old"))
    if disabled:
        problems.append(("DISABLED", "%d files renamed to *.old, e.g. %s"
                         % (len(disabled), disabled[0])))

    basenames = {}
    for name in installed:
        basenames.setdefault(os.path.basename(name), []).append(name)

    flattened = set()
    for name, want in sorted(package.items()):
        if name in installed:
            if installed[name] != want:
                if debug_dll and name.endswith(".dll"):
                    notes.append(("SWAPPED", swapped(zip_path, name, install_dir)))
                    continue
                problems.append(("CONTENT", "%s  package %s, install %s"
                                 % (name, want, installed[name])))
            continue
        base = os.path.basename(name)
        # A file that belongs in a subdirectory but sits at the root.
        if "/" in name and base in basenames and base in installed:
            flattened.add(base)
            problems.append(("FLATTENED", "%s is installed as %s" % (name, base)))
        else:
            problems.append(("MISSING", name))

    for name in sorted(installed):
        if name in package or name in ignored or name.endswith(".old"):
            continue
        # Suppress only a basename that really was reported as FLATTENED. A flat copy
        # sitting beside a correct tree file is not flattened, it is a leftover, and it
        # is the state a partial repair leaves behind.
        if name in flattened:
            continue
        problems.append(("EXTRA", name))

    print("package : %s (%d files)" % (os.path.basename(zip_path), len(package)))
    print("install : %s (%d files)" % (install_dir, len(installed)))
    print()
    for kind, detail in problems + notes:
        print("%-10s %s" % (kind, detail))
    print()
    if problems:
        print("verdict: INSTALL DOES NOT MATCH THE PACKAGE (%d problems)" % len(problems))
        return 1
    if any(kind == "SWAPPED" for kind, _ in notes):
        print("verdict: install matches the package apart from the Debug DLL")
    else:
        print("verdict: install matches the package exactly")
    return 0


def swapped(zip_path, name, install_dir):
    """The DLL a Debug build copied in, next to the manifest the folder still shows.

    Gale reads manifest.json and its own records, so it keeps showing the packaged
    version while the Debug build's code is what runs."""
    installed = gale.plugin_version(os.path.join(install_dir, name))
    shown = gale.manifest_version(install_dir)
    runs = installed[2] if installed else "unknown"
    detail = "%s declares %s, the folder's manifest.json says %s" % (name, runs, shown)
    if installed and shown and installed[2] != shown:
        detail += ". The mod manager shows %s while %s runs" % (shown, runs)
    return detail


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--debug-dll"]
    if len(args) != 2:
        print(__doc__)
        raise SystemExit(2)
    raise SystemExit(main(args[0], args[1], "--debug-dll" in sys.argv))
