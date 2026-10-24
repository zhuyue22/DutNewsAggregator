
const DATA = window.DUT_DATA || {meta:{},articles:[]};
const arts = DATA.articles || [];
// 弹窗“我要看看”跳转定位：?focus=<标题>
const FOCUS = new URLSearchParams(location.search).get('focus') || '';
document.getElementById('gen').textContent = DATA.meta.generated || '–';
document.getElementById('total').textContent = DATA.meta.total ?? arts.length;
document.getElementById('pjN').textContent = DATA.meta.panjinCount ?? arts.filter(a=>a.campus==='盘锦校区').length;

/* ---------- 打开页面时自检：数据完整性 / 新鲜度 ---------- */
(function selfCheck(){
  const el = document.getElementById('health');
  const problems = [];
  if(!arts.length){ problems.push('数据为空'); }
  else{
    const s = arts[0];
    for(const k of ['title','url','date','summary','campus','category','importance'])
      if(!(k in s)) problems.push('字段缺失:'+k);
  }
  const gen = DATA.meta.generated;
  if(gen){
    const hrs = (Date.now()-new Date(gen.replace(' ','T')))/36e5;
    if(hrs > 36) problems.push(`数据已 ${Math.round(hrs)} 小时未更新（正常应每日 7:30 与开机时自动刷新）`);
  } else problems.push('缺少更新时间元数据');
  if(!problems.length) return;
  el.style.display='block';
  el.textContent = '⚠️ 自检发现：' + problems.join('；') +
    '。修复：重启电脑（开机会自动重新采集），或手动运行 collector.py；排查日志见 data\\collect.log';
})();

const F = {campus:new Set(), category:new Set(), audience:new Set(), college:new Set(), site:new Set(), tags:new Set(), flags:new Set()};
const P = {baoyan:false, deadline:false, freshman:true, unread:false, fav:false};
/* ---------- 已读 / 收藏（localStorage） ---------- */
const READ = new Set(JSON.parse(localStorage.getItem('dut_read')||'[]'));
const FAV = new Set(JSON.parse(localStorage.getItem('dut_fav')||'[]'));
function saveLocal(k, set){
  let arr=[...set];
  if(arr.length>5000) arr=arr.slice(-5000);
  localStorage.setItem(k, JSON.stringify(arr));
}
const $ = id => document.getElementById(id);
const cnt = (fn) => arts.filter(fn).length;

/* ---------- 顶部预设 ---------- */
function bindPreset(id, key){
  $(id).onclick = ()=>{
    P[key]=!P[key];
    $(id).classList.toggle('on', P[key]);
    render();
  };
}
bindPreset('pBaoyan','baoyan'); bindPreset('pDeadline','deadline');
bindPreset('pUnread','unread'); bindPreset('pFav','fav'); bindPreset('pFreshman','freshman');

/* ---------- 构建筛选器 ---------- */
function chipGroup(el, items, key){
  el.innerHTML = '';
  items.forEach(([label,n])=>{
    const c = document.createElement('span');
    c.className='chip'; c.innerHTML = `${label}<span class="n">${n}</span>`;
    c.onclick = ()=>{
      F[key].has(label)? F[key].delete(label) : F[key].add(label);
      c.classList.toggle('on'); render();
    };
    el.appendChild(c);
  });
}
function countBy(fn){
  const m=new Map();
  arts.forEach(a=>fn(a).forEach(k=>m.set(k,(m.get(k)||0)+1)));
  return [...m.entries()].sort((x,y)=>y[1]-x[1]);
}
chipGroup($('fCampus'), [['盘锦校区',cnt(a=>a.campus==='盘锦校区')],['主校区·全校',cnt(a=>a.campus==='主校区')]], 'campus');
chipGroup($('fCat'), countBy(a=>[a.category]), 'category');
chipGroup($('fGrade'), countBy(a=>a.audience||[]), 'audience');
chipGroup($('fCollege'), countBy(a=>a.college||[]).slice(0,24), 'college');
chipGroup($('fSite'), countBy(a=>[a.source]), 'site');
chipGroup($('fTags'), countBy(a=>a.tags).slice(0,50), 'tags');
chipGroup($('fFlags'), [['🎯 保研相关',cnt(a=>a.baoyan)],['⭐ 已收藏',cnt(a=>FAV.has(a.url))],['★★★★★ 重要',cnt(a=>a.importance===5)],['★★★★ 以上',cnt(a=>a.importance>=4)],['学生工作相关',cnt(a=>a.studentWork)],['涉及盘锦',cnt(a=>a.tags.includes('涉及盘锦'))],['有截止时间',cnt(a=>a.deadline)],['未读',cnt(a=>!READ.has(a.url))]], 'flags');
document.querySelectorAll('.clr').forEach(b=>b.onclick=()=>{
  F[b.dataset.clr].clear();
  const map={campus:'fCampus',category:'fCat',audience:'fGrade',college:'fCollege',site:'fSite',tags:'fTags',flags:'fFlags'};
  document.querySelectorAll(`#${map[b.dataset.clr]} .chip`).forEach(c=>c.classList.remove('on'));
  render();
});

/* ---------- 过滤 + 渲染 ---------- */
function inRange(date){
  const lim=$('limit').value; if(!date||lim==='全部') return true;
  const d=new Date(date); const now=new Date();
  if(lim==='本周'){const w=new Date(now);w.setDate(now.getDate()-7);return d>=w;}
  if(lim==='本月'){const m=new Date(now);m.setDate(now.getDate()-30);return d>=m;}
  return true;
}
function match(a){
  if(P.freshman && a.staff) return false;
  if(P.baoyan && !a.baoyan) return false;
  if(P.deadline && !a.deadline) return false;
  if(P.unread && READ.has(a.url)) return false;
  if(P.fav && !FAV.has(a.url)) return false;
  if(F.campus.size){
    const c=a.campus==='盘锦校区'?'盘锦校区':'主校区·全校';
    if(!F.campus.has(c)) return false;
  }
  if(F.category.size && !F.category.has(a.category)) return false;
  if(F.audience.size && !(a.audience||[]).some(g=>F.audience.has(g))) return false;
  if(F.college.size && !(a.college||[]).some(g=>F.college.has(g))) return false;
  if(F.site.size && !F.site.has(a.source)) return false;
  if(F.tags.size && !a.tags.some(t=>F.tags.has(t))) return false;
  if(F.flags.size){
    const ok=[...F.flags].some(f=>{
      if(f.startsWith('🎯')) return a.baoyan;
      if(f==='⭐ 已收藏') return FAV.has(a.url);
      if(f==='未读') return !READ.has(a.url);
      if(f==='有截止时间') return !!a.deadline;
      if(f.startsWith('★★★★★')) return a.importance===5;
      if(f.startsWith('★★★★')) return a.importance>=4;
      if(f==='学生工作相关') return a.studentWork;
      if(f==='涉及盘锦') return a.tags.includes('涉及盘锦');
      return false;
    });
    if(!ok) return false;
  }
  const q=$('q').value.trim().toLowerCase();
  if(q && !(a.title.toLowerCase().includes(q)||a.summary.toLowerCase().includes(q)||a.tags.join(' ').toLowerCase().includes(q))) return false;
  return inRange(a.date);
}
function render(){
  let list = arts.filter(match);
  const sortBy=$('sort').value;
  list.sort((x,y)=>{
    if(P.fav && FAV.has(x.url)!==FAV.has(y.url)) return FAV.has(x.url)?-1:1; // 收藏优先
    if(sortBy==='imp') return (y.importance-x.importance||y.date.localeCompare(x.date));
    if(sortBy==='deadline') return ((x.deadline||'9999')<(y.deadline||'9999')?-1:1);
    return y.date.localeCompare(x.date);
  });
  $('sTotal').textContent=list.length;
  $('sPj').textContent=list.filter(a=>a.campus==='盘锦校区').length;
  $('sHigh').textContent=list.filter(a=>a.importance>=4).length;
  const wk=new Date(); wk.setDate(wk.getDate()-7);
  $('sWeek').textContent=list.filter(a=>a.date && new Date(a.date)>=wk).length;
  $('sBy').textContent=list.filter(a=>a.baoyan).length;
  const el=$('list');
  if(!list.length){ el.innerHTML='<div class="empty">没有符合条件的信息，试试减少筛选条件</div>'; return; }
  const today=new Date(); today.setHours(0,0,0,0);
  el.innerHTML = list.map(a=>{
    const pj=a.campus==='盘锦校区';
    const stars='★'.repeat(a.importance)+'<span style="color:#d5dae2">'+'★'.repeat(5-a.importance)+'</span>';
    const tags=a.tags.map(t=>`<span class="chip" onclick="quickTag('${t}')">${t}</span>`).join('');
    const collegeChip=(a.college&&a.college.length)?`<span class="lt col"><b>学院</b>${a.college.join('、')}</span>`:'';
    const layers=`<span class="lt obj"><b>对象</b>${(a.audience||['全体学生']).join('、')}</span>`
      +`<span class="lt cat2"><b>分类</b>${a.category}</span>`
      +`<span class="lt camp"><b>校区</b>${pj?'盘锦校区':'主校区·全校'}${a.tags.includes('涉及盘锦')?'(涉盘锦)':''}</span>`
      +collegeChip
      +`<span class="lt site"><b>来源</b>${a.source}</span>`;
    let dlBadge='';
    if(a.deadline){
      const days=Math.round((new Date(a.deadline)-today)/86400000);
      const txt= days<0 ? `已截止 ${a.deadline.slice(5)}` : days===0 ? `今天截止！` : days===1?'明天截止':`还剩${days}天`;
      dlBadge=`<span class="dl ${days<=3?'urgent':'soon'}">⏰ ${a.deadline.slice(5)} ${txt}</span>`;
    }
    const byTag=a.baoyan?`<span class="badge" style="background:#e8f7ee;color:#1e8e5a">🎯保研相关</span>`:'';
    return `<article class="card ${pj?'pj':'zc'}${READ.has(a.url)?' read':''}" data-url="${a.url.replace(/"/g,'&quot;')}">
      <span class="favbtn${FAV.has(a.url)?' on':''}" title="${FAV.has(a.url)?'取消收藏':'收藏'}">${FAV.has(a.url)?'★':'☆'}</span>
      <div class="top">
        <span class="badge ${pj?'pj':'zc'}">${pj?'盘锦校区':'主校区·全校'}</span>
        <span class="badge cat">${a.category}</span>
        ${byTag}${dlBadge}
        <span class="stars" title="重要度 ${a.importance}/5 ${a.importanceReason?'· '+a.importanceReason:''}">${stars}</span>
        <span class="date">📅 ${a.date||'日期待查'}</span>
        <span class="src">${a.source}${a.studentWork?' · 学生工作':''}</span>
      </div>
      <h2><a href="${a.url}" target="_blank" rel="noopener">${a.title}</a></h2>
      <div class="sum">${a.summary}</div>
      <div class="tags">${layers}${tags}</div>
    </article>`;
  }).join('');
  if(FOCUS){
    const idx=list.findIndex(a=>a.title===FOCUS);
    if(idx>=0){
      const card=el.querySelectorAll('article.card')[idx];
      if(card){ card.classList.add('flash'); card.scrollIntoView({behavior:'smooth',block:'center'}); }
    }
  }
}
/* ---------- 事件委托：点开链接记已读，点星标切换收藏 ---------- */
$('list').addEventListener('click', e=>{
  const card=e.target.closest('article.card');
  if(!card) return;
  const url=card.dataset.url;
  if(e.target.classList.contains('favbtn')){
    if(FAV.has(url)){FAV.delete(url);e.target.classList.remove('on');e.target.textContent='☆';e.target.title='收藏';}
    else{FAV.add(url);e.target.classList.add('on');e.target.textContent='★';e.target.title='取消收藏';}
    saveLocal('dut_fav',FAV);
    return;
  }
  if(e.target.closest('a') && !READ.has(url)){
    READ.add(url); saveLocal('dut_read',READ); card.classList.add('read');
  }
});
window.quickTag = t=>{
  F.tags.add(t);
  document.querySelectorAll('#fTags .chip').forEach(c=>{ if(c.textContent.includes(t)) c.classList.add('on'); });
  render();
  window.scrollTo({top:0,behavior:'smooth'});
};
$('q').addEventListener('input',render);
$('sort').addEventListener('change',render);
$('limit').addEventListener('change',render);
render();
