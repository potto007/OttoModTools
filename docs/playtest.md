# Playtest builds

Thunderstore and Hexium take a while to index a new version, and Gale reads packages
from those two sources only. There is no setting for another one. Playtest builds
therefore go out as GitHub pre-releases on each mod's repository.
`playtest/ottopia-playtest.bat` installs them into a tester's Gale profile as local
mods.

## Why not only profile sync

Gale's profile sync already carries the Ottopia profile to testers, and a pull runs at
every launch unless turned off. What a synced profile uploads is `export.r2x`, the
list of Thunderstore mods with versions and enabled flags, plus config files. Local
mods and mod DLLs are not in it. This comes from `export_zip` in Gale's
`src-tauri/src/profile/export/mod.rs`, which sync's `create_profile` and
`push_profile` both call.

Sync still does two jobs here:

- **Carries the switches.** It spreads the enabled/disabled state of the Thunderstore
  copies, and the configs.
- **Leaves local mods alone.** `incremental_update` in `profile/import/mod.rs` removes
  only Thunderstore mods that are missing from the manifest, so a tester's playtest
  build survives a pull.

## Maintainer

1. **Build and check** the Release zip: `tools/build.sh ../OttoPay release`.
2. **Publish it**:
   `python3 tools/playtest.py publish ../OttoPay/Thunderstore/potto007-OttoPay-1.5.2.zip`,
   then again with `--apply`. It refuses in these cases:
   - a package that is not shippable;
   - a repository the tester script does not read, or a private one;
   - a version not newer than the latest `v` release;
   - a version older than a playtest build already out.

   With `--apply` it creates `playtest-<version>` as a pre-release with the zip and
   its sha256 in the notes. It deletes that mod's older playtest pre-releases, then
   downloads the zip back to compare hashes.
3. **Disable the Thunderstore copy** of the mod in the synced Ottopia profile in Gale,
   then **Push update**. Otherwise a tester loads both copies, the Thunderstore one
   and the playtest one.
4. **When the version is on Thunderstore**, run
   `python3 tools/playtest.py retire OttoPay --apply`. Then re-enable and update the
   Thunderstore copy in the Ottopia profile, and Push update.

`python3 tools/playtest.py list` shows what is out.

The tags are `playtest-<version>`. A `v` tag would start the Thunderstore publish
workflow. Only the public repositories can serve playtest builds, because the tester
script does not log in: OttoAura, OttoBifrost, OttoFuel, OttoLens, OttoPay, OttoStash.

## Testers

One-time setup:

1. Install Gale.
2. Import the Ottopia profile with the code from Paul: Import > ...profile from code.

Each playtest:

1. Double-click `ottopia-playtest.bat`, downloaded from this repository's `playtest/`
   folder. It reads the newest `playtest-*` pre-release of each mod and checks the
   zip against the sha256 in its notes. It then runs
   `gale.exe --game valheim --profile Ottopia --install <zip>`, which switches Gale to
   Ottopia and installs the zip as a local mod. Installing again replaces the previous
   local copy.
2. Read the last lines. `OK` means one live copy of the mod. `live copies` means
   the Thunderstore copy is still enabled: click Pull update on the Ottopia profile, or
   disable it by hand.
3. Launch Valheim from Gale.

After a version ships to Thunderstore, delete the local copy in Gale. The next Pull
update brings the Thunderstore copy back.

`OTTOPIA_PROFILE` picks another Gale profile name.

## What has been checked

From Gale's source on 2026-09-13:

- `--install` calls `import_local_mod`;
- a second `gale.exe` passes its arguments to the running instance;
- `--no-gui` would exit a running Gale, so the script does not use it.

Validated here:

- `playtest.py` refusals and dry runs against the real repositories.

Not yet run:

- a real `publish --apply`;
- the tester script. PowerShell cannot run in the session that wrote it.

The first real playtest build is the test of both.
