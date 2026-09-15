from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from curl_cffi import requests as creq
import re, time

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

cache = {}  # 30 saniyelik önbellek — FVT'ye daha az istek gider

YARDIM = {
    "hata": "Adres bulunamadı",
    "dogru_kullanim": "https://BU-ADRES.onrender.com/fund?code=TLY",
}


# Yanlış adrese gelen herkese yardım mesajı göster
@app.exception_handler(StarletteHTTPException)
async def hata_yakala(request: Request, exc: StarletteHTTPException):
    return JSONResponse(YARDIM, status_code=404)


@app.get("/")
def health():
    return {"status": "ok", "kullanim": "/fund?code=TLY"}


# 4 adres çeşidini de kabul eder: /fund, /fund/, /api/fund, /api/fund/
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

    # Aynı fon 30 sn içinde tekrar istendiyse önbellekten ver
    if code in cache and time.time() - cache[code][0] < 30:
        return JSONResponse(cache[code][1])

    url = f"https://fvt.com.tr/fonlar/yatirim-fonlari/{code}"
    try:
        r = creq.get(url, impersonate="chrome", timeout=25,
                     headers={"Accept-Language": "tr-TR,tr;q=0.9"})
    except Exception as e:
        return JSONResponse({"error": f"Bağlantı hatası: {e}"}, status_code=502)

    if r.status_code != 200:
        return JSONResponse({"error": f"FVT HTTP {r.status_code}"}, status_code=502)

    html = r.text.replace('\\"', '"')
    data = parse(html, code)
    if not data:
        return JSONResponse({"error": "Veri ayrıştırılamadı"}, status_code=502)

    cache[code] = (time.time(), data)
    return JSONResponse(data)


def parse(html, fallback):
    # 1) Gömülü Next.js JSON verisinden çek (en güvenilir)
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
    # 2) Yedek: görünen HTML'den çek
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
