# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2014-2026, Lars Asplund lars.anders.asplund@gmail.com

"""
Embedded Python
---------------

Demonstrates calling Python from VHDL with ``add_python()``: executing Python
code and calling Python functions, for example NumPy/Matplotlib reference
models, from a testbench. Some tests need optional Python packages
(``PySimpleGUI``, ``python-constraint``, ``crccheck`` and ``matplotlib``) or
demonstrate error reporting and are excluded by default; see
:ref:`python_bridge`.
"""

from pathlib import Path
from vunit import VUnit
from vunit.python_pkg import compile_vhpi_application, compile_fli_application


def hello_world():
    print("Hello World")


class Plot:

    def __init__(self, x_points, y_limits, title, x_label, y_label):
        from matplotlib import pyplot as plt

        # Create plot with a line based on x and y vectors before they have been calculated
        # Starting with an uncalculated line and updating it as we calculate more points
        # is a trick to make the rendering of the plot quicker. This is not a bottleneck
        # created by the VHDL package but inherent to the Python matplotlib package.
        fig = plt.figure()
        plt.xlabel(x_label)
        plt.ylabel(y_label)
        plt.title(title)
        plt.xlim(x_points[0], x_points[-1])
        plt.ylim(*y_limits)
        x_vector = [x_points[0]] * len(x_points)
        y_vector = [(y_limits[0] + y_limits[1]) / 2] * len(x_points)
        (line,) = plt.plot(x_vector, y_vector, "r-")
        fig.canvas.draw()
        fig.canvas.flush_events()
        plt.show(block=False)

        self.plt = plt
        self.fig = fig
        self.x_vector = x_vector
        self.y_vector = y_vector
        self.line = line

    def update(self, x, y):
        self.x_vector[x] = x
        self.y_vector[x] = y
        self.line.set_xdata(self.x_vector)
        self.line.set_ydata(self.y_vector)
        self.fig.canvas.draw()
        self.fig.canvas.flush_events()

    def close(self):
        # Some extra code to allow showing the plot without blocking
        # the test indefinitely if window isn't closed.
        timer = self.fig.canvas.new_timer(interval=5000)
        timer.add_callback(self.plt.close)
        timer.start()
        self.plt.show()


def main():
    root = Path(__file__).parent

    vu = VUnit.from_argv()
    vu.add_vhdl_builtins()
    vu.add_python()
    vu.add_random()
    simulator_name = vu.get_simulator_name()

    if simulator_name in ["rivierapro", "activehdl"]:
        # TODO: Include VHPI application compilation in VUnit
        # NOTE: A clean build will delete the output after it was created so another no clean build has to be performed.
        compile_vhpi_application(root, vu)
    elif simulator_name == "modelsim":
        compile_fli_application(root, vu)
    # NVC and GHDL are handled automatically by add_python() through the VUnit
    # Python bridge; no separate compile step or simulator flag is needed.

    lib = vu.add_library("lib")
    lib.add_source_files(root / "*.vhd")

    vu.set_compile_option("rivierapro.vcom_flags", ["-dbg"])
    vu.set_sim_option("rivierapro.vsim_flags", ["-interceptcoutput"])
    # Crashes RPRO for some reason. TODO: Fix when the C code is properly
    # integrated into the project. Must be able to debug the C code.
    # vu.set_sim_option("rivierapro.vsim_flags" , ["-cdebug"])

    tb = lib.test_bench("tb_example")

    # These tests need optional Python packages that are not part of VUnit's
    # own test requirements; they are skipped unless explicitly selected.
    for test_name in (
        "Test GUI browsing for input stimuli file",  # PySimpleGUI
        "Test querying for randomization seed",  # PySimpleGUI
        "Test controlling progress of simulation",  # PySimpleGUI
        "Test constraint solving",  # python-constraint
        "Test using a Python module as the golden reference",  # crccheck
        "Test using Python in an behavioral model",  # crccheck
        "Test simple plot",  # matplotlib
        "Test advanced plot",  # matplotlib
    ):
        tb.test(test_name).set_attribute(".optional_deps", None)

    # These tests demonstrate error reporting and are expected to fail.
    for test_name in (
        "Test syntax error",
        "Test type error",
        "Test Python exception",
    ):
        tb.test(test_name).set_attribute(".expected_failure", None)

    vu.main()


if __name__ == "__main__":
    main()
