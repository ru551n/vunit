# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2014-2026, Lars Asplund lars.anders.asplund@gmail.com

"""
Setup of the VHDL-to-Python bridge enabled by ``add_vhdl_builtins(python=True)``.

Nothing in this module runs unless Python support is explicitly requested.

The bridge is a small C library (native/vunit_python_bridge.c) that
embeds CPython in the simulator process and is called from VHDL through
VHPIDIRECT foreign subprograms:

* Linux: the C source is compiled on first use against the Python running
  VUnit and cached in the VUnit output directory.
* Windows: a prebuilt DLL matching the Python version is selected from
  bin. No compiler is needed.

The simulator is made to find the library through small hooks in the NVC and
GHDL interfaces, see nvc_run_flags, ghdl_elab_flags and ghdl_run_env.
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
from typing import Dict, List, Optional
from weakref import WeakKeyDictionary

PACKAGE_PATH = Path(__file__).parent.resolve()
BRIDGE_SOURCE = PACKAGE_PATH / "native" / "vunit_python_bridge.c"
RUNTIME_SOURCE = PACKAGE_PATH / "runtime.py"
# Prebuilt Windows DLLs, included in releases
BINARY_PATH = PACKAGE_PATH / "bin"
VHDL_SOURCE_PATH = PACKAGE_PATH.parent / "vhdl" / "python" / "src"
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

    def __init__(self, library_file: Path, vhdl_files: List[Path]):
        self.library_file = library_file
        self.vhdl_files = vhdl_files

    @property
    def directory(self) -> Path:
        return self.library_file.parent


def setup(project, output_path, simulator_class, vunit_context_file: Path) -> PythonBridge:
    """
    Prepare the Python bridge for a project. Called by add_vhdl_builtins(python=True).

    :returns: The bridge. Its vhdl_files are to be added to vunit_lib.
    """
    simulator_name = None if simulator_class is None else simulator_class.name
    if simulator_name is not None and simulator_name not in SUPPORTED_SIMULATORS:
        raise RuntimeError(
            f"VHDL Python support (add_vhdl_builtins(python=True)) requires NVC or GHDL, "
            f"it is not supported for {simulator_name}"
        )

    _check_python_build()

    root = Path(output_path) / "python_bridge"
    library_file = _prepare_library(root)
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


def nvc_run_flags(project) -> List[str]:
    """
    NVC run (-r) flags loading the bridge. Empty unless Python support is enabled.
    """
    bridge = get_bridge(project)
    if bridge is None:
        return []
    return [f"--load={bridge.library_file!s}"]


def ghdl_elab_flags(project, backend) -> List[str]:
    """
    GHDL elaboration flags. Only ahead-of-time linking backends need them.
    """
    bridge = get_bridge(project)
    if bridge is None or backend not in GHDL_LINKING_BACKENDS:
        return []
    return [f"-Wl,-L{bridge.directory!s}"]


def ghdl_run_env(project, env: Dict[str, str]) -> Dict[str, str]:
    """
    Add the bridge directory to the dynamic library search path of a GHDL simulation.
    """
    bridge = get_bridge(project)
    if bridge is None:
        return env
    variable = "PATH" if sys.platform == "win32" else "LD_LIBRARY_PATH"
    env = dict(env)
    env[variable] = os.pathsep.join(item for item in (str(bridge.directory), env.get(variable, "")) if item)
    return env


def _check_python_build():
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
        lines["python_dll"] = _windows_python_dll()
    for key, value in lines.items():
        if "\n" in value or "\r" in value:
            raise RuntimeError(f"VHDL Python support cannot handle line breaks in the path {value!r}")
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


def _write_if_changed(path: Path, text: str):
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


# ---------------------------------------------------------------------------
# Native library
# ---------------------------------------------------------------------------


def _prepare_library(root: Path) -> Path:
    if sys.platform == "win32":
        return _prepare_windows_library(root)
    return _prepare_posix_library(root)


def windows_dll_name(version_info=None) -> str:
    """
    File name of the prebuilt bridge DLL for a Python version.
    """
    version_info = sys.version_info if version_info is None else version_info
    return f"vunit_python_bridge-cp{version_info[0]}{version_info[1]}-win_amd64.dll"


def _windows_python_dll() -> str:
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
