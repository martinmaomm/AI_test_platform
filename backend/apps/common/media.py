"""Restricted development-only serving for public files under ``MEDIA_ROOT``."""
from __future__ import annotations

import os
import posixpath
from urllib.parse import unquote

from django.conf import settings
from django.http import Http404
from django.views.static import serve as django_static_serve


_PRIVATE_MEDIA_DIRECTORIES = ("webui_failure_screenshots",)


def _normalized_url_path(path: str) -> str:
    value = str(path or "")
    # WSGI servers normally decode once before URL resolving. Decode boundedly
    # again so encoded and double-encoded aliases reach the same guard.
    for _ in range(4):
        decoded = unquote(value)
        if decoded == value:
            break
        value = decoded
    value = value.replace("\\", "/")
    if not value or "\x00" in value or value.startswith("/"):
        raise Http404("媒体文件不存在")
    normalized = posixpath.normpath(value)
    if normalized in {"", ".", ".."} or normalized.startswith("../"):
        raise Http404("媒体文件不存在")
    return normalized


def _within_casefold(candidate: str, root: str) -> bool:
    """Filesystem containment that also protects case-insensitive deployments."""
    candidate_folded = os.path.normpath(candidate).casefold()
    root_folded = os.path.normpath(root).casefold()
    try:
        return os.path.commonpath([candidate_folded, root_folded]) == root_folded
    except ValueError:
        return False


def _public_media_file(path: str) -> tuple[str, str]:
    normalized = _normalized_url_path(path)
    first_segment = normalized.split("/", 1)[0].casefold()
    if first_segment in {item.casefold() for item in _PRIVATE_MEDIA_DIRECTORIES}:
        raise Http404("媒体文件不存在")

    media_root = os.path.realpath(str(settings.MEDIA_ROOT))
    candidate = os.path.realpath(os.path.join(media_root, *normalized.split("/")))
    if not _within_casefold(candidate, media_root):
        raise Http404("媒体文件不存在")
    for private_name in _PRIVATE_MEDIA_DIRECTORIES:
        private_root = os.path.realpath(os.path.join(media_root, private_name))
        if _within_casefold(candidate, private_root):
            raise Http404("媒体文件不存在")
    if not os.path.isfile(candidate):
        # This also refuses directory indexes, including MEDIA_ROOT itself.
        raise Http404("媒体文件不存在")
    canonical_relative = os.path.relpath(candidate, media_root).replace(os.sep, "/")
    return media_root, canonical_relative


def public_media_serve(request, path, **_kwargs):
    """Serve non-private development media without exposing execution screenshots."""
    media_root, relative_path = _public_media_file(path)
    return django_static_serve(
        request, relative_path, document_root=media_root, show_indexes=False,
    )

