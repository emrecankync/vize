import json
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
        "last_checked_at": "2026-09-25T10:00:00Z",
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

    def test_ignores_other_countries(self):
        alerts, st = self.ev([rec(mission="deu"), rec(country="gbr"), rec(mission="hun", status="closed")])
        self.assertEqual(alerts, [])
        self.assertEqual(len(st), 1)  # sadece hun/closed takip ediliyor

    def test_closed_then_open_alerts_again(self):
        _, st = self.ev([rec()])
        _, st = self.ev([rec(status="closed")], st)
        alerts, _ = self.ev([rec()], st)
        self.assertEqual(len(alerts), 1)

    def test_waitlist_and_hungary(self):
        alerts, _ = self.ev([rec(mission="HUN", center="Ankara", status="waitlist_open")])
        self.assertEqual(alerts[0].target, "Macaristan")
        self.assertIn("BEKLEME", alerts[0].title)

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


class RunOnceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = Path(self.tmp.name) / "state.json"

    def tearDown(self):
        self.tmp.cleanup()

    def run_with(self, payload, sent=1):
        def fake_get(url, timeout):
            if isinstance(payload, Exception):
                raise payload
            return json.dumps(payload)

        with mock.patch.object(checker, "http_get", fake_get), \
                mock.patch.object(checker, "notify", return_value=sent) as n:
            rc = checker.run_once(checker.DEFAULT_CONFIG, self.state)
        return rc, n

    def test_end_to_end(self):
        rc, n = self.run_with(visasbot(rec(mission="hun", center="Istanbul")))
        self.assertEqual(rc, 0)
        n.assert_called_once()
        subject, body = n.call_args.args[:2]
        self.assertIn("Macaristan", subject)
        self.assertIn("Istanbul", body)
        rc, n = self.run_with(visasbot(rec(mission="hun", center="Istanbul")))
        n.assert_not_called()

    def test_failed_notification_retries_next_run(self):
        self.run_with(visasbot(rec()), sent=0)
        _, n = self.run_with(visasbot(rec()))
        n.assert_called_once()

    def test_all_sources_down_alerts_once(self):
        limit = CONFIG["settings"]["error_alert_after"]
        calls = 0
        for _ in range(limit + 3):
            rc, n = self.run_with(OSError("down"))
            self.assertEqual(rc, 0)
            calls += n.call_count
        self.assertEqual(calls, 1)
        _, n = self.run_with(visasbot())
        self.assertIn("tekrar çalışıyor", n.call_args.args[0])


class NotifierTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
