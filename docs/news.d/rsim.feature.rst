Added experimental support for the rsim VHDL simulator (VHDL-2008 and VHDL-2019). Select it with
``VUNIT_SIMULATOR=rsim``. It adds the ``rsim.*`` compile and simulation options and the ``rsim_version`` package marker.
rsim supports ``--batch-compile``, and ``--parallel-compile`` with concurrent compiles into a library, since it locks
its libraries.
