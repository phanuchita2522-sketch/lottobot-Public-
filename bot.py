#!/usr/bin/env python3
"""LottoBot — บอทส่วนตัวดึงผลหวยให้ LottoSnap (ใช้เฉพาะ Python stdlib ไม่ต้องติดตั้งอะไร)
คำสั่งใหม่: watch = เฝ้าหน้าเว็บในช่วงเวลาที่หวยออกแล้วดึงทันที (ดู README)

ผลลัพธ์เป็นไฟล์ JSON แบบ static ในโฟลเดอร์ site/ เอาไปโฮสต์ฟรีที่ไหนก็ได้ (GitHub Pages ฯลฯ)
แล้วตั้ง "แหล่งข้อมูลหวยไทย (API)" ในแอปเป็น URL นั้น

  site/latest            หวยไทยงวดล่าสุด   (รูปแบบเดียวกับ rayriffy ที่แอปรองรับ)
  site/list/1 2 3 …      รายการงวด (หน้าละ 10)
  site/lotto/<YYYY-MM-DD>  ผลแต่ละงวด
  site/other/<lot>       ผลล่าสุดของ gsb | lao | hanoi | my | yeekee

คำสั่ง:  fetch | probe | add | selftest | serve     (ดู README.md)
"""
import argparse, datetime as dt, functools, html as htmllib, http.server, json, os, re, sys, tempfile, time
import urllib.error, urllib.request
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import scrapers  # noqa: E402

UA = "Mozilla/5.0 (compatible; LottoBot-personal/1.0)"
ICT = dt.timezone(dt.timedelta(hours=7))
TH_MONTHS = ["มกราคม", "กุมภาพันธ์", "มีนาคม", "เมษายน", "พฤษภาคม", "มิถุนายน",
             "กรกฎาคม", "สิงหาคม", "กันยายน", "ตุลาคม", "พฤศจิกายน", "ธันวาคม"]
GLO_URL = "https://www.glo.or.th/api/checking/getLotteryResult"
OTHERS = ["gsb", "lao", "hanoi", "my", "yeekee"]
ISO_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


class SourceError(Exception):
    """เครือข่าย/เซิร์ฟเวอร์มีปัญหา (ต่างจาก 'ยังไม่มีผลงวดนี้')"""


def log(*a):
    print(dt.datetime.now(ICT).strftime("%H:%M:%S"), *a, flush=True)


# ---------------------------------------------------------------- HTTP / utils
def http(url, data=None, timeout=25, retries=2):
    body = json.dumps(data).encode() if data is not None else None
    headers = {"User-Agent": UA, "Accept": "*/*"}
    if body:
        headers["Content-Type"] = "application/json"
    last = None
    for i in range(retries + 1):
        try:
            req = urllib.request.Request(url, data=body, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, r.read().decode(r.headers.get_content_charset() or "utf-8", "replace")
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", "replace")
        except Exception as e:  # timeout, DNS, TLS ...
            last = e
            time.sleep(1.5 * (i + 1))
    raise SourceError(f"{url}: {last}")


def strip_tags(s):
    s = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", s)
    s = re.sub(r"(?s)<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", htmllib.unescape(s)).strip()


def thai_date(d):
    return f"{d.day} {TH_MONTHS[d.month - 1]} {d.year + 543}"


def parse_thai_date(s):
    m = re.search(r"(\d{1,2})\s+(\S+)\s+(\d{4})", s or "")
    if not m or m.group(2) not in TH_MONTHS:
        return None
    y = int(m.group(3))
    return dt.date(y - 543 if y > 2400 else y, TH_MONTHS.index(m.group(2)) + 1, int(m.group(1)))


def write_json(path, obj):
    """เขียนเมื่อเนื้อหาเปลี่ยนเท่านั้น (git diff สะอาด) — คืน True ถ้ามีการเขียน"""
    text = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    path = Path(path)
    if path.exists() and path.read_text(encoding="utf-8") == text:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)
    return True


# ---------------------------------------------------------------- หวยไทย (GLO)
PRIZES = [("prizeFirst", "รางวัลที่ 1", 6000000), ("prizeFirstNear", "รางวัลข้างเคียงรางวัลที่ 1", 100000),
          ("prizeSecond", "รางวัลที่ 2", 200000), ("prizeThird", "รางวัลที่ 3", 80000),
          ("prizeForth", "รางวัลที่ 4", 40000), ("prizeFifth", "รางวัลที่ 5", 20000)]
RUNNING = [("runningNumberFrontThree", "รางวัลเลขหน้า 3 ตัว", 4000), ("runningNumberBackThree", "รางวัลเลขท้าย 3 ตัว", 4000),
           ("runningNumberBackTwo", "รางวัลเลขท้าย 2 ตัว", 2000)]
GLO_KEYS = {"first": "prizeFirst", "near1": "prizeFirstNear", "second": "prizeSecond", "third": "prizeThird",
            "fourth": "prizeForth", "fifth": "prizeFifth", "last3f": "runningNumberFrontThree",
            "last3b": "runningNumberBackThree", "last2": "runningNumberBackTwo"}
NAMES = {k: (n, r) for k, n, r in PRIZES + RUNNING}


def _nums(x):
    out = []
    for it in x or []:
        v = it.get("value") if isinstance(it, dict) else it
        v = str(v if v is not None else "").strip()
        if v:
            out.append(v)
    return out


def _entry(rid, numbers, reward=None):
    name, default = NAMES[rid]
    reward = int(reward) if str(reward or "").isdigit() else default
    return {"id": rid, "name": name, "reward": str(reward), "amount": len(numbers), "number": numbers}


def normalize_thai(obj, d):
    """รับ JSON ได้ 2 แบบ: (A) รูปแบบ rayriffy prizes/runningNumbers  (B) รูปแบบ GLO data.first/last2/... → รูปแบบ A ที่ตรวจแล้ว"""
    r = obj.get("response", obj) if isinstance(obj, dict) else None
    if not isinstance(r, dict):
        raise ValueError("ไม่ใช่ JSON object")
    prizes, running = [], []
    if isinstance(r.get("prizes"), list):
        for p in r["prizes"]:
            if p.get("id") in NAMES:
                prizes.append(_entry(p["id"], _nums(p.get("number")), p.get("reward")))
        for p in r.get("runningNumbers") or []:
            if p.get("id") in NAMES:
                running.append(_entry(p["id"], _nums(p.get("number")), p.get("reward")))
    elif isinstance(r.get("data"), dict):
        for gk, rid in GLO_KEYS.items():
            e = r["data"].get(gk)
            if e is None:
                continue
            nums = _nums(e.get("number") if isinstance(e, dict) else e)
            (running if rid.startswith("running") else prizes).append(
                _entry(rid, nums, e.get("price") if isinstance(e, dict) else None))
    else:
        raise ValueError("ไม่รู้จักรูปแบบข้อมูล (GLO อาจเปลี่ยนรูปแบบ)")
    validate_thai(prizes, running)
    return {"status": "success", "response": {"date": thai_date(d), "endpoint": f"lotto/{d.isoformat()}",
                                             "prizes": prizes, "runningNumbers": running}}


def validate_thai(prizes, running, legacy=False):
    P = {p["id"]: p["number"] for p in prizes + running}
    need = {"prizeFirst": 6, "prizeSecond": 6, "prizeThird": 6, "prizeForth": 6, "prizeFifth": 6,
            "runningNumberFrontThree": 3, "runningNumberBackThree": 3, "runningNumberBackTwo": 2}
    bad = [k for k, n in need.items() if not P.get(k) or not all(re.fullmatch(r"\d{%d}" % n, x) for x in P[k])]
    if legacy:  # งวดก่อน 2015-09-01 ยังไม่มีรางวัลเลขหน้า 3 ตัว
        bad = [k for k in bad if k != "runningNumberFrontThree"]
    if "prizeFirstNear" in P and not all(re.fullmatch(r"\d{6}", x) for x in P["prizeFirstNear"]):
        bad.append("prizeFirstNear")
    if bad or len(P["prizeFirst"]) != 1:
        raise ValueError("ข้อมูลไม่ครบ/ไม่ถูกต้อง: " + ",".join(bad or ["prizeFirst"]))


DATASET_URL = "https://raw.githubusercontent.com/huayreview/thai-lottery-results/main/thai-lottery-results.json"
DS_MAP = {"prizeFirst": "first_prize", "prizeFirstNear": "near_first_prize", "prizeSecond": "second_prize",
          "prizeThird": "third_prize", "prizeForth": "fourth_prize", "prizeFifth": "fifth_prize",
          "runningNumberFrontThree": "front_three_digits", "runningNumberBackThree": "last_three_digits",
          "runningNumberBackTwo": "last_two_digits"}
DS_LEN = {"runningNumberFrontThree": 3, "runningNumberBackThree": 3, "runningNumberBackTwo": 2}


def dataset_to_draw(rec):
    """ชุดข้อมูลชุมชน huayreview/thai-lottery-results (CC BY 4.0) → รูปแบบเดียวกับที่แอปอ่าน"""
    d = dt.date.fromisoformat(rec["draw_date"])
    prizes, running = [], []
    for rid, key in DS_MAP.items():
        n = DS_LEN.get(rid, 6)
        nums = [str(x).zfill(n) for x in (rec.get(key) or [])]
        if not nums and rid in ("prizeFirstNear", "runningNumberFrontThree"):
            continue
        (running if rid.startswith("running") else prizes).append(_entry(rid, nums))
    validate_thai(prizes, running, legacy=d < dt.date(2015, 9, 1))
    return d, {"status": "success", "response": {"date": thai_date(d), "endpoint": f"lotto/{d.isoformat()}",
                                                "prizes": prizes, "runningNumbers": running}}


def run_dataset(out):
    url = os.environ.get("DATASET_URL") or DATASET_URL
    st, text = http(url, timeout=60)
    if st != 200:
        raise SourceError(f"{url}: HTTP {st}")
    try:
        recs = json.loads(text)["draws"]
    except (ValueError, KeyError, TypeError) as e:
        raise ValueError(f"รูปแบบชุดข้อมูลไม่ถูกต้อง: {e}")
    have, new, skipped = set(stored_draws(out)), 0, 0
    for rec in recs:
        iso = str(rec.get("draw_date", ""))
        if not ISO_RE.fullmatch(iso) or iso in have:
            continue
        try:
            d, res = dataset_to_draw(rec)
        except (ValueError, KeyError):
            skipped += 1
            continue
        write_json(Path(out) / "lotto" / iso, res)
        new += 1
    rebuild_lists(out)
    return new, skipped


def fetch_thai_draw(d):
    status, text = http(GLO_URL, {"date": f"{d.day:02d}", "month": f"{d.month:02d}", "year": str(d.year)})
    if status != 200:
        return None
    try:
        obj = json.loads(text)
    except ValueError:
        return None
    try:
        return normalize_thai(obj, d)
    except ValueError as e:
        log(f"  [{d}] ตอบกลับแล้วแต่ใช้ไม่ได้: {e}")
        return None


def candidate_dates(today, days_back):
    """งวดปกติคือวันที่ 1 และ 16 (เลื่อนได้ 1-2 วันเมื่อติดวันหยุด) จึงลองเฉพาะ 1-3 และ 16-18"""
    return [today - dt.timedelta(i) for i in range(days_back + 1) if (today - dt.timedelta(i)).day in (1, 2, 3, 16, 17, 18)]


def stored_draws(out):
    d = Path(out) / "lotto"
    return sorted(p.name for p in d.glob("*") if ISO_RE.fullmatch(p.name)) if d.exists() else []


def rebuild_lists(out):
    out = Path(out)
    draws = sorted(stored_draws(out), reverse=True)
    if not draws:
        return
    write_json(out / "latest", json.loads((out / "lotto" / draws[0]).read_text(encoding="utf-8")))
    items = [{"id": i, "url": f"lotto/{i}", "date": thai_date(dt.date.fromisoformat(i))} for i in draws]
    pages = [items[i:i + 10] for i in range(0, len(items), 10)]
    for n, page in enumerate(pages, 1):
        write_json(out / "list" / str(n), {"status": "success", "response": page})
    write_json(out / "list" / str(len(pages) + 1), {"status": "success", "response": []})


def run_thai(out, today, backfill_months=0):
    existing = stored_draws(out)
    latest = dt.date.fromisoformat(existing[-1]) if existing else None
    if backfill_months:
        cands = candidate_dates(today, backfill_months * 31)
    else:
        cands = [d for d in candidate_dates(today, 45) if latest is None or d > latest]
    cands = [d for d in cands if d.isoformat() not in existing]
    new = errors = 0
    for d in cands:
        try:
            res = fetch_thai_draw(d)
        except SourceError as e:
            errors += 1
            log("  เครือข่ายผิดพลาด:", e)
            if errors >= 3:
                break
            continue
        if res:
            write_json(Path(out) / "lotto" / d.isoformat(), res)
            log(f"  ✔ หวยไทยงวด {d} บันทึกแล้ว")
            new += 1
        time.sleep(0.6)
    base = os.environ.get("THAI_FALLBACK_BASE", "").rstrip("/")
    if base and new == 0 and (errors or not existing):
        try:  # แหล่งสำรอง: API สาธารณะที่รูปแบบเดียวกับ rayriffy
            st, text = http(base + "/latest")
            obj = json.loads(text) if st == 200 else None
            d = parse_thai_date(((obj or {}).get("response") or {}).get("date"))
            if d and d.isoformat() not in existing:
                write_json(Path(out) / "lotto" / d.isoformat(), normalize_thai(obj, d))
                log(f"  ✔ หวยไทยงวด {d} (แหล่งสำรอง)")
                new += 1
        except (SourceError, ValueError) as e:
            log("  แหล่งสำรองใช้ไม่ได้:", e)
    rebuild_lists(out)
    return new, errors


# ---------------------------------------------------------------- หวยชนิดอื่น
def G(id, name, match, numbers, reward=0):
    return {"id": id, "name": name, "match": match, "reward": reward, "numbers": numbers}


def _digits(v, n, label):
    v = str(v).strip()
    if not re.fullmatch(r"\d{%d}" % n, v):
        raise ValueError(f"{label} ต้องเป็นเลข {n} หลัก (ได้ '{v}')")
    return v


def build_lao(n4):
    n4 = _digits(n4, 4, "เลขลาว")
    return [G("l4", "4 ตัวตรง", "full", [n4]), G("l3", "3 ตัวบน", "last", [n4[1:]]),
            G("l2", "2 ตัวบน", "last", [n4[2:]]), G("l2b", "2 ตัวล่าง", "first", [n4[:2]])]


def build_hanoi(special, first):
    special, first = _digits(special, 5, "รางวัลพิเศษ"), _digits(first, 5, "รางวัลที่ 1")
    return [G("h3", "3 ตัวบน", "last", [special[-3:]]), G("h2", "2 ตัวบน", "last", [special[-2:]]),
            G("h2b", "2 ตัวล่าง", "full", [first[-2:]])]


def build_my(nums):
    if len(nums) != 23:
        raise ValueError(f"4D ต้องมี 23 เลข (ที่ 1-3 + Special 10 + Consolation 10) ได้ {len(nums)}")
    nums = [_digits(n, 4, "เลข 4D") for n in nums]
    return [G("m1", "รางวัลที่ 1", "full", [nums[0]]), G("m2", "รางวัลที่ 2", "full", [nums[1]]),
            G("m3", "รางวัลที่ 3", "full", [nums[2]]), G("ms", "Special (10 ตัว)", "full", nums[3:13]),
            G("mc", "Consolation (10 ตัว)", "full", nums[13:23])]


def build_yeekee(top3, bottom2):
    top3, bottom2 = _digits(top3, 3, "3 ตัวบน"), _digits(bottom2, 2, "2 ตัวล่าง")
    return [G("y3", "3 ตัวบน", "full", [top3]), G("y2", "2 ตัวบน", "full", [top3[-2:]]), G("y2b", "2 ตัวล่าง", "full", [bottom2])]


def build_gsb(first, last3, last2=None, last4=None):
    """last3/last2/last4 รับเลขเดียวหรือรายการ (ธ.ก.ส. มีเลขท้าย 3 ตัวหลายชุด) — last2/last4 เว้นได้ถ้าหน้าเว็บไม่แสดง"""
    first = str(first).strip()
    if not first.isdigit():
        raise ValueError("รางวัลที่ 1 ต้องเป็นตัวเลข")
    lst = lambda v: [v] if isinstance(v, str) else list(v or [])
    out = [G("g1", "รางวัลที่ 1", "full", [first])]
    if last4:
        out.append(G("g4", "เลขท้าย 4 ตัว", "last", [_digits(x, 4, "ท้าย 4") for x in lst(last4)]))
    out.append(G("g3", "เลขท้าย 3 ตัว", "last", [_digits(x, 3, "ท้าย 3") for x in lst(last3)]))
    if last2:
        out.append(G("g2", "เลขท้าย 2 ตัว", "last", [_digits(x, 2, "ท้าย 2") for x in lst(last2)]))
    return out


def make_other(lot, groups, label):
    return {"lottery": lot, "date": label, "groups": groups, "source": "bot"}


def extract_other(lot, conf, text):
    """ตัด anchor → จับด้วย regex (named groups) → ประกอบรางวัลตามชนิดหวย"""
    if conf.get("anchor"):
        m = re.search(conf["anchor"], text, re.S)
        if not m:
            raise ValueError("ไม่พบ anchor ในหน้าเว็บ (โครงหน้าอาจเปลี่ยน)")
        text = text[m.end():]
    if lot == "my":
        return build_my(re.findall(r"(?<!\d)\d{4}(?!\d)", text)[:23])
    m = re.search(conf.get("regex") or "(?!)", text, re.S)
    if not m:
        raise ValueError("regex ไม่ตรงกับหน้าเว็บ — รัน probe แล้วปรับ sources.json")
    g = m.groupdict()
    if lot == "lao":
        return build_lao(g["n4"])
    if lot == "hanoi":
        return build_hanoi(g["special"], g["first"])
    if lot == "gsb":
        return build_gsb(g["first"], g["last3"], g["last2"])
    if lot == "yeekee":
        return build_yeekee(g["top3"], g["bottom2"])
    raise ValueError("ไม่รู้จักชนิดหวย " + lot)


def draw_label(conf, text, now):
    m = re.search(conf["date_regex"], text) if conf.get("date_regex") else None
    if m:
        try:
            g = m.groupdict()
            y = int(g["y"]); y = y - 543 if y > 2400 else y
            mo = TH_MONTHS.index(g["mon"]) + 1 if g.get("mon") else int(g["m"])   # รองรับชื่อเดือนไทยเต็ม (กลุ่ม mon)
            return thai_date(dt.date(y, mo, int(g["d"])))
        except (KeyError, ValueError):
            pass
    return f"ดึงเมื่อ {now:%d/%m/%Y %H:%M}"


def add_history(out, lot, groups, label):
    """เก็บผลทุกงวดที่ดึงได้ไว้ที่ other/<ชนิด>-history ให้แอปตรวจย้อนหลังได้ — เก็บเฉพาะรายการที่รู้วันที่จริง"""
    d = parse_thai_date(label)
    if not d:
        return False
    p = Path(out) / "other" / f"{lot}-history"
    try:
        draws = json.loads(p.read_text(encoding="utf-8")).get("draws", []) if p.exists() else []
    except ValueError:
        draws = []
    draws = [x for x in draws if x.get("date") != label] + [{"date": label, "groups": groups}]
    draws.sort(key=lambda x: parse_thai_date(x["date"]) or dt.date.min, reverse=True)
    return write_json(p, {"lottery": lot, "draws": draws[:300]})


def save_other(out, lot, groups, label):
    """เขียนเมื่อ 'เลขรางวัล' เปลี่ยนจริง ๆ — ไม่ใช่แค่เวลาเปลี่ยน"""
    add_history(out, lot, groups, label)
    p = Path(out) / "other" / lot
    if p.exists():
        try:
            if json.loads(p.read_text(encoding="utf-8")).get("groups") == groups:
                return False
        except ValueError:
            pass
    return write_json(p, make_other(lot, groups, label))


PARSER_DEFAULT = {"hanoi": "xsmb", "my": "4d", "lao": "lao", "gsb": "gsb"}


def source_list(conf):
    if conf.get("sources"):
        return [s for s in conf["sources"] if s.get("url")]
    return [{"url": conf["url"]}] if conf.get("url") else []


def groups_from(lot, parser, conf, text):
    if parser == "xsmb":
        r = scrapers.parse_xsmb(text, conf)
        return build_hanoi(r["db"], r["g1"])
    if parser == "4d":
        return build_my(scrapers.parse_4d(text, conf))
    if parser == "lao":
        return build_lao(scrapers.parse_lao(text, conf))
    if parser == "gsb":
        r = scrapers.parse_gsb(text, conf)
        return build_gsb(r["first"][0], r["last3"], r.get("last2"), r.get("last4"))
    return extract_other(lot, conf, text)


def fetch_other(lot, conf, now=None):
    """ดึงทุกแหล่ง → ต้องมี ≥ quorum แหล่งที่ได้ผลตรงกันจึงเชื่อถือ (กันเว็บเดียวพัง/ช้า/ผิด) → (groups, label, ข้อความเตือน)"""
    now = now or dt.datetime.now(ICT)
    parser = conf.get("parser") or PARSER_DEFAULT.get(lot, "regex")
    seen, errors, blocked = [], [], False
    for s in source_list(conf):
        name = s.get("name") or s.get("url")
        c = {**conf, **s}
        try:
            if c.get("render") == "browser":
                raw = scrapers.render_browser(c["url"])
            else:
                st, raw = http(c["url"])
                scrapers.check_block(st, raw)
            text = strip_tags(raw)
            groups = groups_from(lot, parser, c, text)
            seen.append((name, groups, draw_label(c, text, now)))
        except scrapers.Blocked as e:
            blocked = True
            errors.append(f"{name}: ถูกบล็อก — {e}")
        except (SourceError, ValueError) as e:
            errors.append(f"{name}: {e}")
    quorum = max(1, int(conf.get("quorum", 1)))
    votes = {}
    for name, groups, label in seen:
        votes.setdefault(json.dumps([g["numbers"] for g in groups]), []).append((name, groups, label))
    if votes:
        best = max(votes.values(), key=len)
        if len(best) >= quorum:
            label = next((l for _, _, l in best if not l.startswith("ดึงเมื่อ")), best[0][2])
            return best[0][1], label, errors
    why = "; ".join(errors) or "แหล่งข้อมูลให้ผลไม่ตรงกัน: " + " | ".join(f"{n}={[g['numbers'][0] for g in gr[:1]]}" for n, gr, _ in seen)
    if len(seen) >= 2 and not errors:
        why = "แหล่งข้อมูลให้ผลไม่ตรงกัน (อาจกำลังอัปเดตไม่พร้อมกัน) — ไม่บันทึกจนกว่าจะตรงกัน ≥ %d แหล่ง" % quorum
    err = scrapers.Blocked(why) if blocked and not seen else ValueError(why)
    raise err


def run_others(out, cfg, only=None):
    n = failed = 0
    for lot in OTHERS:
        conf = cfg.get(lot) or {}
        if (only and lot not in only) or not conf.get("enabled") or not source_list(conf):
            continue
        try:
            groups, label, warns = fetch_other(lot, conf)
            for w in warns:
                log(f"  ⚠ {lot}: {w}")
            if save_other(out, lot, groups, label):
                log(f"  ✔ {lot} อัปเดตแล้ว ({label})")
                n += 1
        except scrapers.Blocked as e:
            failed += 1
            log(f"  ✘ {lot}: ถูกบล็อก — {e}")
            notify(f"⚠️ LottoBot: {lot} ถูกบล็อก/ล้มเหลว — {e}", key=f"fail-{lot}")
        except (SourceError, ValueError) as e:
            failed += 1
            log(f"  ✘ {lot}: {e}")
    return n, failed


# ---------------------------------------------------------------- แจ้งเตือน + โหมดเฝ้ารอผล
_notified = {}


def notify(msg, key=None, every=6 * 3600):
    """ส่งข้อความไป NOTIFY_WEBHOOK (Discord/Slack/webhook ทั่วไป) — กันซ้ำด้วย key"""
    url = os.environ.get("NOTIFY_WEBHOOK")
    if not url:
        return
    now = time.time()
    if key and now - _notified.get(key, 0) < every:
        return
    _notified[key or msg] = now
    try:
        req = urllib.request.Request(url, data=json.dumps({"content": msg, "text": msg}).encode(),
                                     headers={"Content-Type": "application/json", "User-Agent": UA})
        urllib.request.urlopen(req, timeout=15).read()
    except Exception as e:
        log("  ส่งแจ้งเตือนไม่สำเร็จ:", e)


def _hm(s):
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def in_window(sched, now):
    """sched = {"days":[0-6 จันทร์=0], "from":"20:00", "to":"22:30"} เวลาไทย (ICT)"""
    if not sched:
        return True
    if now.weekday() not in sched.get("days", range(7)):
        return False
    cur = now.hour * 60 + now.minute
    return _hm(sched.get("from", "00:00")) <= cur <= _hm(sched.get("to", "23:59"))


def fresh_enough(conf, text_label, today):
    """True ถ้าวันที่บนหน้าเว็บ = วันนี้ (หรือไม่มี date_regex ให้เทียบ)"""
    return text_label == thai_date(today) if conf.get("date_regex") else True


def cmd_watch(a):
    """เฝ้าหน้าเว็บในช่วงเวลาที่หวยออก ถี่ตามที่ตั้ง หยุดเมื่อได้ผลใหม่ของวันนั้น — รันค้างบนเครื่อง/เซิร์ฟเวอร์เล็ก ๆ"""
    cfg = load_cfg(a.config)
    lots = [l for l in OTHERS if (cfg.get(l) or {}).get("enabled") and source_list(cfg[l])]
    if not lots:
        sys.exit("ยังไม่ได้เปิดแหล่งข้อมูลใน sources.json (enabled + sources)")
    done, fails, nxt = {}, {}, {}
    log("เฝ้ารอผล:", ", ".join(lots), "— กด Ctrl+C เพื่อหยุด")
    while True:
        now = dt.datetime.now(ICT)
        for lot in lots:
            conf = cfg[lot]
            if done.get(lot) == now.date() or not in_window(conf.get("schedule"), now) or time.time() < nxt.get(lot, 0):
                continue
            gap = max(20, int(conf.get("poll_seconds", 45)))
            try:
                groups, label, _ = fetch_other(lot, conf, now)
                fails[lot] = 0
                changed = save_other(a.out, lot, groups, label)
                if changed:
                    write_index(a.out)
                    log(f"  ✔ {lot} ผลใหม่: {label}")
                    notify(f"🎟️ LottoBot: {lot} ผลใหม่ {label}", key=f"new-{lot}-{label}")
                if (changed or label == thai_date(now.date())) and fresh_enough(conf, label, now.date()):
                    done[lot] = now.date()
                    log(f"  {lot} เสร็จของวันนี้ — หยุดดึงจนถึงพรุ่งนี้")
            except (SourceError, ValueError) as e:
                fails[lot] = fails.get(lot, 0) + 1
                gap = min(600, gap * 2 ** min(fails[lot], 4))   # ถอยห่างเมื่อล้มเหลว (ลดโอกาสโดนบล็อก)
                log(f"  ✘ {lot}: {e} (ลองใหม่ใน {gap} วิ)")
                if fails[lot] == 5 or isinstance(e, scrapers.Blocked):
                    notify(f"⚠️ LottoBot: {lot} ดึงผลไม่ได้ต่อเนื่อง — {e}", key=f"fail-{lot}")
            nxt[lot] = time.time() + gap + (hash((lot, now.second)) % 7)
        time.sleep(5)


def write_index(out):
    out = Path(out)
    idx = {"thai": {"latest": (stored_draws(out) or [None])[-1], "draws": len(stored_draws(out))}, "others": {}}
    for lot in OTHERS:
        p = out / "other" / lot
        if p.exists():
            idx["others"][lot] = json.loads(p.read_text(encoding="utf-8")).get("date")
    write_json(out / "index.json", idx)


# ---------------------------------------------------------------- commands
def load_cfg(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}


def cmd_fetch(a):
    out, today = Path(a.out), dt.datetime.now(ICT).date()
    only = set(a.only.split(",")) if a.only else None
    changed = failed = 0
    if not only or "thai" in only:
        ds_failed = False
        log("หวยไทย: ชุดข้อมูลชุมชน…")
        try:
            n, sk = run_dataset(out)
            changed += n
            log(f"  ชุดข้อมูล: เพิ่ม {n} งวด" + (f" (ข้ามงวดที่ข้อมูลไม่ครบ {sk})" if sk else ""))
        except (SourceError, ValueError) as e:
            ds_failed = True
            log("  ✘ ชุดข้อมูล:", e)
        log("หวยไทย: GLO (งวดที่ชุดข้อมูลยังไม่มี)…")
        n, errs = run_thai(out, today, a.backfill_months)
        changed += n
        failed += 1 if (errs and ds_failed) else 0  # ล้มเหลวจริงเมื่อทั้งสองแหล่งใช้ไม่ได้
    n, f = run_others(out, load_cfg(a.config), only)
    changed += n
    failed += f
    write_index(out)
    log(f"เสร็จ — มีข้อมูลใหม่ {changed} รายการ, แหล่งที่ล้มเหลว {failed}")
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as fh:
            fh.write(f"changed={'true' if changed else 'false'}\n")
    if failed:  # ให้ cron/Actions เห็นว่าพัง (จะได้รับอีเมลแจ้งเตือน) — ข้อมูลที่ได้มาแล้วยังถูกบันทึก
        sys.exit(1)


def cmd_probe(a):
    """ทดสอบแหล่งข้อมูลจริงบนเครื่องคุณ + เก็บหน้าเว็บดิบไว้ดูใน probe_out/"""
    pdir = Path("probe_out"); pdir.mkdir(exist_ok=True)
    today = dt.datetime.now(ICT).date()
    log("== หวยไทย: ชุดข้อมูลชุมชน ==")
    try:
        st, text = http(os.environ.get("DATASET_URL") or DATASET_URL, timeout=60)
        (pdir / "dataset.json").write_text(text, encoding="utf-8")
        recs = json.loads(text)["draws"]
        d, _ = dataset_to_draw(recs[0]) if recs else (None, None)
        log(f"  HTTP {st}, {len(recs)} งวด, งวดแรกในไฟล์: {recs[0].get('draw_date') if recs else '-'}")
    except (SourceError, ValueError, KeyError, TypeError) as e:
        log("  ✘", e, "(งวดแรกในไฟล์อาจยังไม่ครบ ไม่ใช่ปัญหาถ้า fetch ข้ามให้)")
    log("== หวยไทย (GLO) ==")
    ok = False
    for d in candidate_dates(today, 45):
        try:
            st, text = http(GLO_URL, {"date": f"{d.day:02d}", "month": f"{d.month:02d}", "year": str(d.year)})
        except SourceError as e:
            log("  เชื่อมต่อไม่ได้:", e); break
        (pdir / f"thai-{d}.json").write_text(text, encoding="utf-8")
        log(f"  {d}: HTTP {st}, {len(text)} bytes")
        try:
            res = normalize_thai(json.loads(text), d)
            log("  ✔ อ่านได้ — รางวัลที่ 1:", res["response"]["prizes"][0]["number"]); ok = True; break
        except (ValueError, KeyError) as e:
            log("  ✘", e)
    if not ok:
        log("  → ดูไฟล์ใน probe_out/ ถ้ารูปแบบเปลี่ยน ให้ปรับ normalize_thai() หรือตั้ง THAI_FALLBACK_BASE")
    cfg = load_cfg(a.config)
    for lot in OTHERS:
        conf = cfg.get(lot) or {}
        log(f"== {lot} ==")
        srcs = source_list(conf)
        if not srcs:
            log("  (ยังไม่ได้ตั้ง sources ใน sources.json — ใช้คำสั่ง add กรอกเองได้)"); continue
        parser = conf.get("parser") or PARSER_DEFAULT.get(lot, "regex")
        for i, s in enumerate(srcs):
            name, c = s.get("name") or s.get("url"), {**conf, **s}
            try:
                if c.get("render") == "browser":
                    raw, st = scrapers.render_browser(c["url"]), 200
                else:
                    st, raw = http(c["url"]); scrapers.check_block(st, raw)
                text = strip_tags(raw)
                (pdir / f"{lot}-{i}.html").write_text(raw, encoding="utf-8"); (pdir / f"{lot}-{i}.txt").write_text(text, encoding="utf-8")
                log(f"  [{name}] HTTP {st}, {len(text)} ตัวอักษร")
                groups = groups_from(lot, parser, c, text)
                log("    ✔ อ่านได้:", {g['name']: g['numbers'] for g in groups}, "| งวด:", draw_label(c, text, dt.datetime.now(ICT)))
            except scrapers.Blocked as e:
                log(f"    ✘ ถูกบล็อก: {e}")
            except (SourceError, ValueError) as e:
                log(f"    ✘ {e} → เปิด probe_out/{lot}-{i}.txt ดูข้อความที่บอทเห็น แล้วปรับ anchor/exclude/stop ใน sources.json")


def cmd_add(a):
    lot = a.lottery
    try:
        if lot == "lao": groups = build_lao(a.n4)
        elif lot == "hanoi": groups = build_hanoi(a.special, a.first)
        elif lot == "my": groups = build_my(a.numbers.split())
        elif lot == "yeekee": groups = build_yeekee(a.top3, a.bottom2)
        elif lot == "gsb": groups = build_gsb(a.first, a.last3, a.last2)
        else: raise ValueError("ใช้ได้กับ: lao hanoi my yeekee gsb")
    except (ValueError, TypeError, AttributeError) as e:
        sys.exit(f"ข้อมูลไม่ครบหรือไม่ถูกต้อง: {e}\nตัวอย่าง: add lao --n4 4036 | add hanoi --special 12345 --first 67890 | add yeekee --top3 123 --bottom2 45")
    d = dt.date.fromisoformat(a.date) if a.date else dt.datetime.now(ICT).date()
    label = thai_date(d) + (f" รอบ {a.round}" if a.round else "")
    write_json(Path(a.out) / "other" / lot, make_other(lot, groups, label))
    add_history(a.out, lot, groups, label)
    write_index(a.out)
    log(f"บันทึก {lot} ({label}) แล้ว:", {g['name']: g['numbers'] for g in groups})


def cmd_backfill(a):
    """ดึงผลย้อนหลังจากเว็บที่มีหน้าแยกตามวันที่ — ตั้ง "history_url" ใน sources.json เช่น
    https://example.com/xsmb/{dd}-{mm}-{yyyy}.html   (ใช้ได้กับชนิดที่มี parser: hanoi/my/lao/gsb)
    ดึงช้า ๆ (หน่วง --delay วินาทีต่อหน้า) เพื่อไม่รบกวนเว็บ; วันที่ไม่มีผล/อ่านไม่ได้จะข้ามและรายงานท้ายงาน"""
    lot, conf = a.lottery, (load_cfg(a.config).get(a.lottery) or {})
    tpl = conf.get("history_url")
    if not tpl:
        sys.exit(f'ยังไม่ได้ตั้ง "history_url" ของ {lot} ใน sources.json (ต้องเป็นเว็บที่มีหน้าแยกตามวันที่ ใส่ {{dd}} {{mm}} {{yyyy}})')
    end = dt.date.fromisoformat(a.to) if a.to else dt.datetime.now(ICT).date()
    start = dt.date.fromisoformat(a.start) if a.start else end - dt.timedelta(days=a.days - 1)
    if (end - start).days > 400:
        sys.exit("ช่วงเกิน 400 วัน — แบ่งทำเป็นช่วง ๆ เพื่อไม่ยิงเว็บถี่เกินไป")
    parser = conf.get("parser") or PARSER_DEFAULT.get(lot, "regex")
    c = {**conf, **(source_list(conf) or [{}])[0]}
    c.pop("anchor", None)   # หน้าย้อนหลังเป็นคนละโครงกับหน้าล่าสุด
    ok = skipped = 0
    weekdays = conf.get("history_days")
    d = start
    while d <= end:
        if weekdays is None or d.weekday() in weekdays:
            url = tpl.format(dd=f"{d.day:02d}", mm=f"{d.month:02d}", yyyy=d.year, byyyy=d.year + 543, d=d.day, m=d.month, ymd=d.isoformat())
            try:
                if c.get("render") == "browser":
                    raw = scrapers.render_browser(url)
                else:
                    st, raw = http(url)
                    scrapers.check_block(st, raw)
                text = strip_tags(raw)
                if conf.get("date_style"):
                    text = scrapers.anchor_date(text, d, conf["date_style"])
                groups = groups_from(lot, parser, c, text)
                add_history(a.out, lot, groups, thai_date(d)); ok += 1
                log(f"  ✔ {d}")
            except scrapers.Blocked as e:
                sys.exit(f"ถูกบล็อกที่ {d}: {e} — หยุดทันที ไม่ยิงต่อ")
            except (SourceError, ValueError) as e:
                skipped += 1; log(f"  – {d}: {e}")
            time.sleep(max(1.0, a.delay))
        d += dt.timedelta(days=1)
    # ผลล่าสุดของ other/<ชนิด> = งวดใหม่สุดในประวัติ (ใช้ backfill --days 3 ใน cron แทนหน้า "ล่าสุด" ที่ระบุวันที่ไม่ได้)
    p = Path(a.out) / "other" / f"{lot}-history"
    if p.exists():
        top = json.loads(p.read_text(encoding="utf-8"))["draws"][0]
        write_json(Path(a.out) / "other" / lot, make_other(lot, top["groups"], top["date"]))
    write_index(a.out)
    log(f"เสร็จ: ได้ {ok} งวด, ข้าม {skipped}")


def cmd_serve(a):
    class H(http.server.SimpleHTTPRequestHandler):
        def end_headers(self):
            self.send_header("Access-Control-Allow-Origin", "*"); self.send_header("Cache-Control", "no-store"); super().end_headers()
        def guess_type(self, path):
            return "application/json" if "." not in Path(path).name else super().guess_type(path)
    log(f"เสิร์ฟ {a.out} ที่ http://localhost:{a.port}  (ตั้งเป็นแหล่งข้อมูลในแอป)")
    http.server.ThreadingHTTPServer(("", a.port), functools.partial(H, directory=a.out)).serve_forever()


def selftest_gsb():
    page = ("<h2>ตรวจผลรางวัลสลาก ธ.ก.ส. งวดวันที่ 16 ตุลาคม 2569</h2><div>รางวัลที่ 1</div><div>9221612</div>"
            "<div>เลขท้าย 4 ตัว</div><div>3270</div><div>เลขท้าย 3 ตัว</div><div>167</div><div>541</div>"
            "<div>สลากดิจิทัล ธ.ก.ส. เลขท้าย 3 ตัว</div><div>866</div><div>โทร 02-555-0555</div>")
    r = scrapers.parse_gsb(strip_tags(page))
    assert r["first"] == ["9221612"] and r["last4"] == ["3270"] and r["last3"] == ["167", "541"] and "last2" not in r, r
    g = build_gsb(r["first"][0], r["last3"], r.get("last2"), r.get("last4"))
    assert [x["numbers"] for x in g] == [["9221612"], ["3270"], ["167", "541"]], g
    lab = draw_label({"date_regex": r"งวดวันที่\s*(?P<d>\d{1,2})\s*(?P<mon>[ก-๙]+)\s*(?P<y>\d{4})"}, strip_tags(page), dt.datetime.now(ICT))
    assert "16" in lab and "2026" in lab or "2569" in lab, lab
    try:
        scrapers.parse_gsb(strip_tags("<p>ไม่มีผล</p>")); raise AssertionError("ต้องล้มเหลว")
    except ValueError:
        pass


def selftest_scrapers():
    """ทดสอบตัวอ่านด้วยหน้าเว็บจำลอง (ไม่ใช่ HTML จริงของเว็บใด — ทดสอบตรรกะ ไม่ใช่การรับรองว่าใช้กับเว็บจริงได้)"""
    xs = ["12345", "67890", "11111", "22222", "33333", "44444", "55555", "66666", "77777", "88888",
          "1234", "2345", "3456", "4567", "5678", "6789", "7890", "8901", "9012", "0123", "123", "456", "789", "12", "34", "56", "78"]
    labels = ["ĐB", "G1", "G2", "G2", "G3", "G3", "G3", "G3", "G3", "G3", "G4", "G4", "G4", "G4", "G5", "G5", "G5", "G5", "G5", "G5", "G6", "G6", "G6", "G7", "G7", "G7", "G7"]
    html = "<html><body><h1>XSMB ngày 12 tháng 11 năm 2026 18h15</h1><table>" + "".join(
        f"<tr><td>{l}</td><td>{n}</td></tr>" for l, n in zip(labels, xs)) + "</table><footer>Hotline 0912345678 © 2026</footer></body></html>"
    r = scrapers.parse_xsmb(strip_tags(html))
    assert r["db"] == "12345" and r["g1"] == "67890" and r["g7"] == ["12", "34", "56", "78"] and len(r["g3"]) == 6, "xsmb"
    g = build_hanoi(r["db"], r["g1"])
    assert g[0]["numbers"] == ["345"] and g[1]["numbers"] == ["45"] and g[2]["numbers"] == ["90"], "hanoi build"
    rev = "".join(f"<p>{l} {n}</p>" for l, n in list(zip(labels, xs))[::-1])
    assert scrapers.parse_xsmb(strip_tags(rev))["db"] == "12345", "xsmb reversed"
    try:
        scrapers.parse_xsmb(strip_tags("<p>ยังไม่มีผล 12345 67890</p>")); raise AssertionError("ต้องล้มเหลว")
    except ValueError:
        pass
    n4 = [f"{i * 1111 % 10000:04d}" for i in range(1, 24)]
    h4 = "<div>Draw No: 2945/26 Date: 12/11/2026</div>" + "".join(f"<td>{x}</td>" for x in n4) + "<p>Next draw 1234 5678</p>"
    assert scrapers.parse_4d(strip_tags(h4)) == n4 and len(build_my(n4)) == 5, "4d"
    try:
        scrapers.parse_4d(strip_tags("<td>1234</td>" * 22)); raise AssertionError("ต้องล้มเหลว")
    except ValueError:
        pass
    assert scrapers.parse_lao(strip_tags("<b>ผลหวยพัฒนา 12/11/2026 งวดที่ 7</b> <i>4036</i>"), {"anchor": "ผลหวยพัฒนา"}) == "4036", "lao"
    assert scrapers.parse_lao(strip_tags("<td>522193</td><td>2193</td><td>193</td><td>93</td>")) == "2193", "lao6"
    page = "12 พฤศจิกายน 2569 xxxxxx xxxx xxx xx 11 พฤศจิกายน 2569 522193 2193 193 93 10 พฤศจิกายน 2569 173546 3546 546 46"
    try:
        scrapers.parse_lao(scrapers.anchor_date(page, dt.date(2026, 11, 12), "th")); raise AssertionError("ไม่ควรเอาเลขงวดอื่นมาติดวันที่ 12")
    except ValueError:
        pass
    assert scrapers.parse_lao(scrapers.anchor_date(page, dt.date(2026, 11, 11), "th")) == "2193", "anchor th"
    try:
        scrapers.anchor_date("XSMB 11/11/2026 ...", dt.date(2026, 11, 12), "num"); raise AssertionError("ต้องล้มเหลว")
    except ValueError:
        pass
    for page, st in (("<title>Just a moment...</title>", 200), ("x", 403)):
        try:
            scrapers.check_block(st, page); raise AssertionError("ต้องจับ block")
        except scrapers.Blocked:
            pass
    now = dt.datetime(2026, 11, 12, 20, 30, tzinfo=ICT)
    assert in_window({"days": [3], "from": "20:00", "to": "22:00"}, now) and not in_window({"days": [0], "from": "20:00", "to": "22:00"}, now), "window"


def cmd_selftest(_):
    glo_style = {"statusCode": 200, "status": "success", "response": {"date": "2026-11-01", "data": {
        "first": {"price": "6000000", "number": [{"round": 1, "value": "123456"}]},
        "near1": {"price": "100000", "number": [{"round": 1, "value": "123455"}, {"round": 2, "value": "123457"}]},
        "second": {"price": "200000", "number": [{"round": i, "value": f"20000{i}"} for i in range(1, 6)]},
        "third": {"price": "80000", "number": [{"round": i, "value": f"30000{i % 10}"} for i in range(10)]},
        "fourth": {"price": "40000", "number": [{"round": i, "value": f"4000{i:02d}"} for i in range(50)]},
        "fifth": {"price": "20000", "number": [{"round": i, "value": f"5000{i:02d}"} for i in range(100)]},
        "last3f": {"price": "4000", "number": [{"round": 1, "value": "111"}, {"round": 2, "value": "222"}]},
        "last3b": {"price": "4000", "number": [{"round": 1, "value": "333"}, {"round": 2, "value": "444"}]},
        "last2": {"price": "2000", "number": [{"round": 1, "value": "56"}]}}}}
    d = dt.date(2026, 11, 1)
    r = normalize_thai(glo_style, d)["response"]
    assert r["date"] == "1 พฤศจิกายน 2569" and r["prizes"][0]["number"] == ["123456"], "GLO-style"
    assert {p["id"] for p in r["runningNumbers"]} == {"runningNumberFrontThree", "runningNumberBackThree", "runningNumberBackTwo"}
    rec = {"draw_date": "2026-10-16", "first_prize": ["045998"], "near_first_prize": ["045997", "045999"],
           "second_prize": [f"20000{i}" for i in range(5)], "third_prize": [f"30000{i}" for i in range(10)],
           "fourth_prize": [f"4000{i:02d}" for i in range(50)], "fifth_prize": [f"5000{i:02d}" for i in range(100)],
           "front_three_digits": ["123", "789"], "last_three_digits": ["456", "012"], "last_two_digits": ["7"]}
    dd, dres = dataset_to_draw(rec)
    assert dres["response"]["prizes"][0]["number"] == ["045998"] and dres["response"]["runningNumbers"][2]["number"] == ["07"], "เลขนำหน้าศูนย์/zfill"
    dataset_to_draw(dict(rec, draw_date="2012-01-16", front_three_digits=[]))  # งวดเก่าไม่มีเลขหน้า 3 ตัว ต้องผ่าน
    for broken in (dict(rec, first_prize=[]), dict(rec, front_three_digits=[])):
        try: dataset_to_draw(broken); raise SystemExit("ต้องปฏิเสธงวดที่ข้อมูลไม่ครบ")
        except ValueError: pass
    again = normalize_thai({"status": "success", "response": {"prizes": r["prizes"], "runningNumbers": r["runningNumbers"]}}, d)
    assert again == normalize_thai(glo_style, d), "rayriffy-style ต้องได้ผลเท่ากัน"
    bad = json.loads(json.dumps(glo_style)); bad["response"]["data"]["first"]["number"] = [{"value": "xxxxxx"}]
    try: normalize_thai(bad, d); raise SystemExit("ต้องปฏิเสธข้อมูลที่ยังไม่ออก")
    except ValueError: pass
    assert parse_thai_date("16 พฤศจิกายน 2569") == dt.date(2026, 11, 16)
    assert [g["numbers"] for g in build_lao("4036")] == [["4036"], ["036"], ["36"], ["40"]]
    assert [g["numbers"] for g in build_hanoi("12345", "67890")] == [["345"], ["45"], ["90"]]
    assert [g["numbers"] for g in build_yeekee("123", "45")] == [["123"], ["23"], ["45"]]
    assert len(build_my([f"{i:04d}" for i in range(23)])[3]["numbers"]) == 10
    page = "<html><b>ผลหวยลาว</b> <script>x=1</script><span>เลข 4036</span> งวด 11/11/2026</html>"
    txt = strip_tags(page)
    conf = {"anchor": "ผลหวยลาว", "regex": r"(?P<n4>\d{4})", "date_regex": r"(?P<d>\d{2})/(?P<m>\d{2})/(?P<y>\d{4})"}
    assert extract_other("lao", conf, txt)[0]["numbers"] == ["4036"]
    assert draw_label(conf, txt, dt.datetime.now(ICT)) == "11 พฤศจิกายน 2569"
    with tempfile.TemporaryDirectory() as t:
        for i in range(23):
            write_json(Path(t) / "lotto" / f"2024-{i // 2 + 1:02d}-{'01' if i % 2 == 0 else '16'}", normalize_thai(glo_style, d))
        rebuild_lists(t)
        p1 = json.loads((Path(t) / "list" / "1").read_text(encoding="utf-8"))["response"]
        p3 = json.loads((Path(t) / "list" / "3").read_text(encoding="utf-8"))["response"]
        assert len(p1) == 10 and len(p3) == 3 and p1[0]["id"] > p1[-1]["id"], "pagination"
        assert not write_json(Path(t) / "latest", json.loads((Path(t) / "latest").read_text(encoding="utf-8"))), "ไม่เขียนซ้ำ"
    selftest_scrapers()
    selftest_gsb()
    with tempfile.TemporaryDirectory() as t:
        g = build_lao("4036")
        add_history(t, "lao", g, "1 พฤศจิกายน 2569"); add_history(t, "lao", build_lao("1234"), "3 พฤศจิกายน 2569")
        add_history(t, "lao", g, "1 พฤศจิกายน 2569"); assert not add_history(t, "lao", g, "ดึงเมื่อ 01/11/2026 10:00")
        hs = json.loads((Path(t) / "other" / "lao-history").read_text(encoding="utf-8"))["draws"]
        assert [x["date"] for x in hs] == ["3 พฤศจิกายน 2569", "1 พฤศจิกายน 2569"], hs
    print("selftest ผ่านทั้งหมด ✔")


def main():
    ap = argparse.ArgumentParser(description="LottoBot")
    sub = ap.add_subparsers(dest="cmd", required=True)
    def common(p):
        p.add_argument("--out", default="site"); p.add_argument("--config", default="sources.json")
    p = sub.add_parser("fetch"); common(p); p.add_argument("--only"); p.add_argument("--backfill-months", type=int, default=0); p.set_defaults(f=cmd_fetch)
    p = sub.add_parser("probe"); common(p); p.set_defaults(f=cmd_probe)
    p = sub.add_parser("watch"); common(p); p.set_defaults(f=cmd_watch)
    p = sub.add_parser("add"); common(p); p.add_argument("lottery", choices=["lao", "hanoi", "my", "yeekee", "gsb"])
    for k in ["n4", "special", "first", "top3", "bottom2", "numbers", "last3", "last2", "date", "round"]:
        p.add_argument("--" + k)
    p.set_defaults(f=cmd_add)
    p = sub.add_parser("backfill"); common(p); p.add_argument("lottery", choices=["lao", "hanoi", "my", "gsb"])
    p.add_argument("--days", type=int, default=30); p.add_argument("--start"); p.add_argument("--to"); p.add_argument("--delay", type=float, default=2.0)
    p.set_defaults(f=cmd_backfill)
    p = sub.add_parser("serve"); common(p); p.add_argument("--port", type=int, default=8787); p.set_defaults(f=cmd_serve)
    p = sub.add_parser("selftest"); p.set_defaults(f=cmd_selftest)
    a = ap.parse_args(); a.f(a)


if __name__ == "__main__":
    main()
