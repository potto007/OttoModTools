# Harnesses

Each harness is validated against a deliberately broken input before it is
trusted. A clean run means the check ran and found nothing, which is not the same
as the tree being correct.

| Script | Answers |
| --- | --- |
| `verify_package.py` | Is this zip shippable? |
| `verify_install.py` | Does the installed folder match the package it claims to be? |
| `check_enums.py` | Did the decompile resolve an enum constant to the wrong member? |
| `repair_install.py` | Make an installed folder match its package again. |
| `check_config_names.py` | Are the setting names already PascalCase? Opt-in: `CHECK_CONFIG_NAMES=1`. |
| `preflight.sh` | All of the above, for one repo. |

## repair_install.py

Fixes the layout and nothing else. It writes only paths the package defines, and
removes a flattened duplicate only once the correct path holds the same bytes, so
nothing is deleted before its replacement exists. It never touches
`mm_v2_manifest.json` or `mods.yml`.

The records stay true because the version does not change. Only the layout does.

Run it without `--apply` first: it reports what it would do and changes nothing.

## verify_install.py

The check that was missing. A package can be correct and the install still wrong,
because other processes write into that folder: a build target, a hand copy, or
the mod manager reconciling against its own stored file list.

It reports the shape of the failure, not just a mismatch: `MISSING`, `FLATTENED`,
`CONTENT`, `DISABLED`, `EXTRA`.

`FLATTENED` exists because of a real failure. r2modman re-enabled OttoUI and moved
`Translations/<Language>/*.json` to the folder root. Jotunn reads only the tree,
so every translation silently stopped loading. The package was correct throughout.

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

## Usage

```
tools/preflight.sh ../OttoUI
tools/preflight.sh ../OttoUI "$PROFILE/BepInEx/plugins/potto007-OttoUI"
```

`verify_package.py` also runs inside every Release build, so a zip that fails it
is never produced.

## What these do not cover

None of them starts the game. Every failure that reached a player this series was
found by playing, not by checking. These harnesses shorten the list of things
that can be wrong before you get there.
