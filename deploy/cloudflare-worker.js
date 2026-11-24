// Посредник для сайта ANC на Cloudflare Workers (бесплатно).
// Пропускает запросы только к cetatenie.just.ro: https://<worker>/?url=https://cetatenie.just.ro/...
export default {
  async fetch(request) {
    const target = new URL(request.url).searchParams.get("url");
    if (!target) return new Response("missing url", { status: 400 });
    let u;
    try { u = new URL(target); } catch { return new Response("bad url", { status: 400 }); }
    if (u.protocol !== "https:" || u.hostname !== "cetatenie.just.ro") {
      return new Response("forbidden", { status: 403 });
    }
    const resp = await fetch(u.toString(), {
      headers: {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36",
        "Accept-Language": "ro,en;q=0.8",
      },
    });
    return new Response(resp.body, {
      status: resp.status,
      headers: { "Content-Type": resp.headers.get("Content-Type") || "application/octet-stream" },
    });
  },
};
