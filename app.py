from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from curl_cffi import requests as creq
from urllib.parse import quote
import re, time

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

SCRAPER_API_KEY = "1967f7d39ff9d57ff2bcc1f3ed097fb7"

PROFILES = ["chrome", "chrome131", "chrome124", "chrome120", "safari17_0", "edge101"]
cache = {}
CACHE_SURE = 300

YARDIM = {"hata": "Adres bulunamadı", "dogru_kullanim": ["/fund?code=TLY", "/debug?code=TLY"]}


@app.exception_handler(StarletteHTTPException)
async def hata_yakala(request: Request, exc: StarletteHTTPException):
    return JSONResponse(YARDIM, status_code=404)


@app.get("/")
def health():
    return {"status": "ok", "kullanim": "/fund?code=TLY", "debug": "/debug?code=TLY"}


def fetch_direct(code, log):
    url = f"https://fvt.com.tr/fonlar/yatirim-fonlari/{code}"
    try:
        s = creq.Session()
        try:
            r0 = s.get("https://fvt.com.tr/", impersonate="chrome", timeout=15,
                       headers={"Accept-Language": "tr-TR,tr;q=0.9"})
            log["warmup"] = r0.status_code
        except Exception as e:
            log["warmup"] = f"hata: {e}"
        for p in PROFILES:
            try:
                r = s.get(url, impersonate=p, timeout=20,
                          headers={"Accept-Language": "tr-TR,tr;q=0.9"})
                log[f"direct:{p}"] = r.status_code
                if r.status_code == 200 and ("fonKodu" in r.text or "Günün Tahmini" in r.text):
                    return r.text, f"direct:{p}"
            except Exception as e:
                log[f"direct:{p}"] = f"hata: {e}"
    except Exception as e:
        log["direct_genel"] = str(e)
    return None, None


def fetch_scraperapi(code, log):
    if not SCRAPER_API_KEY:
        log["scraperapi"] = "ANAHTAR BOS"
        return None, None
    target = f"https://fvt.com.tr/fonlar/yatirim-fonlari/{code}"    
    api = ("https://api.scraperapi.com?api_key=" + SCRAPER_API_KEY +
           "&url=" + quote(target, safe="") + "&country_code=tr&premium=true")
    try:
        r = creq.get(api, timeout=90, headers={"Accept": "*/*"})
        log["scraperapi_status"] = r.status_code
        log["scraperapi_uzunluk"] = len(r.text)
        log["scraperapi_baslangic"] = r.text[:250]
        if r.status_code == 200 and ("fonKodu" in r.text or "Günün Tahmini" in r.text):
            return r.text, "scraperapi:tr"
    except Exception as e:
        log["scraperapi_hata"] = str(e)
    return None, None


@app.get("/debug")
def debug(code: str = "TLY"):
    code = code.upper().strip()
    log = {"key_ilk6": SCRAPER_API_KEY[:6] + "..." if SCRAPER_API_KEY else "BOS"}
    html, kaynak = fetch_direct(code, log)
    if html:
        log["SONUC"] = "BASARILI: " + kaynak
        return JSONResponse(log)
    html, kaynak = fetch_scraperapi(code, log)
    if html:
        log["SONUC"] = "BASARILI: " + kaynak
        return JSONResponse(log)
    log["SONUC"] = "BASARISIZ: detaylar yukarida"
    return JSONResponse(log)


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

    log = {}
    html, kaynak = fetch_direct(code, log)
    if not html:
        html, kaynak = fetch_scraperapi(code, log)

    if not html:
        return JSONResponse({
            "error": "Tüm denemeler başarısız",
            "detay": log,
            "ipucu": "Bu detayı aynen sohbete yapıştır",
        }, status_code=502)

    data = parse(html.replace('\\"', '"'), code) or parse(html, code)
    if not data:
        return JSONResponse({"error": "Veri ayrıştırılamadı", "detay": log}, status_code=502)

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
