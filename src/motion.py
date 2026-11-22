"""LMemM - motion, in one place. Every animation goes through here so they share one curve, one set of
durations and one reduced-motion rule (docs: design/ui-audit-emilkowalski.md).

    ease     a strong ease-out, cubic-bezier(0.2, 0.7, 0.1, 1) (the site's, tokens.EASE): enters and exits start fast.
    pop      a card or dialog arrives: opacity and a small scale (never from zero), under 300 ms.
    step     the setup window changes screen: a short push forward, the other way for Back.
    none     things a person does many times a day, and anything started from the keyboard, do not animate.

Reduced motion (System Settings > Accessibility > Display) keeps the fades and drops the movement.
Only opacity and transform are ever animated. Every call is best effort: if the animation cannot
be made, the change simply happens, so motion can never stop a card from showing.
"""

import tokens

EASE = tokens.EASE                 # the site's curve, cubic-bezier(.2, .7, .1, 1): a strong ease-out
PRESS_SECONDS = tokens.DURATION["press"]         # a pressed control
POP_SECONDS = tokens.DURATION["pop"]             # the pill's card, from a click
MODAL_SECONDS = tokens.DURATION["modal"]         # a dialog
STEP_SECONDS = tokens.DURATION["step"]           # a setup screen
EXIT_SECONDS = tokens.DURATION["exit"]           # leaving is quicker than arriving
CELEBRATE_SECONDS = tokens.DURATION["celebrate"]  # the "All caught up" badge
POP_SCALE = tokens.POP_SCALE                     # a small card starts here, never at zero
MODAL_SCALE = tokens.MODAL_SCALE


def reduced():
    """True when the person asked their Mac for less motion."""
    try:
        from AppKit import NSWorkspace
        return bool(NSWorkspace.sharedWorkspace().accessibilityDisplayShouldReduceMotion())
    except Exception:
        return False


def _timing():
    from Quartz import CAMediaTimingFunction
    return CAMediaTimingFunction.functionWithControlPoints____(*EASE)


def pop(view, origin="center", seconds=POP_SECONDS, scale=POP_SCALE):
    """Fade a view in while it grows from `scale` to full size. origin "bottom" keeps its bottom edge
    still (a card sitting just above the pill), "center" scales about the middle (a dialog).
    Reduced motion: the fade only."""
    try:
        from Foundation import NSValue
        from Quartz import (CAAnimationGroup, CABasicAnimation, CATransform3DConcat, CATransform3DIdentity,
                            CATransform3DMakeScale, CATransform3DMakeTranslation)
        view.setWantsLayer_(True)
        layer = view.layer()
        if layer is None:
            return
        parts = []
        fade = CABasicAnimation.animationWithKeyPath_("opacity")
        fade.setFromValue_(0.0)
        fade.setToValue_(1.0)
        parts.append(fade)
        if not reduced():
            drop = -(1.0 - scale) * view.bounds().size.height / 2 if origin == "bottom" else 0.0
            start = CATransform3DConcat(CATransform3DMakeScale(scale, scale, 1.0),
                                        CATransform3DMakeTranslation(0.0, drop, 0.0))
            grow = CABasicAnimation.animationWithKeyPath_("transform")
            grow.setFromValue_(NSValue.valueWithCATransform3D_(start))
            grow.setToValue_(NSValue.valueWithCATransform3D_(CATransform3DIdentity))
            parts.append(grow)
        timing = _timing()
        for part in parts:
            part.setDuration_(seconds)
            part.setTimingFunction_(timing)
        group = CAAnimationGroup.animation()
        group.setAnimations_(parts)
        group.setDuration_(seconds)
        layer.addAnimation_forKey_(group, "lmemm-pop")
    except Exception:
        pass


def fade_in(view, seconds=MODAL_SECONDS):
    """Opacity only (a scrim)."""
    try:
        from Quartz import CABasicAnimation
        view.setWantsLayer_(True)
        fade = CABasicAnimation.animationWithKeyPath_("opacity")
        fade.setFromValue_(0.0)
        fade.setToValue_(1.0)
        fade.setDuration_(seconds)
        fade.setTimingFunction_(_timing())
        view.layer().addAnimation_forKey_(fade, "lmemm-fade")
    except Exception:
        pass


def step(view, forward=True, seconds=STEP_SECONDS):
    """Call just before replacing a view's contents: the old screen leaves and the new one arrives from
    the side it belongs to (forward comes from the right, Back from the left). Reduced motion: a cross-fade."""
    try:
        from Quartz import CATransition
        view.setWantsLayer_(True)
        move = CATransition.animation()
        if reduced():
            move.setType_("fade")
        else:
            move.setType_("push")
            move.setSubtype_("fromRight" if forward else "fromLeft")
        move.setDuration_(seconds)
        move.setTimingFunction_(_timing())
        view.layer().addAnimation_forKey_(move, "lmemm-step")
    except Exception:
        pass


def eased(seconds):
    """Set the current NSAnimationContext (for an animator() change) to this duration and the shared curve."""
    try:
        from AppKit import NSAnimationContext
        context = NSAnimationContext.currentContext()
        context.setDuration_(seconds)
        context.setTimingFunction_(_timing())
    except Exception:
        pass


def fade_out(panel, done, seconds=EXIT_SECONDS):
    """Fade a panel out, then call done(). The window is put back to full alpha for its next showing, so a
    window opened again mid-fade can tell by `done` not running (the caller guards with a token)."""
    try:
        from AppKit import NSAnimationContext
        NSAnimationContext.beginGrouping()
        eased(seconds)
        NSAnimationContext.currentContext().setCompletionHandler_(done)
        panel.animator().setAlphaValue_(0.0)
        NSAnimationContext.endGrouping()
    except Exception:
        done()
