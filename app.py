from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from curl_cffi import requests as creq
import re, time

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

cache = {}  # 30 saniyelik bellek önbelleği — FVT'ye daha az istek gider


@app.get("/")
def health():
    return {"status": "ok", "kullanim": "/fund?code=TLY"}


@app.get("/fund")
def fund(code: str):
    code = code.upper().strip()
    if not re.fullmatch(r"[A-Z0-9]{2,5}", code):
        raise HTTPException(400, "Geçersiz fon kodu")

    # Aynı fon 30 sn içinde tekrar istendiyse önbellekten ver
    if code in cache and time.time() - cache[code][0] < 30:
        return JSONResponse(cache[code][1])

    url = f"https://fvt.com.tr/fonlar/yatirim-fonlari/{code}"
    try:
        # impersonate="chrome" = gerçek Chrome parmak izi, Cloudflare bunu geçemez
        r = creq.get(url, impersonate="chrome", timeout=25,
                     headers={"Accept-Language": "tr-TR,tr;q=0.9"})
    except Exception as e:
        raise HTTPException(502, f"Bağlantı hatası: {e}")

    if r.status_code != 200:
        raise HTTPException(502, f"FVT HTTP {r.status_code}")

    html = r.text.replace('\\"', '"')
    data = parse(html, code)
    if not data:
        raise HTTPException(502, "Veri ayrıştırılamadı")

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
    g = re.search(r"Günün Tahmini[\s\S]{0,800}?([\d.,]+)\s*(?:<!-- -->)?\s*%", html)
    p = re.search(r"₺(?:<!-- -->)?\s*([\d.,]+)", html)
    t = re.search(r"<title>([^<|]+)\|", html)
    if p and g:
        def num(s):
            return float(s.replace(".", "").replace(",", "."))
        return {
            "kod": fallback,
            "ad": t.group(1).strip() if t else fallback,
            "getiri": num(g.group(1)), "fiyat": num(p.group(1)),
            "guncellemeRaw": None, "kategori": "-",
            "toplamDeger": 0, "yatirimci": 0, "risk": "-",
        }
    return None
