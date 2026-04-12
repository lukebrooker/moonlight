"""Generate a Moonlight app icon (.icns) using AppKit.

Renders a moon emoji onto a gradient background, produces a set of PNG
variants at standard icon sizes, then combines them into icon.icns.
"""

import os
import subprocess
import sys

import AppKit
import Foundation
from AppKit import (
    NSBitmapImageRep,
    NSColor,
    NSFont,
    NSGradient,
    NSImage,
    NSMakeRect,
    NSMutableParagraphStyle,
    NSPNGFileType,
    NSString,
    NSBezierPath,
)
from Foundation import NSMakePoint, NSMakeSize

ICONSET_DIR = "Moonlight.iconset"
ICNS_PATH = "icon.icns"
SIZES = [16, 32, 64, 128, 256, 512, 1024]


def render_icon(size: int) -> bytes:
    """Render the Moonlight icon at a given pixel size and return PNG bytes."""
    image = NSImage.alloc().initWithSize_(NSMakeSize(size, size))
    image.lockFocus()

    # Rounded-rect background with a deep purple-to-navy gradient
    radius = size * 0.22
    rect = NSMakeRect(0, 0, size, size)
    path = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(rect, radius, radius)
    path.addClip()

    top = NSColor.colorWithCalibratedRed_green_blue_alpha_(0.12, 0.05, 0.30, 1.0)
    bottom = NSColor.colorWithCalibratedRed_green_blue_alpha_(0.02, 0.02, 0.10, 1.0)
    gradient = NSGradient.alloc().initWithStartingColor_endingColor_(top, bottom)
    gradient.drawInRect_angle_(rect, 270)

    # Draw the moon emoji centered
    font_size = size * 0.68
    font = NSFont.systemFontOfSize_(font_size)
    para = NSMutableParagraphStyle.alloc().init()
    para.setAlignment_(AppKit.NSTextAlignmentCenter)

    attrs = {
        AppKit.NSFontAttributeName: font,
        AppKit.NSForegroundColorAttributeName: NSColor.whiteColor(),
        AppKit.NSParagraphStyleAttributeName: para,
    }
    text = NSString.stringWithString_("🌙")
    text_size = text.sizeWithAttributes_(attrs)
    draw_rect = NSMakeRect(
        (size - text_size.width) / 2,
        (size - text_size.height) / 2 - size * 0.02,
        text_size.width,
        text_size.height,
    )
    text.drawInRect_withAttributes_(draw_rect, attrs)

    image.unlockFocus()

    # Convert to PNG
    tiff = image.TIFFRepresentation()
    rep = NSBitmapImageRep.imageRepWithData_(tiff)
    png_data = rep.representationUsingType_properties_(NSPNGFileType, None)
    return bytes(png_data)


def write_iconset():
    os.makedirs(ICONSET_DIR, exist_ok=True)

    # Standard Apple iconset naming: icon_<size>x<size>.png and @2x variants
    variants = [
        (16, "icon_16x16.png", 1),
        (16, "icon_16x16@2x.png", 2),
        (32, "icon_32x32.png", 1),
        (32, "icon_32x32@2x.png", 2),
        (128, "icon_128x128.png", 1),
        (128, "icon_128x128@2x.png", 2),
        (256, "icon_256x256.png", 1),
        (256, "icon_256x256@2x.png", 2),
        (512, "icon_512x512.png", 1),
        (512, "icon_512x512@2x.png", 2),
    ]

    for base_size, name, scale in variants:
        pixel_size = base_size * scale
        print(f"  Rendering {name} ({pixel_size}x{pixel_size})...")
        data = render_icon(pixel_size)
        path = os.path.join(ICONSET_DIR, name)
        with open(path, "wb") as f:
            f.write(data)


def build_icns():
    print(f"Building {ICNS_PATH}...")
    subprocess.run(
        ["iconutil", "-c", "icns", ICONSET_DIR, "-o", ICNS_PATH],
        check=True,
    )
    print(f"Done: {ICNS_PATH}")


if __name__ == "__main__":
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    print("Generating icon PNGs...")
    write_iconset()
    build_icns()
