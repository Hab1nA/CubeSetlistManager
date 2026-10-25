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
    # 等 App 建好 + WebRemote 栈就绪（registry 等共享件在构造期创建，
    # 不依赖遥控总开关——用户关着遥控时桌面壳照常可用且不碰热点语义）
    app = None
    for _ in range(150):
        app = holder.get("app")
        if app is not None:
            wr = getattr(app, "web", None)
            if wr is not None and wr.registry is not None:
                break
        if "err" in holder:
            sys.exit("业务栈启动失败：%s" % holder["err"])
        time.sleep(0.2)
    else:
        sys.exit("等待业务栈超时（App 未就绪）")
    # WebUI 模式旗标必须立即置位：会话内经 /exit 退出时 _on_exit 要跳过
    # Tk 确认框（root 全程 withdraw，弹了也看不见——点「确认退出」会沉底
    # 卡死）。原实现置在 webview.start() 之后=会话内退出路径失效
    app.webui_mode = True               # Tk 确认框改由前端两段式承担

    # UI 专用 loopback 实例：127.0.0.1 随机口，/app /state /cmd 同一套面；
    # 与 WebRemote 主服务（按用户配置起/不起热点与对外端口）完全独立
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
    try:
        webview.start(gui="edgechromium")   # 主线程阻塞至窗口关闭
    except Exception as e:
        # WebView2 Runtime 缺失/损坏的可读出口（pywebview 起窗失败在此抛）
        import ctypes
        ctypes.windll.user32.MessageBoxW(
            0, "新前端需要 WebView2 Runtime，但启动失败：\n%s\n\n"
               "安装指引：https://developer.microsoft.com/microsoft-edge/"
               "webview2/（Evergreen Bootstrapper）\n"
               "或设置环境变量 CUBE_TK=1 回退旧 Tk 界面。" % e,
            "Cube Setlist Manager", 0x30)
        app.calls.put(app._on_exit)
        return
    ui_srv.shutdown()
    app.calls.put(app._on_exit)         # 窗口关=通知 Tk 线程退出全程序


if __name__ == "__main__":
    main()
