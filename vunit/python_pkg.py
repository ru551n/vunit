# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2014-2026, Lars Asplund lars.anders.asplund@gmail.com

"""
Building the foreign language application that implements ``python_pkg``/``python_context``
(see :ref:`python_bridge`) for Riviera-PRO/Active-HDL (VHPI).

``setup_vhpi_application`` is called by :meth:`add_python() <vunit.ui.VUnit.add_python>`; the
application is built under the output path on first use and rebuilt when its sources, the
Python running VUnit or the simulator change. ``compile_vhpi_application`` is the run script
helper of the upstream ``python_pkg`` branch, kept for compatibility.

``setup_fli_application``/``compile_fli_application``, ``compile_vhpidirect_nvc_application``
and ``compile_vhpidirect_ghdl_application`` build the upstream applications for
Questa/ModelSim, NVC and GHDL. They are superseded by the VUnit Python bridge
(``vunit.python_bridge``), which builds and loads the equivalent application for those
simulators automatically when :meth:`add_python() <vunit.ui.VUnit.add_python>` is called, and
are kept here for reference only. VUnit itself does not use them.
"""

from pathlib import Path
from glob import glob
import sysconfig
import hashlib
import subprocess
import os
import sys


SRC_PATH = Path(__file__).parent.resolve() / "vhdl" / "python" / "src"


def setup_fli_application(output_path, simulator_class):
    """
    Build the upstream FLI application for Questa/ModelSim under the output path, unless the one
    already there was built from the same sources for the same Python and simulator.

    Kept for reference: :meth:`add_python() <vunit.ui.VUnit.add_python>` uses the VUnit Python
    bridge on Questa/ModelSim.
    """
    # vsim runs with the simulator directory of the output path as its working directory, which
    # the relative path in the foreign attributes of python_pkg_fli.vhd refers to
    target = Path(output_path) / simulator_class.name / "libraries" / "python" / "python_fli.so"
    _build_if_stale(target, [SRC_PATH / "python_pkg_fli.c", SRC_PATH / "python_pkg.c"], simulator_class, _build_fli)


def setup_vhpi_application(output_path, simulator_class):
    """
    Build the VHPI application for Riviera-PRO/Active-HDL under the output path, unless the one
    already there was built from the same sources for the same Python and simulator.

    Called by :meth:`add_python() <vunit.ui.VUnit.add_python>`.
    """
    target = Path(output_path) / simulator_class.name / "libraries" / "python.dll"
    _build_if_stale(
        target, [SRC_PATH / "python_pkg_vhpi.c", SRC_PATH / "python_pkg.c"], simulator_class, _build_vhpi
    )


def compile_fli_application(run_script_root, vu):  # pylint: disable=unused-argument
    """
    Compile the upstream FLI application used by Questa.

    Kept for run scripts written for the upstream ``python_pkg`` branch. ``add_python()`` no
    longer uses this application, so what it builds is only used by such a run script.
    """
    setup_fli_application(vu._output_path, vu._simulator_class)  # pylint: disable=protected-access


def compile_vhpi_application(run_script_root, vu):  # pylint: disable=unused-argument
    """
    Compile VHPI application used by Aldec's simulators.

    Kept for run scripts written for the upstream ``python_pkg`` branch. ``add_python()`` now
    builds the application by itself, so this is a no-op when it was already built.
    """
    setup_vhpi_application(vu._output_path, vu._simulator_class)  # pylint: disable=protected-access


def _fingerprint(sources, simulator_prefix):
    """
    Everything the built application depends on: the C sources and headers, the Python that is
    embedded and the simulator whose headers and libraries are used.
    """
    digest = hashlib.sha256()
    for path in sorted(sources) + sorted(SRC_PATH.glob("*.h")):
        digest.update(path.name.encode("utf-8") + b"\0" + path.read_bytes() + b"\0")
    for item in [sys.executable, sys.prefix, sys.version, sys.platform, str(simulator_prefix)]:
        digest.update(item.encode("utf-8") + b"\0")
    return digest.hexdigest()


def _build_if_stale(target, sources, simulator_class, build):
    """
    Build target with build(target, sources, simulator_prefix) unless it is up to date.
    """
    simulator_prefix = Path(simulator_class.find_prefix()).resolve()
    fingerprint = _fingerprint(sources, simulator_prefix)
    fingerprint_file = target.with_suffix(target.suffix + ".fingerprint")
    if target.exists() and fingerprint_file.exists() and fingerprint_file.read_text(encoding="utf-8") == fingerprint:
        return

    target.parent.mkdir(parents=True, exist_ok=True)
    if fingerprint_file.exists():
        fingerprint_file.unlink()
    build(target, sources, simulator_prefix)
    fingerprint_file.write_text(fingerprint, encoding="utf-8")


def _run(args, what):
    """
    Run a compiler command, failing with its output.
    """
    proc = subprocess.run(args, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"Failed to {what}:\n{' '.join(str(arg) for arg in args)}\n{proc.stdout}{proc.stderr}")


def _build_fli(target, sources, simulator_prefix):
    """
    Compile and link the FLI application. On Windows the MinGW compiler bundled with Questa is
    used and the Python import library is linked, as on the upstream branch. Elsewhere the system
    C compiler (``cc``, or ``CC``) is used and the library of the Python running VUnit is linked,
    so that the interpreter embedded in vsim is that Python; the mti_* symbols are resolved by
    vsim when it loads the application.
    """
    include = simulator_prefix.parent / "include"
    if sys.platform != "win32":
        python_lib_dir = sysconfig.get_config_var("LIBDIR")
        args = [os.environ.get("CC", "cc"), "-g", "-Wall", "-fPIC", "-shared", "-o", str(target)]
        args += [str(path) for path in sources]
        args += ["-I" + str(include), "-I" + sysconfig.get_paths()["include"]]
        if python_lib_dir:
            args += ["-L" + python_lib_dir, "-Wl,-rpath," + python_lib_dir]
        args += [f"-lpython{sys.version_info[0]}.{sys.version_info[1]}{getattr(sys, 'abiflags', '')}"]
        _run(args, "compile the FLI application")
        return

    # 32 or 64 bit installation?
    proc = subprocess.run([simulator_prefix / "vsim.exe", "-version"], capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"Failed to get vsim version:\n{proc.stderr}")
    is_64_bit = "64 vsim" in proc.stdout

    matches = glob(str((simulator_prefix.parent / f"gcc*mingw{'64' if is_64_bit else '32'}*").resolve()))
    if len(matches) != 1:
        raise RuntimeError("Failed to find the GCC executable bundled with the simulator")
    gcc = str((Path(matches[0]) / "bin" / "gcc.exe").resolve())

    python_include = Path(sys.executable).parent.resolve() / "include"
    python_libs = Path(sys.executable).parent.resolve() / "libs"
    objects = []
    for source in sources:
        args = [gcc, "-g", "-c", "-m64" if is_64_bit else "-m32", "-Wall", "-D__USE_MINGW_ANSI_STDIO=1"]
        if not is_64_bit:
            args += ["-ansi", "-pedantic"]
        obj = target.parent / (source.stem + ".o")
        args += ["-I" + str(include), "-I" + str(python_include), "-freg-struct-return", "-o", str(obj), str(source)]
        _run(args, "compile the FLI application")
        objects.append(str(obj))

    args = [gcc, "-shared", "-lm", "-m64" if is_64_bit else "-m32", "-Wl,-Bsymbolic", "-Wl,-export-all-symbols"]
    args += ["-o", str(target)] + objects
    args += [f"-lpython{sys.version_info[0]}{sys.version_info[1]}", "-l_tkinter"]
    args += ["-L" + str(simulator_prefix), "-L" + str(python_libs), "-lmtipli"]
    _run(args, "link the FLI application")


def _build_vhpi(target, sources, simulator_prefix):
    """
    Compile the VHPI application with the ccomp compiler driver of Riviera-PRO/Active-HDL, as on
    the upstream branch.
    """
    python_include = Path(sys.executable).parent.resolve() / "include"
    python_libs = Path(sys.executable).parent.resolve() / "libs"
    ccomp = simulator_prefix / ("ccomp.exe" if sys.platform == "win32" else "ccomp")
    args = [str(ccomp), "-vhpi", "-dbg", "-verbose", "-o", f'"{target}"']
    args += ["-l", f"python{sys.version_info[0]}{sys.version_info[1]}", "-l", "python3", "-l", "_tkinter"]
    args += ["-I", f'"{python_include}"', "-I", f'"{SRC_PATH}"', "-L", f'"{python_libs}"']
    args += [" ".join(f'"{path}"' for path in sources)]
    _run(args, "compile the VHPI application")


def compile_vhpidirect_nvc_application(run_script_root, vu):
    """
    Compile VHPIDIRECT application for NVC.
    """
    path_to_shared_lib = (run_script_root / "vunit_out" / vu.get_simulator_name() / "libraries").resolve()
    if not path_to_shared_lib.exists():
        path_to_shared_lib.mkdir(parents=True, exist_ok=True)
    shared_lib = path_to_shared_lib / "python.so"
    path_to_python_include = (
        Path(sys.executable).parent.parent.resolve() / "include" / f"python{sys.version_info[0]}.{sys.version_info[1]}"
    )
    path_to_python_libs = Path(sys.executable).parent.parent.resolve() / "bin"
    python_shared_lib = f"libpython{sys.version_info[0]}.{sys.version_info[1]}"
    path_to_python_pkg = Path(__file__).parent.resolve() / "vhdl" / "python" / "src"

    c_file_paths = [path_to_python_pkg / "python_pkg_vhpidirect_nvc.c", path_to_python_pkg / "python_pkg.c"]

    for c_file_path in c_file_paths:
        args = [
            "gcc",
            "-c",
            "-I",
            str(path_to_python_include),
            str(c_file_path),
        ]

        proc = subprocess.run(args, capture_output=True, text=True, check=False, cwd=str(path_to_shared_lib / ".."))
        if proc.returncode != 0:
            print(proc.stdout)
            print(proc.stderr)
            raise RuntimeError("Failed to compile NVC VHPIDIRECT application")

    args = [
        "gcc",
        "-shared",
        "-fPIC",
        "-o",
        str(shared_lib),
        "python_pkg.o",
        "python_pkg_vhpidirect_nvc.o",
        "-l",
        python_shared_lib,
        "-L",
        str(path_to_python_libs),
    ]

    proc = subprocess.run(args, capture_output=True, text=True, check=False, cwd=str(path_to_shared_lib / ".."))
    if proc.returncode != 0:
        print(proc.stdout)
        print(proc.stderr)
        raise RuntimeError("Failed to link NVC VHPIDIRECT application")


def compile_vhpidirect_ghdl_application(run_script_root, vu):  # pylint: disable=unused-argument
    """
    Compile VHPIDIRECT application for GHDL.
    """
    # TODO: Avoid putting in root  # pylint: disable=fixme
    path_to_shared_lib = (run_script_root).resolve()
    if not path_to_shared_lib.exists():
        path_to_shared_lib.mkdir(parents=True, exist_ok=True)
    shared_lib = path_to_shared_lib / "python.so"

    path_to_python_include = Path(sysconfig.get_config_var("INCLUDEPY"))
    path_to_python_libs = Path(sysconfig.get_config_var("LIBDIR"))

    # path_to_python_libs = Path(sys.executable).parent.parent.resolve() / "bin"
    python_shared_lib = f"python{sys.version_info[0]}.{sys.version_info[1]}"
    path_to_python_pkg = Path(__file__).parent.resolve() / "vhdl" / "python" / "src"

    c_file_names = ["python_pkg_vhpidirect_ghdl.c", "python_pkg.c"]

    for c_file_name in c_file_names:
        args = [
            "gcc",
            "-c",
            "-I",
            str(path_to_python_include.as_posix()),
            str((path_to_python_pkg / c_file_name).as_posix()),
            "-o",
            str((path_to_shared_lib / (c_file_name[:-1] + "o")).as_posix()),
        ]
        print(" ".join(args))

        proc = subprocess.run(args, capture_output=True, text=True, check=False, cwd=str(path_to_shared_lib / ".."))
        if proc.returncode != 0:
            print(proc.stdout)
            print(proc.stderr)
            raise RuntimeError("Failed to compile GHDL VHPIDIRECT application")

    args = [
        "gcc",
        "-shared",
        "-fPIC",
        "-o",
        str(shared_lib),
        str(path_to_shared_lib / "python_pkg.o"),
        str(path_to_shared_lib / "python_pkg_vhpidirect_ghdl.o"),
        "-l" + python_shared_lib,
        "-L",
        str(path_to_python_libs),
    ]

    print(" ".join(args))

    proc = subprocess.run(args, capture_output=True, text=True, check=False, cwd=str(path_to_shared_lib / ".."))
    if proc.returncode != 0:
        print(proc.stdout)
        print(proc.stderr)
        raise RuntimeError("Failed to link GHDL VHPIDIRECT application")
