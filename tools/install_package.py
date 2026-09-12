"""Install a built package into a profile's plugin folder for testing.

It unlinks each file before writing it. Gale installs package files as hard links
into its cache and every profile holding that version, so unzip -o or a plain copy
would rewrite all of them. It refuses while Valheim runs, because the game holds
the DLL open.

Gale's records do not change. Gale keeps listing the version it installed itself,
while the game loads what is on disk. Use check_log.py to see which version loaded.

It writes the package's paths and removes flattened duplicates the way
repair_install.py does, then runs verify_install.py. It never deletes a file the
package does not name, so a *.old copy or an EXTRA file is reported, not removed.

Usage: install_package.py <package.zip> <plugin folder> [--apply]
       Without --apply it reports what it would do and changes nothing.
"""
import os
import sys

import gale
import repair_install
import verify_install


def main(zip_path, folder, apply_changes):
    running = gale.game_running()
    if running:
        print("FAIL  Valheim is running and holds the DLL open. Quit the game first.")
        return 1
    if running is None:
        print("NOTE  could not ask Windows whether Valheim is running")

    if not os.path.isdir(folder):
        if not apply_changes:
            print("CREATE   %s" % folder)
            print()
            print("dry run. Re-run with --apply to install.")
            return 1
        os.makedirs(folder)

    status = repair_install.main(zip_path, folder, apply_changes)
    if not apply_changes:
        return status

    print()
    status = verify_install.main(zip_path, folder)
    plugins = os.path.dirname(os.path.normpath(folder))
    for dll in sorted(n for n in os.listdir(folder) if n.endswith(".dll")):
        live, _ = gale.live_copies(plugins, dll)
        others = [p for p in live if os.path.normpath(p) != os.path.normpath(folder)]
        if others:
            print("DUPLICATE  %s is also live in %s" % (dll, ", ".join(others)))
            status = 1
        declared = gale.plugin_version(os.path.join(folder, dll))
        if declared:
            print("installed  %s %s. Gale still lists the version it installed itself"
                  % (declared[1], declared[2]))
    return status


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--apply"]
    if len(args) != 2:
        print(__doc__)
        raise SystemExit(2)
    raise SystemExit(main(args[0], args[1], "--apply" in sys.argv))
