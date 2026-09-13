"""Publish test builds as GitHub pre-releases for the Ottopia playtest group.

Thunderstore and Hexium take a while to index a new version, and Gale reads no other
package source. A playtest build goes out as a pre-release on the mod's own GitHub
repository instead. playtest/ottopia-playtest.bat downloads it and installs it into
each tester's Gale profile as a local mod.

  list                        every playtest pre-release on the mod repositories
  publish <zip>... [--apply]  without --apply, the plan and nothing else. With it:
                              create or update the pre-release playtest-<version>
                              with the zip attached, delete that mod's older playtest
                              pre-releases, then download the zip back and compare
                              its hash
  retire <mod> [--apply]      delete a mod's playtest pre-releases and their tags,
                              once its version is on Thunderstore

Tags are playtest-<version>, never v<version>: a v tag starts the Thunderstore publish
workflow. The pre-release points at the default branch, and its notes carry the zip's
sha256, which the tester script checks before installing. Needs the gh CLI logged in
as the repositories' owner.
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import zipfile

import verify_package
from server import PACKAGE

OWNER = os.environ.get("PLAYTEST_OWNER", "potto007")
# The tester script reads releases without logging in, so only public repositories
# can serve playtest builds. Keep this list and the one in ottopia-playtest.bat equal.
MODS = ["OttoAura", "OttoBifrost", "OttoFuel", "OttoLens", "OttoPay", "OttoStash"]
PREFIX = "playtest-"
VERSION = re.compile(r"^(?:v|playtest-)?(\d+)\.(\d+)\.(\d+)$")


class PlaytestError(Exception):
    pass


def gh(*args):
    result = subprocess.run(["gh", *args], capture_output=True, text=True)
    if result.returncode != 0:
        raise PlaytestError("gh %s: %s" % (" ".join(args[:2]),
                                           (result.stderr or result.stdout).strip()[:300]))
    return result.stdout


def releases(mod):
    return json.loads(gh("api", "repos/%s/%s/releases?per_page=100" % (OWNER, mod)))


def tags(mod):
    return [t["name"] for t in json.loads(gh("api", "repos/%s/%s/tags?per_page=100"
                                            % (OWNER, mod)))]


def version_key(text):
    found = VERSION.match(text)
    return tuple(int(part) for part in found.groups()) if found else None


def sha256(path):
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def notes(name, version, digest):
    return ("Playtest build of %s %s. It is not on Thunderstore or Hexium.\n\n"
            "sha256: %s\n\n"
            "Testers: run playtest/ottopia-playtest.bat from %s/OttoModTools. It installs "
            "this zip into the Ottopia profile in Gale." % (name, version, digest, OWNER))


def plan(zip_path):
    """What publishing one zip would do, and the problems that forbid it."""
    base = os.path.basename(zip_path)
    found = PACKAGE.match(base)
    if not found:
        return None, ["NAME       %s is not <namespace>-<name>-<version>.zip" % base]
    name, version = found.group("name"), found.group("version")
    with zipfile.ZipFile(zip_path) as archive:
        manifest = json.loads(archive.read("manifest.json").decode("utf-8-sig"))
    problems = []
    if (manifest.get("name"), manifest.get("version_number")) != (name, version):
        problems.append("MANIFEST   manifest.json names %s %s, the file name %s %s"
                        % (manifest.get("name"), manifest.get("version_number"), name, version))
    if name not in MODS:
        problems.append("REPO       %s is not one of the public repositories the tester "
                        "script reads: %s" % (name, ", ".join(MODS)))
        return None, problems
    visibility = json.loads(gh("repo", "view", "%s/%s" % (OWNER, name),
                               "--json", "visibility"))["visibility"]
    if visibility != "PUBLIC":
        problems.append("PRIVATE    %s/%s is %s, so testers cannot download from it"
                        % (OWNER, name, visibility.lower()))

    shipped = [t for t in tags(name) if t.startswith("v") and version_key(t)]
    newest = max(shipped, key=version_key, default=None)
    if newest and version_key(newest) >= version_key(version):
        problems.append("RELEASED   %s is already at %s. A playtest build must be newer "
                        "than the latest release" % (name, newest))
    tag = PREFIX + version
    existing = [r for r in releases(name) if r["tag_name"].startswith(PREFIX)]
    newer = [r["tag_name"] for r in existing
             if version_key(r["tag_name"]) and version_key(r["tag_name"]) > version_key(version)]
    if newer:
        problems.append("NEWER      %s already has %s. Retire it first to go back"
                        % (name, ", ".join(newer)))
    update = any(r["tag_name"] == tag for r in existing)
    retire = sorted(r["tag_name"] for r in existing if r["tag_name"] != tag)

    print("%s %s: %s pre-release %s on %s/%s"
          % (name, version, "update" if update else "create", tag, OWNER, name))
    for old in retire:
        print("  RETIRE  %s" % old)
    return {"zip": zip_path, "name": name, "version": version, "tag": tag,
            "update": update, "retire": retire}, problems


def publish_one(build):
    repo = "%s/%s" % (OWNER, build["name"])
    base = os.path.basename(build["zip"])
    digest = sha256(build["zip"])
    body = notes(build["name"], build["version"], digest)
    if build["update"]:
        gh("release", "upload", build["tag"], build["zip"], "--clobber", "-R", repo)
        gh("release", "edit", build["tag"], "--prerelease", "--notes", body, "-R", repo)
    else:
        gh("release", "create", build["tag"], build["zip"], "--prerelease",
           "--title", "%s %s playtest" % (build["name"], build["version"]),
           "--notes", body, "-R", repo)
    for old in build["retire"]:
        gh("release", "delete", old, "--cleanup-tag", "--yes", "-R", repo)

    # Check what a tester will see: a pre-release, the zip, its hash in the notes.
    problems = []
    release = next((r for r in releases(build["name"]) if r["tag_name"] == build["tag"]), None)
    if release is None:
        return ["MISSING    %s has no release %s after publishing" % (repo, build["tag"])]
    if not release["prerelease"] or release["draft"]:
        problems.append("STATE      %s %s is not a published pre-release" % (repo, build["tag"]))
    if "sha256: %s" % digest not in (release["body"] or ""):
        problems.append("NOTES      %s %s notes do not carry the zip's sha256"
                        % (repo, build["tag"]))
    with tempfile.TemporaryDirectory() as work:
        gh("release", "download", build["tag"], "-p", base, "-D", work, "-R", repo)
        if sha256(os.path.join(work, base)) != digest:
            problems.append("CONTENT    the downloaded %s differs from the local zip" % base)
    left = [r["tag_name"] for r in releases(build["name"])
            if r["tag_name"].startswith(PREFIX) and r["tag_name"] != build["tag"]]
    if left:
        problems.append("RETIRE     %s still has %s" % (repo, ", ".join(left)))
    return problems


def publish(args):
    builds, refused = [], 0
    for zip_path in args.zips:
        print("=============== %s" % os.path.basename(zip_path))
        if verify_package.main(zip_path) != 0:
            refused += 1
            continue
        print()
        build, problems = plan(zip_path)
        for problem in problems:
            print(problem)
        refused += bool(problems)
        if build and not problems:
            builds.append(build)
        print()
    if refused:
        print("PUBLISH REFUSED: %d of %d packages" % (refused, len(args.zips)))
        return 1
    if not args.apply:
        print("dry run, nothing changed. --apply publishes the pre-releases, which are public.")
        return 0
    problems = []
    for build in builds:
        problems += publish_one(build)
    for problem in problems:
        print(problem)
    if problems:
        print("PUBLISH FAILED: %d problems" % len(problems))
        return 1
    print("PUBLISH PASSED: %s" % ", ".join("%s %s" % (b["name"], b["tag"]) for b in builds))
    return 0


def list_builds():
    for mod in MODS:
        builds = [r for r in releases(mod) if r["tag_name"].startswith(PREFIX)]
        if not builds:
            print("%-12s none" % mod)
        for release in builds:
            state = "draft" if release["draft"] else (
                "pre-release" if release["prerelease"] else "FULL RELEASE")
            assets = ", ".join(a["name"] for a in release["assets"]) or "no assets"
            print("%-12s %-20s %-12s %s  %s" % (mod, release["tag_name"], state,
                                                (release["published_at"] or "")[:16], assets))
    return 0


def retire(mod, apply):
    if mod not in MODS:
        print("FAIL  %s is not one of %s" % (mod, ", ".join(MODS)))
        return 1
    builds = [r["tag_name"] for r in releases(mod) if r["tag_name"].startswith(PREFIX)]
    for tag in builds:
        print("RETIRE  %s/%s %s" % (OWNER, mod, tag))
    if not builds:
        print("RETIRE DONE: %s has no playtest pre-releases" % mod)
        return 0
    if not apply:
        print("dry run, nothing changed. --apply deletes these releases and their tags.")
        return 0
    for tag in builds:
        gh("release", "delete", tag, "--cleanup-tag", "--yes", "-R", "%s/%s" % (OWNER, mod))
    left = [r["tag_name"] for r in releases(mod) if r["tag_name"].startswith(PREFIX)]
    print("RETIRE %s" % ("DONE" if not left else "FAILED: %s remain" % ", ".join(left)))
    return 1 if left else 0


def main():
    parser = argparse.ArgumentParser(usage=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list")
    publishing = sub.add_parser("publish")
    publishing.add_argument("zips", nargs="+")
    publishing.add_argument("--apply", action="store_true")
    retiring = sub.add_parser("retire")
    retiring.add_argument("mod")
    retiring.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    try:
        if args.command == "list":
            return list_builds()
        if args.command == "publish":
            return publish(args)
        return retire(args.mod, args.apply)
    except PlaytestError as error:
        print("FAIL  %s" % error)
        return 1


if __name__ == "__main__":
    sys.exit(main())
