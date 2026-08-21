"""Extra yt-dlp extractors bundled with ytdl-stash.

yt-dlp ships single-video extractors for these sites but no channel/profile
*playlist* extractors, so watching a creator page fails with "Unsupported URL":

  - ``xvideos.com`` channel / profile / model pages (no playlist extractor at
    all upstream, including bare-slug URLs like ``/primalfetish``)
  - ``xhamster.com/pornstars/<name>`` pages (upstream ``XHamsterUserIE`` only
    covers ``/users/`` and ``/creators/``)

Each extractor enumerates videos using the site's own listing/pagination and
hands the individual video URLs back to the upstream single-video extractor, so
downloading, metadata and format selection stay entirely upstream's job.

Discovery: yt-dlp scans every ``sys.path`` entry for a ``yt_dlp_plugins``
namespace package (see ``yt_dlp/plugins.py``). The repo root is on ``sys.path``
because the app itself is imported as ``app.main``, so no extra configuration
is needed. Deliberately no ``__init__.py`` files — these are namespace packages.

IMPORTANT: plugin extractors are *prepended* to yt-dlp's extractor lookup
(``load_plugins`` → ``merge_dicts(regular_classes, ...)``), so the patterns here
must never match single-video URLs that upstream extractors handle.
"""

from __future__ import annotations

import itertools
import re

from yt_dlp.extractor.common import InfoExtractor
from yt_dlp.utils import (
    ExtractorError,
    clean_html,
    extract_attributes,
    int_or_none,
    parse_duration,
    traverse_obj,
    url_or_none,
)

__all__ = ['XHamsterPornstarIE', 'XVideosChannelIE']


# ---------------------------------------------------------------------------
# xvideos — channels / profiles / models (and bare-slug profile URLs)
# ---------------------------------------------------------------------------

# Path prefixes xvideos uses for creator pages. The listing endpoint actually
# routes on the slug alone (every prefix returns the same JSON), but we keep the
# list so prefixed URLs are recognised and so we can retry across forms.
_XV_KINDS = (
    'channels',
    'profiles',
    'models',
    'amateur-channels',
    'model-channels',
    'pornstar-channels',
)
_XV_KIND_RE = f'(?:{"|".join(_XV_KINDS)})'

# First path segments that are never a creator slug. Bare-slug URLs are only
# accepted when the segment is not one of these (and not a video URL).
_XV_RESERVED_RE = (
    r'(?:%s)' % '|'.join((
        *_XV_KINDS,
        r'video[s.]?\d*', 'embedframe', 'prof-video-click', 'favorite', 'favorites',
        'account', 'search', 'tag', 'tags', 'c', 'best', 'new', 'hits', 'top',
        'change-country', 'history', 'porn', 'lang', 'gay', 'shemale', 'trans',
        'straight', 'pornstars', 'verified', 'playlists', 'quickies', 'live',
        'red', 'about', 'terms', 'dmca', 'contact', 'upload', 'login', 'signup',
        'premium', 'subscriptions', 'notifications', 'settings', 'api', 'static',
        'js', 'css', 'img', 'sitemap', 'rss',
    ))
)


class XVideosChannelIE(InfoExtractor):
    IE_NAME = 'xvideos:channel'
    IE_DESC = 'xvideos.com channel/profile/model video listings'

    _VALID_URL = rf'''(?x)
        https?://(?:[^/?#]+\.)?xvideos2?\.(?:com|es)/
        (?!.*\#quickies)                     # never shadow XVideosQuickiesIE
        (?:{_XV_KIND_RE}/)?
        (?!video[.\d])                       # never shadow XVideosIE
        (?!{_XV_RESERVED_RE}(?:[/?\#]|$))    # not a site section / index page
        (?P<id>[^/?\#]+)
    '''

    _TESTS = [{
        'url': 'https://www.xvideos.com/primalfetish',
        'info_dict': {'id': 'primalfetish', 'title': 'Primal Fetish'},
        'playlist_mincount': 200,
    }, {
        'url': 'https://www.xvideos.com/channels/primalfetish',
        'only_matching': True,
    }, {
        'url': 'https://www.xvideos.com/profiles/primalfetish',
        'only_matching': True,
    }]

    _PAGE_SIZE = 36

    def _listing(self, kind, slug, page, fatal=False):
        """Fetch one page of the JSON video listing. Returns a dict or None."""
        data = self._download_json(
            f'https://www.xvideos.com/{kind}/{slug}/videos/new/{page}', slug,
            note=f'Downloading {kind} listing page {page + 1}',
            fatal=fatal, errnote=False)
        return data if isinstance(data, dict) else None

    def _resolve_kind(self, url, slug):
        """Find a listing path form that works for this slug."""
        mobj = re.search(rf'/({_XV_KIND_RE})/', url)
        candidates = [mobj.group(1)] if mobj else []
        candidates += [k for k in _XV_KINDS if k not in candidates]
        for kind in candidates:
            data = self._listing(kind, slug, 0)
            if data is not None and data.get('videos') is not None:
                return kind, data
        return None, None

    @staticmethod
    def _video_entry_url(video):
        """Build the canonical single-video URL that XVideosIE understands."""
        eid = video.get('eid')
        if eid:
            # /prof-video-click/upload/<user>/<eid>/<slug> → keep the trailing slug
            slug = ''
            path = video.get('u') or ''
            tail = path.rstrip('/').rsplit('/', 1)
            if len(tail) == 2 and tail[1] and tail[1] != eid:
                slug = '/' + tail[1]
            return f'https://www.xvideos.com/video.{eid}{slug}'
        vid = video.get('id')
        return f'https://www.xvideos.com/video{vid}' if vid else None

    def _entries(self, kind, slug, first_page):
        seen = set()
        total = int_or_none(first_page.get('nb_videos'))
        per_page = int_or_none(first_page.get('nb_per_page')) or self._PAGE_SIZE
        page_data = first_page
        for page in itertools.count(0):
            if page_data is None:
                page_data = self._listing(kind, slug, page)
            videos = (page_data or {}).get('videos') or []
            if not videos:
                return
            for video in videos:
                video_url = self._video_entry_url(video)
                if not video_url:
                    continue
                video_id = str(video.get('eid') or video.get('id'))
                if video_id in seen:
                    continue
                seen.add(video_id)
                yield self.url_result(
                    video_url, ie='XVideos', video_id=video_id,
                    title=clean_html(video.get('tf') or video.get('t')),
                    duration=parse_duration(video.get('d')),
                    thumbnail=url_or_none(video.get('il') or video.get('i')),
                )
            if total is not None and len(seen) >= total:
                return
            if len(videos) < per_page:
                return
            page_data = None

    def _real_extract(self, url):
        slug = self._match_id(url)
        kind, first_page = self._resolve_kind(url, slug)
        if first_page is None:
            raise ExtractorError(
                f'Could not load an xvideos video listing for {slug!r}. '
                'Check that the channel/profile URL is correct.', expected=True)

        videos = first_page.get('videos') or []
        name = None
        if videos:
            name = clean_html(videos[0].get('pn'))

        # The profile page carries the avatar (and the display name when the
        # creator has no videos yet). Non-fatal: listings still work without it.
        thumbnails = []
        webpage = self._download_webpage(
            f'https://www.xvideos.com/{kind}/{slug}', slug,
            note='Downloading profile page', fatal=False, errnote=False)
        if webpage:
            if not name:
                title = self._html_search_regex(
                    r'<title[^>]*>([^<]+)', webpage, 'title', default='')
                name = clean_html(re.split(r'\s+-\s+', title)[0]) or None
            avatar = self._search_regex(
                r'<div class="profile-pic">.{0,200}?<img[^>]+src="([^"]+)"',
                webpage, 'avatar', default=None, flags=re.S)
            if avatar and 'profile_default' not in avatar:
                thumbnails.append({'id': 'avatar', 'url': avatar})

        return self.playlist_result(
            self._entries(kind, slug, first_page),
            playlist_id=slug,
            playlist_title=name or slug,
            uploader=name or slug,
            thumbnails=thumbnails or None,
        )


# ---------------------------------------------------------------------------
# xhamster — /pornstars/<name>
# ---------------------------------------------------------------------------

# Keep in sync with upstream when possible, but never let an upstream rename
# break the whole plugin module (which would silently disable both extractors).
try:  # pragma: no cover - trivial fallback
    from yt_dlp.extractor.xhamster import XHamsterIE as _XHamsterIE

    _XH_DOMAINS = _XHamsterIE._DOMAINS
except Exception:  # noqa: BLE001
    _XH_DOMAINS = (
        r'(?:xhamster\.(?:com|one|desi)|xhms\.pro'
        r'|xhamster\d+\.(?:com|desi)|xhday\.com|xhvid\.com)'
    )

_XH_VIDEO_RE = rf'https?://(?:[^/?\#]+\.)?{_XH_DOMAINS}/videos/(?P<slug>[^/?\#]*?)-(?P<id>[\dA-Za-z]+)/?$'


class XHamsterPornstarIE(InfoExtractor):
    IE_NAME = 'xhamster:pornstar'
    IE_DESC = 'xHamster pornstar video listings'

    _VALID_URL = rf'https?://(?:[^/?\#]+\.)?{_XH_DOMAINS}/pornstars/(?P<id>[^/?\#&]+)'

    _TESTS = [{
        'url': 'https://xhamster.com/pornstars/polly-yangs',
        'info_dict': {'id': 'polly-yangs'},
        'playlist_mincount': 300,
    }, {
        'url': 'https://xhamster.com/pornstars/polly-yangs/2',
        'only_matching': True,
    }]

    def _entries(self, base, slug):
        seen = set()
        for page in itertools.count(1):
            page_url = f'{base}/pornstars/{slug}' + (f'/{page}' if page > 1 else '')
            webpage = self._download_webpage(
                page_url, slug, note=f'Downloading page {page}',
                fatal=False, errnote=False)
            if not webpage:
                # Past the last page xHamster answers 404 (with unrelated
                # "related videos" markup), so stop on the failed fetch.
                return
            new = 0
            for block in re.findall(
                    r'<a[^>]+\bvideo-thumb__image-container\b.*?</a>', webpage, re.S):
                a_tag = re.match(r'<a[^>]*>', block)
                if not a_tag:
                    continue
                attrs = extract_attributes(a_tag.group(0))
                video_url = url_or_none(attrs.get('href'))
                mobj = re.match(_XH_VIDEO_RE, video_url) if video_url else None
                if not mobj:
                    continue
                video_id = mobj.group('id')
                if video_id in seen:
                    continue
                seen.add(video_id)
                new += 1
                yield self.url_result(
                    video_url, ie='XHamster', video_id=video_id,
                    title=clean_html(attrs.get('aria-label')) or None,
                    thumbnail=url_or_none(self._search_regex(
                        r'<img[^>]+src="([^"]+)"', block, 'thumbnail', default=None)),
                )
            if not new:
                # Safety net in case a stale/duplicate page is served instead
                # of a 404 at the end of the listing.
                return

    def _real_extract(self, url):
        slug = self._match_id(url)
        base = self._search_regex(
            r'^(https?://[^/?\#]+)', url, 'base url', default='https://xhamster.com')

        webpage = self._download_webpage(
            f'{base}/pornstars/{slug}', slug,
            note='Downloading pornstar page', fatal=False, errnote=False)
        name, thumbnails = None, []
        if webpage:
            title = self._html_search_regex(
                r'<title[^>]*>([^<]+)', webpage, 'title', default='')
            name = clean_html(
                self._search_regex(
                    r'^(.*?)\s+(?:Porn Videos|Nude|Naked)', title, 'name',
                    default=re.split(r'\s+[|:]\s+', title)[0])) or None
            # Avatar lives in the page's bootstrap JSON. Supplying it here also
            # spares the app an expensive non-flat retry just to find a
            # thumbnail (see downloader.extract_channel_metadata).
            initials = self._search_json(
                r'window\.initials\s*=', webpage, 'initials', slug,
                fatal=False, default={})
            avatar = traverse_obj(
                initials,
                ('infoComponent', 'pornstarTop', 'thumbUrl'),
                ('infoComponent', 'displayUserModel', 'thumbURL'),
                expected_type=url_or_none)
            if avatar:
                thumbnails.append({'id': 'avatar', 'url': avatar})

        return self.playlist_result(
            self._entries(base, slug),
            playlist_id=slug,
            playlist_title=name or slug.replace('-', ' ').title(),
            uploader=name or slug.replace('-', ' ').title(),
            thumbnails=thumbnails or None,
        )
