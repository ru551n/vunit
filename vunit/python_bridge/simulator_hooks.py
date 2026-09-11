# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2014-2026, Lars Asplund lars.anders.asplund@gmail.com


"""
Hooks used by the NVC and GHDL interfaces to make the simulator find the bridge library.
They have no effect unless Python support is enabled for the project.
"""

import os
import sys
from typing import Dict, List

from .bridge import GHDL_LINKING_BACKENDS, get_bridge


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
