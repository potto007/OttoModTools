"""Will a Debug build land in the folder the game loads, and only there?

It asks MSBuild for the mod's resolved properties, so it sees where the build will
really copy, not what environment.props appears to say.

  MANAGER    BepInExPath is not a Gale profile. r2modman was replaced on 2026-09-11,
             and a copy into its old profile succeeds and is never loaded
  INACTIVE   the profile is not the one Gale launches
  NO FOLDER  CopyOutputDLLPath does not exist. The copy target's Exists() condition
             then skips the copy, and the build still succeeds
  STALE      the game loads the DLL from a different plugin folder than the build
             writes, such as the unprefixed folder a local import creates
  DUPLICATE  more than one live copy of the DLL under plugins/
  DISABLED   the target folder holds only Gale's *.old copy: the mod is disabled
Notes:
  SWAPPED    the folder's DLL declares a different version than its manifest.json,
             which is what a Debug copy leaves. Gale shows the manifest's version
  RUNNING    Valheim is running and holds the DLL open, so the copy will fail

Usage: check_deploy.py <repo dir>
"""
import json
import os
import subprocess
import sys

import gale

PROPERTIES = ["BepInExPath", "CopyOutputDLLPath", "TargetFileName"]


def properties(project):
    command = [gale.DOTNET, "msbuild", project, "-p:Configuration=Debug"]
    command += ["-getProperty:%s" % name for name in PROPERTIES]
    try:
        out = subprocess.run(command, capture_output=True, text=True, timeout=300)
    except (OSError, subprocess.TimeoutExpired) as error:
        return None, str(error)
    if out.returncode != 0:
        return None, (out.stdout + out.stderr).strip()[-600:]
    return json.loads(out.stdout)["Properties"], None


def same(a, b):
    return os.path.normpath(a) == os.path.normpath(b)


def main(repo):
    project = gale.project_file(repo)
    if not project:
        print("FAIL  no .csproj in %s" % repo)
        return 1
    props, error = properties(project)
    if props is None:
        print("FAIL  MSBuild could not evaluate %s\n%s" % (project, error))
        return 1

    bepinex = props["BepInExPath"].rstrip("/")
    target = props["CopyOutputDLLPath"].rstrip("/")
    dll = props["TargetFileName"]
    profile = os.path.dirname(bepinex)
    plugins = os.path.join(bepinex, "plugins")
    problems = []
    notes = []

    if not same(os.path.dirname(profile), gale.PROFILES):
        problems.append(("MANAGER", "BepInExPath is %s, not a profile under %s"
                         % (bepinex, gale.PROFILES)))
    else:
        active = gale.active_profile()
        if active is None:
            notes.append(("ACTIVE", "could not read Gale's active profile"))
        elif not same(active, profile):
            problems.append(("INACTIVE", "the build copies into profile %s, Gale launches %s"
                             % (os.path.basename(profile), os.path.basename(active))))

    if not target:
        problems.append(("NO FOLDER", "CopyOutputDLLPath is empty, so Debug builds copy nothing"))
    elif not os.path.isdir(target):
        problems.append(("NO FOLDER", "%s does not exist, so a Debug build skips the copy"
                         % target))

    if os.path.isdir(plugins):
        live, disabled = gale.live_copies(plugins, dll)
        rel = lambda path: os.path.relpath(path, plugins)
        target_live = bool(target) and any(same(path, target) for path in live)
        if len(live) > 1:
            problems.append(("DUPLICATE", "%s is live in %s" % (dll, ", ".join(map(rel, live)))))
        if live and target and not target_live:
            problems.append(("STALE", "the game loads %s from %s, the build writes to %s"
                             % (dll, ", ".join(map(rel, live)), rel(target))))
        if target and not target_live and any(same(path, target) for path in disabled):
            problems.append(("DISABLED", "%s holds only %s.old: Gale has disabled the mod"
                             % (rel(target), dll)))
        if not live and not disabled:
            notes.append(("ABSENT", "no copy of %s anywhere under plugins/" % dll))
        if target_live:
            declared = gale.plugin_version(os.path.join(target, dll))
            shown = gale.manifest_version(target)
            if declared and shown and declared[2] != shown:
                notes.append(("SWAPPED", "%s declares %s, manifest.json says %s. Gale shows %s"
                              % (dll, declared[2], shown, shown)))
    elif not any(kind == "MANAGER" for kind, _ in problems):
        problems.append(("NO FOLDER", "%s does not exist" % plugins))

    if gale.game_running():
        notes.append(("RUNNING", "Valheim is running and holds %s open" % dll))

    print("project : %s" % project)
    print("profile : %s" % profile)
    print("copy to : %s" % (target or "(nothing)"))
    print()
    for kind, detail in problems + notes:
        print("%-10s %s" % (kind, detail))
    print()
    if problems:
        print("verdict: A DEBUG BUILD WILL NOT REACH THE GAME AS EXPECTED (%d problems)"
              % len(problems))
        return 1
    print("verdict: a Debug build lands in the folder the game loads")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
