"""LMemM - the living ambient canvas: the supplied artwork behind the window, with light that very slowly moves.

One layer-backed view, only Core Animation underneath (no per-frame drawing, no timers of its own):

    picture layer   one per period; the supplied picture, cropped (not stretched) to the window around its lit region
    light layers    a few faint soft glows on the picture; each drifts and swells on its own 30-90 s cycle
                    (opacity and transform animations the compositor runs; the app does nothing per frame)
    grain layer     a static, pre-made noise tile on top, the same for every period
    cross-fade      a period change fades the new picture in over the old one (never an empty frame)

Pure decisions (files, crop, light data) are in ambient_model.py; which period is showing is backdrop.py; the
window calls show(). The animations stop when the window is hidden, minimised or covered and when the Mac's
Reduce Motion is on; the picture then simply stays still. If the artwork is missing the window keeps the
code-drawn stand-in (window.py), so nothing here can leave the window empty.
"""

import sys
import traceback

import objc
from AppKit import NSColor, NSGradient, NSImage, NSMakeRect, NSView, NSWorkspace
from Foundation import NSData, NSNotificationCenter, NSNull, NSURL

import ambient_model as model
import config
import grain
import motion

MAX_PIXELS = 3200                  # the long side the pictures are decoded at: sharp on a Retina window, a third of the memory of 5K
_blobs = {}
_NONE = NSNull.null()          # a layer action of NSNull means: do not animate this property
_grain_colors = {}


def grain_color(opacity):
    """The film-grain tile (grain.py) as a tiling colour; made once per strength and kept."""
    key = round(opacity, 3)
    if key not in _grain_colors:
        png = grain.tile_png(key)
        image = NSImage.alloc().initWithData_(NSData.dataWithBytes_length_(png, len(png)))
        image.setSize_((grain.SIZE / 2, grain.SIZE / 2))                  # drawn at half size: fine on a Retina screen
        _grain_colors[key] = NSColor.colorWithPatternImage_(image)
    return _grain_colors[key]


def _decode(path):
    """The picture at `path` as a decoded CGImage (ImageIO reads WebP on macOS 11+), or None."""
    from Quartz import (CGImageSourceCreateThumbnailAtIndex, CGImageSourceCreateWithURL, kCGImageSourceCreateThumbnailFromImageAlways,
                        kCGImageSourceCreateThumbnailWithTransform, kCGImageSourceShouldCacheImmediately, kCGImageSourceThumbnailMaxPixelSize)
    source = CGImageSourceCreateWithURL(NSURL.fileURLWithPath_(path), None)
    if source is None:
        return None
    return CGImageSourceCreateThumbnailAtIndex(source, 0, {
        kCGImageSourceCreateThumbnailFromImageAlways: True, kCGImageSourceCreateThumbnailWithTransform: True,
        kCGImageSourceShouldCacheImmediately: True, kCGImageSourceThumbnailMaxPixelSize: MAX_PIXELS})


def _blob(color):
    """A soft radial glow of one colour (opaque at the centre, clear at the edge) as a small CGImage, made once."""
    if color not in _blobs:
        size = 256
        image = NSImage.alloc().initWithSize_((size, size))
        image.lockFocus()
        centre = (size / 2, size / 2)
        r, g, b = color
        clear = NSColor.colorWithSRGBRed_green_blue_alpha_(r, g, b, 0.0)
        NSGradient.alloc().initWithColors_([NSColor.colorWithSRGBRed_green_blue_alpha_(r, g, b, 1.0),
                                            NSColor.colorWithSRGBRed_green_blue_alpha_(r, g, b, 0.3), clear]) \
            .drawFromCenter_radius_toCenter_radius_options_(centre, 0, centre, size / 2, 0)
        image.unlockFocus()
        _blobs[color] = image.CGImageForProposedRect_context_hints_(None, None, None)
    return _blobs[color]


def _ease():
    from Quartz import CAMediaTimingFunction, kCAMediaTimingFunctionEaseInEaseOut
    return CAMediaTimingFunction.functionWithName_(kCAMediaTimingFunctionEaseInEaseOut)


class AmbientCanvas(NSView):
    """The background behind the whole window. Clicks pass through. See the module note."""

    def initWithFrame_(self, frame):
        self = objc.super(AmbientCanvas, self).initWithFrame_(frame)
        if self is None:
            return None
        from Quartz import CALayer
        self.setWantsLayer_(True)
        self.layers = {}                 # period -> its picture layer
        self.sizes = {}                  # period -> (pixel width, pixel height)
        self.current = None
        self.top = 0                     # z-order counter: a newly shown picture goes above the rest
        self.visible = True
        self.watching = []
        self.missing = model.missing()
        self.usable = not self.missing
        if not self.usable:
            print("ambient: artwork not in place, keeping the code-drawn background; missing: "
                  + ", ".join(p + ".webp" for p in self.missing) + " in " + model.art_dir(), file=sys.stderr)
        self.grain_layer = None
        if self.usable and config.AMBIENT_GRAIN > 0:
            self.grain_layer = CALayer.layer()
            self.grain_layer.setBackgroundColor_(grain_color(config.AMBIENT_GRAIN).CGColor())
            self.grain_layer.setZPosition_(1e6)
            self.grain_layer.setActions_({"bounds": _NONE, "position": _NONE, "contents": _NONE})   # never animate a resize
            self.layer().addSublayer_(self.grain_layer)
        self.setHidden_(True)            # shown once a picture has loaded
        return self

    def hitTest_(self, point):
        return None

    def setFrameSize_(self, size):
        objc.super(AmbientCanvas, self).setFrameSize_(size)
        try:
            self._relayout()
        except Exception:
            traceback.print_exc()

    # ------------------------------------------------------------ showing a period

    def show(self, key, animate=True, seconds=None):
        """Show `key`'s artwork, fading it in over what is there. True if it is showing; False if it cannot (the
        window then falls back to the code-drawn stand-in)."""
        if not self.usable or key not in model.PERIODS:
            return False
        if key == self.current:
            return True
        try:
            layer = self._layer_for(key)
        except Exception:
            traceback.print_exc()
            return False
        if layer is None:
            return False
        from Quartz import CATransaction
        self.setHidden_(False)
        self.top += 1
        self._motion(self.current, False)
        first = self.current is None
        self.current = key
        CATransaction.begin()
        CATransaction.setDisableActions_(True)
        layer.setZPosition_(self.top)
        layer.setOpacity_(0.0)
        CATransaction.commit()
        fade = (seconds if seconds is not None else config.AMBIENT_FADE_SECONDS)
        if first or not animate or motion.reduced() or fade <= 0:
            self._settle(layer, instant=True)
        else:
            CATransaction.begin()
            CATransaction.setAnimationDuration_(fade)
            CATransaction.setAnimationTimingFunction_(_ease())
            CATransaction.setCompletionBlock_(lambda: self._settle(layer))
            layer.setOpacity_(1.0)
            CATransaction.commit()
        self._motion(key, True)
        return True

    def _settle(self, layer, instant=False):
        """`layer` is fully in: the pictures under it can go (their pixels are freed)."""
        from Quartz import CATransaction
        CATransaction.begin()
        CATransaction.setDisableActions_(True)
        if instant:
            layer.setOpacity_(1.0)
        for key, other in list(self.layers.items()):
            if other is not layer and other.zPosition() < layer.zPosition():
                other.removeAllAnimations()
                other.setOpacity_(0.0)
                other.setContents_(None)
                for sub in list(other.sublayers() or ()):
                    sub.removeAllAnimations()
        CATransaction.commit()

    def _layer_for(self, key):
        """The picture layer for a period, made (and its picture decoded) when it is first needed or after it was freed."""
        from Quartz import CALayer, CATransaction
        layer = self.layers.get(key)
        if layer is not None and layer.contents() is not None:
            return layer
        image = _decode(model.art_path(key))
        if image is None:
            return None
        from Quartz import CGImageGetHeight, CGImageGetWidth
        self.sizes[key] = (CGImageGetWidth(image), CGImageGetHeight(image))
        CATransaction.begin()
        CATransaction.setDisableActions_(True)
        if layer is None:
            layer = CALayer.layer()
            layer.setMasksToBounds_(True)
            layer.setOpacity_(0.0)
            layer.setActions_({"bounds": _NONE, "position": _NONE, "contents": _NONE, "contentsRect": _NONE})
            self.layer().insertSublayer_below_(layer, self.grain_layer) if self.grain_layer is not None else self.layer().addSublayer_(layer)
            scrim = CALayer.layer()                                 # always the first sublayer; the glows follow it
            scrim.setBackgroundColor_(NSColor.blackColor().colorWithAlphaComponent_(model.SCRIM[key]).CGColor())
            scrim.setActions_({"bounds": _NONE, "position": _NONE})
            layer.addSublayer_(scrim)
            for spec in model.LIGHTS[key]:
                glow = CALayer.layer()
                glow.setContents_(_blob(spec[3]))
                glow.setOpacity_(spec[4])
                glow.setActions_({"bounds": _NONE, "position": _NONE, "opacity": _NONE, "transform": _NONE})
                layer.addSublayer_(glow)
            self.layers[key] = layer
        layer.setContents_(image)
        self._place(key)
        CATransaction.commit()
        return layer

    # ------------------------------------------------------------ geometry (on every resize)

    def _place(self, key):
        """Size one period's layers to the window: crop the picture around its lit region, set each glow's box."""
        layer = self.layers.get(key)
        if layer is None:
            return
        box = self.bounds()
        w, h = box.size.width, box.size.height
        layer.setFrame_(NSMakeRect(0, 0, w, h))
        iw, ih = self.sizes.get(key, (0, 0))
        x, y, cw, ch = model.crop(w, h, iw, ih, model.FOCUS[key])
        layer.setContentsRect_(((x, 1.0 - y - ch), (cw, ch)))             # contentsRect's origin is bottom-left
        longer = max(w, h)
        layer.sublayers()[0].setFrame_(NSMakeRect(0, 0, w, h))
        for glow, (fx, fy, size, _color, _strength, _dx, _dy, _p, _q) in zip(list(layer.sublayers())[1:], model.LIGHTS[key]):
            side = size * longer
            glow.setBounds_(NSMakeRect(0, 0, side, side))
            glow.setPosition_((fx * w, (1.0 - fy) * h))

    def _relayout(self):
        from Quartz import CATransaction
        CATransaction.begin()
        CATransaction.setDisableActions_(True)
        box = self.bounds()
        if self.grain_layer is not None:
            self.grain_layer.setFrame_(NSMakeRect(0, 0, box.size.width, box.size.height))
        for key, layer in self.layers.items():
            if layer.contents() is not None:
                self._place(key)
        CATransaction.commit()
        if self.current:
            self._motion(self.current, True)                             # the drift distances follow the new size

    # ------------------------------------------------------------ slow movement

    def _running(self):
        return self.visible and config.AMBIENT_MOTION and not motion.reduced()

    def _motion(self, key, on):
        """Start (or stop) the drift and swell of one period's lights. Only the showing period ever moves."""
        layer = self.layers.get(key) if key else None
        if layer is None:
            return
        glows = list(layer.sublayers() or ())[1:]                        # [0] is the still scrim
        for glow in glows:
            glow.removeAllAnimations()
        if not (on and self._running()):
            return
        from Quartz import CABasicAnimation
        box = self.bounds()
        longer = max(box.size.width, box.size.height)
        for glow, (_fx, _fy, _size, _color, strength, dx, dy, phase_a, phase_b) in zip(glows, model.LIGHTS[key]):
            for path, distance, phase in (("transform.translation.x", dx * longer, phase_a), ("transform.translation.y", dy * longer, phase_b)):
                move = CABasicAnimation.animationWithKeyPath_(path)       # x and y on different cycles: the path never quite repeats
                move.setFromValue_(-distance / 2)
                move.setToValue_(distance / 2)
                move.setDuration_(model.seconds_between(config.AMBIENT_MOVE_SECONDS, phase))
                self._loop(move)
                glow.addAnimation_forKey_(move, path)
            swell = CABasicAnimation.animationWithKeyPath_("opacity")
            swell.setFromValue_(strength * (1.0 - model.PULSE_DEPTH))
            swell.setToValue_(strength)
            swell.setDuration_(model.seconds_between(config.AMBIENT_PULSE_SECONDS, phase_b))
            self._loop(swell)
            glow.addAnimation_forKey_(swell, "swell")

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
