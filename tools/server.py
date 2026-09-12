"""Deploy mod packages to the dedicated server and check what its launch loaded.

The server runs on Winternode's WISP panel. Every call goes through the panel's
client API with the key in ~/.config/mods/winternode-github_actions.pat, or in
WISP_TOKEN when that is set. The key is never printed.

  status                   power state, every plugin folder on the server with the
                           version its manifest names, and what the last launch loaded
  log [--archive DIR]      check the last launch for every potto007 plugin installed
  deploy <zip>... [--apply] [--allow-downgrade] [--archive DIR]
                           without --apply, the plan and nothing else. With it: stop
                           the server, upload each package into
                           BepInEx/plugins/<namespace>-<name>, delete the files the
                           package does not hold, compare every file's bytes with the
                           package, start the server, wait for the launch, check it
  restart [--archive DIR]  restart, wait for the launch, check it
  start [--archive DIR]    start, wait for the launch, check it
  stop                     stop, and wait until the panel reports it offline

Stopping the server disconnects every player on it, and nothing here can tell whether
anyone is playing. A launch counts as confirmed only when the server's log shows
"Game server connected" at or after the moment the panel accepted the power signal.

Environment: WISP_PANEL (default https://gcp.winternode.com), WISP_SERVER (default
a48fc6f4), WISP_TOKEN, WISP_KEY_FILE.
"""
import argparse
import datetime
import email.utils
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile

import gale
import verify_package

TOOLS = os.path.dirname(os.path.abspath(__file__))
PANEL = os.environ.get("WISP_PANEL", "https://gcp.winternode.com")
SERVER = os.environ.get("WISP_SERVER", "a48fc6f4")
KEY_FILE = os.environ.get("WISP_KEY_FILE") or os.path.expanduser(
    "~/.config/mods/winternode-github_actions.pat")
PLUGINS = "/BepInEx/plugins"
LOG = "/BepInEx/LogOutput.log"
NAMESPACE = "potto007-"
# Cloudflare in front of the panel refused a browser user agent sent from a script.
AGENT = "OttoModTools/1.0"
PACKAGE = re.compile(r"^(?P<folder>[^-]+-(?P<name>.+))-(?P<version>\d+\.\d+\.\d+)\.zip$")
# Unity stamps this line in UTC.
CONNECTED = re.compile(r"(\d\d/\d\d/\d{4} \d\d:\d\d:\d\d): Game server connected")
LOADING = re.compile(r"\[Info\s*:\s*BepInEx\] Loading \[([^\]]+)\]")
SKEW = datetime.timedelta(seconds=10)
OFFLINE_GRACE = 90
LAUNCH_TIMEOUT = 900
STOP_TIMEOUT = 300


class PanelError(Exception):
    pass


def api_key():
    value = os.environ.get("WISP_TOKEN", "").strip()
    if value:
        return value
    try:
        with open(KEY_FILE) as handle:
            return handle.read().strip()
    except OSError:
        raise PanelError("no API key: set WISP_TOKEN or put the key in %s" % KEY_FILE)


class Panel:
    def __init__(self):
        self.key = api_key()
        self.base = "%s/api/client/servers/%s/" % (PANEL.rstrip("/"), SERVER)

    def call(self, method, path, params=None, body=None, missing_ok=False):
        """(status, headers, body). Waits out a rate limit, raises on any other error.

        With missing_ok, a 400 or 404 comes back as a status instead of an error: the
        panel answers 400 for a directory that does not exist."""
        url = self.base + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Authorization": "Bearer " + self.key,
                   "Accept": "application/vnd.wisp.v1+json",
                   "Content-Type": "application/json", "User-Agent": AGENT}
        for attempt in range(5):
            request = urllib.request.Request(url, data=data, method=method, headers=headers)
            try:
                with urllib.request.urlopen(request, timeout=120) as response:
                    return response.status, response.headers, response.read()
            except urllib.error.HTTPError as error:
                if error.code == 429 and attempt < 4:
                    time.sleep(2 ** attempt * 2)
                    continue
                if missing_ok and error.code in (400, 404):
                    return error.code, error.headers, b""
                detail = error.read().decode(errors="replace")[:300]
                raise PanelError("%s %s: HTTP %d %s" % (method, path, error.code, detail))
            except urllib.error.URLError as error:
                raise PanelError("%s %s: %s" % (method, path, error.reason))
        raise PanelError("%s %s: still rate limited" % (method, path))

    def json(self, method, path, params=None, body=None):
        status, headers, raw = self.call(method, path, params, body)
        try:
            return json.loads(raw) if raw else {}
        except ValueError:
            raise PanelError("%s %s: the panel did not answer with JSON, so a login page "
                             "may be in the way" % (method, path))

    def power_state(self):
        return self.json("GET", "resources").get("status")

    def signal(self, name):
        """Send a power signal and return the panel's clock when it accepted it."""
        status, headers, raw = self.call("POST", "power", body={"signal": name})
        stamp = headers.get("Date")
        if stamp:
            return email.utils.parsedate_to_datetime(stamp)
        return datetime.datetime.now(datetime.timezone.utc)

    def listdir(self, path):
        """The entries of a directory, or None if it does not exist."""
        entries, page = [], 1
        while True:
            status, headers, raw = self.call(
                "GET", "files/directory", {"path": path, "per_page": 25, "page": page},
                missing_ok=True)
            if status in (400, 404):
                return None
            data = json.loads(raw)
            entries += [item["attributes"] for item in data.get("data", [])]
            pages = data.get("meta", {}).get("pagination", {})
            if pages.get("currentPage", 1) >= pages.get("totalPages", 1):
                return entries
            page += 1

    def walk(self, path):
        """{relative path: size} for every file under path, or None if it does not exist."""
        top = self.listdir(path)
        if top is None:
            return None
        files, pending = {}, [("", top)]
        while pending:
            prefix, entries = pending.pop()
            for entry in entries:
                rel = prefix + entry["name"]
                if entry["type"] == "directory":
                    pending.append((rel + "/", self.listdir(path + "/" + rel) or []))
                else:
                    files[rel] = entry["size"]
        return files

    def read_text(self, path):
        return self.json("GET", "files/read", {"path": path}).get("content", "")

    def download(self, path):
        url = self.json("GET", "files/download", {"path": path})["url"]
        request = urllib.request.Request(url, headers={"User-Agent": AGENT})
        try:
            with urllib.request.urlopen(request, timeout=300) as response:
                return response.read()
        except (urllib.error.HTTPError, urllib.error.URLError) as error:
            # The download URL is signed, so it stays out of the message.
            raise PanelError("download %s: %s" % (path, getattr(error, "code", None)
                                                  or error.reason))

    def upload(self, files):
        """files is [(directory, name, bytes)]. The upload overwrites a file of the same
        name and creates missing directories. One grant covers the batch, as it does
        in the panel's own file manager."""
        grant = self.json("POST", "files/upload-token", body={"file_count": len(files)})
        for directory, name, data in files:
            boundary = uuid.uuid4().hex
            head = ('--%s\r\nContent-Disposition: form-data; name="files"; filename="%s"\r\n'
                    'Content-Type: application/octet-stream\r\n\r\n' % (boundary, name))
            body = head.encode() + data + ("\r\n--%s--\r\n" % boundary).encode()
            url = "%s?token=%s&directory=%s" % (grant["url"], grant["token"],
                                                urllib.parse.quote(directory, safe=""))
            request = urllib.request.Request(url, data=body, method="POST", headers={
                "Content-Type": "multipart/form-data; boundary=" + boundary,
                "User-Agent": AGENT})
            try:
                with urllib.request.urlopen(request, timeout=300) as response:
                    response.read()
            except (urllib.error.HTTPError, urllib.error.URLError) as error:
                # The upload URL carries the grant token, so it stays out of the message.
                raise PanelError("upload %s/%s: %s" % (directory, name,
                                                       getattr(error, "code", None)
                                                       or error.reason))

    def delete(self, paths):
        self.call("POST", "files/delete", body={"paths": paths})


def sha(data):
    return hashlib.sha256(data).hexdigest()[:12]


def version_key(version):
    return tuple(int(part) for part in version.split("."))


def launched_after(text, since):
    """The UTC time of the first "Game server connected" at or after since, or None."""
    for found in CONNECTED.finditer(text):
        at = datetime.datetime.strptime(found.group(1), "%m/%d/%Y %H:%M:%S").replace(
            tzinfo=datetime.timezone.utc)
        if at >= since - SKEW:
            return at
    return None


def wait_state(panel, want, timeout):
    deadline, last = time.time() + timeout, None
    while time.time() < deadline:
        state = panel.power_state()
        if state != last:
            print("  power: %s" % state)
            last = state
        if state == want:
            return True
        time.sleep(5)
    return False


def wait_launch(panel, since):
    """The log of a launch that connected at or after since, or None.

    A restart passes through offline, so offline counts as a failure only once it
    lasts OFFLINE_GRACE seconds."""
    deadline, last, offline_since = time.time() + LAUNCH_TIMEOUT, None, None
    while time.time() < deadline:
        state = panel.power_state()
        if state != last:
            print("  power: %s" % state)
            last = state
        if state == "offline":
            offline_since = offline_since or time.time()
            if time.time() - offline_since > OFFLINE_GRACE:
                print("LAUNCH FAILED: offline for %d s after the signal" % OFFLINE_GRACE)
                return None
        else:
            offline_since = None
            try:
                text = panel.read_text(LOG)
            except PanelError:
                text = ""
            at = launched_after(text, since)
            if at:
                print("LAUNCH CONFIRMED: Game server connected at %s UTC"
                      % at.strftime("%Y-%m-%d %H:%M:%S"))
                return text
        time.sleep(10)
    print("LAUNCH FAILED: no Game server connected within %d s of the signal"
          % LAUNCH_TIMEOUT)
    return None


def potto_folders(panel):
    entries = panel.listdir(PLUGINS) or []
    return sorted(e["name"] for e in entries
                  if e["type"] == "directory" and e["name"].startswith(NAMESPACE))


def check_launch(panel, text, folders, archive):
    """check_log.py --server for the plugin in each folder. Returns the failure count.

    The name and version to expect come from the BepInPlugin attribute of the DLL on
    the server, which is what BepInEx itself prints."""
    with tempfile.TemporaryDirectory(prefix="server-log-") as work:
        log = os.path.join(archive or work, "LogOutput-server-%s.log"
                           % datetime.datetime.now().strftime("%Y%m%d-%H%M%S"))
        os.makedirs(os.path.dirname(log), exist_ok=True)
        with open(log, "w", encoding="utf-8") as handle:
            handle.write(text)
        return check_plugins(panel, log, folders, work)


def check_plugins(panel, log, folders, work):
    failures = 0
    for folder in folders:
        dlls = sorted(n for n in (panel.walk(PLUGINS + "/" + folder) or {})
                      if n.endswith(".dll"))
        if len(dlls) != 1:
            print("FAIL  %s holds %d DLLs, expected 1" % (folder, len(dlls)))
            failures += 1
            continue
        local = os.path.join(work, os.path.basename(dlls[0]))
        with open(local, "wb") as handle:
            handle.write(panel.download(PLUGINS + "/" + folder + "/" + dlls[0]))
        declared = gale.plugin_version(local)
        if not declared:
            print("FAIL  no BepInPlugin attribute read from %s/%s (is ilspycmd installed?)"
                  % (folder, dlls[0]))
            failures += 1
            continue
        print("=============== %s" % folder)
        sys.stdout.flush()
        result = subprocess.run([sys.executable, os.path.join(TOOLS, "check_log.py"),
                                 declared[1], "--log", log, "--expect", declared[2],
                                 "--server"])
        failures += result.returncode != 0
    return failures


def plan(panel, zip_path, allow_downgrade):
    """What deploying one package would change, and why it must not, if it must not."""
    problems = []
    found = PACKAGE.match(os.path.basename(zip_path))
    if not found:
        return {"problems": ["NAME       %s is not <namespace>-<name>-<version>.zip"
                             % os.path.basename(zip_path)]}
    with zipfile.ZipFile(zip_path) as archive:
        package = {i.filename.replace("\\", "/"): archive.read(i)
                   for i in archive.infolist() if not i.is_dir()}
    manifest = json.loads(package.get("manifest.json", b"{}").decode("utf-8-sig"))
    if (manifest.get("name"), manifest.get("version_number")) != (found.group("name"),
                                                                  found.group("version")):
        problems.append("MANIFEST   manifest.json names %s %s, the file name %s %s"
                        % (manifest.get("name"), manifest.get("version_number"),
                           found.group("name"), found.group("version")))

    folder = PLUGINS + "/" + found.group("folder")
    server = panel.walk(folder)
    current = None
    if server and "manifest.json" in server:
        try:
            current = json.loads(panel.read_text(folder + "/manifest.json")).get(
                "version_number")
        except (ValueError, PanelError):
            current = None
    if current and re.fullmatch(r"\d+\.\d+\.\d+", current):
        if version_key(found.group("version")) < version_key(current) and not allow_downgrade:
            problems.append("DOWNGRADE  the server has %s, the package is %s. Pass "
                            "--allow-downgrade to replace it anyway"
                            % (current, found.group("version")))
    extras = sorted(set(server or {}) - set(package))

    print("%s: server %s -> package %s" % (found.group("folder"),
                                          current or ("absent" if server is None else "unknown"),
                                          found.group("version")))
    print("  UPLOAD  %d files, %d bytes, into %s" % (len(package),
                                                     sum(map(len, package.values())), folder))
    for name in extras:
        print("  DELETE  %s/%s" % (folder, name))
    return {"folder": folder, "package": package, "extras": extras, "problems": problems}


def verify_folder(panel, folder, package):
    """Every package file present with the package's bytes, and nothing else."""
    problems = []
    server = panel.walk(folder) or {}
    for name, data in sorted(package.items()):
        if name not in server:
            problems.append("MISSING    %s/%s" % (folder, name))
        elif server[name] != len(data):
            problems.append("SIZE       %s/%s is %d bytes, the package %d"
                            % (folder, name, server[name], len(data)))
        else:
            got = panel.download(folder + "/" + name)
            if sha(got) != sha(data):
                problems.append("CONTENT    %s/%s is %s, the package %s"
                                % (folder, name, sha(got), sha(data)))
    for name in sorted(set(server) - set(package)):
        problems.append("EXTRA      %s/%s" % (folder, name))
    return problems


def deploy(panel, args):
    plans, refused = [], 0
    for zip_path in args.zips:
        print("=============== %s" % os.path.basename(zip_path))
        if verify_package.main(zip_path) != 0:
            refused += 1
            continue
        print()
        result = plan(panel, zip_path, args.allow_downgrade)
        for problem in result["problems"]:
            print(problem)
        refused += bool(result["problems"])
        plans.append(result)
        print()
    if refused:
        print("DEPLOY REFUSED: %d of %d packages" % (refused, len(args.zips)))
        return 1
    if not args.apply:
        print("dry run, nothing changed. --apply stops the server, which disconnects every "
              "player, then uploads, verifies and starts it.")
        return 0

    was = panel.power_state()
    print("server is %s" % was)
    if was != "offline":
        panel.signal("stop")
        if not wait_state(panel, "offline", STOP_TIMEOUT):
            print("DEPLOY FAILED: the server did not stop within %d s, nothing uploaded"
                  % STOP_TIMEOUT)
            return 1

    problems = []
    for result in plans:
        folder, package = result["folder"], result["package"]
        panel.upload([((folder + "/" + os.path.dirname(name)).rstrip("/"),
                       os.path.basename(name), data) for name, data in package.items()])
        if result["extras"]:
            panel.delete([folder + "/" + name for name in result["extras"]])
        problems += verify_folder(panel, folder, package)
    for problem in problems:
        print(problem)
    if problems:
        print("DEPLOY FAILED: %d problems. The server is left stopped so it cannot launch "
              "a half-written plugin; rerun, or start it with server.py start"
              % len(problems))
        return 1
    print("uploaded and verified byte for byte: %s"
          % ", ".join(os.path.basename(r["folder"]) for r in plans))

    if was == "offline":
        print("DEPLOY PASSED: the server was offline before, so it was not started")
        return 0
    text = wait_launch(panel, panel.signal("start"))
    if text is None:
        return 1
    failures = check_launch(panel, text, [os.path.basename(r["folder"]) for r in plans],
                            args.archive)
    print("DEPLOY %s" % ("PASSED" if not failures else "FAILED: %d plugins" % failures))
    return 1 if failures else 0


def launch(panel, signal, archive):
    text = wait_launch(panel, panel.signal(signal))
    if text is None:
        return 1
    failures = check_launch(panel, text, potto_folders(panel), archive)
    print("%s %s" % (signal.upper(), "PASSED" if not failures
                     else "FAILED: %d plugins" % failures))
    return 1 if failures else 0


def status(panel):
    print("server : %s %s" % (PANEL, SERVER))
    print("power  : %s" % panel.power_state())
    print()
    for entry in panel.listdir(PLUGINS) or []:
        if entry["type"] != "directory":
            continue
        try:
            version = json.loads(panel.read_text(
                PLUGINS + "/" + entry["name"] + "/manifest.json")).get("version_number")
        except (ValueError, PanelError):
            version = None
        print("  %-40s %s" % (entry["name"], version or "no manifest"))
    text = panel.read_text(LOG)
    print()
    connected = CONNECTED.findall(text)
    print("last launch: %s" % ("Game server connected %s UTC" % connected[-1] if connected
                               else "no Game server connected line"))
    for loaded in LOADING.findall(text):
        print("  loaded %s" % loaded)
    return 0


def main():
    parser = argparse.ArgumentParser(usage=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status")
    for name in ("log", "restart", "start"):
        sub.add_parser(name).add_argument("--archive", default=None)
    sub.add_parser("stop")
    deploying = sub.add_parser("deploy")
    deploying.add_argument("zips", nargs="+")
    deploying.add_argument("--apply", action="store_true")
    deploying.add_argument("--allow-downgrade", action="store_true")
    deploying.add_argument("--archive", default=None)
    args = parser.parse_args()

    try:
        panel = Panel()
        if args.command == "status":
            return status(panel)
        if args.command == "log":
            failures = check_launch(panel, panel.read_text(LOG), potto_folders(panel),
                                    args.archive)
            print("LOG %s" % ("PASSED" if not failures else "FAILED: %d plugins" % failures))
            return 1 if failures else 0
        if args.command == "deploy":
            return deploy(panel, args)
        if args.command == "stop":
            panel.signal("stop")
            stopped = wait_state(panel, "offline", STOP_TIMEOUT)
            print("STOP %s" % ("PASSED" if stopped else "FAILED: still not offline after "
                               "%d s" % STOP_TIMEOUT))
            return 0 if stopped else 1
        return launch(panel, args.command, args.archive)
    except PanelError as error:
        print("FAIL  %s" % error)
        return 1


if __name__ == "__main__":
    sys.exit(main())
