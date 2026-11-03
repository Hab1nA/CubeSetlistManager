import Foundation

/// 连接信息：iPhone/iPad 通过局域网访问 PC 的 APP 页面（默认 8767）。
public struct ConnectionInfo: Equatable, Sendable {
    public var host: String
    public var port: Int
    public var token: String

    public init(host: String, port: Int = 8767, token: String = "") {
        self.host = host
        self.port = port
        self.token = token
    }

    public var displayAddress: String { "\(host):\(port)" }

    public var pageURL: URL? {
        var comps = URLComponents()
        comps.scheme = "http"
        comps.host = host
        comps.port = port
        comps.path = "/"
        if !token.isEmpty {
            comps.queryItems = [URLQueryItem(name: "t", value: token)]
        }
        return comps.url
    }

    public var baseURL: URL? {
        var comps = URLComponents()
        comps.scheme = "http"
        comps.host = host
        comps.port = port
        return comps.url
    }

    /// 接受四种写法：
    /// - cube://connect?host=192.168.1.5&port=8767&token=abc
    /// - http://192.168.1.5:8767/?t=abc
    /// - 192.168.1.5:8767
    /// - 192.168.1.5（端口默认 8767）
    public static func parse(_ raw: String) -> ConnectionInfo? {
        let text = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return nil }

        if text.lowercased().hasPrefix("cube://") {
            guard let comps = URLComponents(string: text) else { return nil }
            let q = Dictionary(uniqueKeysWithValues:
                (comps.queryItems ?? []).map { ($0.name, $0.value ?? "") })
            let host = q["host"] ?? q["ip"] ?? ""
            guard !host.isEmpty else { return nil }
            let port = Int(q["port"] ?? "") ?? 8767
            guard (1...65535).contains(port) else { return nil }
            return ConnectionInfo(host: host, port: port,
                                  token: q["t"] ?? q["token"] ?? "")
        }

        var candidate = text
        if !candidate.contains("://") { candidate = "http://" + candidate }
        guard let comps = URLComponents(string: candidate),
              let host = comps.host, !host.isEmpty else { return nil }
        let port = comps.port ?? 8767
        guard (1...65535).contains(port) else { return nil }
        var token = ""
        for item in comps.queryItems ?? []
        where item.name == "t" || item.name == "token" {
            token = item.value ?? ""
            break
        }
        return ConnectionInfo(host: host, port: port, token: token)
    }
}
