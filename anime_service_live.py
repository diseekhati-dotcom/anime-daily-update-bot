import re
import html
import logging
import time as time_module
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from urllib.parse import urljoin, quote_plus
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

# IMPORTANT:
# This version does NOT contain a hard-coded anime list.
# It discovers current schedule articles at runtime and reads the
# release date/day/time/language/platform from the source itself.
BASE_URL = "https://animemirchi.com/"
IST = ZoneInfo("Asia/Kolkata")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 "
        "Chrome/140.0.0.0 Mobile Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-IN,en;q=0.9",
}

WEEKDAYS = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}

PLATFORMS = (
    "Crunchyroll", "Netflix", "Muse India", "Ani-One India",
    "JioHotstar", "Sony LIV", "Sony YAY!", "Amazon Prime Video",
    "Prime Video", "Disney+ Hotstar",
)

LANGUAGE_ORDER = ("Hindi", "Tamil", "Telugu")


@dataclass
class Release:
    title: str
    episode: str = ""
    time: str = ""
    platform: str = ""
    languages: tuple = ()
    daily: bool = False
    expected: bool = False
    score: int = 0
    release_date: str = ""
    source_url: str = ""
    language_times: dict = field(default_factory=dict)


def clean(s):
    return re.sub(r"\s+", " ", html.unescape(s or "")).strip()


def normalize_title(s):
    s = clean(s)
    s = re.sub(r"^\d+\s*[.)-]\s*", "", s)
    return s


def detect_languages(text):
    t = clean(text).lower()
    langs = []
    for lang in LANGUAGE_ORDER:
        if re.search(rf"\b{re.escape(lang.lower())}\b", t):
            langs.append(lang)
    return tuple(langs)


def platform_icon(platform):
    p = platform.lower()
    if "crunchyroll" in p:
        return "🟠"
    if "netflix" in p:
        return "🔴"
    if "muse" in p:
        return "▶️"
    if "ani-one" in p or "ani one" in p:
        return "▶️"
    if "jiohotstar" in p or "hotstar" in p:
        return "🔵"
    if "sony" in p:
        return "🟣"
    if "amazon" in p or "prime" in p:
        return "🟦"
    return "📺"


def fetch_direct(url, timeout=25):
    r = requests.get(url, headers=HEADERS, timeout=timeout, allow_redirects=True)
    r.raise_for_status()
    return r.text


def get(url, timeout=35):
    """Fetch a source directly, then through safe public readers/proxies."""
    attempts = []

    try:
        return fetch_direct(url, timeout)
    except Exception as exc:
        attempts.append(f"direct={exc}")

    # Google Translate proxy is useful when Render's IP is blocked.
    try:
        if url.startswith(BASE_URL):
            path = url[len(BASE_URL):]
            proxy = "https://animemirchi-com.translate.goog/" + path
            proxy += (
                "?_x_tr_sl=auto&_x_tr_tl=en&_x_tr_hl=en"
                if "?" not in proxy else
                "&_x_tr_sl=auto&_x_tr_tl=en&_x_tr_hl=en"
            )
            r = requests.get(proxy, headers=HEADERS, timeout=45)
            r.raise_for_status()
            if r.text.strip():
                return r.text
    except Exception as exc:
        attempts.append(f"translate={exc}")

    # Jina Reader fallback.
    try:
        if url.startswith(BASE_URL):
            target = url.split("animemirchi.com/", 1)[1]
            proxy = "https://r.jina.ai/https://animemirchi.com/" + target
        else:
            proxy = "https://r.jina.ai/http://" + url.split("://", 1)[-1]
        r = requests.get(proxy, headers=HEADERS, timeout=45)
        r.raise_for_status()
        if r.text.strip():
            return r.text
    except Exception as exc:
        attempts.append(f"jina={exc}")

    raise RuntimeError("Unable to fetch source: " + url + " | " + " | ".join(attempts))


def parse_date(s):
    s = clean(s)
    if not s or s.upper() in {"TBA", "N/A", "-"}:
        return None
    for fmt in (
        "%b %d, %Y", "%B %d, %Y", "%d %B %Y", "%d %b %Y",
        "%Y-%m-%d", "%b %d %Y", "%B %d %Y",
    ):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    # Extract a date from a longer cell such as "Sep 07, 2026 (IST)".
    m = re.search(
        r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
        r"\s+\d{1,2},\s+\d{4}\b",
        s, re.I,
    )
    if m:
        try:
            return datetime.strptime(m.group(0), "%b %d, %Y").date()
        except ValueError:
            pass
    return None


def weekday_from_schedule(s):
    s = clean(s).lower()
    for name, idx in WEEKDAYS.items():
        if re.search(rf"\b{name}\b", s):
            return idx
    return None


def extract_time(s):
    s = clean(s)
    m = re.search(r"\b(\d{1,2}:\d{2}\s*(?:AM|PM))\b", s, re.I)
    return m.group(1).upper().replace("  ", " ") if m else ""


def schedule_is_daily(s):
    t = clean(s).lower()
    return bool(re.search(r"\bdaily\b|\bevery day\b|\beach day\b", t))


def is_tba(s):
    t = clean(s).lower()
    return not t or t in {"tba", "tbd", "to be announced", "____", "-", "n/a"}


def extract_episode(text):
    """Keep an episode/range only when the source explicitly gives it."""
    t = clean(text)

    patterns = [
        r"\b(?:episodes?|eps?|ep\.?)\s*[:#]?\s*(\d{1,4}\s*[-–]\s*\d{1,4})\b",
        r"\b(?:episodes?|eps?|ep\.?)\s*[:#]?\s*(\d{1,4})\b",
        r"\bS\d{1,2}\s*E\d{1,4}\b",
        r"\bE\d{1,4}\b",
    ]
    for pat in patterns:
        m = re.search(pat, t, re.I)
        if m:
            return clean(m.group(0))

    # Common source wording: "Ep. 176-196".
    m = re.search(r"\bEp\.?\s*(\d{1,4}\s*[-–]\s*\d{1,4})\b", t, re.I)
    if m:
        return "Ep. " + clean(m.group(1))
    return ""


def infer_platform(text):
    t = clean(text).lower()
    for name in PLATFORMS:
        if name.lower() in t:
            return name
    return ""


def discover_article_urls():
    """
    Runtime discovery:
    1) Anime Mirchi category pages
    2) Anime Mirchi RSS
    3) Google News RSS search for newly published Anime Mirchi articles

    No fixed anime titles are stored here.
    """
    urls = [
        urljoin(BASE_URL, "streaming/"),
        urljoin(BASE_URL, "news/"),
        urljoin(BASE_URL, "feed/"),
    ]

    queries = [
        "site:animemirchi.com anime Hindi dubbed schedule",
        "site:animemirchi.com anime Tamil Telugu dub schedule",
        "site:animemirchi.com Crunchyroll Netflix Muse India Ani-One anime",
    ]
    for q in queries:
        urls.append(
            "https://news.google.com/rss/search?q="
            + quote_plus(q)
            + "&hl=en-IN&gl=IN&ceid=IN:en"
        )

    found = []
    seen = set()

    for source in urls:
        try:
            if source.endswith(".xml") or "/feed/" in source or "news.google.com/rss" in source:
                text = get(source)
                soup = BeautifulSoup(text, "xml")
                for item in soup.find_all(["item", "entry"]):
                    link = ""
                    link_tag = item.find("link")
                    if link_tag:
                        link = link_tag.get("href") or link_tag.get_text(" ", strip=True)
                    if not link:
                        continue
                    if "animemirchi.com/" in link and link not in seen:
                        seen.add(link)
                        found.append(link)
                continue

            text = get(source)
            soup = BeautifulSoup(text, "html.parser")
            for a in soup.find_all("a", href=True):
                href = urljoin(BASE_URL, a["href"])
                if "animemirchi.com/" not in href:
                    continue
                if href.rstrip("/") in {BASE_URL.rstrip("/"), source.rstrip("/")}:
                    continue
                label = clean(a.get_text(" ", strip=True))
                # Keep article-looking links; category/navigation links are ignored.
                if label and href not in seen:
                    seen.add(href)
                    found.append(href)
        except Exception as exc:
            log.warning("Discovery failed %s: %s", source, exc)

    # Limit network work per run, but keep enough articles to discover new titles.
    return found[:40]


def parse_table(table):
    rows = table.find_all("tr")
    if len(rows) < 2:
        return []

    headers = [
        clean(x.get_text(" ", strip=True)).lower()
        for x in rows[0].find_all(["th", "td"])
    ]
    out = []

    for tr in rows[1:]:
        cells = [clean(x.get_text(" ", strip=True)) for x in tr.find_all(["td", "th"])]
        if len(cells) != len(headers):
            continue

        row = dict(zip(headers, cells))
        title = next(
            (row[h] for h in headers if any(x in h for x in ("anime", "title", "series"))),
            "",
        )
        premiere = next(
            (
                row[h]
                for h in headers
                if any(x in h for x in ("premiere", "release date", "start date", "date"))
            ),
            "",
        )
        schedule = next(
            (
                row[h]
                for h in headers
                if any(x in h for x in ("schedule", "time", "release time"))
            ),
            "",
        )
        language = next(
            (
                row[h]
                for h in headers
                if any(x in h for x in ("language", "dub"))
            ),
            "",
        )

        if title and (premiere or schedule or language):
            out.append((title, premiere, schedule, language, row))

    return out


def row_release(anime, premiere, schedule, language, raw, source_url, today, page_text):
    anime = normalize_title(anime)
    if not anime:
        return None

    row_text = " ".join(str(v) for v in raw.values())
    combined = clean(" ".join((anime, premiere, schedule, language, row_text)))

    langs = detect_languages(language or combined)
    # We only publish the languages this source explicitly mentions.
    if not langs:
        return None

    p = parse_date(premiere)
    if p and today < p:
        return None

    sched = clean(schedule)
    if is_tba(sched):
        return None

    daily = schedule_is_daily(sched)
    wd = weekday_from_schedule(sched)

    # A recurring schedule must match today's actual calendar day.
    if not daily and wd is not None and today.weekday() != wd:
        return None

    # If a source gives neither "daily" nor a weekday, it is a batch drop.
    # Include it only on its explicit premiere/release date.
    if not daily and wd is None:
        if not p or today != p:
            return None

    release_time = extract_time(sched)
    episode = extract_episode(combined)

    # Never manufacture an episode number from the date.
    # If the source doesn't state it, show "Expected".
    if not episode:
        episode = "Expected"

    platform = infer_platform(row_text) or infer_platform(page_text) or "Platform"

    return Release(
        title=anime,
        episode=episode,
        time=release_time,
        platform=platform,
        languages=langs,
        daily=daily,
        expected=(episode == "Expected"),
        score=10 if p else 6,
        release_date=p.isoformat() if p else "",
        source_url=source_url,
        language_times={lang: release_time for lang in langs if release_time},
    )


def parse_article(url, today):
    html_text = get(url)
    soup = BeautifulSoup(html_text, "html.parser")

    page_text = clean(soup.get_text(" ", strip=True))
    releases = []

    # Best case: structured schedule tables.
    for table in soup.find_all("table"):
        for anime, premiere, schedule, language, raw in parse_table(table):
            item = row_release(
                anime, premiere, schedule, language, raw, url, today, page_text
            )
            if item:
                releases.append(item)

    # Some newer articles use lists/cards instead of tables.
    # Inspect headings/paragraphs containing a date + time + language.
    if not releases:
        blocks = soup.find_all(["article", "li", "p", "div"])
        for block in blocks:
            text = clean(block.get_text(" ", strip=True))
            if len(text) < 20 or len(text) > 1200:
                continue
            if not detect_languages(text):
                continue

            p = parse_date(text)
            sched = text
            if not p:
                # The page may put the date in the surrounding article.
                continue

            item = row_release(
                text.split(" | ")[0][:180],
                p.strftime("%b %d, %Y"),
                sched,
                text,
                {"text": text},
                url,
                today,
                page_text,
            )
            if item and item.title.lower() not in {x.title.lower() for x in releases}:
                releases.append(item)

    return releases


def collect_releases(today=None):
    """
    Build today's list from freshly discovered source articles.

    There is deliberately NO hard-coded anime fallback. If a source is
    unavailable, the bot does not invent an episode or schedule.
    """
    today = today or datetime.now(IST).date()
    releases = []

    urls = discover_article_urls()
    log.info("Discovered %d candidate source URLs", len(urls))

    for url in urls:
        try:
            # Skip obviously irrelevant Google/website pages.
            if "animemirchi.com/" not in url:
                continue
            for release in parse_article(url, today):
                releases.append(release)
        except Exception as exc:
            log.warning("Source article failed %s: %s", url, exc)

    # Merge same anime/platform records while preserving all languages.
    merged = {}
    for r in releases:
        key = (
            re.sub(r"\s+", " ", r.title.lower()).strip(),
            r.platform.lower(),
        )
        if key not in merged:
            merged[key] = r
            continue

        old = merged[key]
        times = dict(old.language_times)
        times.update(r.language_times)

        # Prefer explicit episode/date/time from a source over "Expected".
        episode = old.episode
        if episode == "Expected" and r.episode != "Expected":
            episode = r.episode

        merged[key] = Release(
            title=old.title,
            episode=episode,
            time=old.time or r.time,
            platform=old.platform if old.platform != "Platform" else r.platform,
            languages=tuple(dict.fromkeys(old.languages + r.languages)),
            daily=old.daily or r.daily,
            expected=(episode == "Expected"),
            score=max(old.score, r.score),
            release_date=old.release_date or r.release_date,
            source_url=old.source_url or r.source_url,
            language_times=times,
        )

    return sorted(
        merged.values(),
        key=lambda x: ((x.time or "99:99"), x.title.lower()),
    )


def format_release(r: Release):
    ep = r.episode or "Expected"
    if r.expected and ep != "Expected" and "Expected" not in ep:
        ep += " Expected"

    lines = [
        f"⫷ {r.title} ⫸",
        f"┃🎬 Episode: {ep}",
    ]

    # Show language-specific times when the source provides them.
    if r.language_times:
        groups = {}
        for lang in LANGUAGE_ORDER:
            t = r.language_times.get(lang)
            if t:
                groups.setdefault(t, []).append(lang)

        if len(groups) == 1:
            t, langs = next(iter(groups.items()))
            lines.append(
                f"┃⏰: {' • '.join(langs)} {t}"
                f"{' (Daily)' if r.daily else ''}"
            )
        else:
            for t, langs in groups.items():
                lines.append(
                    f"┃⏰: {' • '.join(langs)} {t}"
                    f"{' (Daily)' if r.daily else ''}"
                )
    elif r.time:
        lines.append(f"┃⏰: {r.time}{' (Daily)' if r.daily else ''}")

    lines.append(f"┃📺 Platform: {platform_icon(r.platform)} {r.platform}")
    lines.append("┃🔊 " + " ".join("#" + x for x in r.languages))
    lines.append("╰───────────────────")
    return "\n".join(lines)


def bold_sans(text):
    normal = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
    bold = (
        "𝗔𝗕𝗖𝗗𝗘𝗙𝗚𝗛𝗜𝗝𝗞𝗟𝗠𝗡𝗢𝗣𝗤𝗥𝗦𝗧𝗨𝗩𝗪𝗫𝗬𝗭"
        "𝗮𝗯𝗰𝗱𝗲𝗳𝗴𝗵𝗶𝗷𝗸𝗹𝗺𝗻𝗼𝗽𝗾𝗿𝘀𝘁𝘂𝘃𝘄𝘅𝘆𝘇"
        "𝟬𝟭𝟮𝟯𝟰𝟱𝟲𝟳𝟴𝟵"
    )
    return text.translate(str.maketrans(normal, bold))


def build_message(today=None):
    today = today or datetime.now(IST).date()
    releases = collect_releases(today)

    day = bold_sans(today.strftime("%A").upper())
    human_date = bold_sans(f"{today.day} {today.strftime('%B')}")

    header = (
        "💫 [DC  Empire] –FAIRY WORLD⚡\n"
        "⟣━━━━━━━━━━━━━━━━━⟢\n"
        f"   🗓️ {day} – {human_date}\n"
        "  『 Anime Release Guide | Hindi,Telugu,Tamil Dub 』\n"
        "⟣━━━━━━━━━━━━━━━━━⟢"
    )

    body = "\n".join(format_release(r) for r in releases)

    footer = (
        "🔎 Source: DC\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        "🔔 𝗗𝗮𝗶𝗹𝘆 𝗔𝗻𝗶𝗺𝗲 𝗨𝗽𝗱𝗮𝘁𝗲𝘀 | 𝗡𝗲𝘄 𝗘𝗽𝗶𝘀𝗼𝗱𝗲𝘀 | 𝗔𝗻𝗶𝗺𝗲 𝗡𝗲𝘄𝘀\n"
        "💠 𝗣𝗼𝘄𝗲𝗿𝗲𝗱 𝗕𝘆 : @dc_hmm\n"
        "❗ 𝕁𝕠𝕚𝕟 ℕ𝕠𝕨 ➤"
    )

    if not body:
        body = "⫷ No source-confirmed dubbed anime release found for today ⫸"

    return f"{header}\n{body}\n{footer}"
