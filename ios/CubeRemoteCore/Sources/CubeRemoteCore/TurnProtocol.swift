import Foundation

public enum TurnDirection: String, Sendable, Equatable {
    case next
    case prev
}

/// 电脑端 push_turn 发来的 `{"dir":"next|prev","test":1,"t":"令牌"}`。
public struct TurnRequest: Equatable, Sendable {
    public let direction: TurnDirection
    public let isTest: Bool
    public let token: String

    public init(direction: TurnDirection, isTest: Bool = false,
                token: String = "") {
        self.direction = direction
        self.isTest = isTest
        self.token = token
    }

    public init?(body: Data) {
        guard let obj = (try? JSONSerialization.jsonObject(with: body))
                as? [String: Any] else { return nil }
        guard let raw = obj["dir"] as? String,
              let dir = TurnDirection(rawValue: raw) else { return nil }
        direction = dir
        if let flag = obj["test"] as? Bool {
            isTest = flag
        } else if let n = obj["test"] as? NSNumber {
            isTest = n.intValue != 0
        } else {
            isTest = false
        }
        token = obj["t"] as? String ?? ""
    }
}
