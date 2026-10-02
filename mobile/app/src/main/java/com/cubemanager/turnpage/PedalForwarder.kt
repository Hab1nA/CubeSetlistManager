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

/** 有线踏板（USB-C HID）按键转发：无障碍 onKeyEvent 捕获的原始边沿在这里
 *  打包 POST 给电脑端 /pedal/event。协议 {device, seq, events:[{vk,down,dt}]}：
 *  vk=HID usage（KeyEvent.scanCode 原样，电脑端静态表映射 VK——蓝牙路径学的
 *  绑定自动生效）；dt=距包内最后事件的毫秒偏移（本机单调钟相对值，电脑端
 *  重定基准——包内边沿间距原样保留，网络抖动进不了电脑端的弹跳闸/双踩窗）。
 *  一次按压脉冲 down/up 打包在一包（等 UP +40ms 静默窗）。
 *  重试：失败每 150ms 重试，队列年龄超 2s 丢弃（陈旧的走带动作重放比丢弃
 *  更危险——现场已经往下走了）；403=电脑端开关未开，停发到重新开启；
 *  心跳 5s 一发（电脑端健康显示数据源，link 判据 15s）。 */
object PedalForwarder {

    private const val DEVICE = "pedal-usb"
    private const val FLUSH_MS = 40L        // UP 后静默窗：并走带突发为一包
    private const val GUARD_MS = 1000L      // DOWN 后未见 UP：补合成 up（防电脑端悬挂）
    private const val MAX_AGE_MS = 2000L    // 队列年龄上限，超龄丢弃
    private const val HB_MS = 5000L
    private const val RETRY_MS = 150L

    private class Ev(val vk: Int, val down: Boolean, val t: Long)
    private class Pkt(val t: Long, val body: String)

    @Volatile var enabled = false           // APP 端开关（prefs "pedalForward"）
        private set
    @Volatile private var disabledByPc = false
    @Volatile private var ready = false
    @Volatile private var running = false
    private lateinit var appContext: Context
    private val mainHandler = Handler(Looper.getMainLooper())
    private val buf = ArrayList<Ev>()       // 主线程独占（onKeyEvent 在主线程）
    private val pendingDown = HashMap<Int, Long>()
    private val guard = HashMap<Int, Runnable>()
    private var flushPending = false
    private val q = LinkedBlockingQueue<Pkt>()
    private val seq = AtomicLong()
    @Volatile private var worker: Thread? = null

    /** 键位白名单：只认踏板会发的键（媒体/音量/翻页/方向/F1-24/编辑键）。
     *  输入法与屏幕按键本就不产生硬件事件（虚拟设备），白名单是防真实
     *  键盘在场时被整只吞掉。 */
    private val PEDAL_KEYS: Set<Int> = buildSet {
        addAll(intArrayOf(85, 86, 87, 88, 89, 90, 126, 127, 164, 24, 25).asIterable())
        addAll(intArrayOf(92, 93, 61, 66, 111, 62, 122, 123).asIterable())
        addAll(intArrayOf(19, 20, 21, 22, 23).asIterable())
        addAll(131..154)
    }

    /** TurnService.onCreate 调：读开关、按需启动。 */
    fun init(ctx: Context) {
        appContext = ctx.applicationContext
        ready = true
        setEnabled(ctx, prefs(ctx).getBoolean("pedalForward", false))
    }

    fun shutdown() {
        running = false
        worker?.interrupt()
        worker = null
        mainHandler.removeCallbacks(hbTask)
        mainHandler.removeCallbacks(flushTask)
    }

    /** JS 桥 setPedalForward 落地：持久化 + 启停（重新开启时清除电脑端
     *  403 停发标志——对方可能已把开关打开）。 */
    fun setEnabled(ctx: Context, v: Boolean) {
        prefs(ctx).edit().putBoolean("pedalForward", v).apply()
        enabled = v
        if (!ready) return
        if (v) {
            disabledByPc = false
            startHb()
            ensureWorker()
            report("踩钉转发开启")
        } else {
            mainHandler.removeCallbacks(hbTask)
            synchronized(buf) { buf.clear(); pendingDown.clear() }
            q.clear()
            report("踩钉转发关闭")
        }
    }

    private fun prefs(ctx: Context) =
        ctx.getSharedPreferences("cube", Context.MODE_PRIVATE)

    // ---- 捕获侧（无障碍服务主线程调用） ----

    fun onKey(keyCode: Int, scanCode: Int, down: Boolean, eventTime: Long,
              virtualDevice: Boolean): Boolean {
        if (!enabled || disabledByPc || !ready) return false
        if (virtualDevice) return false            // 输入法/屏幕按键
        if (keyCode !in PEDAL_KEYS) return false
        if (scanCode <= 0) return false            // 无 usage 电脑端没法映射
        if (down) {
            buf.add(Ev(scanCode, true, eventTime))
            pendingDown[scanCode] = eventTime
            val g = Runnable {                     // 卡死保险：1s 无 UP 补合成 up
                guard.remove(scanCode)
                if (pendingDown.remove(scanCode) != null) {
                    buf.add(Ev(scanCode, false, SystemClock.uptimeMillis()))
                    scheduleFlush()
                }
            }
            guard[scanCode] = g
            mainHandler.postDelayed(g, GUARD_MS)
        } else {
            pendingDown.remove(scanCode)
            buf.add(Ev(scanCode, false, eventTime))
        }
        scheduleFlush()
        return true                                // 拦截：键不再送达前台 App
    }

    private val flushTask = Runnable { flush() }

    private fun scheduleFlush() {
        if (flushPending) return
        flushPending = true
        mainHandler.postDelayed(flushTask, FLUSH_MS)
    }

    private fun flush() {
        flushPending = false
        synchronized(buf) {
            if (buf.isEmpty()) return
            val last = buf.last().t
            val events = JSONArray()
            for (e in buf) {
                events.put(JSONObject().put("vk", e.vk)
                    .put("down", e.down).put("dt", (last - e.t).toInt()))
            }
            buf.clear()
            q.offer(Pkt(SystemClock.elapsedRealtime(),
                JSONObject().put("device", DEVICE)
                    .put("seq", seq.incrementAndGet())
                    .put("events", events).toString()))
        }
        ensureWorker()
    }

    // ---- 心跳（健康显示数据源） ----

    private val hbTask: Runnable = Runnable {
        if (!enabled || disabledByPc) return@Runnable
        q.offer(Pkt(SystemClock.elapsedRealtime(),
            JSONObject().put("device", DEVICE)
                .put("seq", seq.incrementAndGet()).put("hb", true).toString()))
        ensureWorker()
        mainHandler.postDelayed(hbTask, HB_MS)
    }

    private fun startHb() {
        mainHandler.removeCallbacks(hbTask)
        mainHandler.postDelayed(hbTask, HB_MS)
    }

    // ---- 发送侧（单 worker 线程串行） ----

    private fun ensureWorker() {
        if (worker?.isAlive == true || running) return
        running = true
        worker = Thread {
            while (running) {
                val p = try {
                    q.poll(1, TimeUnit.SECONDS) ?: continue
                } catch (_: InterruptedException) {
                    break
                }
                send(p)
            }
        }.apply { name = "pedal-fwd"; isDaemon = true; start() }
    }

    private fun send(p: Pkt) {
        while (true) {
            val code = postOnce(p.body)
            when {
                code == 200 -> return
                // 电脑端开关未开：停发到 APP 端重新开启（不打无意义的包）
                code == 403 -> {
                    disabledByPc = true
                    report("电脑端未勾选「允许平板转发」，停发")
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
