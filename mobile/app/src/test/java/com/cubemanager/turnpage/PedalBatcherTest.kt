package com.cubemanager.turnpage

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** PedalBatcher 纯逻辑单测（无 Android 依赖）：打包/切块/guard/dt 非负。
 *  协议契约与 tools/pedal_wire_sim.py（Python 镜像）、电脑端校验
 *  （events≤32、dt≤5000）三方对齐。 */
class PedalBatcherTest {

    @Test
    fun `flush empty returns nothing`() {
        assertTrue(PedalBatcher().flush().isEmpty())
    }

    @Test
    fun `single pulse batches down and up in one block`() {
        val b = PedalBatcher()
        b.onKey(163, 87, true, 1000)
        b.onKey(163, 87, false, 1010)
        val blocks = b.flush()
        assertEquals(1, blocks.size)
        assertEquals(1010L, blocks[0].et)
        assertEquals(2, blocks[0].events.size)
        // dt=距块内最大时戳：down 10ms、up 0ms——按压间距原样保留
        assertEquals(10L, blocks[0].et - blocks[0].events[0].t)
        assertEquals(0L, blocks[0].et - blocks[0].events[1].t)
    }

    @Test
    fun `flush chunks at 32 events`() {
        val b = PedalBatcher()
        for (i in 0 until 40) b.onKey(163, 87, i % 2 == 0, 1000L + i)
        val blocks = b.flush()
        assertEquals(2, blocks.size)
        assertEquals(32, blocks[0].events.size)
        assertEquals(8, blocks[1].events.size)
    }

    @Test
    fun `flush splits blocks over 5000ms span`() {
        val b = PedalBatcher()
        // 跨 6s 的在途事件（主线程长卡顿模拟）：切成两块，每块 dt≤5000
        b.onKey(163, 87, true, 0)
        b.onKey(163, 87, false, 100)
        b.onKey(164, 85, true, 6000)
        b.onKey(164, 85, false, 6010)
        val blocks = b.flush()
        assertEquals(2, blocks.size)
        for (blk in blocks) for (e in blk.events)
            assertTrue(blk.et - e.t in 0..5000)
        assertEquals(100L, blocks[0].et)         // 第一块含前两个事件
        assertEquals(6010L, blocks[1].et)
    }

    @Test
    fun `guardFire synthesizes up only while down pending`() {
        val b = PedalBatcher()
        b.onKey(163, 87, true, 1000)
        assertTrue(b.guardFire(163, 2000))
        val blocks = b.flush()
        assertEquals(1, blocks.size)
        val up = blocks[0].events.last()
        assertFalse(up.down)
        assertEquals(2000L, up.t)                // 合成 up 时戳=now
        assertFalse(b.guardFire(163, 3000))      // 已补过，不重复
    }

    @Test
    fun `guardFire no-op after real up`() {
        val b = PedalBatcher()
        b.onKey(163, 87, true, 1000)
        b.onKey(163, 87, false, 1010)
        assertFalse(b.guardFire(163, 2000))
    }

    @Test
    fun `late real up after guard keeps dt non-negative`() {
        val b = PedalBatcher()
        b.onKey(163, 87, true, 1000)
        b.guardFire(163, 2000)                   // 合成 up @2000 先入队
        b.onKey(163, 87, false, 1990)            // 迟到的真实 up（时戳更早）
        val blocks = b.flush()
        assertEquals(1, blocks.size)
        assertEquals(2000L, blocks[0].et)        // et 取块内 max，而非末元素
        for (e in blocks[0].events) assertTrue(blocks[0].et - e.t >= 0)
    }

    @Test
    fun `clear empties in-flight state`() {
        val b = PedalBatcher()
        b.onKey(163, 87, true, 1000)
        b.clear()
        assertTrue(b.flush().isEmpty())
        assertFalse(b.guardFire(163, 2000))
    }
}
