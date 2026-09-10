# OttoModTools

Build and release checks for the Otto Valheim mods. Each mod repo sits next to this
one, and its Release build runs `tools/verify_package.py` on the zip it produces.

See `tools/README.md` for what each harness answers and what none of them cover.

```
tools/preflight.sh ../OttoStash
tools/preflight.sh ../OttoStash "$PROFILE/BepInEx/plugins/potto007-OttoStash"
```
