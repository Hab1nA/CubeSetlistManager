"""主线程停摆复现器：隔离复刻「UI 冻结/黑面 + APP 指令积压、恢复后集中执行」。

复刻 Cube Setlist Manager 的三件套结构：
  after(50)  排空指令队列   = _drain_calls / app.calls
  after(200) 心跳刷新       = _tick（状态栏时钟/进度）
  「全停」按钮             = _panic → ctl.stop_media()（主线程网络等待）
  后台线程每 0.4s 入队一条指令 = web_remote HTTP 线程（入队即回 APP 200）

第 2 秒自动触发一次 8 秒主线程停摆（或点按钮手动触发），12 秒后自动退出，
打印时间线：心跳断流窗口、指令入队→执行的最大延迟、恢复后单次排水条数。
运行：python tools/repro_mainloop_stall.py
"""
import queue
import threading
import time
import tkinter as tk

CMD_SEC = 12.0     # 总时长
STALL_AT = 2.0     # 第几秒触发停摆
STALL_SEC = 8.0    # 停摆时长（≈ _panic → stop_media 2×5s 的量级）


def main():
    q = queue.Queue()
    log = []
    stats = {"queued": 0, "ran": 0, "max_latency": 0.0}
    t0 = time.monotonic()
    now = lambda: time.monotonic() - t0

    root = tk.Tk()
    root.title("主线程停摆复现器（12 秒自动退出）")
    root.geometry("420x150")
    hb = tk.Label(root, font=("Consolas", 11), justify="left", anchor="w")
    hb.pack(fill="both", expand=True)
    tk.Button(root, text="全停（主线程阻塞 8 秒）",
              command=lambda: time.sleep(STALL_SEC)).pack(pady=4)

    def drain():                      # = _drain_calls：q 里存入队时刻，执行时算延迟
        n = 0
        while True:
            try:
                enq_at = q.get_nowait()
            except queue.Empty:
                break
            latency = now() - enq_at
            stats["max_latency"] = max(stats["max_latency"], latency)
            n += 1
        stats["ran"] += n
        if n > 1:
            log.append("%6.2fs  恢复：单次排水 %d 条积压指令" % (now(), n))
        root.after(50, drain)

    def tick():                       # = _tick 心跳
        if tick.last is not None:
            gap = now() - tick.last
            if gap > 1.0:
                log.append("%6.2fs  心跳断流 %.1f 秒（主线程停摆窗口）"
                           % (now(), gap))
        tick.last = now()
        hb.config(text="心跳 t=%.1fs\n（第 2 秒自动触发：主线程睡 8 秒）" % now())
        root.after(200, tick)
    tick.last = None

    def producer():                   # = web_remote HTTP 线程
        while now() < CMD_SEC:
            q.put(now())
            stats["queued"] += 1
            time.sleep(0.4)

    root.after(50, drain)
    root.after(200, tick)
    threading.Thread(target=producer, daemon=True).start()
    root.after(int(STALL_AT * 1000), lambda: (
        log.append("%6.2fs  全停：主线程开始阻塞 %.0f 秒" % (now(), STALL_SEC)),
        time.sleep(STALL_SEC)))
    root.after(int(CMD_SEC * 1000) + 300, root.destroy)
    root.mainloop()

    log.sort()
    print("\n".join(log))
    print("---")
    print("入队 %d 条 / 执行 %d 条；指令入队→执行最大延迟 %.1f 秒。"
          % (stats["queued"], stats["ran"], stats["max_latency"]))
    print("真实软件中同构：APP 指令积压于 app.calls，主线程停摆期间 UI 不重绘，"
          "阻塞超时到期后集中执行+全窗重绘（黑面恢复）。")


if __name__ == "__main__":
    main()
