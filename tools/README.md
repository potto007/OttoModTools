# Harnesses

Each harness is validated against a deliberately broken input before it is
trusted. A clean run means the check ran and found nothing, which is not the same
as the tree being correct.

`../docs/local-testing.md` walks the build, install, launch and log loop these
serve, and lists what the loop has taught so far.

| Script | Answers |
| --- | --- |
| `verify_package.py` | Is this zip shippable? |
| `verify_install.py` | Does the installed folder match the package it claims to be? |
| `check_enums.py` | Did the decompile resolve an enum constant to the wrong member? |
| `check_harmony.py` | Does every Harmony patch target exist in the game installed now? |
| `check_template.py` | Can the build template skip a safeguard without saying so? |
| `check_deploy.py` | Will a Debug build land in the folder the game loads, and only there? |
| `check_log.py` | What did the last launch say about this mod? |
| `repair_install.py` | Make an installed folder match its package again. |
| `install_package.py` | Put a built package into a profile folder for testing. |
| `check_config_names.py` | Are the setting names already PascalCase? Opt-in: `CHECK_CONFIG_NAMES=1`. |
| `server.py` | Deploy packages to the dedicated server, restart it, and check what its launch loaded. |
| `build.sh` | Build one mod in `compile`, `debug` or `release` mode, gated on the real result. |
| `preflight.sh` | Every check above that needs no launch, for one repo. |

`gale.py` holds what several of them need: Gale's paths, its active profile, whether
Valheim is running, and the version a plugin DLL declares.

## Writing into a profile

Gale installs package files as hard links into its cache, shared by every profile
holding that version. Every tool here that writes into a profile unlinks a file
before writing it, and a build target must do the same (ADR-0001).
`repair_install.py` wrote through the links until 2026-09-12.

## repair_install.py

Fixes the layout and nothing else. It writes only paths the package defines, and
removes a flattened duplicate only once the correct path holds the same bytes, so
nothing is deleted before its replacement exists. It never touches
`mm_v2_manifest.json` or `mods.yml`.

The records stay true because the version does not change. Only the layout does.

Run it without `--apply` first: it reports what it would do and changes nothing.

## install_package.py

The same writes as `repair_install.py`, for a version the profile does not hold
yet. It refuses while Valheim runs, then runs `verify_install.py` and reports any
other plugin folder with a live copy of the same DLL. Gale's records keep the
version Gale installed, so `check_log.py` is how to see what loaded.

## verify_install.py

The check that was missing. A package can be correct and the install still wrong,
because other processes write into that folder: a build target, a hand copy, or
the mod manager reconciling against its own stored file list.

It reports the shape of the failure, not just a mismatch: `MISSING`, `FLATTENED`,
`CONTENT`, `DISABLED`, `EXTRA`. Notes that do not fail the folder: `LINKED` for hard
links, `FOREIGN` for an r2modman record left in a Gale profile, and `SWAPPED` with
`--debug-dll`, where a Debug build replaced the DLL and the manifest still names
the packaged version.

`FLATTENED` exists because of a real failure. r2modman re-enabled OttoUI and moved
`Translations/<Language>/*.json` to the folder root. Jotunn reads only the tree,
so every translation silently stopped loading. The package was correct throughout.

Gale does the same. On 2026-09-12 its Default profile held a local import of
Ottomation_ModLib 1.17.0 with both translation files at the folder root. Gale also
disables a mod by renaming its files to `*.old`, and installs imported from
r2modman can still carry `mm_v2_manifest.json`.

## check_enums.py

`ilspycmd` stores an enum constant as an ordinal and prints whichever member sits
at that ordinal in the assembly it is given. Decompiling a mod built against an
older Valheim, using the current Valheim, yields a wrong but plausible member
name. It compiles, runs, logs nothing, and reads correctly to a reviewer.

Valheim 1.0 added five members to `GlobalKeys`. A property named `IsNoCraftCost`
read `GlobalKeys.DeathDeleteItems`, five ordinals low. A method named
`NoTeleport` read `GlobalKeys.PassiveMobs`, also five low.

The surviving evidence is the surrounding identifier. Method and property names
are stored as strings and never re-resolved, so they still say what the author
meant. The harness compares the two and reports the ordinal shift.

It is a heuristic. It finds a contradiction between a constant and its context;
it cannot prove a constant is right.

A reference someone has checked against vanilla can carry
`// check_enums: verified - <reason>` on its own line or the line above. The harness
skips it and reports how many it skipped. A marker with no reason is ignored, because the
reason is what lets the next reader trust the skip. OttoStash carries one, on a
`GlobalKeys.TeleportAll` check that matches vanilla `InventoryGrid.cs` word for word and
trips the heuristic only because `m_foodStamina` sits two lines below it.

## check_harmony.py

A game update that changes a patched method's signature compiles cleanly and throws
"Undefined target method" inside `PatchAll` at launch, which also skips the patches
after it. The harness reads each `[HarmonyPatch]` attribute, `#if DEBUG` code
included, and looks the target up in the decompiled game assemblies: `MISSING` for
no such method, `SIGNATURE` for no overload with exactly the listed argument types.

Validated against OttoAura's Debug-only `GetTooltip` patch, which listed four
argument types after the 2026-09-11 update gave the method six. The fixed patch in
OttoAura's `auraboost` worktree passes, and so does every other mod repo. Targets
outside the game assemblies, constructors and types named by string are counted as
`SKIP`, never as a pass.

## check_template.py and check_deploy.py

`check_template.py` reads the `.csproj` for the faults that let a safeguard skip
itself: `IN-PLACE`, `SILENT`, `WORKTREE`, `UNCHECKED`. `check_deploy.py` asks MSBuild
where a Debug build will copy, then checks that path against the profile Gale
launches and the plugin folders that hold a live copy of the DLL: `MANAGER`,
`INACTIVE`, `NO FOLDER`, `STALE`, `DUPLICATE`, `DISABLED`. The template fixes and
the state of every repo on 2026-09-12 are in `../docs/local-testing.md`.

## server.py

Talks to the dedicated server through the Winternode WISP panel's client API.
`deploy` is a dry run until `--apply`. With it, the tool stops the server, uploads
the package, and compares every file's bytes on the server with the package. It then
starts the server and waits for a `Game server connected` line stamped after the
start signal, and runs `check_log.py --server` for each plugin. `check_log.py
--server` skips the client checks that make no sense on a server.

Validated read-only against the live server on 2026-09-12:

- `status`, `log`, and a dry-run `deploy` of OttoBifrost 1.3.0 pass.
- A downgrade, a package missing `icon.png`, a misnamed zip, and a bad key are each
  refused with exit 1.
- On a copy of the server log, `check_log.py --server` reports `VERSION`, `ERROR` and
  `NOT LOADED` for an injected wrong version, a mod error and a missing mod.

Uploads, overwrites, nested directories, deletes and download hashes were exercised
in a scratch directory on the server.

On 2026-09-13, `deploy --apply` shipped OttoPay 1.5.0 and OttoAura 1.2.0 to the live
server. It stopped the server, uploaded both packages and verified them byte for byte.
It then started the server and confirmed the launch at 15:11:34 UTC, and
`check_log.py --server` found both versions loaded cleanly. `restart` has not run.
`../docs/server.md` has the workflow and what the API really does.

## check_config_names.py

A run that sees no bind call site fails with `NOTHING CHECKED`. OttoBifrost binds
with section constants and `BindSynced`, which the check did not match, and it once
printed a clean verdict over zero sites.

## Usage

`$PROFILE` is the Gale profile the game loads, `Default`:

```
PROFILE=/mnt/c/Users/paulo/AppData/Roaming/com.kesomannen.gale/valheim/profiles/Default
tools/build.sh ../OttoUI release
tools/preflight.sh ../OttoUI
tools/preflight.sh ../OttoUI "$PROFILE/BepInEx/plugins/potto007-OttoUI"
python3 tools/check_log.py OttoUI --expect 1.2.0
```

`verify_package.py` also runs inside every Release build. In a worktree the current
template skips it silently, which is why `build.sh release` runs it too.

## What these do not cover

None of them starts the game. `check_log.py` reads a launch, but someone has to play
it first. Every failure that reached a player this series was found by playing, not
by checking. These harnesses shorten the list of things that can be wrong before you
get there.
