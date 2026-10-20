# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2014-2026, Lars Asplund lars.anders.asplund@gmail.com

"""
Test the yavs interface
"""

import os
import subprocess
import threading
import unittest
from pathlib import Path
from shutil import rmtree
from types import SimpleNamespace
from unittest import mock
from vunit.sim_if.yavs import YavsInterface, YavsVersion, debug_port, write_response_file
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


class TestYavsVersion(unittest.TestCase):
    def test_from_version_output(self):
        version = YavsVersion.from_output("yavs 0.4.0-dev (1a2b3c4 2026-10-18)\n")
        self.assertEqual(version, YavsVersion("0.4.0"))
        self.assertEqual(str(version), "0.4.0")

    def test_versions_are_ordered(self):
        self.assertLess(YavsVersion("0.4"), YavsVersion("0.4.1"))
        self.assertLess(YavsVersion("0.9.9"), YavsVersion("0.10.0"))
        self.assertEqual(YavsVersion("1.0"), YavsVersion("1.0.0"))

    def test_unrecognized_version_warns_and_is_unknown(self):
        with self.assertWarnsRegex(RuntimeWarning, "version is unknown"):
            version = YavsVersion.from_output("no version here")
        self.assertFalse(version.is_valid)
        with self.assertRaisesRegex(ValueError, "Cannot order unknown yavs versions"):
            version < YavsVersion("1.0")  # pylint: disable=expression-not-assigned


class TestYavsInterface(unittest.TestCase):
    """
    Test the yavs interface
    """

    def setUp(self):
        self.output_path = str(Path(__file__).parent / "test_yavs_interface_out")
        renew_path(self.output_path)
        self.cwd = os.getcwd()
        os.chdir(self.output_path)
        self.simif = YavsInterface(prefix="prefix", output_path=self.output_path)

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
        # Last so that an installed yavs is never picked over an established simulator by default
        self.assertIs(SIMULATOR_FACTORY.supported_simulators()[-1], YavsInterface)
        self.assertIn("yavs.a_flags", SIMULATOR_FACTORY._compile_options)  # pylint: disable=protected-access
        self.assertIn("yavs.sim_flags", SIMULATOR_FACTORY._sim_options)  # pylint: disable=protected-access

    def test_capabilities(self):
        self.assertTrue(YavsInterface.has_valid_exit_code())
        self.assertTrue(YavsInterface.supports_vhdl_package_generics())
        self.assertTrue(YavsInterface.supports_vhdl_call_paths())
        self.assertTrue(YavsInterface.supports_vhpi())
        self.assertFalse(YavsInterface.supports_coverage())
        # yavs locks its libraries (CLI contract section 3)
        self.assertTrue(YavsInterface.supports_concurrent_compiles_into_library)
        self.assertTrue(YavsInterface.supports_batch_compile)
        # A target: record_dict_pkg fails on yavs until std.reflection lands
        self.assertTrue(YavsInterface.supports_vhdl_reflection())

    @mock.patch("vunit.sim_if.check_output", autospec=True)
    def test_compile_project_in_a_batch_with_a_file_list(self, check_output):
        project = self._project("2008", "2008", "2019")
        file_lists = []

        def check_output_side_effect(command, env=None):  # pylint: disable=unused-argument
            if command[-1].startswith("@"):
                file_lists.append(Path(command[-1][1:]).read_text(encoding="utf-8"))
            return ""

        check_output.side_effect = check_output_side_effect
        self.simif.compile_project(project, batch=True)

        # The files of a standard go in one call, the 2019 file in its own call with its own standard
        commands = {call[0][0][2]: call[0][0] for call in check_output.call_args_list}
        self.assertEqual(sorted(commands), ["--std=2008", "--std=2019"])
        for std, command in commands.items():
            self.assertEqual(
                command[:-1],
                [
                    str(Path("prefix") / "yavs"),
                    "analyze",
                    std,
                    "--work=lib:lib_path",
                    "--map=lib:lib_path",
                    f"--map=other:{Path('libs') / 'other path'}",
                    "--color=always",
                ],
            )
        self.assertTrue(commands["--std=2008"][-1].startswith("@"))
        # One file name per line
        self.assertEqual(len(file_lists), 1)
        self.assertTrue(file_lists[0].endswith("\n"))
        self.assertEqual(sorted(file_lists[0].splitlines()), ["file0.vhd", "file1.vhd"])
        self.assertEqual(commands["--std=2019"][-1], "file2.vhd")
        self.assertEqual(list(Path(self.output_path).glob("batch_*.txt")), [])

    @mock.patch("vunit.sim_if.check_output", autospec=True)
    def test_compile_project_into_a_library_concurrently(self, check_output):
        barrier = threading.Barrier(2, timeout=5)
        concurrent = []

        def check_output_side_effect(command, env=None):  # pylint: disable=unused-argument
            barrier.wait()  # Raises unless both files of lib are analyzed at the same time
            concurrent.append(command[-1])
            return ""

        check_output.side_effect = check_output_side_effect
        self.simif.compile_project(self._project("2008", "2008"), num_threads=2)
        self.assertEqual(sorted(concurrent), ["file0.vhd", "file1.vhd"])

    @mock.patch("vunit.sim_if.check_output", autospec=True, return_value="")
    def test_compile_project(self, check_output):
        project = self._project("2019")
        self.simif.compile_project(project)
        check_output.assert_called_once_with(
            [
                str(Path("prefix") / "yavs"),
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
        # The parent of each library is created, the library itself is left to yavs
        self.assertTrue(Path("libs").is_dir())
        self.assertFalse(Path("libs", "other path").exists())

    @mock.patch("vunit.sim_if.check_output", autospec=True, return_value="")
    def test_compile_project_extra_flags(self, check_output):
        project = self._project("2008")
        source_file = project.get_source_files_in_order()[0]
        source_file.set_compile_option("yavs.global_flags", ["--relaxed"])
        source_file.set_compile_option("yavs.a_flags", ["-Werror"])
        self.simif.compile_project(project)
        cmd = check_output.call_args[0][0]
        self.assertEqual(cmd[-3:], ["--relaxed", "-Werror", "file0.vhd"])
        self.assertIn("--std=2008", cmd)

    @mock.patch("vunit.sim_if.check_output", autospec=True)
    def test_compile_failure_is_reported(self, check_output):
        check_output.side_effect = subprocess.CalledProcessError(1, "yavs", output="error[E0001]: bad")
        with self.assertRaises(CompileError):
            self.simif.compile_project(self._project("2008"))

    def test_rejects_unsupported_standards(self):
        with self.assertRaisesRegex(RuntimeError, "VHDL-2008 and VHDL-2019 only, found VHDL-2002 for file1.vhd"):
            self.simif.setup_library_mapping(self._project("2008", "2002"))

    @mock.patch("vunit.sim_if.check_output", autospec=True, return_value="")
    def test_mixed_standards_are_analyzed_with_their_own_standard(self, check_output):
        self.simif.compile_project(self._project("2008", "2019"))
        stds = {call[0][0][-1]: call[0][0][2] for call in check_output.call_args_list}
        self.assertEqual(stds, {"file0.vhd": "--std=2008", "file1.vhd": "--std=2019"})

    def _simulate(self, config, elaborate_only=False, gui=False, returncode=0):
        if gui:
            self.simif = YavsInterface(prefix="prefix", output_path=self.output_path, gui=True)
        self.simif.setup_library_mapping(self._project("2008"))
        with (
            mock.patch("vunit.sim_if.yavs.Process", autospec=True) as process,
            mock.patch("vunit.sim_if.yavs.subprocess.call", autospec=True) as call,
        ):
            process.NonZeroExitCode = Process.NonZeroExitCode
            if returncode:
                process.return_value.consume_output.side_effect = Process.NonZeroExitCode
            status = self.simif.simulate(self.output_path, "lib.tb", config, elaborate_only)

        # The arguments after the subcommand are passed in a response file
        cmd = process.call_args[0][0]
        response_file = Path(self.output_path) / "yavs" / "args.rsp"
        sub = "elab" if elaborate_only else "run"
        gui_flag = ["--gui"] if gui and not elaborate_only else []
        self.assertEqual(cmd, [str(Path("prefix") / "yavs"), sub] + gui_flag + [f"@{response_file}"])
        args = response_file.read_bytes().decode("utf-8").split("\n")
        self.assertEqual(args.pop(), "")
        return status, cmd[:2] + args, call

    def test_simulate(self):
        runner_cfg = encode_dict({"enabled_test_cases": "a, b,,c", "output path": "C:/out path/"})
        config = make_config(
            generics={"runner_cfg": runner_cfg, "width": 8},
            sim_options={
                "yavs.global_flags": ["--threads=2"],
                "yavs.elab_flags": ["--relaxed"],
                "yavs.sim_flags": ["--exit-severity=failure", "--stop-time=1ms"],
                "disable_ieee_warnings": True,
            },
        )
        status, cmd, call = self._simulate(config)
        self.assertTrue(status)
        self.assertEqual(
            cmd,
            [
                str(Path("prefix") / "yavs"),
                "run",
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
        config = make_config(sim_options={"yavs.elab_flags": ["--relaxed"], "yavs.sim_flags": ["--stop-time=1ms"]})
        _, cmd, _ = self._simulate(config, elaborate_only=True)
        self.assertEqual(cmd[1], "elab")
        self.assertIn("--relaxed", cmd)
        self.assertNotIn("--stop-time=1ms", cmd)
        self.assertFalse(any(arg.startswith("--exit-severity") for arg in cmd))

    def test_simulate_failure_is_not_swallowed(self):
        status, _, _ = self._simulate(make_config(), returncode=1)
        self.assertFalse(status)

    def test_gui_runs_yavs_gui_with_the_same_arguments(self):
        config = make_config(
            generics={"runner_cfg": "x", "width": 8},
            sim_options={"yavs.sim_flags": ["--stop-time=1ms"], "yavs.global_flags": ["--threads=2"]},
        )
        _, plain, _ = self._simulate(config)
        _, gui, call = self._simulate(config, gui=True)
        self.assertEqual(gui, [arg for arg in plain if arg != "--color=always"])
        self.assertFalse(any("--wave" in arg for arg in gui))
        call.assert_not_called()

    def test_gui_elaborate_only_has_no_gui_flag(self):
        _, cmd, _ = self._simulate(make_config(), elaborate_only=True, gui=True)
        self.assertEqual(cmd[1], "elab")

    def test_debug_waits_for_a_debugger_in_the_run_only(self):
        self.simif = YavsInterface(prefix="prefix", output_path=self.output_path, debug_port=4711)
        _, cmd, _ = self._simulate(make_config())
        self.assertIn("--dap=4711", cmd)
        self.assertIn(f"--wave={Path(self.output_path) / 'wave.fst'}", cmd)
        self.assertLess(cmd.index("--dap=4711"), cmd.index("tb.arch"))
        _, cmd, _ = self._simulate(make_config(), elaborate_only=True)
        self.assertNotIn("--dap=4711", cmd)

    def test_debug_needs_exactly_one_test_and_one_thread(self):
        args = SimpleNamespace(num_threads=1)
        self.simif = YavsInterface(prefix="prefix", output_path=self.output_path, debug_port=4711)
        self.simif.check_selected_tests(1, args)
        with self.assertRaisesRegex(ValueError, "exactly one test but 2"):
            self.simif.check_selected_tests(2, args)
        with self.assertRaisesRegex(ValueError, "-p"):
            self.simif.check_selected_tests(1, SimpleNamespace(num_threads=2))
        gui = YavsInterface(prefix="prefix", output_path=self.output_path, gui=True, debug_port=4711)
        with self.assertRaisesRegex(ValueError, "--gui"):
            gui.check_selected_tests(1, args)

    def test_debug_port_from_environment(self):
        args = SimpleNamespace(debug=True, verbose=False, quiet=True)
        with mock.patch.dict(os.environ, {"YAVS_DAP_PORT": "5000"}):
            self.assertEqual(debug_port(args), 5000)
        self.assertTrue(args.verbose)
        self.assertFalse(args.quiet)
        with mock.patch.dict(os.environ, {"YAVS_DAP_PORT": "x"}), self.assertRaisesRegex(RuntimeError, "YAVS_DAP_PORT"):
            debug_port(args)
        self.assertIsNone(debug_port(SimpleNamespace(debug=False)))

    def test_response_file_keeps_arguments_verbatim(self):
        args = [
            "-grunner_cfg=" + encode_dict({"enabled_test_cases": 'te"st, x', "output path": "C:\\out path\\"}),
            "@not_a_response_file",
            "",
            "  spaces  ",
            "-gtext=\u00e5\u00e4\u00f6",
        ]
        file_name = write_response_file(Path(self.output_path) / "args.rsp", args)
        self.assertEqual(Path(file_name).read_bytes().decode("utf-8"), "".join(arg + "\n" for arg in args))

    def test_response_file_rejects_line_breaks(self):
        for arg in ["-gx=a\nb", "-gx=a\r"]:
            with self.assertRaisesRegex(ValueError, "cannot contain line breaks"):
                write_response_file(Path(self.output_path) / "args.rsp", [arg])

    def test_check_version(self):
        with mock.patch.object(YavsInterface, "_get_version_output", return_value="yavs 0.1.0\n"):
            self.assertEqual(YavsInterface.check_version("prefix"), YavsVersion("0.1.0"))

        with (
            mock.patch.object(YavsInterface, "_get_version_output", return_value="garbage"),
            self.assertWarns(RuntimeWarning),
            self.assertRaisesRegex(RuntimeError, "Cannot parse the yavs version from 'garbage'"),
        ):
            YavsInterface.check_version("prefix")

        with (
            mock.patch.object(YavsInterface, "_get_version_output", side_effect=OSError("boom")),
            self.assertRaisesRegex(RuntimeError, "--version' failed: boom"),
        ):
            YavsInterface.check_version("prefix")

        with (
            mock.patch("vunit.sim_if.yavs.YAVS_MIN_VERSION", YavsVersion("1.0")),
            mock.patch.object(YavsInterface, "_get_version_output", return_value="yavs 0.9.0"),
            self.assertRaisesRegex(RuntimeError, "yavs 0.9.0 is too old for VUnit, 1.0.0 or later is required"),
        ):
            YavsInterface.check_version("prefix")

    def test_from_args_compiles_in_batches(self):
        args = SimpleNamespace(gui=False, debug=False, batch_compile=False)
        with (
            mock.patch.object(YavsInterface, "find_prefix", return_value="prefix"),
            mock.patch("vunit.sim_if.yavs.check_executable"),
            mock.patch.object(YavsInterface, "check_version"),
        ):
            YavsInterface.from_args(args, self.output_path)
        self.assertTrue(args.batch_compile)

    def test_from_args_without_executable(self):
        args = SimpleNamespace(gui=False, viewer_fmt=None, viewer_args="", viewer=None)
        with (
            mock.patch.object(YavsInterface, "find_prefix", return_value=None),
            self.assertRaisesRegex(FileNotFoundError, "YAVS executable not found"),
        ):
            YavsInterface.from_args(args, self.output_path)
