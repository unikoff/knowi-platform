"""Compatibility facade; canonical authoring is in courses/backend."""
from pymentor_core.adapters.course_local.legacy_exports import export_legacy as _export
_export(globals(), 'chapter_05.block_00.manual')
del _export
