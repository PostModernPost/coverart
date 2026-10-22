"""Draws the record icon (same as the tray icon) into packaging/linernotes.ico for the exe and installer."""
import os
from PIL import Image, ImageDraw

size = 256
img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
d = ImageDraw.Draw(img)
c = size / 2
for r, col in ((0.48, (20, 20, 22, 255)), (0.40, (40, 40, 44, 255)), (0.32, (24, 24, 26, 255)),
               (0.24, (40, 40, 44, 255)), (0.17, (201, 160, 74, 255)), (0.03, (20, 20, 22, 255))):
    d.ellipse([c - r * size, c - r * size, c + r * size, c + r * size], fill=col)
out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "linernotes.ico")
img.save(out, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
print("wrote", out)
