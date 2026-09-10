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

Usage: verify_install.py <package.zip> <installed folder>
"""
import hashlib
import os
import sys
import zipfile


def digest(data):
    return hashlib.sha256(data).hexdigest()[:12]


def main(zip_path, install_dir):
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
    for dirpath, dirnames, filenames in os.walk(install_dir):
        for filename in filenames:
            full = os.path.join(dirpath, filename)
            rel = os.path.relpath(full, install_dir).replace(os.sep, "/")
            with open(full, "rb") as handle:
                installed[rel] = digest(handle.read())

    # The mod manager's own record is not part of the package.
    ignored = {"mm_v2_manifest.json"}

    problems = []

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
    for kind, detail in problems:
        print("%-10s %s" % (kind, detail))
    print()
    if problems:
        print("verdict: INSTALL DOES NOT MATCH THE PACKAGE (%d problems)" % len(problems))
        return 1
    print("verdict: install matches the package exactly")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(__doc__)
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1], sys.argv[2]))
