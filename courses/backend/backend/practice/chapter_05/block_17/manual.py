"""Block 17 practice entries, merged without duplicate lesson keys."""

from ....practice_helpers import merge_lesson_maps

from . import practice_81
from . import practice_81_1
from . import practice_82
from . import practice_83
from . import practice_84
from . import practice_85

MANUAL_PRACTICE = merge_lesson_maps(
    practice_81.MANUAL_PRACTICE,
    practice_81_1.MANUAL_PRACTICE,
    practice_82.MANUAL_PRACTICE,
    practice_83.MANUAL_PRACTICE,
    practice_84.MANUAL_PRACTICE,
    practice_85.MANUAL_PRACTICE,
)
