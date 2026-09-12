[GHDL/NVC] VHDL testbenches can now execute Python code and call Python functions,
for example NumPy reference models, through the ``python_pkg`` API: ``exec``,
``eval``/``eval_<type>``, and ``call``/``arg``/``kwarg``/``to_call_str``, the
latter also taking ``std_ulogic`` and arbitrarily wide ``unsigned``/``signed``
argument values and keyword argument groups combined with ``&``. Enable
with ``add_vhdl_builtins()`` followed by ``add_python()``. On NVC and GHDL the
package also exchanges ``integer_array_t`` values as NumPy arrays, returns
boolean, std_ulogic and width-checked vector results, executes files with
``exec_file`` and isolates models in named sessions. Questa/ModelSim (FLI) and
Riviera-PRO/Active-HDL (VHPI) are supported through the helpers in
``vunit.python_pkg``. See :ref:`python_bridge`.
