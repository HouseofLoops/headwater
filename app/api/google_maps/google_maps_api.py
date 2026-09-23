"""Backwards-compatible import path for ``google_maps_router``.

The endpoints that used to fill this module now live in ``search``, ``places``,
``jobs``, ``monitors``, ``analytics``, ``geo`` and ``health``, composed by this
package's ``__init__``. This module stays because ``main.py`` imports
``app.api.google_maps.google_maps_api``; new code should import
``app.api.google_maps`` instead.

Importing this module runs the package ``__init__`` first, which builds the
router, so the name below is already bound by the time it is read -- there is
no import cycle.
"""

from app.api.google_maps import google_maps_router

__all__ = ["google_maps_router"]
