[GHDL/NVC] VHDL testbenches can now execute Python code and call Python functions,
for example NumPy reference models, through the upstream ``python_pkg`` API:
``exec``, ``eval``/``eval_<type>``, and ``call``/``arg``/``kwarg``/``to_call_str``.
Enable with ``add_vhdl_builtins()`` followed by ``add_python()``. VUnit-specific
extensions (``std_ulogic``/``std_ulogic_vector``/``signed``/``unsigned`` arguments,
``integer_array_t`` as NumPy arrays, named sessions and ``exec_file``) are available
through ``python_ext_pkg``. Questa/ModelSim (FLI) and Riviera-PRO/Active-HDL (VHPI)
are also supported, through helpers in :mod:`vunit.python_pkg`. See :ref:`python_bridge`.
