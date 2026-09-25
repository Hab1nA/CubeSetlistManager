package com.cubemanager.turnpage

import android.accessibilityservice.AccessibilityService
import android.accessibilityservice.GestureDescription
import android.graphics.Path
import android.os.Handler
import android.os.Looper
import android.view.accessibility.AccessibilityEvent

/** 手势执行器：电脑推送的翻页动作最终在这里落成屏幕点按/滑动。
 *  instance 由系统绑定/解绑维护，TurnService 判空即可知道无障碍是否开启。 */
class TurnAccessibilityService : AccessibilityService() {

    companion object {
        @Volatile
        var instance: TurnAccessibilityService? = null
            private set
    }

    private val mainHandler = Handler(Looper.getMainLooper())

    override fun onServiceConnected() {
        instance = this
        TurnService.instance?.refreshNotification()
    }

    override fun onUnbind(intent: android.content.Intent?): Boolean {
        instance = null
        TurnService.instance?.refreshNotification()
        return super.onUnbind(intent)
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent?) = Unit
    override fun onInterrupt() = Unit

    /** 点按；count>=2 为双击（两次间隔约 100ms，与电脑端语义一致）。 */
    fun tap(x: Int, y: Int, count: Int, done: (Boolean) -> Unit = {}) {
        val once = { cb: (Boolean) -> Unit ->
            val path = Path().apply { moveTo(x.toFloat(), y.toFloat()) }
            val gesture = GestureDescription.Builder().addStroke(
                GestureDescription.StrokeDescription(path, 0, 60)).build()
            cb(dispatchGesture(gesture, null, null))
        }
        if (count >= 2) {
            once { ok1 ->
                mainHandler.postDelayed({
                    once { ok2 -> done(ok1 && ok2) }
                }, 100)
            }
        } else once(done)
    }

    /** 滑动：起笔(x1,y1)→收笔(x2,y2)，约 220ms（谱面 App 的翻页滑动区间内）。 */
    fun swipe(x1: Int, y1: Int, x2: Int, y2: Int, done: (Boolean) -> Unit = {}) {
        val path = Path().apply {
            moveTo(x1.toFloat(), y1.toFloat())
            lineTo(x2.toFloat(), y2.toFloat())
        }
        val gesture = GestureDescription.Builder().addStroke(
            GestureDescription.StrokeDescription(path, 0, 220)).build()
        done(dispatchGesture(gesture, null, null))
    }
}
