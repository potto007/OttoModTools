"""Find the build-template faults that make a safeguard skip itself without a word.

Every Otto mod's .csproj grew from one template, so a fault in it is in every mod.
Each of these was hit in a local test loop on 2026-09-12.

  IN-PLACE   the Debug copy target copies into the profile without deleting first.
             Gale's files are hard links into its cache, and a Copy that overwrites
             one changes the cache and every profile linked to it
  SILENT     the Debug copy target skips when CopyOutputDLLPath is missing, and no
             Warning says so. A renamed plugin folder leaves the old DLL in play
  WORKTREE   the published-version guard reads .git/refs/tags and .git/packed-refs.
             In a git worktree .git is a file, so the guard never fires and a
             published version can be repacked
  UNCHECKED  verify_package.py runs only if a relative path to it exists, and
             nothing fails when it does not. From a worktree the package check is
             skipped and the build still succeeds
Notes:
  QUIET      the package check's output is logged below the default verbosity, so
             a passing build shows nothing of what it checked
  FORK       the build runs a copy of verify_package.py outside OttoModTools

docs/local-testing.md has the template fix for each.

Usage: check_template.py <repo dir>
"""
import os
import re
import sys

import gale


def main(repo):
    project = gale.project_file(repo)
    if not project:
        print("FAIL  no .csproj in %s" % repo)
        return 1
    with open(project, encoding="utf-8-sig") as handle:
        text = re.sub(r"<!--.*?-->", "", handle.read(), flags=re.S)

    problems = []
    notes = []

    copy = re.search(r'<Target\s+Name="CopyOutputDLL"([^>]*)>(.*?)</Target>', text, re.S)
    if copy:
        condition, body = copy.groups()
        copies = len(re.findall(r"<Copy\b", body))
        deletes = len(re.findall(r"<Delete\b", body))
        if deletes < copies:
            problems.append(("IN-PLACE", "CopyOutputDLL has %d Copy and %d Delete. Delete "
                             "what each Copy replaces before copying" % (copies, deletes)))
        warned = re.search(r"<(?:Warning|Error)\b[^>]*!Exists\('\$\(CopyOutputDLLPath\)'\)",
                           text)
        if "Exists('$(CopyOutputDLLPath)')" in condition and not warned:
            problems.append(("SILENT", "CopyOutputDLL skips when CopyOutputDLLPath is "
                             "missing, and nothing warns"))
    else:
        notes.append(("NO COPY", "no CopyOutputDLL target, so Debug builds reach no profile"))

    if ".git/refs/tags" in text or ".git/packed-refs" in text:
        problems.append(("WORKTREE", "the published-version guard reads .git/refs/tags and "
                         ".git/packed-refs, which a worktree does not have"))

    execs = [m.group(0) for m in re.finditer(r"<Exec\b[^>]*?/>|<Exec\b.*?</Exec>", text, re.S)
             if re.search(r"verify_package|VerifyPackage", m.group(0))]
    if not execs:
        problems.append(("UNCHECKED", "no Exec runs verify_package.py"))
    else:
        guarded = re.search(r'<Error\b[^>]*Condition="[^"]*!Exists\([^"]*'
                            r'(?:verify_package|VerifyPackage|OttoModTools)', text)
        if any(re.search(r"Condition=\"[^\"]*Exists\(", e) for e in execs) and not guarded:
            problems.append(("UNCHECKED", "verify_package.py runs only if its path exists, "
                             "and nothing fails when it does not"))
        if not any('StandardOutputImportance="high"' in e for e in execs):
            notes.append(("QUIET", "the package check prints below the default verbosity"))
        if re.search(r"Ottomation_ModLib/tools/verify_package|\$\(MSBuildProjectDirectory\)"
                     r"/tools/verify_package", text):
            notes.append(("FORK", "runs Ottomation_ModLib's copy of verify_package.py"))

    print("project : %s" % project)
    print()
    for kind, detail in problems + notes:
        print("%-10s %s" % (kind, detail))
    print()
    if problems:
        print("verdict: THE BUILD TEMPLATE CAN SKIP A SAFEGUARD SILENTLY (%d problems)"
              % len(problems))
        return 1
    print("verdict: no silent skip found in the build template")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
