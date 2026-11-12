import Foundation

public enum TurnDirection: String, Sendable, Equatable {
    case next
    case prev
}

/// 电脑端 push_turn 发来的 `{"dir":"next|prev","t":"令牌"}`。test 旗标
/// iOS 不解析（测试推送=当次翻页演示，行为与正常推送一致）。
public struct TurnRequest: Equatable, Sendable {
    public let direction: TurnDirection
    public let token: String

    public init(direction: TurnDirection, token: String = "") {
        self.direction = direction
        self.token = token
    }

    public init?(body: Data) {
        guard let obj = (try? JSONSerialization.jsonObject(with: body))
                as? [String: Any] else { return nil }
        guard let raw = obj["dir"] as? String,
              let dir = TurnDirection(rawValue: raw) else { return nil }
        direction = dir
        token = obj["t"] as? String ?? ""
    }
}
