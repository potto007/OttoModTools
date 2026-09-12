"""Read what the last launch of the game says about one mod.

BepInEx rewrites LogOutput.log at every launch, so only the last launch is there.
Pass --archive to keep a copy before the next launch replaces it.

  NOT LOADED  BepInEx never loaded the mod
  VERSION     it loaded a different version than --expect
  ERROR       an error or fatal entry from the mod, or one that names it. A Harmony
              patch that throws inside PatchAll is logged by BepInEx, not the mod,
              and it also skips every patch and setup step after it
  SERVER      the server runs a different version than the client loaded.
              ServerSync disconnects a client below the server's minimum
Notes:
  LOADED      the version BepInEx loaded
  RECEIVED    the version and minimum ServerSync received from the server. This line
              is the evidence a server runs the mod; the mod's own logging is not
  WARNING     warning entries from the mod
  RUNNING     the game is still running, so the log is not finished

Usage: check_log.py <plugin name> [--log FILE] [--expect VERSION] [--archive DIR]
                    [--server]
       <plugin name> is the name in BepInPlugin, as the log prints it: OttoAura,
       Ottomation.ModLib. --log defaults to the active Gale profile's log.
       --server reads a dedicated server's log, given with --log. A server has no
       ServerSync handshake and no local game, so RECEIVED, SERVER and RUNNING are
       skipped.
"""
import argparse
import datetime
import os
import re
import shutil
import sys

import gale

HEADER = re.compile(r"^\[(\w+)\s*:\s*([^\]]*?)\s*\]\s?(.*)$")


def entries(lines):
    """(level, source, text) per log entry, continuation lines folded in."""
    current = None
    for line in lines:
        found = HEADER.match(line)
        if found:
            if current:
                yield current
            current = [found.group(1), found.group(2), found.group(3)]
        elif current:
            current[2] += "\n" + line
    if current:
        yield current


def default_log():
    profile = gale.active_profile() or os.path.join(gale.PROFILES, "Default")
    return os.path.join(profile, "BepInEx", "LogOutput.log")


def main():
    parser = argparse.ArgumentParser(usage=__doc__)
    parser.add_argument("name")
    parser.add_argument("--log", default=None)
    parser.add_argument("--expect", default=None)
    parser.add_argument("--archive", default=None)
    parser.add_argument("--server", action="store_true")
    args = parser.parse_args()
    if args.server and not args.log:
        parser.error("--server needs --log, the server's LogOutput.log")
    log = args.log or default_log()

    if not os.path.exists(log):
        print("FAIL  no log at %s" % log)
        return 1

    written = datetime.datetime.fromtimestamp(os.path.getmtime(log))
    with open(log, encoding="utf-8", errors="replace") as handle:
        lines = handle.read().splitlines()

    problems = []
    notes = []

    if not args.server and gale.game_running():
        notes.append(("RUNNING", "Valheim is running, so the log is still being written"))

    loaded = None
    received = None
    warnings = 0
    loading = re.compile(r"Loading \[%s ([^\]]+)\]" % re.escape(args.name))
    handshake = re.compile(r"Received %s version (\S+) and minimum version (\S+) from the "
                           r"server" % re.escape(args.name))
    for level, source, text in entries(lines):
        found = loading.search(text)
        if found and source == "BepInEx":
            loaded = found.group(1)
        found = handshake.search(text)
        if found:
            received = found.groups()
        if level in ("Error", "Fatal") and (source == args.name or args.name in text):
            first = text.splitlines()[0] if text else ""
            problems.append(("ERROR", "[%s:%s] %s" % (level, source, first[:200])))
        elif level == "Warning" and source == args.name:
            warnings += 1

    if loaded is None:
        problems.append(("NOT LOADED", "no 'Loading [%s ...]' line" % args.name))
    else:
        notes.append(("LOADED", "%s %s" % (args.name, loaded)))
        if args.expect and loaded != args.expect:
            problems.append(("VERSION", "loaded %s, expected %s" % (loaded, args.expect)))

    if args.server:
        pass
    elif received:
        notes.append(("RECEIVED", "server runs %s, minimum %s" % received))
        if loaded and received[0] != loaded:
            problems.append(("SERVER", "client loaded %s, server runs %s"
                             % (loaded, received[0])))
    else:
        notes.append(("RECEIVED", "no ServerSync line from a server: single player, no "
                      "server connection, or the mod does not sync"))
    if warnings:
        notes.append(("WARNING", "%d warning entries from %s" % (warnings, args.name)))

    if args.archive:
        os.makedirs(args.archive, exist_ok=True)
        kept = os.path.join(args.archive, "LogOutput-%s.log"
                            % written.strftime("%Y%m%d-%H%M%S"))
        shutil.copy2(log, kept)
        notes.append(("ARCHIVED", kept))

    print("log     : %s" % log)
    print("written : %s" % written.strftime("%Y-%m-%d %H:%M:%S"))
    print()
    for kind, detail in problems + notes:
        print("%-10s %s" % (kind, detail))
    print()
    if problems:
        print("verdict: %s HAS PROBLEMS IN THE LAST LAUNCH (%d)" % (args.name, len(problems)))
        return 1
    print("verdict: %s loaded cleanly in the last launch" % args.name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
