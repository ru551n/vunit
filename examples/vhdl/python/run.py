#!/usr/bin/env python3

# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2014-2026, Lars Asplund lars.anders.asplund@gmail.com

"""
Python
------

Demonstrates how a VHDL testbench can call Python with ``python_execute`` and
``python_call``, here to compare a VHDL filter implementation with a NumPy
reference model. ``integer_array_t`` values are passed to and from Python as
NumPy arrays. Requires NVC or GHDL and NumPy.
"""

from pathlib import Path
from vunit import VUnit

VU = VUnit.from_argv()
VU.add_vhdl_builtins(python=True)

VU.add_library("lib").add_source_files(Path(__file__).parent / "*.vhd")

VU.main()
