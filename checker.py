#!/usr/bin/env python3
"""Yunanistan / Macaristan vize randevu takipçisi.

Randevu kaynaklarını kontrol eder, yeni açılan randevu olursa e-posta,
Telegram ve/veya ntfy (telefon bildirimi) ile haber verir.

Sadece Python standart kütüphanesini kullanır.

Kullanım:
    python checker.py                 # bir kez kontrol et
    python checker.py --loop 300      # 5 dakikada bir sürekli kontrol et
    python checker.py --test-notify   # bildirim kanallarını test et
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


def log(msg: str) -> None:
    print(f"[{datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S}Z] {msg}", flush=True)


def norm(text: object) -> str:
    """Küçük harf, Türkçe karakterler sadeleştirilmiş, boşlukları kırpılmış metin."""
    s = str(text or "").replace("İ", "i").replace("I", "ı").lower()
    s = s.translate(str.maketrans("çğıöşü", "cgiosu"))
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s).strip()


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

    def matches(self, appt: Appointment) -> bool:
        if norm(appt.mission) not in {norm(c) for c in self.codes}:
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
    )


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


def notify(subject: str, body: str, link: str = "") -> int:
    """Tüm yapılandırılmış kanallara gönderir; başarılı kanal sayısını döner."""
    sent = 0
    for name, fn in (
        ("E-posta", lambda: send_email(subject, body)),
        ("Telegram", lambda: send_telegram(subject, body)),
        ("ntfy", lambda: send_ntfy(subject, body, link)),
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
    targets = [Target(**t) for t in cfg.get("targets", [])]

    # Aynı randevu birden çok kaynakta varsa "açık" olan kaydı kullan.
    merged: dict[str, tuple[Appointment, Target]] = {}
    for a in appts:
        if countries and norm(a.country) not in countries:
            continue
        target = next((t for t in targets if t.matches(a)), None)
        if target is None:
            continue
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
            remind = not first and remind_s > 0 and now - float(last) >= remind_s
            entry["notified_at"] = last
            if first or remind:
                label = STATUS_LABELS.get(a.status, a.status.upper())
                alerts.append(
                    Alert(
                        target=target.name,
                        title=f"{target.name} - {a.center or '?'}: {label}",
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

    alerts: list[Alert] = []
    if ok_sources:
        alerts, appt_state = evaluate(appts, cfg, state, now)
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
            for section in ("appointments", "pages"):
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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    ap.add_argument("--state", type=Path, default=DEFAULT_STATE)
    ap.add_argument("--loop", type=int, metavar="SANIYE", help="sürekli çalış, her N saniyede kontrol et")
    ap.add_argument("--test-notify", action="store_true", help="test bildirimi gönder ve çık")
    args = ap.parse_args(argv)

    if args.test_notify:
        sent = notify(
            "✅ Vize takip sistemi test bildirimi",
            "Bu bir test mesajıdır. Bunu görüyorsanız, Yunanistan veya Macaristan "
            "randevusu açıldığında size bu kanaldan haber verilecek.",
        )
        return 0 if sent else 1

    if not args.loop:
        return run_once(args.config, args.state)

    while True:
        try:
            run_once(args.config, args.state)
        except Exception as e:  # noqa: BLE001
            log(f"Beklenmeyen hata: {type(e).__name__}: {e}")
        time.sleep(max(60, args.loop))


if __name__ == "__main__":
    sys.exit(main())
