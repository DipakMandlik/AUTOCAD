"""Static dashboard frontend, served by the REST API (apps/api) via StaticFiles.

Vanilla HTML/CSS/JS on purpose: this is a thin client over apps/api/main.py
(chat -> process_command, a tool explorer, a client-side drawing preview,
and a validation panel). No build step, no framework, so it stays legible
alongside the rest of the platform and has no dependency of its own.
"""
