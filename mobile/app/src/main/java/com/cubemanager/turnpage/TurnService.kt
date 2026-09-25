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

/** 前台服务：内嵌 HTTP 服务器收电脑的 /turn 推送（协议见 web_remote.push_turn），
 *  按 mode 分发：tap→无障碍点按、swipeL/R→无障碍滑动、media→媒体键
 *  （dispatchMediaKeyEvent 不走无障碍，兼容支持蓝牙踏板翻页的谱面 App）。 */
class TurnService : Service() {

    private var server: Http? = null

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
        val mode = j.optString("mode", "tap")
        val x = j.optInt("x")
        val y = j.optInt("y")
        val count = j.optInt("count", 1)
        reportDiag("收到推送 mode=$mode x=$x y=$y count=$count test=${j.optInt("test")}")
        val fire = {
            when (mode) {
                "media" -> {
                    val next = x >= resources.displayMetrics.widthPixels / 2
                    val code = if (next) KeyEvent.KEYCODE_MEDIA_NEXT
                    else KeyEvent.KEYCODE_MEDIA_PREVIOUS
                    val audio = getSystemService(AUDIO_SERVICE) as AudioManager
                    audio.dispatchMediaKeyEvent(KeyEvent(KeyEvent.ACTION_DOWN, code))
                    audio.dispatchMediaKeyEvent(KeyEvent(KeyEvent.ACTION_UP, code))
                }
                "swipeL", "swipeR" -> {
                    val x2 = j.optInt("x2", x)
                    TurnAccessibilityService.instance?.swipe(x, y, x2, y)
                }
                else -> TurnAccessibilityService.instance?.tap(x, y, count)
            }
        }
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
        if (server == null) startReceiverOn(desiredPort(this))
        return START_STICKY
    }

    override fun onDestroy() {
        server?.stop()
        server = null
        instance = null
        super.onDestroy()
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
