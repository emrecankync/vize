import json
from datetime import datetime, timezone
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import checker  # noqa: E402

CONFIG = checker.load_config(checker.DEFAULT_CONFIG)


def visasbot(*records):
    return {"success": True, "data": {"visas": list(records)}}


def rec(mission="grc", center="Istanbul", status="open", country="tur", **kw):
    r = {
        "id": 1,
        "country_code": country,
        "mission_code": mission,
        "center": center,
        "visa_category": "Short Term",
        "visa_type": "Tourism",
        "status": status,
        "last_checked_at": datetime.now(timezone.utc).isoformat(),
    }
    r.update(kw)
    return r


class ParseTests(unittest.TestCase):
    def test_visasbot_shape(self):
        appts = [checker.parse_record(r, "x") for r in checker.extract_records(visasbot(rec()))]
        self.assertEqual(appts[0].mission, "grc")
        self.assertEqual(appts[0].status, "open")

    def test_schengenvisaappointments_shape(self):
        payload = [
            {
                "source_country": "Turkiye",
                "mission_country": "Hungary",
                "center_name": "Ankara",
                "visa_category": "Tourism",
                "visa_subcategory": "",
                "appointment_date": "2026-10-12",
                "book_now_link": "https://example.com",
            },
            {"source_country": "Turkiye", "mission_country": "Greece", "center_name": "Izmir",
             "appointment_date": None},
        ]
        appts = [checker.parse_record(r, "x") for r in checker.extract_records(payload)]
        self.assertEqual([a.status for a in appts], ["open", "closed"])

    def test_bad_payload(self):
        with self.assertRaises(ValueError):
            checker.extract_records({"foo": 1})

    def test_norm_turkish(self):
        self.assertEqual(checker.norm("  İSTANBUL  Türkiye "), "istanbul turkiye")


class EvaluateTests(unittest.TestCase):
    def ev(self, records, state=None, now=1000.0, cfg=CONFIG):
        appts = [checker.parse_record(r, "t") for r in records]
        return checker.evaluate(appts, cfg, {"appointments": state or {}}, now)

    def test_new_open_alerts_once(self):
        alerts, st = self.ev([rec()])
        self.assertEqual(len(alerts), 1)
        self.assertIn("Yunanistan", alerts[0].title)
        alerts2, _ = self.ev([rec()], st, now=1060)
        self.assertEqual(alerts2, [])

    def test_ignores_non_schengen_other_city_other_source(self):
        alerts, st = self.ev([
            rec(mission="gbr"),                 # Schengen değil
            rec(mission="fra", center="Ankara"),  # İstanbul değil
            rec(country="gbr"),                 # başka ülkeden başvuru
            rec(mission="nld", status="closed"),
        ])
        self.assertEqual(alerts, [])
        self.assertEqual(len(st), 1)  # sadece nld/closed takip ediliyor

    def test_closed_then_open_alerts_again(self):
        _, st = self.ev([rec()])
        _, st = self.ev([rec(status="closed")], st)
        alerts, _ = self.ev([rec()], st)
        self.assertEqual(len(alerts), 1)

    def test_waitlist_and_country_label(self):
        alerts, _ = self.ev([rec(mission="NLD", center="Istanbul Beyoglu", status="waitlist_open")])
        self.assertEqual(alerts[0].target, "🇳🇱 Hollanda")
        self.assertIn("BEKLEME", alerts[0].title)

    def test_country_names_from_other_api(self):
        alerts, _ = self.ev([{"source_country": "Turkiye", "mission_country": "France",
                              "center_name": "İstanbul", "appointment_date": "2026-11-02"}])
        self.assertEqual(alerts[0].target, "🇫🇷 Fransa")

    def test_stale_open_ignored(self):
        now = 1_800_000_000.0
        stale = datetime.fromtimestamp(now - 13 * 3600, timezone.utc).isoformat()
        fresh = datetime.fromtimestamp(now - 600, timezone.utc).isoformat().replace("+00:00", "Z")
        alerts, _ = self.ev([rec(mission="fra", last_checked_at=stale),
                             rec(mission="ita", last_checked_at=fresh)], now=now)
        self.assertEqual([a.target for a in alerts], ["🇮🇹 İtalya"])

    def test_exclude(self):
        cfg = dict(CONFIG)
        cfg["targets"] = [{"name": "x", "codes": ["schengen"], "exclude": ["Almanya"]}]
        alerts, _ = self.ev([rec(mission="deu"), rec(mission="esp")], cfg=cfg)
        self.assertEqual([a.target for a in alerts], ["🇪🇸 İspanya"])

    def test_reminder(self):
        _, st = self.ev([rec()], now=0)
        alerts, _ = self.ev([rec()], st, now=6 * 3600 + 1)
        self.assertEqual(len(alerts), 1)
        self.assertTrue(alerts[0].reminder)

    def test_duplicate_sources_merge(self):
        alerts, st = self.ev([rec(status="closed"), rec(status="open")])
        self.assertEqual(len(alerts), 1)
        self.assertEqual(len(st), 1)

    def test_city_filter(self):
        cfg = dict(CONFIG)
        cfg["targets"] = [{"name": "Yunanistan", "codes": ["grc"], "cities": ["izmir"]}]
        alerts, _ = self.ev([rec(center="Istanbul"), rec(center="Greece Visa Center İzmir")], cfg=cfg)
        self.assertEqual(len(alerts), 1)
        self.assertIn("İzmir", alerts[0].title)


class PageTests(unittest.TestCase):
    def test_visible_text(self):
        t = checker.visible_text('<html><script>x()</script><input value="zzz"><p>Merhaba &amp; selam</p></html>')
        self.assertEqual(t, "Merhaba & selam")

    def test_disappears(self):
        src = {"mode": "disappears", "patterns": ["Uygun randevu bulunmamaktadır"]}
        a, st, _ = checker.check_page(src, "Şu an uygun randevu bulunmamaktadır.", None)
        self.assertFalse(a)
        a, st, _ = checker.check_page(src, "Randevu al", st)
        self.assertTrue(a)
        a, st, _ = checker.check_page(src, "Randevu al", st)
        self.assertFalse(a)  # tekrar tekrar bildirme

    def test_changes(self):
        src = {"mode": "changes"}
        a, st, _ = checker.check_page(src, "v1", None)
        self.assertFalse(a)
        a, st, _ = checker.check_page(src, "v1", st)
        self.assertFalse(a)
        a, st, _ = checker.check_page(src, "v2", st)
        self.assertTrue(a)


API_ONLY_CONFIG = """
[settings]
source_country = ["tur"]
notify_statuses = ["open", "waitlist_open"]
max_age_hours = 12
remind_every_hours = 6
error_alert_after = 18

[[targets]]
name = "Schengen - İstanbul"
codes = ["schengen"]
cities = ["istanbul"]

[[api]]
name = "visasbot"
url = "https://example.invalid/api"
"""


class RunOnceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = Path(self.tmp.name) / "state.json"
        self.config = Path(self.tmp.name) / "config.toml"
        self.config.write_text(API_ONLY_CONFIG)

    def tearDown(self):
        self.tmp.cleanup()

    def run_with(self, payload, sent=1):
        def fake_get(url, timeout):
            if isinstance(payload, Exception):
                raise payload
            return json.dumps(payload)

        with mock.patch.object(checker, "http_get", fake_get), \
                mock.patch.object(checker, "notify", return_value=sent) as n:
            rc = checker.run_once(self.config, self.state)
        return rc, n

    def test_end_to_end(self):
        rc, n = self.run_with(visasbot(rec(mission="aut", center="Istanbul")))
        self.assertEqual(rc, 0)
        n.assert_called_once()
        subject, body = n.call_args.args[:2]
        self.assertIn("Avusturya", subject)
        self.assertIn("Istanbul", body)
        rc, n = self.run_with(visasbot(rec(mission="aut", center="Istanbul")))
        n.assert_not_called()

    def test_failed_notification_retries_next_run(self):
        self.run_with(visasbot(rec()), sent=0)
        _, n = self.run_with(visasbot(rec()))
        n.assert_called_once()

    def test_all_sources_down_alerts_once(self):
        limit = 18
        calls = 0
        for _ in range(limit + 3):
            rc, n = self.run_with(OSError("down"))
            self.assertEqual(rc, 0)
            calls += n.call_count
        self.assertEqual(calls, 1)
        _, n = self.run_with(visasbot())
        self.assertIn("tekrar çalışıyor", n.call_args.args[0])


    def test_report(self):
        with mock.patch.object(checker, "http_get",
                               lambda u, t: json.dumps(visasbot(rec(mission="fra"), rec(mission="gbr")))):
            text = checker.coverage_report(self.config)
        self.assertIn("🇫🇷 Fransa", text)
        self.assertIn("| ✅ |", text)


FIX = Path(__file__).parent / "fixtures"


class VisaCatcherTests(unittest.TestCase):
    def setUp(self):
        self.now = 1_800_000_000.0
        self.page = (FIX / "visacatcher_istanbul.html").read_text()
        self.appts = checker.parse_visacatcher(self.page, "İstanbul", "vc", "https://x", self.now)

    def test_only_tracked_list_parsed(self):
        names = [a.mission for a in self.appts]
        self.assertEqual(names, ["Belçika", "Çekya", "Fransa", "Hırvatistan", "İsviçre", "Karadağ", "Ukrayna"])

    def test_statuses(self):
        st = {a.mission: (a.status, a.date) for a in self.appts}
        self.assertEqual(st["Belçika"], ("closed", ""))
        self.assertEqual(st["İsviçre"], ("waitlist_open", ""))
        self.assertEqual(st["Fransa"], ("open", "14/10/2026"))

    def test_checked_at_relative(self):
        self.assertAlmostEqual(checker.age_hours(self.appts[0].checked_at, self.now), 5 / 60, places=3)

    def test_evaluate_filters_non_schengen(self):
        alerts, st = checker.evaluate(self.appts, CONFIG, {}, self.now)
        titles = sorted(a.title for a in alerts)
        self.assertEqual(len(alerts), 2)
        self.assertIn("🇫🇷 Fransa - İstanbul: AÇIK", titles)
        self.assertIn("🇨🇭 İsviçre - İstanbul: BEKLEME LİSTESİ AÇIK", titles)
        self.assertFalse(any("Karadağ" in k or "ukrayna" in k for k in st))

    def test_waitlist_not_reminded_but_open_is(self):
        _, st = checker.evaluate(self.appts, CONFIG, {}, self.now)
        later = self.now + 7 * 3600
        appts = checker.parse_visacatcher(self.page, "İstanbul", "vc", "https://x", later)
        alerts, _ = checker.evaluate(appts, CONFIG, {"appointments": st}, later)
        self.assertEqual([a.target for a in alerts], ["🇫🇷 Fransa"])
        self.assertTrue(alerts[0].reminder)

    def test_structure_change_raises(self):
        with self.assertRaises(ValueError):
            checker.parse_visacatcher("<html>bakım</html>", "İstanbul", "vc", "u", self.now)

    def test_unknown_status_not_alerted(self):
        self.assertEqual(checker.classify_status("Yakında"), ("unknown", ""))


class VizetakipTests(unittest.TestCase):
    CARD = (FIX / "vizetakip_card.html").read_text()

    def page(self, *cards):
        return '<div class="appt-grid">' + "".join(cards) + "</div>"

    def card(self, country="İtalya", city="Gaziantep", found="2026-09-24T19:25:50.386Z"):
        c = self.CARD.replace(">İtalya</a>", f">{country}</a>").replace(">Gaziantep<", f">{city}<")
        return c.replace("2026-09-24T19:25:50.386Z", found)

    def test_parse_real_card(self):
        [slot] = checker.parse_vizetakip(self.page(self.CARD))
        self.assertEqual((slot.country, slot.city, slot.visa_type, slot.date),
                         ("İtalya", "Gaziantep", "Turistik", "22/10/2026"))
        self.assertEqual(slot.found_at, "2026-09-24T19:25:50.386Z")

    def test_structure_change_raises(self):
        with self.assertRaises(ValueError):
            checker.parse_vizetakip("<html></html>")

    def test_feed_alerts_new_istanbul_only(self):
        now = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc).timestamp()
        targets = [checker.Target(**t) for t in CONFIG["targets"]]
        src = {"name": "vt", "url": "https://vizetakip.app/"}
        slots = checker.parse_vizetakip(self.page(
            self.card("Fransa", "İstanbul", "2026-09-25T11:55:00Z"),
            self.card("İtalya", "Gaziantep", "2026-09-25T11:56:00Z"),
            self.card("Hollanda", "İstanbul", "2026-09-24T08:00:00Z"),  # eski
        ))
        alerts, st = checker.feed_alerts(src, slots, targets, {}, now)
        self.assertEqual([a.target for a in alerts], ["🇫🇷 Fransa"])
        self.assertEqual(st["last_seen"], "2026-09-25T11:56:00Z")
        alerts, _ = checker.feed_alerts(src, slots, targets, st, now + 600)
        self.assertEqual(alerts, [])


class LoopTests(unittest.TestCase):
    def test_loop_stops_at_deadline(self):
        clock = [1000.0]
        with mock.patch.object(checker, "run_once") as run, \
                mock.patch("time.time", lambda: clock[0]), \
                mock.patch("time.sleep", lambda s: clock.__setitem__(0, clock[0] + s)):
            rc = checker.main(["--loop", "300", "--max-minutes", "16"])
        self.assertEqual(rc, 0)
        self.assertEqual(run.call_count, 4)  # 0., 5., 10. ve 15. dakika; 20. sınırı aşar


class NotifierTests(unittest.TestCase):
    def test_github_issue(self):
        env = {"GITHUB_ISSUE_NOTIFY": "true", "GITHUB_TOKEN": "t",
               "GITHUB_REPOSITORY": "o/r", "GITHUB_REPOSITORY_OWNER": "o"}
        with mock.patch.dict("os.environ", env, clear=True), \
                mock.patch.object(checker, "http_post") as post:
            self.assertEqual(checker.notify("🔔 Vize", "gövde"), 1)
        url, data, headers = post.call_args.args
        self.assertEqual(url, "https://api.github.com/repos/o/r/issues")
        payload = json.loads(data)
        self.assertEqual(payload["title"], "🔔 Vize")
        self.assertEqual(payload["assignees"], ["o"])
        self.assertTrue(payload["body"].startswith("@o"))
        self.assertEqual(headers["Authorization"], "Bearer t")

    def test_github_issue_disabled_without_flag(self):
        env = {"GITHUB_TOKEN": "t", "GITHUB_REPOSITORY": "o/r"}
        with mock.patch.dict("os.environ", env, clear=True):
            self.assertEqual(checker.notify("s", "b"), 0)

    def test_no_channels_configured(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertEqual(checker.notify("s", "b"), 0)

    def test_ntfy_and_telegram_requests(self):
        env = {"NTFY_TOPIC": "abc", "TELEGRAM_BOT_TOKEN": "T", "TELEGRAM_CHAT_ID": "1,2"}
        with mock.patch.dict("os.environ", env, clear=True), \
                mock.patch.object(checker, "http_post") as post:
            self.assertEqual(checker.notify("🔔 Vize: Yunanistan", "gövde", "https://x"), 2)
        urls = [c.args[0] for c in post.call_args_list]
        self.assertEqual(urls.count("https://api.telegram.org/botT/sendMessage"), 2)
        self.assertIn("https://ntfy.sh/abc", urls)
        ntfy_headers = post.call_args_list[-1].args[2]
        ntfy_headers["Title"].encode("latin-1")  # başlık latin-1 olmalı
        self.assertEqual(ntfy_headers["Click"], "https://x")

    def test_email(self):
        env = {"SMTP_USER": "a@gmail.com", "SMTP_PASSWORD": "p"}
        with mock.patch.dict("os.environ", env, clear=True), \
                mock.patch("smtplib.SMTP_SSL") as smtp:
            self.assertEqual(checker.notify("konu", "gövde"), 1)
        msg = smtp.return_value.__enter__.return_value.send_message.call_args.args[0]
        self.assertEqual(msg["To"], "a@gmail.com")
        self.assertEqual(msg["Importance"], "High")

    def test_starttls(self):
        env = {"SMTP_USER": "a@example.com", "SMTP_PASSWORD": "p",
               "SMTP_HOST": "smtp.example.com", "SMTP_PORT": "587"}
        with mock.patch.dict("os.environ", env, clear=True), \
                mock.patch("smtplib.SMTP") as smtp:
            self.assertEqual(checker.notify("konu", "gövde"), 1)
        smtp.assert_called_once_with("smtp.example.com", 587, timeout=30)
        smtp.return_value.__enter__.return_value.starttls.assert_called_once()


if __name__ == "__main__":
    unittest.main()
