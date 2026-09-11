# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2014-2026, Lars Asplund lars.anders.asplund@gmail.com

"""
Runtime of the VUnit Python bridge.

This module is executed *inside the simulator process* by the embedded
interpreter of the native bridge (vunit_python_bridge.c). It is loaded by file
path, not imported from the vunit package, and must therefore not import
anything from vunit. It is a private implementation detail of the VHDL
python_execute/python_call operations.
"""

import builtins
import linecache
import math
import numbers
import os
import sys
import traceback
from pathlib import Path
import __main__

# Result kinds, must match vunit_python_bridge.c and python_pkg
KIND_INTEGER = 0
KIND_REAL = 1
KIND_BOOLEAN = 2
KIND_STRING = 3
KIND_STD_ULOGIC = 4
KIND_STD_ULOGIC_VECTOR = 5
KIND_SIGNED = 6
KIND_UNSIGNED = 7
KIND_INTEGER_ARRAY = 8

VHDL_TYPE_NAMES = {
    KIND_INTEGER: "integer",
    KIND_REAL: "real",
    KIND_BOOLEAN: "boolean",
    KIND_STRING: "string",
    KIND_STD_ULOGIC: "std_ulogic",
    KIND_STD_ULOGIC_VECTOR: "std_ulogic_vector",
    KIND_SIGNED: "signed",
    KIND_UNSIGNED: "unsigned",
    KIND_INTEGER_ARRAY: "integer_array_t",
}

STD_ULOGIC_VALUES = frozenset("UX01ZWLH-")
INTEGER_LOW = -(2**31)
INTEGER_HIGH = 2**31 - 1
TEXT_ENCODING = "utf-8"
TEXT_ERRORS = "surrogateescape"

_NO_VALUE = object()


def _type_name(value):
    return type(value).__qualname__


def _is_bool(value):
    """True for Python and NumPy booleans."""
    if isinstance(value, bool):
        return True
    numpy = sys.modules.get("numpy")
    return numpy is not None and isinstance(value, numpy.bool_)


def _encode(text):
    return text.encode(TEXT_ENCODING, TEXT_ERRORS)


class Runtime:
    """
    State of the embedded Python session: one persistent namespace shared by
    all python_execute and python_call operations of a simulation.
    """

    def __init__(self, base_dir, prefix):
        self._base_dir = Path(base_dir)
        self._namespace = __main__.__dict__
        self._inline_count = 0
        self._result = _NO_VALUE
        self._function_name = ""
        # Metadata of the integer_array_t arguments of the current call, keyed
        # by id() of the NumPy array created for them.
        self._array_meta = {}

        self._check_environment(prefix)

        # Mimic "python run.py": the run script directory is sys.path[0].
        if str(self._base_dir) not in sys.path:
            sys.path.insert(0, str(self._base_dir))

    @staticmethod
    def _check_environment(prefix):
        """
        The embedded interpreter must use the Python environment that launched VUnit.
        """

        def normalize(path):
            return os.path.normcase(os.path.realpath(path))

        if normalize(sys.prefix) != normalize(prefix):
            raise RuntimeError(
                f"The embedded Python interpreter selected the environment {sys.prefix!r} "
                f"but VUnit was started from {prefix!r}"
            )

    @staticmethod
    def _flush():
        """
        Flush Python's output so that it is ordered with the simulator's output.
        """
        for stream in (sys.stdout, sys.stderr):
            try:
                if stream is not None:
                    stream.flush()
            except Exception:  # pylint: disable=broad-except
                pass

    def format_exception(self, exc):
        """
        Format an exception with its traceback, hiding frames of this module.
        """
        this_file = os.path.normcase(os.path.abspath(__file__))
        traceback_ = exc.__traceback__
        while traceback_ is not None and (
            os.path.normcase(os.path.abspath(traceback_.tb_frame.f_code.co_filename)) == this_file
        ):
            traceback_ = traceback_.tb_next
        self._flush()
        return "".join(traceback.format_exception(type(exc), exc, traceback_)).rstrip("\n")

    # ------------------------------------------------------------------
    # python_execute
    # ------------------------------------------------------------------

    def execute(self, text, is_file):
        """
        Execute inline source code or a Python file in the persistent namespace.
        """
        try:
            if is_file:
                self._execute_file(text)
            else:
                self._execute_source(text)
        finally:
            self._flush()

    def _execute_source(self, source):
        """
        Execute inline source code, registered in linecache for readable tracebacks.
        """
        self._inline_count += 1
        file_name = f"<python_execute #{self._inline_count}>"
        # Make tracebacks show the source lines of inline code
        linecache.cache[file_name] = (len(source), None, source.splitlines(True), file_name)
        code = compile(source, file_name, "exec", dont_inherit=True)
        exec(code, self._namespace, self._namespace)  # pylint: disable=exec-used

    def _resolve_file(self, file_name):
        """
        Absolute path of a Python file, relative names are relative to the run script directory.
        """
        if file_name == "":
            raise ValueError("python_execute: empty file name")
        path = Path(file_name)
        if not path.is_absolute():
            path = self._base_dir / path
        return Path(os.path.normpath(path.absolute()))

    def _execute_file(self, file_name):
        """
        Execute a Python file with __file__ set and its directory on sys.path.
        """
        path = self._resolve_file(file_name)
        with open(path, encoding="utf-8") as fptr:
            source = fptr.read()
        code = compile(source, str(path), "exec", dont_inherit=True)

        namespace = self._namespace
        previous_file = namespace.get("__file__", _NO_VALUE)
        directory = str(path.parent)
        namespace["__file__"] = str(path)
        # Let the file import its sibling modules
        sys.path.insert(0, directory)
        try:
            exec(code, namespace, namespace)  # pylint: disable=exec-used
        finally:
            try:
                sys.path.remove(directory)
            except ValueError:
                pass
            if previous_file is _NO_VALUE:
                namespace.pop("__file__", None)
            else:
                namespace["__file__"] = previous_file

    # ------------------------------------------------------------------
    # python_call arguments
    # ------------------------------------------------------------------

    @staticmethod
    def bits_to_int(bits, is_signed):
        """
        Convert the image of a signed/unsigned value (MSB first) to an int.

        'L' and 'H' are treated as '0' and '1' like numeric_std's TO_01;
        other metavalues cannot be converted.
        """
        normalized = bits.replace("L", "0").replace("H", "1")
        if normalized == "":
            raise ValueError(f"Cannot convert a null {'signed' if is_signed else 'unsigned'} value to a Python int")
        if not set(normalized) <= {"0", "1"}:
            raise ValueError(
                f"Cannot convert {'signed' if is_signed else 'unsigned'} value \"{bits}\" "
                "containing metavalues to a Python int"
            )
        value = int(normalized, 2)
        if is_signed and normalized[0] == "1":
            value -= 1 << len(normalized)
        return value

    @staticmethod
    def _numpy():
        """
        Import NumPy, which is only needed when integer_array_t values are exchanged.
        """
        try:
            import numpy  # type: ignore[import-not-found]  # pylint: disable=import-outside-toplevel
        except ImportError as exc:
            raise ImportError(
                "integer_array_t values are exchanged as NumPy arrays but NumPy could not be imported "
                f"in the Python environment used by VUnit ({sys.executable}): {exc}"
            ) from exc
        return numpy

    @staticmethod
    def _shape(length, width, height, depth):
        """
        NumPy shape of an integer_array_t. get(arr, x, y) is a[y, x] and
        get(arr, x, y, z) is a[y, x, z], matching VUnit's storage order.
        """
        if depth > 1:
            return (height, width, depth)
        if height > 1:
            return (height, width)
        return (length,)

    def make_array(
        self, storage, length, width, height, depth, bit_width, is_signed
    ):  # pylint: disable=too-many-arguments,too-many-positional-arguments
        """
        Create the NumPy array for an integer_array_t argument. The bridge
        fills the storage (native int32) after this call returns.
        """
        numpy = self._numpy()
        array = numpy.frombuffer(storage, dtype=numpy.int32).reshape(self._shape(length, width, height, depth))
        self._array_meta[id(array)] = (array, bit_width, bool(is_signed))
        return array

    # ------------------------------------------------------------------
    # python_call
    # ------------------------------------------------------------------

    def _resolve_function(self, name):
        """
        Look up a (dotted) function name in the namespace or among the builtins.
        """
        parts = name.split(".")
        if not all(part.isidentifier() for part in parts):
            raise ValueError(f"python_call: {name!r} is not a valid Python function name")

        if parts[0] in self._namespace:
            obj = self._namespace[parts[0]]
        elif hasattr(builtins, parts[0]):
            obj = getattr(builtins, parts[0])
        else:
            raise NameError(f"Python function {name!r} is not defined. Define it with python_execute first.")

        for part in parts[1:]:
            obj = getattr(obj, part)

        if not callable(obj):
            raise TypeError(f"python_call: {name!r} is not callable (it is a {_type_name(obj)})")
        return obj

    def call(self, name, args):
        """
        Call a function in the namespace. The result is kept until converted.
        """
        self._result = _NO_VALUE
        self._function_name = name
        try:
            function = self._resolve_function(name)
            self._result = function(*args)
        except BaseException:
            self._array_meta.clear()
            raise
        finally:
            self._flush()

    # ------------------------------------------------------------------
    # python_call results
    # ------------------------------------------------------------------

    def _type_error(self, kind, value, expected):
        return TypeError(
            f"python_call({self._function_name!r}): cannot return Python {_type_name(value)} "
            f"({value!r:.200}) as VHDL {VHDL_TYPE_NAMES[kind]}; expected {expected}"
        )

    def convert_result(self, kind, width):
        """
        Convert the result of the last call to the VHDL type given by kind.

        :param width: Width of a std_ulogic_vector/signed/unsigned result, -1 if not given by VHDL.
        :returns: (integer, real, data bytes, metadata tuple)
        """
        value = self._result
        self._result = _NO_VALUE
        array_meta = self._array_meta
        self._array_meta = {}
        if value is _NO_VALUE:
            raise RuntimeError("Internal error: no Python call result available")

        converters = {
            KIND_INTEGER: self._integer_result,
            KIND_REAL: self._real_result,
            KIND_BOOLEAN: self._boolean_result,
            KIND_STRING: self._string_result,
            KIND_STD_ULOGIC: self._std_ulogic_result,
            KIND_STD_ULOGIC_VECTOR: self._std_ulogic_vector_result,
            KIND_SIGNED: self._bits_result,
            KIND_UNSIGNED: self._bits_result,
        }
        if kind == KIND_INTEGER_ARRAY:
            return self._array_result(value, array_meta)
        if kind not in converters:
            raise RuntimeError(f"Internal error: unknown result kind {kind}")
        return converters[kind](kind, value, width)

    def _integer_result(self, kind, value, _width):
        """
        Convert an int result.
        """
        if _is_bool(value) or not isinstance(value, numbers.Integral):
            raise self._type_error(kind, value, "int")
        value = int(value)
        if not INTEGER_LOW <= value <= INTEGER_HIGH:
            raise OverflowError(
                f"python_call({self._function_name!r}): {value} is outside the range of VHDL integer "
                f"({INTEGER_LOW} to {INTEGER_HIGH})"
            )
        return (value, 0.0, b"", ())

    def _real_result(self, kind, value, _width):
        """
        Convert a float (or int) result.
        """
        if _is_bool(value) or not isinstance(value, numbers.Real):
            raise self._type_error(kind, value, "float (or int)")
        result = float(value)
        if not math.isfinite(result):
            raise ValueError(f"python_call({self._function_name!r}): {result} cannot be represented as VHDL real")
        return (0, result, b"", ())

    def _boolean_result(self, kind, value, _width):
        """
        Convert a bool result.
        """
        if not _is_bool(value):
            raise self._type_error(kind, value, "bool")
        return (int(bool(value)), 0.0, b"", ())

    def _string_result(self, kind, value, _width):
        """
        Convert a str result to UTF-8.
        """
        if not isinstance(value, str):
            raise self._type_error(kind, value, "str")
        data = _encode(value)
        return (0, 0.0, data, (len(data),))

    def _std_ulogic_result(self, kind, value, _width):
        """
        Convert a one character str result.
        """
        if not isinstance(value, str) or len(value) != 1 or value not in STD_ULOGIC_VALUES:
            raise self._type_error(kind, value, "a one character str, one of 'UX01ZWLH-'")
        return (0, 0.0, value.encode("ascii"), (1,))

    def _std_ulogic_vector_result(self, kind, value, width):
        """
        Convert a str result, one character per element.
        """
        if not isinstance(value, str) or not STD_ULOGIC_VALUES.issuperset(value):
            raise self._type_error(kind, value, "a str of the characters 'UX01ZWLH-'")
        if width >= 0 and len(value) != width:
            raise ValueError(
                f"python_call({self._function_name!r}): returned {len(value)} std_ulogic values "
                f"but the VHDL result has length {width}"
            )
        return (0, 0.0, value.encode("ascii"), (len(value),))

    def _bits_result(self, kind, value, width):
        """
        Convert an int result to the bits of a signed/unsigned.
        """
        data = self._int_to_bits(kind, value, width)
        return (0, 0.0, data, (len(data),))

    def _int_to_bits(self, kind, value, width):
        """
        Two's complement/binary image of an int, checked to fit width.
        """
        if _is_bool(value) or not isinstance(value, numbers.Integral):
            raise self._type_error(kind, value, "int")
        value = int(value)
        is_signed = kind == KIND_SIGNED
        if is_signed:
            low, high = (-(1 << (width - 1)), (1 << (width - 1)) - 1) if width > 0 else (0, -1)
        else:
            low, high = 0, (1 << width) - 1
        if not low <= value <= high:
            raise OverflowError(
                f"python_call({self._function_name!r}): {value} does not fit in a "
                f"{width} bit {VHDL_TYPE_NAMES[kind]} ({low} to {high})"
            )
        if width == 0:
            return b""
        return format(value & ((1 << width) - 1), f"0{width}b").encode("ascii")

    _DTYPE_WORD_SIZE = {
        # dtype.str without byte order: (bit_width, is_signed)
        "b1": (1, False),
        "i1": (8, True),
        "u1": (8, False),
        "i2": (16, True),
        "u2": (16, False),
        "i4": (32, True),
    }

    def _array_result(self, value, array_meta):
        """
        Convert a NumPy array (or nested int sequence) to integer_array_t data.
        """
        numpy = self._numpy()
        original = value
        value = self._integer_ndarray(numpy, value)
        bit_width, is_signed = self._word_size(original, value, array_meta)

        low, high = (-(1 << (bit_width - 1)), (1 << (bit_width - 1)) - 1) if is_signed else (0, (1 << bit_width) - 1)
        if value.size > 0:
            minimum, maximum = int(value.min()), int(value.max())
            if minimum < low or maximum > high:
                raise OverflowError(
                    f"python_call({self._function_name!r}): array values ({minimum} to {maximum}) do not fit in "
                    f"the integer_array_t word size ({bit_width} bit {'signed' if is_signed else 'unsigned'}, "
                    f"{low} to {high})"
                )

        # get(arr, x, y, z) is value[y, x, z]
        height, width, depth = (value.shape + (1, 1))[:3] if value.ndim > 1 else (1, value.shape[0], 1)

        data = numpy.ascontiguousarray(value, dtype=numpy.int32).tobytes()
        return (0, 0.0, data, (value.size, width, height, depth, bit_width, int(is_signed)))

    def _integer_ndarray(self, numpy, value):
        """
        The result as an integer NumPy array with 1 to 3 dimensions.
        """
        if not isinstance(value, numpy.ndarray):
            if isinstance(value, (str, bytes)) or not isinstance(value, (list, tuple)):
                raise self._type_error(KIND_INTEGER_ARRAY, value, "a NumPy array of integers")
            value = numpy.asarray(value)

        if value.dtype.kind not in "biu":
            raise TypeError(
                f"python_call({self._function_name!r}): cannot return a NumPy array of dtype {value.dtype} "
                "as VHDL integer_array_t; expected an integer or boolean dtype"
            )
        if value.ndim not in (1, 2, 3):
            raise ValueError(
                f"python_call({self._function_name!r}): cannot return a {value.ndim}-dimensional NumPy array "
                "as VHDL integer_array_t; expected 1, 2 or 3 dimensions"
            )
        return value

    def _word_size(self, original, value, array_meta):
        """
        bit_width and is_signed of a returned array.
        """
        meta = array_meta.get(id(original))
        if meta is not None and meta[0] is original and meta[1] >= 1:
            # The function returned (possibly modified in place) one of its
            # integer_array_t arguments: keep its bit width and signedness.
            return meta[1], meta[2]
        return self._DTYPE_WORD_SIZE.get(value.dtype.str[1:], (32, True))
