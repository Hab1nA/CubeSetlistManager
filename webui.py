# -*- coding: utf-8 -*-
"""pywebview 桌面入口（新前端）。

线程模型：pywebview 强制主线程（6.x 显式检查），因此 Tk 业务栈整体迁到
专属子线程——Tk 线程亲和意味着所有 Tk 调用都在创建线程内即可，App 现有
跨线程约定（calls/q 队列投递 Tk 线程执行）语义不变，业务层零改动。
桌面壳 js_api 仅窗口控制（演出模式置顶走 Win32，见 _Shell）；业务动作
全走既有 HTTP 端点（与平板同一条命令面）。
web 服务：WebRemote 主服务仍按用户配置绑定（热点/LAN）；桌面壳另起一个
127.0.0.1 随机口的服务实例承载 /app（UI 专用，遥控语义零改动）。
用法：python webui.py [主窗宽] [主窗高]（正式入口=setlist_gui.py，含
单实例守卫）"""
import os
import sys
import threading
import time

import webview

import setlist_gui as sg
import web_remote


class _Shell:
    """桌面壳专属 js_api 桥：仅窗口控制（演出模式置顶），业务动作仍全走
    既有 HTTP 命令面不动摇。方法在 pywebview 的 js 线程执行。
    置顶走 Win32 SetWindowPos：pywebview 的 Window.on_top 在 winforms 上
    经 Form.TopMost 会 RecreateHandle，js 线程跨线程调用实测挂起。"""

    def set_topmost(self, on):
        import ctypes
        u32 = ctypes.windll.user32
        target = ctypes.c_void_p(-1 if on else -2)   # TOPMOST/NOTOPMOST
        found = []

        @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        def cb(h, _):
            pid = ctypes.c_ulong()
            u32.GetWindowThreadProcessId(h, ctypes.byref(pid))
            if pid.value == os.getpid() and u32.IsWindowVisible(h):
                found.append(h)
            return True
        u32.EnumWindows(cb, 0)
        if not found:
            return False
        ok = True
        for h in found:   # 只此一个可见顶层窗；保险遍历
            ok &= bool(u32.SetWindowPos(
                h, target, 0, 0, 0, 0, 0x1 | 0x2 | 0x10))  # NOSIZE|NOMOVE|NOACTIVATE
        return ok


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

    biz = threading.Thread(target=tk_main, daemon=True, name="tk-biz")
    biz.start()
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

    webview.create_window("Cube Setlist Manager", url, width=w, height=h,
                           js_api=_Shell())
    # 侧栏「退出」只经 /exit 销毁 Tk 根——pywebview 窗口不会跟着关（用户
    # 实证「无法退出」）。看门狗：Tk mainloop 结束即主动关 Web 窗
    def _web_watchdog():
        while True:
            time.sleep(0.4)
            if not biz.is_alive():
                try:
                    webview.windows[0].destroy()
                except Exception:
                    pass
                return
    threading.Thread(target=_web_watchdog, daemon=True,
                     name="web-watchdog").start()
    try:
        webview.start(gui="edgechromium")   # 主线程阻塞至窗口关闭
    except Exception as e:
        # WebView2 Runtime 缺失/损坏的可读出口（pywebview 起窗失败在此抛）
        import ctypes
        ctypes.windll.user32.MessageBoxW(
            0, "新前端需要 WebView2 Runtime，但启动失败：\n%s\n\n"
               "安装指引：https://developer.microsoft.com/microsoft-edge/"
               "webview2/（Evergreen Bootstrapper）" % e,
            "Cube Setlist Manager", 0x30)
        app.calls.put(app._on_exit)
        return
    ui_srv.shutdown()
    # 先入队退出再 join：_on_exit（热点/OBS/连带关闭）跑完才 root.destroy，
    # biz 线程随之结束——join 在 join 之后入队则 Tk 线程无死亡路径，恒
    # 超时、清理与解释器退出赛跑（/exit 路径 Tk 已自毁，重复入队幂等）
    app.calls.put(app._on_exit)
    biz.join(timeout=5)                 # 等 Tk 收尾（热点关停等秒级作业）


if __name__ == "__main__":
    main()
