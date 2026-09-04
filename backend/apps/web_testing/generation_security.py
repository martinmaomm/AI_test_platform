"""Shared URL-pattern compatibility for legacy normalizers.

Generation entry URLs are now validated by ``target_urls``.  Login material is
kept verbatim in the user description and is never parsed or cached here.
"""

from __future__ import annotations

import re


URL_RE = re.compile(r'https?://[^\s,，;；。]+', re.IGNORECASE)
