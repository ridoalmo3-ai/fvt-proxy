from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from curl_cffi import requests as creq
from urllib.parse import quote
from fastapi import Request
import re, time

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ▼▼ ScraperAPI anahtarını tırnakların arasına yapıştır (yoksa boş bırak) ▼▼
SCRAPER_API_KEY = "1967f7d39ff9d57ff2bcc1f3ed097fb7"
# ▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲

PROFILES = ["chrome", "chrome131", "chrome124", "chrome120", "safari17_0", "edge101"]
cache = {}
CACHE_SURE = 300  # 5 dakika — kotayı korur, dokunma

YARDIM = {"hata": "Adres bulunamadı", "dogru_kullanim": "/fund?code=TLY"}


@app.exception_handler(StarletteHTTPException)
async def hata_yakala(request: Request, exc: StarletteHTTPException):
    return JSONResponse(YARDIM, status_code=404)


@app.get("/")
def health():
    return {"status": "ok", "kullanim": "/fund?code=TLY"}


def fetch_direct(code):
    """FVT'ye doğrudan istek — birden fazla Chrome kılığı dener"""
    url = f"https://fvt.com.tr/fonlar/yatirim-fonlari/{code}"
    try:
        s = creq.Session()
        # Önce ana sayfayı ziyaret et: çerez alır, gerçek kullanıcı gibi görünür
        try:
            s.get("https://fvt.com.tr/", impersonate="chrome", timeout=15,
                  headers={"Accept-Language": "tr-TR,tr;q=0.9"})
        except Exception:
            pass
        for p in PROFILES:
            try:
                r = s.get(url, impersonate=p, timeout=20,
                          headers={"Accept-Language": "tr-TR,tr;q=0.9"})
                if r.status_code == 200 and ("fonKodu" in r.text or "Günün Tahmini" in r.text):
                    return r.text, f"direct:{p}"
            except Exception:
                continue
    except Exception:
        pass
    return None, None


def fetch_scraperapi(code):
    """Anahtar varsa: istek Türkiye'deki bir IP üzerinden gider"""
    if not SCRAPER_API_KEY:
        return None, None
    target = f"https://fvt.com.tr/fonlar/yatirim-fonlari/{code}"
    api = ("https://api.scraperapi.com?api_key=" + SCRAPER_API_KEY +
           "&url=" + quote(target, safe="") + "&country_code=tr")
    try:
        r = creq.get(api, timeout=90, headers={"Accept": "text/html"})
        if r.status_code == 200 and ("fonKodu" in r.text or "Günün Tahmini" in r.text):
            return r.text, "scraperapi:tr"
    except Exception:
        pass
    return None, None


@app.get("/fund")
@app.get("/fund/")
@app.get("/api/fund")
@app.get("/api/fund/")
def fund(code: str = ""):
    code = code.upper().strip()
    if not re.fullmatch(r"[A-Z0-9]{2,5}", code):
        return JSONResponse(
            {"error": "Fon kodu eksik veya geçersiz. Doğru kullanım: /fund?code=TLY"},
            status_code=400)

    now = time.time()
    if code in cache and now - cache[code][0] < CACHE_SURE:
        d = dict(cache[code][1])
        d["onbellek"] = True
        return JSONResponse(d)

    html, kaynak = fetch_direct(code)          # 1. deneme: doğrudan (ücretsiz)
    if not html:
        html, kaynak = fetch_scraperapi(code)  # 2. deneme: Türk IP'si ile

    if not html:
        return JSONResponse({
            "error": "FVT tüm denemeleri engelledi. app.py'nin üstündeki "
                     "SCRAPER_API_KEY alanına anahtar ekleyip tekrar dene."
        }, status_code=502)

    data = parse(html.replace('\\"', '"'), code) or parse(html, code)
    if not data:
        return JSONResponse({"error": "Veri ayrıştırılamadı"}, status_code=502)

    data["kaynak"] = kaynak
    cache[code] = (now, data)
    return JSONResponse(data)


def parse(html, fallback):
    m = re.search(
        r'"fonKodu":"([^"]+)","fonAdi":"([^"]+)","getiri":"(-?[\d.]+)"'
        r'[\s\S]*?"fiyat":"([\d.]+)","sonGuncelleme":"([^"]+)"'
        r'[\s\S]*?"kategori":"([^"]+)"[\s\S]*?"toplamDeger":"([\d.]+)"'
        r',"yatirimci":"(\d+)[\s\S]*?"risk":"(\d)"',
        html)
    if m:
        return {
            "kod": m.group(1), "ad": m.group(2),
            "getiri": float(m.group(3)), "fiyat": float(m.group(4)),
            "guncellemeRaw": m.group(5), "kategori": m.group(6),
            "toplamDeger": float(m.group(7)),
            "yatirimci": int(m.group(8)), "risk": int(m.group(9)),
        }
    g = re.search(r"Günün Tahmini[\s\S]{0,800}?(-?[\d.,]+)\s*(?:<!-- -->)?\s*%", html)
    p = re.search(r"₺(?:<!-- -->)?\s*([\d.,]+)", html)
    t = re.search(r"<title>([^<|]+)\|", html)
    if p and g:
        return {
            "kod": fallback,
            "ad": t.group(1).strip() if t else fallback,
            "getiri": float(g.group(1).replace(",", ".")),
            "fiyat": float(p.group(1).replace(".", "").replace(",", ".")),
            "guncellemeRaw": None, "kategori": "-",
            "toplamDeger": 0, "yatirimci": 0, "risk": "-",
        }
    return None
