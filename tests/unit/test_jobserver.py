# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2014-2026, Lars Asplund lars.anders.asplund@gmail.com

"""
Test the jobserver lending the CPU budget of a test run to simulator processes
"""

# pylint: disable=protected-access

import os
import sys
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest import mock
from vunit import ostools
from vunit.jobserver import Jobserver, lend_order
from vunit.ostools import Process
from vunit.sim_if import SimulatorInterface
from vunit.test.runner import TestRunner as Runner
from vunit.test.report import TestReport as Report
from vunit.test.list import TestList as CaseList
from tests.common import with_tempdir
from tests.unit.test_test_runner import TestCaseMock as CaseMock

CHILD = r"""
import os, sys, time
flags = os.environ["MAKEFLAGS"]
print(flags)
read_fd, write_fd = map(int, flags.split("--jobserver-auth=")[1].split(","))
deadline = time.time() + 10
while True:
    try:
        token = os.read(read_fd, 1)
        break
    except BlockingIOError:
        assert time.time() < deadline
        time.sleep(0.01)
print("token", token.decode())
if sys.argv[1] == "return":
    os.write(write_fd, token)
"""


class FakeProcess(object):
    """A process that runs until told otherwise"""

    def __init__(self, pid):
        self.pid = pid
        self.returncode = None

    def poll(self):
        return self.returncode


@unittest.skipIf(ostools.IS_WINDOWS_SYSTEM, "The jobserver needs POSIX pipes")
class TestJobserver(unittest.TestCase):
    """
    Test the jobserver
    """

    def setUp(self):
        self.jobserver = None
        self.tests = ExitStack()
        self.processes = []
        self.fds = []

    def tearDown(self):
        self.tests.close()
        if self.jobserver is not None:
            self.jobserver.close()
        ostools.JOBSERVER = None

    def start_test(self):
        """Start a test whose simulator process joins the jobserver"""
        self.tests.enter_context(self.jobserver.running_test())
        process = FakeProcess(len(self.processes))
        fds = self.jobserver.new_pipe()
        self.jobserver.add(fds, process)
        self.processes.append(process)
        self.fds.append(fds)
        return process

    def lent(self):
        return [client.lent for client in self.jobserver._clients]

    def test_lends_breadth_first_newest_first(self):
        self.assertEqual(lend_order(4, [0, 0, 0]), [1, 1, 2])
        self.assertEqual(lend_order(3, [2, 0]), [0, 3])
        self.assertEqual(lend_order(2, [1, 0, 0]), [0, 1, 1])
        self.assertEqual(lend_order(0, [0, 0]), [0, 0])
        self.assertEqual(lend_order(-2, [0, 0]), [0, 0])
        self.assertEqual(lend_order(3, []), [])

    def test_total_cpu_slots_stay_num_threads(self):
        # -p 16 with 4 tests left: 12 free slots, 3 extra threads for each, 16 threads in all
        self.jobserver = Jobserver(16)
        for _ in range(4):
            self.start_test()
        self.assertEqual(self.lent(), [3, 3, 3, 3])
        self.assertEqual(4 + sum(self.lent()), 16)

    def test_new_tests_get_tokens_first(self):
        self.jobserver = Jobserver(7)
        self.start_test()
        self.assertEqual(self.lent(), [6])
        self.start_test()
        self.assertEqual(self.lent(), [2, 3])
        self.start_test()
        self.assertEqual(self.lent(), [1, 1, 2])

    def test_tokens_in_use_are_not_taken_back(self):
        self.jobserver = Jobserver(7)
        self.start_test()
        self.assertEqual(os.read(self.fds[0][0], 2), b"++")  # The first simulator takes two
        self.start_test()
        self.assertEqual(self.lent(), [2, 3])

    def test_tokens_return_when_a_test_finishes(self):
        self.jobserver = Jobserver(7)
        for _ in range(3):
            self.start_test()
        self.processes[2].returncode = 0
        self.tests.close()
        with self.jobserver.running_test(), self.jobserver.running_test():
            self.jobserver._rebalance()
            self.assertEqual(self.lent(), [2, 3])

    def test_warns_and_restores_tokens_of_a_process_that_exited_holding_them(self):
        self.jobserver = Jobserver(3)
        process = self.start_test()
        self.assertEqual(os.read(self.fds[0][0], 1), b"+")
        process.returncode = 1
        with self.assertLogs("vunit.jobserver", level="WARNING") as logs:
            self.jobserver._rebalance()
        self.assertIn("exited holding 1 jobserver token(s)", logs.output[0])
        self.assertEqual(self.lent(), [])
        self.tests.close()
        self.start_test()
        self.assertEqual(self.lent(), [2])

    def test_close_warns_unless_every_token_came_back(self):
        self.jobserver = Jobserver(3)
        self.start_test()
        os.read(self.fds[0][0], 1)
        with self.assertLogs("vunit.jobserver", level="WARNING") as logs:
            self.jobserver.close()
        self.assertIn("missing 1 of its 2 tokens", logs.output[0])

        self.jobserver = Jobserver(3)
        self.start_test()
        with self.assertNoLogs("vunit.jobserver", level="WARNING"):
            self.jobserver.close()

    def run_child(self, mode):
        """Run CHILD through Process and return its output"""
        output = []
        process = Process([sys.executable, "-c", CHILD, mode])
        process.consume_output(output.append)
        return output

    def test_child_takes_and_returns_a_token(self):
        self.jobserver = Jobserver(4)
        ostools.JOBSERVER = self.jobserver
        with self.jobserver.running_test():
            output = self.run_child("return")
        self.assertRegex(output[0], r"^-j4 --jobserver-auth=\d+,\d+$")
        self.assertEqual(output[1], "token +")
        with self.assertNoLogs("vunit.jobserver", level="WARNING"):
            self.jobserver._rebalance()
        self.assertEqual(self.lent(), [])

    def test_child_keeping_a_token_is_reported(self):
        self.jobserver = Jobserver(4)
        ostools.JOBSERVER = self.jobserver
        with self.jobserver.running_test():
            self.run_child("keep")
        with self.assertLogs("vunit.jobserver", level="WARNING"):
            self.jobserver._rebalance()

    def test_no_jobserver_by_default(self):
        self.assertFalse(SimulatorInterface.supports_jobserver)
        self.assertIsNone(ostools.JOBSERVER)
        output = []
        Process([sys.executable, "-c", "import os; print(os.environ.get('MAKEFLAGS'))"]).consume_output(output.append)
        self.assertEqual(output, [str(os.environ.get("MAKEFLAGS"))])

        report = Report()
        self.assertIsNone(Runner(report, "out", Path("run.py"), num_threads=4)._jobserver)
        self.assertIsNone(Runner(report, "out", Path("run.py"), num_threads=1, jobserver=True)._jobserver)
        self.assertIsNotNone(Runner(report, "out", Path("run.py"), num_threads=2, jobserver=True)._jobserver)

    @with_tempdir
    def test_runner_shares_the_jobserver_during_the_run(self, tempdir):
        seen = []

        def run(**kwargs):  # pylint: disable=unused-argument
            seen.append(ostools.JOBSERVER)
            return True

        test_list = CaseList()
        test_list.add_test(CaseMock("test1", run))
        runner = Runner(Report(), tempdir, Path("run.py"), num_threads=2, jobserver=True)
        with mock.patch("time.time", lambda: 1000):
            runner.run(test_list)
        self.assertEqual(seen, [runner._jobserver])
        self.assertIsNone(ostools.JOBSERVER)
