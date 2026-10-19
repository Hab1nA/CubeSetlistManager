# -*- coding: utf-8 -*-
"""pywebview 桌面入口（新前端）。

线程模型：pywebview 强制主线程（6.x 显式检查），因此 Tk 业务栈整体迁到
专属子线程——Tk 线程亲和意味着所有 Tk 调用都在创建线程内即可，App 现有
跨线程约定（calls/q 队列投递 Tk 线程执行）语义不变，业务层零改动。
桌面壳不引入 js_api：动作全走既有 HTTP 端点（与平板同一条命令面）。
web 服务：WebRemote 主服务仍按用户配置绑定（热点/LAN）；桌面壳另起一个
127.0.0.1 随机口的服务实例承载 /app（UI 专用，遥控语义零改动）。
用法：python webui.py [主窗宽] [主窗高]"""
import sys
import threading
import time

import webview

import setlist_gui as sg
import web_remote


def main():
    w = int(sys.argv[1]) if len(sys.argv) > 1 else 1280
    h = int(sys.argv[2]) if len(sys.argv) > 2 else 800
    sg.dpi.enable()

    # 桌面壳需要 WebRemote 栈在位（/state、/cmd、设备注册表都在其上）；
    # 总开关强制开——用户选 webui 入口即"要用 Web 界面"。对外绑定行为
    # 仍按 config（loopback 实例另起，不扩大暴露面）
    orig_cfg = sg._load_config

    def _cfg():
        cfg = orig_cfg()
        wr = dict(cfg.get("webRemote") or {})
        wr["enabled"] = True
        cfg["webRemote"] = wr
        return cfg

    sg._load_config = _cfg

    holder = {}

    def tk_main():
        try:
            root = sg.tk.Tk()
            root.withdraw()             # Tk 主窗全程隐藏：业务面在 Web
            sg.dpi.apply_theme(root)
            app = sg.App(root)
            holder["app"] = app
            root.mainloop()
        except Exception as e:          # 启动失败要能传给主线程报告
            holder["err"] = e

    threading.Thread(target=tk_main, daemon=True, name="tk-biz").start()
    # 等 App 建好 + WebRemote 主服务就绪（注册表等共享件在其上）
    app = None
    for _ in range(150):
        app = holder.get("app")
        if app is not None:
            wr = getattr(app, "web", None)
            if wr is not None and getattr(wr, "server", None) is not None:
                break
        if "err" in holder:
            sys.exit("业务栈启动失败：%s" % holder["err"])
        time.sleep(0.2)
    else:
        sys.exit("等待业务栈超时（App 未就绪）")

    # UI 专用 loopback 实例：127.0.0.1 随机口，/app /state /cmd 同一套面
    ui_srv = web_remote._bind_web(app, app.web.registry,
                                  lambda: app.web.tasker_port,
                                  "127.0.0.1", 0, web_remote.PAGE_APP,
                                  app.q.put)
    threading.Thread(target=ui_srv.serve_forever,
                     kwargs={"poll_interval": 0.5}, daemon=True).start()
    port = ui_srv.server_address[1]
    url = "http://127.0.0.1:%d/app" % port
    print("ui:", url, flush=True)

    webview.create_window("Cube Setlist Manager", url, width=w, height=h)
    webview.start(gui="edgechromium")   # 主线程阻塞至窗口关闭
    ui_srv.shutdown()
    app.calls.put(app._on_exit)         # 窗口关=通知 Tk 线程退出全程序


if __name__ == "__main__":
    main()
