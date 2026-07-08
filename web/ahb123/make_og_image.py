#!/usr/bin/env python3
"""Generate assets/s/og-homepage.jpg (1200x630) from a hero portfolio image."""
import os
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
SRC_IMG = os.path.join(HERE, "assets", "s", "01-modern-kitchen-fishtown.jpg")
OUT = os.path.join(HERE, "assets", "s", "og-homepage.jpg")
TARGET = (1200, 630)

def make():
    im = Image.open(SRC_IMG).convert("RGB")
    tw, th = TARGET
    scale = max(tw / im.width, th / im.height)
    im = im.resize((round(im.width * scale), round(im.height * scale)))
    left = (im.width - tw) // 2
    top = (im.height - th) // 2
    im.crop((left, top, left + tw, top + th)).save(OUT, "JPEG", quality=85)
    print("wrote", OUT)

if __name__ == "__main__":
    make()
