const fs = require('fs');
let s = fs.readFileSync('_bili_pinia.js.txt', 'utf8');
s = s.replace(/^[\s\S]*?window\.__pinia=/, '');
const data = eval('(' + s + ')');
// 找搜索结果
function walk(o, path, out) {
  if (!o || typeof o !== 'object') return;
  if (Array.isArray(o)) { o.forEach((v, i) => walk(v, path + '[' + i + ']', out)); return; }
  if (o.result_type && Array.isArray(o.data)) {
    out.push({ type: o.result_type, n: o.data.length, sample: o.data.slice(0, 3) });
  }
  for (const k of Object.keys(o)) walk(o[k], path + '.' + k, out);
}
const out = [];
walk(data, '', out);
for (const g of out) {
  console.log('GROUP', g.type, g.n);
  for (const v of g.sample) {
    if (g.type === 'video') console.log('  V', v.bvid, '|', v.author, '|', new Date(v.pubdate * 1000).toISOString().slice(0, 10), '|', (v.title || '').replace(/<[^>]+>/g, '').slice(0, 30));
    else if (g.type === 'bili_user') console.log('  U', v.mid, v.uname, 'fans=', v.fans, '|', (v.official_verify || {}).desc || '');
    else console.log('  ?', JSON.stringify(v).slice(0, 100));
  }
}
