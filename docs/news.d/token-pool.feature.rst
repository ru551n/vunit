Simulator interfaces can set ``supports_jobserver = True`` to share the CPU budget of a parallel run (``-p N``)
with multi-threaded simulations through the GNU make jobserver protocol. Extra threads go first to the most
recently started tests, and the cores of idle workers speed up the tests still running. See
:ref:`package_jobserver`.
