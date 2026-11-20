// พร็อกซีส่วนตัวของ LottoSnap (Cloudflare Workers ฟรี) — ให้แอปบนเบราว์เซอร์ดึงหน้าผลรางวัลได้ (แก้ปัญหา CORS)
// ติดตั้ง: dash.cloudflare.com → Workers & Pages → Create → Hello World → Edit code → วางไฟล์นี้ → Deploy
// แล้วนำ URL ที่ได้ไปใส่ในแอป ตั้งค่า → "ดึงผลจากเว็บโดยตรง" เป็น  https://ชื่อคุณ.workers.dev/?url={url}
// ความปลอดภัย: อนุญาตเฉพาะโดเมนในรายการนี้ เฉพาะ GET และไม่ส่ง cookie/ข้อมูลของผู้ใช้ไปยังเว็บต้นทาง
const ALLOW = ["xosodaiphat.com", "xosominhngoc.com", "lottery.kapook.com"];

export default {
  async fetch(req) {
    const cors = { "Access-Control-Allow-Origin": "*", "Access-Control-Allow-Methods": "GET", "Cache-Control": "public, max-age=30" };
    if (req.method === "OPTIONS") return new Response(null, { headers: cors });
    if (req.method !== "GET") return new Response("GET only", { status: 405, headers: cors });
    let target;
    try { target = new URL(new URL(req.url).searchParams.get("url")); } catch { return new Response("bad url", { status: 400, headers: cors }); }
    const ok = target.protocol === "https:" && ALLOW.some(h => target.hostname === h || target.hostname.endsWith("." + h));
    if (!ok) return new Response("host not allowed", { status: 403, headers: cors });
    const r = await fetch(target.toString(), { headers: { "User-Agent": "Mozilla/5.0 (compatible; LottoSnap-personal/1.0)", "Accept-Language": "th,en" }, redirect: "manual", cf: { cacheTtl: 30 } });
    if (r.status >= 300 && r.status < 400) return new Response("redirect blocked", { status: 502, headers: cors });   // กันเด้งไปโดเมนนอกรายการ
    return new Response(r.body, { status: r.status, headers: { ...cors, "X-Content-Type-Options": "nosniff", "Content-Disposition": "inline", "Content-Type": "text/plain; charset=utf-8" } });   // ส่งเป็นข้อความล้วน ไม่ให้เบราว์เซอร์รันเป็นหน้าเว็บ
  },
};
