import sys
import cv2

img = cv2.imread(sys.argv[1])
x, y, w, h = map(int, sys.argv[3:7])
cv2.imwrite(sys.argv[2], img[y:y + h, x:x + w])
