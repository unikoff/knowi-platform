"""Small catalog protocol implementation for courses with their own program.

A trusted backend/catalog.py may build TRACKS and then expose
``globals().update(Catalog(TRACKS).exports())``. No chapter naming is required.
"""

import hashlib
import json
from copy import deepcopy

from pymentor_core.modules.learning.progress_policy import is_countable_lesson
from pymentor_core.modules.practice.public_specs import public_task


class Catalog:
    def __init__(self, tracks):
        self.tracks = tracks
        self.lessons = [lesson for track in tracks for lesson in track["lessons"]]

    def exports(self):
        methods = (
            "default_track_id", "find_track", "find_lesson", "find_task",
            "find_lesson_by_task", "get_content_revision", "get_tracks_summary",
            "get_public_lessons", "get_legacy_lesson_id_map",
        )
        return {"TRACKS": self.tracks, "LESSONS": self.lessons,
                **{name: getattr(self, name) for name in methods}}

    def default_track_id(self):
        return self.tracks[0]["id"] if self.tracks else None

    def find_track(self, track_id):
        return next((track for track in self.tracks if track["id"] == track_id), None)

    def find_lesson(self, lesson_id):
        return next((lesson for lesson in self.lessons if lesson["id"] == lesson_id
                     or lesson.get("legacy_id") == lesson_id), None)

    def find_task(self, task_id):
        lesson = self.find_lesson_by_task(task_id)
        return next((task for task in lesson["tasks"] if task["id"] == task_id
                     or task_id in task.get("legacy_ids", [])), None) if lesson else None

    def find_lesson_by_task(self, task_id):
        return next((lesson for lesson in self.lessons for task in lesson["tasks"]
                     if task["id"] == task_id or task_id in task.get("legacy_ids", [])), None)

    def get_content_revision(self):
        payload = json.dumps(self.tracks, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def get_tracks_summary(self):
        return [{"id": track["id"], "title": track["title"], "tagline": track.get("tagline", ""),
                 "lessons_total": len(track["lessons"]),
                 "lessons_countable": sum(is_countable_lesson(lesson) for lesson in track["lessons"]),
                 "countable_lesson_ids": [lesson["id"] for lesson in track["lessons"] if is_countable_lesson(lesson)]}
                for track in self.tracks]

    def get_public_lessons(self, track_id):
        track = self.find_track(track_id)
        result = []
        for lesson in track["lessons"] if track else []:
            item = {key: deepcopy(value) for key, value in lesson.items()
                    if key not in {"theory_markdown", "legacy_id", "progress_policy", "private_manual_answers"}}
            item["tasks"] = [public_task(task) for task in lesson["tasks"]]
            for exercise in item.get("manual_practice", []):
                for task in exercise.get("tasks", []):
                    task.pop("answer", None)
            result.append(item)
        return result

    def get_legacy_lesson_id_map(self):
        return {lesson["legacy_id"]: lesson["id"] for lesson in self.lessons
                if lesson.get("legacy_id") and lesson["legacy_id"] != lesson["id"]}
