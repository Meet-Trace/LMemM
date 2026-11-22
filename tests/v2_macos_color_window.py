"""A borderless solid-colour AppKit window owned by its own process, for the real screenshot-scope tests.

    python v2_macos_color_window.py <r> <g> <b> <x> <y> <w> <h> <level>

x, y, w, h are in points from the top-left of the main display; level is "normal" or "floating". Prints "READY <pid>" once
the window is on screen and the run loop is running, then runs until its stdin closes or 60 s pass. Accessory activation
policy: no Dock icon, never takes focus. A "floating" window of another process over the front app stands in for what a
notification banner is: content that belongs to a different application than the one the privacy gates judged."""

import sys
import threading

import AppKit
import Foundation
import Quartz


def main(r, g, b, x, y, w, h, level):
    app = AppKit.NSApplication.sharedApplication()
    app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)
    screen_h = Quartz.CGDisplayBounds(Quartz.CGMainDisplayID()).size.height
    window = AppKit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
        Foundation.NSMakeRect(x, screen_h - y - h, w, h), AppKit.NSWindowStyleMaskBorderless,
        AppKit.NSBackingStoreBuffered, False)
    window.setBackgroundColor_(AppKit.NSColor.colorWithSRGBRed_green_blue_alpha_(r / 255, g / 255, b / 255, 1.0))
    window.setOpaque_(True)
    window.setHasShadow_(False)
    window.setLevel_(AppKit.NSStatusWindowLevel if level == "floating" else AppKit.NSNormalWindowLevel)
    window.orderFrontRegardless()

    def ready(timer):
        print(f"READY {Foundation.NSProcessInfo.processInfo().processIdentifier()}", flush=True)
    Foundation.NSTimer.scheduledTimerWithTimeInterval_repeats_block_(0.7, False, ready)

    def watch():
        sys.stdin.read()
        app.terminate_(None)
    threading.Thread(target=watch, daemon=True).start()
    Foundation.NSTimer.scheduledTimerWithTimeInterval_repeats_block_(60.0, False, lambda t: app.terminate_(None))
    app.run()


if __name__ == "__main__":
    main(*(int(v) for v in sys.argv[1:8]), sys.argv[8])
