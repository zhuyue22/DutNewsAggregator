// 用 Node 22 原生 WebSocket 连 Edge CDP，确认真实浏览器的 DOM/JS 状态
const http = require("http");

function getWsUrl() {
  return new Promise((res, rej) => {
    http
      .get("http://127.0.0.1:9222/json", (r) => {
        let d = "";
        r.on("data", (c) => (d += c));
        r.on("end", () => {
          const arr = JSON.parse(d);
          const p = arr.find(
            (x) =>
              x.type === "page" &&
              String(x.url).startsWith("http://127.0.0.1:8799")
          );
          p ? res(p.webSocketDebuggerUrl) : rej(new Error("no page: " + d.slice(0,200)));
        });
      })
      .on("error", rej);
  });
}

(async () => {
  const wsUrl = await getWsUrl();
  console.log("连上:", wsUrl);
  const ws = new WebSocket(wsUrl);
  let id = 0;
  const pending = new Map();
  ws.onmessage = (ev) => {
    const m = JSON.parse(ev.data);
    if (m.id && pending.has(m.id)) {
      pending.get(m.id)(m);
      pending.delete(m.id);
    }
  };
  const send = (method, params = {}) =>
    new Promise((resolve) => {
      const mid = ++id;
      pending.set(mid, resolve);
      ws.send(JSON.stringify({ id: mid, method, params }));
    });

  await new Promise((r) => (ws.onopen = r));
  await send("Runtime.enable");
  await send("Page.enable");
  await send("Page.reload", { ignoreCache: true });
  await new Promise((r) => setTimeout(r, 3000));

  const expr = `JSON.stringify({
    secAll: !!document.getElementById('secAll'),
    secAllText: (document.getElementById('secAll')||{}).textContent,
    sTotal: (document.getElementById('sTotal')||{}).textContent,
    cards: document.querySelectorAll('article.card').length,
    secTexts: [...document.querySelectorAll('#sectionsNav .sc')].map(e=>e.textContent),
    total: (document.getElementById('total')||{}).textContent,
    hasJQ: typeof window.$,
    dollarIsFn: typeof document.getElementById
  })`;
  const m = await send("Runtime.evaluate", { expression: expr, returnByValue: true });
  console.log("=== 真实Edge reload后状态 ===");
  console.log(m.result.result.value);
  process.exit(0);
})();
