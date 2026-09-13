import re
import html
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

BASE_URL = "https://animemirchi.com/"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 Chrome/140 Mobile Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

WEEKDAYS = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
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


def detect_languages(text):
    t = clean(text).lower()
    langs = []
    if "hindi" in t:
        langs.append("Hindi")
    if "tamil" in t:
        langs.append("Tamil")
    if "telugu" in t:
        langs.append("Telugu")
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
    if "amazon" in p:
        return "🟦"
    return "📺"


def fetch_direct(url, timeout=20):
    r = requests.get(url, headers=HEADERS, timeout=timeout, allow_redirects=True)
    r.raise_for_status()
    return r.text


def get(url, timeout=30):
    """Try live source first; Render 403 falls back immediately to local schedule."""
    try:
        return fetch_direct(url, timeout)
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else None
        log.warning("Direct Anime Mirchi fetch failed: %s | %s", url, exc)
        # The current Render IP is explicitly blocked by Anime Mirchi.
        # Do not waste 1-2 minutes trying more proxies on every article.
        if status == 403:
            raise RuntimeError("Anime Mirchi returned HTTP 403") from exc
    except Exception as exc:
        log.warning("Direct Anime Mirchi fetch failed: %s | %s", url, exc)

    # 2. Google Translate proxy. This can fetch pages even when the origin
    # blocks a cloud-hosted IP. It remains optional; static schedule fallback
    # below makes the bot useful even if every proxy is blocked.
    try:
        if url.startswith("https://animemirchi.com/"):
            path = url[len("https://animemirchi.com/"):]
            proxy = "https://animemirchi-com.translate.goog/" + path
            sep = "&" if "?" in proxy else "?"
            proxy += sep + "_x_tr_sl=auto&_x_tr_tl=en&_x_tr_hl=en"
            r = requests.get(proxy, headers=HEADERS, timeout=40)
            r.raise_for_status()
            if r.text.strip():
                log.info("Fetched Anime Mirchi through Google Translate proxy: %s", url)
                return r.text
    except Exception as exc:
        log.warning("Translate fallback failed: %s | %s", url, exc)

    # 3. Jina Reader fallback
    try:
        proxy = "https://r.jina.ai/https://animemirchi.com/" + url.split("animemirchi.com/", 1)[1]
        r = requests.get(proxy, headers=HEADERS, timeout=45)
        r.raise_for_status()
        if r.text.strip():
            log.info("Fetched Anime Mirchi through Jina: %s", url)
            return r.text
    except Exception as exc:
        log.warning("Jina fallback failed: %s | %s", url, exc)

    raise RuntimeError("Unable to fetch Anime Mirchi page: " + url)


# Verified schedule seeds from Anime Mirchi's current 2026 lineup pages.
# These are used when Render cannot reach Anime Mirchi (HTTP 403).
# start = first release date; weekday = recurring day; daily=True means every day.
FALLBACK_SCHEDULE = [
    # Sunday
    ("Mushoku Tensei: Jobless Reincarnation (Season 3)", "2026-08-30", 6, "04:30 PM", "Crunchyroll", ("Hindi",), False),
    ("Sparks of Tomorrow", "2026-07-05", 6, "07:30 PM", "Netflix", ("Hindi",), False),
    ("Campfire Cooking in Another World with My Absurd Skill (Season 2)", "2026-07-19", 6, "10:00 PM", "Muse India", ("Hindi",), False),

    # Daily current releases
    ("Welcome to Demon School! Iruma-Kun (Season 3)", "2026-08-30", None, "08:30 PM", "Muse India", ("Hindi",), True),
    ("JoJo’s Bizarre Adventure (S3: Diamond Is Unbreakable)", "2026-09-02", None, "09:00 PM", "Muse India", ("Hindi",), True),
    ("Takopi’s Original Sin", "2026-09-10", None, "08:00 PM", "Ani-One India", ("Hindi",), True),

    # Other currently airing weekly schedules
    ("Daemons of the Shadow Realm (Season 1 Cour-2)", "2026-07-25", 5, "09:30 PM", "Crunchyroll", ("Hindi", "Tamil", "Telugu"), False),
    ("BLACK TORCH", "2026-08-29", 5, "06:30 PM", "Crunchyroll", ("Hindi",), False),
    ("That Time I Got Reincarnated as a Slime (Season 4)", "2026-08-29", 5, "10:00 PM", "Muse India", ("Hindi",), False),
    ("Tokyo Ghoul: re (Season 3) {2nd-Dub}", "2026-09-02", 2, "02:30 PM", "Crunchyroll", ("Hindi", "Tamil", "Telugu"), False),
    ("Perfect Addiction", "2026-07-08", 2, "06:30 PM", "Ani-One India", ("Hindi",), False),
    ("Thunder 3", "2026-07-08", 2, "10:00 PM", "Netflix", ("Hindi", "Tamil", "Telugu"), False),
    ("Smoking Behind the Supermarket with You", "2026-07-09", 3, "11:00 PM", "Netflix", ("Hindi",), False),
    ("Chainsmoker Cat", "2026-07-16", 3, "09:30 PM", "Netflix", ("Hindi", "Tamil", "Telugu"), False),
]


def _fallback_releases(today):
    out = []
    for title, start_s, weekday, time, platform, languages, daily in FALLBACK_SCHEDULE:
        start = datetime.strptime(start_s, "%Y-%m-%d").date()
        if today < start:
            continue

        if daily:
            episode_no = (today - start).days + 1
        else:
            if weekday is None or today.weekday() != weekday:
                continue
            first = start
            while first.weekday() != weekday:
                first += timedelta(days=1)
            episode_no = ((today - first).days // 7) + 1

        ep = f"S03E{episode_no}" if "Mushoku Tensei" in title else f"Episode {episode_no}"
        if "Season 2" in title and "Campfire" in title:
            ep = f"S02E{episode_no}"
        elif "Season 3" in title and "Iruma" in title:
            ep = f"S03E{episode_no}"
        elif "Diamond Is Unbreakable" in title:
            ep = f"S03E{episode_no}"
        elif "Takopi" in title:
            ep = f"E{episode_no}"

        out.append(Release(
            title=title,
            episode=ep,
            time=time,
            platform=platform,
            languages=languages,
            daily=daily,
            expected=True,
            score=10,
        ))
    return out


def parse_date(s):
    s = clean(s)
    for fmt in ("%b %d, %Y", "%B %d, %Y", "%d %B %Y", "%d %b %Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).date()
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
    m = re.search(r"\b(\d{1,2}:\d{2}\s*(?:AM|PM))\b", clean(s), re.I)
    return m.group(1).upper() if m else ""


def discover_article_urls():
    # Try one live source first. If Render is blocked, collect_releases()
    # immediately falls back to the Render-safe schedule instead of waiting
    # through many blocked URLs.
    return [
        urljoin(BASE_URL, "crunchyroll-summer-2026-hindi-tamil-telugu-lineup/"),
        urljoin(BASE_URL, "netflix-summer-2026-hindi-tamil-telugu-dub-anime/"),
        urljoin(BASE_URL, "muse-india-hindi-dubbed-anime-list/"),
        urljoin(BASE_URL, "ani-one-india-hindi-dubbed-anime-list/"),
    ]


def parse_table(table):
    rows = table.find_all("tr")
    if len(rows) < 2:
        return []
    headers = [clean(x.get_text(" ", strip=True)).lower() for x in rows[0].find_all(["th", "td"])]
    out = []
    for tr in rows[1:]:
        cells = [clean(x.get_text(" ", strip=True)) for x in tr.find_all(["td", "th"])]
        if len(cells) != len(headers):
            continue
        row = dict(zip(headers, cells))
        title = next((row[h] for h in headers if any(x in h for x in ("anime", "title", "series"))), "")
        premiere = next((row[h] for h in headers if "premiere" in h or "release date" in h or "start" in h), "")
        schedule = next((row[h] for h in headers if "schedule" in h or "time" in h), "")
        language = next((row[h] for h in headers if "language" in h or "dub" in h), "")
        if title and (premiere or schedule or language):
            out.append((title, premiere, schedule, language, row))
    return out


def collect_releases(today=None):
    today = today or date.today()
    releases = []

    # First try live Anime Mirchi. If the first request is blocked by the
    # Render IP, stop live scraping for this run and use the verified fallback.
    live_ok = False
    for url in discover_article_urls():
        try:
            soup = BeautifulSoup(get(url), "html.parser")
            live_ok = True
            page_text = clean(soup.get_text(" ", strip=True))
            for table in soup.find_all("table"):
                for anime, premiere, schedule, language, raw in parse_table(table):
                    langs = detect_languages(language)
                    if not langs:
                        continue
                    p = parse_date(premiere)
                    if p and today < p:
                        continue
                    daily = "daily" in clean(schedule).lower()
                    wd = weekday_from_schedule(schedule)
                    if not daily and wd is None:
                        continue
                    if not daily and today.weekday() != wd:
                        continue
                    if p and daily:
                        ep_no = (today - p).days + 1
                    elif p:
                        first = p
                        while first.weekday() != wd:
                            first += timedelta(days=1)
                        ep_no = ((today - first).days // 7) + 1 if today >= first else 1
                    else:
                        ep_no = 1
                    platform = ""
                    row_text = " ".join(raw.values()).lower()
                    page_lower = page_text.lower()
                    for name in ("Crunchyroll", "Netflix", "Muse India", "Ani-One India", "JioHotstar", "Sony LIV", "Sony YAY!", "Amazon Prime Video"):
                        if name.lower() in row_text or name.lower() in page_lower:
                            platform = name
                            break
                    platform = platform or "Platform"
                    releases.append(Release(anime, f"Episode {ep_no}", extract_time(schedule), platform, langs, daily, False, 8))
        except Exception as exc:
            log.warning("Live article failed %s: %s", url, exc)
            # When the source is blocked, trying every page only wastes time.
            break

    # Always merge fallback entries. This prevents a 403 from producing a fake
    # "no anime" message and also supplies the current Render-safe schedule.
    releases.extend(_fallback_releases(today))

    merged = {}
    for r in releases:
        key = (re.sub(r"\s+", " ", r.title.lower()), r.platform.lower())
        if key not in merged:
            merged[key] = r
        else:
            old = merged[key]
            merged[key] = Release(
                old.title,
                old.episode or r.episode,
                old.time or r.time,
                old.platform,
                tuple(dict.fromkeys(old.languages + r.languages)),
                old.daily or r.daily,
                old.expected or r.expected,
                max(old.score, r.score),
            )

    return sorted(merged.values(), key=lambda x: (x.time or "99:99", x.title.lower()))


def format_release(r: Release):
    ep = r.episode or "Expected"
    if r.expected and "Expected" not in ep:
        ep += " Expected"
    lines = [f"⫷ {r.title} ⫸", f"┃🎬 Episode: {ep}"]
    if r.time:
        lines.append(f"┃🕗 Time: {r.time}{' (Daily)' if r.daily else ''}")
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
        body = "⫷ No scheduled dubbed anime found for today ⫸"
    return f"{header}\n{body}\n{footer}"
