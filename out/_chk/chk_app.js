
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


/* ---- 设置面板（仅 APP 版页面）：连接设置 + 翻谱设置 ---- */
function updateAcc(){
  if(!window.CubeApp)return;
  var i=$("acc-ind");if(!i)return;
  var ok=CubeApp.accEnabled()===true;
  i.textContent=ok?"无障碍开":"无障碍关";
  i.className="ind"+(ok?" ok":" bad");
}
function refreshUsageTip(){
  if(!window.CubeApp)return;
  var tip=$("usage-tip"),btns=$("usage-btns");if(!tip)return;
  if(CubeApp.usageAccess()===true){tip.style.display="none";btns.style.display="none"}
  else{tip.style.display="block";btns.style.display="flex"}
}
function refreshTarget(){
  if(!window.CubeApp)return;
  var t=$("t-target");if(!t)return;
  var name=CubeApp.turnTarget();
  t.textContent=name||"自动检测（最近使用的第三方应用）";
}
$("t-pick").addEventListener("click",function(){
  if(!window.CubeApp)return;
  var apps=JSON.parse(CubeApp.listApps());
  var box=$("apps-list");box.textContent="";
  for(var i=0;i<apps.length;i++){(function(app){
    var row=el("div","dv");row.style.cursor="pointer";
    row.appendChild(el("span",null,app.name));
    row.addEventListener("click",function(){
      CubeApp.setTurnTarget(app.pkg,app.name);
      $("m-apps").classList.remove("show");
      refreshTarget();toast("谱面 App："+app.name);
    });
    box.appendChild(row);
  })(apps[i])}
  $("m-apps").classList.add("show");
});
$("apps-clear").addEventListener("click",function(){
  if(window.CubeApp)CubeApp.clearTurnTarget();
  $("m-apps").classList.remove("show");
  refreshTarget();toast("已清除，使用自动检测");
});
$("apps-close").addEventListener("click",function(){
  $("m-apps").classList.remove("show")});
$("usage-grant").addEventListener("click",function(){
  if(window.CubeApp)CubeApp.openUsageAccess()});
$("b-acc").addEventListener("click",function(){
  if(window.CubeApp)CubeApp.openAccSettings()});
/* b-dev 仅完整版页面存在（lite 版面板常显、无「设置」入口按钮）；
   不判空会在 lite 页抛 TypeError，杀死整个 script 块——
   openDev/c-save/refresh 轮询全部失联 */
var _bd=$("b-dev");if(_bd)_bd.addEventListener("click",openDev);
/* 翻页方法：四选一分段按钮，选中态存 APP 本机（语义协议由 APP 组装动作） */
function renderMethod(){
  if(!window.CubeApp)return;
  var cur=null;try{cur=CubeApp.turnMethod()}catch(e){}
  var bs=document.querySelectorAll(".m-opt");
  for(var i=0;i<bs.length;i++)
    bs[i].classList.toggle("on",bs[i].getAttribute("data-v")===cur);
}
var _mbs=document.querySelectorAll(".m-opt");
for(var _mi=0;_mi<_mbs.length;_mi++)(function(b){
  b.addEventListener("click",function(){
    if(!window.CubeApp)return;
    if(CubeApp.setTurnMethod(b.getAttribute("data-v"))){
      renderMethod();toast("翻页方法："+b.textContent);
    }else toast("设置失败");
  });
})(_mbs[_mi]);
/* 踩钉转发：有线踏板接本机时捕获按键转发给电脑（电脑端踩钉控制页
   需同步勾选「允许平板转发」；两开关默认都关） */
function renderPedal(){
  if(!window.CubeApp)return;
  var cur=false;try{cur=CubeApp.pedalForward()===true}catch(e){}
  var bs=document.querySelectorAll(".p-opt");
  for(var i=0;i<bs.length;i++)
    bs[i].classList.toggle("on",bs[i].getAttribute("data-v")===(cur?"1":"0"));
}
var _pbs=document.querySelectorAll(".p-opt");
for(var _pi=0;_pi<_pbs.length;_pi++)(function(b){
  b.addEventListener("click",function(){
    if(!window.CubeApp)return;
    var on=b.getAttribute("data-v")==="1";
    if(CubeApp.setPedalForward(on)){
      renderPedal();toast(on?"踩钉转发：开":"踩钉转发：关");
    }else toast("设置失败");
  });
})(_pbs[_pi]);
// 触摸遮罩（面板区域以外）关闭：移动端标准交互
$("m-dev").addEventListener("click",function(e){
  if(e.target===this)$("m-dev").classList.remove("show")});
$("m-apps").addEventListener("click",function(e){
  if(e.target===this)$("m-apps").classList.remove("show")});

function openDev(){
  $("m-dev").classList.add("show");
  $("c-addr").value=location.href;
  refreshInd();
  refreshUsageTip();
  refreshTarget();
  renderMethod();
  renderPedal();
  try{$("app-ver").textContent=CubeApp?CubeApp.version():"?"}catch(e){}
  fetch("/devices",{cache:"no-store"}).then(function(r){return r.json()})
    .then(function(d){renderDev(d);
      $("t-ip").textContent="http://"+d.you+":";},
      function(){toast("取设备列表失败")});
}

function setInd(ok,text){var i=$("t-ind");i.textContent=text;
  i.className="ind"+(ok?" ok":" bad")}

function refreshInd(){
  if(!window.CubeApp)return;
  var p=CubeApp.turnPort();
  $("t-port").value=p;
  setInd(!!p,p?p+" 端口监听中":"未监听");
}

$("t-apply").addEventListener("click",function(){
  if(!window.CubeApp)return;
  var p=$("t-port").value.trim();
  if(!/^[0-9]{2,5}$/.test(p)||parseInt(p,10)<1024||parseInt(p,10)>65535){
    setInd(false,"端口非法");return}
  var ok=CubeApp.setTurnPort(p);
  setInd(ok,ok?p+" 端口监听中":"端口不可用");
  toast(ok?"端口已应用：请同步电脑端「APP 翻谱地址」":"端口被占用或无法监听");
});

$("c-save").addEventListener("click",function(){
  if(!window.CubeApp){toast("桥未就绪");return}
  var url=$("c-addr").value.trim();
  if(!url){toast("地址不能为空");return}
  var r=CubeApp.requestAddressChange(url);
  if(r==="restart"){toast("地址已保存，重启中…")}
  else if(r==="same"){toast("地址未变化");$("c-addr").value=location.href}
  else{toast("已取消：地址保持原值");$("c-addr").value=location.href}
});

function dpr(){return window.devicePixelRatio||1}
function phys(){return{w:Math.round(screen.width*dpr()),
  h:Math.round(screen.height*dpr())}}

function devTest(dir){
  post("/device/test",{dir:dir}).then(function(r){
    toast(r.j&&r.j.ok?"测试已发送：已切到后台":((r.j&&r.j.error)||"发送失败"));
  },function(){toast("发送失败")});
}

function renderDev(d){
  var all=d.devices||[],own=null;
  for(var i=0;i<all.length;i++)if(all[i].ip===d.you)own=all[i];
  var g=el("div","grp");
  if(!own){
    g.appendChild(el("div","sub","本机未登记"));
    var f1=el("div","fld");f1.appendChild(el("label",null,"名字"));
    var inp=el("input");inp.type="text";inp.value="谱台";inp.id="d-name";
    f1.appendChild(inp);g.appendChild(f1);
    var f2=el("div","fld");f2.appendChild(el("label",null,"槽位"));
    var sel=el("select");sel.id="d-slot";
    sel.appendChild(new Option("自动",""));
    for(var s=1;s<=10;s++)sel.appendChild(new Option(String(s),String(s)));
    f2.appendChild(sel);g.appendChild(f2);
  var bts=el("div","btns");
  var bb=el("button","btn pri","认领本机");
  bb.addEventListener("click",function(){
    var body={name:$("d-name").value.trim()||"谱台",screen:phys()};
    var sv=$("d-slot").value;if(sv)body.slot=parseInt(sv,10);
      post("/device/claim",body).then(function(r){
        if(r.j&&r.j.ok){toast("已认领槽位 "+r.j.device.slot);openDev();}
        else toast((r.j&&r.j.error)||"认领失败");
      },function(){toast("认领失败")});
    });
    bts.appendChild(bb);g.appendChild(bts);
  }else{
    g.appendChild(el("div","sub",
      "本机：槽位 "+own.slot+"（"+(own.ip||"")+"）"));
    var f1=el("div","fld");f1.appendChild(el("label",null,"名字"));
    var inp=el("input");inp.type="text";inp.value=own.name;inp.id="d-name";
    f1.appendChild(inp);g.appendChild(f1);
    var f2=el("div","fld");f2.appendChild(el("label",null,"启用"));
    var ck=el("input");ck.type="checkbox";ck.id="d-en";
    ck.checked=own.enabled!==false;
    f2.appendChild(ck);g.appendChild(f2);
    var bts=el("div","btns");
    var b1=el("button","btn","保存修改");
    b1.addEventListener("click",function(){
      post("/device/update",{name:$("d-name").value.trim(),
        enabled:$("d-en").checked})
        .then(function(r){toast(r.j&&r.j.ok?"已保存":
          ((r.j&&r.j.error)||"保存失败"));if(r.j&&r.j.ok)openDev();},
          function(){toast("保存失败")});
    });
    var b0=el("button","btn","取消登记");
    b0.addEventListener("click",function(){
      post("/device/unregister",{}).then(function(r){
        toast(r.j&&r.j.ok?"已取消登记":((r.j&&r.j.error)||"操作失败"));
        if(r.j&&r.j.ok)openDev();
      },function(){toast("操作失败")});
    });
    var b2=el("button","btn","测试上一页");
    b2.addEventListener("click",function(){devTest("prev")});
    var b3=el("button","btn","测试下一页");
    b3.addEventListener("click",function(){devTest("next")});
    bts.appendChild(b1);bts.appendChild(b0);
    bts.appendChild(b2);bts.appendChild(b3);
    g.appendChild(bts);
  }
  var box=$("dev-own");box.textContent="";box.appendChild(g);
  var allbox=$("dev-all");allbox.textContent="";
  if(!all.length)allbox.appendChild(el("div","sub","还没有任何设备登记"));
  for(var j=0;j<all.length;j++){
    var dd=all[j],row=el("div","dv");
    row.appendChild(el("span","no","#"+dd.slot));
    row.appendChild(el("span",null,dd.name+(dd.ip===d.you?"（本机）":"")));
    row.appendChild(el("span","st"+(dd.enabled!==false?" on":""),
      dd.enabled!==false?"启用":"停用"));
    allbox.appendChild(row);
  }
}

setInterval(refresh,1000);
document.addEventListener("visibilitychange",function(){
  if(!document.hidden)refresh()});
refresh();
