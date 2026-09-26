#!/usr/bin/env python3
"""Schengen vize randevu takipçisi.

Randevu kaynaklarını kontrol eder, yeni açılan randevu olursa e-posta,
Telegram ve/veya ntfy (telefon bildirimi) ile haber verir.

Sadece Python standart kütüphanesini kullanır.

Kullanım:
    python checker.py                 # bir kez kontrol et
    python checker.py --loop 300      # 5 dakikada bir sürekli kontrol et
    python checker.py --loop 300 --max-minutes 330   # ...ve 330 dakika sonra dur
    python checker.py --test-notify   # bildirim kanallarını test et
    python checker.py --report        # kaynaklar hangi ülkeleri kapsıyor?
"""

from __future__ import annotations

import argparse
import base64
import copy
import hashlib
import html
import json
import os
import re
import smtplib
import ssl
import sys
import time
import tomllib
import unicodedata
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG = ROOT / "config.toml"
DEFAULT_STATE = ROOT / "state" / "state.json"
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)

STATUS_LABELS = {
    "open": "AÇIK",
    "waitlist_open": "BEKLEME LİSTESİ AÇIK",
    "page": "SAYFA UYARISI",
}

# Schengen ülkeleri: (ISO alpha-3, alpha-2, İngilizce ad, Türkçe ad, bayrak)
SCHENGEN = [
    ("aut", "at", "Austria", "Avusturya", "🇦🇹"),
    ("bel", "be", "Belgium", "Belçika", "🇧🇪"),
    ("bgr", "bg", "Bulgaria", "Bulgaristan", "🇧🇬"),
    ("hrv", "hr", "Croatia", "Hırvatistan", "🇭🇷"),
    ("cze", "cz", "Czechia", "Çekya", "🇨🇿"),
    ("dnk", "dk", "Denmark", "Danimarka", "🇩🇰"),
    ("est", "ee", "Estonia", "Estonya", "🇪🇪"),
    ("fin", "fi", "Finland", "Finlandiya", "🇫🇮"),
    ("fra", "fr", "France", "Fransa", "🇫🇷"),
    ("deu", "de", "Germany", "Almanya", "🇩🇪"),
    ("grc", "gr", "Greece", "Yunanistan", "🇬🇷"),
    ("hun", "hu", "Hungary", "Macaristan", "🇭🇺"),
    ("isl", "is", "Iceland", "İzlanda", "🇮🇸"),
    ("ita", "it", "Italy", "İtalya", "🇮🇹"),
    ("lva", "lv", "Latvia", "Letonya", "🇱🇻"),
    ("lie", "li", "Liechtenstein", "Lihtenştayn", "🇱🇮"),
    ("ltu", "lt", "Lithuania", "Litvanya", "🇱🇹"),
    ("lux", "lu", "Luxembourg", "Lüksemburg", "🇱🇺"),
    ("mlt", "mt", "Malta", "Malta", "🇲🇹"),
    ("nld", "nl", "Netherlands", "Hollanda", "🇳🇱"),
    ("nor", "no", "Norway", "Norveç", "🇳🇴"),
    ("pol", "pl", "Poland", "Polonya", "🇵🇱"),
    ("prt", "pt", "Portugal", "Portekiz", "🇵🇹"),
    ("rou", "ro", "Romania", "Romanya", "🇷🇴"),
    ("svk", "sk", "Slovakia", "Slovakya", "🇸🇰"),
    ("svn", "si", "Slovenia", "Slovenya", "🇸🇮"),
    ("esp", "es", "Spain", "İspanya", "🇪🇸"),
    ("swe", "se", "Sweden", "İsveç", "🇸🇪"),
    ("che", "ch", "Switzerland", "İsviçre", "🇨🇭"),
]
EXTRA_ALIASES = {"cze": ["czech republic"], "nld": ["the netherlands", "holland"]}


def log(msg: str) -> None:
    print(f"[{datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S}Z] {msg}", flush=True)


def norm(text: object) -> str:
    """Küçük harf, Türkçe karakterler sadeleştirilmiş, boşlukları kırpılmış metin."""
    s = str(text or "").replace("İ", "i").replace("I", "ı").lower()
    s = s.translate(str.maketrans("çğıöşü", "cgiosu"))
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s).strip()


def _country_index() -> dict[str, tuple]:
    idx = {}
    for row in SCHENGEN:
        for alias in (*row[:4], *EXTRA_ALIASES.get(row[0], [])):
            idx[norm(alias)] = row
    return idx


COUNTRY_INDEX = _country_index()


def country_label(code: str) -> str:
    row = COUNTRY_INDEX.get(norm(code))
    return f"{row[4]} {row[3]}" if row else code.upper()


def expand_codes(codes: list[str]) -> set[str]:
    """"schengen" kelimesini tüm Schengen ülkelerinin kod/adlarına açar."""
    out: set[str] = set()
    for c in codes:
        if norm(c) == "schengen":
            out.update(COUNTRY_INDEX)
        else:
            row = COUNTRY_INDEX.get(norm(c))
            out.update(norm(a) for a in (row[:4] if row else [c]))
            if row:
                out.update(norm(a) for a in EXTRA_ALIASES.get(row[0], []))
    return out


# ---------------------------------------------------------------------------
# Veri modelleri
# ---------------------------------------------------------------------------


@dataclass
class Appointment:
    source: str
    country: str
    mission: str
    center: str
    visa_category: str
    visa_type: str
    status: str
    date: str = ""
    link: str = ""
    checked_at: str = ""

    @property
    def key(self) -> str:
        return "|".join(
            norm(x)
            for x in (self.country, self.mission, self.center, self.visa_category, self.visa_type)
        )


@dataclass
class Target:
    name: str
    codes: list[str]
    cities: list[str] = field(default_factory=list)
    visa_types: list[str] = field(default_factory=list)
    exclude: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._codes = expand_codes(self.codes) - expand_codes(self.exclude)

    def matches(self, appt: Appointment) -> bool:
        if norm(appt.mission) not in self._codes:
            return False
        if self.cities and not any(norm(c) in norm(appt.center) for c in self.cities):
            return False
        if self.visa_types:
            haystack = norm(f"{appt.visa_category} {appt.visa_type}")
            if not any(norm(v) in haystack for v in self.visa_types):
                return False
        return True


@dataclass
class Alert:
    target: str
    title: str
    details: list[str]
    link: str = ""
    reminder: bool = False


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------


def http_get(url: str, timeout: float) -> str:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json, text/html;q=0.9, */*;q=0.8",
            "Accept-Language": "tr-TR,tr;q=0.9,en;q=0.8",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        charset = resp.headers.get_content_charset() or "utf-8"
        return resp.read().decode(charset, errors="replace")


def http_post(url: str, data: bytes, headers: dict[str, str], timeout: float = 30) -> None:
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        resp.read()


# ---------------------------------------------------------------------------
# Kaynak: JSON API
# ---------------------------------------------------------------------------


def _first(record: dict, *names: str) -> str:
    for n in names:
        v = record.get(n)
        if v not in (None, ""):
            return str(v)
    return ""


def extract_records(payload: object) -> list[dict]:
    """Farklı API şekillerinden randevu listesini çıkarır."""
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)]
    if isinstance(payload, dict):
        for path in (("data", "visas"), ("data",), ("visas",), ("results",), ("items",)):
            node: object = payload
            for p in path:
                node = node.get(p) if isinstance(node, dict) else None
            if isinstance(node, list):
                return [r for r in node if isinstance(r, dict)]
    raise ValueError("API yanıtında randevu listesi bulunamadı")


def parse_record(record: dict, source: str) -> Appointment:
    date = _first(record, "last_available_date", "appointment_date", "available_date", "date")
    status = norm(_first(record, "status"))
    if not status:
        # Durum alanı olmayan API'lerde tarih varsa randevu açık demektir.
        status = "open" if date else "closed"
    return Appointment(
        source=source,
        country=_first(record, "country_code", "source_country", "country"),
        mission=_first(record, "mission_code", "mission_country", "mission"),
        center=_first(record, "center", "center_name", "city"),
        visa_category=_first(record, "visa_category", "category"),
        visa_type=_first(record, "visa_type", "visa_subcategory", "subcategory"),
        status=status,
        date=date,
        link=_first(record, "book_now_link", "link", "url"),
        checked_at=_first(record, "last_checked_at", "last_checked", "updated_at"),
    )


def age_hours(ts: str, now: float) -> float | None:
    """ISO zaman damgasının kaç saat önce olduğunu döner (okunamazsa None)."""
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (now - dt.timestamp()) / 3600


def fetch_api(src: dict, timeout: float) -> list[Appointment]:
    body = http_get(src["url"], timeout)
    records = extract_records(json.loads(body))
    return [parse_record(r, src.get("name", src["url"])) for r in records]


# ---------------------------------------------------------------------------
# Kaynak: web sayfası izleme
# ---------------------------------------------------------------------------


def visible_text(page: str) -> str:
    page = re.sub(r"(?is)<(script|style|noscript|template)\b.*?</\1>", " ", page)
    page = re.sub(r"(?is)<input\b[^>]*>", " ", page)  # ASP.NET viewstate vb.
    page = re.sub(r"(?s)<!--.*?-->", " ", page)
    page = re.sub(r"(?s)<[^>]+>", " ", page)
    return re.sub(r"\s+", " ", html.unescape(page)).strip()


def check_page(src: dict, text: str, prev: dict | None) -> tuple[bool, dict, str]:
    """(uyarı_gerekli_mi, yeni_durum, açıklama) döner."""
    mode = src.get("mode", "changes")
    ntext = norm(text)
    patterns = [norm(p) for p in src.get("patterns", [])]
    digest = hashlib.sha256(ntext.encode()).hexdigest()

    if mode == "appears":
        found = [p for p in patterns if p in ntext]
        active = bool(found)
        why = f"Sayfada şu yazı çıktı: {', '.join(found)}"
    elif mode == "disappears":
        active = bool(patterns) and not any(p in ntext for p in patterns)
        why = "Sayfadaki 'randevu yok' yazısı kalktı"
    elif mode == "changes":
        active = prev is not None and prev.get("hash") != digest
        why = "Sayfa içeriği değişti"
    else:
        raise ValueError(f"Bilinmeyen sayfa modu: {mode}")

    was_active = bool(prev and prev.get("active"))
    should_alert = active and (mode == "changes" or not was_active)
    return should_alert, {"hash": digest, "active": active and mode != "changes"}, why


# ---------------------------------------------------------------------------
# Kaynak: Visa Catcher şehir sayfası (ülke başına anlık durum)
# ---------------------------------------------------------------------------

FLAG_RE = re.compile(r"[\U0001F1E6-\U0001F1FF]{2}")
DATE_RE = re.compile(r"\b\d{1,2}[./]\d{1,2}[./]\d{4}\b")


def _relative_to_iso(text: str, now: float) -> str:
    """'az önce', '5 dakika önce', '2 saat önce' -> ISO zaman damgası."""
    t = norm(text)
    if "az once" in t or "simdi" in t:
        secs = 0.0
    else:
        m = re.search(r"(\d+)\s*(saniye|sn|dakika|dk|saat|gun)", t)
        if not m:
            return ""
        n = float(m.group(1))
        secs = n * {"saniye": 1, "sn": 1, "dakika": 60, "dk": 60, "saat": 3600, "gun": 86400}[m.group(2)]
    return datetime.fromtimestamp(now - secs, timezone.utc).isoformat()


def _split_country(chunk: str) -> tuple[str, str]:
    """'İsviçre Bekleme listesi açık BEKLEME' -> ('İsviçre', 'Bekleme listesi açık BEKLEME')."""
    words = chunk.split()
    for n in (3, 2, 1):
        if norm(" ".join(words[:n])) in COUNTRY_INDEX:
            return " ".join(words[:n]), " ".join(words[n:])
    return (words[0] if words else ""), " ".join(words[1:])


def classify_status(text: str) -> tuple[str, str]:
    """Durum metnini (status, tarih) çiftine çevirir."""
    t = norm(text)
    date_m = DATE_RE.search(text)
    if "musait tarih yok" in t or re.search(r"\byok$", t):
        return "closed", ""
    if "bekleme" in t or "waitlist" in t:
        return "waitlist_open", ""
    if date_m or "musait" in t or re.search(r"\b(var|acik|open|available)\b", t):
        return "open", date_m.group(0) if date_m else ""
    return "unknown", ""


def parse_visacatcher(page: str, city: str, source: str, url: str, now: float) -> list[Appointment]:
    text = visible_text(page)
    section = text[_orig_index(text, "takip edilen ülkeler"):_orig_index(text, "Diğer şehirler")]
    checked = ""
    m = re.search(r"Son kontrol:\s*([^.]+)", text)
    if m:
        checked = _relative_to_iso(m.group(1), now)
    chunks = [c.strip() for c in FLAG_RE.split(section)[1:] if c.strip()]
    out = []
    for chunk in chunks:
        name, rest = _split_country(chunk)
        status, date = classify_status(rest)
        out.append(Appointment(source=source, country="tur", mission=name, center=city,
                               visa_category="", visa_type="", status=status, date=date,
                               link=url, checked_at=checked))
    if not out:
        raise ValueError("Visa Catcher sayfasında hiç ülke bulunamadı")
    return out


def _orig_index(text: str, needle: str) -> int:
    i = text.find(needle)
    if i < 0:
        i = text.lower().find(needle.lower())
    if i < 0:
        raise ValueError(f"Visa Catcher sayfa yapısı değişmiş ('{needle}' bulunamadı)")
    return i


def fetch_visacatcher(src: dict, timeout: float, now: float) -> list[Appointment]:
    return parse_visacatcher(http_get(src["url"], timeout), src.get("city", ""),
                             src.get("name", src["url"]), src["url"], now)


# ---------------------------------------------------------------------------
# Kaynak: vizetakip.app (bulunan randevuların akışı)
# ---------------------------------------------------------------------------


@dataclass
class FoundSlot:
    country: str
    city: str
    visa_type: str
    date: str
    found_at: str


def parse_vizetakip(page: str) -> list[FoundSlot]:
    if "appt-grid" not in page:
        raise ValueError("vizetakip.app sayfa yapısı değişmiş (appt-grid yok)")

    def grab(pattern: str, block: str) -> str:
        m = re.search(pattern, block, re.S)
        return html.unescape(re.sub(r"<[^>]+>", " ", m.group(1))).strip() if m else ""

    out = []
    for block in re.findall(r'<article class="appt-card">(.*?)</article>', page, re.S):
        out.append(FoundSlot(
            country=grab(r'class="appt-country"[^>]*>(.*?)</a>', block),
            city=grab(r'class="appt-city"[^>]*>(.*?)</span>', block),
            visa_type=grab(r'class="appt-type"[^>]*>(.*?)</span>', block),
            date=re.sub(r"\s+", " ", grab(r'class="appt-date-row"[^>]*>(.*?)</div>', block)),
            found_at=(re.search(r'class="appt-meta".*?<time datetime="([^"]+)"', block, re.S)
                      or [None, ""])[1],
        ))
    return out


def feed_alerts(src: dict, slots: list[FoundSlot], targets: list["Target"], state: dict,
                now: float) -> tuple[list[Alert], dict]:
    """Son görülen zamandan sonra bulunan ve hedefe uyan randevular için bildirim üretir."""
    first_run = "last_seen" not in state
    # İlk çalışmada eski geçmişi bildirme; sadece son 30 dakikada bulunanlar.
    last_seen = state.get("last_seen") or datetime.fromtimestamp(now - 1800, timezone.utc).isoformat()
    last_ts = age_hours(last_seen, now)
    newest = last_seen
    groups: dict[tuple[str, str], list[FoundSlot]] = {}
    for s in slots:
        age = age_hours(s.found_at, now)
        if age is None or last_ts is None or age >= last_ts:
            continue
        if age_hours(newest, now) is None or age < age_hours(newest, now):
            newest = s.found_at
        probe = Appointment(source="", country="tur", mission=s.country, center=s.city,
                            visa_category="", visa_type=s.visa_type, status="open")
        if any(t.matches(probe) for t in targets):
            groups.setdefault((s.country, s.city), []).append(s)
    alerts = []
    for (country, city), items in groups.items():
        alerts.append(Alert(
            target=country_label(country),
            title=f"{country_label(country)} - {city}: YENİ RANDEVU BULUNDU",
            details=[f"{s.visa_type}: {s.date}" for s in items] + [f"Kaynak: {src.get('name')}"],
            link=src["url"],
        ))
    if first_run:
        log(f"{src.get('name')}: ilk çalışma, geçmiş kayıtlar atlandı")
    return alerts, {"last_seen": newest}


# ---------------------------------------------------------------------------
# Durum (daha önce bildirilenleri hatırlamak için)
# ---------------------------------------------------------------------------


def load_state(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True))
    tmp.replace(path)


# ---------------------------------------------------------------------------
# Bildirim kanalları
# ---------------------------------------------------------------------------


def render(alerts: list[Alert]) -> tuple[str, str]:
    targets = sorted({a.target for a in alerts})
    subject = f"🔔 Vize randevusu: {', '.join(targets)}"
    lines = []
    for a in alerts:
        lines.append(("⏰ HATIRLATMA - " if a.reminder else "") + a.title)
        lines.extend(f"  • {d}" for d in a.details if d)
        if a.link:
            lines.append(f"  → {a.link}")
        lines.append("")
    lines.append("Randevular çok hızlı doluyor, hemen giriş yapmayı deneyin!")
    return subject, "\n".join(lines)


def send_email(subject: str, body: str) -> bool:
    user = os.environ.get("SMTP_USER")
    password = os.environ.get("SMTP_PASSWORD")
    to = os.environ.get("MAIL_TO") or user
    if not (user and password and to):
        return False
    host = os.environ.get("SMTP_HOST") or "smtp.gmail.com"
    port = int(os.environ.get("SMTP_PORT") or 465)
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = os.environ.get("MAIL_FROM") or user
    msg["To"] = to
    # Outlook'ta "Yüksek önem" (kırmızı ünlem) olarak görünsün.
    msg["Importance"] = "High"
    msg["X-Priority"] = "1"
    msg.set_content(body)
    ctx = ssl.create_default_context()
    if port == 465:
        with smtplib.SMTP_SSL(host, port, context=ctx, timeout=30) as s:
            s.login(user, password)
            s.send_message(msg)
    else:
        with smtplib.SMTP(host, port, timeout=30) as s:
            s.starttls(context=ctx)
            s.login(user, password)
            s.send_message(msg)
    return True


def send_telegram(subject: str, body: str) -> bool:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_ids = os.environ.get("TELEGRAM_CHAT_ID")
    if not (token and chat_ids):
        return False
    for chat_id in (c.strip() for c in chat_ids.split(",") if c.strip()):
        data = urllib.parse.urlencode(
            {"chat_id": chat_id, "text": f"{subject}\n\n{body}"[:4000]}
        ).encode()
        http_post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data,
            {"Content-Type": "application/x-www-form-urlencoded"},
        )
    return True


def send_ntfy(subject: str, body: str, link: str = "") -> bool:
    topic = os.environ.get("NTFY_TOPIC")
    if not topic:
        return False
    server = (os.environ.get("NTFY_SERVER") or "https://ntfy.sh").rstrip("/")
    headers = {
        # HTTP başlıkları latin-1 olmalı; ntfy RFC 2047 kodlamasını destekler.
        "Title": "=?UTF-8?B?" + base64.b64encode(subject.encode()).decode() + "?=",
        "Priority": "urgent",
        "Tags": "rotating_light",
    }
    if link:
        headers["Click"] = link
    if os.environ.get("NTFY_TOKEN"):
        headers["Authorization"] = f"Bearer {os.environ['NTFY_TOKEN']}"
    http_post(f"{server}/{topic}", body.encode(), headers)
    return True


def send_github_issue(subject: str, body: str) -> bool:
    """Repoda issue açar; GitHub repo sahibine e-posta bildirimi gönderir."""
    if norm(os.environ.get("GITHUB_ISSUE_NOTIFY", "")) not in ("1", "true", "yes"):
        return False
    token = os.environ.get("GITHUB_TOKEN")
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not (token and repo):
        return False
    user = os.environ.get("NOTIFY_GITHUB_USER") or os.environ.get("GITHUB_REPOSITORY_OWNER", "")
    payload = {"title": subject[:250], "body": (f"@{user}\n\n" if user else "") + body}
    if user:
        payload["assignees"] = [user]
    http_post(
        f"https://api.github.com/repos/{repo}/issues",
        json.dumps(payload).encode(),
        {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "User-Agent": "vize-randevu-takip",
        },
    )
    return True


def notify(subject: str, body: str, link: str = "") -> int:
    """Tüm yapılandırılmış kanallara gönderir; başarılı kanal sayısını döner."""
    sent = 0
    for name, fn in (
        ("E-posta", lambda: send_email(subject, body)),
        ("Telegram", lambda: send_telegram(subject, body)),
        ("ntfy", lambda: send_ntfy(subject, body, link)),
        ("GitHub issue", lambda: send_github_issue(subject, body)),
    ):
        try:
            if fn():
                log(f"Bildirim gönderildi: {name}")
                sent += 1
        except Exception as e:  # noqa: BLE001 - bir kanal diğerini engellemesin
            log(f"HATA: {name} bildirimi gönderilemedi: {type(e).__name__}: {e}")
    if sent == 0:
        log("UYARI: Hiçbir bildirim kanalı çalışmadı (secret'ları kontrol edin).")
    return sent


# ---------------------------------------------------------------------------
# Ana kontrol
# ---------------------------------------------------------------------------


def load_config(path: Path) -> dict:
    with open(path, "rb") as f:
        return tomllib.load(f)


def evaluate(
    appts: list[Appointment], cfg: dict, state: dict, now: float
) -> tuple[list[Alert], dict]:
    """API randevularını hedeflerle karşılaştırır, yeni bildirimleri üretir."""
    settings = cfg.get("settings", {})
    countries = {norm(c) for c in _as_list(settings.get("source_country", ["tur"]))}
    wanted = {norm(s) for s in settings.get("notify_statuses", ["open", "waitlist_open"])}
    remind_s = float(settings.get("remind_every_hours", 0)) * 3600
    remind_statuses = {norm(x) for x in settings.get("remind_statuses", ["open"])}
    targets = [Target(**t) for t in cfg.get("targets", [])]
    max_age = float(settings.get("max_age_hours", 0))

    # Aynı randevu birden çok kaynakta varsa "açık" olan kaydı kullan.
    merged: dict[str, tuple[Appointment, Target]] = {}
    for a in appts:
        if countries and norm(a.country) not in countries:
            continue
        target = next((t for t in targets if t.matches(a)), None)
        if target is None:
            continue
        age = age_hours(a.checked_at, now)
        if max_age > 0 and age is not None and age > max_age and a.status in wanted:
            continue  # kaynak bu randevuyu uzun süredir kontrol etmemiş, güvenilmez
        if a.key not in merged or (a.status in wanted and merged[a.key][0].status not in wanted):
            merged[a.key] = (a, target)

    old = state.get("appointments", {})
    new: dict[str, dict] = {}
    alerts: list[Alert] = []
    for key, (a, target) in merged.items():
        prev = old.get(key, {})
        entry = {"status": a.status, "date": a.date, "notified_at": None}
        if a.status in wanted:
            last = prev.get("notified_at") if prev.get("status") in wanted else None
            first = last is None
            remind = (not first and remind_s > 0 and a.status in remind_statuses
                      and now - float(last) >= remind_s)
            entry["notified_at"] = last
            if first or remind:
                label = STATUS_LABELS.get(a.status, a.status.upper())
                alerts.append(
                    Alert(
                        target=country_label(a.mission),
                        title=f"{country_label(a.mission)} - {a.center or '?'}: {label}",
                        details=[
                            f"Kategori: {a.visa_category}" if a.visa_category else "",
                            f"Vize türü: {a.visa_type}" if a.visa_type else "",
                            f"İlk müsait tarih: {a.date}" if a.date else "",
                            f"Kaynak: {a.source}",
                        ],
                        link=a.link,
                        reminder=remind,
                    )
                )
                entry["notified_at"] = now
        new[key] = entry
    return alerts, new


def _as_list(v: object) -> list:
    return v if isinstance(v, list) else [v]


def run_once(config_path: Path, state_path: Path) -> int:
    cfg = load_config(config_path)
    settings = cfg.get("settings", {})
    timeout = float(settings.get("timeout", 30))
    state = load_state(state_path)
    before = copy.deepcopy(state)
    now = time.time()

    appts: list[Appointment] = []
    ok_sources = 0
    total_sources = 0
    errors: list[str] = []

    for src in cfg.get("api", []):
        if not src.get("enabled", True):
            continue
        total_sources += 1
        try:
            got = fetch_api(src, timeout)
            appts.extend(got)
            ok_sources += 1
            log(f"{src['name']}: {len(got)} kayıt alındı")
        except Exception as e:  # noqa: BLE001
            errors.append(f"{src.get('name')}: {type(e).__name__}: {e}")
            log(f"HATA {src.get('name')}: {type(e).__name__}: {e}")

    for src in cfg.get("visacatcher", []):
        if not src.get("enabled", True):
            continue
        total_sources += 1
        try:
            got = fetch_visacatcher(src, timeout, now)
            appts.extend(got)
            ok_sources += 1
            log(f"{src['name']}: {len(got)} ülke okundu")
        except Exception as e:  # noqa: BLE001
            errors.append(f"{src.get('name')}: {type(e).__name__}: {e}")
            log(f"HATA {src.get('name')}: {type(e).__name__}: {e}")

    alerts: list[Alert] = []
    feed_state = state.setdefault("feeds", {})
    targets = [Target(**t) for t in cfg.get("targets", [])]
    for src in cfg.get("feed", []):
        if not src.get("enabled", True):
            continue
        total_sources += 1
        name = src.get("name", src["url"])
        try:
            slots = parse_vizetakip(http_get(src["url"], timeout))
            got, feed_state[name] = feed_alerts(src, slots, targets, feed_state.get(name, {}), now)
            alerts.extend(got)
            ok_sources += 1
            log(f"{name}: {len(slots)} kayıt okundu, {len(got)} yeni eşleşme")
        except Exception as e:  # noqa: BLE001
            errors.append(f"{name}: {type(e).__name__}: {e}")
            log(f"HATA {name}: {type(e).__name__}: {e}")

    if appts:
        status_alerts, appt_state = evaluate(appts, cfg, state, now)
        alerts.extend(status_alerts)
        state["appointments"] = appt_state
        for key, v in appt_state.items():
            log(f"  {key} -> {v['status']} {v.get('date') or ''}".rstrip())

    page_state = state.setdefault("pages", {})
    for src in cfg.get("page", []):
        if not src.get("enabled", False):
            continue
        total_sources += 1
        name = src.get("name", src["url"])
        try:
            text = visible_text(http_get(src["url"], timeout))
            alert, new, why = check_page(src, text, page_state.get(name))
            page_state[name] = new
            ok_sources += 1
            log(f"{name}: kontrol edildi ({'UYARI' if alert else 'değişiklik yok'})")
            if alert:
                alerts.append(
                    Alert(
                        target=src.get("target", name),
                        title=f"{src.get('target', '')} - {name}: {why}".lstrip(" -"),
                        details=[],
                        link=src["url"],
                    )
                )
        except Exception as e:  # noqa: BLE001
            errors.append(f"{name}: {type(e).__name__}: {e}")
            log(f"HATA {name}: {type(e).__name__}: {e}")

    # Kaynakların hepsi uzun süre çalışmazsa kullanıcıyı uyar (bir kez).
    health = state.setdefault("health", {"fail_streak": 0, "alerted": False})
    limit = int(settings.get("error_alert_after", 18))
    if total_sources and ok_sources == 0:
        health["fail_streak"] += 1
        if limit > 0 and health["fail_streak"] >= limit and not health["alerted"]:
            notify(
                "⚠️ Vize takip sistemi veri alamıyor",
                "Son "
                f"{health['fail_streak']} kontrolde hiçbir kaynaktan veri alınamadı.\n"
                "Randevu açılırsa haber veremeyebilirim. Hatalar:\n\n"
                + "\n".join(f"• {e}" for e in errors)
                + "\n\nconfig.toml içindeki kaynakları güncellemeniz gerekebilir.",
            )
            health["alerted"] = True
    else:
        if health.get("alerted"):
            notify("✅ Vize takip sistemi tekrar çalışıyor", "Kaynaklardan veri alınabiliyor.")
        health["fail_streak"] = 0
        health["alerted"] = False
    if total_sources == 0:
        log("UYARI: config.toml içinde etkin kaynak yok.")

    if alerts:
        subject, body = render(alerts)
        log(f"{len(alerts)} yeni bildirim:\n{body}")
        link = next((a.link for a in alerts if a.link), "")
        if notify(subject, body, link) == 0:
            # Hiçbir kanal çalışmadıysa bir sonraki kontrolde tekrar denensin.
            for section in ("appointments", "pages", "feeds"):
                if section in before:
                    state[section] = before[section]
                else:
                    state.pop(section, None)
    elif ok_sources:
        log("Takip edilen ülkeler için yeni randevu yok.")
    else:
        log("Hiçbir kaynaktan veri alınamadı; bir sonraki kontrolde tekrar denenecek.")

    state["last_run"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    save_state(state_path, state)
    # Kaynak hataları yukarıdaki sağlık uyarısıyla bildiriliyor; burada hata kodu
    # dönülürse GitHub her 10 dakikada bir "workflow failed" e-postası atar.
    return 0


def coverage_report(config_path: Path) -> str:
    """Kaynakların hangi ülke/şehir için ne kadar güncel veri verdiğini özetler."""
    cfg = load_config(config_path)
    settings = cfg.get("settings", {})
    timeout = float(settings.get("timeout", 30))
    countries = {norm(c) for c in _as_list(settings.get("source_country", ["tur"]))}
    targets = [Target(**t) for t in cfg.get("targets", [])]
    now = time.time()
    out = ["# Kaynak kapsam raporu", ""]
    sources = [(s, fetch_api) for s in cfg.get("api", [])] + [
        (s, lambda src, t: fetch_visacatcher(src, t, now)) for s in cfg.get("visacatcher", [])]
    for src, fetch in sources:
        if not src.get("enabled", True):
            continue
        out.append(f"## {src['name']}")
        try:
            appts = fetch(src, timeout)
        except Exception as e:  # noqa: BLE001
            out += [f"HATA: {type(e).__name__}: {e}", ""]
            continue
        mine = [a for a in appts if not countries or norm(a.country) in countries]
        out.append(f"Toplam kayıt: {len(appts)}, başvuru ülkesi eşleşen: {len(mine)}")
        out += ["", "| Hedefe uyuyor | Ülke | Merkez | Durum | Kontrol (saat önce) | Tarih |",
                "|---|---|---|---|---|---|"]
        for a in sorted(mine, key=lambda a: (norm(a.mission), norm(a.center), a.key)):
            age = age_hours(a.checked_at, now)
            hit = "✅" if any(t.matches(a) for t in targets) else ""
            age_s = f"{age:.1f}" if age is not None else "?"
            out.append(
                f"| {hit} | {country_label(a.mission)} | {a.center} | {a.status} | {age_s} | {a.date} |"
            )
        out.append("")
    for src in cfg.get("feed", []):
        if not src.get("enabled", True):
            continue
        out.append(f"## {src['name']}")
        try:
            slots = parse_vizetakip(http_get(src["url"], timeout))
        except Exception as e:  # noqa: BLE001
            out += [f"HATA: {type(e).__name__}: {e}", ""]
            continue
        out += [f"Son bulunan {len(slots)} randevu:", "", "| Hedefe uyuyor | Ülke | Şehir | Tür | Tarih | Bulunma |",
                "|---|---|---|---|---|---|"]
        for sl in slots:
            probe = Appointment(source="", country="tur", mission=sl.country, center=sl.city,
                                visa_category="", visa_type=sl.visa_type, status="open")
            hit = "✅" if any(t.matches(probe) for t in targets) else ""
            out.append(f"| {hit} | {sl.country} | {sl.city} | {sl.visa_type} | {sl.date} | {sl.found_at} |")
        out.append("")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    ap.add_argument("--state", type=Path, default=DEFAULT_STATE)
    ap.add_argument("--loop", type=int, metavar="SANIYE", help="sürekli çalış, her N saniyede kontrol et")
    ap.add_argument("--max-minutes", type=float, metavar="DAKIKA",
                    help="--loop ile: bu kadar dakika sonra dur (GitHub iş süresi sınırı için)")
    ap.add_argument("--test-notify", action="store_true", help="test bildirimi gönder ve çık")
    ap.add_argument("--report", action="store_true", help="kaynakların kapsamını raporla ve çık")
    args = ap.parse_args(argv)

    if args.report:
        text = coverage_report(args.config)
        print(text)
        summary = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary:
            with open(summary, "a", encoding="utf-8") as f:
                f.write(text + "\n")
        return 0

    if args.test_notify:
        sent = notify(
            "✅ Vize takip sistemi test bildirimi",
            "Bu bir test mesajıdır. Bunu görüyorsanız, takip edilen Schengen "
            "randevularından biri açıldığında size bu kanaldan haber verilecek.",
        )
        return 0 if sent else 1

    if not args.loop:
        return run_once(args.config, args.state)

    interval = max(60, args.loop)
    deadline = time.time() + args.max_minutes * 60 if args.max_minutes else None
    while True:
        try:
            run_once(args.config, args.state)
        except Exception as e:  # noqa: BLE001
            log(f"Beklenmeyen hata: {type(e).__name__}: {e}")
        if deadline and time.time() + interval > deadline:
            log("Süre doldu, döngü bitiyor.")
            return 0
        time.sleep(interval)


if __name__ == "__main__":
    sys.exit(main())
