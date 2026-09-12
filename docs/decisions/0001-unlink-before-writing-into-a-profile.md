# ADR-0001: Unlink before writing into a mod manager profile

- Status: accepted
- Date: 2026-09-12

## Context

Gale installs a package's files into a profile as hard links to its package cache.
The files in `plugins/potto007-OttoPay`, `potto007-OttoLens` and others showed a link
count of 3. A write that opens an existing file and truncates it changes the one
inode behind every link: Gale's cached package and every profile holding that
version change together.

MSBuild `Copy` over an existing file, `unzip -o`, `cp` onto an existing path and
Python `open(path, "wb")` all write in place. `repair_install.py` did the last until
this decision. Against a fixture with a hard-linked "cache" file, it rewrote the
cache copy.

## Decision

Anything in OttoModTools, and any build target, that writes a file into a profile
removes the existing path first and then creates a new file. Removing a link leaves
the cache and other profiles holding the old bytes.

## Consequences

- `repair_install.py` and `install_package.py` unlink before writing.
- `verify_install.py` reports hard-linked files as `LINKED`, so the hazard is visible.
- `check_template.py` fails a `CopyOutputDLL` target that has fewer `Delete` than
  `Copy` tasks. OttoPay's target already deletes the DLL first.
- A tool that writes into a profile without unlinking is a bug, even when the file
  it replaces happens to have a single link.
