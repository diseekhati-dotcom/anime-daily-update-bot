import re
import html
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

BASE_URL = "https://animemirchi.com/"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Android 14; Mobile) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/140.0 Mobile Safari/537.36"
}
DISCOVERY_URLS = [
    BASE_URL,
    urljoin(BASE_URL, "guide/"),
    urljoin(BASE_URL, "list/"),
    urljoin(BASE_URL, "streaming/"),
]
KEYWORDS = (
    "hindi", "tamil", "telugu", "dub", "dubs", "lineup", "release",
    "schedule", "crunchyroll", "netflix", "muse india", "ani-one",
    "jiohotstar", "sony liv", "sony yay", "anime in india"
)
WEEKDAYS = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6
}

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

def clean(s):
    return re.sub(r"\s+", " ", html.unescape(s or "")).strip()

def get(url, timeout=20):
    r = requests.get(url, headers=HEADERS, timeout=timeout)
    r.raise_for_status()
    return r.text

def parse_date(s):
    s = clean(s)
    for fmt in ("%b %d, %Y", "%B %d, %Y", "%d %B %Y", "%d %b %Y",
                "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    m = re.search(r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+(\d{1,2}),\s+(\d{4})", s, re.I)
    if m:
        return datetime.strptime(m.group(0).replace(".", ""), "%b %d, %Y").date()
    return None

def weekday_from_schedule(s):
    sl = clean(s).lower()
    for name, idx in WEEKDAYS.items():
        if re.search(rf"\b{name}\b", sl):
            return idx
    return None

def extract_time(s):
    s = clean(s)
    m = re.search(r"\b(\d{1,2}:\d{2}\s*(?:AM|PM))\b", s, re.I)
    return m.group(1).upper().replace(" ", " ") if m else ""

def platform_icon(platform):
    p = platform.lower()
    if "crunchyroll" in p: return "🟠"
    if "netflix" in p: return "🔴"
    if "muse" in p: return "▶️"
    if "ani-one" in p or "ani one" in p: return "▶️"
    if "jiohotstar" in p or "hotstar" in p: return "🔵"
    if "sony" in p: return "🟣"
    if "amazon" in p: return "🟦"
    return "📺"

def normalize_platform(s):
    s = clean(s)
    s = re.sub(r"\bSchedule.*$", "", s, flags=re.I).strip(" -|")
    return s

def detect_languages(text):
    t = clean(text).lower()
    langs = []
    if re.search(r"\bhindi\b", t): langs.append("Hindi")
    if re.search(r"\btamil\b", t): langs.append("Tamil")
    if re.search(r"\btelugu\b", t): langs.append("Telugu")
    return tuple(langs)

def discover_article_urls():
    urls = set()
    for u in DISCOVERY_URLS:
        try:
            soup = BeautifulSoup(get(u), "lxml")
            for a in soup.select("a[href]"):
                href = urljoin(u, a.get("href"))
                if urlparse(href).netloc.endswith("animemirchi.com"):
                    txt = clean(a.get_text(" ", strip=True)).lower()
                    if any(k in txt for k in KEYWORDS):
                        urls.add(href.split("#")[0])
        except Exception as e:
            log.warning("Discovery failed %s: %s", u, e)

    # WordPress REST API gives a second discovery path when enabled.
    for endpoint in (
        "wp-json/wp/v2/posts?per_page=100&orderby=date&order=desc",
        "wp-json/wp/v2/pages?per_page=100&orderby=date&order=desc",
    ):
        try:
            data = requests.get(urljoin(BASE_URL, endpoint), headers=HEADERS, timeout=20).json()
            for item in data:
                title = clean(item.get("title", {}).get("rendered", ""))
                link = item.get("link")
                if link and any(k in title.lower() for k in KEYWORDS):
                    urls.add(link)
        except Exception:
            pass
    return list(urls)[:80]

def parse_table(table):
    rows = table.find_all("tr")
    if len(rows) < 2:
        return []
    headers = [clean(x.get_text(" ", strip=True)).lower() for x in rows[0].find_all(["th","td"])]
    out = []
    for tr in rows[1:]:
        cells = [clean(x.get_text(" ", strip=True)) for x in tr.find_all(["td","th"])]
        if not cells:
            continue
        if len(headers) != len(cells):
            # Some WordPress tables have an extra poster cell.
            if len(cells) == len(headers) + 1:
                cells = cells[1:]
            else:
                continue
        row = dict(zip(headers, cells))
        title = next((row[h] for h in headers if any(x in h for x in ("anime", "title", "series"))), "")
        premiere = next((row[h] for h in headers if "premiere" in h or "release date" in h or "start" in h), "")
        schedule = next((row[h] for h in headers if "schedule" in h or "time" in h), "")
        language = next((row[h] for h in headers if "language" in h or "dub" in h), "")
        if title and (premiere or schedule or language):
            out.append((title, premiere, schedule, language, row))
    return out

def infer_episode(premiere, schedule, today, title, fulltext):
    # Prefer explicit episode number in the row/article when present.
    patterns = [
        r"\bS(?:eason)?\s*(\d{1,2})\s*E(?:p(?:isode)?)?\s*(\d{1,3})\b",
        r"\bEpisode\s*[:#-]?\s*(\d{1,3})\b",
        r"\bEp\.?\s*(\d{1,3})\b",
    ]
    for pat in patterns:
        m = re.search(pat, fulltext, re.I)
        if m:
            if len(m.groups()) == 2:
                return f"S{int(m.group(1)):02d}E{int(m.group(2))}"
            return f"Episode {int(m.group(1))}"

    p = parse_date(premiere)
    if not p or p > today:
        return ""
    sched = clean(schedule).lower()
    # Batch releases do not have a meaningful single episode number.
    if "batch" in sched:
        return "Batch"
    daily = "daily" in sched
    wd = weekday_from_schedule(sched)
    if daily:
        n = (today - p).days + 1
        return f"Episode {max(1, n)}"
    if wd is not None:
        # first scheduled weekday on/after premiere
        first = p
        while first.weekday() != wd:
            first += timedelta(days=1)
        if today >= first:
            n = ((today - first).days // 7) + 1
            return f"Episode {n}"
    return ""

def collect_releases(today=None):
    today = today or date.today()
    releases = []
    urls = discover_article_urls()
    for url in urls:
        try:
            soup = BeautifulSoup(get(url), "lxml")
            title_tag = soup.find(["h1","title"])
            article_title = clean(title_tag.get_text(" ", strip=True) if title_tag else "")
            page_text = clean(soup.get_text(" ", strip=True))
            if not detect_languages(page_text) and not any(k in page_text.lower() for k in ("hindi dub", "tamil dub", "telugu dub")):
                continue
            for table in soup.find_all("table"):
                for anime, premiere, schedule, language, raw in parse_table(table):
                    langs = detect_languages(language)
                    if not langs:
                        langs = detect_languages(anime + " " + page_text)
                    if not langs:
                        continue
                    # Match the day from a weekly/daily schedule.
                    wd = weekday_from_schedule(schedule)
                    p = parse_date(premiere)
                    sl = schedule.lower()
                    daily = "daily" in sl
                    if p and today < p:
                        continue
                    if wd is not None and not daily and today.weekday() != wd:
                        continue
                    if not wd and not daily:
                        continue
                    # Don't treat explicit ended/removed entries as active.
                    if re.search(r"\b(ended|removed)\b", " ".join(raw.values()).lower()):
                        continue
                    ep = infer_episode(premiere, schedule, today, anime, " ".join(raw.values()))
                    time = extract_time(schedule)
                    platform = ""
                    lower_page = page_text.lower()
                    for name in ("Crunchyroll", "Netflix", "Muse India (YouTube)", "Muse India",
                                 "Ani-One India", "JioHotstar", "Sony LIV", "Sony YAY!", "Amazon Prime Video"):
                        if name.lower() in lower_page:
                            platform = name
                            break
                    if not platform:
                        platform = "Platform"
                    expected = "tba" in premiere.lower() or "expected" in schedule.lower() or "expected" in anime.lower()
                    score = 0
                    score += 4 if "hindi" in [x.lower() for x in langs] else 0
                    score += 2 if p else 0
                    score += 2 if time else 0
                    score += 2 if platform != "Platform" else 0
                    releases.append(Release(anime, ep, time, platform, langs, daily, expected, score))
        except Exception as e:
            log.debug("Article failed %s: %s", url, e)

    # Deduplicate same anime/platform/language combination.
    merged = {}
    for r in releases:
        key = (re.sub(r"\s+", " ", r.title.lower()), r.platform.lower())
        if key not in merged:
            merged[key] = r
        else:
            old = merged[key]
            langs = tuple(dict.fromkeys(old.languages + r.languages))
            merged[key] = Release(
                old.title, old.episode or r.episode, old.time or r.time,
                old.platform, langs, old.daily or r.daily,
                old.expected or r.expected, max(old.score, r.score)
            )
    return sorted(merged.values(), key=lambda x: (-x.score, x.time or "99:99", x.title.lower()))

def format_release(r: Release):
    title = r.title
    if r.episode:
        ep = r.episode
        # Convert "Episode 11" to S01 E11 when season is absent.
        if ep.startswith("Episode "):
            ep = f"S01 E{int(ep.split()[-1]):02d}"
    else:
        ep = "Expected"
    if r.expected and "Expected" not in ep:
        ep += " Expected"
    daily = " (Daily)" if r.daily else ""
    lines = [f"⫷ {title} ⫸"]
    if r.time:
        lines.append(f"┃🕗 Time: {r.time}{daily}")
    elif daily:
        lines.append("┃🕗 Time: Daily")
    lines.append(f"┃🎬 Episode: {ep}")
    lines.append(f"┃📺 Platform: {platform_icon(r.platform)} {r.platform}")
    lines.append("┃🔊 " + " ".join("#" + x for x in r.languages))
    lines.append("╰───────────────────")
    return "\n".join(lines)

def bold_sans(text):
    normal = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
    bold = "𝗔𝗕𝗖𝗗𝗘𝗙𝗚𝗛𝗜𝗝𝗞𝗟𝗠𝗡𝗢𝗣𝗤𝗥𝗦𝗧𝗨𝗩𝗪𝗫𝗬𝗭𝗮𝗯𝗰𝗱𝗲𝗳𝗴𝗵𝗶𝗷𝗸𝗹𝗺𝗻𝗼𝗽𝗾𝗿𝘀𝘁𝘂𝘃𝘄𝘅𝘆𝘇𝟬𝟭𝟮𝟯𝟰𝟱𝟲𝟕𝟠𝟡"
    return text.translate(str.maketrans(normal, bold))

def build_message(today=None):
    today = today or date.today()
    releases = collect_releases(today)
    day = bold_sans(today.strftime("%A").upper())
    human_date = bold_sans(today.strftime("%-d %B"))
    header = (
        "💫 [DC  Empire] –FAIRY WORLD⚡\n"
        "⟣━━━━━━━━━━━━━━━━━⟢\n"
        f"   🗓️ {day} – {human_date}\n"
        "  『 Anime Release Guide | Hindi,Telugu,Tamil Dub 』\n"
        "⟣━━━━━━━━━━━━━━━━━⟢"
    )
    body = "\n".join(format_release(r) for r in releases)
    footer = (
        "━━━━━━━━━━━━━━━━━━━\n"
        "🔔 𝗗𝗮𝗶𝗹𝘆 𝗔𝗻𝗶𝗺𝗲 𝗨𝗽𝗱𝗮𝘁𝗲𝘀 | 𝗡𝗲𝘄 𝗘𝗽𝗶𝘀𝗼𝗱𝗲𝘀 | 𝗔𝗻𝗶𝗺𝗲 𝗡𝗲𝘄𝘀\n"
        "💠 𝗣𝗼𝘄𝗲𝗿𝗲𝗱 𝗕𝘆 : @dc_hmm\n"
        "❗ 𝕁𝕠𝕚𝕟 ℕ𝕠𝕨 ➤"
    )
    if not body:
        body = "⫷ No scheduled dubbed anime found for today ⫸"
    return f"{header}\n{body}\n{footer}"
