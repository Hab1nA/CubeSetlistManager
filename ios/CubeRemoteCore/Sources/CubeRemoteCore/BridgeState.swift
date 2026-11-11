import Foundation

/// 注入 WKWebView 的同步桥状态：页面里的 CubeApp.* 直接读这份缓存，
/// 设置动作 postMessage 给原生并乐观更新（与 Android JS Bridge 语义一致）。
public struct BridgeState: Equatable, Sendable {
    public var version: String
    public var turnMethod: String
    public var pedalForward: Bool
    public var turnPort: Int
    public var nextNote: Int
    public var prevNote: Int
    public var listening: Bool

    public init(version: String = "0.1.0", turnMethod: String = "midi",
                pedalForward: Bool = false, turnPort: Int = 0,
                nextNote: Int = Int(TurnNotes.next),
                prevNote: Int = Int(TurnNotes.prev),
                listening: Bool = false) {
        self.version = version
        self.turnMethod = turnMethod
        self.pedalForward = pedalForward
        self.turnPort = turnPort
        self.nextNote = nextNote
        self.prevNote = prevNote
        self.listening = listening
    }

    public var jsonObject: [String: Any] {
        [
            "platform": "ios",
            "version": version,
            "turnMethod": turnMethod,
            "pedalForward": pedalForward,
            "turnPort": turnPort,
            "nextNote": nextNote,
            "prevNote": prevNote,
            "listening": listening,
        ]
    }

    public var jsonString: String {
        guard let data = try? JSONSerialization.data(withJSONObject: jsonObject,
                                                     options: [.sortedKeys]),
              let text = String(data: data, encoding: .utf8) else { return "{}" }
        return text
    }
}
