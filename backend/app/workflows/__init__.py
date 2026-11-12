"""The registry of workflows, filled by the files of this package.

A workflow file defines `WORKFLOW`. Adding a claim type is adding one file:
`WORKFLOWS` finds it, no edit here.
"""

import importlib
import pkgutil

from app.workflows.engine import Workflow

WORKFLOWS: dict[str, Workflow] = {}

for _module in pkgutil.iter_modules(__path__):
    if _module.name != "engine":
        _w = importlib.import_module(f"{__name__}.{_module.name}").WORKFLOW
        WORKFLOWS[_w.claim_type] = _w
