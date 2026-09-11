.. _vhdl_python:

Calling Python from VHDL
========================

VUnit can embed a Python interpreter in the simulator so that VHDL testbenches
can execute Python code and call Python functions, for example reference
models written with NumPy. The feature is enabled with ``python=True``:

.. code-block:: python

    from vunit import VUnit

    vu = VUnit.from_argv()
    vu.add_vhdl_builtins(python=True)

    lib = vu.add_library("lib")
    lib.add_source_files("*.vhd")

    vu.main()

The two operations ``python_execute`` and ``python_call`` then become
available through ``vunit_context``:

.. code-block:: vhdl

    library vunit_lib;
    context vunit_lib.vunit_context;

    ...

    python_execute(file_name => "models/reference_model.py");
    expected := python_call("model", input);

Requirements
------------

* NVC or GHDL. Other simulators are not supported and
  ``add_vhdl_builtins(python=True)`` raises an error for them. Without
  ``python=True`` nothing changes for any simulator.
* VHDL-2008 or later.
* CPython 3.10 or later with the standard (GIL) build. Free-threaded builds
  are rejected with an error.
* NumPy, but only if ``integer_array_t`` values are exchanged.
* Linux: a C compiler (``cc``, ``gcc`` or ``clang``, or ``CC``) and the Python
  development headers (for example the ``python3-dev`` package) since the
  bridge library is compiled on first use, see :ref:`vhdl_python_native`.
  Python must provide a shared ``libpython`` (``--enable-shared``), which is
  the case for distribution Pythons, ``actions/setup-python``, ``uv`` and
  ``pyenv`` builds with default settings.
* Windows: a 64-bit CPython from python.org (or compatible, such as
  ``actions/setup-python`` and ``uv``). No compiler is needed. MSYS2/MinGW
  Pythons are not supported.

The simulator runs Python in the same environment as VUnit itself, including
an active virtual environment and its installed packages.

A missing prerequisite, for example a missing C compiler or missing Python
headers, is reported by ``add_vhdl_builtins(python=True)`` as an ``ERROR``
explaining what to install, after which VUnit exits with code 1.

python_execute
--------------

``python_execute`` executes Python source code or a Python file. All code
runs in a persistent namespace that lives until the simulation ends. Names
defined by one ``python_execute`` are therefore visible to later calls,
whether inline or from files. Separate namespaces can be created with
:ref:`sessions <vhdl_python_sessions>`.

.. code-block:: vhdl

    python_execute("GAIN = 4");

    python_execute(
      "import numpy as np" +
      "" +
      "def scale(x):" +
      "    return x * GAIN"
    );

    python_execute(file_name => "models/reference_model.py");

Source code is passed as a string. Lines are joined with the ``+`` operator
that ``python_pkg`` defines for strings: ``"a" + "b"`` is ``"a" & LF & "b"``.
Indentation and blank lines (``""``) are preserved exactly. VHDL cannot
express an aggregate of strings of different lengths, so
``python_execute(("line 1", "line 2"))`` is not possible.

``+`` only applies where a ``string`` is expected; ``numeric_std`` arithmetic
such as ``unsigned'("0011") + "0001"`` is unaffected.

A Python file is executed with the equivalent of

.. code-block:: python

    with open(path, encoding="utf-8") as file:
        source = file.read()
    exec(compile(source, str(path), "exec"), namespace, namespace)

which means that

* tracebacks show the real file name and line numbers,
* ``__file__`` is set to the absolute path of the file while it executes (and
  restored afterwards), so ``Path(__file__).parent`` works,
* the directory of the file is added to ``sys.path`` while the file executes,
  so it can import sibling modules (``from filters import fir``) without
  setting ``PYTHONPATH``. Imports are therefore expected at the top level of
  the file,
* executing the same file twice executes it twice.

Relative file names are relative to the directory of the VUnit run script
(the script started by ``python``), independently of the simulator and of the
current working directory. That directory is also the first entry of
``sys.path``, just like when the run script is started. Absolute paths work
too, for example ``tb_path(runner_cfg) & "model.py"`` for a file next to the
testbench.

python_call
-----------

``python_call`` calls a function in the namespace with zero or one argument,
or with any number of ``integer_array_t`` arguments, and converts the returned
value to the VHDL type given by the context. The function name may be a dotted
name such as ``"np.sum"`` or ``"model.run"``, and Python builtins such as
``"len"`` can be called directly.

.. code-block:: vhdl

    variable answer : integer;
    variable ratio : real;
    variable result : integer_array_t;
    variable value : signed(11 downto 0);

    answer := python_call("answer");
    ratio := python_call("ratio", 3);
    result := python_call("model", input);
    result := python_call("add", a & b);
    result := python_call("merge", args => (a, b, c, d, e));
    python_call("to_fixed_point", 0.25, value);

Arguments
~~~~~~~~~

The argument can be one of the types below. A string *literal* must be
qualified, ``python_call("greet", string'("world"))``, since a literal would
otherwise also match ``std_ulogic_vector``, ``signed`` and ``unsigned``.

Several ``integer_array_t`` arrays are passed as separate positional
arguments, either concatenated with ``&`` or as an aggregate associated with
the formal ``args``. A positional aggregate, ``python_call("f", (a, b))``, is
ambiguous in VHDL and does not compile.

Type mapping
~~~~~~~~~~~~

====================== ============================ ==============================================
VHDL type              Python type                  Notes
====================== ============================ ==============================================
``integer``            ``int``                      Results outside the VHDL integer range fail.
``real``               ``float``                    An ``int`` result is accepted. NaN and infinity fail.
``boolean``            ``bool``                     Only ``bool`` (or ``numpy.bool_``) results are accepted.
``string``             ``str``                      UTF-8, see below.
``std_ulogic``         ``str`` of length 1          One of ``U X 0 1 Z W L H -``.
``std_ulogic_vector``  ``str``                      One character per element from left to right.
``signed``             ``int``                      Argument must not contain metavalues other than ``L``/``H``.
``unsigned``           ``int``                      Idem.
``integer_array_t``    ``numpy.ndarray``            See below.
====================== ============================ ==============================================

Results are strict. For example, a ``bool`` is not accepted as ``integer``
and a ``float`` is not accepted as ``integer``. A result that does not fit
the VHDL type is an error. Values are never silently truncated or wrapped.

``signed``, ``unsigned`` and ``std_ulogic_vector`` results can also be
received with a procedure form of ``python_call`` that takes the result as its
last parameter. Its width is given by the actual, and a Python value that
does not fit that width is an error. ``signed`` and ``unsigned`` results are
only available in the procedure form since a function cannot know the width
the result is assigned to.

VHDL strings are exchanged as UTF-8 bytes, with undecodable bytes preserved
(``surrogateescape``), so text round-trips unchanged. Note that a non-ASCII
character is then more than one VHDL ``character``.

integer_array_t and NumPy
~~~~~~~~~~~~~~~~~~~~~~~~~

An ``integer_array_t`` argument becomes a NumPy array of dtype ``int32``. The
shape follows VUnit's indexing so that elements correspond directly:

============= ================================ ===========================
Array         VHDL                             Python
============= ================================ ===========================
1D            ``get(a, i)``                    ``a[i]``, shape ``(length,)``
2D            ``get(a, x, y)``                 ``a[y, x]``, shape ``(height, width)``
3D            ``get(a, x, y, z)``              ``a[y, x, z]``, shape ``(height, width, depth)``
============= ================================ ===========================

``integer_array_t`` does not record its number of dimensions. An argument is
treated as 3D if its depth is larger than one, else as 2D if its height is
larger than one, else as 1D.

A returned array (or nested list of integers) becomes a new
``integer_array_t`` with the corresponding width, height and depth. Its
``bit_width`` and ``is_signed`` are:

* those of the argument, if the function returns one of its
  ``integer_array_t`` arguments (possibly modified in place),
* else given by the dtype: ``bool`` is 1 bit unsigned, ``int8``/``uint8`` 8 bit,
  ``int16``/``uint16`` 16 bit and all other integer dtypes 32 bit signed.

All values must fit that word size, otherwise the call fails. Floating point
arrays are rejected; convert them explicitly with ``astype``. As usual, the
returned ``integer_array_t`` is owned by the caller and can be freed with
``deallocate``.

.. _vhdl_python_sessions:

Sessions
--------

Every ``python_execute`` and ``python_call`` takes an optional last
parameter, ``session``, which selects the namespace the operation runs in.
A session is identified by a name of type ``python_session_t``, and is
created the first time it is used. When no session is given, the
``default_session`` constant is used, whose namespace is ``__main__``.

.. code-block:: vhdl

    constant golden : python_session_t := "golden";
    constant fixed_point : python_session_t := "fixed_point";

    ...

    python_execute(file_name => "models/golden.py", session => golden);
    python_execute(file_name => "models/fixed_point.py", session => fixed_point);

    expected := python_call("model", input, session => golden);
    got := python_call("model", input, session => fixed_point);

Both files can define ``model`` without interfering. ``session`` can also be
given positionally, for example ``python_call("model", input, golden)``.

``python_session_t`` is a separate string type, rather than ``string``, so
that the session parameter cannot be mistaken for a string argument. A
``string`` value can be converted with ``python_session_t(name)``.

Caveats
~~~~~~~

Sessions are separate namespaces in *one* Python interpreter, not separate
interpreters (which would not work with NumPy and many other extension
modules). Consequently, only the names defined by the executed code, such as
functions, classes and variables, are separate. Everything else is shared:

* Imported modules are loaded once and shared by all sessions. A module
  imported in one session is the same module object in another session, so
  changes to module state, such as ``np.random.seed(...)`` or attributes set
  on a module, are visible in all sessions. The same applies to sibling
  modules imported by a Python file executed in several sessions.
* ``sys.path``, ``sys.modules``, environment variables, the current directory
  and open files are process wide.
* Only the default session runs in ``__main__``. Classes defined in other
  sessions report ``__main__`` as their module but cannot be found there,
  which matters for example when pickling their instances.
* All sessions end with the simulation. Test cases run in the same simulation
  (``run_all_in_same_sim``) share the sessions.

Errors
------

Python exceptions, type and range errors, missing files and undefined
functions are reported as failures on the ``python_logger`` logger (named
``vunit_lib:python``), including the Python traceback. The test fails and
stops like for any other failure. The logger can be mocked to test error
handling. When mocked, ``python_call`` returns a default value after the
failure.

.. code-block:: text

    FAILURE - vunit_lib:python - python_call("model") failed:
    Traceback (most recent call last):
      File "/path/to/models/reference_model.py", line 12, in model
        return image / 0
               ~~~~~~^~~
    ZeroDivisionError: division by zero

Output of ``print`` is written to the simulator output and flushed after
every operation.

.. _vhdl_python_native:

How it works
------------

The interpreter is embedded in the simulator process by a small C library
(``vunit/python_bridge/native/vunit_python_bridge.c``) called through VHPIDIRECT.
The interpreter is started on first use, is never restarted within a
simulation, and uses no signal handlers of its own.

Linux
  The bridge is compiled from source against the Python running VUnit the
  first time ``add_vhdl_builtins(python=True)`` is called, and cached in
  ``<output path>/python_bridge``. It is rebuilt automatically when the source,
  the Python version or the Python installation changes. VUnit ships no
  prebuilt Linux library.

Windows
  VUnit ships DLLs built with MSVC for each supported Python minor version
  (``vunit/python_bridge/bin``). The matching DLL is copied to the output path.
  Nothing is compiled. A development checkout of VUnit does not contain the
  DLLs; they can be built with ``tools/build_python_bridge.py`` from an MSVC
  developer prompt.

The bridge uses the full (version specific) CPython ABI rather than the
Stable ABI since embedding the interpreter in the environment VUnit runs in
requires the ``PyConfig`` initialization API, which is not part of the
limited API.

VUnit makes the simulator find the library automatically. NVC is given a
``--load`` option. GHDL gets the library directory in its dynamic library
search path, plus a linker search path for the ahead-of-time compiled llvm
and gcc backends. No simulator options need to be set by the user.

Tested configurations
~~~~~~~~~~~~~~~~~~~~~

========================== ========================================================
Simulator                  Linux
========================== ========================================================
NVC                        1.22 (and 1.23-devel)
GHDL mcode                 6.0.0, 7.0.0-dev
GHDL llvm-jit              6.0.0, 7.0.0-dev
GHDL llvm                  6.0.0, 7.0.0-dev
GHDL gcc                   6.0.0, 7.0.0-dev
========================== ========================================================

On Windows, CI tests NVC 1.22 and GHDL mcode (nightly) with Python 3.10, 3.12
and 3.14.

Limitations
-----------

* Only zero or one argument of the scalar types, or ``integer_array_t``
  arguments, can be passed. Mixed argument lists are not supported. Pass
  additional values through the namespace instead, for example with
  ``python_execute("GAIN = " & to_string(gain))``.
* ``real_vector``, records and other composite types are not converted.
* One interpreter per simulation. Sessions provide separate namespaces but
  share imported modules and all other interpreter state, see
  :ref:`vhdl_python_sessions`. Namespaces are not reset between test cases
  that run in the same simulation (``run_all_in_same_sim``).
