// 通过 CDP 连真实 Edge，执行 JS 探测页面真实状态
const http = require("http");
const WebSocket = require("ws");

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
          p ? res(p.webSocketDebuggerUrl) : rej(new Error("no page"));
        });
      })
      .on("error", rej);
  });
}

(async () => {
  const wsUrl = await getWsUrl();
  const ws = new WebSocket(wsUrl, { maxPayload: 50 * 1024 * 1024 });
  let id = 0;
  const send = (method, params = {}) =>
    new Promise((resolve) => {
      const mid = ++id;
      const onMsg = (ev) => {
        const m = JSON.parse(ev.data);
        if (m.id === mid) {
          ws.off("message", onMsg);
          resolve(m);
        }
      };
      ws.on("message", onMsg);
      ws.send(JSON.stringify({ id: mid, method, params }));
    });

  ws.on("open", async () => {
    // 先启用 Runtime，抓 console
    await send("Runtime.enable");
    // 刷新页面确保拿到完整加载
    await send("Page.reload", { ignoreCache: true });
    await new Promise((r) => setTimeout(r, 2500));

    const expr = `JSON.stringify({
      secAll: !!document.getElementById('secAll'),
      secAllText: (document.getElementById('secAll')||{}).textContent,
      secJingSai: (document.getElementById('sec竞赛')||{}).textContent,
      sectionsCount: document.querySelectorAll('#sectionsNav .sec').length,
      total: (document.getElementById('total')||{}).textContent,
      sTotal: (document.getElementById('sTotal')||{}).textContent,
      cards: document.querySelectorAll('article.card').length,
      secTexts: [...document.querySelectorAll('#sectionsNav .sc')].map(e=>e.textContent)
    })`;
    const m = await send("Runtime.evaluate", {
      expression: expr,
      returnByValue: true,
    });
    console.log("=== 真实Edge探测结果 ===");
    console.log(
      JSON.stringify(JSON.parse(m.result.result.value), null, 2)
    );
    process.exit(0);
  });
  ws.on("error", (e) => {
    console.log("WS错误:", e.message);
    process.exit(1);
  });
})();
