# OttoModTools

Build and release checks for the Otto Valheim mods. Each mod repo sits next to this
one, and its Release build runs `tools/verify_package.py` on the zip it produces.

See `tools/README.md` for what each harness answers and what none of them cover.

`$PROFILE` is the Gale profile the game loads, `Default`:

```
PROFILE=/mnt/c/Users/paulo/AppData/Roaming/com.kesomannen.gale/valheim/profiles/Default
tools/preflight.sh ../OttoStash
tools/preflight.sh ../OttoStash "$PROFILE/BepInEx/plugins/potto007-OttoStash"
```
