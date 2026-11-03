import Foundation

public struct PedalEvent: Equatable, Sendable {
    public let vk: Int
    public let down: Bool
    public let dt: Int

    public init(vk: Int, down: Bool, dt: Int = 0) {
        self.vk = vk
        self.down = down
        self.dt = dt
    }
}

public enum PedalKeyMap {
    /// iOS 没有 Android 的 scanCode：把 MIDI 音符按音高类映射到 Linux
    /// keycode F13–F24（183–194）。PC 的 remote_key_to_vk 原样转成
    /// VK 0x7C–0x87，踩钉学习页看到的就是 F13–F24。
    public static let lkcBase = 183
    public static func lkc(forNote note: UInt8) -> Int {
        lkcBase + Int(note % 12)
    }
}

public struct PedalPacket: Equatable, Sendable {
    public let device: String
    public let seq: Int
    public let et: Int
    public let sid: String
    public let heartbeat: Bool
    public let events: [PedalEvent]

    public init(device: String, seq: Int, et: Int, sid: String,
                heartbeat: Bool = false, events: [PedalEvent] = []) {
        self.device = device
        self.seq = seq
        self.et = et
        self.sid = sid
        self.heartbeat = heartbeat
        self.events = events
    }

    public var jsonObject: [String: Any] {
        var obj: [String: Any] = [
            "device": device,
            "seq": seq,
            "et": et,
            "sid": sid,
        ]
        if heartbeat {
            obj["hb"] = true
        } else {
            obj["events"] = events.map {
                ["vk": $0.vk, "kc": 0, "down": $0.down, "dt": $0.dt]
            }
        }
        return obj
    }

    public func jsonData() -> Data {
        (try? JSONSerialization.data(withJSONObject: jsonObject)) ?? Data()
    }
}
