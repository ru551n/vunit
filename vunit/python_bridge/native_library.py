# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2014-2026, Lars Asplund lars.anders.asplund@gmail.com


"""
The native bridge library: compiled and cached on Linux, prebuilt DLLs on Windows.

This module only depends on the standard library so that tools/build_python_bridge.py
can load it without VUnit's dependencies.
"""

import hashlib
import os
import platform
import shlex
import shutil
import subprocess
import sys
import sysconfig
from pathlib import Path
from typing import List

PACKAGE_PATH = Path(__file__).parent.resolve()
BRIDGE_SOURCE = PACKAGE_PATH / "native" / "vunit_python_bridge.c"
# Prebuilt Windows DLLs, included in releases
BINARY_PATH = PACKAGE_PATH / "bin"


def check_python_build():
    """
    Reject Python builds that the bridge is known not to support.
    """
    if sysconfig.get_config_var("Py_GIL_DISABLED"):
        raise RuntimeError(
            "VHDL Python support does not support free-threaded CPython builds "
            f"({sys.executable}). Use a regular (GIL) CPython build."
        )
    if sys.implementation.name != "cpython":
        raise RuntimeError(f"VHDL Python support requires CPython, not {sys.implementation.name}")


def prepare_library(root: Path) -> Path:
    """
    Path of the bridge library for the running Python, built or selected under root.
    """
    if sys.platform == "win32":
        return _prepare_windows_library(root)
    return _prepare_posix_library(root)


def windows_dll_name(version_info=None) -> str:
    """
    File name of the prebuilt bridge DLL for a Python version.
    """
    version_info = sys.version_info if version_info is None else version_info
    return f"vunit_python_bridge-cp{version_info[0]}{version_info[1]}-win_amd64.dll"


def windows_python_dll() -> str:
    """
    Path of the Python DLL of the running interpreter.
    """
    import ctypes  # pylint: disable=import-outside-toplevel

    dll_handle = getattr(sys, "dllhandle", None)
    if dll_handle is None:
        raise RuntimeError(
            f"VHDL Python support requires a CPython build using a Python DLL ({sys.executable} has none)"
        )
    buffer = ctypes.create_unicode_buffer(32768)
    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    length = kernel32.GetModuleFileNameW(ctypes.c_void_p(dll_handle), buffer, len(buffer))
    if length == 0:
        raise RuntimeError("Failed to determine the path of the Python DLL")
    return buffer.value


def _prepare_windows_library(root: Path) -> Path:
    """
    Select the prebuilt DLL for the running Python. Never compiles.
    """
    if sysconfig.get_platform() != "win-amd64":
        raise RuntimeError(
            "VHDL Python support on Windows requires a 64-bit (x86-64) python.org style CPython, "
            f"{sys.executable} is built for {sysconfig.get_platform()}"
        )
    if hasattr(sys, "gettotalrefcount"):
        raise RuntimeError("VHDL Python support does not provide bridge DLLs for debug builds of CPython")

    name = windows_dll_name()
    source = BINARY_PATH / name
    if not source.is_file():
        raise RuntimeError(
            f"No prebuilt VUnit Python bridge DLL for Python {sys.version_info[0]}.{sys.version_info[1]} "
            f"({source!s} is missing). Released VUnit packages include the DLLs for the supported Python "
            "versions. A development checkout can build them with tools/build_python_bridge.py (requires MSVC)."
        )
    data = source.read_bytes()
    directory = root / f"cp{sys.version_info[0]}{sys.version_info[1]}-win_amd64-{hashlib.sha256(data).hexdigest()[:12]}"
    target = directory / "vunit_python_bridge.dll"
    if not (target.is_file() and target.read_bytes() == data):
        directory.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + f".{os.getpid()}.tmp")
        tmp.write_bytes(data)
        os.replace(tmp, target)
    return target


def _python_library() -> Path:
    """
    The shared libpython of the running interpreter.
    """
    ldlibrary = sysconfig.get_config_var("LDLIBRARY") or ""
    if (
        not sysconfig.get_config_var("Py_ENABLE_SHARED")
        or not ldlibrary.endswith((".so", ".dylib"))
        and ".so." not in ldlibrary
    ):
        raise RuntimeError(
            f"VHDL Python support requires a CPython built with a shared Python library (--enable-shared). "
            f"{sys.executable} links Python statically."
        )
    candidates = [Path(sysconfig.get_config_var("LIBDIR") or "") / ldlibrary]
    # Relocated installations (e.g. uv/python-build-standalone) may report a
    # build-time LIBDIR, fall back to the installation prefix.
    candidates.append(Path(sys.base_prefix) / "lib" / ldlibrary)
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise RuntimeError(
        f"Failed to find the shared Python library {ldlibrary} of {sys.executable} "
        f"(searched {', '.join(str(path) for path in candidates)})"
    )


def _include_dirs() -> List[str]:
    """
    Include directories of the Python development headers.
    """
    result = []
    for name in ("include", "platinclude"):
        path = sysconfig.get_paths().get(name)
        if path and path not in result:
            result.append(path)
    for path in result:
        if (Path(path) / "Python.h").is_file():
            return result
    raise RuntimeError(
        f"VHDL Python support needs the Python development headers (Python.h) of {sys.executable} "
        f"to build its bridge library, but they were not found in {', '.join(result)}. "
        "Install the development package of your Python (e.g. python3-dev)."
    )


def _compiler() -> List[str]:
    """
    The C compiler command: CC, the compiler Python was built with, or cc/gcc/clang.
    """
    if os.environ.get("CC"):
        return shlex.split(os.environ["CC"])
    configured = shlex.split(sysconfig.get_config_var("CC") or "")
    if configured and shutil.which(configured[0]):
        return configured[:1]
    for name in ("cc", "gcc", "clang"):
        if shutil.which(name):
            return [name]
    raise RuntimeError(
        "VHDL Python support needs a C compiler (cc, gcc or clang) to build its bridge library. "
        "Install one or set the CC environment variable."
    )


def _prepare_posix_library(root: Path) -> Path:
    """
    Compile the bridge for the running Python, reusing a cached build when possible.
    """
    python_library = _python_library()
    include_dirs = _include_dirs()

    source = BRIDGE_SOURCE.read_bytes()
    key_items = [
        hashlib.sha256(source).hexdigest(),
        sys.platform,
        platform.machine(),
        " ".join(platform.libc_ver()),
        sys.version,
        str(sysconfig.get_config_var("SOABI")),
        str(sysconfig.get_config_var("Py_GIL_DISABLED")),
        str(python_library),
        " ".join(include_dirs),
    ]
    key = hashlib.sha256("\n".join(key_items).encode("utf-8")).hexdigest()[:16]
    tag = sysconfig.get_config_var("SOABI") or f"cp{sys.version_info[0]}{sys.version_info[1]}"
    directory = root / f"{tag}-{key}"
    library_file = directory / "libvunit_python_bridge.so"
    if library_file.is_file():
        return library_file

    compiler = _compiler()
    directory.mkdir(parents=True, exist_ok=True)
    tmp = directory / f"libvunit_python_bridge.so.{os.getpid()}.tmp"
    cmd = (
        compiler
        + ["-shared", "-fPIC", "-O2", "-fvisibility=hidden"]
        + [f"-I{path}" for path in include_dirs]
        + [str(BRIDGE_SOURCE), "-o", str(tmp)]
        + [str(python_library), f"-Wl,-rpath,{python_library.parent!s}", "-Wl,-soname,libvunit_python_bridge.so"]
        + (["-ldl"] if sys.platform.startswith("linux") else [])
    )
    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)
    except OSError as exc:
        raise RuntimeError(f"Failed to run the C compiler {compiler[0]!r}: {exc}") from exc
    if proc.returncode != 0:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(
            "Failed to build the VUnit Python bridge library:\n"
            + " ".join(shlex.quote(item) for item in cmd)
            + "\n"
            + proc.stdout.decode(errors="replace")
        )
    os.replace(tmp, library_file)
    return library_file
