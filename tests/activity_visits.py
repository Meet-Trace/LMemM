"""Hand-built Moment records for the PR 2B tests (test-only): one visit at a time, any field set explicitly.

Records are real moments.Moment dicts, so activity_input.prepare validates them like captured data."""

import moments

T0 = 1_800_000_000.0


class Clock:
    def __init__(self, t0=T0):
        self.t, self.n, self.records = t0, 0, []

    def visit(self, item, moments_=None, kind="reading_page", app="Google Chrome", url=None, window="Page", target=None,
              dwell=120.0, terms=(), idents=(), lines=(), frames=None, prov=None, gap=2.0, moments_spec=None):
        """Append one visit. `moments_spec`: list of dicts (dwell, terms, idents, lines) for a visit with several moments."""
        self.t += gap
        self.n += 1
        vid = f"v{int(self.t)}-{self.n}"
        spec = moments_spec or [{"dwell": dwell, "terms": terms, "idents": idents, "lines": lines}]
        u, domain = moments.clean_url(url) if url else (None, None)
        t = self.t
        for seq, s in enumerate(spec):
            a, b = t, t + s["dwell"]
            fr = frames if frames is not None else max(2, int(s["dwell"] // 3))
            m = moments.Moment(
                id=f"{vid}.{seq}", visit_id=vid, seq=seq, t_start=a, t_end=b, item_id=f"item-{item}", app=app,
                bundle_id="com.example." + app.replace(" ", "").lower(), window=window, url=u, domain=domain, kind=kind,
                target=target, ref=f"{app}|{kind}|{target or u}", n_frames=fr,
                interaction={"category": "reading", "seconds": {"reading": s["dwell"]}, "ages": {}},
                digest={"terms": list(s["terms"]), "identifiers": list(s["idents"]), "lines": list(s["lines"]),
                        "text_hash": "abc" if (s["terms"] or s["idents"] or s["lines"]) else None},
                provenance=dict(prov) if prov is not None else {"ocr": fr},
                context={"prev_moment_id": None if seq == 0 else f"{vid}.{seq - 1}", "gap_s": None,
                         "same_item_before": None, "trigger": None, "pinned": False})
            self.records.append(m.to_dict())
            t = b
        self.t = t
        return vid

    def fillers(self, n, prefix="filler"):
        """n visits with unique content on unique pages: they make the corpus big enough that tokens are not all hub-like."""
        for i in range(n):
            self.visit(f"{prefix}{i}", url=f"site{i}.example.com/page/{i}", window=f"{prefix} {i}",
                       terms=[f"{prefix}word{chr(97 + i % 26)}{chr(97 + i // 26)}", f"unique{chr(97 + i % 26)}tokenx"],
                       idents=[])
        return self
