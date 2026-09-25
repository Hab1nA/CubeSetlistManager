package com.cubemanager.turnpage

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
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

    inner class Http : NanoHTTPD("0.0.0.0", 8766) {
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

    private fun handle(j: JSONObject) {
        val mode = j.optString("mode", "tap")
        val x = j.optInt("x")
        val y = j.optInt("y")
        val count = j.optInt("count", 1)
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

    override fun onCreate() {
        super.onCreate()
        val ch = NotificationChannel(
            CHANNEL_ID, "翻谱接收", NotificationManager.IMPORTANCE_LOW)
        (getSystemService(NOTIFICATION_SERVICE) as NotificationManager)
            .createNotificationChannel(ch)
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        startForeground(NOTIFY_ID, buildNotification())
        if (server == null) {
            server = Http().also { it.start(NanoHTTPD.SOCKET_READ_TIMEOUT, false) }
        }
        return START_STICKY
    }

    override fun onDestroy() {
        server?.stop()
        server = null
        super.onDestroy()
    }

    private fun buildNotification(): Notification {
        val svcOn = TurnAccessibilityService.instance != null
        return Notification.Builder(this, CHANNEL_ID)
            .setSmallIcon(android.R.drawable.ic_media_play)
            .setContentTitle("翻谱接收中")
            .setContentText(if (svcOn) "端口 8766 · 手势就绪"
                            else "端口 8766 · 仅媒体键（无障碍未开启）")
            .setOngoing(true)
            .build()
    }

    override fun onBind(intent: Intent?): IBinder? = null

    companion object {
        private const val CHANNEL_ID = "turn"
        private const val NOTIFY_ID = 1
        const val TURN_PATH = "/turn"
    }
}
