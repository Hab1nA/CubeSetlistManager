package com.cubemanager.turnpage

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Context
import android.content.Intent
import android.media.AudioManager
import android.os.IBinder
import android.view.KeyEvent
import fi.iki.elonen.NanoHTTPD
import org.json.JSONObject

/** 前台服务：内嵌 HTTP 服务器收电脑的 /turn 语义推送（dir=next/prev，
 *  协议见 web_remote.push_turn），按本机设定的翻页方法分发：点按/双击/滑动
 *  →无障碍手势、媒体键→dispatchMediaKeyEvent（不走无障碍，兼容支持蓝牙
 *  踏板翻页的谱面 App）。Wi-Fi 高性能锁保推送低时延。 */
class TurnService : Service() {

    private var server: Http? = null
    private var wifiLock: android.net.wifi.WifiManager.WifiLock? = null

    inner class Http(port: Int) : NanoHTTPD("0.0.0.0", port) {
        override fun serve(session: IHTTPSession): Response {
            if (session.method != NanoHTTPD.Method.POST || session.uri != TURN_PATH)
                return newFixedLengthResponse(
                    NanoHTTPD.Response.Status.NOT_FOUND, "text/plain", "not found")
            val n = session.headers["content-length"]?.toIntOrNull()?.coerceAtMost(1 shl 20) ?: 0
            val buf = ByteArray(n)
            var off = 0
            while (off < n) {
                val r = session.inputStream.read(buf, off, n - off)
                if (r < 0) break
                off += r
            }
            try {
                handle(JSONObject(String(buf, 0, off, Charsets.UTF_8)))
            } catch (e: Exception) {
                return newFixedLengthResponse(
                    NanoHTTPD.Response.Status.BAD_REQUEST, "text/plain", "bad body")
            }
            return newFixedLengthResponse(
                NanoHTTPD.Response.Status.OK, "application/json", """{"ok":true}""")
        }
    }

    private val mainHandler = android.os.Handler(android.os.Looper.getMainLooper())

    private fun handle(j: JSONObject) {
        val tRecv = android.os.SystemClock.elapsedRealtime()
        // 语义协议：dir=next/prev，翻页方法与坐标按本机设置在 APP 端组装。
        // 旧版 PC 推 mode+x：按旧字段派生 dir，防两端版本错配把翻页弄死。
        val dir = j.optString("dir").ifEmpty {
            if (j.optString("mode") == "swipeR" ||
                j.optInt("x") in 1 until resources.displayMetrics.widthPixels / 2
            ) "prev" else "next"
        }
        val next = dir == "next"
        val method = getSharedPreferences("cube", MODE_PRIVATE)
            .getString("turnMethod", "tap") ?: "tap"
        reportDiag("收到推送 dir=$dir method=$method test=${j.optInt("test")}")
        val fire = { dispatchTurn(method, next, tRecv) }
        if (j.optInt("test") == 1) {
            // 测试按钮在前台是本 APP：自动切回谱面 App（手动指定优先，
            // 其次按使用记录自动检测最近使用的第三方应用）再执行手势。
            // 决策链全文回传电脑端日志（/diag），跳转失败可远程定位。
            val prefs = getSharedPreferences("cube", MODE_PRIVATE)
            val usage = usageGranted()
            val manual = prefs.getString("turnTargetPkg", null)
            val auto = if (manual == null) lastUsedNonSelfPackage() else null
            val target = manual ?: auto
            val intent = target?.let { packageManager.getLaunchIntentForPackage(it) }
            reportDiag("usage=$usage manual=${manual ?: "null"} " +
                "auto=${auto ?: "null"} intent=${intent != null} " +
                "acc=${TurnAccessibilityService.instance != null}")
            if (intent != null) {
                // NEW_TASK+NEW_TASK 缺 SINGLE_TOP 时 standard 模式会新建实例
                // （表现为「重新打开」丢阅读状态）；SINGLE_TOP 复用已有实例
                intent.addFlags(android.content.Intent.FLAG_ACTIVITY_NEW_TASK or
                    android.content.Intent.FLAG_ACTIVITY_SINGLE_TOP)
                startActivity(intent)
                reportDiag("startActivity 已发起 → $target")
                mainHandler.postDelayed({ fire() }, 1200)
            } else {
                reportDiag("无可用启动意图：回退切桌面")
                mainHandler.post {
                    MainActivity.instance?.moveTaskToBack(true)
                }
                mainHandler.postDelayed({ fire() }, 800)
            }
        } else fire()
    }

    /** 按本机翻页方法组装并执行（坐标按屏幕尺寸换算成像素）。
     *  完成即回传耗时行：recv→disp=收包到派发，disp→done=手势执行耗时。 */
    private fun dispatchTurn(method: String, next: Boolean, tRecv: Long) {
        val tDisp = android.os.SystemClock.elapsedRealtime()
        val tag = "turn dir=${if (next) "next" else "prev"} method=$method"
        val acc = TurnAccessibilityService.instance
        if (method != "media" && acc == null) {
            reportDiag("$tag 无障碍未开启，点按/滑动不可用")
            return
        }
        val done = { ok: Boolean ->
            val tDone = android.os.SystemClock.elapsedRealtime()
            reportDiag("$tag recv→disp=${tDisp - tRecv}ms " +
                "disp→done=${tDone - tDisp}ms" + if (ok) "" else "（手势被取消）")
        }
        val dm = resources.displayMetrics
        val x = (dm.widthPixels * (if (next) 0.75 else 0.25)).toInt()
        val y = dm.heightPixels / 2
        when (method) {
            "media" -> {
                val code = if (next) KeyEvent.KEYCODE_MEDIA_NEXT
                    else KeyEvent.KEYCODE_MEDIA_PREVIOUS
                val audio = getSystemService(AUDIO_SERVICE) as AudioManager
                audio.dispatchMediaKeyEvent(KeyEvent(KeyEvent.ACTION_DOWN, code))
                audio.dispatchMediaKeyEvent(KeyEvent(KeyEvent.ACTION_UP, code))
                done(true)
            }
            "swipe" -> acc?.swipe(x, y, dm.widthPixels - x, y, done)
            "double" -> acc?.tap(x, y, 2, done)
            else -> acc?.tap(x, y, 1, done)
        }
    }

    private fun usageGranted(): Boolean {
        val appOps = getSystemService(Context.APP_OPS_SERVICE) as android.app.AppOpsManager
        return appOps.checkOpNoThrow(
            android.app.AppOpsManager.OPSTR_GET_USAGE_STATS,
            android.os.Process.myUid(), packageName) ==
            android.app.AppOpsManager.MODE_ALLOWED
    }

    /** 诊断回传：POST 到电脑端 /diag（复用 APP 已知地址）。 */
    private fun reportDiag(msg: String) {
        Thread {
            try {
                val addr = getSharedPreferences("cube", MODE_PRIVATE)
                    .getString("addr", "") ?: return@Thread
                val uri = android.net.Uri.parse(addr)
                val host = uri.host ?: return@Thread
                val port = if (uri.port > 0) uri.port else 80
                val conn = java.net.URL("http://$host:$port/diag").openConnection()
                    as java.net.HttpURLConnection
                conn.requestMethod = "POST"
                conn.connectTimeout = 2000
                conn.readTimeout = 2000
                conn.doOutput = true
                conn.setRequestProperty("Content-Type", "application/json")
                conn.outputStream.write(
                    """{"msg":${org.json.JSONObject.quote(msg)}}""".toByteArray())
                conn.outputStream.close()
                conn.responseCode
            } catch (e: Exception) {
            }
        }.start()
    }

    /** 最近 7 天实际使用时间最新的非本 APP 应用（需「使用情况访问」授权；
     *  排除桌面，避免切换路径经过桌面时抓错目标）。 */
    private fun lastUsedNonSelfPackage(): String? {
        val appOps = getSystemService(Context.APP_OPS_SERVICE) as android.app.AppOpsManager
        val mode = appOps.checkOpNoThrow(
            android.app.AppOpsManager.OPSTR_GET_USAGE_STATS,
            android.os.Process.myUid(), packageName)
        if (mode != android.app.AppOpsManager.MODE_ALLOWED) return null
        val usm = getSystemService(Context.USAGE_STATS_SERVICE)
            as android.app.usage.UsageStatsManager
        val now = System.currentTimeMillis()
        val stats = usm.queryUsageStats(
            android.app.usage.UsageStatsManager.INTERVAL_BEST,
            now - 7L * 24 * 3600 * 1000, now)
        var best: String? = null
        var bestT = 0L
        for (s in stats) {
            val p = s.packageName ?: continue
            if (p == packageName || p.contains("launcher")) continue
            if (s.lastTimeUsed > bestT) {
                bestT = s.lastTimeUsed
                best = p
            }
        }
        return best
    }

    override fun onCreate() {
        super.onCreate()
        instance = this
        val ch = NotificationChannel(
            CHANNEL_ID, "翻谱接收", NotificationManager.IMPORTANCE_LOW)
        (getSystemService(NOTIFICATION_SERVICE) as NotificationManager)
            .createNotificationChannel(ch)
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        startForeground(NOTIFY_ID, buildNotification())
        acquireWifiLock()
        if (server == null) startReceiverOn(desiredPort(this))
        return START_STICKY
    }

    override fun onDestroy() {
        server?.stop()
        server = null
        try {
            wifiLock?.release()
        } catch (_: Exception) {
        }
        wifiLock = null
        instance = null
        super.onDestroy()
    }

    /** Wi-Fi 高性能锁：阻止平板 Wi-Fi 进入省电轮询——两首歌之间连接闲置，
     *  第一页推送会被唤醒延迟拖到秒级；持有期间耗电略增（前台服务可接受）。 */
    private fun acquireWifiLock() {
        if (wifiLock?.isHeld == true) return
        val wm = applicationContext.getSystemService(WIFI_SERVICE)
            as android.net.wifi.WifiManager
        val mode = if (android.os.Build.VERSION.SDK_INT >= 29)
            android.net.wifi.WifiManager.WIFI_MODE_FULL_LOW_LATENCY
        else android.net.wifi.WifiManager.WIFI_MODE_FULL_HIGH_PERF
        wifiLock = wm.createWifiLock(mode, "cube:turn").apply {
            setReferenceCounted(false)
            acquire()
        }
    }

    /** 在指定端口（重）启接收器；端口被占等失败返回 false。 */
    fun startReceiverOn(port: Int): Boolean {
        server?.stop()
        server = null
        listening = false
        return try {
            Http(port).also {
            it.start(NanoHTTPD.SOCKET_READ_TIMEOUT, false)
            server = it
        }
        Companion.port = port
        listening = true
            (getSystemService(NOTIFICATION_SERVICE) as NotificationManager)
                .notify(NOTIFY_ID, buildNotification())
            true
        } catch (e: Exception) {
            false
        }
    }

    /** 通知随无障碍开关即时刷新（id 相同覆盖更新）。 */
    fun refreshNotification() {
        (getSystemService(NOTIFICATION_SERVICE) as NotificationManager)
            .notify(NOTIFY_ID, buildNotification())
    }

    private fun buildNotification(): Notification {
        val svcOn = TurnAccessibilityService.instance != null
        return Notification.Builder(this, CHANNEL_ID)
            .setSmallIcon(android.R.drawable.ic_media_play)
            .setContentTitle("翻谱接收中")
            .setContentText(if (svcOn) "端口 $port · 手势就绪"
                            else "端口 $port · 仅媒体键（无障碍未开启）")
            .setOngoing(true)
            .build()
    }

    override fun onBind(intent: Intent?): IBinder? = null

    companion object {
        private const val CHANNEL_ID = "turn"
        private const val NOTIFY_ID = 1
        const val TURN_PATH = "/turn"

        @Volatile var listening = false
            private set
        @Volatile var port = 8766
            private set
        @Volatile var instance: TurnService? = null
            private set

        fun desiredPort(ctx: android.content.Context): Int =
            ctx.getSharedPreferences("cube", android.content.Context.MODE_PRIVATE)
                .getInt("turnPort", 8766)

        /** 供 JS 桥调用：切换接收端口并持久化；成功=true（监听已就绪）。 */
        fun restartOn(ctx: android.content.Context, port: Int): Boolean {
            val ok = instance?.startReceiverOn(port) ?: false
            if (ok) ctx.getSharedPreferences("cube",
                    android.content.Context.MODE_PRIVATE)
                .edit().putInt("turnPort", port).apply()
            return ok
        }
    }
}
