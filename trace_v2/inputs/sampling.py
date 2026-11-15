"""Cursor sampling and event-time conversion. Pure logic with injected clocks, so it is testable anywhere."""

MIN_SAMPLE_MS, MAX_SAMPLE_MS, DEFAULT_SAMPLE_MS = 100, 200, 150
_MS = 1_000_000


class MachClock:
    """Converts CGEvent timestamps to wall-clock ns. CGEventGetTimestamp is already in nanoseconds on the uptime
    (mach absolute time) clock, measured on this Mac as equal to mach_absolute_time() * timebase, so no tick
    conversion is needed, only an offset: wall = timestamp + offset, with offset = wall now minus uptime ns now,
    captured at construction. `uptime_ns` must be on that same clock.
    mach time does not advance while the Mac sleeps and the wall clock can be stepped, so the offset goes stale:
    `check()` (called from the tap's housekeeping timer) compares how far the wall clock and the mach clock moved
    since the last check and re-syncs when they disagree by more than `tolerance_ns` (default 1 s). Events already
    converted keep their timestamps. `resyncs` counts re-syncs.
    """

    def __init__(self, uptime_ns, wall_now, tolerance_ns=1_000_000_000):
        self._mach_now, self._wall_now = uptime_ns, wall_now
        self.tolerance_ns = tolerance_ns
        self.resyncs = 0
        self._sync()

    def _sync(self):
        mach, wall = self._mach_now(), self._wall_now()
        self.offset_ns = wall - mach
        self._last = (mach, wall)

    def to_wall_ns(self, timestamp_ns):
        return timestamp_ns + self.offset_ns

    def check(self):
        """-> True when the offset had gone stale and was re-synced."""
        mach_ns, wall = self._mach_now(), self._wall_now()
        drift = (wall - self._last[1]) - (mach_ns - self._last[0])
        if abs(drift) > self.tolerance_ns:
            self.resyncs += 1
            self._sync()
            return True
        self._last = (mach_ns, wall)
        return False


class CursorSampler:
    """At most one cursor_move per `interval_ns` while the cursor moves, plus a final position once it stops.

    move(t_ns, x, y): a move at least one interval after the last emitted sample is emitted at once; moves inside
    the interval only update the pending position. tick(now_ns) (called by a timer, at most every interval/2)
    emits the pending position as `final` once the cursor has been still for an interval. The final sample is
    stamped max(time of the last move, last sample + interval): the cursor stayed where it was from its last move
    until the next move, so the position is correct at that time and two samples are never closer than the
    interval. Each sample carries `coalesced`, the number of raw moves it stands for (the first sample after a
    pause counts 1). Not thread-safe: use from one thread (the tap's run loop).
    """

    def __init__(self, emit, interval_ms=DEFAULT_SAMPLE_MS):
        if isinstance(interval_ms, bool) or not isinstance(interval_ms, int) \
                or not MIN_SAMPLE_MS <= interval_ms <= MAX_SAMPLE_MS:
            raise ValueError(f"cursor sample interval must be an integer {MIN_SAMPLE_MS}..{MAX_SAMPLE_MS} ms")
        self.emit, self.interval_ns = emit, interval_ms * _MS
        self._last_emit = None
        self._pending = None            # (t_ns, x, y)
        self._coalesced = 0
        self.raw_moves = self.samples = self.finals = 0

    def move(self, t_ns, x, y):
        self.raw_moves += 1
        self._coalesced += 1
        if self._last_emit is None or t_ns - self._last_emit >= self.interval_ns:
            self._emit(t_ns, x, y, final=False)
        else:
            self._pending = (t_ns, x, y)

    def tick(self, now_ns):
        pending = self._pending
        if pending is not None and now_ns - pending[0] >= self.interval_ns:
            t, x, y = pending
            self._emit(max(t, self._last_emit + self.interval_ns), x, y, final=True)

    def _emit(self, t_ns, x, y, final):
        payload = {"x": x, "y": y, "coalesced": self._coalesced}
        if final:
            payload["final"] = True
            self.finals += 1
        self._coalesced, self._pending, self._last_emit = 0, None, t_ns
        self.samples += 1
        self.emit(t_ns, payload)
