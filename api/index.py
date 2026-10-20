"""Vercel Python ASGI entry (deployment.md §4 / #110).

Expose ``create_app()`` — do not import ``ak5.server.app:app`` if you need a
fresh factory; lifespan still owns DB migrate/seed (set ``AK5_AUTO_MIGRATE=0``
and run ``ak5 db upgrade`` out-of-band for serverless).
"""

from __future__ import annotations

from ak5.server.main import create_app

app = create_app()
