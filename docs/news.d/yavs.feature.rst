Added experimental support for the yavs VHDL simulator (VHDL-2008 and VHDL-2019). Select it with
``VUNIT_SIMULATOR=yavs``. It adds the ``yavs.*`` compile and simulation options and the ``yavs_version`` package marker.
yavs supports ``--batch-compile``, and ``--parallel-compile`` with concurrent compiles into a library, since it locks
its libraries.
The ``--gui`` flag runs ``yavs run --gui`` with the arguments of the normal run, which opens the run in ``yavs-gui``.
The ``--debug`` flag runs the one selected test with ``yavs run --dap`` (port 4711, or ``YAVS_DAP_PORT``) and holds it until
a debugger such as VS Code attaches.
