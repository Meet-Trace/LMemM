"""LMemM - the living ambient canvas: the supplied artwork behind the window, with light that very slowly moves.

Layer-backed views and Core Animation, nothing drawn per frame:

    picture view    one per period; draws the supplied picture once (cropped, not stretched, around its lit
                    region) under a graphite wash, and again only when the window is resized
    glow views      a few faint soft lights on the picture; each drifts and swells on its own 30-90 s cycle
                    (opacity and transform animations the compositor runs; the app does nothing per frame)
    grain view      a static, pre-made noise tile on top, the same for every period
    cross-fade      a period change fades the new picture in over the old one (never an empty frame)

No Core Graphics objects are handed to Core Animation (an earlier version set a CGImage as a layer's contents and
crashed inside PyObjC's bridge); images are NSImages drawn by AppKit.

Pure decisions (files, crop, light data) are in ambient_model.py; which period is showing is backdrop.py; the
window calls show(). The animations stop when the window is hidden, minimised or covered and when the Mac's
Reduce Motion is on; the picture then simply stays still. If the artwork is missing the window keeps the
code-drawn stand-in (window.py), so nothing here can leave the window empty.
"""

import sys
import traceback

import objc
from AppKit import NSBezierPath, NSColor, NSGradient, NSImage, NSMakeRect, NSNumber, NSView, NSWorkspace
from Foundation import NSData, NSNotificationCenter

import ambient_model as model
import config
import grain
import motion

_blobs = {}
_grain_colors = {}
SOURCE_OVER = 2                # NSCompositingOperationSourceOver
WINDOW_BELOW = -1
WIDTH_HEIGHT_SIZABLE = 2 | 16  # NSViewWidthSizable | NSViewHeightSizable


def grain_color(opacity):
    """The film-grain tile (grain.py) as a tiling colour; made once per strength and kept."""
    key = round(opacity, 3)
    if key not in _grain_colors:
        png = grain.tile_png(key)
        image = NSImage.alloc().initWithData_(NSData.dataWithBytes_length_(png, len(png)))
        image.setSize_((grain.SIZE / 2, grain.SIZE / 2))                  # drawn at half size: fine on a Retina screen
        _grain_colors[key] = NSColor.colorWithPatternImage_(image)
    return _grain_colors[key]


def _blob(color):
    """A soft radial glow of one colour (opaque at the centre, clear at the edge) as an NSImage, made once."""
    if color not in _blobs:
        size = 256
        image = NSImage.alloc().initWithSize_((size, size))
        image.lockFocus()
        centre = (size / 2, size / 2)
        r, g, b = color
        NSGradient.alloc().initWithColors_([NSColor.colorWithSRGBRed_green_blue_alpha_(r, g, b, 1.0),
                                            NSColor.colorWithSRGBRed_green_blue_alpha_(r, g, b, 0.3),
                                            NSColor.colorWithSRGBRed_green_blue_alpha_(r, g, b, 0.0)]) \
            .drawFromCenter_radius_toCenter_radius_options_(centre, 0, centre, size / 2, 0)
        image.unlockFocus()
        _blobs[color] = image
    return _blobs[color]


def _number(value):
    return NSNumber.numberWithDouble_(float(value))


def _ease():
    from Quartz import CAMediaTimingFunction, kCAMediaTimingFunctionEaseInEaseOut
    return CAMediaTimingFunction.functionWithName_(kCAMediaTimingFunctionEaseInEaseOut)


class _Passive(NSView):
    """Base of the views below: clicks pass through, and nothing else is exposed to Objective-C."""

    def hitTest_(self, point):
        return None


class _Picture(_Passive):
    """One period's artwork under its graphite wash. Draws once; only a resize draws it again."""

    def drawRect_(self, rect):
        try:
            image = getattr(self, "image", None)
            box = self.bounds()
            if image is None:
                return
            iw, ih = image.size()
            x, y, w, h = model.crop(box.size.width, box.size.height, iw, ih, self.focus)
            image.drawInRect_fromRect_operation_fraction_(box, NSMakeRect(x * iw, (1.0 - y - h) * ih, w * iw, h * ih), SOURCE_OVER, 1.0)
            if self.scrim > 0:
                NSColor.blackColor().colorWithAlphaComponent_(self.scrim).setFill()
                NSBezierPath.fillRect_(box)
        except Exception:
            traceback.print_exc()


class _Glow(_Passive):
    def drawRect_(self, rect):
        try:
            _blob(self.color).drawInRect_fromRect_operation_fraction_(self.bounds(), NSMakeRect(0, 0, 0, 0), SOURCE_OVER, 1.0)
        except Exception:
            traceback.print_exc()


class _Grain(_Passive):
    def drawRect_(self, rect):
        try:
            grain_color(config.AMBIENT_GRAIN).setFill()
            NSBezierPath.fillRect_(self.bounds())
        except Exception:
            traceback.print_exc()


class _Host(_Passive):
    """The view that goes into the window; tells its owner when it is resized."""

    def setFrameSize_(self, size):
        objc.super(_Host, self).setFrameSize_(size)
        owner = getattr(self, "owner", None)
        if owner is not None:
            owner.relayout()


class AmbientCanvas:
    """The background behind the whole window; `.view` is what goes into the window. See the module note."""

    def __init__(self, frame):
        self.view = _Host.alloc().initWithFrame_(frame)
        self.view.owner = None
        self.view.setWantsLayer_(True)
        self.pictures = {}               # period -> its picture view (glows are its subviews)
        self.current = None
        self.visible = True
        self.watching = []
        self.missing = model.missing()
        self.usable = not self.missing
        if not self.usable:
            print("ambient: artwork not in place, keeping the code-drawn background; missing: "
                  + ", ".join(p + ".webp" for p in self.missing) + " in " + model.art_dir(), file=sys.stderr)
        self.grain = None
        if self.usable and config.AMBIENT_GRAIN > 0:
            self.grain = _Grain.alloc().initWithFrame_(frame)
            self.grain.setAutoresizingMask_(WIDTH_HEIGHT_SIZABLE)
            self.grain.setWantsLayer_(True)
            self.view.addSubview_(self.grain)
        self.view.setHidden_(True)       # shown once a picture has loaded
        self.view.owner = self

    # ------------------------------------------------------------ showing a period

    def show(self, key, animate=True, seconds=None):
        """Show `key`'s artwork, fading it in over what is there. True if it is showing; False if it cannot (the
        window then falls back to the code-drawn stand-in)."""
        if not self.usable or key not in model.PERIODS:
            return False
        if key == self.current:
            return True
        picture = self._picture_for(key)
        if picture is None:
            return False
        self.view.setHidden_(False)
        self._motion(self.current, False)
        first = self.current is None
        self.current = key
        self._to_top(picture)
        fade = seconds if seconds is not None else config.AMBIENT_FADE_SECONDS
        if first or not animate or motion.reduced() or fade <= 0:
            self._settle(picture)
        else:
            from Quartz import CABasicAnimation, CATransaction
            layer = picture.layer()
            layer.setOpacity_(1.0)
            fading = CABasicAnimation.animationWithKeyPath_("opacity")
            fading.setFromValue_(_number(0.0))
            fading.setToValue_(_number(1.0))
            fading.setDuration_(fade)
            fading.setTimingFunction_(_ease())
            CATransaction.begin()
            CATransaction.setCompletionBlock_(lambda: self._settle(picture))
            layer.addAnimation_forKey_(fading, "fade")
            CATransaction.commit()
        self._motion(key, True)
        return True

    def _to_top(self, picture):
        """Above every other picture, below the grain."""
        if picture.superview() is not None:
            picture.removeFromSuperview()
        if self.grain is not None:
            self.view.addSubview_positioned_relativeTo_(picture, WINDOW_BELOW, self.grain)
        else:
            self.view.addSubview_(picture)

    def _settle(self, picture):
        """`picture` is fully in: the pictures under it can go (their pixels are freed)."""
        if picture.superview() is None:
            return
        order = list(self.view.subviews())
        for key, other in list(self.pictures.items()):
            if other is not picture and other in order and order.index(other) < order.index(picture):
                other.layer().removeAllAnimations()
                other.removeFromSuperview()
                other.image = None
                del self.pictures[key]

    def _picture_for(self, key):
        picture = self.pictures.get(key)
        if picture is not None and picture.image is not None:
            return picture
        image = NSImage.alloc().initWithContentsOfFile_(model.art_path(key))
        if image is None:
            return None
        picture = _Picture.alloc().initWithFrame_(self.view.bounds())
        picture.image, picture.focus, picture.scrim = image, model.FOCUS[key], model.SCRIM[key]
        picture.setAutoresizingMask_(WIDTH_HEIGHT_SIZABLE)
        picture.setWantsLayer_(True)
        picture.glows = []
        for fx, fy, size, color, strength, _dx, _dy, _p, _q in model.LIGHTS[key]:
            glow = _Glow.alloc().initWithFrame_(NSMakeRect(0, 0, 10, 10))
            glow.color = color
            glow.setWantsLayer_(True)
            glow.layer().setOpacity_(strength)
            picture.addSubview_(glow)
            picture.glows.append(glow)
        self.pictures[key] = picture
        self._place(key)
        return picture

    # ------------------------------------------------------------ geometry (on every resize)

    def _place(self, key):
        """Set each glow's box for the window's size (the picture itself follows by autoresizing)."""
        picture = self.pictures.get(key)
        if picture is None:
            return
        box = self.view.bounds()
        w, h = box.size.width, box.size.height
        longer = max(w, h)
        for glow, spec in zip(picture.glows, model.LIGHTS[key]):
            fx, fy, size = spec[0], spec[1], spec[2]
            side = size * longer
            glow.setFrame_(NSMakeRect(fx * w - side / 2, (1.0 - fy) * h - side / 2, side, side))

    def relayout(self):
        try:
            for key in list(self.pictures):
                self._place(key)
                self.pictures[key].setNeedsDisplay_(True)           # the crop follows the window's shape
            if self.current:
                self._motion(self.current, True)                    # the drift distances follow the new size
        except Exception:
            traceback.print_exc()

    # ------------------------------------------------------------ slow movement

    def _running(self):
        return self.visible and config.AMBIENT_MOTION and not motion.reduced()

    def _motion(self, key, on):
        """Start (or stop) the drift and swell of one period's lights. Only the showing period ever moves."""
        picture = self.pictures.get(key) if key else None
        if picture is None:
            return
        for glow in picture.glows:
            glow.layer().removeAllAnimations()
        if not (on and self._running()):
            return
        from Quartz import CABasicAnimation
        box = self.view.bounds()
        longer = max(box.size.width, box.size.height)
        for glow, spec in zip(picture.glows, model.LIGHTS[key]):
            strength, dx, dy, phase_a, phase_b = spec[4], spec[5], spec[6], spec[7], spec[8]
            layer = glow.layer()
            for path, distance, phase in (("transform.translation.x", dx * longer, phase_a), ("transform.translation.y", dy * longer, phase_b)):
                move = CABasicAnimation.animationWithKeyPath_(path)       # x and y on different cycles: the path never quite repeats
                move.setFromValue_(_number(-distance / 2))
                move.setToValue_(_number(distance / 2))
                move.setDuration_(model.seconds_between(config.AMBIENT_MOVE_SECONDS, phase))
                self._loop(move)
                layer.addAnimation_forKey_(move, path)
            swell = CABasicAnimation.animationWithKeyPath_("opacity")
            swell.setFromValue_(_number(strength * (1.0 - model.PULSE_DEPTH)))
            swell.setToValue_(_number(strength))
            swell.setDuration_(model.seconds_between(config.AMBIENT_PULSE_SECONDS, phase_b))
            self._loop(swell)
            layer.addAnimation_forKey_(swell, "swell")

    @staticmethod
    def _loop(animation):
        animation.setAutoreverses_(True)
        animation.setRepeatCount_(float("inf"))
        animation.setTimingFunction_(_ease())

    # ------------------------------------------------------------ lifecycle: hidden, minimised, covered, Reduce Motion

    def attach(self, window):
        """Follow `window`: movement stops whenever nobody can see it and when Reduce Motion is on."""
        center = NSNotificationCenter.defaultCenter()
        names = ("NSWindowDidChangeOcclusionStateNotification", "NSWindowDidMiniaturizeNotification",
                 "NSWindowDidDeminiaturizeNotification", "NSWindowDidChangeScreenNotification")
        for name in names:
            self.watching.append(center.addObserverForName_object_queue_usingBlock_(name, window, None, lambda note, w=window: self._look(w)))
        for name in ("NSApplicationDidHideNotification", "NSApplicationDidUnhideNotification"):
            self.watching.append(center.addObserverForName_object_queue_usingBlock_(name, None, None, lambda note, w=window: self._look(w)))
        try:
            self.watching.append(NSWorkspace.sharedWorkspace().notificationCenter().addObserverForName_object_queue_usingBlock_(
                "NSWorkspaceAccessibilityDisplayOptionsDidChangeNotification", None, None, lambda note, w=window: self._look(w, force=True)))
        except Exception:
            pass
        self._look(window)

    def _look(self, window, force=False):
        try:
            seen = bool(window.isVisible() and not window.isMiniaturized() and (int(window.occlusionState()) & 2))   # 2 = NSWindowOcclusionStateVisible
        except Exception:
            seen = True
        if seen == self.visible and not force:
            return
        self.visible = seen
        self._motion(self.current, True)
