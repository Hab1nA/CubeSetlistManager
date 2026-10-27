# -*- coding: utf-8 -*-
"""审计用临时脚本：生成「模拟播放态」副本（注入 mock state + 直接调用
页面既有 render/renderLive，不改任何样式），供截图：
  browser_play / app_play / app_confirm（确认弹窗打开态）"""
import io
import pathlib

HERE = pathlib.Path(__file__).resolve().parent
SRC = HERE / "_legacy_pages"

MOCK = """
<script>
document.addEventListener('DOMContentLoaded',function(){
  st={busy:false,ready:true,live:true,cur:1,tstate:'playing',
      pos:83,dur:214,projName:'01_夜曲_Live_v3',
      confirm:true,
      songs:[{name:'01_开场序曲_Intro',dur:'3:12'},
             {name:'02_夜曲',dur:'4:05'},
             {name:'03_光辉岁月_(升Key)',dur:'5:41'},
             {name:'04_安可-海阔天空',dur:'6:20'}]};
  setConn(true);render();renderLive();
});
</script>
"""

CONFIRM = """
<script>
document.addEventListener('DOMContentLoaded',function(){
  st={busy:false,ready:true,live:true,cur:1,tstate:'playing',
      pos:83,dur:214,projName:'01_夜曲_Live_v3',
      confirm:true,
      songs:[{name:'01_开场序曲_Intro',dur:'3:12'},
             {name:'02_夜曲',dur:'4:05'},
             {name:'03_光辉岁月_(升Key)',dur:'5:41'},
             {name:'04_安可-海阔天空',dur:'6:20'}]};
  setConn(true);render();renderLive();
  var a='01_夜曲_Live_v3',b=st.songs[2];
  document.getElementById('c-text').textContent=
    '确定从《'+a+'》切换到《'+b.name+'》？当前工程将被关闭（自动保存）。';
  document.getElementById('m-confirm').classList.add('show');
});
</script>
"""


def make(src_name, inj, out_name):
    html = io.open(str(SRC / src_name), encoding="utf-8").read()
    assert "</body>" in html
    html = html.replace("</body>", inj + "\n</body>", 1)
    dst = SRC / out_name
    io.open(str(dst), "w", encoding="utf-8").write(html)
    print("written:", dst)


make("browser.html", MOCK, "browser_play.html")
make("app.html", MOCK, "app_play.html")
make("app.html", CONFIRM, "app_confirm.html")
