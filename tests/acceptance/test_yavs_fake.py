# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2014-2026, Lars Asplund lars.anders.asplund@gmail.com

"""
End to end test of the yavs interface against a fake yavs implementing the CLI contract
"""

import json
import os
import sys
import unittest
from pathlib import Path
from subprocess import Popen, call
from tests.common import check_report, create_tempdir

ROOT = Path(__file__).parent / "yavs"


@unittest.skipIf(sys.platform == "win32", "The fake yavs is a Python script without an .exe wrapper")
class TestYavsFake(unittest.TestCase):
    """
    Run a real run.py with VUNIT_SIMULATOR=yavs against the fake yavs
    """

    def _run(self, tempdir, *args, fail="", delay="0"):
        log = tempdir / "yavs.log"
        env = dict(
            os.environ,
            VUNIT_SIMULATOR="yavs",
            VUNIT_YAVS_PATH=str(ROOT / "fake_bin"),
            FAKE_YAVS_LOG=str(log),
            FAKE_YAVS_FAIL=fail,
            FAKE_YAVS_ANALYZE_DELAY=delay,
        )
        output_path = tempdir / "out with space"
        exit_code = call(
            [sys.executable, str(ROOT / "run.py"), "--clean", "-o", str(output_path), "-x", str(tempdir / "x.xml")]
            + list(args),
            env=env,
        )
        calls = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
        return exit_code, calls

    def test_pass(self):
        with create_tempdir() as tempdir:
            exit_code, calls = self._run(tempdir, "lib.tb_yavs.test_pass", "lib.tb_yavs.test with, comma: and colon")
            self.assertEqual(exit_code, 0)
            check_report(
                str(tempdir / "x.xml"),
                [("passed", "lib.tb_yavs.test_pass"), ("passed", "lib.tb_yavs.test with, comma: and colon")],
            )

        self.assertTrue(any(cmd[0] == "analyze" and cmd[-1].endswith("tb_yavs.vhd") for cmd in calls))
        runs = [cmd for cmd in calls if cmd[0] == "run"]
        self.assertEqual(len(runs), 2)
        self.assertIn("tb_yavs.tb", runs[0])
        self.assertIn("--exit-severity=error", runs[0])

    def test_fail(self):
        with create_tempdir() as tempdir:
            exit_code, _ = self._run(tempdir, fail="test_fail")
            self.assertEqual(exit_code, 1)
            check_report(
                str(tempdir / "x.xml"),
                [
                    ("passed", "lib.tb_yavs.test_pass"),
                    ("passed", "lib.tb_yavs.test with, comma: and colon"),
                    ("failed", "lib.tb_yavs.test_fail"),
                ],
            )

    def test_elaborate_only(self):
        with create_tempdir() as tempdir:
            exit_code, calls = self._run(tempdir, "--elaborate", "lib.tb_yavs.test_pass")
            self.assertEqual(exit_code, 0)
        self.assertTrue(any(cmd[0] == "elab" for cmd in calls))
        self.assertFalse(any(cmd[0] == "run" for cmd in calls))

    def _compile(self, *args):
        """
        Run all tests after compiling with args. Return the analyze calls, the libraries a call waited for and the
        manifest of vunit_lib.
        """
        with create_tempdir() as tempdir:
            exit_code, calls = self._run(tempdir, *args, delay="0.02")
            self.assertEqual(exit_code, 0)
            check_report(
                str(tempdir / "x.xml"),
                [
                    ("passed", "lib.tb_yavs.test_pass"),
                    ("passed", "lib.tb_yavs.test with, comma: and colon"),
                    ("passed", "lib.tb_yavs.test_fail"),
                ],
            )
            analyzes = [cmd for cmd in calls if cmd[0] == "analyze"]
            work = next(arg for cmd in analyzes for arg in cmd if arg.startswith("--work=vunit_lib:")).split(":", 1)[1]
            manifest = json.loads((Path(work) / "yavs-lib.json").read_text(encoding="utf-8"))
        waits = [cmd[1] for cmd in calls if cmd[0] == "lock-wait"]
        vunit_lib_files = [
            str(Path(arg).resolve())
            for cmd in analyzes
            if any(arg.startswith("--work=vunit_lib:") for arg in cmd)
            for arg in cmd[1:]
            if not arg.startswith("-")
        ]
        # Every file is recorded once, none lost by an analyze overwriting another one
        self.assertEqual(sorted(manifest), sorted(vunit_lib_files))
        self.assertEqual(len(set(manifest)), len(manifest))
        return analyzes, waits

    def test_parallel_compile(self):
        analyzes, waits = self._compile("--parallel-compile", "-p", "8")
        self.assertTrue(all(len([arg for arg in cmd[1:] if not arg.startswith("-")]) == 1 for cmd in analyzes))
        # Analyzes into vunit_lib overlapped, and the lock kept them apart
        self.assertTrue(any(wait.endswith("vunit_lib") for wait in waits))

    def test_batch_compile(self):
        analyzes, waits = self._compile("--batch-compile")
        self.assertTrue(any(len([arg for arg in cmd[1:] if not arg.startswith("-")]) > 1 for cmd in analyzes))
        self.assertEqual(waits, [])

    def test_parallel_batch_compile(self):
        analyzes, _ = self._compile("--batch-compile", "--parallel-compile", "-p", "8")
        self.assertTrue(any(len([arg for arg in cmd[1:] if not arg.startswith("-")]) > 1 for cmd in analyzes))

    def _analyze_concurrently(self, tempdir, no_lock):
        """Analyze 4 files into one library from 4 processes at the same time. Return the exit codes."""
        env = dict(os.environ, FAKE_YAVS_ANALYZE_DELAY="0.3")
        env.pop("FAKE_YAVS_LOG", None)
        if no_lock:
            env["FAKE_YAVS_NO_LOCK"] = "1"
        library = tempdir / "lib"
        processes = []
        for idx in range(4):
            source = tempdir / f"file{idx}.vhd"
            source.write_text("", encoding="utf-8")
            processes.append(
                Popen(  # pylint: disable=consider-using-with
                    [sys.executable, str(ROOT / "fake_bin" / "yavs"), "analyze", f"--work=lib:{library}", str(source)],
                    env=env,
                )
            )
        return [process.wait() for process in processes], library

    def test_concurrent_analyzes_into_a_library_are_serialized_by_the_lock(self):
        with create_tempdir() as tempdir:
            exit_codes, library = self._analyze_concurrently(tempdir, no_lock=False)
            self.assertEqual(exit_codes, [0, 0, 0, 0])
            manifest = json.loads((library / "yavs-lib.json").read_text(encoding="utf-8"))
            self.assertEqual(sorted(manifest), sorted(str(tempdir.resolve() / f"file{idx}.vhd") for idx in range(4)))

    def test_concurrent_analyzes_interleave_without_the_lock(self):
        # Shows that the test above fails when the lock is missing
        with create_tempdir() as tempdir:
            exit_codes, _ = self._analyze_concurrently(tempdir, no_lock=True)
            self.assertIn(3, exit_codes)
