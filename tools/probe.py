"""Keşif aracı: kaynakların GitHub sunucularından erişilebilir olup olmadığını ve
Kosmos sitesinin arka planda hangi API'leri kullandığını raporlar.

Sadece elle (workflow_dispatch) çalıştırılır; bildirim göndermez.
"""

import json
import sys
import urllib.error
import urllib.request

URLS = [
    "https://api.visasbot.com/api/visa/list",
    "https://api.visasbot.com/",
    "https://api.schengenvisaappointments.com/api/visa-list/?format=json",
    "https://schengenvisaappointments.com/",
    "https://www.kosmosvisa.com/",
    "https://www.kosmosvize.com/",
    "https://www.as-visa.com/",
    "https://visa.vfsglobal.com/tur/tr/fra/",
]
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"


def http_probe():
    print("## HTTP erişim testi")
    for url in URLS:
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
        try:
            with urllib.request.urlopen(req, timeout=25) as r:
                body = r.read(400).decode("utf-8", "replace")
                print(f"- {url} -> {r.status} {r.headers.get('content-type')} | {body[:200]!r}")
        except urllib.error.HTTPError as e:
            print(f"- {url} -> HTTP {e.code} | {e.read(200)!r}")
        except Exception as e:  # noqa: BLE001
            print(f"- {url} -> {type(e).__name__}: {e}")
    print()


def kosmos_probe(start_url):
    from playwright.sync_api import sync_playwright

    print(f"## Tarayıcı ile ağ trafiği: {start_url}")
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(user_agent=UA, locale="tr-TR")

        def on_response(resp):
            rt = resp.request.resource_type
            if rt not in ("xhr", "fetch", "document"):
                return
            snippet = ""
            try:
                if "json" in (resp.headers.get("content-type") or ""):
                    snippet = json.dumps(resp.json(), ensure_ascii=False)[:600]
            except Exception:  # noqa: BLE001
                pass
            print(f"[{rt}] {resp.request.method} {resp.url} -> {resp.status} {snippet}")

        page.on("response", on_response)
        try:
            page.goto(start_url, wait_until="networkidle", timeout=60000)
        except Exception as e:  # noqa: BLE001
            print("goto hata:", e)
        page.screenshot(path="probe/01-home.png", full_page=True)
        print("\n### Ana sayfa linkleri (randevu içerenler)")
        for a in page.query_selector_all("a"):
            href = a.get_attribute("href") or ""
            text = (a.inner_text() or "").strip().replace("\n", " ")
            if any(k in (href + text).lower() for k in ("randevu", "appointment", "booking")):
                print(f"- {text[:60]!r} -> {href}")
        links = [a.get_attribute("href") for a in page.query_selector_all("a")]
        target = next((h for h in links if h and "randevu" in h.lower()), None)
        if target:
            if target.startswith("/"):
                target = start_url.rstrip("/") + target
            print(f"\n### Randevu sayfası açılıyor: {target}")
            try:
                page.goto(target, wait_until="networkidle", timeout=60000)
            except Exception as e:  # noqa: BLE001
                print("goto hata:", e)
            page.wait_for_timeout(4000)
            page.screenshot(path="probe/02-randevu.png", full_page=True)
            for fr in page.frames:
                print(f"frame: {fr.url}")
            print("\n### select kutuları")
            for fr in page.frames:
                for s in fr.query_selector_all("select"):
                    opts = [o.inner_text().strip() for o in s.query_selector_all("option")]
                    print(f"- {fr.url} select#{s.get_attribute('id')} name={s.get_attribute('name')}: {opts[:40]}")
            print("\n### Görünen metin (ilk 3000 karakter)")
            print(page.inner_text("body")[:3000])
        browser.close()


if __name__ == "__main__":
    import os

    os.makedirs("probe", exist_ok=True)
    http_probe()
    for url in sys.argv[1:]:
        try:
            kosmos_probe(url)
        except Exception as e:  # noqa: BLE001
            print("tarayıcı hata:", type(e).__name__, e)
