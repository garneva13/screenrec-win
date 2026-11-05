"""Does the catalogue still cover the interface? Answered by reading the source.

The English text is the key (see ``screenrec.i18n``), so a translatable string exists in
exactly two places: the call site that shows it and the block in
``screenrec/translations`` that gives its Russian. Nothing links the two but the
a sentence reworded at the call site is a sentence that silently stops being translated -
the fallback in ``_()`` hands the user correct English and no error at all.

So this tool parses every source file with ``ast`` and collects

* every string literal that is the single argument of a call named ``_()``;
* the first argument of every call named ``pick()``;

and compares that set with the catalogue, in both directions:

* **missing** - shown to a user, with no Russian. This is the failure; the exit status is
  non-zero when the list is not empty.
* **dead** - translated, used nowhere. Not a failure (a string may be landing in the
  source this minute) but worth seeing, because a renamed sentence leaves its old
  translation behind looking healthy.
* **unchecked** - ``_()`` handed something that is not a literal. These are *counted and
  listed* rather than skipped quietly: ``_(f"{n} frames")`` translates the finished
  sentence, which can never be a catalogue key, so every one of them is either a bug or a
  deliberate exception somebody should have to look at.

Reading the source, not running it, is the point: a string on a branch that only a second
monitor reaches is found exactly like one on the main window.

Run: .venv\\Scripts\\python.exe tools\\check_translations.py
"""
from __future__ import annotations

import argparse
import ast
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_DIR = ROOT / "app"
PACKAGE_DIR = APP_DIR / "screenrec"

if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from screenrec import i18n  # noqa: E402 - app/ has to be on sys.path first

#: The call that takes the English sentence itself.
TRANSLATE_CALL = "_"
#: The call that takes a catalogue key plus the English fallback.
PICK_CALL = "pick"


@dataclass(frozen=True)
class Reference:
    """One call site, named the way an editor can jump to it."""

    path: Path
    line: int
    call: str

    def __str__(self) -> str:
        try:
            where = self.path.relative_to(ROOT)
        except ValueError:  # a file outside the project, if ever asked for
            where = self.path
        return f"{where}:{self.line}"


@dataclass
class Usage:
    """Everything the source asks the catalogue for."""

    #: key -> every place it is used.
    keys: dict[str, list[Reference]] = field(default_factory=dict)
    #: call sites whose argument is not a literal, with what it is instead.
    unchecked: list[tuple[Reference, str]] = field(default_factory=list)
    #: calls this tool cannot read at all (no argument, keywords, ``*args``).
    malformed: list[tuple[Reference, str]] = field(default_factory=list)
    #: how many files were read.
    files: int = 0

    def add(self, key: str, reference: Reference) -> None:
        self.keys.setdefault(key, []).append(reference)


def source_files(root: Path) -> list[Path]:
    """Every source file under `root`, bytecode caches excluded, in a stable order."""
    return sorted(
        path for path in root.rglob("*.py") if "__pycache__" not in path.parts
    )


def _call_name(node: ast.Call) -> str | None:
    """``_("x")`` and ``i18n._("x")`` are the same call; ``self._pick(t)`` is not.

    Matching on the attribute's last component is what makes the qualified spelling work.
    It cannot confuse a private method with ``pick()``, because ``_pick`` is a different
    name - the leading underscore is part of it.
    """
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _literal(node: ast.expr) -> str | None:
    """The string this expression certainly is, or None if it cannot be known here.

    Implicit concatenation (``"one " "two"``) is already one Constant by the time the
    parser is done. Explicit concatenation with ``+``, which is how a sentence too long
    for one line is usually wrapped, is folded here: ``literal_eval`` refuses it, because
    the only ``+`` it accepts is the one in a complex number.
    """
    if isinstance(node, ast.Constant):
        return node.value if isinstance(node.value, str) else None
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _literal(node.left)
        right = _literal(node.right)
        if left is None or right is None:
            return None
        return left + right
    return None


def _describe(node: ast.expr) -> str:
    """What was passed, in words, so the report is read without opening the file."""
    if isinstance(node, ast.JoinedStr):
        return "an f-string (the sentence is built before it is translated)"
    if isinstance(node, ast.Name):
        return f"the variable {node.id!r}"
    if isinstance(node, ast.Attribute):
        return f"the attribute {node.attr!r}"
    if isinstance(node, ast.Call):
        inner = _call_name(node)
        return f"the result of {inner or 'a'}()"
    if isinstance(node, ast.Subscript):
        return "a lookup"
    if isinstance(node, ast.IfExp):
        return "a conditional expression (two strings - translate each of them)"
    return f"a {type(node).__name__} expression"


def _positional(node: ast.Call) -> list[ast.expr] | None:
    """The plain positional arguments, or None if the call uses ``*args``."""
    arguments: list[ast.expr] = []
    for argument in node.args:
        if isinstance(argument, ast.Starred):
            return None
        arguments.append(argument)
    return arguments


def scan_file(path: Path, usage: Usage) -> None:
    """Add everything `path` asks of the catalogue to `usage`."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _call_name(node)
        if name not in (TRANSLATE_CALL, PICK_CALL):
            continue
        reference = Reference(path, node.lineno, name)
        arguments = _positional(node)
        if arguments is None or node.keywords:
            usage.malformed.append((reference, f"{name}() called with * or keywords"))
            continue
        if name == TRANSLATE_CALL:
            if len(arguments) != 1:
                usage.malformed.append(
                    (reference, f"_() called with {len(arguments)} arguments, not 1")
                )
                continue
            target = arguments[0]
        else:
            if not arguments:
                usage.malformed.append((reference, "pick() called with no arguments"))
                continue
            target = arguments[0]
        key = _literal(target)
        if key is None:
            why = f"{name}() was given {_describe(target)}"
            usage.unchecked.append((reference, why))
        else:
            usage.add(key, reference)


def collect(root: Path = PACKAGE_DIR) -> Usage:
    """Read every source file under `root` and return what it asks the catalogue for."""
    usage = Usage()
    for path in source_files(root):
        usage.files += 1
        scan_file(path, usage)
    return usage


def missing_keys(usage: Usage) -> list[str]:
    """Keys the interface uses that have no Russian. The list that fails the build."""
    return i18n.missing_translations(sorted(usage.keys))


def dead_keys(usage: Usage) -> list[str]:
    """Catalogue entries no call site asks for."""
    return sorted(key for key in i18n.CATALOGUE if key not in usage.keys)


def _utf8_stdout() -> None:
    """Keep a Cyrillic report from killing the run on a cp1251 console."""
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure is None:
        return
    try:
        reconfigure(encoding="utf-8", errors="backslashreplace")
    except (ValueError, OSError):  # detached or already written to; the report survives
        pass


def report(usage: Usage, strict: bool = False, limit: int = 0) -> int:
    """Print the whole picture and return the process exit status."""
    missing = missing_keys(usage)
    dead = dead_keys(usage)

    print(f"read {usage.files} files under {PACKAGE_DIR.relative_to(ROOT)}")
    print(f"{len(usage.keys)} translatable strings, {len(i18n.CATALOGUE)} in the "
          f"catalogue")

    if usage.unchecked:
        print(f"\n--- not checkable statically ({len(usage.unchecked)}) ---")
        print("   a translated string must be a literal; build the sentence after "
              "translating it")
        for reference, why in _capped(usage.unchecked, limit):
            print(f"   {reference}  {why}")

    if usage.malformed:
        print(f"\n--- odd calls ({len(usage.malformed)}) ---")
        for reference, why in _capped(usage.malformed, limit):
            print(f"   {reference}  {why}")

    if dead:
        print(f"\n--- translated but unused ({len(dead)}) ---")
        for key in _capped(dead, limit):
            print(f"   {key!r}")

    if missing:
        print(f"\n--- MISSING RUSSIAN ({len(missing)}) ---")
        for key in missing:
            where = ", ".join(str(r) for r in usage.keys[key][:3])
            print(f"   {key!r}\n      {where}")
        print(f"\n{len(missing)} strings are shown to a user with no Russian.")
        return 1

    if strict and dead:
        print(f"\n{len(dead)} catalogue entries are used nowhere (--strict).")
        return 1

    print("\nevery translatable string has Russian")
    return 0


def _capped(items: list, limit: int) -> list:
    """The first `limit` items, or all of them when `limit` is 0."""
    if limit > 0 and len(items) > limit:
        return items[:limit]
    return items


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--strict", action="store_true",
        help="also fail when the catalogue holds an entry nothing uses",
    )
    parser.add_argument(
        "--limit", type=int, default=0, metavar="N",
        help="print at most N lines per informational section (0 = all)",
    )
    parser.add_argument(
        "--root", type=Path, default=PACKAGE_DIR,
        help="the package to read (default: app/screenrec)",
    )
    options = parser.parse_args(argv)
    _utf8_stdout()
    return report(collect(options.root), strict=options.strict, limit=options.limit)


if __name__ == "__main__":
    sys.exit(main())
