"""Block 17 practice entries, merged without duplicate lesson keys."""

from ....practice_helpers import merge_lesson_maps

from . import practice_81
from . import practice_81_1
from . import practice_82
from . import practice_83
from . import practice_84
from . import practice_85

CODE_TASKS = merge_lesson_maps(
    practice_81.CODE_TASKS,
    practice_81_1.CODE_TASKS,
    practice_82.CODE_TASKS,
    practice_83.CODE_TASKS,
    practice_84.CODE_TASKS,
    practice_85.CODE_TASKS,
)
