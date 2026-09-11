# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2014-2026, Lars Asplund lars.anders.asplund@gmail.com

import numpy as np
from filters import fir  # Sibling modules can be imported

TAPS = np.array([1, 2, 1])


def model(image):
    """Filter each row of an image. image[y, x] is get(image, x, y) in VHDL."""
    return np.array([fir(row, TAPS) for row in image], dtype=np.int32)
