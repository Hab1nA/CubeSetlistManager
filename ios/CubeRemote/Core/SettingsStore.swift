import Foundation
import CubeRemoteCore

/// 本机设置（UserDefaults）。连接信息、翻谱端口、MIDI 音符、踩钉转发开关。
final class SettingsStore {
    static let shared = SettingsStore()

    private let defaults = UserDefaults.standard

    private enum Key {
        static let host = "cube.host"
        static let port = "cube.port"
        static let token = "cube.token"
        static let turnPort = "cube.turnPort"
        static let nextNote = "cube.nextNote"
        static let prevNote = "cube.prevNote"
        static let pedalForward = "cube.pedalForward"
        static let keepAlive = "cube.keepAlive"
        static let recent = "cube.recent"
    }

    var connection: ConnectionInfo? {
        get {
            let host = defaults.string(forKey: Key.host) ?? ""
            guard !host.isEmpty else { return nil }
            let port = defaults.integer(forKey: Key.port)
            return ConnectionInfo(host: host,
                                  port: port == 0 ? 8767 : port,
                                  token: defaults.string(forKey: Key.token) ?? "")
        }
        set {
            if let c = newValue {
                defaults.set(c.host, forKey: Key.host)
                defaults.set(c.port, forKey: Key.port)
                defaults.set(c.token, forKey: Key.token)
                // 存带 ?t= 的 URL 形态：重新点最近连接时令牌随行，否则
                // 配了 authToken 的电脑重连即 401。旧版存的裸 host:port
                // 条目 parse 同样认得（令牌为空）。
                let entry = c.pageURL?.absoluteString ?? c.displayAddress
                var list = recent
                list.removeAll {
                    ConnectionInfo.parse($0)?.displayAddress == c.displayAddress
                }
                list.insert(entry, at: 0)
                recent = Array(list.prefix(6))
            } else {
                defaults.removeObject(forKey: Key.host)
                defaults.removeObject(forKey: Key.port)
                defaults.removeObject(forKey: Key.token)
            }
        }
    }

    var recent: [String] {
        get { defaults.stringArray(forKey: Key.recent) ?? [] }
        set { defaults.set(newValue, forKey: Key.recent) }
    }

    /// 最近连接（含令牌）。令牌与当前连接同级存 UserDefaults，未上
    /// Keychain——若日后有更高敏感诉求再迁移，存储形态不变。
    var recentEntries: [ConnectionInfo] {
        recent.compactMap { ConnectionInfo.parse($0) }
    }

    var token: String {
        get { defaults.string(forKey: Key.token) ?? "" }
        set { defaults.set(newValue, forKey: Key.token) }
    }

    var turnPort: UInt16 {
        get {
            let n = defaults.integer(forKey: Key.turnPort)
            return n >= 1024 && n <= 65535 ? UInt16(n) : 8766
        }
        set { defaults.set(Int(newValue), forKey: Key.turnPort) }
    }

    var nextNote: UInt8 {
        get {
            let n = defaults.integer(forKey: Key.nextNote)
            return n >= 0 && n <= 127 ? UInt8(n) : 36
        }
        set { defaults.set(Int(newValue), forKey: Key.nextNote) }
    }

    var prevNote: UInt8 {
        get {
            let n = defaults.integer(forKey: Key.prevNote)
            return n >= 0 && n <= 127 ? UInt8(n) : 37
        }
        set { defaults.set(Int(newValue), forKey: Key.prevNote) }
    }

    var pedalForward: Bool {
        get { defaults.bool(forKey: Key.pedalForward) }
        set { defaults.set(newValue, forKey: Key.pedalForward) }
    }

    var keepAlive: Bool {
        get { defaults.bool(forKey: Key.keepAlive) }
        set { defaults.set(newValue, forKey: Key.keepAlive) }
    }
}
