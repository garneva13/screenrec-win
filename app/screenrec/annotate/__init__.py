"""Drawing over the screen while recording.

The drawings are an ordinary transparent window, so the Desktop Duplication API picks
them up with the rest of the desktop and they are in the video at the moment they appear.
The toolbar is a window too, and would be recorded as well - so it is marked
``WDA_EXCLUDEFROMCAPTURE``, which keeps it on screen and out of the capture. Both halves
of that were measured against the real capture path before any of this was written; see
docs/ANNOTATE_CONTRACT.md.

Nothing here touches the encoding pipeline.
"""
