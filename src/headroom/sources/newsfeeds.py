"""Press releases and regulatory announcements: MFN, with Cision as fallback.

MFN (mfn.se) is the main newswire for Swedish issuers. Its company search
returns each entity's ISINs, LEIs and organisation number, so a feed is linked
to a company on identifiers rather than names. Company pages and release pages
are plain HTML. MFN's robots.txt disallows .json/.rss/.xml/.atom paths, so
those feeds are not used.

Cision (news.cision.com) is the fallback for issuers that publish there. A
Cision page is accepted only when its title matches the legal name.
"""

from __future__ import annotations

import html as htmllib
import json
import re
import time
import unicodedata
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from urllib.parse import unquote, unquote_plus

from headroom.http import Fetcher

MFN = "https://mfn.se"
CISION = "https://news.cision.com"


@dataclass
class Feed:
    org_nr: str
    provider: str  # mfn | cision
    slug: str
    url: str
    name: str
    tickers: list[str] = field(default_factory=list)
    isins: list[str] = field(default_factory=list)
    matched_on: str = ""  # org_nr | lei | name


@dataclass
class Release:
    org_nr: str
    provider: str
    url: str
    title: str
    published: datetime | None
    attachments: list[str]
    body: str = ""

    def to_row(self) -> dict:
        d = asdict(self)
        d["attachments"] = json.dumps(self.attachments)
        return d


def _plain(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"\(publ\)|\bpubl\b|\baktiebolag(et)?\b|\bab\b", " ", s)
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def _text(fragment: str) -> str:
    return re.sub(r"\s+", " ", htmllib.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


# --------------------------------------------------------------------------- MFN


GENERIC = {
    "fastighets",
    "fastigheter",
    "fastighet",
    "holding",
    "i",
    "norden",
    "group",
    "property",
    "properties",
    "sverige",
    "svenska",
    "real",
    "estate",
    "invest",
    "the",
    "och",
    "and",
}


def _queries(name: str) -> list[str]:
    """MFN searches brand names, so try the legal name and then its distinctive words."""
    words = _plain(name).split()
    distinctive = [w for w in words if w not in GENERIC and len(w) > 2]
    qs = [re.sub(r"\s*\(publ\)\s*", " ", name, flags=re.I).strip(), " ".join(words)]
    if distinctive:
        qs.append(distinctive[0])
        qs.append(max(distinctive, key=len))
    return list(dict.fromkeys(q for q in qs if q))[:4]


def mfn_find(f: Fetcher, org_nr: str, lei: str | None, name: str) -> Feed | None:
    """Search MFN; accept a hit on org number or LEI, or on an exact name match
    for unlisted issuers whose MFN entry carries no identifiers."""
    for q in _queries(name):
        hits = f.get(
            f"{MFN}/search/companies", params={"limit": "10", "query": q}, ttl=timedelta(days=7)
        ).json()
        for h in hits:
            refs = [r.replace("-", "") for r in h.get("local_refs") or []]
            if f"SE:{org_nr.replace('-', '')}" in refs:
                how = "org_nr"
            elif lei and lei in (h.get("leis") or []):
                how = "lei"
            elif not refs and not h.get("leis") and _plain(h["name"]) == _plain(name):
                how = "name"
            else:
                continue
            return Feed(
                org_nr,
                "mfn",
                h["slug"],
                f"{MFN}/all/a/{h['slug']}",
                h["name"],
                h.get("tickers") or [],
                h.get("isins") or [],
                how,
            )
    return None


# Items are /a/<slug>/<item>, or /cis/a/... for Cision releases mirrored on MFN.
_ITEM = re.compile(r"goToNewsItem\(event, '(/(?:[a-z]+/)?a/[^']+)'\)")
_PDF = re.compile(r'href="(https://(?:storage\.mfn\.se|mb\.cision\.com)/[^"]+\.pdf)"')


def mfn_releases(f: Fetcher, feed: Feed, max_items: int = 150) -> list[Release]:
    """Release list from the company page; titles and attachments come with it.

    The page takes a limit parameter. 150 items reaches back past a year even for
    issuers that publish weekly buy-back notices, so the last reports are included.
    """
    url = f"{feed.url}?limit={max_items}"
    page = f.get(url, ttl=timedelta(hours=6)).text
    if "goToNewsItem" not in page:  # throttled or challenge page: fetch again, uncached
        time.sleep(5)
        page = f.get(url, ttl=timedelta(seconds=0)).text
    out: list[Release] = []
    # Each item is a block starting at its onclick handler; split on that.
    parts = _ITEM.split(page)
    for i in range(1, len(parts), 2):
        path, block = parts[i], parts[i + 1]
        title_m = re.search(r'title="([^"]+)"', block)
        date_m = re.search(r"(\d{4}-\d{2}-\d{2})(?:[ T](\d{2}:\d{2}))?", block)
        atts = _PDF.findall(block)
        published = None
        if date_m:
            published = datetime.fromisoformat(
                date_m.group(1) + ("T" + date_m.group(2) if date_m.group(2) else "")
            )
        out.append(
            Release(
                feed.org_nr,
                "mfn",
                f"{MFN}{path}",
                htmllib.unescape(title_m.group(1)) if title_m else path.rsplit("/", 1)[-1],
                published,
                list(dict.fromkeys(atts)),
            )
        )
        if len(out) >= max_items:
            break
    return out


def mfn_release_detail(f: Fetcher, rel: Release) -> Release:
    """Fills in publication time, body text and attachments from the release page."""
    page = f.get(rel.url, ttl=None).text  # releases do not change once published
    ld = re.search(r'application/ld\+json">(\{.*?\})</script>', page, re.S)
    if ld:
        try:
            meta = json.loads(ld.group(1))
            rel.published = datetime.fromisoformat(meta["datePublished"].replace("Z", "+00:00"))
            rel.title = meta.get("headline") or rel.title
        except (ValueError, KeyError):
            pass
    body = re.search(r'<div class="mfn-body">(.*?)</div>\s*</div>', page, re.S)
    rel.body = _text(body.group(1))[:20_000] if body else ""
    atts = _PDF.findall(page)
    rel.attachments = list(dict.fromkeys([*rel.attachments, *atts]))
    return rel


# --------------------------------------------------------------------------- Cision


def _cision_slugs(name: str) -> list[str]:
    """Candidate newsroom slugs. Many issuers have an old archive page under the
    legal name and an active one under the brand name, so both are tried."""
    base = _plain(name)
    full = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    full = re.sub(r"[^a-z0-9]+", "-", re.sub(r"\(publ\)", "", full)).strip("-")
    words = base.split()
    brand = [w for w in words if w not in GENERIC]
    slugs = [full, base.replace(" ", "-"), f"{base.replace(' ', '-')}-ab"]
    if brand and brand != words:
        slugs.append("-".join(brand))
    return list(dict.fromkeys(s for s in slugs if s))


def _name_match(title: str, name: str) -> bool:
    """Exact match on the plain name, or on the brand once generic words are removed."""
    t, n = _plain(title), _plain(name)
    if t == n:
        return True
    strip = lambda s: " ".join(w for w in s.split() if w not in GENERIC)  # noqa: E731
    return bool(strip(t)) and strip(t) == strip(n)


def _latest(page: str) -> str:
    return max(re.findall(r'datetime="([^"]+)"', page), default="")


def cision_find(f: Fetcher, org_nr: str, name: str) -> Feed | None:
    """Every candidate page whose title matches; the most recently active one wins."""
    found: list[tuple[str, Feed]] = []
    for slug in _cision_slugs(name):
        url = f"{CISION}/se/{slug}"
        try:
            page = f.get(url, ttl=timedelta(days=7)).text
        except Exception:  # 404 for wrong guesses
            continue
        t = re.search(r"<title>(.*?)</title>", page, re.S)
        if t and _name_match(htmllib.unescape(t.group(1)), name):
            title = htmllib.unescape(t.group(1)).strip()
            found.append(
                (_latest(page), Feed(org_nr, "cision", slug, url, title, matched_on="name"))
            )
    return max(found, key=lambda x: x[0])[1] if found else None


def _cision_id(url: str) -> int:
    m = re.search(r",c(\d+)$", url)
    return int(m.group(1)) if m else 0


def cision_releases(f: Fetcher, feed: Feed, max_items: int = 60) -> list[Release]:
    """Release links, newest first by Cision's sequential id. Titles come from the
    share links on the page, which carry the original headline with diacritics."""
    page = f.get(feed.url, ttl=timedelta(hours=6)).text
    base = f"{CISION}/se/{feed.slug}/r/"
    links = set(re.findall(rf'href="({re.escape(base)}[^"]+)"', page))
    titles: dict[str, str] = {}
    for enc, t in re.findall(
        r"shareArticle\?mini=true&(?:amp;)?url=([^&\"]+)&(?:amp;)?title=([^&\"]+)", page
    ):
        u = unquote(enc)
        if u.startswith(base):
            links.add(u)
            titles[u] = unquote_plus(t)
    out = []
    for url in sorted(links, key=_cision_id, reverse=True)[:max_items]:
        title = titles.get(url) or url.rsplit("/r/", 1)[-1].split(",")[0].replace("-", " ")
        out.append(Release(feed.org_nr, "cision", url, htmllib.unescape(title), None, []))
    return out


def cision_release_detail(f: Fetcher, rel: Release) -> Release:
    page = f.get(rel.url, ttl=None).text
    t = re.search(r'<meta property="og:title" content="([^"]+)"', page)
    if t:
        rel.title = htmllib.unescape(t.group(1))
    d = re.search(r'"datePublished"\s*:\s*"([^"]+)"', page) or re.search(
        r'<time[^>]+datetime="([^"]+)"', page
    )
    if d:
        try:
            rel.published = datetime.fromisoformat(d.group(1).replace("Z", "+00:00"))
        except ValueError:
            pass
    rel.attachments = list(
        dict.fromkeys(re.findall(r'href="(https://mb\.cision\.com/[^"]+\.pdf)"', page))
    )
    body = re.search(r'<div class="release-body[^"]*">(.*?)</div>', page, re.S)
    rel.body = _text(body.group(1))[:20_000] if body else ""
    return rel


# --------------------------------------------------------------------------- facade


def find_feed(f: Fetcher, org_nr: str, lei: str | None, name: str) -> Feed | None:
    return mfn_find(f, org_nr, lei, name) or cision_find(f, org_nr, name)


def releases(f: Fetcher, feed: Feed, max_items: int = 150) -> list[Release]:
    return (mfn_releases if feed.provider == "mfn" else cision_releases)(f, feed, max_items)


def release_detail(f: Fetcher, rel: Release) -> Release:
    return (mfn_release_detail if rel.provider == "mfn" else cision_release_detail)(f, rel)


def yahoo_ticker(tickers: list[str]) -> str | None:
    """Pick the main common share on a Swedish venue and convert to Yahoo format.

    'XSTO:SAGA B' -> 'SAGA-B.ST'. Prefers class B, then A, then a single class.
    Preference and class D shares are skipped (they trade on yield, not NAV).
    """
    swedish = [
        t.split(":", 1)[1]
        for t in tickers
        if t.split(":", 1)[0] in ("XSTO", "SSME", "XNGM", "NSME", "XSAT")
    ]
    common = [t for t in swedish if not re.search(r"\b(PREF|PRF|D|TO\d*|BTA|TR)\b", t)]
    if not common:
        return None
    for suffix in (" B", " A"):
        for t in common:
            if t.endswith(suffix):
                return t.replace(" ", "-") + ".ST"
    return sorted(common, key=len)[0].replace(" ", "-") + ".ST"


_NOT_REPORT = re.compile(
    r"invitation|inbjudan|presentation of|presentation av|webcast|webbsandning|"
    r"telefonkonferens|conference call|kallelse|notice|datum for|date for|"
    r"publiceringsdatum|financial calendar|finansiell kalender|rattelse av datum|changed date|"
    r"andrat datum|will publish|kommer att publicera|exklusive|ex dividend"
)
_PERIOD = re.compile(
    r"interim report|delarsrapport|year end report|bokslutskommunike|halvarsrapport|"
    r"half year report|kvartalsrapport|quarterly report|six month report|nine month report|"
    r"\bq[1-4] 20\d\d\b|\b20\d\d q[1-4]\b|\b(?:first|second|third|fourth) quarter\b|"
    r"\b(?:forsta|andra|tredje|fjarde) kvartal|\b(?:forsta halvaret|first half)\b|"
    r"\b(?:januari|january|jan) "
    r"(?:mars|march|mar|juni|june|jun|september|sep|december|dec) 20\d\d\b|"
    r"for perioden (?:januari|1 januari)"
)
_ANNUAL = re.compile(
    r"arsredovisning|annual report|ars och hallbarhetsredovisning|annual and sustainability"
)


def _fold(title: str) -> str:
    t = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", t).strip()


def is_period_report(title: str) -> bool:
    """Interim and year-end reports, in English or Swedish, including headline-style
    titles such as 'X ökar förvaltningsresultatet ... för perioden januari-juni 2026'."""
    t = _fold(title)
    return bool(_PERIOD.search(t)) and not _NOT_REPORT.search(t)


def is_annual_report(title: str) -> bool:
    t = _fold(title)
    return bool(_ANNUAL.search(t)) and not _NOT_REPORT.search(t) and not is_period_report(title)


def report_date(title: str, published: datetime | None) -> date | None:
    return published.date() if published else None
