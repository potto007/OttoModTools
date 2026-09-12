# Local testing loop

How to get a change from a mod repo into the game on this machine, and what the
loop taught the OttoPay, OttoAura, OttoBifrost and OttoLens sessions on 2026-09-12.

Gale has been the mod manager since 2026-09-11. Its Valheim profiles are `Default`,
the one Gale launches, and `Ottopia`. `check_deploy.py` reads which one is active
from Gale's database rather than guessing from folder dates.

```
TOOLS=~/src/valheim/mods/OttoModTools/tools
PROFILE=/mnt/c/Users/paulo/AppData/Roaming/com.kesomannen.gale/valheim/profiles/Default
```

## The loop

1. **Where will the build go?** `$TOOLS/check_deploy.py ../OttoX`. It asks MSBuild
   for the resolved paths, then compares them with the profile Gale launches and
   with the plugin folders that really hold the DLL.
2. **Compile only**, leaving the profile untouched: `$TOOLS/build.sh ../OttoX compile`.
3. **After a game update**, before launching: `$TOOLS/check_harmony.py ../OttoX`.
4. **Debug into the profile.** Quit Valheim first, then `$TOOLS/build.sh ../OttoX debug`.
   Only the DLL changes. The folder's `manifest.json` and Gale's own records keep
   the old version, so Gale shows one version while the new code runs.
5. **Launch from Gale** and play the change. None of these tools start the game.
6. **After quitting**, read the launch before the next one overwrites it:
   `$TOOLS/check_log.py OttoX --expect 1.2.0 --archive ~/valheim-logs`.
7. **Release.** `$TOOLS/build.sh ../OttoX release`, then `$TOOLS/preflight.sh ../OttoX`.
8. **Test the Release zip in the profile**:
   `$TOOLS/install_package.py ../OttoX/Thunderstore/potto007-OttoX-1.2.0.zip "$PROFILE/BepInEx/plugins/potto007-OttoX"`,
   then again with `--apply`, then launch and run `check_log.py` again.

Gate commits and scripts on the exit code of `build.sh` and the harnesses. Each one
ends with a verdict line and exits non-zero on failure.

## What the loop taught

### Gale and the profile

- **Gale's files are hard links into its cache.** Overwriting one in place rewrites
  the cache and every profile linked to it. Unlink, then write. See ADR-0001.
  `verify_install.py` reports `LINKED`, `check_template.py` reports `IN-PLACE`.
- **Six repos still point Debug builds at the old r2modman profile.** Where the
  folder exists, the copy succeeds, a checksum of the copied file matches, and the
  game never loads it. OttoAura lost a test round this way. Where it does not, the
  copy is skipped without a word. `check_deploy.py` reports `MANAGER` and `NO FOLDER`.
- **The folder a build writes to can differ from the folder the game loads.** Gale
  disables a mod by renaming its files to `*.old`. A local zip import lands in an
  unprefixed folder such as `plugins/OttoPay` beside `plugins/potto007-OttoPay`, and
  Gale later removed it and re-enabled the prefixed one. `check_deploy.py` reports
  `STALE`, `DUPLICATE` and `DISABLED`.
- **Gale's local import flattens translations**, as r2modman's did. The Default
  profile held `plugins/Ottomation_ModLib` with both `Translations/<Language>/*.json`
  files at the folder root. `verify_install.py` reports `FLATTENED`, and
  `repair_install.py` restores the tree.
- **Gale does not notice a hand swap.** It lists the version it installed. The log
  says what loaded.
- **An r2modman record can outlive the switch.** `plugins/potto007-OttoLens` still
  holds `mm_v2_manifest.json` from 2026-09-10. `verify_install.py` reports `FOREIGN`.
- **Valheim holds the plugin DLL open.** `build.sh debug` and `install_package.py`
  refuse while `valheim.exe` runs.

### Building

- **Worktrees defeat two safeguards in the current template.** `.git` is a file in a
  worktree, so the published-version guard that reads `.git/refs/tags` never fires.
  `../OttoModTools` does not exist from `.claude/worktrees/<name>`, so the package
  check is skipped and the build succeeds. `check_template.py` reports `WORKTREE`
  and `UNCHECKED`. `build.sh release` runs `verify_package.py` itself.
- **A passing package check prints nothing** at the default verbosity. OttoPay's
  build passed with no sign of what was checked. `check_template.py` reports `QUIET`.
- **A missing profile folder skips the Debug copy silently.** `check_template.py`
  reports `SILENT`.
- **Compile without deploying** with `-p:CopyOutputDLLPath=`, which is what
  `build.sh compile` passes.
- **Never gate on grep.** `dotnet build | grep -E "error|Build succeeded" && git commit`
  committed a failed build, because grep exits 0 when it matches the error lines.

### The game

- **A stale patch target throws in `Awake`.** OttoAura's Debug-only `GetTooltip`
  patch listed four argument types after the 2026-09-11 update gave the method six.
  Harmony threw "Undefined target method" inside `PatchAll`, which also skipped the
  patches and setup after it. Release builds never compiled that patch.
  `check_harmony.py` reads source, `#if DEBUG` included, and `check_log.py` reports
  the throw as `ERROR`.
- **`LogOutput.log` is rewritten at every launch.** Archive it after quitting.
- **The ServerSync line is the evidence a server runs the mod**:
  `Received OttoBifrost version 1.2.0 and minimum version 1.2.0 from the server.`
  The mod's own logging is not, since it may be switched off. The Otto mods set the
  minimum to their own version, so a client on any other version is disconnected.
  `check_log.py` reports `SERVER` on a mismatch.
- **Another mod can change the behavior under test.** Server devcommands' debug-mode
  fast teleport sets `Player.m_teleportTimer = 15`, which looked like an OttoBifrost
  regression. Search every DLL in the profile's `plugins/` for the method names
  involved, then decompile the hits.
- **Check the version a DLL declares**, not the file name:
  `verify_install.py <zip> <folder> --debug-dll` reports `SWAPPED` with both versions.

### C#, BepInEx and decompiling

- `assembly_valheim` declares a global `Version` type. `Version` alone fails with
  CS0144 and `using Version = System.Version;` with CS0576. Write `System.Version`.
- `ConfigFile.Bind` saves the file at once, rewriting the "Settings file was created
  by plugin X vY" header. A migration that reads the header must read it before the
  first `Bind`. The `ConfigFile` constructor is called with `saveOnInit` false.
- Nested types decompile with `+`:
  `ilspycmd -t "ItemDrop+ItemData" "<Valheim>/valheim_Data/Managed/assembly_valheim.dll"`.

### Claude sessions

- The worktree-isolation guard rejects a Bash command that mixes git with pipes,
  loops, variables or heredocs. Run git as a plain command on its own, and put
  multi-step fixtures in a script file.

## Template fixes

Every mod's `.csproj` came from one template, so these apply to each repo.
`check_template.py` passes a project that has all four. Each was built on
2026-09-12 in a scratch clone of OttoPay, against the input it exists for.

**1. Say when the Debug copy is skipped.** Built with a nonexistent
`CopyOutputDLLPath`, this printed the warning.

```xml
<Target Name="WarnNoProfileCopy" AfterTargets="ILRepacker" Condition="'$(Configuration)' == 'Debug' AND '$(CopyOutputDLLPath)' != ''">
  <Warning Condition="!Exists('$(CopyOutputDLLPath)')" Text="CopyOutputDLLPath $(CopyOutputDLLPath) does not exist, so this build was not copied into a profile. Run OttoModTools/tools/check_deploy.py." />
</Target>
```

Mods whose template hangs `CopyOutputDLL` off `Build` rather than `ILRepacker` use
`AfterTargets="Build"`.

**2. Delete before every Copy into the profile.** OttoPay's target, already in use:

```xml
<Delete Files="$(CopyOutputDLLPath)/$(TargetFileName)" />
<Copy SourceFiles="$(TargetPath)" DestinationFolder="$(CopyOutputDLLPath)" OverwriteReadOnlyFiles="true" />
```

The ModLib-style template also copies `Translations/**/*.json` into the profile.
It needs a matching `Delete` of the destination files before that `Copy`; that form
has not been built yet.

**3. Ask git for the tag.** Replaces the `_TagRefFile`/`_PackedRefs` property group.
In a git worktree of the scratch clone, where `.git` is a file, a Release build of
the tagged 1.4.0 succeeded with the current template and failed with this one on
the "already tagged" error.

```xml
<Exec Command="git tag --list v$(PackageVersion)" WorkingDirectory="$(MSBuildProjectDirectory)" ConsoleToMSBuild="true" StandardOutputImportance="low">
  <Output TaskParameter="ConsoleOutput" PropertyName="_PublishedTag" />
</Exec>
<Error Condition="'$(AllowRepackPublished)' != 'true' AND '$(_PublishedTag)' == 'v$(PackageVersion)'" Text="Version $(PackageVersion) is already tagged as v$(PackageVersion), so it is published and frozen. Bump the version, or pass -p:AllowRepackPublished=true." />
```

A build outside any git checkout now fails at this step instead of skipping it.

**4. Fail when the package check cannot run, and show its output.** In the project:

```xml
<PropertyGroup>
  <OttoModToolsDir Condition="'$(OttoModToolsDir)' == ''">$(MSBuildProjectDirectory)/../OttoModTools</OttoModToolsDir>
</PropertyGroup>
```

In `MakeThunderstorePackage`, replacing the conditional `Exec`:

```xml
<Error Condition="!Exists('$(OttoModToolsDir)/tools/verify_package.py')" Text="verify_package.py is not under OttoModToolsDir=$(OttoModToolsDir), so the package cannot be checked. Set OttoModToolsDir in environment.props; a worktree cannot reach ../OttoModTools." />
<ZipDirectory SourceDirectory="$(PackageStageDir)" DestinationFile="$(PackageZip)" Overwrite="true" />
<Exec Command="python3 &quot;$(OttoModToolsDir)/tools/verify_package.py&quot; &quot;$(PackageZip)&quot;" StandardOutputImportance="high" />
```

And in the WSL group of `environment.props`:

```xml
<OttoModToolsDir>/home/potto/src/valheim/mods/OttoModTools</OttoModToolsDir>
```

Without the property, a Release build from the scratch clone failed with the error
above. With it, the build printed `verdict: package is shippable`. The ModLib-style
template, which runs `Ottomation_ModLib/tools/verify_package.py`, takes the same
change. Its copy of the script matched OttoModTools' on 2026-09-12.

## Repo status on 2026-09-12

From `check_deploy.py` and `check_template.py`, before any repo took the fixes above.

| Repo | Debug copy goes to | Template faults |
| --- | --- | --- |
| OttoPay | Gale Default | SILENT, WORKTREE, UNCHECKED |
| OttoBifrost, OttoLens, Ottomation_ModLib | Gale Default | IN-PLACE, SILENT, WORKTREE, UNCHECKED |
| OttoAura, OttoStash, OttoFuel | r2modman profile (MANAGER) | IN-PLACE, SILENT, WORKTREE, UNCHECKED |
| OttoPath, OttoPocketUse, OttoRedecorate | missing r2modman folder (MANAGER, NO FOLDER) | IN-PLACE, SILENT, WORKTREE, UNCHECKED |

OttoLens moved to Gale in an unpushed commit. OttoPocketUse, OttoRedecorate and
Ottomation_ModLib are installed in Gale Default as local imports in unprefixed folders.
