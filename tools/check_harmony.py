"""Resolve every Harmony patch target against the game assemblies installed now.

A game update that changes a patched method's signature makes Harmony throw
"Undefined target method" inside PatchAll. PatchAll runs in Awake, so the throw also
skips every patch and setup step after it. Nothing fails at compile time, because
the attribute names the method with a string and its arguments with typeof.

This reads source, not a build, so it also sees #if DEBUG patches that a Release
build never compiles. OttoAura had one: a Debug-only patch on
ItemDrop.ItemData.GetTooltip listed four argument types after the 2026-09-11 game
update gave the method six.

  MISSING    the type declares no method or property of that name
  SIGNATURE  the method exists, but no overload takes exactly the listed types
Notes:
  AMBIGUOUS  several overloads and no argument types listed
  SKIP       a target this cannot resolve: a type outside the game assemblies, a
             constructor, a type named by string, or arguments built at run time

Only declared members count, because Harmony resolves a patch attribute against the
type it names, not the type's bases. It is a source scan, not a compiler: patches
chosen in TargetMethod() are invisible to it, and its type comparison uses the last
segment of each type name.

Usage: check_harmony.py <source root>
"""
import os
import re
import subprocess
import sys

import gale

ASSEMBLIES = ["assembly_valheim", "assembly_utils", "assembly_guiutils"]
ALIASES = {"int32": "int", "int64": "long", "int16": "short", "uint32": "uint",
           "uint64": "ulong", "uint16": "ushort", "single": "float", "boolean": "bool"}
PARAM_MODIFIERS = {"ref", "out", "in", "params", "this", "scoped"}
ATTRIBUTE = re.compile(r"\bHarmonyPatch\s*\(")


def game_types():
    """C# spelling of each type in the game assemblies -> (assembly, ilspy name)."""
    types = {}
    for assembly in ASSEMBLIES:
        path = os.path.join(gale.MANAGED, assembly + ".dll")
        if not os.path.exists(path):
            continue
        out = subprocess.run([gale.ILSPY, "-l", "csie", path],
                             capture_output=True, text=True).stdout
        for line in out.splitlines():
            parts = line.split(None, 1)
            if len(parts) == 2:
                full = parts[1].strip()
                types.setdefault(full.replace("+", "."), (path, full))
    return types


def resolve(type_expr, types):
    name = type_expr.replace("global::", "").strip()
    if "<" in name:
        return None
    if name in types:
        return types[name]
    # A type named through a using directive: accept only one match.
    tail = [key for key in types if key.endswith("." + name)]
    return types[tail[0]] if len(tail) == 1 else None


_members = {}


def members(target):
    """Lines declaring the type's own members, from its decompile."""
    if target in _members:
        return _members[target]
    path, full = target
    out = subprocess.run([gale.ILSPY, "-t", full, path], capture_output=True, text=True).stdout
    short = re.sub(r"`\d+$", "", re.split(r"[.+]", full)[-1])
    declaration = re.compile(r"^(\t*)\S.*\b(?:class|struct|interface)\s+%s\b" % re.escape(short))
    depth = None
    found = []
    for line in out.splitlines():
        if depth is None:
            match = declaration.match(line)
            if match:
                depth = len(match.group(1)) + 1
            continue
        indent = len(line) - len(line.lstrip("\t"))
        stripped = line.strip()
        if indent == depth - 1 and stripped == "}":
            break
        if indent == depth and stripped and not stripped.startswith(("[", "{", "}", "//")):
            found.append(stripped)
    _members[target] = found
    return found


def call_args(text, start):
    """The text from start to the parenthesis that closes the one before it."""
    depth, index, quoted = 1, start, False
    while index < len(text):
        char = text[index]
        if quoted:
            if char == "\\":
                index += 1
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
            if depth == 0:
                return text[start:index]
        index += 1
    return None


def split_top(text):
    """Split on commas that are not nested in brackets, braces, generics or strings."""
    parts, depth, current, quoted = [], 0, [], False
    for char in text:
        if char == '"':
            quoted = not quoted
        if not quoted:
            if char in "([{<":
                depth += 1
            elif char in ")]}>":
                depth -= 1
            elif char == "," and depth == 0:
                parts.append("".join(current).strip())
                current = []
                continue
        current.append(char)
    if "".join(current).strip():
        parts.append("".join(current).strip())
    return parts


def normalize(type_name):
    """Compare types by last name segment, without generic arguments or C# aliases."""
    text = type_name.replace("global::", "").strip()
    while "<" in text:
        text = re.sub(r"<[^<>]*>", "", text)
    arrays = text.count("[]")
    base = re.split(r"[.+]", text.replace("[]", "").replace("?", "").strip())[-1].lower()
    return ALIASES.get(base, base) + "[]" * arrays


def classify(arg):
    found = re.fullmatch(r"typeof\((.+)\)(?:\.MakeByRefType\(\))?", arg, re.S)
    if found:
        return "type", found.group(1).strip()
    found = re.fullmatch(r"nameof\(([\w.]+)\)", arg)
    if found:
        return "name", found.group(1)
    found = re.fullmatch(r'"([^"]*)"', arg)
    if found:
        return "string", found.group(1)
    found = re.fullmatch(r"MethodType\.(\w+)", arg)
    if found:
        return "methodtype", found.group(1)
    # new[] { ... }, new Type[] { ... }, or a collection expression [ ... ]
    found = (re.fullmatch(r"new\s*(?:Type\s*)?\[\s*\]\s*\{(.*)\}", arg, re.S)
             or re.fullmatch(r"\[(.*)\]", arg, re.S))
    if found:
        return "types", [classify(a) for a in split_top(found.group(1))]
    if re.fullmatch(r"new\s*ArgumentType\s*\[\s*\]\s*\{.*\}", arg, re.S):
        return "variations", None
    return "other", arg


def target_of(args, context):
    """("TARGET", type, name, argtypes, methodtype), ("CONTEXT", type),
    ("SKIP", reason), or None for an attribute that names no target."""
    kinds = [classify(a) for a in args]
    if not kinds:
        return None
    type_expr = name = qualifier = argtypes = None
    method_type = "Normal"
    if kinds[0][0] == "type":
        type_expr = kinds.pop(0)[1]
    elif kinds[0][0] == "string" and len(kinds) > 1 and kinds[1][0] == "string":
        return "SKIP", "type named by string %r" % kinds[0][1]
    if kinds and kinds[0][0] in ("name", "string"):
        kind, value = kinds.pop(0)
        if kind == "name":
            qualifier, _, name = value.rpartition(".")
        else:
            name = value
    loose = []
    for kind, value in kinds:
        if kind == "methodtype":
            method_type = value
        elif kind == "types":
            if any(k != "type" for k, _ in value):
                return "SKIP", "argument types not all typeof"
            argtypes = [v for _, v in value]
        elif kind == "type":
            loose.append(value)
        elif kind != "variations":
            return "SKIP", "argument %s is computed" % value
    if loose:
        argtypes = (argtypes or []) + loose
    if method_type in ("Constructor", "StaticConstructor", "Enumerator", "Async"):
        return "SKIP", "%s patch" % method_type
    if name is None:
        if type_expr and argtypes is None and method_type == "Normal":
            return "CONTEXT", type_expr
        return "SKIP", "no method name in the attribute"
    type_expr = type_expr or context or qualifier
    if not type_expr:
        return "SKIP", "no type for %s" % name
    return "TARGET", type_expr, name, argtypes, method_type


def strip_comments(text):
    def blank(match):
        return re.sub(r"[^\n]", " ", match.group(0))
    text = re.sub(r"/\*.*?\*/", blank, text, flags=re.S)
    return re.sub(r"//[^\n]*", blank, text)


def overloads(lines, name):
    """Parameter lists of each declared method called name."""
    found = []
    for line in lines:
        if "(" not in line:
            continue
        head, _, rest = line.partition("(")
        declared = re.search(r"(\w+)\s*(?:<[^()]*>)?\s*$", head)
        if not declared or declared.group(1) != name or "=" in head:
            continue
        params = call_args(line, len(head) + 1)
        if params is not None:
            found.append(params)
    return found


def param_types(params):
    types = []
    for part in split_top(params):
        part = re.sub(r"^\s*\[[^\]]*\]\s*", "", part).split("=", 1)[0].strip()
        words = [w for w in part.split() if w not in PARAM_MODIFIERS]
        if len(words) >= 2:
            types.append(normalize(" ".join(words[:-1])))
    return types


def has_property(lines, name):
    pattern = re.compile(r"^[^(=]*\b%s\s*(?:\{|=>|$)" % re.escape(name))
    return any(pattern.match(line) for line in lines)


def main(root):
    types = game_types()
    if not types:
        print("FAIL  could not list the types in the game assemblies at %s" % gale.MANAGED)
        return 1

    checked = 0
    skipped = []
    failures = []
    notes = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames
                             if d not in ("bin", "obj", ".git", "Libs", ".claude"))
        for filename in sorted(filenames):
            if not filename.endswith(".cs"):
                continue
            path = os.path.join(dirpath, filename)
            where = os.path.relpath(path, root)
            with open(path, encoding="utf-8", errors="replace") as handle:
                text = strip_comments(handle.read())
            context = None
            for match in ATTRIBUTE.finditer(text):
                args = call_args(text, match.end())
                if args is None:
                    continue
                line = text.count("\n", 0, match.start()) + 1
                result = target_of(split_top(args), context)
                if result is None:
                    continue
                if result[0] == "CONTEXT":
                    context = result[1]
                    continue
                if result[0] == "SKIP":
                    skipped.append("%s:%d  %s" % (where, line, result[1]))
                    continue
                _, type_expr, name, argtypes, method_type = result
                target = resolve(type_expr, types)
                if target is None:
                    skipped.append("%s:%d  %s is not a game type" % (where, line, type_expr))
                    continue
                lines = members(target)
                label = "%s:%d  %s.%s" % (where, line, type_expr, name)
                checked += 1
                if method_type in ("Getter", "Setter"):
                    if not has_property(lines, name):
                        failures.append(("MISSING", "%s  no property %s" % (label, name)))
                    continue
                found = overloads(lines, name)
                if not found:
                    failures.append(("MISSING", "%s  %s declares no method %s"
                                     % (label, type_expr, name)))
                elif argtypes is None:
                    if len(found) > 1:
                        notes.append(("AMBIGUOUS", "%s  %d overloads, no argument types"
                                      % (label, len(found))))
                else:
                    want = [normalize(t) for t in argtypes]
                    have = [param_types(p) for p in found]
                    if want not in have:
                        failures.append(("SIGNATURE", "%s(%s)  the game declares %s"
                                         % (label, ", ".join(want),
                                            " or ".join("(%s)" % ", ".join(h) for h in have))))

    print("checked %d patch target(s), skipped %d" % (checked, len(skipped)))
    print()
    for kind, detail in failures + notes:
        print("%-10s %s" % (kind, detail))
    for detail in skipped:
        print("%-10s %s" % ("SKIP", detail))
    print()
    if failures:
        print("verdict: %d PATCH TARGET(S) DO NOT EXIST IN THIS GAME BUILD" % len(failures))
        return 1
    if not checked:
        print("verdict: NOTHING CHECKED - no patch target resolved to a game type")
        return 1
    print("verdict: every checked patch target exists in this game build")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
