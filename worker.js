// Cloudflare Worker – prosljeđuje zahtjeve SofaScore API-ju.
// Zalijepi cijeli ovaj kod u Cloudflare editor i klikni Deploy.
export default {
  async fetch(request) {
    const url = new URL(request.url);

    // propušta samo SofaScore API (/api/v1/...)
    if (request.method !== "GET" || !url.pathname.startsWith("/api/v1/")) {
      return new Response("Not found", { status: 404 });
    }

    const target = "https://www.sofascore.com" + url.pathname + url.search;
    const r = await fetch(target, {
      headers: {
        "User-Agent":
          "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 " +
          "(KHTML, like Gecko) Chrome/130.0 Safari/537.36",
        "Accept": "application/json",
        "Referer": "https://www.sofascore.com/",
      },
    });

    return new Response(r.body, {
      status: r.status,
      headers: { "Content-Type": "application/json; charset=utf-8" },
    });
  },
};
