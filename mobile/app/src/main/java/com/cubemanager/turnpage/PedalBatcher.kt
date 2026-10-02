package com.cubemanager.turnpage

/** 纯逻辑打包器：踏板边沿流 → 协议事件块。无任何 Android 依赖（JVM 可
 *  直接单测，见 PedalBatcherTest）；定时器不进来——owner（PedalForwarder）
 *  负责调度 flush 窗与 DOWN 超时守卫，本类只管状态与切块。
 *  线程模型：所有入口 @Synchronized——onKey 在无障碍主线程、guardFire 在
 *  主 handler 任务、clear 在 setEnabled（JS 桥线程任意时刻可调），互斥
 *  不依赖调用方线程约束。
 *  协议契约（Python 镜像 tools/pedal_wire_sim.py，两侧改动须同步）：
 *  - dt = 块内最大时戳 − 该事件时戳（用 max 而非末元素：主线程卡顿下
 *    guard 合成 up 先入队、真实 up 迟到时末元素时戳更早，取末元素会算出
 *    负 dt 被电脑端整包拒收）
 *  - 块按 ≤32 条、跨度 ≤5000ms 切（电脑端校验 dt≤5000；主线程长卡顿
 *    积压时分块出包，不整包报废）
 *  - et = 块内最大时戳（APP 端单调毫秒）：电脑端据此做跨包时间重定基准
 */
class PedalBatcher {

    class Ev(val vk: Int, val kc: Int, val down: Boolean, val t: Long)
    class Block(val events: List<Ev>, val et: Long)

    private val buf = ArrayList<Ev>()
    private val pendingDown = HashMap<Int, Ev>()   // vk → 在途按下沿（guard 补 up 用）

    /** 边沿进入；等 owner 调度 flush() 取走。 */
    @Synchronized
    fun onKey(vk: Int, kc: Int, down: Boolean, t: Long) {
        val ev = Ev(vk, kc, down, t)
        buf.add(ev)
        if (down) pendingDown[vk] = ev else pendingDown.remove(vk)
    }

    /** DOWN 超时无 UP：补合成 up（时戳=now）防电脑端状态悬挂。
     *  返回 true=确实补了（owner 应调度 flush）；UP 已到则无操作。 */
    @Synchronized
    fun guardFire(vk: Int, now: Long): Boolean {
        val ev = pendingDown.remove(vk) ?: return false
        buf.add(Ev(ev.vk, ev.kc, false, now))
        return true
    }

    /** 取走缓冲事件并切块；无事件返回空表。 */
    @Synchronized
    fun flush(): List<Block> {
        if (buf.isEmpty()) return emptyList()
        val events = ArrayList(buf)
        buf.clear()
        val out = ArrayList<Block>()
        var chunk = ArrayList<Ev>()
        var minT = Long.MAX_VALUE
        for (e in events) {
            if (chunk.size >= 32 || (chunk.isNotEmpty() && e.t - minT > 5000)) {
                out.add(seal(chunk))
                chunk = ArrayList()
                minT = Long.MAX_VALUE
            }
            chunk.add(e)
            if (e.t < minT) minT = e.t
        }
        if (chunk.isNotEmpty()) out.add(seal(chunk))
        return out
    }

    /** 清空全部在途状态（开关关闭/服务销毁）。 */
    @Synchronized
    fun clear() {
        buf.clear()
        pendingDown.clear()
    }

    private fun seal(chunk: List<Ev>): Block {
        var maxT = Long.MIN_VALUE
        for (e in chunk) if (e.t > maxT) maxT = e.t
        return Block(chunk.toList(), maxT)
    }
}
