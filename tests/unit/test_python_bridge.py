# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2014-2026, Lars Asplund lars.anders.asplund@gmail.com

"""
Test the VHDL-to-Python bridge (vunit/python_bridge)

These tests must never invoke an HDL simulator. Compiling the small C bridge
library with the system C compiler, and reading/writing files, is fine.
"""

import os
import shutil
import re
import sys
import unittest
from glob import glob
from pathlib import Path
from unittest import mock

from vunit.python_bridge import bridge as bridge_setup, native_library, simulator_hooks
from vunit.builtins import Builtins, VHDL_PATH
from vunit.vhdl_standard import VHDLStandard
from vunit.sim_if import SimulatorInterface
from tests.common import create_tempdir


class _FakeSimulator:
    """
    Minimal stand-in for a simulator interface class, as used directly by
    bridge_setup.setup() (name, find_prefix(), determine_backend()).
    """

    def __init__(self, name, backend=None, prefix="prefix"):
        self.name = name
        self._backend = backend
        self._prefix = prefix

    def find_prefix(self):
        return self._prefix

    def determine_backend(self, prefix):  # pylint: disable=unused-argument
        return self._backend


def _autospec_simulator(name):
    """
    A simulator-interface autospec stand-in for use through Builtins, whose
    _add_files()/_add_vhdl_logging() call several other simulator-interface
    methods (supports_vhdl_contexts(), supports_vhdl_call_paths(), ...) that
    are irrelevant to what these tests check, so let them return truthy mocks.
    """
    simulator = mock.create_autospec(SimulatorInterface, instance=True)
    simulator.name = name
    return simulator


class _BridgeKey:
    """
    A plain, weak-referenceable object to use as a project key in bridge_setup._BRIDGES.
    (A bare ``object()`` cannot be weakly referenced.)
    """


def _write_context_stub(path: Path):
    path.write_text("context vunit_context is\nend context;\n", encoding="utf-8")


class TestFeatureIsolation(unittest.TestCase):
    """
    add_vhdl_builtins(python=False) must never touch the bridge.
    """

    def setUp(self):
        self.vu = mock.Mock()
        self.vu._project = mock.Mock()
        self.vu._project._libraries = []
        self.library_mock = mock.Mock()

        def add_library(name):
            self.vu._project._libraries.append(name)
            return self.library_mock

        self.vu.add_library.side_effect = add_library
        self.builtins = Builtins(self.vu, VHDLStandard("2008"), _autospec_simulator("nvc"))

    def test_python_false_never_touches_bridge(self):
        with mock.patch("vunit.python_bridge.bridge.setup") as setup_mock:
            self.builtins.add_vhdl_builtins(python=False)
        setup_mock.assert_not_called()

        added_files = [Path(call.args[0]) for call in self.library_mock.add_source_file.call_args_list]
        added_names = {p.name for p in added_files}
        self.assertIn("vunit_context.vhd", added_names)
        self.assertNotIn("python_pkg.vhd", added_names)
        self.assertNotIn("python_pkg-body.vhd", added_names)
        self.assertNotIn("python_ffi_pkg.vhd", added_names)
        self.assertTrue(any(p == VHDL_PATH / "vunit_context.vhd" for p in added_files))

    def test_importing_python_bridge_has_no_side_effects(self):
        # Re-importing must not create any files or directories or run any subprocess.
        def listing():
            return sorted(
                str(path) for path in native_library.PACKAGE_PATH.rglob("*") if "__pycache__" not in str(path)
            )

        before = listing()
        with mock.patch("subprocess.run") as run_mock:
            import importlib

            for module in (native_library, bridge_setup, simulator_hooks):
                importlib.reload(module)
        run_mock.assert_not_called()
        self.assertEqual(listing(), before)


class TestAddVhdlBuiltinsPython(unittest.TestCase):
    """
    add_vhdl_builtins(python=True) wiring, using a stubbed bridge_setup.setup
    so no compilation happens here.
    """

    def setUp(self):
        self.vu = mock.Mock()
        self.vu._project = mock.Mock()
        self.vu._project._libraries = []
        self.library_mock = mock.Mock()

        def add_library(name):
            self.vu._project._libraries.append(name)
            return self.library_mock

        self.vu.add_library.side_effect = add_library

    def _builtins(self, vhdl_standard="2008", simulator=None):
        return Builtins(self.vu, VHDLStandard(vhdl_standard), simulator or _autospec_simulator("nvc"))

    def test_rejects_pre_2008_vhdl(self):
        builtins = self._builtins(vhdl_standard="2002")
        with self.assertRaisesRegex(RuntimeError, "VHDL Python support only supports vhdl 2008 and later"):
            builtins.add_vhdl_builtins(python=True)

    def test_rejects_unsupported_simulator(self):
        # Reported like other VUnit setup errors: logged, then exit code 1
        builtins = self._builtins(simulator=_autospec_simulator("modelsim"))
        with self.assertLogs("vunit.builtins", level="ERROR") as logs, self.assertRaises(SystemExit) as exit_:
            builtins.add_vhdl_builtins(python=True)
        self.assertEqual(exit_.exception.code, 1)
        self.assertRegex(logs.output[0], "requires NVC or GHDL.*not supported for modelsim")

    def test_adds_expected_files_instead_of_original_context(self):
        with create_tempdir() as tempdir:
            fake_bridge = bridge_setup.PythonBridge(
                library_file=tempdir / "libFAKE.so",
                vhdl_files=[
                    tempdir / "vhdl" / "python_ffi_pkg.vhd",
                    bridge_setup.VHDL_SOURCE_PATH / "python_pkg.vhd",
                    bridge_setup.VHDL_SOURCE_PATH / "python_pkg-body.vhd",
                    tempdir / "vhdl" / "vunit_context.vhd",
                ],
            )
            with mock.patch("vunit.python_bridge.bridge.setup", return_value=fake_bridge) as setup_mock:
                builtins = self._builtins()
                builtins.add_vhdl_builtins(python=True)

            setup_mock.assert_called_once()
            added_files = {Path(call.args[0]) for call in self.library_mock.add_source_file.call_args_list}
            for expected in fake_bridge.vhdl_files:
                self.assertIn(expected, added_files)
            self.assertNotIn(VHDL_PATH / "vunit_context.vhd", added_files)

    def test_setup_accepts_simulator_class_none(self):
        # bridge_setup.setup() itself (not Builtins, which needs a real
        # simulator_class for unrelated calls) must accept simulator_class=None.
        with create_tempdir() as tempdir:
            context_file = tempdir / "vunit_context.vhd"
            _write_context_stub(context_file)
            fake_library_file = tempdir / "cache" / "libvunit_python_bridge.so"
            with mock.patch("vunit.python_bridge.bridge.prepare_library", return_value=fake_library_file):
                bridge = bridge_setup.setup(_BridgeKey(), tempdir / "out", None, context_file)
            self.assertEqual(bridge.library_file, fake_library_file)


class TestPythonContextGeneration(unittest.TestCase):
    """
    setup() generates vunit_context with python_pkg use-claused added.
    """

    def test_generated_context_is_original_plus_one_line(self):
        with create_tempdir() as tempdir:
            original = tempdir / "vunit_context.vhd"
            _write_context_stub(original)

            generated = bridge_setup._python_context(original)  # pylint: disable=protected-access
            original_text = original.read_text(encoding="utf-8")

            self.assertEqual(
                generated,
                original_text.replace("end context;", "  use vunit_lib.python_pkg.all;\nend context;", 1),
            )
            # Exactly one extra line
            self.assertEqual(len(generated.splitlines()), len(original_text.splitlines()) + 1)

    def test_raises_if_marker_missing(self):
        with create_tempdir() as tempdir:
            bad = tempdir / "vunit_context.vhd"
            bad.write_text("context vunit_context is\nend contextt;\n", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "Failed to find 'end context;'"):
                bridge_setup._python_context(bad)  # pylint: disable=protected-access


class TestFfiTemplateSubstitution(unittest.TestCase):
    """
    Library token substitution into python_ffi_pkg.vhd.
    """

    def setUp(self):
        self.tempdir_cm = create_tempdir()
        self.tempdir = self.tempdir_cm.__enter__()
        self.addCleanup(self.tempdir_cm.__exit__, None, None, None)
        self.context_file = self.tempdir / "vunit_context.vhd"
        _write_context_stub(self.context_file)

    def _setup(self, simulator, library_file_name="libvunit_python_bridge.so"):
        fake_library_file = self.tempdir / "cache" / library_file_name
        with mock.patch("vunit.python_bridge.bridge.prepare_library", return_value=fake_library_file):
            return bridge_setup.setup(_BridgeKey(), self.tempdir / "out", simulator, self.context_file)

    def _ffi_text(self, bridge):
        for path in bridge.vhdl_files:
            if path.name == "python_ffi_pkg.vhd":
                return path.read_text(encoding="utf-8")
        self.fail("python_ffi_pkg.vhd not found among bridge.vhdl_files")
        return ""

    def test_token_is_library_file_name_for_nvc(self):
        bridge = self._setup(_FakeSimulator("nvc"))
        text = self._ffi_text(bridge)
        self.assertIn('"VHPIDIRECT libvunit_python_bridge.so vpy_begin"', text)

    def test_token_is_library_file_name_for_ghdl_mcode(self):
        bridge = self._setup(_FakeSimulator("ghdl", backend="mcode"))
        text = self._ffi_text(bridge)
        self.assertIn('"VHPIDIRECT libvunit_python_bridge.so vpy_begin"', text)

    def test_token_is_library_file_name_for_ghdl_llvm_jit(self):
        bridge = self._setup(_FakeSimulator("ghdl", backend="llvm-jit"))
        text = self._ffi_text(bridge)
        self.assertIn('"VHPIDIRECT libvunit_python_bridge.so vpy_begin"', text)

    def test_token_is_link_flag_for_ghdl_llvm(self):
        bridge = self._setup(_FakeSimulator("ghdl", backend="llvm"))
        text = self._ffi_text(bridge)
        self.assertIn('"VHPIDIRECT -lvunit_python_bridge vpy_begin"', text)

    def test_token_is_link_flag_for_ghdl_gcc(self):
        bridge = self._setup(_FakeSimulator("ghdl", backend="gcc"))
        text = self._ffi_text(bridge)
        self.assertIn('"VHPIDIRECT -lvunit_python_bridge vpy_begin"', text)

    def test_no_remaining_library_placeholder(self):
        bridge = self._setup(_FakeSimulator("nvc"))
        text = self._ffi_text(bridge)
        self.assertNotIn("{library}", text)

    def test_all_vhpidirect_tokens_fit_ghdl_limit(self):
        for simulator in (
            _FakeSimulator("nvc"),
            _FakeSimulator("ghdl", backend="mcode"),
            _FakeSimulator("ghdl", backend="llvm"),
            _FakeSimulator("ghdl", backend="gcc"),
        ):
            bridge = self._setup(simulator)
            text = self._ffi_text(bridge)
            tokens = re.findall(r'VHPIDIRECT\s+(\S+)\s+\S+"', text)
            self.assertTrue(tokens, "no VHPIDIRECT tokens found")
            for token in tokens:
                self.assertLessEqual(len(token), 32, f"token {token!r} exceeds GHDL's 32 character limit")
                self.assertNotIn(" ", token)


@unittest.skipIf(sys.platform == "win32", "POSIX build/cache behavior")
class TestPosixBuildAndCache(unittest.TestCase):
    """
    Compilation and caching of the native bridge library on Linux/macOS.

    Only a couple of real compiles happen here (gcc + Python headers are
    available in this environment); everything else is asserted not to compile.
    """

    def setUp(self):
        self.tempdir_cm = create_tempdir()
        self.tempdir = self.tempdir_cm.__enter__()
        self.addCleanup(self.tempdir_cm.__exit__, None, None, None)
        self.context_file = self.tempdir / "vunit_context.vhd"
        _write_context_stub(self.context_file)

    def _setup(self, output_path=None):
        return bridge_setup.setup(_BridgeKey(), output_path or self.tempdir / "out", None, self.context_file)

    def test_first_setup_compiles_and_names_library(self):
        bridge = self._setup()
        self.assertTrue(bridge.library_file.is_file())
        self.assertEqual(bridge.library_file.name, "libvunit_python_bridge.so")

    def test_second_setup_reuses_cache_without_compiling(self):
        self._setup()
        with mock.patch("subprocess.run") as run_mock:
            bridge = self._setup()
        run_mock.assert_not_called()
        self.assertTrue(bridge.library_file.is_file())

    def test_changed_source_gets_new_cache_dir_and_recompiles(self):
        first = self._setup()

        modified_native = self.tempdir / "native"
        shutil.copytree(native_library.NATIVE_PATH, modified_native)
        with (modified_native / "error.c").open("a", encoding="utf-8") as fptr:
            fptr.write("\n/* test tweak */\n")

        with mock.patch("vunit.python_bridge.native_library.NATIVE_PATH", modified_native):
            second = self._setup()

        self.assertNotEqual(first.library_file.parent, second.library_file.parent)
        self.assertTrue(second.library_file.is_file())

    def test_compile_failure_raises_with_compiler_output(self):
        fake_proc = mock.Mock(returncode=1, stdout=b"bogus.c:1:1: error: fake failure\n")
        with mock.patch("subprocess.run", return_value=fake_proc):
            with self.assertRaisesRegex(RuntimeError, "fake failure"):
                self._setup()

    def test_missing_python_h_raises_actionable_error(self):
        empty_include_dir = self.tempdir / "no_headers"
        empty_include_dir.mkdir()
        real_get_paths = native_library.sysconfig.get_paths

        def fake_get_paths():
            paths = dict(real_get_paths())
            paths["include"] = str(empty_include_dir)
            paths["platinclude"] = str(empty_include_dir)
            return paths

        with mock.patch("vunit.python_bridge.native_library.sysconfig.get_paths", side_effect=fake_get_paths):
            with self.assertRaisesRegex(RuntimeError, "Python.h"):
                self._setup()

    def test_static_only_python_raises(self):
        real_get_config_var = native_library.sysconfig.get_config_var

        def fake_get_config_var(name):
            if name == "Py_ENABLE_SHARED":
                return 0
            return real_get_config_var(name)

        with mock.patch("vunit.python_bridge.native_library.sysconfig.get_config_var", side_effect=fake_get_config_var):
            with self.assertRaisesRegex(RuntimeError, "shared Python library"):
                self._setup()

    def test_free_threaded_python_raises(self):
        real_get_config_var = native_library.sysconfig.get_config_var

        def fake_get_config_var(name):
            if name == "Py_GIL_DISABLED":
                return 1
            return real_get_config_var(name)

        with mock.patch("vunit.python_bridge.native_library.sysconfig.get_config_var", side_effect=fake_get_config_var):
            with self.assertRaisesRegex(RuntimeError, "free-threaded"):
                self._setup()

    def test_no_prebuilt_shared_library_in_repository(self):
        matches = glob(str(native_library.PACKAGE_PATH / "**" / "*.so"), recursive=True)
        matches += glob(str(bridge_setup.VHDL_SOURCE_PATH.parent / "**" / "*.so"), recursive=True)
        self.assertEqual(matches, [])


class TestConfigFile(unittest.TestCase):
    """
    _config_text / _write_if_changed / _base_dir
    """

    def test_config_keys_linux(self):
        with mock.patch("sys.platform", "linux"):
            text = bridge_setup._config_text()  # pylint: disable=protected-access
        keys = dict(line.split("=", 1) for line in text.splitlines())
        self.assertEqual(set(keys), {"executable", "prefix", "runtime", "base_dir"})
        self.assertEqual(keys["executable"], sys.executable)
        self.assertEqual(keys["prefix"], sys.prefix)
        self.assertEqual(keys["runtime"], str(bridge_setup.RUNTIME_SOURCE))

    def test_config_keys_windows_include_python_dll(self):
        with (
            mock.patch("sys.platform", "win32"),
            mock.patch("vunit.python_bridge.bridge.windows_python_dll", return_value=r"C:\python.dll"),
        ):
            text = bridge_setup._config_text()  # pylint: disable=protected-access
        keys = dict(line.split("=", 1) for line in text.splitlines())
        self.assertEqual(keys["python_dll"], r"C:\python.dll")

    def test_write_if_changed_does_not_rewrite_identical_content(self):
        with create_tempdir() as tempdir:
            path = tempdir / "file.txt"
            bridge_setup._write_if_changed(path, "hello")  # pylint: disable=protected-access
            mtime_before = path.stat().st_mtime_ns
            bridge_setup._write_if_changed(path, "hello")  # pylint: disable=protected-access
            self.assertEqual(path.stat().st_mtime_ns, mtime_before)

    def test_write_if_changed_rewrites_changed_content(self):
        with create_tempdir() as tempdir:
            path = tempdir / "file.txt"
            bridge_setup._write_if_changed(path, "hello")  # pylint: disable=protected-access
            bridge_setup._write_if_changed(path, "world")  # pylint: disable=protected-access
            self.assertEqual(path.read_text(encoding="utf-8"), "world")

    def test_base_dir_uses_directory_of_run_script(self):
        with create_tempdir() as tempdir:
            script = tempdir / "run.py"
            script.write_text("", encoding="utf-8")
            with mock.patch("sys.argv", [str(script)]):
                self.assertEqual(bridge_setup._base_dir(), tempdir.resolve())  # pylint: disable=protected-access

    def test_base_dir_falls_back_to_cwd_when_argv0_not_a_file(self):
        with mock.patch("sys.argv", ["not_a_real_script.py"]):
            self.assertEqual(bridge_setup._base_dir(), Path.cwd())  # pylint: disable=protected-access

    def test_config_paths_with_spaces_and_unicode_written_as_utf8(self):
        with create_tempdir() as tempdir:
            weird_dir = tempdir / "weird dir \u00e5\u00e4\u00f6 \u65e5\u672c\u8a9e"
            weird_dir.mkdir()
            script = weird_dir / "run.py"
            script.write_text("", encoding="utf-8")
            with mock.patch("sys.argv", [str(script)]), mock.patch("sys.platform", "linux"):
                text = bridge_setup._config_text()  # pylint: disable=protected-access
            self.assertIn(str(weird_dir.resolve()), text)
            path = tempdir / "cfg"
            bridge_setup._write_if_changed(path, text)  # pylint: disable=protected-access
            self.assertEqual(path.read_text(encoding="utf-8"), text)

    def test_line_break_in_path_raises(self):
        with mock.patch("sys.executable", "/usr/bin/py\nthon"):
            with self.assertRaisesRegex(RuntimeError, "line breaks"):
                bridge_setup._config_text()  # pylint: disable=protected-access


class TestWindowsDllSelection(unittest.TestCase):
    """
    Windows prebuilt-DLL selection logic. Testable on any platform since it
    is pure logic + file copying, gated only by explicit patches here (never
    by sys.platform inside these helper functions themselves).
    """

    def test_windows_dll_name_for_supported_versions(self):
        for minor in range(10, 15):
            self.assertEqual(
                native_library.windows_dll_name((3, minor)),
                f"vunit_python_bridge-cp3{minor}-win_amd64.dll",
            )

    def _prepare(self, tempdir, dll_bytes, version_info=(3, 12)):
        binary_path = tempdir / "bin"
        binary_path.mkdir(exist_ok=True)
        name = native_library.windows_dll_name(version_info)
        (binary_path / name).write_bytes(dll_bytes)
        root = tempdir / "root"
        with (
            mock.patch("vunit.python_bridge.native_library.BINARY_PATH", binary_path),
            mock.patch("sys.version_info", version_info),
            mock.patch("vunit.python_bridge.native_library.sysconfig.get_platform", return_value="win-amd64"),
            mock.patch("subprocess.run") as run_mock,
            mock.patch("shutil.which") as which_mock,
        ):
            target = native_library._prepare_windows_library(root)  # pylint: disable=protected-access
        run_mock.assert_not_called()
        which_mock.assert_not_called()
        return target

    def test_copies_selected_dll_as_vunit_python_bridge_dll(self):
        with create_tempdir() as tempdir:
            target = self._prepare(tempdir, b"fake-dll-content")
            self.assertEqual(target.name, "vunit_python_bridge.dll")
            self.assertEqual(target.read_bytes(), b"fake-dll-content")

    def test_reuses_existing_file_when_content_unchanged(self):
        with create_tempdir() as tempdir:
            first = self._prepare(tempdir, b"same-content")
            mtime_before = first.stat().st_mtime_ns
            second = self._prepare(tempdir, b"same-content")
            self.assertEqual(first, second)
            self.assertEqual(second.stat().st_mtime_ns, mtime_before)

    def test_new_directory_when_content_changes(self):
        with create_tempdir() as tempdir:
            first = self._prepare(tempdir, b"version-one")
            second = self._prepare(tempdir, b"version-two")
            self.assertNotEqual(first.parent, second.parent)

    def test_missing_dll_for_running_version_raises_actionable_error(self):
        root = None
        with create_tempdir() as tempdir:
            binary_path = tempdir / "bin"
            binary_path.mkdir()
            root = tempdir / "root"
            with (
                mock.patch("vunit.python_bridge.native_library.BINARY_PATH", binary_path),
                mock.patch("sys.version_info", (3, 12)),
                mock.patch("vunit.python_bridge.native_library.sysconfig.get_platform", return_value="win-amd64"),
                mock.patch("subprocess.run") as run_mock,
            ):
                with self.assertRaisesRegex(RuntimeError, "No prebuilt Python bridge DLL"):
                    native_library._prepare_windows_library(root)  # pylint: disable=protected-access
            run_mock.assert_not_called()

    def test_non_win_amd64_platform_raises(self):
        with mock.patch("vunit.python_bridge.native_library.sysconfig.get_platform", return_value="mingw"):
            with self.assertRaisesRegex(RuntimeError, "64-bit"):
                native_library._prepare_windows_library(Path("root"))  # pylint: disable=protected-access


class TestSimulatorHooks(unittest.TestCase):
    """
    nvc_run_flags / ghdl_elab_flags / ghdl_run_env
    """

    def setUp(self):
        self.project = _BridgeKey()
        self.addCleanup(bridge_setup._BRIDGES.pop, self.project, None)  # pylint: disable=protected-access

    def _register(self, library_file):
        bridge = bridge_setup.PythonBridge(library_file=library_file, vhdl_files=[])
        bridge_setup._BRIDGES[self.project] = bridge  # pylint: disable=protected-access
        return bridge

    def test_nvc_run_flags_empty_without_bridge(self):
        self.assertEqual(simulator_hooks.nvc_run_flags(self.project), [])

    def test_nvc_run_flags_with_bridge(self):
        bridge = self._register(Path("/some/dir/libvunit_python_bridge.so"))
        self.assertEqual(simulator_hooks.nvc_run_flags(self.project), [f"--load={bridge.library_file!s}"])

    def test_ghdl_elab_flags_empty_without_bridge(self):
        self.assertEqual(simulator_hooks.ghdl_elab_flags(self.project, "llvm"), [])

    def test_ghdl_elab_flags_empty_for_mcode_and_jit(self):
        bridge_dir = Path("/some/dir")
        self._register(bridge_dir / "libvunit_python_bridge.so")
        self.assertEqual(simulator_hooks.ghdl_elab_flags(self.project, "mcode"), [])
        self.assertEqual(simulator_hooks.ghdl_elab_flags(self.project, "llvm-jit"), [])

    def test_ghdl_elab_flags_for_linking_backends(self):
        bridge_dir = Path("/some/dir")
        self._register(bridge_dir / "libvunit_python_bridge.so")
        for backend in ("llvm", "gcc"):
            self.assertEqual(
                simulator_hooks.ghdl_elab_flags(self.project, backend),
                [f"-Wl,-L{bridge_dir!s}"],
            )

    def test_ghdl_run_env_unchanged_without_bridge(self):
        env = {"FOO": "bar"}
        result = simulator_hooks.ghdl_run_env(self.project, env)
        self.assertEqual(result, env)
        self.assertIs(result, env)

    def test_ghdl_run_env_prepends_ld_library_path_without_mutating_input(self):
        bridge_dir = Path("/some/dir")
        self._register(bridge_dir / "libvunit_python_bridge.so")
        env = {"LD_LIBRARY_PATH": "/existing/path"}
        with mock.patch("vunit.python_bridge.native_library.sys.platform", "linux"):
            result = simulator_hooks.ghdl_run_env(self.project, env)
        self.assertEqual(
            result["LD_LIBRARY_PATH"],
            str(bridge_dir) + os.pathsep + "/existing/path",
        )
        # Input must not be mutated.
        self.assertEqual(env, {"LD_LIBRARY_PATH": "/existing/path"})
        self.assertIsNot(result, env)

    def test_ghdl_run_env_uses_path_variable_on_windows(self):
        bridge_dir = Path("/some/dir")
        self._register(bridge_dir / "libvunit_python_bridge.so")
        with mock.patch("vunit.python_bridge.native_library.sys.platform", "win32"):
            result = simulator_hooks.ghdl_run_env(self.project, {})
        self.assertEqual(result["PATH"], str(bridge_dir))
        self.assertNotIn("LD_LIBRARY_PATH", result)


class TestSimulatorIntegration(unittest.TestCase):
    """
    Confirm the hooks are wired into the real NVC/GHDL command builders at the
    right place.

    Entity() writes a stub source file relative to the cwd, so run these from
    a scratch directory (matching the convention in test_ghdl_interface.py).
    """

    def setUp(self):
        self.tempdir_cm = create_tempdir()
        self.scratch_dir = self.tempdir_cm.__enter__()
        self.addCleanup(self.tempdir_cm.__exit__, None, None, None)
        self.cwd = os.getcwd()
        os.chdir(self.scratch_dir)
        self.addCleanup(os.chdir, self.cwd)

    def test_ghdl_get_command_only_adds_wl_L_for_linking_backends(self):
        from tests.unit.test_test_bench import Entity  # pylint: disable=import-outside-toplevel
        from vunit.sim_if.ghdl import GHDLInterface  # pylint: disable=import-outside-toplevel
        from vunit.project import Project  # pylint: disable=import-outside-toplevel
        from vunit.configuration import Configuration  # pylint: disable=import-outside-toplevel
        from vunit.vhdl_standard import VHDL  # pylint: disable=import-outside-toplevel

        design_unit = Entity("tb_entity", file_name=str(Path("tempdir") / "file.vhd"))
        design_unit.original_file_name = str(Path("tempdir") / "other_path" / "original_file.vhd")
        design_unit.generic_names = ["runner_cfg", "tb_path"]
        config = Configuration("name", design_unit)

        for backend, expect_flag in (("llvm", True), ("gcc", True), ("mcode", False), ("llvm-jit", False)):
            with mock.patch.object(GHDLInterface, "determine_version", return_value=5.0):
                simif = GHDLInterface(prefix="prefix", output_path="", backend=backend)
            simif._vhdl_standard = VHDL.standard("2008")  # pylint: disable=protected-access
            simif._project = Project()  # pylint: disable=protected-access
            simif._project.add_library("lib", "lib_path")  # pylint: disable=protected-access

            bridge = bridge_setup.PythonBridge(
                library_file=Path("/bridge/dir/libvunit_python_bridge.so"), vhdl_files=[]
            )
            bridge_setup._BRIDGES[simif._project] = bridge  # pylint: disable=protected-access
            try:
                cmd = simif._get_command(  # pylint: disable=protected-access
                    config, str(Path("output_path") / "ghdl"), True, False, "tb_entity", None
                )
            finally:
                bridge_setup._BRIDGES.pop(simif._project, None)  # pylint: disable=protected-access

            flag = f"-Wl,-L{Path('/bridge/dir')!s}"
            if expect_flag:
                self.assertIn(flag, cmd, f"backend={backend}")
            else:
                self.assertNotIn(flag, cmd, f"backend={backend}")

    def test_nvc_simulate_loads_bridge_after_dash_r(self):
        from vunit.sim_if.nvc import NVCInterface  # pylint: disable=import-outside-toplevel
        from tests.unit.test_test_bench import Entity  # pylint: disable=import-outside-toplevel
        from vunit.project import Project  # pylint: disable=import-outside-toplevel
        from vunit.configuration import Configuration  # pylint: disable=import-outside-toplevel
        from vunit.vhdl_standard import VHDL  # pylint: disable=import-outside-toplevel

        design_unit = Entity("tb_entity", file_name=str(Path("tempdir") / "file.vhd"))
        design_unit.original_file_name = str(Path("tempdir") / "other_path" / "original_file.vhd")
        design_unit.generic_names = ["runner_cfg", "tb_path"]
        config = Configuration("name", design_unit)

        with create_tempdir() as tempdir:
            with mock.patch.object(NVCInterface, "determine_version", return_value=(1, 99)):
                simif = NVCInterface(output_path=str(tempdir), prefix="prefix", num_threads=1)
            simif._vhdl_standard = VHDL.standard("2008")  # pylint: disable=protected-access
            simif._project = Project()  # pylint: disable=protected-access
            simif._project.add_library("lib", str(tempdir))  # pylint: disable=protected-access

            bridge = bridge_setup.PythonBridge(
                library_file=Path("/bridge/dir/libvunit_python_bridge.so"), vhdl_files=[]
            )
            bridge_setup._BRIDGES[simif._project] = bridge  # pylint: disable=protected-access

            captured = {}

            class _FakeProcess:
                def __init__(self, cmd, env=None):
                    captured["cmd"] = cmd

                def consume_output(self):
                    pass

            try:
                with mock.patch("vunit.sim_if.nvc.Process", _FakeProcess):
                    simif.simulate(str(tempdir), "tb_entity", config, elaborate_only=False)
            finally:
                bridge_setup._BRIDGES.pop(simif._project, None)  # pylint: disable=protected-access

            cmd = captured["cmd"]
            self.assertIn("-r", cmd)
            load = f"--load={Path('/bridge/dir/libvunit_python_bridge.so')!s}"
            self.assertIn(load, cmd)
            self.assertGreater(cmd.index(load), cmd.index("-r"))

    def test_nvc_simulate_has_no_load_flag_without_bridge(self):
        from vunit.sim_if.nvc import NVCInterface  # pylint: disable=import-outside-toplevel
        from tests.unit.test_test_bench import Entity  # pylint: disable=import-outside-toplevel
        from vunit.project import Project  # pylint: disable=import-outside-toplevel
        from vunit.configuration import Configuration  # pylint: disable=import-outside-toplevel
        from vunit.vhdl_standard import VHDL  # pylint: disable=import-outside-toplevel

        design_unit = Entity("tb_entity", file_name=str(Path("tempdir") / "file.vhd"))
        design_unit.original_file_name = str(Path("tempdir") / "other_path" / "original_file.vhd")
        design_unit.generic_names = ["runner_cfg", "tb_path"]
        config = Configuration("name", design_unit)

        with create_tempdir() as tempdir:
            with mock.patch.object(NVCInterface, "determine_version", return_value=(1, 99)):
                simif = NVCInterface(output_path=str(tempdir), prefix="prefix", num_threads=1)
            simif._vhdl_standard = VHDL.standard("2008")  # pylint: disable=protected-access
            simif._project = Project()  # pylint: disable=protected-access
            simif._project.add_library("lib", str(tempdir))  # pylint: disable=protected-access

            captured = {}

            class _FakeProcess:
                def __init__(self, cmd, env=None):
                    captured["cmd"] = cmd

                def consume_output(self):
                    pass

            with mock.patch("vunit.sim_if.nvc.Process", _FakeProcess):
                simif.simulate(str(tempdir), "tb_entity", config, elaborate_only=False)

            self.assertFalse(any(flag.startswith("--load=") for flag in captured["cmd"]))


if __name__ == "__main__":
    unittest.main()
