# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2014-2026, Lars Asplund lars.anders.asplund@gmail.com

"""
Interface for the rsim simulator
"""

from pathlib import Path
from os import environ, makedirs, remove
import logging
import subprocess
import shlex
import re
import warnings
from functools import total_ordering
from sys import stdout  # To avoid output catched in non-verbose mode
from ..exceptions import CompileError
from ..ostools import Process
from . import SimulatorInterface, ListOfStringOption, StringOption
from . import check_executable
from ._viewermixin import ViewerMixin
from ..vhdl_standard import VHDL
from . import hooks

LOGGER = logging.getLogger(__name__)


class RSIMInterface(SimulatorInterface, ViewerMixin):
    """
    Interface for the rsim simulator
    """

    name = "rsim"
    executable = environ.get("RSIM", "rsim")
    supports_gui_flag = True
    supports_colors_in_gui = True

    compile_options = [
        ListOfStringOption("rsim.global_flags"),
        ListOfStringOption("rsim.a_flags"),
    ]

    sim_options = [
        ListOfStringOption("rsim.global_flags"),
        ListOfStringOption("rsim.elab_flags"),
        ListOfStringOption("rsim.sim_flags"),
        StringOption("rsim.viewer_script.gui"),
        StringOption("rsim.viewer.gui"),
    ]

    @classmethod
    def from_args(cls, args, output_path, **kwargs):
        """
        Create instance from args namespace
        """
        prefix = cls.find_prefix()
        check_executable("RSIM", prefix, cls.executable)
        cls.check_version(prefix)

        return cls(
            output_path=output_path,
            prefix=prefix,
            gui=args.gui,
            viewer_fmt=args.viewer_fmt,
            viewer_args=args.viewer_args,
            viewer=args.viewer,
        )

    @classmethod
    def find_prefix_from_path(cls):
        """
        Find first valid rsim toolchain prefix
        """
        return cls.find_toolchain([cls.executable])

    def __init__(  # pylint: disable=too-many-arguments
        self, output_path, prefix, *, gui=False, viewer_fmt=None, viewer_args="", viewer=None
    ):
        SimulatorInterface.__init__(self, output_path, gui)
        if viewer_fmt not in (None, "fst"):
            LOGGER.warning("rsim only writes fst, ignoring --viewer-fmt=%s", viewer_fmt)
        ViewerMixin.__init__(self, gui=gui, viewer=viewer, viewer_fmt="fst", viewer_args=viewer_args)

        self._prefix = prefix
        self._project = None
        self._vhdl_standard = None

    @classmethod
    def _get_version_output(cls, prefix):
        """
        Get the output of 'rsim --version'
        """
        return subprocess.check_output([str(Path(prefix) / cls.executable), "--version"]).decode()

    @classmethod
    def determine_version(cls, prefix):
        """
        Determine the rsim version
        """
        return RSIMVersion.from_output(cls._get_version_output(prefix))

    @classmethod
    def check_version(cls, prefix):
        """
        Raise an error unless 'rsim --version' works and reports a version implementing the VUnit CLI contract
        """
        try:
            version = cls.determine_version(prefix)
        except (OSError, subprocess.CalledProcessError) as exc:
            raise RuntimeError(f"Running '{Path(prefix) / cls.executable} --version' failed: {exc}") from exc

        if not version.is_valid:
            raise RuntimeError(f"Cannot parse the rsim version from {version.version_string!r}")

        if version < RSIM_MIN_VERSION:
            raise RuntimeError(f"rsim {version} is too old for VUnit, {RSIM_MIN_VERSION} or later is required")

        return version

    @staticmethod
    def has_valid_exit_code():
        """
        Return if the simulation should fail with nonzero exit codes
        """
        return True

    @staticmethod
    def supports_vhpi():
        """
        Returns True when the simulator supports VHPI
        """
        return True

    @classmethod
    def supports_vhdl_call_paths(cls):
        """
        Returns True when this simulator supports VHDL-2019 call paths
        """
        return True

    @classmethod
    def supports_vhdl_package_generics(cls):
        """
        Returns True when this simulator supports VHDL package generics
        """
        return True

    def setup_library_mapping(self, project):
        """
        Setup library mapping. A library is a directory that rsim creates on first analysis,
        and a missing directory is an empty library, so there is nothing to initialise.
        """
        self._project = project

        for library in project.get_libraries():
            makedirs(Path(library.directory).parent, exist_ok=True)

        vhdl_standards = set(
            source_file.get_vhdl_standard()
            for source_file in project.get_source_files_in_order()
            if source_file.is_vhdl
        )

        if not vhdl_standards:
            self._vhdl_standard = VHDL.STD_2008
        elif len(vhdl_standards) != 1:
            raise RuntimeError(f"rsim cannot handle mixed VHDL standards, found {vhdl_standards!r}")
        else:
            self._vhdl_standard = list(vhdl_standards)[0]

        if self._vhdl_standard not in (VHDL.STD_2008, VHDL.STD_2019):
            raise RuntimeError(f"rsim supports VHDL-2008 and VHDL-2019 only, found VHDL-{self._vhdl_standard}")

    def compile_source_file_command(self, source_file):
        """
        Returns the command to compile a single source_file
        """
        if source_file.is_vhdl:
            return self.compile_vhdl_file_command(source_file)

        LOGGER.error("Unknown file type: %s", source_file.file_type)
        raise CompileError

    def _get_command(self, subcommand, std, worklib, workpath):
        """
        Get basic rsim command with library mapping
        """
        cmd = [
            str(Path(self._prefix) / self.executable),
            subcommand,
            f"--std={std}",
            f"--work={worklib}:{workpath!s}",
        ]

        for library in self._project.get_libraries():
            cmd += [f"--map={library.name}:{library.directory}"]

        if self.use_color:
            cmd += ["--color=always"]

        return cmd

    def compile_vhdl_file_command(self, source_file):
        """
        Returns the command to compile a VHDL file
        """
        cmd = self._get_command(
            "analyze", source_file.get_vhdl_standard(), source_file.library.name, source_file.library.directory
        )
        cmd += source_file.compile_options.get("rsim.global_flags", [])
        cmd += source_file.compile_options.get("rsim.a_flags", [])
        cmd += [source_file.name]
        return cmd

    def _get_viewer(self, config):
        """
        Prefer Surfer, which the rsim GUI will embed, when no viewer is given.
        """
        if (
            self._viewer is None
            and config.sim_options.get(self.name + ".viewer.gui") is None
            and self._surfer_available
        ):
            return "surfer"
        return ViewerMixin._get_viewer(self, config)

    def simulate(self, output_path, test_suite_name, config, elaborate_only):
        """
        Simulate with entity as top level using generics
        """
        libdir = self._project.get_library(config.library_name).directory
        cmd = self._get_command("elab" if elaborate_only else "run", self._vhdl_standard, config.library_name, libdir)

        if not elaborate_only and config.sim_options.get("disable_ieee_warnings", False):
            cmd += ["--ieee-warnings=off"]

        cmd += config.sim_options.get("rsim.global_flags", [])
        cmd += config.sim_options.get("rsim.elab_flags", [])
        cmd += hooks.get_flags(self, "elab_flags")

        wave_file = None
        if not elaborate_only:
            cmd += config.sim_options.get("rsim.sim_flags", [])
            cmd += hooks.get_flags(self, "run_flags")
            # Last one wins in rsim, so this overrides any --exit-severity in rsim.sim_flags
            cmd += [f"--exit-severity={config.vhdl_assert_stop_level}"]

            if self._gui:
                script_path = Path(output_path) / self.name
                makedirs(script_path, exist_ok=True)
                wave_file = script_path / f"{config.entity_name}.fst"
                if wave_file.exists():
                    remove(wave_file)
                cmd += [f"--wave={wave_file}"]

        if config.vhdl_configuration_name is not None:
            cmd += [config.vhdl_configuration_name]
        else:
            cmd += [f"{config.entity_name}.{config.architecture_name}"]

        for name, value in config.generics.items():
            cmd += [f"-g{name}={value}"]

        print(" ".join([f"'{word}'" if " " in word else word for word in cmd]))

        status = True
        try:
            proc = Process(cmd, env=hooks.get_run_env(self))
            proc.consume_output()
        except Process.NonZeroExitCode:
            status = False

        if wave_file:
            cmd = [self._get_viewer(config)] + shlex.split(self._viewer_args) + [str(wave_file)]
            init_file = config.sim_options.get(self.name + ".viewer_script.gui", None)
            if init_file is not None:
                cmd += ["--script", str(Path(init_file).resolve())]

            stdout.write(f'{" ".join(cmd)}\n')
            subprocess.call(cmd)

        return status


@total_ordering
class RSIMVersion:
    """rsim version number."""

    _VERSION_RE = re.compile(r"([0-9]+)\.([0-9]+)(?:\.([0-9]+))?")

    def __init__(self, version_string):
        self.version_string = version_string
        self._version = None
        match = self._VERSION_RE.match(version_string)
        if not match:
            warnings.warn(
                f"Cannot parse rsim version: {version_string!r}; version is unknown",
                RuntimeWarning,
                stacklevel=2,
            )
            return

        self._version = (int(match.group(1)), int(match.group(2)), int(match.group(3) or 0))

    @classmethod
    def from_output(cls, output):
        """Extract the rsim version from 'rsim --version' output, or return an unknown version."""
        match = cls._VERSION_RE.search(output)
        return cls(match.group(0) if match else output)

    @property
    def is_valid(self):
        return self._version is not None

    def __eq__(self, other):
        if isinstance(other, RSIMVersion):
            if not self.is_valid or not other.is_valid:
                raise ValueError("Cannot compare unknown rsim versions")
            return self._version == other._version  # pylint: disable=protected-access
        return NotImplemented

    def __lt__(self, other):
        if isinstance(other, RSIMVersion):
            if not self.is_valid or not other.is_valid:
                raise ValueError("Cannot order unknown rsim versions")
            return self._version < other._version  # pylint: disable=protected-access
        return NotImplemented

    def __str__(self):
        return ".".join(str(component) for component in self._version) if self.is_valid else "unknown"

    def __repr__(self):
        return f"RSIMVersion({self.version_string!r})"


# The first rsim version implementing the VUnit CLI contract. Raise it when VUnit starts relying on a newer contract.
RSIM_MIN_VERSION = RSIMVersion("0.0.0")
