"""Fixed renditions use the existing signed mode parameter. Responsive candidates reuse the same mode as previews and full-size viewers, so
identical pixels have one browser/CDN address. Port of media/renditions.ts."""

from __future__ import annotations

IMAGE_WIDTHS = {"avatar": 96, "card": 336, "thumb": 720, "full": 1600, "og": 1200, "avatar-48": 48, "image-1200": 1200}
RESPONSIVE_MODES = {
    "avatar": ["avatar-48", "avatar"],
    "card": ["card", "thumb"],
    "body": ["thumb", "image-1200", "full"],
    "hero": ["thumb", "image-1200", "full"],
}
# Renditions the pages also offer as AVIF (AVIF_MODE_SUFFIX): the responsive sets, not avatars.
AVIF_MODES = list(dict.fromkeys([*RESPONSIVE_MODES["card"], *RESPONSIVE_MODES["body"], *RESPONSIVE_MODES["hero"]]))
AVIF_MODE_SUFFIX = "-avif"
