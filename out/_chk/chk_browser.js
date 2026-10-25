
"use strict";
var st=null,conn=null,pend=-1,pendK="",toastT=null,lastSig=null;

function $(id){return document.getElementById(id)}
function el(tag,cls,txt){var e=document.createElement(tag);
  if(cls)e.className=cls;if(txt!=null)e.textContent=txt;return e}

function toast(m){var t=$("toast");t.textContent=m;t.classList.add("show");
  clearTimeout(toastT);toastT=setTimeout(function(){
    t.classList.remove("show")},1800)}

/* 安卓返回手势/返回键经桥调 uiBack：关闭最上层浮层（.mask.show，
 * DOM 靠后=视觉在上，倒序找第一个）——设置面板、「选择谱面 App」及
 * 未来新增的二级/三级页面只要用 .mask 结构就自动被覆盖；
 * 无浮层可关返回 false，原生侧走默认行为 */
function uiBack(){
  var ms=document.querySelectorAll(".mask");
  for(var i=ms.length-1;i>=0;i--)
    if(ms[i].classList.contains("show")){
      ms[i].classList.remove("show");
      return "true";
    }
  return "false";
}

function post(path,body){return fetch(path,{method:"POST",
  headers:{"Content-Type":"application/json"},
  body:JSON.stringify(body||{})}).then(function(r){
    return r.json().then(function(j){return{code:r.status,j:j}})})}

function setConn(on){if(on===conn)return;conn=on;
  var b=$("badge");b.classList.toggle("on",on);
  $("bt").textContent=on?"已连接":"连接断开，重试中"}

function render(){
  if(!st)return;
  var cur=typeof st.cur==="number"?st.cur:-1;
  var sig=[st.busy,st.ready,st.live,cur,st.projName,
    JSON.stringify(st.songs)].join("|");
  if(sig===lastSig)return;      // 无变化不动 DOM：切歌期间每次重绘都是
  lastSig=sig;                  // 一次帧提交（Chromium 合成过渡会闪白）
  var s=st.songs?st.songs[cur]:null;
  // NOW/NEXT 对齐 PC 横幅：显示真实打开的工程名（工程可能不在播放列表
  // 里，cur 为空时按歌名索引查不到）。切歌期间保持切歌前的歌名不动
  // （当前状态行已提示「切换中…」，这里不再重复），切完刷成新值
  if(!st.busy){
    $("now").textContent=st.projName?st.projName:
      (st.ready?"（无打开的工程）":"启动中…");
    var n=st.songs?st.songs[cur+1]:null;
    $("next").textContent=n?n.name:(s?"（末尾）":"—");
  }
  var L=$("list");L.textContent="";
  for(var i=0;i<(st.songs||[]).length;i++){
    var li=el("li");if(i===cur)li.className="cur";
    li.appendChild(el("span","no",String(i+1)));
    li.appendChild(el("span","nm",st.songs[i].name));
    li.appendChild(el("span","du",st.songs[i].dur||""));
    li.addEventListener("click",onRow.bind(null,i,
      st.songs[i].key||""));
    L.appendChild(li);
  }
  $("empty").hidden=st.songs?st.songs.length>0:true;
  var bs=document.querySelectorAll(".tbtn");
  for(var k=0;k<bs.length;k++){var b=bs[k];
    b.disabled=!!st.busy&&b.dataset.cmd!=="panic";
    if(!st.ready&&b.dataset.cmd!=="panic")b.disabled=true;}
}

function fmtT(s){s=Math.max(0,Math.floor(s));return Math.floor(s/60)+":"+
  ("0"+(s%60)).slice(-2)}

/* 播放状态行 + 进度条：pos 每 tick 都变，独立于 render 的大 sig 门
   （render 会重建整个列表 DOM，跟着 pos 刷会一秒两抖），只动这几个节点。
   状态两副本（手机小字/平板大字，断点显隐），一并更新 */
function renderLive(){
  if(!st)return;
  var ts=st.busy?"busy":(st.tstate||"stopped");
  var txt={playing:"播放中",paused:"已暂停",stopped:"未在播放",busy:"切换中…"}[ts];
  var cls="tstate "+(ts==="busy"?"paused":ts);
  var tes=document.querySelectorAll(".tstate");
  for(var i=0;i<tes.length;i++){
    tes[i].textContent=txt;
    tes[i].className=cls;
  }
  var prow=$("prow");
  var cur=typeof st.cur==="number"?st.cur:-1;
  var d=typeof st.dur==="number"?st.dur:0;
  if(cur<0||!d){prow.hidden=true;return}
  prow.hidden=false;
  var pos=typeof st.pos==="number"?st.pos:0;
  $("pfill").style.width=Math.min(100,pos/d*100)+"%";
  $("ptime").textContent=fmtT(pos)+" | "+fmtT(d-pos);
}

function onRow(i,k){
  if(!st||st.busy||!st.ready)return;
  if(st.confirm&&st.open&&i!==st.cur){
    pend=i;pendK=k||"";
    // 「从」名与 PC 弹窗同源：真实工程名优先（cur 为空也拿得到）
    var a=st.projName||(st.songs[st.cur]&&st.songs[st.cur].name),
        b=st.songs[i];
    $("c-text").textContent="确定从《"+(a||"？")+"》切换到《"+
      b.name+"》？当前工程将被关闭（自动保存）。";
    $("m-confirm").classList.add("show");
  }else cmd("switch",i,k);
}
$("c-no").addEventListener("click",function(){
  $("m-confirm").classList.remove("show")});
$("c-yes").addEventListener("click",function(){
  $("m-confirm").classList.remove("show");
  if(pend>=0){
    var i=pend,k=pendK;pend=-1;pendK="";
    /* 确认窗内列表可能被它端重排：按 key 现场重解析，key 失效即中止
       （与桌面新前端同一防线；旧后端 songs 无 key 时按原下标发） */
    if(k&&st&&st.songs){
      var f=-1;
      for(var q=0;q<st.songs.length;q++)
        if(st.songs[q].key===k){f=q;break}
      if(f<0){toast("该曲目已不在播放列表");return}
      i=f}
    cmd("switch",i,k)}
});

var tbs=document.querySelectorAll(".tbtn");
for(var ti=0;ti<tbs.length;ti++){
  tbs[ti].addEventListener("click",function(){cmd(this.dataset.cmd)});
}

function cmd(action,index,key){
  post("/cmd",{action:action,index:index,key:key||undefined})
  .then(function(r){
    if(!r.j||!r.j.ok)toast((r.j&&r.j.error)||"操作失败");
    refresh();
  },function(){setConn(false);toast("发送失败")});
}

function refresh(){
  fetch("/state",{cache:"no-store"}).then(function(r){return r.json()})
    .then(function(j){st=j;setConn(true);render();renderLive()},
          function(e){setConn(false);
            try{toast("刷新失败："+e)}catch(_){}});
  try{if(window.updateAcc)updateAcc()}catch(e){}
  try{if(window.refreshUsageTip)refreshUsageTip()}catch(e){}
  try{if(window.refreshTarget)refreshTarget()}catch(e){}
  try{refreshEnv()}catch(e){}
  // APP 内误连网页端口时自动纠正（端口从 /state 动态获取，不写死）
  try{if(window.CubeApp&&st&&typeof st.appPort==="number"&&st.appPort>0
      &&(location.port||"80")!==String(st.appPort))
    CubeApp.switchToAppPort(String(st.appPort))}catch(e){}
}

function refreshEnv(){
  var n=$("env-notice");if(!n)return;
  var page=n.getAttribute("data-page");
  var inApp=!!window.CubeApp;         // 桥存在=运行在 APP 的 WebView 内
  var msg=null;
  if(page==="app"&&!inApp)
    msg="本页在浏览器中打开——翻谱功能请在 Cube Remote 内使用";
  if(page==="browser"&&inApp){
    var ap=(st&&typeof st.appPort==="number")?st.appPort:null;
    msg=ap===null?"正在获取 APP 连接端口…"
      :"检测到 APP 连接端口为 "+ap+"——正在自动切换…";
  }
  if(msg){n.hidden=false;n.textContent=msg}
  else n.hidden=true;
}
try{refreshEnv()}catch(e){}


setInterval(refresh,1000);
document.addEventListener("visibilitychange",function(){
  if(!document.hidden)refresh()});
refresh();
