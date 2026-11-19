"""Bounded AES-256-GCM streams; plaintext is never returned before authentication."""
import hashlib
import json
import os
from pathlib import Path
import struct
from uuid import uuid4

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

MAGIC = b"KNWBK01\n"
MAX_HEADER = 64 * 1024
MAX_BYTES = 512 * 1024 * 1024
CHUNK = 1024 * 1024
TAG_BYTES = 16


def _key(key):
    if not isinstance(key, bytes) or len(key) != 32:
        raise ValueError("AES-256 key must be exactly 32 bytes")


def _exclusive(path):
    return os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb")


def file_digest(path):
    digest, size = hashlib.sha256(), 0
    with path.open("rb") as source:
        while block := source.read(CHUNK):
            size += len(block)
            if size > MAX_BYTES:
                raise ValueError("backup exceeds limit")
            digest.update(block)
    return digest.hexdigest(), size


def encrypt_file(source: Path, target: Path, key: bytes, metadata: dict):
    """Create an encrypted artifact exclusively; source is a private dump file."""
    _key(key)
    sha, size = file_digest(source)
    nonce = os.urandom(12)
    header = json.dumps({"version": 1, "nonce": nonce.hex(), "dump_sha256": sha,
                         "dump_bytes": size, "metadata": metadata},
                        sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    if len(header) > MAX_HEADER:
        raise ValueError("backup metadata exceeds limit")
    prefix = MAGIC + struct.pack("!I", len(header)) + header
    temporary = target.with_name("." + target.name + "." + uuid4().hex + ".pending")
    digest = hashlib.sha256()
    try:
        with source.open("rb") as plain, _exclusive(temporary) as output:
            cipher = Cipher(algorithms.AES(key), modes.GCM(nonce)).encryptor()
            cipher.authenticate_additional_data(prefix)
            output.write(prefix)
            total = 0
            while block := plain.read(CHUNK):
                total += len(block)
                if total > MAX_BYTES:
                    raise ValueError("backup exceeds limit")
                digest.update(block)
                output.write(cipher.update(block))
            if total != size or digest.hexdigest() != sha:
                raise ValueError("dump changed during encryption")
            output.write(cipher.finalize())
            output.write(cipher.tag)
            output.flush()
            os.fsync(output.fileno())
        os.link(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return {"dump_sha256": sha, "dump_bytes": size}


def decrypt_verified(artifact: Path, private_dir: Path, key: bytes):
    """Return (header, private temporary dump) only after GCM tag and SHA verify."""
    _key(key)
    stage = private_dir / (".verified-dump-" + uuid4().hex + ".tmp")
    try:
        with artifact.open("rb") as source:
            prefix = source.read(len(MAGIC) + 4)
            if len(prefix) != len(MAGIC) + 4 or not prefix.startswith(MAGIC):
                raise ValueError("invalid backup header")
            size = struct.unpack("!I", prefix[-4:])[0]
            if not 1 <= size <= MAX_HEADER:
                raise ValueError("invalid backup header")
            encoded = source.read(size)
            if len(encoded) != size:
                raise ValueError("truncated backup header")
            header = json.loads(encoded)
            if (set(header) != {"version", "nonce", "dump_sha256", "dump_bytes", "metadata"}
                    or header["version"] != 1 or type(header["dump_bytes"]) is not int
                    or not 0 <= header["dump_bytes"] <= MAX_BYTES
                    or not isinstance(header["metadata"], dict)):
                raise ValueError("invalid backup header")
            nonce = bytes.fromhex(header["nonce"])
            if len(nonce) != 12 or len(header["dump_sha256"]) != 64:
                raise ValueError("invalid backup header")
            cipher_len = artifact.stat().st_size - len(prefix) - size - TAG_BYTES
            if cipher_len != header["dump_bytes"]:
                raise ValueError("invalid backup length")
            decryptor = Cipher(algorithms.AES(key), modes.GCM(nonce)).decryptor()
            decryptor.authenticate_additional_data(prefix + encoded)
            digest = hashlib.sha256()
            with _exclusive(stage) as output:
                remaining = cipher_len
                while remaining:
                    block = source.read(min(CHUNK, remaining))
                    if not block:
                        raise ValueError("truncated backup")
                    remaining -= len(block)
                    clear = decryptor.update(block)
                    digest.update(clear)
                    output.write(clear)
                tag = source.read(TAG_BYTES)
                if len(tag) != TAG_BYTES or source.read(1):
                    raise ValueError("invalid backup tag")
                final = decryptor.finalize_with_tag(tag)
                digest.update(final)
                output.write(final)
                output.flush()
                os.fsync(output.fileno())
            if digest.hexdigest() != header["dump_sha256"]:
                raise ValueError("backup digest mismatch")
        return header, stage
    except BaseException:
        stage.unlink(missing_ok=True)
        raise
