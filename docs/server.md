# Dedicated server

The Otto mods' dedicated server, "Ottopia Chitown", runs on Winternode's WISP game
panel at `https://gcp.winternode.com/server/a48fc6f4`. The image is the
`ghcr.io/parkervcp/games:valheim` egg with the BepInEx pack. `tools/server.py`
deploys packages to it, restarts it, and checks what the launch loaded, all through
the panel's client API.

```
TOOLS=~/src/valheim/mods/OttoModTools/tools
python3 $TOOLS/server.py status
python3 $TOOLS/server.py deploy ../OttoBifrost/Thunderstore/potto007-OttoBifrost-1.3.0.zip
python3 $TOOLS/server.py deploy ../OttoBifrost/Thunderstore/potto007-OttoBifrost-1.3.0.zip --apply
python3 $TOOLS/server.py restart
python3 $TOOLS/server.py log --archive ~/valheim-logs/server
```

## The workflow

1. **Build and check the Release package**: `$TOOLS/build.sh ../OttoX release`.
2. **Plan the deploy**: `server.py deploy <zip>...`. It runs `verify_package.py` on
   `potto007-` packages (a third-party zip from Thunderstore skips it), then prints the version on the server, the files it will upload and the files it
   will delete. It refuses a package that is not shippable, one whose file name and
   manifest disagree, and a downgrade unless `--allow-downgrade` is passed.
3. **Deploy**: the same command with `--apply`.
   - Stops the server and waits until the panel reports it offline. Every player is
     disconnected, and the tool cannot tell whether anyone is playing.
   - Uploads every package file into `BepInEx/plugins/<namespace>-<name>`, then
     deletes files in that folder that the package does not hold.
   - Downloads every file back and compares its hash with the package. On any
     difference it stops there and leaves the server stopped, so a half-written
     plugin never launches.
   - Starts the server and waits for the launch (below), then runs
     `check_log.py --server` for each deployed plugin.

   A server that was already offline is left offline.
4. **Restart without deploying**: `server.py restart`. It checks every `potto007-`
   plugin on the server after the launch.
5. **Read the last launch without touching the server**: `server.py log`.

Every command ends with a verdict line (`DEPLOY PASSED`, `LAUNCH FAILED`,
`LOG PASSED`) and exits non-zero on failure.

## How a launch is confirmed

The server's `BepInEx/LogOutput.log` is rewritten at every launch, and Unity stamps
`Game server connected` in UTC. The tool takes the time the panel accepted the power
signal from the response's `Date` header. A launch counts as confirmed only when the
log holds that line stamped at or after the signal (10 s of clock skew allowed).

It fails the launch when either of these happens:

- the panel reports the server offline for 90 s straight (a restart passes through
  offline briefly);
- no such line appears within 15 minutes.

The expected plugin name and version come from the `BepInPlugin` attribute of the
DLL on the server, read with `ilspycmd`, not from the manifest.

## Configs

The server's `BepInEx/config` is kept as code in a private git repository,
`~/src/valheim/ottopia-server`, mirrored under `BepInEx/config`. It has no remote and
must never go in a public one: `permissions.yaml` holds a player Steam ID.
`SERVER_CONFIG_DIR` points the tool at another checkout.

```
python3 $TOOLS/server.py config pull              # server -> repo, then review and commit
python3 $TOOLS/server.py config diff              # exit 1 when the server has drifted
python3 $TOOLS/server.py config push              # dry run: what would change
python3 $TOOLS/server.py config push --apply      # upload, read back, settle, read again
python3 $TOOLS/server.py config push --apply --restart
```

The loop: edit a setting in the repo, commit, `config push --apply`. Anything changed
in-game through ConfigurationManager, or by a plugin that saves its own file, shows
up in `config diff`. `config pull` brings it into the repo to review and commit.

- **Settings are compared, not bytes.** BepInEx rewrites a `.cfg`'s header and
  comments whenever a plugin saves it. A `.cfg` is compared as `Section/Key = value`;
  other files as text.
- **Push refuses to drop a setting.** A setting only the server has, usually added by
  a newer plugin version, would vanish from the uploaded file. `config push` stops and
  asks for a pull first.
- **Push checks that the file stays pushed.** After uploading it reads each file back,
  waits 20 s and reads it again. A plugin that holds its settings in memory and saves
  them would overwrite the push, and `--restart` is then the way to apply it.
- **Live or restart.** OttoPay, OttoAura and OttoBifrost watch their own `.cfg` and call
  `Config.Reload()` when it changes, so their settings should apply with no restart.
  ServerSync then sends the synced ones to players. The third-party plugins were not
  checked; assume they read their file only at launch.

Validated on 2026-09-13 without writing to the server:

- a pull of all 21 files, then `diff` clean;
- a changed `LogPerformance` reported as `CHANGED` with exit 1;
- a dry-run push that listed it and changed nothing;
- a dry-run push with a missing setting refused with exit 1;
- a missing repo folder failing with exit 1.

Live reload was confirmed on 2026-09-13 with Paul's approval, using OttoBifrost's
server-only `LogPerformance`:

- `push --apply` of `true` passed both read-backs, and the server log's OttoBifrost
  `Perf` lines went from 4 to 10 over the next 30 s;
- `push --apply` of `false` stopped them, 12 to 12 over 20 s;
- `config diff` was clean afterwards, and the server never restarted.

`--restart` has not run.

## The API

The panel is WISP 3.7.11, not Pterodactyl. The docs are at
[gamepanel.notion.site](https://gamepanel.notion.site/API-Documentation-814fae5e161849f192c5626eec4359cd),
under Client API. Base URL `https://gcp.winternode.com/api/client/servers/a48fc6f4/`.
Every request carries these headers:

- `Authorization: Bearer <key>`
- `Accept: application/vnd.wisp.v1+json`
- `Content-Type: application/json`

The key is in `~/.config/mods/winternode-github_actions.pat`, or in `WISP_TOKEN` for
CI. `WISP_PANEL`, `WISP_SERVER` and `WISP_KEY_FILE` override the rest.

What was checked against the live server on 2026-09-12, and where the docs are
wrong or silent:

| Call | Behavior seen |
| --- | --- |
| `GET resources` | `status` is `running`. `query.players` is null for this server. |
| `GET files/directory?path=&per_page=25&page=` | Pagination keys are `currentPage` and `totalPages`. A missing directory answers 400. |
| `GET files/read?path=` | Text content as JSON. Used for manifests and the log. |
| `GET files/download?path=` | A signed URL. Its bytes matched the local zip's hash for `OttoBifrost.dll` 1.3.0. |
| `POST files/upload-token {"file_count": n}` | `{url, token}` for the daemon. Not in the docs; the panel's own file manager uses it. |
| `POST <url>?token=&directory=` | Multipart, one file per request, field `files`. Overwrites a file of the same name and creates missing directories. |
| `POST files/delete {"paths": [...]}` | 204, and removes a directory with its contents. The docs say `DELETE` with `path`. The panel answers 405 to `DELETE` and 422 to `path`. |
| `POST power {"signal": ...}` | `stop` and `start` confirmed by a deploy on 2026-09-13: `running` went to `offline`, then `starting`, then `running`. `restart` has not run. |

## Gotchas

- **`panel.winternode.com` is not the panel.** It sits behind Cloudflare Access and
  redirects every API call, bearer token included, to a login page.
  `panel.hostvenom.com` is not a WISP panel either.
- **Cloudflare in front of `gcp.winternode.com` answered 403 to a script that sent a
  browser user agent.** The tool sends `OttoModTools/1.0`.
- **The docs site rate-limits scripted reads.** Notion's page API returned 429 and the
  scraper got a challenge page. A real browser rendered it.
- **`BepInEx 5.4.23.5 - valheim_server (08/25/2026 ...)`** at the top of the log is
  the BepInEx build stamp, not the launch time.
- **The Unity errors at every launch are vanilla server noise**: `AsyncResourceUpload
  failed`, the missing video shaders, `Failed to play intro cinematic`.
  `check_log.py` counts only errors from a mod or naming it.
- **Other sessions deploy to this server too.** On 2026-09-12 OttoBifrost 1.3.0 was
  uploaded and the server restarted while this tool was being written.
- The OneDrive folder `My Games/Valheim/winternode` is a hand-kept mirror of the server
  and is not what the server runs. `server.py status` reads the server itself.
