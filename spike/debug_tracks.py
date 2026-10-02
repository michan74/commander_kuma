import sys

import cv2
import numpy as np

from detect_measures import binarize
from staves import detect_staves, measure_ruler

img = cv2.imread(sys.argv[1])
binary = binarize(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY))
_, space = measure_ruler(binary)
for i, s in enumerate(detect_staves(binary, space, min_span=0.0)):
    mids = (s["top"] + s["bottom"]) / 2
    print(i, int(s["xs"][0]), int(s["xs"][-1]), "mid", int(mids[0]), int(mids[-1]), "n", len(mids))
