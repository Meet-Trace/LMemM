"""A tiny AppKit app for the real accessibility tests: one window with a text field and a secure text field.

    python v2_macos_ax_window.py <normal text> <secure text>

Prints "READY <pid>" once the window is up and the run loop is running, then runs until its stdin closes (the test process ends it) or 120 s pass.
Accessory activation policy: no Dock icon, and it never takes focus from the user."""

import sys
import threading

import AppKit
import Foundation

TITLE = "Trace AX Test Window"


def main(normal, secure):
    app = AppKit.NSApplication.sharedApplication()
    app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)
    window = AppKit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
        Foundation.NSMakeRect(200, 200, 520, 160), AppKit.NSWindowStyleMaskTitled, AppKit.NSBackingStoreBuffered, False)
    window.setTitle_(TITLE)
    view = window.contentView()
    for i, (cls, text, identifier) in enumerate(((AppKit.NSTextField, normal, "normal-field"),
                                                  (AppKit.NSSecureTextField, secure, "secure-field"))):
        field = cls.alloc().initWithFrame_(Foundation.NSMakeRect(20, 90 - i * 50, 460, 28))
        field.setStringValue_(text)
        field.setIdentifier_(identifier)
        view.addSubview_(field)
    window.orderFrontRegardless()

    def ready(timer):                       # from inside the run loop: before it runs the app answers AX with timeouts
        print(f"READY {Foundation.NSProcessInfo.processInfo().processIdentifier()}", flush=True)
    Foundation.NSTimer.scheduledTimerWithTimeInterval_repeats_block_(0.5, False, ready)

    def watch():
        sys.stdin.read()
        app.terminate_(None)
    threading.Thread(target=watch, daemon=True).start()
    Foundation.NSTimer.scheduledTimerWithTimeInterval_repeats_block_(120.0, False, lambda t: app.terminate_(None))
    app.run()


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
