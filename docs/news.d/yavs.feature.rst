Added experimental support for the yavs VHDL simulator (VHDL-2008 and VHDL-2019). Select it with
``VUNIT_SIMULATOR=yavs``. It adds the ``yavs.*`` compile and simulation options and the ``yavs_version`` package marker.
yavs supports ``--batch-compile``, and ``--parallel-compile`` with concurrent compiles into a library, since it locks
its libraries.
