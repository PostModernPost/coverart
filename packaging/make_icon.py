"""Draws the Liner Notes logo into packaging/linernotes.ico for the exe and installer."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "app"))
import logo  # noqa: E402

sizes = [16, 20, 24, 32, 40, 48, 64, 128, 256]
imgs = [logo.draw_mark(s, tile=s >= 48) for s in sizes]      # small sizes: the bare mark reads better
out = os.path.join(HERE, "linernotes.ico")
imgs[-1].save(out, sizes=[(s, s) for s in sizes], append_images=imgs[:-1])
print("wrote", out)
