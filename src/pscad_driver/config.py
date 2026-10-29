"""Load user configuration without inheriting a particular workstation's paths."""
from copy import deepcopy
import os
from pathlib import Path
import re

import yaml

DEFAULT = {
    "pscad": {
        "exe": "",
        "install": "",
        "fortran": "GFortran 4.6.2",
        "silence": True,
        "minimize": True,
    },
    "paths": {
        "home": "work",
        "templates": "templates",
        "cases": "cases",
        "runs": "runs",
        "baseline": "baseline",
        "assets": "assets",
        "logs": "logs",
    },
    "service": {"port": 46263},
    "run": {
        "build_timeout": 300,
        "run_timeout": 3600,
        "poll": 1.0,
        "stall_idle_build": 180,
        "stall_idle_run": 300,
    },
    "grid": 18,
}

CONFIG = None


def _merge(base, over):
    out = deepcopy(base)
    for key, value in over.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def load_config(path=None):
    """Explicit/env config must exist; otherwise use ./config.yaml or defaults."""
    global CONFIG
    selected = path or os.environ.get("PSCAD_AI_CONFIG")
    file = Path(selected).expanduser().resolve() if selected else Path.cwd() / "config.yaml"
    if selected or file.exists():
        values = yaml.safe_load(file.read_text(encoding="utf-8"))
        if not isinstance(values, dict):
            raise ValueError("Configuration must be a YAML mapping: %s" % file)
        cfg = _merge(DEFAULT, values)
    else:
        cfg = deepcopy(DEFAULT)
    for section, key in (("paths", "home"), ("pscad", "exe"), ("pscad", "install")):
        value = cfg[section][key]
        if value:
            location = Path(value).expanduser()
            if not location.is_absolute():
                location = file.parent / location
            cfg[section][key] = str(location.resolve())
    if not cfg["paths"]["home"].isascii():
        raise ValueError("Use an ASCII working directory for the PSCAD/GFortran toolchain")
    CONFIG = cfg
    return cfg


def path_of(key):
    cfg = CONFIG or load_config()
    directory = Path(cfg["paths"][key])
    if not directory.is_absolute():
        directory = Path(cfg["paths"]["home"]) / directory
    directory.mkdir(parents=True, exist_ok=True)
    return str(directory)


def child_path(root, *names):
    """Resolve named work items inside their configured directory, before any mutation."""
    for name in names:
        if (not isinstance(name, str) or not name.isascii()
                or not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*", name)
                or name.endswith('.')):
            raise ValueError("Use an ASCII name without path separators or trailing dots: %r" % name)
    base = Path(root).resolve()
    target = base.joinpath(*names).resolve()
    if target == base or not target.is_relative_to(base):
        raise ValueError("Work item must remain inside %s" % base)
    return str(target)
