#!/usr/bin/env python3
"""Yahoo Finance から先物・為替の四本値を取得して JSON に書き出す（GitHub Actions 用）。

ブラウザからは Yahoo に直接アクセスできない（CORS）ので、Actions が5分ごとに取得し
`data` ブランチへ置く。ページは raw.githubusercontent.com から読む。
依存ライブラリなし（標準ライブラリのみ）。
"""
import json, os, sys, time, urllib.request, urllib.error, urllib.parse, http.cookiejar

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
HOSTS = ["https://query2.finance.yahoo.com", "https://query1.finance.yahoo.com"]
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
           "Accept": "application/json,text/plain,*/*", "Accept-Language": "en-US,en;q=0.9"}
JAR = http.cookiejar.CookieJar()
OPENER = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(JAR))
CRUMB = {"v": None}

def raw(url, timeout=30):
    req = urllib.request.Request(url, headers=HEADERS)
    with OPENER.open(req, timeout=timeout) as r:
        return r.status, r.read().decode("utf-8", "replace")

def ensure_crumb():
    """Yahoo はデータセンターからのアクセスに cookie と crumb を求めることがある。"""
    if CRUMB["v"]:
        return CRUMB["v"]
    try:
        raw("https://fc.yahoo.com", 20)            # 404 が返るが cookie が付く
    except urllib.error.HTTPError:
        pass
    except Exception as e:
        print(f"cookie warm-up failed: {e}", file=sys.stderr)
    for host in HOSTS:
        try:
            st, body = raw(f"{host}/v1/test/getcrumb", 20)
            if st == 200 and body and "<" not in body and len(body) < 64:
                CRUMB["v"] = body.strip(); print(f"crumb ok via {host}")
                return CRUMB["v"]
        except Exception as e:
            print(f"crumb failed via {host}: {e}", file=sys.stderr)
    return None

def get(path, tries=3):
    last = None
    for i in range(tries):
        for host in HOSTS:
            crumb = ensure_crumb()
            url = f"{host}{path}" + (f"&crumb={urllib.parse.quote(crumb)}" if crumb else "")
            try:
                st, body = raw(url)
                return json.loads(body)
            except urllib.error.HTTPError as e:
                snippet = ""
                try: snippet = e.read().decode("utf-8", "replace")[:160]
                except Exception: pass
                last = f"HTTP {e.code} {snippet}"
                if e.code in (401, 403): CRUMB["v"] = None   # crumb を取り直す
            except Exception as e:
                last = repr(e)
        time.sleep(3 + 3*i)
    raise RuntimeError(f"fetch failed: {path}: {last}")

def chart(sym, iv, rng):
    path = f"/v8/finance/chart/{urllib.parse.quote(sym)}?interval={iv}&range={rng}&includePrePost=true&events=none"
    j = get(path)
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
                print(f"ERR {key:4s} {tfk:3s} {e}", flush=True)
            time.sleep(0.4)
    with open(os.path.join(outdir, "index.json"), "w") as f:
        json.dump(index, f, separators=(",", ":"))
    summ = os.environ.get("GITHUB_STEP_SUMMARY")
    if summ:
        with open(summ, "a") as f:
            f.write("| symbol | tf | result |\n|---|---|---|\n")
            for k, v in index["symbols"].items():
                for tfk, r in v["tf"].items():
                    f.write(f"| {k} | {tfk} | {r.get('bars', r.get('error'))} |\n")
    # 全滅のときだけ失敗扱い（前回のデータを残すため push しない）
    total = len(SYMBOLS) * len(INTERVALS)
    if failures >= total:
        sys.exit(1)

if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "out")
