"""ตัวอ่านผลหวยแบบ "อิงโครงสร้างของผลรางวัล" (ไม่พึ่ง CSS selector) — stdlib ล้วน

หลักการ: ตัด HTML เหลือข้อความ → ลบวันที่/เวลา → แตกเป็นกลุ่มตัวเลข → หาลำดับตัวเลขที่ "จำนวนหลักตรงตามรูปแบบหวยชนิดนั้นเป๊ะ"
 - หวยฮานอย (XSMB): 27 ตัว  ĐB 5 | G1 5 | G2 5×2 | G3 5×6 | G4 4×4 | G5 4×6 | G6 3×3 | G7 2×4
 - หวยมาเลย์ 4D:   23 ตัว  ที่ 1-3 + Special 10 + Consolation 10 (ทุกตัว 4 หลัก)
 - หวยลาว:          เลข 4 หลักตัวแรกหลัง anchor
ถ้าไม่ตรงรูปแบบ = โยนข้อผิดพลาด ไม่เดา ไม่บันทึกข้อมูลผิด
"""
import re

BLOCK_MARKERS = ("just a moment", "cf-chl", "attention required", "checking your browser", "cf-browser-verification",
                 "enable javascript and cookies", "verify you are human", "access denied", "captcha")
XSMB_PATTERN = [5, 5] + [5] * 2 + [5] * 6 + [4] * 4 + [4] * 6 + [3] * 3 + [2] * 4
MONTHS = (r"jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|"
          r"มกราคม|กุมภาพันธ์|มีนาคม|เมษายน|พฤษภาคม|มิถุนายน|กรกฎาคม|สิงหาคม|กันยายน|ตุลาคม|พฤศจิกายน|ธันวาคม")
NOISE = [
    r"\d{1,2}\s*[/\-.]\s*\d{1,2}\s*[/\-.]\s*\d{2,4}",            # 12/11/2026, 12-11-26
    r"\d{4}\s*[/\-.]\s*\d{1,2}\s*[/\-.]\s*\d{1,2}",               # 2026-11-12
    r"\d{1,2}\s+(?:%s)[a-zก-๙.]*\s+\d{4}" % MONTHS,                # 12 Nov 2026 / 12 พฤศจิกายน 2569
    r"(?:ngày\s*)?\d{1,2}\s*tháng\s*\d{1,2}(?:\s*năm\s*\d{4})?",    # ngày 12 tháng 11 năm 2026
    r"\d{1,2}\s*:\s*\d{2}(?:\s*:\s*\d{2})?",                       # 18:15
    r"(?<!\d)\d{1,2}h\d{0,2}(?![a-z0-9])",                      # 18h15 (ติดกัน ไม่กินเลขที่ตามด้วยคำ)
    r"\b\d{1,5}\s*/\s*\d{2,4}\b",                                  # Draw No 2945/26
]


class Blocked(ValueError):
    """เว็บบล็อกบอท/ขึ้นหน้า challenge — ไม่พยายามเลี่ยง ให้แจ้งเตือนผู้ดูแลแทน"""


def check_block(status, raw):
    if status in (403, 429, 503):
        raise Blocked(f"HTTP {status} — เว็บอาจป้องกันบอท/จำกัดความถี่ (ไม่พยายามหลบเลี่ยง)")
    if status != 200:
        raise ValueError(f"HTTP {status}")
    if len(raw) < 30000 and any(m in raw.lower() for m in BLOCK_MARKERS):
        raise Blocked("พบหน้า challenge/captcha แทนผลรางวัล (ไม่พยายามหลบเลี่ยง)")


def clean(text, conf=None):
    conf = conf or {}
    if conf.get("anchor"):
        m = re.search(conf["anchor"], text, re.S | re.I)
        if not m:
            raise ValueError("ไม่พบ anchor ในหน้าเว็บ (โครงหน้าอาจเปลี่ยน)")
        text = text[m.end():]
    if conf.get("stop"):
        m = re.search(conf["stop"], text, re.S | re.I)
        if m:
            text = text[:m.start()]
    for pat in NOISE + list(conf.get("exclude") or []):
        text = re.sub(pat, " ", text, flags=re.I)
    return text


def tokens(text):
    """กลุ่มตัวเลขต่อเนื่อง ยาว ≥2 (ตัดเลขหลักเดียวจากป้าย เช่น G1, 1st)"""
    return [t for t in re.findall(r"(?<!\d)\d+(?!\d)", text) if len(t) >= 2]


def _window(toks, lens):
    n = len(lens)
    for i in range(len(toks) - n + 1):
        if all(len(toks[i + j]) == lens[j] for j in range(n)):
            return toks[i:i + n]
    return None


def parse_xsmb(text, conf=None):
    """คืน dict: db, g1, g2[2], g3[6], g4[4], g5[6], g6[3], g7[4] — รองรับทั้งเรียง ĐB→G7 และ G7→ĐB"""
    toks = tokens(clean(text, conf))
    w = _window(toks, XSMB_PATTERN)
    if w is None:
        w = _window(toks, XSMB_PATTERN[::-1])
        if w is not None:
            w = w[::-1]
    if w is None:
        raise ValueError("ไม่พบผล XSMB ครบ 27 ตัวตามรูปแบบ (5,5,5×2,5×6,4×4,4×6,3×3,2×4)")
    i = 0
    out = {}
    for key, n in (("db", 1), ("g1", 1), ("g2", 2), ("g3", 6), ("g4", 4), ("g5", 6), ("g6", 3), ("g7", 4)):
        out[key] = w[i] if n == 1 else w[i:i + n]
        i += n
    return out


def parse_4d(text, conf=None):
    """เลข 4 หลักต่อเนื่อง 23 ตัว: ที่ 1, 2, 3, Special×10, Consolation×10"""
    toks = tokens(clean(text, conf))
    w = _window(toks, [4] * 23)
    if w is None:
        raise ValueError("ไม่พบเลข 4D ครบ 23 ตัวต่อเนื่อง (ที่ 1-3 + Special 10 + Consolation 10)")
    return w


def parse_lao_full(text, conf=None):
    """หวยลาวพัฒนา: ต้องพบเลข 6 หลัก + ท้าย 4/3/2 ที่สอดคล้องกันเท่านั้น (ไม่มีการเดาเลข 4 หลักตัวแรก — เคยหยิบ "2569" จากปี พ.ศ. มาเป็นผล)"""
    toks = tokens(clean(text, conf))
    for i in range(len(toks) - 3):
        a, b, c, d = toks[i:i + 4]
        if len(a) == 6 and len(b) == 4 and len(c) == 3 and len(d) == 2 and a.endswith(b) and b.endswith(c) and c.endswith(d):
            return a
    raise ValueError("ไม่พบผลหวยลาว (เลข 6 หลัก + ท้าย 4/3/2 ที่สอดคล้องกัน) — ยังไม่ออกผลหรือโครงหน้าเปลี่ยน")


def parse_lao(text, conf=None):
    """คืนเลขท้าย 4 ตัว (ใช้ parse_lao_full ถ้าต้องการเลข 6 หลัก)"""
    return parse_lao_full(text, conf)[-4:]


TH_M = ["มกราคม", "กุมภาพันธ์", "มีนาคม", "เมษายน", "พฤษภาคม", "มิถุนายน", "กรกฎาคม", "สิงหาคม", "กันยายน", "ตุลาคม", "พฤศจิกายน", "ธันวาคม"]
TH_A = ["ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.", "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.", "พ.ย.", "ธ.ค."]


def anchor_date(text, d, style):
    """หน้าที่รวมหลายงวด: ตัดให้เหลือเฉพาะบล็อกของวันที่ d — style 'th' = วันที่ไทย (ตัดที่วันที่ถัดไป), 'num' = dd/mm/yyyy (ต้องมีในหน้า)
    ไม่พบวันที่ = ValueError (ไม่เดา กันหน้าที่เด้งไปงวดล่าสุดแล้วติดป้ายวันที่ผิด)"""
    if style == "numblock":
        # หน้ารวมหลายงวด (เช่น xosodaiphat): วันที่แต่ละงวดเป็นหัวข้อนำหน้าตารางผล — เอาเฉพาะ "ช่วงที่ตามหลังหัวข้อวันที่ d จนถึงวันที่ถัดไป"
        # ที่มีตัวเลขพอสำหรับผลเต็ม (ข้ามลิงก์/เมนูที่มีวันที่แต่ไม่มีผล) ไม่ปนกับงวดอื่น
        dre = r"(?<!\d)\d{1,2}\s*[/.\-]\s*\d{1,2}\s*[/.\-]\s*\d{4}(?!\d)"
        want = re.compile(rf"(?<!\d)0?{d.day}\s*[/.\-]\s*0?{d.month}\s*[/.\-]\s*{d.year}(?!\d)")
        for m in want.finditer(text):
            rest = text[m.end():]
            nx = re.search(dre, rest)
            sec = rest[:nx.start()] if nx else rest
            sec = re.sub(r"(?<![0-9A-Za-z])\d{1,2}[A-Z]{2}(?![0-9A-Za-z])", " ", sec)   # "Mã ĐB" เช่น 15LH — ไม่ใช่เลขรางวัล
            if len(tokens(clean(sec))) >= 27:
                return sec
        raise ValueError(f"ไม่พบหัวข้อวันที่ {d:%d/%m/%Y} ที่มีผลครบตามหลังในหน้า (ยังไม่ออก/หน้าไม่มีงวดนี้)")
    if style == "thblock":
        # หน้ารวมหลายงวด วันที่ไทย (kapook /laolottery): หัวข้อ "ตรวจหวยลาว 17 พฤศจิกายน 2569" ตามด้วย 6/4/3/2 — ข้ามลิงก์ในแถบข้างที่มีแต่วันที่
        y = d.year + 543
        mon = "|".join(map(re.escape, [TH_M[d.month - 1], TH_A[d.month - 1]]))
        allm = "|".join(map(re.escape, TH_M + TH_A))
        for m in re.finditer(rf"(?<!\d){d.day}\s*(?:{mon})\s*(?:{y}|{y % 100})(?!\d)", text):
            rest = text[m.end():]
            nx = re.search(rf"(?<!\d)\d{{1,2}}\s*(?:{allm})\s*(?:\d{{4}}|\d{{2}})(?!\d)", rest)
            sec = rest[:nx.start()] if nx else rest
            try:
                parse_lao_full(sec)
                return sec
            except ValueError:
                continue
        raise ValueError(f"ไม่พบงวด {d.day} {TH_M[d.month - 1]} {y} ที่มีเลข 6/4/3/2 ครบตามหลังในหน้า (ยังไม่ออก/หน้าไม่มีงวดนี้)")
    if style == "num":
        m = re.search(rf"(?<!\d)0?{d.day}\s*[/.\-]\s*0?{d.month}\s*[/.\-]\s*{d.year}(?!\d)", text)
        if not m:
            raise ValueError(f"ไม่พบวันที่ {d:%d/%m/%Y} ในหน้า (ยังไม่ออก/หน้าเด้งไปงวดอื่น)")
        return text[m.end():]
    y = d.year + 543
    mon = "|".join(map(re.escape, [TH_M[d.month - 1], TH_A[d.month - 1]]))
    m = re.search(rf"(?<!\d){d.day}\s*(?:{mon})\s*(?:{y}|{y % 100})(?!\d)", text)
    if not m:
        raise ValueError(f"ไม่พบงวดวันที่ {d.day} {TH_M[d.month - 1]} {y} ในหน้า")
    rest = text[m.end():]
    allm = "|".join(map(re.escape, TH_M + TH_A))
    nx = re.search(rf"(?<!\d)\d{{1,2}}\s*(?:{allm})\s*(?:\d{{4}}|\d{{2}})(?!\d)", rest)
    return rest[:nx.start()] if nx else rest


def render_browser(url, wait_ms=6000):
    """ตัวเลือก: เปิดหน้าด้วยเบราว์เซอร์จริงเมื่อผลถูกสร้างด้วย JavaScript (ต้อง pip install playwright)
    ใช้เพื่อ 'เรนเดอร์หน้า' เท่านั้น — เจอ challenge/captcha จะโยน Blocked ไม่พยายามผ่าน"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise ValueError("เปิด render=browser ต้องติดตั้ง playwright ก่อน (pip install playwright && playwright install chromium)")
    with sync_playwright() as p:
        b = p.chromium.launch()
        try:
            pg = b.new_page(user_agent="Mozilla/5.0 (compatible; LottoBot-personal/1.0)")
            resp = pg.goto(url, wait_until="networkidle", timeout=45000)
            pg.wait_for_timeout(wait_ms)
            html = pg.content()
            status = resp.status if resp else 200
        finally:
            b.close()
    check_block(status, html)
    return html
