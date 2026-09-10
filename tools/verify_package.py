"""Check a built Thunderstore package before it is installed or published.

A published version is permanent, so every one of these must hold at build time
rather than after someone reports a symptom.

Usage: verify_package.py <package.zip> [--assembly <Name.dll>]
"""
import json
import os
import re
import struct
import subprocess
import sys
import zipfile

REQUIRED = ["manifest.json", "icon.png", "README.md", "CHANGELOG.md", "LICENSE.txt"]
ILSPY = os.path.expanduser("~/.dotnet/tools/ilspycmd")


def png_size(data):
    """Width and height from the PNG IHDR chunk."""
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    return struct.unpack(">II", data[16:24])


def main(zip_path):
    problems = []
    with zipfile.ZipFile(zip_path) as archive:
        names = [i.filename.replace("\\", "/") for i in archive.infolist() if not i.is_dir()]

        for required in REQUIRED:
            if required not in names:
                problems.append("MISSING    %s is not in the package" % required)

        dlls = [n for n in names if n.endswith(".dll")]
        if len(dlls) != 1:
            problems.append("ASSEMBLY   expected exactly one DLL, found %d" % len(dlls))

        if "manifest.json" in names:
            manifest = json.loads(archive.read("manifest.json").decode("utf-8-sig"))
            version = manifest.get("version_number", "")
            if not re.fullmatch(r"\d+\.\d+\.\d+", version):
                problems.append("VERSION    manifest version_number is %r" % version)
        else:
            manifest, version = {}, ""

        if "icon.png" in names:
            size = png_size(archive.read("icon.png"))
            if size != (256, 256):
                problems.append("ICON       icon.png is %s, Thunderstore requires 256x256"
                                % (size,))

        # Jotunn reads Translations/<Language>/<file>.json beside the DLL. A flat
        # json at the package root is silently never loaded.
        for name in names:
            if not name.endswith(".json") or name == "manifest.json":
                continue
            parts = name.split("/")
            if len(parts) != 3 or parts[0] != "Translations":
                problems.append("LAYOUT     %s is not Translations/<Language>/<file>.json"
                                % name)
                continue
            raw = archive.read(name)
            if raw.startswith(b"\xef\xbb\xbf"):
                problems.append("BOM        %s starts with a byte order mark" % name)
            try:
                json.loads(raw.decode("utf-8-sig"))
            except ValueError as error:
                problems.append("JSON       %s does not parse: %s" % (name, error))

        for name in names:
            if name.endswith(".old"):
                problems.append("STALE      %s is a disabled-file leftover" % name)

        # The assembly must declare the version the manifest claims.
        if dlls and version and os.path.exists(ILSPY):
            import tempfile
            with tempfile.TemporaryDirectory() as work:
                archive.extract(dlls[0], work)
                out = subprocess.run([ILSPY, os.path.join(work, dlls[0])],
                                     capture_output=True, text=True).stdout
            found = re.search(r'BepInPlugin\("[^"]+",\s*"[^"]+",\s*"([^"]+)"', out)
            if not found:
                problems.append("ASSEMBLY   no BepInPlugin attribute found in %s" % dlls[0])
            elif found.group(1) != version:
                problems.append("DRIFT      %s declares %s, manifest says %s"
                                % (dlls[0], found.group(1), version))

    print("package: %s" % os.path.basename(zip_path))
    print()
    for problem in problems:
        print(problem)
    print()
    if problems:
        print("verdict: PACKAGE IS NOT SHIPPABLE (%d problems)" % len(problems))
        return 1
    print("verdict: package is shippable")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
