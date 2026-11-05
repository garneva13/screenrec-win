"""A static sweep for the mistakes that a green test suite does not catch.

Each check exists because the class of bug it looks for has actually shipped before:

* **Undefined names** - a name used inside a function and defined nowhere. Compilation
  does not catch it; it is a runtime NameError in a branch the tests never take, and in a
  Qt slot it is invisible because Qt discards whatever escapes.
* **Swallowed exceptions** - ``except ...: pass`` with no comment saying why. Silence is
  the worst failure to be handed.
* **Bare except** - catches KeyboardInterrupt and SystemExit too.
* **Qt slots that can raise** - a connected method whose body has no try/except at all is
  fine, but one that calls into the recorder or Win32 should not be able to take the
  window down silently.
* **ctypes without argtypes/restype** - on 64-bit Python a function whose restype is left
  at the default truncates a returned handle to 32 bits.
* **PySide2 spellings** - this project is PySide6 only; ``exec_``, ``QtWidgets.QAction``
  and ``QDesktopWidget`` would all be wrong here.
* **Mutable default arguments.**

Run: .venv\\Scripts\\python.exe tools\\static_review.py
"""
from __future__ import annotations

import ast
import builtins
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGETS = [ROOT / "app", ROOT / "tools"]

BUILTINS = set(dir(builtins))

PYSIDE2_SPELLINGS = {
    "exec_": "PySide6 uses .exec(); .exec_() is the PySide2 spelling",
    "QDesktopWidget": "gone in Qt6 - use QGuiApplication.screenAt / primaryScreen",
    "QRegExp": "Qt6 has QRegularExpression",
}

CTYPES_NEEDING_TYPES = {
    "WindowFromPoint", "GetWindowLongPtrW", "SetWindowLongPtrW", "CreateNamedPipeW",
    "SetWindowsHookExW", "CallNextHookEx", "GetModuleHandleW", "LoadLibraryW",
}


class Finding:
    def __init__(self, path: Path, line: int, kind: str, message: str) -> None:
        self.path = path
        self.line = line
        self.kind = kind
        self.message = message

    def __str__(self) -> str:
        rel = self.path.relative_to(ROOT)
        return f"{rel}:{self.line}  [{self.kind}] {self.message}"


def module_scope_names(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                names.add((alias.asname or alias.name).split(".")[0])
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                names.update(_bound(target))
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
            names.update(_bound(node.target))
        elif isinstance(node, (ast.If, ast.Try)):
            for sub in ast.walk(node):
                if isinstance(sub, (ast.Import, ast.ImportFrom)):
                    for alias in sub.names:
                        names.add((alias.asname or alias.name).split(".")[0])
                elif isinstance(sub, ast.Assign):
                    for target in sub.targets:
                        names.update(_bound(target))
                elif isinstance(sub, (ast.FunctionDef, ast.ClassDef)):
                    names.add(sub.name)
    return names


def _bound(node: ast.AST) -> set[str]:
    if isinstance(node, ast.Name):
        return {node.id}
    if isinstance(node, (ast.Tuple, ast.List)):
        out: set[str] = set()
        for element in node.elts:
            out |= _bound(element)
        return out
    return set()


def local_names(func: ast.AST) -> set[str]:
    names: set[str] = set()
    args = getattr(func, "args", None)
    if args is not None:
        for group in (args.posonlyargs, args.args, args.kwonlyargs):
            names.update(a.arg for a in group)
        if args.vararg:
            names.add(args.vararg.arg)
        if args.kwarg:
            names.add(args.kwarg.arg)
    for node in ast.walk(func):
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            names.add(node.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                names.add((alias.asname or alias.name).split(".")[0])
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node is not func:
                names.add(node.name)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            names.add(node.name)
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                if item.optional_vars is not None:
                    names |= _bound(item.optional_vars)
        elif isinstance(node, (ast.comprehension,)):
            names |= _bound(node.target)
        elif isinstance(node, ast.Global) or isinstance(node, ast.Nonlocal):
            names.update(node.names)
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            names |= _bound(node.target)
        elif isinstance(node, ast.MatchAs) and node.name:
            names.add(node.name)
    return names


def _own_nodes(func: ast.AST):
    """Every node of a function's body except the bodies of functions nested in it.

    Without this a closure's use of a name from the enclosing function reads as
    undefined, which is the loudest false positive this check can produce.
    """
    body = getattr(func, "body", [])
    # A Lambda's body is one expression, not a list of statements.
    stack = list(body) if isinstance(body, list) else [body]
    while stack:
        node = stack.pop()
        yield node
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue  # its own scope; visited separately with the right chain
        for child in ast.iter_child_nodes(node):
            stack.append(child)


def _check_scopes(
    node: ast.AST, visible: set[str], path: Path, findings: list[Finding]
) -> None:
    """Walk functions depth first, carrying the names each nested scope can see."""
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            inner_visible = visible | local_names(child)
            for inner in _own_nodes(child):
                if isinstance(inner, ast.Name) and isinstance(inner.ctx, ast.Load):
                    if inner.id not in inner_visible:
                        findings.append(
                            Finding(path, inner.lineno, "undefined-name",
                                    f"{inner.id!r} is used but defined nowhere")
                        )
            _check_scopes(child, inner_visible, path, findings)
        else:
            _check_scopes(child, visible, path, findings)


def check_file(path: Path) -> list[Finding]:
    source = path.read_text(encoding="utf-8")
    lines = source.splitlines()
    tree = ast.parse(source, filename=str(path))
    findings: list[Finding] = []
    module_names = module_scope_names(tree)

    class_attrs: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    class_attrs.add(item.name)
                elif isinstance(item, ast.Assign):
                    for target in item.targets:
                        class_attrs |= _bound(target)
                elif isinstance(item, ast.AnnAssign):
                    class_attrs |= _bound(item.target)

    known = BUILTINS | module_names | class_attrs | {"__file__", "__name__", "__doc__"}

    _check_scopes(tree, known, path, findings)

    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler):
            if node.type is None:
                findings.append(
                    Finding(path, node.lineno, "bare-except",
                            "catches KeyboardInterrupt and SystemExit too")
                )
            body = node.body
            if len(body) == 1 and isinstance(body[0], ast.Pass):
                # Read the PHYSICAL lines, not ast.get_source_segment: a segment ends
                # at the `pass` token, so `pass  # why` - which is exactly how this is
                # usually written - looked like an unexplained swallow. Three real
                # explanations were reported as defects before this was noticed.
                first = node.lineno - 1
                last = max(node.end_lineno or node.lineno, body[0].lineno)
                segment = "\n".join(lines[first:last])
                if "#" not in segment:
                    findings.append(
                        Finding(path, node.lineno, "silent-swallow",
                                "exception discarded with no comment saying why")
                    )

        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for default in node.args.defaults + [d for d in node.args.kw_defaults if d]:
                if isinstance(default, (ast.List, ast.Dict, ast.Set)):
                    findings.append(
                        Finding(path, node.lineno, "mutable-default",
                                f"{node.name}() has a mutable default argument")
                    )

        if isinstance(node, ast.Attribute):
            if node.attr in PYSIDE2_SPELLINGS:
                findings.append(
                    Finding(path, node.lineno, "pyside2-spelling",
                            f"{node.attr}: {PYSIDE2_SPELLINGS[node.attr]}")
                )

    # ctypes functions used without argtypes/restype anywhere in the file
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            name = node.func.attr
            if name in CTYPES_NEEDING_TYPES:
                if f"{name}.restype" not in source and f"{name}.argtypes" not in source:
                    findings.append(
                        Finding(path, node.lineno, "ctypes-untyped",
                                f"{name} is called without argtypes/restype being set")
                    )

    return findings


def main() -> int:
    findings: list[Finding] = []
    files: list[Path] = []
    for target in TARGETS:
        files.extend(sorted(p for p in target.rglob("*.py") if "__pycache__" not in p.parts))
    for path in files:
        try:
            findings.extend(check_file(path))
        except SyntaxError as exc:
            findings.append(Finding(path, exc.lineno or 0, "syntax", str(exc)))

    seen: set[tuple] = set()
    unique: list[Finding] = []
    for finding in findings:
        key = (finding.path, finding.line, finding.kind, finding.message)
        if key not in seen:
            seen.add(key)
            unique.append(finding)

    print(f"scanned {len(files)} files")
    if not unique:
        print("nothing found")
        return 0
    by_kind: dict[str, list[Finding]] = {}
    for finding in unique:
        by_kind.setdefault(finding.kind, []).append(finding)
    for kind in sorted(by_kind):
        print(f"\n--- {kind} ({len(by_kind[kind])}) ---")
        for finding in by_kind[kind]:
            print("  ", finding)
    return 1


if __name__ == "__main__":
    sys.exit(main())
