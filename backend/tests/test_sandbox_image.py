"""The image of the sandbox holds the graph of the backend, not a copy that can drift
(issue #14): the list of files comes from the imports, and a clean import proves it."""

import re
import subprocess
import sys
import tomllib
from pathlib import Path

from app.agent import sandbox_image
from app.agent.sandbox_image import BACKEND, INFRA, build_context, image_files

EXPECTED = {
    "app/__init__.py",
    "app/agent/__init__.py",
    "app/agent/graph.py",
    "app/agent/model.py",
    "app/agent/phrases.py",
    "app/agent/sandbox_turn.py",
}


def test_the_image_files_are_the_graph_the_turn_process_and_what_they_import() -> None:
    files = set(image_files())
    assert files == EXPECTED
    # the graph runs without the store, the gateway and FastAPI, so the image has none
    assert not [f for f in files if f.split("/")[1] in ("store", "tools", "main.py")]


def test_the_image_runs_the_graph_from_the_copied_files_alone(tmp_path: Path) -> None:
    ctx = tmp_path / "ctx"
    build_context(ctx)
    code = (
        "import app.agent.graph, app.agent.sandbox_turn, app.agent.model\n"
        "print(app.agent.graph.__file__)"
    )
    done = subprocess.run(
        [sys.executable, "-c", code], cwd=ctx, capture_output=True, text=True,
        timeout=120,
    )  # fmt: skip
    assert done.returncode == 0, done.stderr
    assert Path(done.stdout.strip()).is_relative_to(ctx.resolve())  # not the backend
    for rel in image_files():  # one source: the same bytes
        assert (ctx / rel).read_bytes() == (BACKEND / rel).read_bytes()


def test_the_context_holds_only_the_dockerfile_the_requirements_and_those_files(
    tmp_path: Path,
) -> None:
    ctx = tmp_path / "ctx"
    build_context(ctx)
    found = {str(p.relative_to(ctx)) for p in ctx.rglob("*") if p.is_file()}
    assert found == {"Dockerfile", "requirements.txt", *EXPECTED}


def test_a_new_import_of_the_graph_is_in_the_image_by_itself(tmp_path: Path) -> None:
    app = tmp_path / "app"
    (app / "agent").mkdir(parents=True)
    (app / "extra").mkdir()
    for init in (app / "__init__.py", app / "agent" / "__init__.py"):
        init.write_text(
            "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    import app.nope\n"
        )
    (app / "extra" / "__init__.py").write_text("")
    (app / "extra" / "thing.py").write_text("import app.deep\n")
    (app / "deep.py").write_text("")
    (app / "lazy.py").write_text("")
    (app / "agent" / "graph.py").write_text(
        "from app.extra.thing import X\n"
        "from app import deep\n"
        "import os\n"
        "def f():\n    import app.lazy\n"
    )
    (app / "agent" / "sandbox_turn.py").write_text("import app.agent.graph\n")
    files = image_files(tmp_path)
    assert files == sorted(
        [
            "app/__init__.py",
            "app/agent/__init__.py",
            "app/agent/graph.py",
            "app/agent/sandbox_turn.py",
            "app/deep.py",
            "app/extra/__init__.py",
            "app/extra/thing.py",
        ]
    )  # not app.lazy (inside a function) and not app.nope (TYPE_CHECKING)


def test_the_dockerfile_installs_the_packages_runs_as_non_root_and_holds_no_key() -> (
    None
):
    text = (INFRA / "Dockerfile").read_text()
    assert "FROM python:3.12-slim" in text
    assert "COPY app /app/app" in text and "USER app" in text
    assert not re.search(r"^(ENV|ARG)\b.*(KEY|TOKEN|SECRET)", text, re.M | re.I)
    assert "curl" not in text  # no tool for a request that the policy would allow
    assert sandbox_image.FROM_INFRA == ("Dockerfile", "requirements.txt")


def test_the_image_requirements_are_the_pins_of_the_backend() -> None:
    pins = {
        m.group(1): m.group(2)
        for line in tomllib.loads((BACKEND / "pyproject.toml").read_text())["project"][
            "dependencies"
        ]
        if (m := re.fullmatch(r"([\w-]+)==([\d.]+)", line))
    }
    image = dict(
        re.findall(
            r"^([\w-]+)==([\d.]+)$", (INFRA / "requirements.txt").read_text(), re.M
        )
    )
    assert image and set(image) == set(pins) & set(image)
    for name, version in image.items():
        assert pins[name] == version, name
