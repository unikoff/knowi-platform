"""Compatibility facade; canonical authoring is in courses/backend."""
from pymentor_core.adapters.course_local.legacy_exports import export_legacy as _export
_export(globals(), 'chapter_02.block_05')
del _export
