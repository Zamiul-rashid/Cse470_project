"""Upload intake and preview generation (FR 1.1, FR 1.4).

Three jobs the router must not do inline:

* **Distrust the client.** ``Content-Type`` is whatever the uploader's browser
  felt like sending, so the first bytes are sniffed as well and anything that
  is not a PDF, PNG or JPEG is a 415. An unvalidated upload is a hole.
* **Read the file once.** The stream is written to disk in 1 MiB chunks and
  SHA-256'd in the same pass, so a 25 MB upload is never held in memory and
  the hash that drives duplicate detection costs nothing extra.
* **Never let a preview failure lose an upload.** Previews are best effort. An
  encrypted or malformed PDF still becomes a listing; it just has no preview.

Paths are stored relative to the repo root and never reach the client. Files
leave the server only through an authorizing endpoint -- ``storage/`` is never
mounted static, or "preview the first three pages" quietly becomes "download
the whole thing".
"""

from __future__ import annotations

import hashlib
import logging
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from fastapi import HTTPException, UploadFile, status
from pypdf import PdfReader, PdfWriter

from app.core.config import settings
from app.core.util import new_id

logger = logging.getLogger(__name__)

PDF_MIME: Final[str] = "application/pdf"
PNG_MIME: Final[str] = "image/png"
JPEG_MIME: Final[str] = "image/jpeg"

#: The whole accepted vocabulary. Scans and photos of notes are the reason the
#: two image types are here at all.
ALLOWED_MIME: Final[frozenset[str]] = frozenset({PDF_MIME, PNG_MIME, JPEG_MIME})

#: Signature -> type, checked against the leading bytes of the stream. This,
#: not the declared header, is what decides whether an upload is accepted.
_MAGIC: Final[tuple[tuple[bytes, str], ...]] = (
    (b"%PDF-", PDF_MIME),
    (b"\x89PNG\r\n\x1a\n", PNG_MIME),
    (b"\xff\xd8\xff", JPEG_MIME),
)

#: The stored extension is derived from what was sniffed, never from
#: ``upload.filename`` -- "notes.pdf.exe" must not survive the round trip.
_SUFFIX: Final[dict[str, str]] = {
    PDF_MIME: ".pdf",
    PNG_MIME: ".png",
    JPEG_MIME: ".jpg",
}

_IMAGE_MIME: Final[frozenset[str]] = frozenset({PNG_MIME, JPEG_MIME})
_CHUNK_BYTES: Final[int] = 1024 * 1024

_UNSUPPORTED = (
    "Upload a PDF, PNG or JPEG. That file is none of those, whatever its name says."
)


@dataclass(frozen=True, slots=True)
class SavedFile:
    """Everything the upload handler needs to write a ``study_materials`` row.

    ``page_count`` and ``preview_path`` are optional because both come from
    best-effort inspection: a listing whose preview could not be generated is
    still a perfectly good listing.
    """

    #: Relative to the repo root, e.g. ``storage/materials/<uuid>.pdf``.
    file_path: str
    file_hash: str
    page_count: int | None
    preview_path: str | None
    content_type: str


# ---------------------------------------------------------------------------
# path handling
# ---------------------------------------------------------------------------


def _relative(path: Path) -> str:
    """Store repo-relative POSIX paths so a moved checkout keeps working."""
    return path.resolve().relative_to(settings.repo_root.resolve()).as_posix()


def resolve(relative_path: str) -> Path:
    """Turn a stored path back into an absolute one, refusing to escape.

    Every byte we serve is addressed by a value read out of the database, but
    a path column is still a path: ``../../.env`` in one badly written admin
    tool would be enough. Confining the result to ``storage/`` costs one
    comparison and closes the whole class of bug.
    """
    candidate = (settings.repo_root / relative_path).resolve()
    if not candidate.is_relative_to(settings.storage_dir.resolve()):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="That file path is not valid.",
        )
    return candidate


def delete_stored_file(relative_path: str | None) -> bool:
    """Best-effort removal; returns whether a file actually went away.

    Called from delete/replace paths where the database row is the thing that
    matters -- an orphaned blob on disk is untidy, a failed delete that rolls
    back the transaction is a bug.
    """
    if not relative_path:
        return False
    try:
        target = resolve(relative_path)
    except HTTPException:
        logger.warning("refusing to delete out-of-storage path %r", relative_path)
        return False
    try:
        target.unlink()
    except FileNotFoundError:
        return False
    except OSError:
        logger.warning("could not delete %s", relative_path, exc_info=True)
        return False
    return True


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------


def detect_content_type(head: bytes) -> str | None:
    """Sniff the leading bytes; ``None`` means "not something we accept"."""
    for signature, mime in _MAGIC:
        if head.startswith(signature):
            return mime
    return None


# ---------------------------------------------------------------------------
# saving
# ---------------------------------------------------------------------------


async def save_upload(upload: UploadFile) -> SavedFile:
    """Stream an upload to ``storage/materials/``, hashing as it goes.

    Raises 415 for a type we do not accept and 413 once the stream passes
    ``settings.max_upload_bytes`` -- mid-stream, so an oversized file is never
    written out in full just to be measured.
    """
    settings.ensure_storage_dirs()

    head = await upload.read(_CHUNK_BYTES)
    content_type = detect_content_type(head)
    if content_type is None:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail=_UNSUPPORTED
        )

    # The declared type only gets a vote when it contradicts the bytes; a
    # generic "application/octet-stream" from some upload widget is fine, but
    # a file claiming to be a PNG while starting with %PDF- is not.
    declared = (upload.content_type or "").split(";", 1)[0].strip().lower()
    if declared in ALLOWED_MIME and declared != content_type:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"This file is really {content_type}, not the {declared} it claims to be.",
        )

    target = settings.materials_dir / f"{new_id()}{_SUFFIX[content_type]}"
    digest = hashlib.sha256()
    written = 0
    chunk = head
    try:
        with target.open("wb") as handle:
            while chunk:
                written += len(chunk)
                if written > settings.max_upload_bytes:
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail=(
                            f"That file is larger than the {settings.max_upload_mb} MB "
                            "limit. Compress it or split it up."
                        ),
                    )
                digest.update(chunk)
                handle.write(chunk)
                chunk = await upload.read(_CHUNK_BYTES)
    except BaseException:
        # A half-written blob is worse than none: no row will reference it, so
        # nothing will ever clean it up.
        target.unlink(missing_ok=True)
        raise

    return SavedFile(
        file_path=_relative(target),
        file_hash=digest.hexdigest(),
        page_count=_page_count(target, content_type),
        preview_path=generate_preview(target, content_type),
        content_type=content_type,
    )


def _page_count(source: Path, content_type: str) -> int | None:
    """Page count for the listing card; ``None`` if the PDF will not open."""
    if content_type in _IMAGE_MIME:
        return 1
    try:
        return len(PdfReader(str(source)).pages)
    except Exception:
        logger.warning("could not read page count from %s", source.name, exc_info=True)
        return None


def generate_preview(source: Path, content_type: str) -> str | None:
    """Write the teaser copy of an upload and return its relative path.

    The point of slicing server-side is that the rest of the document never
    reaches the browser: hiding pages in the viewer leaves the full file in
    the network tab, and "preview before you buy" stops meaning anything.

    Never raises. A missing preview degrades one listing; an exception here
    would fail an upload that has already been written to disk.
    """
    target = settings.previews_dir / f"{new_id()}{_SUFFIX.get(content_type, '.bin')}"
    try:
        settings.ensure_storage_dirs()
        if content_type in _IMAGE_MIME:
            # A scan is a single page, so it is already its own preview.
            shutil.copyfile(source, target)
            return _relative(target)

        reader = PdfReader(str(source))
        writer = PdfWriter()
        # A document shorter than preview_pages ends up fully contained in its
        # own preview. That is the specified behaviour, and it is why the
        # full-file endpoint is separately authorized.
        for page in reader.pages[: settings.preview_pages]:
            writer.add_page(page)
        if not writer.pages:
            logger.warning("no pages to preview in %s", source.name)
            return None
        with target.open("wb") as handle:
            writer.write(handle)
        return _relative(target)
    except Exception:
        logger.warning("preview generation failed for %s", source.name, exc_info=True)
        target.unlink(missing_ok=True)
        return None


__all__ = [
    "ALLOWED_MIME",
    "JPEG_MIME",
    "PDF_MIME",
    "PNG_MIME",
    "SavedFile",
    "delete_stored_file",
    "detect_content_type",
    "generate_preview",
    "resolve",
    "save_upload",
]
