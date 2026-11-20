"""Operator permission for the current boot of a single-node installation."""
import os
from pathlib import Path
from stat import S_ISREG
from uuid import UUID

HOST_BOOT_ID = Path("/proc/sys/kernel/random/boot_id")
PERMIT_PATH = Path("/var/run/analytics-recovery/open")


def host_boot_permitted() -> bool:
    # Mount the parent directory read-only from the host's volatile /run. Mounting
    # a file with subPath would hide subsequent removal or atomic replacement.
    try:
        boot_id = HOST_BOOT_ID.read_text(encoding="ascii").strip()
        value = (str(UUID(boot_id)) + "\n").encode("ascii")
        fd = os.open(PERMIT_PATH, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            info = os.fstat(fd)
            return (
                S_ISREG(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022
                and info.st_size == len(value) and os.read(fd, len(value) + 1) == value
            )
        finally:
            os.close(fd)
    except (OSError, ValueError, AttributeError):
        return False
