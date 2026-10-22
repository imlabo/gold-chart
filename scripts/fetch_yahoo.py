#!/usr/bin/env python3
"""Yahoo Finance から先物・為替の四本値を取得して JSON に書き出す（GitHub Actions 用）。

ブラウザからは Yahoo に直接アクセスできない（CORS）ので、Actions が5分ごとに取得し
`data` ブランチへ置く。ページは raw.githubusercontent.com から読む。
依存ライブラリなし（標準ライブラリのみ）。
"""
import json, os, sys, time, urllib.request, urllib.error

SYMBOLS = {
    "nk":  "NIY=F",   # 日経225先物（CME 円建て、ほぼ24時間）
    "nq":  "NQ=F",    # NASDAQ100先物（CME）
    "jpy": "JPY=X",   # ドル円
    "gc":  "GC=F",    # 金先物（COMEX）
}
# key: (yahoo interval, range)  1000本を目安に取る
INTERVALS = {
    "1":   ("1m",  "7d"),
    "5":   ("5m",  "7d"),
    "15":  ("15m", "30d"),
    "60":  ("60m", "120d"),
    "D":   ("1d",  "5y"),
}
HOST = "https://query1.finance.yahoo.com"
HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
           "Accept": "application/json"}

def get(url, tries=3):
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode("utf-8"))
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError) as e:
            last = e; time.sleep(2 + 2*i)
    raise RuntimeError(f"fetch failed: {url}: {last}")

def chart(sym, iv, rng):
    url = f"{HOST}/v8/finance/chart/{urllib.parse.quote(sym)}?interval={iv}&range={rng}&includePrePost=true&events=none"
    j = get(url)
    res = (j.get("chart") or {}).get("result") or []
    if not res:
        err = (j.get("chart") or {}).get("error")
        raise RuntimeError(f"no result for {sym} {iv}: {err}")
    r = res[0]
    ts = r.get("timestamp") or []
    q = ((r.get("indicators") or {}).get("quote") or [{}])[0]
    o, h, l, c = q.get("open") or [], q.get("high") or [], q.get("low") or [], q.get("close") or []
    bars = []
    for i, t in enumerate(ts):
        try:
            vals = (o[i], h[i], l[i], c[i])
        except IndexError:
            continue
        if any(v is None for v in vals):
            continue
        oo, hh, ll, cc = (float(v) for v in vals)
        bars.append([int(t)*1000, round(oo, 4), round(max(hh, oo, cc), 4), round(min(ll, oo, cc), 4), round(cc, 4)])
    # 同一時刻の重複を除き、時刻順に
    seen = set(); out = []
    for b in sorted(bars, key=lambda x: x[0]):
        if b[0] in seen: continue
        seen.add(b[0]); out.append(b)
    meta = r.get("meta") or {}
    return out[-1000:], {"currency": meta.get("currency"), "exchange": meta.get("exchangeName"),
                         "name": meta.get("shortName") or meta.get("symbol"), "regularMarketPrice": meta.get("regularMarketPrice")}

def main(outdir):
    os.makedirs(outdir, exist_ok=True)
    index = {"updated": int(time.time()*1000), "symbols": {}}
    failures = 0
    for key, ysym in SYMBOLS.items():
        index["symbols"][key] = {"yahoo": ysym, "tf": {}}
        for tfk, (iv, rng) in INTERVALS.items():
            try:
                bars, meta = chart(ysym, iv, rng)
                if len(bars) < 10:
                    raise RuntimeError(f"too few bars ({len(bars)})")
                with open(os.path.join(outdir, f"{key}_{tfk}.json"), "w") as f:
                    json.dump({"sym": ysym, "tf": tfk, "updated": index["updated"], "meta": meta, "bars": bars}, f, separators=(",", ":"))
                index["symbols"][key]["tf"][tfk] = {"bars": len(bars), "last": bars[-1][0]}
                index["symbols"][key]["meta"] = meta
                print(f"ok  {key:4s} {tfk:3s} {len(bars):5d} bars  last={time.strftime('%Y-%m-%d %H:%M', time.gmtime(bars[-1][0]/1000))}Z")
            except Exception as e:  # 1つ失敗しても他は続ける
                failures += 1
                index["symbols"][key]["tf"][tfk] = {"error": str(e)[:200]}
                print(f"ERR {key:4s} {tfk:3s} {e}", file=sys.stderr)
            time.sleep(0.4)
    with open(os.path.join(outdir, "index.json"), "w") as f:
        json.dump(index, f, separators=(",", ":"))
    # 全滅のときだけ失敗扱い（前回のデータを残すため push しない）
    total = len(SYMBOLS) * len(INTERVALS)
    if failures >= total:
        sys.exit(1)

if __name__ == "__main__":
    import urllib.parse
    main(sys.argv[1] if len(sys.argv) > 1 else "out")
