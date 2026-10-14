"""Opt-in host policy, loaded by trial Python subprocesses via PYTHONPATH."""

import importlib.abc
import importlib.machinery
import os
import sys


if os.environ.get("VISTA_RESOURCE_GUARD_ENABLED") == "1":
    if os.environ.get("VISTA_LO_PROFILE"):
        original_expanduser = os.path.expanduser

        def trial_expanduser(path):
            prefixes = ("~/Library/Application Support/LibreOffice/4/user",
                        "~/.config/libreoffice/4/user")
            if isinstance(path, str):
                for prefix in prefixes:
                    if path == prefix or path.startswith(prefix + "/"):
                        return os.environ["VISTA_LO_PROFILE"] + "/user" + path[len(prefix):]
            return original_expanduser(path)

        os.path.expanduser = trial_expanduser

    class SolverLoader(importlib.abc.Loader):
        def __init__(self, wrapped):
            self.wrapped = wrapped

        def create_module(self, spec):
            return self.wrapped.create_module(spec)

        def exec_module(self, module):
            self.wrapped.exec_module(module)
            from sj4_resource_guard import wrap_milp

            module.milp = wrap_milp(module.milp)

    class SolverFinder(importlib.abc.MetaPathFinder):
        def find_spec(self, fullname, path=None, target=None):
            if fullname == "scipy.optimize":
                spec = importlib.machinery.PathFinder.find_spec(fullname, path)
                if spec is not None and spec.loader is not None:
                    spec.loader = SolverLoader(spec.loader)
                return spec
            return None

    sys.meta_path.insert(0, SolverFinder())
