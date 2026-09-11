# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2014-2026, Lars Asplund lars.anders.asplund@gmail.com


"""
Setup of the Python bridge of a project: native library, configuration and generated VHDL.
"""

import os
import sys
from pathlib import Path
from typing import List, Optional
from weakref import WeakKeyDictionary

from .native_library import (
    PACKAGE_PATH,
    PythonBridgeError,
    check_python_build,
    prepare_library,
    windows_python_dll,
)

RUNTIME_SOURCE = PACKAGE_PATH / "runtime.py"
VHDL_SOURCE_PATH = PACKAGE_PATH.parent / "vhdl" / "python_bridge" / "src"
FFI_PACKAGE_TEMPLATE = VHDL_SOURCE_PATH / "python_ffi_pkg.vhd.in"
CONFIG_FILE_NAME = "vunit_python_bridge.cfg"

SUPPORTED_SIMULATORS = ("nvc", "ghdl")

# GHDL backends that link the design ahead of time. For these the library
# token in the VHPIDIRECT attribute is passed to the linker instead of being
# dlopen()ed at run time.
GHDL_LINKING_BACKENDS = ("llvm", "gcc")

_BRIDGES: "WeakKeyDictionary[object, PythonBridge]" = WeakKeyDictionary()


class PythonBridge:
    """
    A prepared bridge: native library, its configuration and the generated VHDL.
    """

    def __init__(self, library_file: Path, vhdl_files: List[Path]) -> None:
        self.library_file = library_file
        self.vhdl_files = vhdl_files

    @property
    def directory(self) -> Path:
        return self.library_file.parent


def setup(project, output_path: str, simulator_class, vunit_context_file: Path) -> PythonBridge:
    """
    Prepare the Python bridge for a project. Called by add_vhdl_builtins(python=True).

    :returns: The bridge. Its vhdl_files are to be added to vunit_lib.
    """
    simulator_name = None if simulator_class is None else simulator_class.name
    if simulator_name is not None and simulator_name not in SUPPORTED_SIMULATORS:
        raise PythonBridgeError(
            f"VHDL Python support (add_vhdl_builtins(python=True)) requires NVC or GHDL, "
            f"it is not supported for {simulator_name}"
        )

    check_python_build()

    root = Path(output_path) / "python_bridge"
    library_file = prepare_library(root)
    _write_if_changed(library_file.parent / CONFIG_FILE_NAME, _config_text())

    if simulator_name == "ghdl" and _ghdl_backend(simulator_class) in GHDL_LINKING_BACKENDS:
        library_token = "-lvunit_python_bridge"
    else:
        library_token = library_file.name

    vhdl_path = root / "vhdl"
    ffi_package = vhdl_path / "python_ffi_pkg.vhd"
    _write_if_changed(ffi_package, FFI_PACKAGE_TEMPLATE.read_text(encoding="utf-8").replace("{library}", library_token))
    context = vhdl_path / "vunit_context.vhd"
    _write_if_changed(context, _python_context(vunit_context_file))

    bridge = PythonBridge(
        library_file,
        [ffi_package, VHDL_SOURCE_PATH / "python_pkg.vhd", VHDL_SOURCE_PATH / "python_pkg-body.vhd", context],
    )
    _BRIDGES[project] = bridge
    return bridge


def get_bridge(project) -> Optional[PythonBridge]:
    """
    The bridge of a project, None unless Python support is enabled.
    """
    return _BRIDGES.get(project)


def _base_dir() -> Path:
    """
    Base directory of relative Python file names: the directory of the run script.
    """
    script = Path(sys.argv[0]) if sys.argv and sys.argv[0] else None
    if script is not None and script.is_file():
        return script.resolve().parent
    return Path.cwd()


def _config_text() -> str:
    """
    Content of the configuration file read by the bridge library at run time.
    """
    lines = {
        "executable": sys.executable,
        "prefix": sys.prefix,
        "runtime": str(RUNTIME_SOURCE),
        "base_dir": str(_base_dir()),
    }
    if sys.platform == "win32":
        lines["python_dll"] = windows_python_dll()
    for key, value in lines.items():
        if "\n" in value or "\r" in value:
            raise PythonBridgeError(f"VHDL Python support cannot handle line breaks in the path {value!r}")
    return "".join(f"{key}={value}\n" for key, value in lines.items())


def _python_context(vunit_context_file: Path) -> str:
    """
    vunit_context with python_pkg added, generated from the original to avoid duplication.
    """
    text = vunit_context_file.read_text(encoding="utf-8")
    marker = "end context;"
    if marker not in text:
        raise RuntimeError(f"Failed to find '{marker}' in {vunit_context_file!s}")
    return text.replace(marker, "  use vunit_lib.python_pkg.all;\n" + marker, 1)


def _write_if_changed(path: Path, text: str) -> None:
    """
    Write a file unless it already has the given content, keeping timestamps stable.
    """
    data = text.encode("utf-8")
    if path.is_file() and path.read_bytes() == data:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def _ghdl_backend(simulator_class) -> Optional[str]:
    """
    Backend of the GHDL that will be used, None if not found.
    """
    prefix = simulator_class.find_prefix()
    if prefix is None:
        return None
    return simulator_class.determine_backend(prefix)
