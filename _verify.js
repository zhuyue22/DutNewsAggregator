// jsdom 验证脚本：加载 index.html 的 HTML+内联JS，捕获运行时错误
const { JSDOM } = require("C:/Users/赵朔/.workbuddy/binaries/node/workspace/node_modules/jsdom");
const fs = require("fs");

const html = fs.readFileSync("index.html", "utf-8");

// 用 runScripts:"dangerously" 执行内联脚本，resources:"usable" 加载外部 data.js
const dom = new JSDOM(html, {
  url: "http://127.0.0.1:8799/index.html",
  runScripts: "dangerously",
  resources: "usable",
  pretendToBeVisual: true,
  beforeParse(window) {
    // 捕获页面脚本的错误
    window.addEventListener("error", (e) => {
      console.log("捕获到页面JS错误:", e.message, "at", e.filename, ":", e.lineno);
    });
  },
});

// 等外部脚本加载完成
dom.window.addEventListener("load", () => {
  setTimeout(() => {
    const doc = dom.window.document;
    console.log("=== 加载完成后 ===");
    console.log("secAll 元素存在:", !!doc.getElementById("secAll"));
    console.log("secAll 内容:", doc.getElementById("secAll")?.textContent);
    console.log("sec竞赛 存在:", !!doc.getElementById("sec竞赛"));
    console.log("sec竞赛 内容:", doc.getElementById("sec竞赛")?.textContent);
    console.log("sections数:", doc.querySelectorAll("#sectionsNav .sec").length);
    console.log("total 内容:", doc.getElementById("total")?.textContent);
    console.log("sTotal 内容:", doc.getElementById("sTotal")?.textContent);
    console.log("卡片数:", doc.querySelectorAll("article.card").length);
    // 模拟点击"竞赛"板块
    const secs = [...doc.querySelectorAll("#sectionsNav .sec")];
    const b = secs.find(x => x.dataset.sec === "竞赛");
    if (b) { b.click(); }
    setTimeout(() => {
      console.log("=== 点击'竞赛'板块后 ===");
      console.log("sTotal 内容:", doc.getElementById("sTotal")?.textContent);
    }, 100);
  }, 800);
});
