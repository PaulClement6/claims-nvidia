"""The build context of the sandbox image (issue #14). Standard library only.

The graph is not forked: the image holds the same Python files as the backend, copied
at build time. The list of files comes from the imports of the two entry modules
(`graph.py` for the graph, `sandbox_turn.py` for the turn process), followed to every
`app.*` module that runs at import, and the `__init__.py` of each package on the way.
A new import in the graph is in the image by itself, and a test imports the graph from
the copied files alone. Imports inside a function or under `if TYPE_CHECKING` do not
run at import and are not followed.

    python3 backend/app/agent/sandbox_image.py <empty folder>

makes the context that `infra/sandbox/setup.sh` gives to `docker build`. The build
context never holds `.env` or any file outside this list.
"""

import ast
import shutil
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]
INFRA = BACKEND.parent / "infra" / "sandbox"
ROOTS = ("app.agent.graph", "app.agent.sandbox_turn")
FROM_INFRA = ("Dockerfile", "requirements.txt")


def _file(module: str, backend: Path) -> Path | None:
    base = backend.joinpath(*module.split("."))
    for candidate in (base.with_suffix(".py"), base / "__init__.py"):
        if candidate.is_file():
            return candidate
    return None


def _imports(path: Path, package: str) -> set[str]:
    """The `app.*` modules that `path` imports when it is imported."""
    found: set[str] = set()

    def visit(nodes: list[ast.stmt]) -> None:
        for node in nodes:
            if isinstance(node, ast.If):  # `if TYPE_CHECKING:` does not run
                test = ast.unparse(node.test)
                if "TYPE_CHECKING" not in test:
                    visit(node.body)
                visit(node.orelse)
            elif isinstance(node, ast.Try):
                visit(node.body + node.orelse + node.finalbody)
                for handler in node.handlers:
                    visit(handler.body)
            elif isinstance(node, ast.Import):
                found.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                base = node.module or ""
                if node.level:  # relative: from . import x
                    parts = package.split(".")[
                        : len(package.split(".")) - node.level + 1
                    ]
                    base = ".".join([*parts, *([base] if base else [])])
                found.add(base)
                found.update(f"{base}.{a.name}" for a in node.names)

    visit(ast.parse(path.read_text()).body)
    return {m for m in found if m == "app" or m.startswith("app.")}


def image_files(backend: Path = BACKEND) -> list[str]:
    """The Python files of the image, relative to `backend`, sorted."""
    files: set[Path] = set()
    todo = list(ROOTS)
    seen: set[str] = set()
    while todo:
        module = todo.pop()
        if module in seen:
            continue
        seen.add(module)
        path = _file(module, backend)
        if path is None:  # `app.agent.graph.CallTool` is a name, not a module
            continue
        files.add(path)
        package = module if path.name == "__init__.py" else module.rpartition(".")[0]
        parents = [
            ".".join(module.split(".")[:i]) for i in range(1, len(module.split(".")))
        ]
        todo += parents + sorted(_imports(path, package))
    return sorted(str(f.relative_to(backend)) for f in files)


def build_context(dest: Path, backend: Path = BACKEND, infra: Path = INFRA) -> None:
    """Fills `dest` (a new or empty folder) with the Dockerfile, the requirements and
    the Python files of the image. Nothing else is copied."""
    dest.mkdir(parents=True, exist_ok=True)
    for name in FROM_INFRA:
        shutil.copy2(infra / name, dest / name)
    for rel in image_files(backend):
        (dest / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(backend / rel, dest / rel)


if __name__ == "__main__":
    build_context(Path(sys.argv[1]))
    print("\n".join(image_files()))
