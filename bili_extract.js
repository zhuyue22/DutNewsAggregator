// 解析 B站搜索页 window.__pinia (devalue IIFE)，从 stdin 读入页面 HTML，stdout 输出 JSON
// 输出: {users:[{mid,uname,official}], videos:[{bvid,author,mid,title,desc,pubdate}]}
let html = '';
process.stdin.setEncoding('utf8');
process.stdin.on('data', c => html += c);
process.stdin.on('end', () => {
  try {
    const i = html.indexOf('window.__pinia=');
    if (i < 0) { console.log('{"users":[],"videos":[],"err":"no pinia"}'); return; }
    let s = html.slice(i + 'window.__pinia='.length);
    const end = s.indexOf('</script>');
    if (end > 0) s = s.slice(0, end);
    s = s.trim().replace(/;+\s*$/, '');
    const data = eval('(' + s + ')');
    const users = [], videos = [];
    (function walk(o) {
      if (!o || typeof o !== 'object') return;
      if (Array.isArray(o)) { o.forEach(walk); return; }
      if (o.result_type === 'bili_user' && Array.isArray(o.data)) {
        for (const u of o.data) {
          users.push({ mid: u.mid, uname: u.uname || '',
            official: (u.official_verify && u.official_verify.desc) || '' });
        }
      }
      if (o.result_type === 'video' && Array.isArray(o.data)) {
        for (const v of o.data) {
          videos.push({
            bvid: v.bvid || '', author: v.author || '', mid: v.mid || 0,
            title: (v.title || '').replace(/<[^>]+>/g, ''),
            desc: (v.description || '').replace(/\s+/g, ' ').trim(),
            pubdate: v.pubdate || v.senddate || 0,
          });
        }
      }
      for (const k of Object.keys(o)) walk(o[k]);
    })(data);
    console.log(JSON.stringify({ users, videos }));
  } catch (e) {
    console.log(JSON.stringify({ users: [], videos: [], err: String(e) }));
  }
});
