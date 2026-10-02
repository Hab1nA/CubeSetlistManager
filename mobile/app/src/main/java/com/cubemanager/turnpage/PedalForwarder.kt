package com.cubemanager.turnpage

import android.content.Context
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import org.json.JSONArray
import org.json.JSONObject
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicLong

/** 有线踏板（USB-C HID）按键转发：无障碍 onKeyEvent 捕获的原始边沿经
 *  PedalBatcher 打包，POST 给电脑端 /pedal/event。
 *  协议 {device, seq, et, events:[{vk,kc,down,dt}]}：
 *  - vk = KeyEvent.scanCode（Linux 键码——内核 hid-input 把 HID usage 折算
 *    成 LKC 上报，usage 不传给应用；电脑端按 LKC 表映射 Windows VK），
 *    kc = keyCode（Android 键码，电脑端兜底表：个别栈 scanCode 报 0）
 *  - et = 块内最大事件时戳（uptimeMillis，APP 单调）：电脑端做跨包时间
 *    重定基准——网络抖动/重试延迟进不了跨包的双踩窗/去抖窗
 *  - dt = 距块内最大时戳的毫秒偏移（保包内边沿间距）
 *  生命周期：失败每 200ms 重试，包年龄超 2s 丢弃（陈旧走带动作重放比
 *  丢弃危险——现场已经往下走了）+ 本地 Toast；403=电脑端开关未开，按键
 *  透传给前台谱面 App 并提示，心跳继续探（电脑端开回即自动恢复）；
 *  心跳 5s 一发（电脑端健康显示数据源，link 判据 15s）。 */
object PedalForwarder {

    private const val DEVICE = "pedal-usb"
    private const val FLUSH_MS = 40L        // UP 后静默窗：并走突发为一包
    private const val GUARD_MS = 1000L      // DOWN 后未见 UP：补合成 up
    private const val MAX_AGE_MS = 2000L    // 包年龄上限，超龄丢弃
    private const val HB_MS = 5000L
    private const val RETRY_MS = 200L

    private class Pkt(val t: Long, val body: String)

    @Volatile var enabled = false           // APP 端开关（prefs "pedalForward"）
        private set
    @Volatile private var disabledByPc = false
    @Volatile private var ready = false
    @Volatile private var running = false
    private lateinit var appContext: Context
    private val mainHandler = Handler(Looper.getMainLooper())
    private val batcher = PedalBatcher()
    private val guardTokens = HashMap<Int, Runnable>()   // 主线程独占
    private var flushPending = false                     // 主线程独占
    private val q = LinkedBlockingQueue<Pkt>()
    private val seq = AtomicLong()
    // 会话标识：每进程随机。电脑端按 (device, sid) 去重——进程重启（seq
    // 归 1）后不被旧基准误判 dup；同进程内同 seq 重发仍幂等确认
    private val sid = java.util.UUID.randomUUID().toString().take(8)
    @Volatile private var worker: Thread? = null

    /** 键位白名单（Android keycode）：媒体键/翻页键/方向键/编辑键/F1-12。
     *  不含音量键（24/25/164）——转发开启时平板自身硬件音量必须保留；
     *  不含字母数字/小键盘（131-142 只到 F12；143-154 是 NumLock/小键盘，
     *  真正的 F13-24 键码 326-337 自 API 30/Android 11 起存在）——防真实键盘
     *  在场时打字被吞；不含 89/90/23（REWIND/FF/DPAD_CENTER——PC 表无
     *  映射，吞了=两端全灭，透传无害）；LKC 183-194（F13-F24）无默认
     *  Android 键码映射（keyCode=0），单独按 scanCode 放行。 */
    private val PEDAL_KEYS: Set<Int> = buildSet {
        addAll(intArrayOf(85, 86, 87, 88, 126, 127).asIterable())
        addAll(intArrayOf(92, 93, 122, 123, 66, 111, 62, 61, 121).asIterable())
        addAll(intArrayOf(19, 20, 21, 22).asIterable())
        addAll(131..142)
    }
    private val PEDAL_SCANS = 183..194      // LKC F13-F24（keyCode=0 的自由键）

    /** TurnService.onCreate 调：读开关、按需启动。 */
    fun init(ctx: Context) {
        appContext = ctx.applicationContext
        ready = true
        setEnabled(ctx, prefs(ctx).getBoolean("pedalForward", false))
    }

    /** 与 ensureWorker 同锁：消除「shutdown 与 enable 并发产生摸不到的
     *  替身 worker」的窗口。 */
    @Synchronized
    fun shutdown() {
        running = false
        worker?.interrupt()
        worker = null
        mainHandler.removeCallbacks(hbTask)
        mainHandler.removeCallbacks(flushTask)
        batcher.clear()
    }

    /** JS 桥 setPedalForward 落地：持久化 + 启停。在途状态清理 post 到主
     *  线程执行（与 onKey/flush 同线程——batcher 本身 @Synchronized 双保险，
     *  guard/flush 定时器句柄只有主线程能安全 remove）。重新开启即清
     *  403 停发标志（下个心跳若电脑端仍关会立刻再次置位）。 */
    fun setEnabled(ctx: Context, v: Boolean) {
        prefs(ctx).edit().putBoolean("pedalForward", v).apply()
        enabled = v
        if (!ready) return
        if (v) {
            mainHandler.post { resetInFlight() }
            disabledByPc = false
            startHb()
            ensureWorker()
            report("踩钉转发开启")
        } else {
            mainHandler.post { resetInFlight() }
            mainHandler.removeCallbacks(hbTask)
            q.clear()
            report("踩钉转发关闭")
        }
    }

    private fun resetInFlight() {
        guardTokens.values.forEach { mainHandler.removeCallbacks(it) }
        guardTokens.clear()
        flushPending = false
        batcher.clear()
    }

    private fun prefs(ctx: Context) =
        ctx.getSharedPreferences("cube", Context.MODE_PRIVATE)

    // ---- 捕获侧（无障碍主线程调用） ----

    /** 返回 true=本事件被捕获转发（拦截，不下发给前台 App）。
     *  电脑端关闭（403）时返回 false 透传——按键仍有去处（谱面 App），
     *  用户可见「转发没在工作」而不是踩了没反应。 */
    fun onKey(keyCode: Int, scanCode: Int, down: Boolean, eventTime: Long,
              virtualDevice: Boolean): Boolean {
        if (!enabled || disabledByPc || !ready) return false
        if (virtualDevice) return false            // 输入法/屏幕按键
        if (keyCode !in PEDAL_KEYS && scanCode !in PEDAL_SCANS) return false
        if (down) {
            batcher.onKey(scanCode, keyCode, true, eventTime)
            armGuard(scanCode)
        } else {
            batcher.onKey(scanCode, keyCode, false, eventTime)
            cancelGuard(scanCode)
        }
        scheduleFlush()
        return true
    }

    private fun armGuard(vk: Int) {
        cancelGuard(vk)
        val r = Runnable {
            guardTokens.remove(vk)
            if (batcher.guardFire(vk, SystemClock.uptimeMillis())) scheduleFlush()
        }
        guardTokens[vk] = r
        mainHandler.postDelayed(r, GUARD_MS)
    }

    private fun cancelGuard(vk: Int) {
        guardTokens.remove(vk)?.let { mainHandler.removeCallbacks(it) }
    }

    private val flushTask: Runnable = Runnable { flush() }

    private fun scheduleFlush() {
        if (flushPending) return
        flushPending = true
        mainHandler.postDelayed(flushTask, FLUSH_MS)
    }

    private fun flush() {
        flushPending = false
        val blocks = batcher.flush()
        for (b in blocks) {
            val events = JSONArray()
            for (e in b.events) {
                events.put(JSONObject().put("vk", e.vk).put("kc", e.kc)
                    .put("down", e.down).put("dt", (b.et - e.t).toInt()))
            }
            q.offer(Pkt(SystemClock.elapsedRealtime(),
                JSONObject().put("device", DEVICE)
                    .put("seq", seq.incrementAndGet())
                    .put("sid", sid)
                    .put("et", b.et)
                    .put("events", events).toString()))
        }
        if (blocks.isNotEmpty()) ensureWorker()
    }

    // ---- 心跳（健康显示数据源；403 期间照发——电脑端开回即自愈） ----

    private val hbTask: Runnable = Runnable {
        if (!enabled || !ready) return@Runnable
        q.offer(Pkt(SystemClock.elapsedRealtime(),
            JSONObject().put("device", DEVICE)
                .put("seq", seq.incrementAndGet()).put("sid", sid)
                .put("hb", true).toString()))
        ensureWorker()
        mainHandler.postDelayed(hbTask, HB_MS)
    }

    private fun startHb() {
        mainHandler.removeCallbacks(hbTask)
        mainHandler.postDelayed(hbTask, HB_MS)
    }

    // ---- 发送侧（单 worker 串行；异常死亡由 finally 自清，可重建） ----

    /** @Synchronized：setEnabled（JavaBridge 线程）与 flush/hbTask（主线程）
     *  并发可达——「isAlive 检查+启动」必须原子，否则双 worker 并发发送
     *  会造成到达乱序、破坏 PC 端 seq 等值判重的单飞前提。 */
    @Synchronized
    private fun ensureWorker() {
        if (worker?.isAlive == true) return
        running = true
        worker = Thread {
            try {
                while (running) {
                    val p = try {
                        q.poll(1, TimeUnit.SECONDS) ?: continue
                    } catch (_: InterruptedException) {
                        break
                    }
                    send(p)
                }
            } finally {
                running = false
            }
        }.apply { name = "pedal-fwd"; isDaemon = true; start() }
    }

    private fun send(p: Pkt) {
        while (true) {
            val code = postOnce(p.body)
            when {
                code == 200 -> {
                    if (disabledByPc) {
                        disabledByPc = false
                        report("电脑端已开启，转发恢复")
                        toast("踩钉转发：已连接电脑")
                    }
                    return
                }
                code == 403 -> {
                    if (!disabledByPc) {
                        disabledByPc = true
                        toast("踩钉转发：电脑端未开启（按键暂透传给谱面 App）")
                    }
                    report("电脑端未勾选「允许平板转发」，停发等待其开启")
                    return
                }
                // 4xx=包被拒（结构/限流），重试无意义；其余（网络/5xx）重试
                code != null && code < 500 && code != 429 -> {
                    report("转发被拒（HTTP $code），本包丢弃")
                    return
                }
            }
            if (SystemClock.elapsedRealtime() - p.t > MAX_AGE_MS) {
                report("链路不通超 2s，本包丢弃")
                toast("踩钉转发：电脑不可达，按键未送达")
                return
            }
            try {
                Thread.sleep(RETRY_MS)
            } catch (_: InterruptedException) {
                return
            }
        }
    }

    private fun postOnce(body: String): Int? = try {
        val addr = prefs(appContext).getString("addr", "") ?: return null
        val uri = android.net.Uri.parse(addr)
        val host = uri.host ?: return null
        val port = if (uri.port > 0) uri.port else 80
        val conn = java.net.URL("http://$host:$port/pedal/event")
            .openConnection() as java.net.HttpURLConnection
        conn.requestMethod = "POST"
        conn.connectTimeout = 1500
        conn.readTimeout = 1500
        conn.doOutput = true
        conn.setRequestProperty("Content-Type", "application/json")
        conn.outputStream.write(body.toByteArray())
        conn.outputStream.close()
        conn.responseCode
    } catch (_: Exception) {
        null
    }

    private fun report(msg: String) {
        TurnService.instance?.reportDiag("踏板转发：$msg")
    }

    private fun toast(msg: String) {
        mainHandler.post {
            try {
                android.widget.Toast.makeText(appContext, msg,
                    android.widget.Toast.LENGTH_SHORT).show()
            } catch (_: Exception) {
            }
        }
    }
}
