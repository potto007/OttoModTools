# OttoModTools

Build, install and release checks for the Otto Valheim mods. Each mod repo sits next
to this one, and its Release build runs `tools/verify_package.py` on the zip it
produces.

- `docs/local-testing.md` is the local loop: build, install into the Gale profile,
  launch, read the log, and what went wrong doing it.
- `docs/server.md` is the dedicated server: deploy a package, restart, and check
  the launch through the Winternode panel's API with `tools/server.py`.
- `docs/playtest.md` gets test builds to the playtest group faster than Thunderstore
  indexes them: GitHub pre-releases, installed with `playtest/ottopia-playtest.bat`.
- `tools/README.md` says what each harness answers and what none of them cover.
- `docs/decisions/` holds the decisions the tools depend on.

`$PROFILE` is the Gale profile the game loads, `Default`:

```
PROFILE=/mnt/c/Users/paulo/AppData/Roaming/com.kesomannen.gale/valheim/profiles/Default
python3 tools/check_deploy.py ../OttoStash
tools/build.sh ../OttoStash debug
python3 tools/check_log.py OttoStash
tools/preflight.sh ../OttoStash "$PROFILE/BepInEx/plugins/potto007-OttoStash"
```
