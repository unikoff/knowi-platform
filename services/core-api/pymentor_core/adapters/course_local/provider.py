"""File-backed course provider with operation-scoped product selection."""

from copy import deepcopy

from pymentor_core.modules.practice.public_specs import public_task
from .runtime import CourseRuntime, current_course_id, default_runtime


class LocalCourseProvider:
    def __init__(self, runtime: CourseRuntime | None = None):
        self.runtime = runtime if runtime is not None else default_runtime()

    def _call(self, method, *args, course_id=None):
        snapshot = self.runtime.snapshot(current_course_id() if course_id is None else course_id)
        # Callers may decorate DTOs with progress/access; never mutate cached specs.
        return deepcopy(getattr(snapshot.catalog, method)(*args))

    def default_track_id(self, *, course_id=None):
        return self._call("default_track_id", course_id=course_id)

    def find_lesson(self, lesson_id, *, course_id=None):
        return self._call("find_lesson", lesson_id, course_id=course_id)

    def find_lesson_by_task(self, task_id, *, course_id=None):
        return self._call("find_lesson_by_task", task_id, course_id=course_id)

    def find_task(self, task_id, *, course_id=None):
        return self._call("find_task", task_id, course_id=course_id)

    def get_content_revision(self, *, course_id=None):
        return self._call("get_content_revision", course_id=course_id)

    def find_track(self, track_id, *, course_id=None):
        return self._call("find_track", track_id, course_id=course_id)

    def get_public_lessons(self, track_id, *, course_id=None):
        lessons = self._call("get_public_lessons", track_id, course_id=course_id)
        for lesson in lessons:
            for key in ("legacy_id", "theory_markdown", "progress_policy", "private_manual_answers"):
                lesson.pop(key, None)
            lesson["tasks"] = [public_task(task) for task in lesson.get("tasks", [])]
            for exercise in lesson.get("manual_practice", []):
                for task in exercise.get("tasks", []):
                    task.pop("answer", None)
        return lessons

    def get_tracks_summary(self, *, course_id=None):
        return self._call("get_tracks_summary", course_id=course_id)

    def get_products(self):
        return self.runtime.products()
