"""Exact-dedup keys: canonical URLs and content hashes."""
import hashlib
import html
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from django.utils.html import strip_tags

# Query parameters that only track the click and never change the page
TRACKING_PARAMS = {'fbclid', 'gclid', 'mc_cid', 'mc_eid', 'ref', 'ref_src', 'cmpid', 'guccounter', 'ncid'}


def canonical_url(url: str) -> str:
    """Lower-case scheme and host, no fragment, no tracking params, no default port, no trailing slash."""
    parts = urlsplit(url.strip())
    host = (parts.hostname or '').lower()
    if parts.port and (parts.scheme, parts.port) not in (('http', 80), ('https', 443)):
        host = f'{host}:{parts.port}'
    query = [
        (key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key.lower() not in TRACKING_PARAMS and not key.lower().startswith('utm_')
    ]
    path = parts.path.rstrip('/') or '/'
    return urlunsplit((parts.scheme.lower(), host, path, urlencode(query), ''))


def html_to_text(value: str) -> str:
    return normalize_space(html.unescape(strip_tags(value or '')))


def normalize_space(value: str) -> str:
    return re.sub(r'\s+', ' ', value).strip()


def normalize_paragraphs(value: str) -> str:
    """Spaces collapsed inside each paragraph, one newline between paragraphs, no empty ones."""
    return '\n'.join(line for line in (normalize_space(p) for p in value.splitlines()) if line)


def content_hash(title: str, body: str) -> str:
    """Same text under a different URL (syndication, AMP, reposts) gets the same hash."""
    normalized = normalize_space(f'{title}\n{body}').lower()
    return hashlib.sha256(normalized.encode()).hexdigest()
