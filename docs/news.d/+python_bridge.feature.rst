[GHDL/NVC] VHDL testbenches can now execute Python code and call Python functions,
for example NumPy reference models, with ``python_execute`` and ``python_call``.
``integer_array_t`` values are exchanged as NumPy arrays, keyword arguments are
created with ``kw``, and code can run in separate named sessions. Enable with
``add_vhdl_builtins(python=True)``. See :ref:`python_bridge`.
