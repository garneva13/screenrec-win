"""Qt user interface for ScreenRec.

Nothing in here talks to ffmpeg or to Windows directly; the widgets drive the modules in
the package above (``config``, ``recorder``, ``audio``, ``winapi``) and report what those
modules hand back.
"""
