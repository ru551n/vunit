[GHDL/NVC] VHDL testbenches can now execute Python code and call Python functions,
for example NumPy reference models, with ``python_execute`` and ``python_call``.
``integer_array_t`` values are exchanged as NumPy arrays. Enable with
``add_vhdl_builtins(python=True)``. See :ref:`vhdl_python`.
