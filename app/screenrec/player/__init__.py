"""Playing a multi-screen recording back as one thing.

A `separate` take is several files written by `screenrec.multi` plus a sidecar that says
where each screen sat on the desktop. On their own they are just files; what makes them a
recording again is a single clock and a window per screen placed the way the screens were
arranged, so the viewer moves their eyes the way the person recording did.

`engine` is that clock and the sidecar's reader. It owns no widgets and no windows, so it
can be exercised head-less.
"""
from __future__ import annotations

__all__ = ["engine"]
