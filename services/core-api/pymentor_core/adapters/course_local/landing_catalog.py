"""Trusted course registry metadata for landing adapters."""

def course_landing_slugs(*, include_disabled: bool = False) -> dict[str, str]:
    """Return enabled course landing routes from the trusted course registry."""
    from pymentor_core.adapters.course_local.runtime import default_runtime
    from pymentor_core.adapters.course_local.packages import load_course_manifest

    if include_disabled:
        registry = default_runtime().registry
        return {entry.course_id: load_course_manifest(registry.root / entry.course_id).slug
                for entry in registry.entries.values()}
    return {
        product["course_id"]: product["slug"]
        for product in default_runtime().products()
    }

