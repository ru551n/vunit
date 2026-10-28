# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2014-2026, Lars Asplund lars.anders.asplund@gmail.com

"""
A GNU make style jobserver which lends the spare CPU of a parallel test run to the simulators.

Each simulator process gets a private pipe, and a broker thread lends tokens into the pipes by
priority. See "Sharing the CPU Budget with a Jobserver" in docs/vunit_packages.rst.
"""

import os
import threading
import logging
from contextlib import contextmanager
from . import ostools

LOGGER = logging.getLogger(__name__)
TOKEN = b"+"


def lend_order(free, held):
    """
    Return how many of ``free`` tokens to lend each client, given the tokens each client
    holds, oldest client first.

    Breadth first, since a second thread rarely doubles the speed of one test, and newest
    test first within a round, since old tests finish first and free their tokens anyway.
    """
    lend = [0] * len(held)
    for _ in range(free if held else 0):
        index = min(reversed(range(len(held))), key=lambda i: held[i] + lend[i])
        lend[index] += 1
    return lend


class _Client(object):
    """
    A simulator process and its private jobserver pipe
    """

    def __init__(self, fds, process):
        self.read_fd, self.write_fd = fds
        self.process = process
        self.lent = 0  # Tokens written to the pipe and not read back: idle in the pipe or in use

    def give(self, count):
        os.write(self.write_fd, TOKEN * count)
        self.lent += count

    def reclaim(self):
        """Take back the idle tokens in the pipe"""
        try:
            count = len(os.read(self.read_fd, self.lent)) if self.lent else 0
        except BlockingIOError:
            count = 0
        self.lent -= count
        return count

    def close(self):
        os.close(self.read_fd)
        os.close(self.write_fd)


class Jobserver(object):
    """
    The CPU budget of a run with num_threads workers, make style: one running test is free and
    each further running test takes one token, so with k tests running num_threads - k tokens
    are lent to the simulator processes.
    """

    def __init__(self, num_threads, interval=0.05):
        self._num_threads = num_threads
        self._running = 0
        self._clients = []  # Oldest first
        self._interval = interval
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None

    def new_pipe(self):
        """A private pipe for a new process, non-blocking so reclaiming never waits"""
        read_fd, write_fd = os.pipe()
        os.set_blocking(read_fd, False)
        return read_fd, write_fd

    def child_env(self, env, fds):
        """The environment of a process using the pipe fds"""
        env = dict(os.environ if env is None else env)
        env["MAKEFLAGS"] = f"-j{self._num_threads} --jobserver-auth={fds[0]},{fds[1]}"
        return env

    def add(self, fds, process):
        """Start lending to a process started with the pipe fds"""
        with self._lock:
            self._clients.append(_Client(fds, process))
            self._rebalance()

    @contextmanager
    def running_test(self):
        """Count a running test, which uses one CPU slot of its own"""
        with self._lock:
            self._running += 1
        try:
            yield
        finally:
            with self._lock:
                self._running -= 1

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        while not self._stop.wait(self._interval):
            with self._lock:
                self._rebalance()

    def _reclaim(self):
        """Take back every idle token and drop the processes that exited"""
        for client in list(self._clients):
            exited = client.process.poll() is not None  # Before reclaiming, to see tokens returned at exit
            client.reclaim()
            if exited:
                self._drop(client)

    def _rebalance(self):
        """Take back every idle token and lend the free ones again by priority"""
        self._reclaim()
        # Negative while a new test runs on tokens still in use elsewhere: lend nothing until they return
        free = self._num_threads - max(self._running, 1) - sum(client.lent for client in self._clients)
        for client, count in zip(self._clients, lend_order(free, [client.lent for client in self._clients])):
            if count:
                client.give(count)

    def _drop(self, client):
        """Close the pipe of an exited process; tokens it still held return to the pool since it is gone"""
        if client.lent and not ostools.PROGRAM_STATUS.is_shutting_down:
            LOGGER.warning(
                "Simulator process %i exited holding %i jobserver token(s) without returning them. "
                "VUnit put them back in the pool. A jobserver client must write back every token it reads.",
                client.process.pid,
                client.lent,
            )
        client.close()
        self._clients.remove(client)

    def close(self):
        """Stop lending, close all pipes and warn unless every token came back"""
        self._stop.set()
        if self._thread is not None:
            self._thread.join()
        with self._lock:
            self._reclaim()
            held = {client.process.pid: client.lent for client in self._clients if client.lent}
            for client in self._clients:
                client.close()
            self._clients = []

        if held and not ostools.PROGRAM_STATUS.is_shutting_down:
            LOGGER.warning(
                "The jobserver is missing %i of its %i tokens at the end of the run: "
                "simulator processes still running hold them (pid: tokens): %s.",
                sum(held.values()),
                self._num_threads - 1,
                held,
            )
