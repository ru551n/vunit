# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2014-2026, Lars Asplund lars.anders.asplund@gmail.com

import numpy as np


def fir(row, taps):
    """Causal FIR filter of a 1D array, zero initial state."""
    return np.convolve(row, taps)[: len(row)]
