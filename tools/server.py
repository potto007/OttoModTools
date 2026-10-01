"""Deploy mod packages to the dedicated server and check what its launch loaded.

The server runs on Winternode's WISP panel. Every call goes through the panel's
client API with the key in ~/.config/mods/winternode-github_actions.pat, or in
WISP_TOKEN when that is set. The key is never printed.

  status                   power state, every plugin and patcher folder on the server
                           with the version its manifest names, and what the last
                           launch loaded
  log [--archive DIR]      check the last launch for every potto007 plugin installed
  deploy <zip>... [--apply] [--allow-downgrade] [--archive DIR] [--progress LOG]
                           without --apply, the plan and nothing else. With it: stop
                           the server, upload each package into
                           BepInEx/plugins/<namespace>-<name>, and its patchers/ into
                           BepInEx/patchers/<namespace>-<name> as a mod manager does,
                           delete the files the package does not hold, compare every
                           file's bytes with the package, start the server, wait for
                           the launch, check it
  restart [--archive DIR] [--progress LOG]
                           restart, wait for the launch, check it
  start [--archive DIR] [--progress LOG]
                           start, wait for the launch, check it
  stop [--progress LOG]    stop, and wait until the panel reports it offline
  config pull              copy the server's BepInEx/config into the config repo
  config diff              compare the config repo with the server, setting by setting.
                           Exits 1 when they differ
  config push [--apply] [--restart]
                           without --apply, the diff. With it: upload the files that
                           differ, read them back, wait, and read them again in case a
                           plugin saved its old values over them. --restart also
                           restarts the server and compares again after the launch

The config repo is a plain folder mirroring BepInEx/config, meant to be its own git
repository: SERVER_CONFIG_DIR, default ~/src/valheim/ottopia-server/BepInEx/config.
Keep it private. permissions.yaml holds player Steam IDs.

Stopping the server disconnects every player on it, and nothing here can tell whether
anyone is playing. A launch counts as confirmed only when the server's log shows
"Game server connected" at or after the moment the panel accepted the power signal.

--progress LOG writes the work band's sidecar, LOG.progress.jsonl: the step running and
an estimate of how far through the run it is, from phase durations learned from earlier
runs. LOG is the file the command's output is tee'd to; the band reads the DONE or
FAILED the wrapper prints there. --label names the band row.

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
# BepInEx's preloader loads patchers from here only, never from a plugin folder.
PATCHERS = "/BepInEx/patchers"
LOG = "/BepInEx/LogOutput.log"
CONFIG = "/BepInEx/config"
CONFIG_DIR = os.environ.get("SERVER_CONFIG_DIR") or os.path.expanduser(
    "~/src/valheim/ottopia-server/BepInEx/config")
SETTING = re.compile(r"^([^=]+?)\s*=\s*(.*?)\s*$")
# How long a pushed file must stay as pushed before a plugin is taken not to have
# saved its old values over it.
SETTLE = 20
NAMESPACE = "potto007-"
# The work band row's name: the server's name, not its panel id.
LABEL = os.environ.get("SERVER_LABEL", "Ottopia")
# Cloudflare in front of the panel refused a browser user agent sent from a script.
AGENT = "OttoModTools/1.0"
PACKAGE = re.compile(r"^(?P<folder>[^-]+-(?P<name>.+))-(?P<version>\d+\.\d+\.\d+)\.zip$")
# Unity stamps this line in UTC.
CONNECTED = re.compile(r"(\d\d/\d\d/\d{4} \d\d:\d\d:\d\d): Game server connected")
LOADING = re.compile(r"\[Info\s*:\s*BepInEx\] Loading \[([^\]]+)\]")
PATCHER = re.compile(r"\] Loaded \d+ patcher methods? from \[([^\]]+)\]")
SKEW = datetime.timedelta(seconds=10)
OFFLINE_GRACE = 90
LAUNCH_TIMEOUT = 900
STOP_TIMEOUT = 300
TIMINGS = os.path.expanduser("~/.cache/OttoModTools/server-timings.json")


class Progress:
    """Estimated progress for the work band, one JSON line per change in a sidecar.

    Each phase is weighted by how long it is expected to take: seconds per item for
    counted phases (files, plugins), seconds in all for timed ones (a stop, a launch).
    The expectations are learned from earlier runs. done/total is the share of the
    expected run already behind, as a percentage. A timed phase creeps toward its
    estimate and holds at 95% of it until it ends. Without a log it does nothing."""

    DEFAULTS = {"file": 2.0, "check": 8.0, "stop": 30.0, "start": 150.0,
                "restart": 180.0}
    TIMED = ("stop", "start", "restart")

    def __init__(self, log, label, phases):
        """phases is [(phase, timing key, item count)], in the order they run."""
        self.path = log + ".progress.jsonl" if log else None
        self.label = label
        self.timings = dict(self.DEFAULTS)
        try:
            with open(TIMINGS) as handle:
                self.timings.update(json.load(handle))
        except (OSError, ValueError):
            pass
        self.phases = {phase: (key, count) for phase, key, count in phases}
        self.weights = {phase: self.timings[key] * (1 if key in self.TIMED else count)
                        for phase, key, count in phases}
        self.total = sum(self.weights.values()) or 1.0
        self.behind, self.phase, self.items, self.started, self.written = 0.0, None, 0, 0, 0

    def begin(self, phase, current=None):
        self.phase, self.items, self.started = phase, 0, time.time()
        self.write(current, force=True)

    def advance(self, current=None):
        self.items += 1
        self.write(current)

    def tick(self, current=None):
        self.write(current)

    def end(self):
        key, count = self.phases[self.phase]
        elapsed = time.time() - self.started
        per = elapsed if key in self.TIMED else elapsed / max(count, 1)
        self.timings[key] = round((self.timings[key] + per) / 2, 1)
        self.behind += self.weights[self.phase]
        self.phase = None
        if self.path:
            try:
                os.makedirs(os.path.dirname(TIMINGS), exist_ok=True)
                with open(TIMINGS, "w") as handle:
                    json.dump(self.timings, handle)
            except OSError:
                pass

    def write(self, current, force=False):
        if not self.path or (not force and time.time() - self.written < 1):
            return
        done = self.behind
        if self.phase:
            key, count = self.phases[self.phase]
            weight = self.weights[self.phase]
            if key in self.TIMED:
                done += min(time.time() - self.started, weight * 0.95)
            else:
                done += weight * min(self.items, count) / max(count, 1)
        left = max(self.total - done, 0)
        tail = "~%dm%02ds left" % divmod(int(left), 60)
        line = {"v": 1, "label": self.label, "phase": self.phase or "done",
                "done": min(int(100 * done / self.total), 99), "total": 100,
                "current": "%s, %s" % (current, tail) if current else tail,
                "ts": int(time.time())}
        with open(self.path, "a") as handle:
            handle.write(json.dumps(line) + "\n")
        self.written = time.time()


NO_PROGRESS = Progress(None, None, [])


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

    def upload(self, files, each=None):
        """files is [(directory, name, bytes)]. The upload overwrites a file of the same
        name and creates missing directories. One grant covers the batch, as it does
        in the panel's own file manager. each(name) runs after every file."""
        if not files:
            return
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
            if each:
                each(name)

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


def wait_state(panel, want, timeout, progress=NO_PROGRESS):
    deadline, last = time.time() + timeout, None
    while time.time() < deadline:
        state = panel.power_state()
        if state != last:
            print("  power: %s" % state)
            last = state
        progress.tick("power " + str(state))
        if state == want:
            return True
        time.sleep(5)
    return False


def wait_launch(panel, since, progress=NO_PROGRESS):
    """The log of a launch that connected at or after since, or None.

    A restart passes through offline, so offline counts as a failure only once it
    lasts OFFLINE_GRACE seconds."""
    deadline, last, offline_since = time.time() + LAUNCH_TIMEOUT, None, None
    while time.time() < deadline:
        state = panel.power_state()
        if state != last:
            print("  power: %s" % state)
            last = state
        progress.tick("power " + str(state))
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


def check_launch(panel, text, folders, archive, progress=NO_PROGRESS):
    """check_log.py --server for the plugin in each folder, and the preloader's line
    for each patcher. Returns the failure count.

    The name and version to expect come from the BepInPlugin attribute of the DLL on
    the server, which is what BepInEx itself prints."""
    with tempfile.TemporaryDirectory(prefix="server-log-") as work:
        log = os.path.join(archive or work, "LogOutput-server-%s.log"
                           % datetime.datetime.now().strftime("%Y%m%d-%H%M%S"))
        os.makedirs(os.path.dirname(log), exist_ok=True)
        with open(log, "w", encoding="utf-8") as handle:
            handle.write(text)
        return check_plugins(panel, log, folders, work, progress)


def check_patchers(text, folder, dlls):
    """The preloader names a patcher by its assembly, which is its file name here."""
    loaded = PATCHER.findall(text)
    failures = 0
    for dll in dlls:
        name = os.path.splitext(os.path.basename(dll))[0]
        found = [l for l in loaded if l.split(" ")[0] == name]
        if found:
            print("LOADED     patcher %s (%s)" % (found[0], folder))
        else:
            print("FAIL  patcher %s from %s not loaded: no 'Loaded ... patcher method "
                  "from [%s ...]' line" % (dll, folder, name))
            failures += 1
    return failures


def check_plugins(panel, log, folders, work, progress=NO_PROGRESS):
    failures = 0
    with open(log, encoding="utf-8") as handle:
        text = handle.read()
    for folder in folders:
        progress.advance(folder)
        dlls = sorted(n for n in (panel.walk(PLUGINS + "/" + folder) or {})
                      if n.endswith(".dll"))
        patchers = sorted(n for n in (panel.walk(PATCHERS + "/" + folder) or {})
                          if n.endswith(".dll"))
        if patchers:
            print("=============== %s" % folder)
            failures += check_patchers(text, folder, patchers)
            if not dlls:
                continue
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

    name = found.group("folder")
    folder = PLUGINS + "/" + name
    targets = []
    for target, files in placements(name, package).items():
        on_server = panel.walk(target)
        targets.append({"folder": target, "files": files, "remove": False,
                        "extras": sorted(set(on_server or {}) - set(files))})
        if target == folder:
            server = on_server
    # A version that drops its patchers must not leave the old ones loading.
    if PATCHERS + "/" + name not in placements(name, package) and \
            panel.walk(PATCHERS + "/" + name) is not None:
        targets.append({"folder": PATCHERS + "/" + name, "files": {}, "remove": True,
                        "extras": []})
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

    print("%s: server %s -> package %s" % (name,
                                          current or ("absent" if server is None else "unknown"),
                                          found.group("version")))
    for target in targets:
        if target["remove"]:
            print("  REMOVE  %s, the package holds no patchers" % target["folder"])
            continue
        files = target["files"]
        print("  UPLOAD  %d files, %d bytes, into %s" % (len(files),
                                                         sum(map(len, files.values())),
                                                         target["folder"]))
        for extra in target["extras"]:
            print("  DELETE  %s/%s" % (target["folder"], extra))
    return {"name": name, "targets": targets, "problems": problems}


def placements(name, package):
    """{server folder: {path in it: bytes}} for one package. Its patchers/ go to
    BepInEx/patchers/<name>, where a mod manager puts them and the preloader looks;
    everything else, manifest included, to BepInEx/plugins/<name>."""
    found = {PLUGINS + "/" + name: {}}
    for path, data in package.items():
        if path.startswith("patchers/"):
            found.setdefault(PATCHERS + "/" + name, {})[path[len("patchers/"):]] = data
        else:
            found[PLUGINS + "/" + name][path] = data
    return found


def verify_folder(panel, folder, package, progress=NO_PROGRESS):
    """Every package file present with the package's bytes, and nothing else."""
    problems = []
    server = panel.walk(folder) or {}
    for name, data in sorted(package.items()):
        progress.advance(name)
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
        # verify_package holds our own builds to our layout. A third-party package
        # (LICENSE, not LICENSE.txt) ships as its author published it.
        if not os.path.basename(zip_path).startswith(NAMESPACE):
            print("third-party package, build checks skipped")
        elif verify_package.main(zip_path) != 0:
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
    targets = [t for r in plans for t in r["targets"]]
    count = sum(len(t["files"]) for t in targets)
    progress = Progress(args.progress, args.label or "%s deploy" % LABEL,
                        ([("stop", "stop", 1)] if was != "offline" else [])
                        + [("upload", "file", count), ("verify", "file", count)]
                        + ([("launch", "start", 1), ("check", "check", len(plans))]
                           if was != "offline" else []))
    if was != "offline":
        progress.begin("stop", "power " + str(was))
        panel.signal("stop")
        if not wait_state(panel, "offline", STOP_TIMEOUT, progress):
            print("DEPLOY FAILED: the server did not stop within %d s, nothing uploaded"
                  % STOP_TIMEOUT)
            return 1

        progress.end()

    problems = []
    progress.begin("upload")
    for target in targets:
        folder, files = target["folder"], target["files"]
        if target["remove"]:
            panel.delete([folder])
            continue
        panel.upload([((folder + "/" + os.path.dirname(name)).rstrip("/"),
                       os.path.basename(name), data) for name, data in files.items()],
                     progress.advance)
        if target["extras"]:
            panel.delete([folder + "/" + name for name in target["extras"]])
    progress.end()
    progress.begin("verify")
    for target in targets:
        if target["remove"]:
            if panel.walk(target["folder"]) is not None:
                problems.append("LEFT       %s is still there" % target["folder"])
            continue
        problems += verify_folder(panel, target["folder"], target["files"], progress)
    progress.end()
    for problem in problems:
        print(problem)
    if problems:
        print("DEPLOY FAILED: %d problems. The server is left stopped so it cannot launch "
              "a half-written plugin; rerun, or start it with server.py start"
              % len(problems))
        return 1
    print("uploaded and verified byte for byte: %s" % ", ".join(r["name"] for r in plans))

    if was == "offline":
        print("DEPLOY PASSED: the server was offline before, so it was not started")
        return 0
    progress.begin("launch")
    text = wait_launch(panel, panel.signal("start"), progress)
    if text is None:
        return 1
    progress.end()
    progress.begin("check")
    failures = check_launch(panel, text, [r["name"] for r in plans], args.archive, progress)
    progress.end()
    print("DEPLOY %s" % ("PASSED" if not failures else "FAILED: %d plugins" % failures))
    return 1 if failures else 0


def launch(panel, signal, archive, progress_log=None, label=None):
    folders = potto_folders(panel)
    progress = Progress(progress_log, label or "%s %s" % (LABEL, signal),
                        [("launch", signal, 1), ("check", "check", len(folders))])
    progress.begin("launch")
    text = wait_launch(panel, panel.signal(signal), progress)
    if text is None:
        return 1
    progress.end()
    progress.begin("check")
    failures = check_launch(panel, text, folders, archive, progress)
    progress.end()
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
    for entry in panel.listdir(PATCHERS) or []:
        if entry["type"] == "directory":
            print("  %-40s patchers" % entry["name"])
    text = panel.read_text(LOG)
    print()
    connected = CONNECTED.findall(text)
    print("last launch: %s" % ("Game server connected %s UTC" % connected[-1] if connected
                               else "no Game server connected line"))
    for loaded in PATCHER.findall(text):
        print("  patcher %s" % loaded)
    for loaded in LOADING.findall(text):
        print("  loaded %s" % loaded)
    return 0


def settings(name, text):
    """What a config file says, without what BepInEx rewrites on every save.

    A .cfg becomes {"Section/Key": value}: comments, blank lines and the "created by
    plugin vX" header are dropped, because a plugin rewrites them whenever it saves.
    Any other file is compared as text with trailing whitespace removed."""
    text = text.lstrip("\ufeff")
    if not name.endswith(".cfg"):
        return {"(text)": "\n".join(line.rstrip() for line in text.strip().splitlines())}
    section, values = "", {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("[") and stripped.endswith("]"):
            section = stripped[1:-1]
            continue
        found = SETTING.match(stripped)
        if found:
            values["%s/%s" % (section, found.group(1))] = found.group(2)
    return values


def local_configs(directory):
    files = {}
    for dirpath, dirnames, filenames in os.walk(directory):
        dirnames[:] = [d for d in dirnames if d != ".git"]
        for filename in filenames:
            full = os.path.join(dirpath, filename)
            rel = os.path.relpath(full, directory).replace(os.sep, "/")
            with open(full, encoding="utf-8") as handle:
                files[rel] = handle.read()
    return files


def config_changes(panel, directory):
    """({file: [(setting, server value, repo value)]}, files only on the server).

    A server value of None is a setting the repo adds; a repo value of None is one only
    the server has, usually added by a newer plugin version since the last pull."""
    local = local_configs(directory)
    remote = panel.walk(CONFIG) or {}
    changes = {}
    for name, text in sorted(local.items()):
        mine = settings(name, text)
        theirs = settings(name, panel.read_text(CONFIG + "/" + name)) if name in remote else {}
        differing = [(key, theirs.get(key), mine.get(key))
                     for key in sorted(set(mine) | set(theirs)) if mine.get(key) != theirs.get(key)]
        if name not in remote:
            differing = [("(file)", None, "new file")]
        if differing:
            changes[name] = differing
    return changes, sorted(set(remote) - set(local))


def print_changes(changes, untracked):
    for name, differing in changes.items():
        print(name)
        for key, theirs, mine in differing:
            if mine is None:
                print("  SERVER ONLY %s = %s" % (key, theirs))
            elif key == "(text)":
                print("  TEXT        differs")
            else:
                print("  %-11s %s: server %s -> repo %s"
                      % ("NEW" if theirs is None else "CHANGED", key, theirs, mine))
    for name in untracked:
        print("UNTRACKED   %s is on the server and not in the repo" % name)


def config_pull(panel, directory):
    remote = panel.walk(CONFIG) or {}
    local = local_configs(directory) if os.path.isdir(directory) else {}
    counts = {"NEW": 0, "UPDATED": 0, "SAME": 0}
    for name in sorted(remote):
        text = panel.read_text(CONFIG + "/" + name)
        kind = "NEW" if name not in local else "SAME" if local[name] == text else "UPDATED"
        counts[kind] += 1
        if kind != "SAME":
            path = os.path.join(directory, name)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(text)
            print("%-8s %s" % (kind, name))
    for name in sorted(set(local) - set(remote)):
        print("NOT ON SERVER %s (left in the repo)" % name)
    print("PULL DONE: %d new, %d updated, %d unchanged, into %s. Review with git diff and commit."
          % (counts["NEW"], counts["UPDATED"], counts["SAME"], directory))
    return 0


def config_diff(panel, directory):
    changes, untracked = config_changes(panel, directory)
    print_changes(changes, untracked)
    print("DIFF %s" % ("CLEAN: the server matches the repo" if not changes
                       else "DRIFT: %d files differ" % len(changes)))
    return 1 if changes else 0


def config_push(panel, directory, apply, restart):
    changes, untracked = config_changes(panel, directory)
    print_changes(changes, untracked)
    if not changes:
        print("PUSH DONE: nothing to push, the server matches the repo")
        return 0
    stale = sorted(name for name, differing in changes.items()
                   if any(mine is None for key, theirs, mine in differing))
    if stale:
        print("PUSH REFUSED: %s hold settings only the server has. Pushing would drop "
              "them. Run config pull, review, and commit first." % ", ".join(stale))
        return 1
    if not apply:
        print("dry run, nothing changed. --apply uploads %d files." % len(changes))
        return 0

    local = local_configs(directory)
    for name in changes:
        panel.upload([((CONFIG + "/" + os.path.dirname(name)).rstrip("/"),
                       os.path.basename(name), local[name].encode("utf-8"))])

    def unequal():
        return sorted(name for name in changes if settings(name, local[name])
                      != settings(name, panel.read_text(CONFIG + "/" + name)))

    wrong = unequal()
    if wrong:
        print("PUSH FAILED: read back differs for %s" % ", ".join(wrong))
        return 1
    print("uploaded and read back: %s" % ", ".join(changes))
    time.sleep(SETTLE)
    reverted = unequal()
    if reverted:
        print("PUSH FAILED: within %d s a plugin saved other values over %s. It keeps "
              "its settings in memory; push again with --restart." % (SETTLE, ", ".join(reverted)))
        return 1
    if not restart:
        print("PUSH DONE: files in place after %d s. A plugin that watches its file (OttoPay, "
              "OttoAura, OttoBifrost) applies them now; others at the next launch." % SETTLE)
        return 0
    if wait_launch(panel, panel.signal("restart")) is None:
        return 1
    reverted = unequal()
    print("PUSH %s" % ("DONE: the pushed settings survived the restart" if not reverted
                       else "FAILED: after the restart %s differ" % ", ".join(reverted)))
    return 1 if reverted else 0


def main():
    parser = argparse.ArgumentParser(usage=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status")
    configuring = sub.add_parser("config")
    configuring.add_argument("action", choices=["pull", "diff", "push"])
    configuring.add_argument("--dir", default=CONFIG_DIR)
    configuring.add_argument("--apply", action="store_true")
    configuring.add_argument("--restart", action="store_true")
    sub.add_parser("log").add_argument("--archive", default=None)
    for name in ("restart", "start"):
        sub.add_parser(name).add_argument("--archive", default=None)
    sub.add_parser("stop")
    deploying = sub.add_parser("deploy")
    deploying.add_argument("zips", nargs="+")
    deploying.add_argument("--apply", action="store_true")
    deploying.add_argument("--allow-downgrade", action="store_true")
    deploying.add_argument("--archive", default=None)
    for name in ("restart", "start", "stop", "deploy"):
        sub.choices[name].add_argument("--progress", default=None, metavar="LOG")
        sub.choices[name].add_argument("--label", default=None)
    args = parser.parse_args()

    try:
        panel = Panel()
        if args.command == "status":
            return status(panel)
        if args.command == "config":
            if args.action == "pull":
                return config_pull(panel, args.dir)
            if not os.path.isdir(args.dir):
                print("FAIL  no config repo at %s. Run config pull first." % args.dir)
                return 1
            if args.action == "diff":
                return config_diff(panel, args.dir)
            return config_push(panel, args.dir, args.apply, args.restart)
        if args.command == "log":
            failures = check_launch(panel, panel.read_text(LOG), potto_folders(panel),
                                    args.archive)
            print("LOG %s" % ("PASSED" if not failures else "FAILED: %d plugins" % failures))
            return 1 if failures else 0
        if args.command == "deploy":
            return deploy(panel, args)
        if args.command == "stop":
            progress = Progress(args.progress, args.label or "%s stop" % LABEL,
                                [("stop", "stop", 1)])
            progress.begin("stop")
            panel.signal("stop")
            stopped = wait_state(panel, "offline", STOP_TIMEOUT, progress)
            if stopped:
                progress.end()
            print("STOP %s" % ("PASSED" if stopped else "FAILED: still not offline after "
                               "%d s" % STOP_TIMEOUT))
            return 0 if stopped else 1
        return launch(panel, args.command, args.archive, args.progress, args.label)
    except PanelError as error:
        print("FAIL  %s" % error)
        return 1


if __name__ == "__main__":
    sys.exit(main())
