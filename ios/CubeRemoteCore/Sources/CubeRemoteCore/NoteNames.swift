import Foundation

/// 音名口径与电脑端 kbd_auto.note_name 同源：60=C3、72=C4（Cubase /
/// Studio One 的 DAW 显示约定，v0.16.2 起全仓锚点对齐此口径）。两端
/// 无共享代码通道，各自实现、测试锁同一组锚点值——改口径必须同步两处。
public enum NoteNames {
    static let chr = ["C", "C#", "D", "D#", "E", "F",
                      "F#", "G", "G#", "A", "A#", "B"]

    public static func name(_ note: Int) -> String {
        chr[(note % 12 + 12) % 12] + String(note / 12 - 2)
    }
}

/// iOS 翻谱默认锚点：与 web_remote.CMD_NEXT / CMD_PREV 同号
/// （48=C2 下一页、49=C#2 上一页）。UI 文案只写音名不写号——号搬家时
/// 文案不变，与电脑端 0.16.2 迁移同规。
public enum TurnNotes {
    public static let next: UInt8 = 48
    public static let prev: UInt8 = 49
}
