# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2014-2026, Lars Asplund lars.anders.asplund@gmail.com

"""
Test the rsim interface
"""

import os
import subprocess
import unittest
from pathlib import Path
from shutil import rmtree
from types import SimpleNamespace
from unittest import mock
from vunit.sim_if.rsim import RSIMInterface, RSIMVersion
from vunit.sim_if.factory import SIMULATOR_FACTORY
from vunit.project import Project
from vunit.ostools import Process, renew_path, write_file
from vunit.exceptions import CompileError
from vunit.test.suites import encode_dict
from vunit.vhdl_standard import VHDL


def make_config(**kwargs):
    """Create a minimal test configuration."""
    config = SimpleNamespace(
        library_name="lib",
        entity_name="tb",
        architecture_name="arch",
        vhdl_configuration_name=None,
        vhdl_assert_stop_level="error",
        generics={},
        sim_options={},
    )
    for name, value in kwargs.items():
        setattr(config, name, value)
    return config


class TestRSIMVersion(unittest.TestCase):
    def test_from_version_output(self):
        version = RSIMVersion.from_output("rsim 0.4.0-dev (1a2b3c4 2026-10-18)\n")
        self.assertEqual(version, RSIMVersion("0.4.0"))
        self.assertEqual(str(version), "0.4.0")

    def test_versions_are_ordered(self):
        self.assertLess(RSIMVersion("0.4"), RSIMVersion("0.4.1"))
        self.assertLess(RSIMVersion("0.9.9"), RSIMVersion("0.10.0"))
        self.assertEqual(RSIMVersion("1.0"), RSIMVersion("1.0.0"))

    def test_unrecognized_version_warns_and_is_unknown(self):
        with self.assertWarnsRegex(RuntimeWarning, "version is unknown"):
            version = RSIMVersion.from_output("no version here")
        self.assertFalse(version.is_valid)
        with self.assertRaisesRegex(ValueError, "Cannot order unknown rsim versions"):
            version < RSIMVersion("1.0")  # pylint: disable=expression-not-assigned


class TestRSIMInterface(unittest.TestCase):
    """
    Test the rsim interface
    """

    def setUp(self):
        self.output_path = str(Path(__file__).parent / "test_rsim_interface_out")
        renew_path(self.output_path)
        self.cwd = os.getcwd()
        os.chdir(self.output_path)
        self.simif = RSIMInterface(prefix="prefix", output_path=self.output_path)

    def tearDown(self):
        os.chdir(self.cwd)
        if Path(self.output_path).exists():
            rmtree(self.output_path)

    @staticmethod
    def _project(*standards):
        project = Project()
        project.add_library("lib", "lib_path")
        project.add_library("other", str(Path("libs") / "other path"))
        for idx, std in enumerate(standards):
            write_file(f"file{idx}.vhd", "")
            project.add_source_file(f"file{idx}.vhd", "lib", file_type="vhdl", vhdl_standard=VHDL.standard(std))
        return project

    def test_is_registered_last(self):
        # Last so that an installed rsim is never picked over an established simulator by default
        self.assertIs(SIMULATOR_FACTORY.supported_simulators()[-1], RSIMInterface)
        self.assertIn("rsim.a_flags", SIMULATOR_FACTORY._compile_options)  # pylint: disable=protected-access
        self.assertIn("rsim.sim_flags", SIMULATOR_FACTORY._sim_options)  # pylint: disable=protected-access

    def test_capabilities(self):
        self.assertTrue(RSIMInterface.has_valid_exit_code())
        self.assertTrue(RSIMInterface.supports_vhdl_package_generics())
        self.assertTrue(RSIMInterface.supports_vhdl_call_paths())
        self.assertTrue(RSIMInterface.supports_vhpi())
        self.assertFalse(RSIMInterface.supports_coverage())

    @mock.patch("vunit.sim_if.check_output", autospec=True, return_value="")
    def test_compile_project(self, check_output):
        project = self._project("2019")
        self.simif.compile_project(project)
        check_output.assert_called_once_with(
            [
                str(Path("prefix") / "rsim"),
                "analyze",
                "--std=2019",
                "--work=lib:lib_path",
                "--map=lib:lib_path",
                f"--map=other:{Path('libs') / 'other path'}",
                "--color=always",
                "file0.vhd",
            ],
            env=self.simif.get_env(),
        )
        # The parent of each library is created, the library itself is left to rsim
        self.assertTrue(Path("libs").is_dir())
        self.assertFalse(Path("libs", "other path").exists())

    @mock.patch("vunit.sim_if.check_output", autospec=True, return_value="")
    def test_compile_project_extra_flags(self, check_output):
        project = self._project("2008")
        source_file = project.get_source_files_in_order()[0]
        source_file.set_compile_option("rsim.global_flags", ["--relaxed"])
        source_file.set_compile_option("rsim.a_flags", ["-Werror"])
        self.simif.compile_project(project)
        cmd = check_output.call_args[0][0]
        self.assertEqual(cmd[-3:], ["--relaxed", "-Werror", "file0.vhd"])
        self.assertIn("--std=2008", cmd)

    @mock.patch("vunit.sim_if.check_output", autospec=True)
    def test_compile_failure_is_reported(self, check_output):
        check_output.side_effect = subprocess.CalledProcessError(1, "rsim", output="error[E0001]: bad")
        with self.assertRaises(CompileError):
            self.simif.compile_project(self._project("2008"))

    def test_rejects_unsupported_and_mixed_standards(self):
        with self.assertRaisesRegex(RuntimeError, "VHDL-2008 and VHDL-2019 only, found VHDL-2002"):
            self.simif.setup_library_mapping(self._project("2002"))
        with self.assertRaisesRegex(RuntimeError, "mixed VHDL standards"):
            self.simif.setup_library_mapping(self._project("2008", "2019"))

    def _simulate(self, config, elaborate_only=False, gui=False, returncode=0):
        if gui:
            self.simif = RSIMInterface(prefix="prefix", output_path=self.output_path, gui=True, viewer="surfer")
        self.simif.setup_library_mapping(self._project("2008"))
        with (
            mock.patch("vunit.sim_if.rsim.Process", autospec=True) as process,
            mock.patch("vunit.sim_if.rsim.subprocess.call", autospec=True) as call,
            mock.patch.object(RSIMInterface, "find_executable", return_value=["surfer"]),
        ):
            process.NonZeroExitCode = Process.NonZeroExitCode
            if returncode:
                process.return_value.consume_output.side_effect = Process.NonZeroExitCode
            status = self.simif.simulate(self.output_path, "lib.tb", config, elaborate_only)
        return status, process.call_args[0][0], call

    def test_simulate(self):
        runner_cfg = encode_dict({"enabled_test_cases": "a, b,,c", "output path": "C:/out path/"})
        config = make_config(
            generics={"runner_cfg": runner_cfg, "width": 8},
            sim_options={
                "rsim.global_flags": ["--threads=2"],
                "rsim.elab_flags": ["--relaxed"],
                "rsim.sim_flags": ["--exit-severity=failure", "--stop-time=1ms"],
                "disable_ieee_warnings": True,
            },
        )
        status, cmd, call = self._simulate(config)
        self.assertTrue(status)
        self.assertEqual(
            cmd,
            [
                str(Path("prefix") / "rsim"),
                "run",
                "--std=2008",
                "--work=lib:lib_path",
                "--map=lib:lib_path",
                f"--map=other:{Path('libs') / 'other path'}",
                "--color=always",
                "--ieee-warnings=off",
                "--threads=2",
                "--relaxed",
                "--exit-severity=failure",
                "--stop-time=1ms",
                "--exit-severity=error",
                "tb.arch",
                f"-grunner_cfg={runner_cfg}",
                "-gwidth=8",
            ],
        )
        call.assert_not_called()

    def test_simulate_configuration_and_stop_level(self):
        config = make_config(vhdl_configuration_name="cfg", vhdl_assert_stop_level="failure")
        _, cmd, _ = self._simulate(config)
        self.assertEqual(cmd[-2:], ["--exit-severity=failure", "cfg"])

    def test_elaborate_only(self):
        config = make_config(sim_options={"rsim.elab_flags": ["--relaxed"], "rsim.sim_flags": ["--stop-time=1ms"]})
        _, cmd, _ = self._simulate(config, elaborate_only=True)
        self.assertEqual(cmd[1], "elab")
        self.assertIn("--relaxed", cmd)
        self.assertNotIn("--stop-time=1ms", cmd)
        self.assertFalse(any(arg.startswith("--exit-severity") for arg in cmd))

    def test_simulate_failure_is_not_swallowed(self):
        status, _, _ = self._simulate(make_config(), returncode=1)
        self.assertFalse(status)

    def test_gui_writes_fst_and_opens_viewer(self):
        config = make_config(sim_options={"rsim.viewer_script.gui": "init.tcl"})
        _, cmd, call = self._simulate(config, gui=True)
        wave_file = Path(self.output_path) / "rsim" / "tb.fst"
        self.assertIn(f"--wave={wave_file}", cmd)
        self.assertLess(cmd.index(f"--wave={wave_file}"), cmd.index("tb.arch"))
        call.assert_called_once_with(["surfer", str(wave_file), "--script", str(Path("init.tcl").resolve())])

    def test_generics_survive_windows_quoting(self):
        # rsim splits its command line with the MSVC rules, the inverse of list2cmdline
        runner_cfg = encode_dict({"enabled_test_cases": 'te"st, x', "output path": "C:\\out path\\"})
        arg = f"-grunner_cfg={runner_cfg}"
        line = subprocess.list2cmdline(["rsim", "run", "tb.arch", arg])
        if os.name == "nt":
            import ctypes  # pylint: disable=import-outside-toplevel

            argc = ctypes.c_int()
            argv = ctypes.windll.shell32.CommandLineToArgvW(line, ctypes.byref(argc))
            self.assertEqual(argv[argc.value - 1], arg)
        else:
            self.assertIn('\\"', line)
            self.assertTrue(line.endswith('"'))

    def test_check_version(self):
        with mock.patch.object(RSIMInterface, "_get_version_output", return_value="rsim 0.1.0\n"):
            self.assertEqual(RSIMInterface.check_version("prefix"), RSIMVersion("0.1.0"))

        with (
            mock.patch.object(RSIMInterface, "_get_version_output", return_value="garbage"),
            self.assertWarns(RuntimeWarning),
            self.assertRaisesRegex(RuntimeError, "Cannot parse the rsim version from 'garbage'"),
        ):
            RSIMInterface.check_version("prefix")

        with (
            mock.patch.object(RSIMInterface, "_get_version_output", side_effect=OSError("boom")),
            self.assertRaisesRegex(RuntimeError, "--version' failed: boom"),
        ):
            RSIMInterface.check_version("prefix")

        with (
            mock.patch("vunit.sim_if.rsim.RSIM_MIN_VERSION", RSIMVersion("1.0")),
            mock.patch.object(RSIMInterface, "_get_version_output", return_value="rsim 0.9.0"),
            self.assertRaisesRegex(RuntimeError, "rsim 0.9.0 is too old for VUnit, 1.0.0 or later is required"),
        ):
            RSIMInterface.check_version("prefix")

    def test_from_args_without_executable(self):
        args = SimpleNamespace(gui=False, viewer_fmt=None, viewer_args="", viewer=None)
        with (
            mock.patch.object(RSIMInterface, "find_prefix", return_value=None),
            self.assertRaisesRegex(FileNotFoundError, "RSIM executable not found"),
        ):
            RSIMInterface.from_args(args, self.output_path)
