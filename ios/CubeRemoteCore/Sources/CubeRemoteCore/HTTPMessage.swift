import Foundation

/// 解析完成的 HTTP/1.1 请求（只支持本项目需要的子集：无 chunked）。
public struct HTTPRequest: Equatable, Sendable {
    public let method: String
    public let path: String
    public let query: [String: String]
    public let headers: [String: String]
    public let body: Data

    public init(method: String, path: String, query: [String: String],
                headers: [String: String], body: Data) {
        self.method = method
        self.path = path
        self.query = query
        self.headers = headers
        self.body = body
    }
}

public enum HTTPParseError: Error, Equatable {
    case malformed
    case bodyTooLarge
}

public enum HTTPParser {
    public static let maxBody = 1 << 20          // 与 PC 端 1MB 上限一致

    /// 返回 nil=数据还不完整（继续收）；抛错=请求非法（直接 400 关闭）。
    public static func parse(_ data: Data) throws -> HTTPRequest? {
        let sep = Data("\r\n\r\n".utf8)
        guard let headEnd = data.range(of: sep)?.lowerBound else {
            if data.count > 64 * 1024 { throw HTTPParseError.malformed }
            return nil
        }
        let headData = data[..<headEnd]
        guard let head = String(data: headData, encoding: .utf8) else {
            throw HTTPParseError.malformed
        }
        var lines = head.components(separatedBy: "\r\n")
        guard !lines.isEmpty else { throw HTTPParseError.malformed }
        let requestLine = lines.removeFirst().split(separator: " ",
                                                    omittingEmptySubsequences: true)
        guard requestLine.count >= 2 else { throw HTTPParseError.malformed }
        let method = String(requestLine[0]).uppercased()
        let target = String(requestLine[1])

        var headers: [String: String] = [:]
        for line in lines where !line.isEmpty {
            guard let colon = line.firstIndex(of: ":") else {
                throw HTTPParseError.malformed
            }
            let key = line[..<colon].trimmingCharacters(in: .whitespaces)
                .lowercased()
            let value = line[line.index(after: colon)...]
                .trimmingCharacters(in: .whitespaces)
            headers[key] = value
        }

        let length: Int
        if let raw = headers["content-length"] {
            guard let n = Int(raw), n >= 0 else {
                throw HTTPParseError.malformed
            }
            guard n <= maxBody else { throw HTTPParseError.bodyTooLarge }
            length = n
        } else {
            length = 0
        }
        let bodyStart = headEnd + sep.count
        guard data.count >= bodyStart + length else { return nil }
        let body = data.subdata(in: bodyStart..<(bodyStart + length))

        let (path, query) = splitTarget(target)
        return HTTPRequest(method: method, path: path, query: query,
                           headers: headers, body: body)
    }

    public static func splitTarget(_ target: String)
        -> (path: String, query: [String: String]) {
        guard let q = target.firstIndex(of: "?") else {
            return (target.isEmpty ? "/" : target, [:])
        }
        let path = String(target[..<q])
        let raw = String(target[target.index(after: q)...])
        var out: [String: String] = [:]
        for pair in raw.split(separator: "&", omittingEmptySubsequences: true) {
            let kv = pair.split(separator: "=", maxSplits: 1,
                                omittingEmptySubsequences: false)
            let key = String(kv[0]).removingPercentEncoding
                ?? String(kv[0])
            let value = kv.count > 1
                ? (String(kv[1]).removingPercentEncoding ?? String(kv[1]))
                : ""
            if !key.isEmpty { out[key] = value }
        }
        return (path.isEmpty ? "/" : path, out)
    }
}

public enum HTTPResponseWriter {
    public static func json(status: Int, object: [String: Any],
                            extraHeaders: [String: String] = [:]) -> Data {
        let body = (try? JSONSerialization.data(withJSONObject: object,
                                                options: [.sortedKeys]))
            ?? Data("{}".utf8)
        var head = "HTTP/1.1 \(status) \(reason(status))\r\n"
        head += "Content-Type: application/json; charset=utf-8\r\n"
        head += "Content-Length: \(body.count)\r\n"
        head += "Connection: close\r\n"
        for (k, v) in extraHeaders.sorted(by: { $0.key < $1.key }) {
            head += "\(k): \(v)\r\n"
        }
        head += "\r\n"
        var data = Data(head.utf8)
        data.append(body)
        return data
    }

    public static func reason(_ status: Int) -> String {
        switch status {
        case 200: return "OK"
        case 400: return "Bad Request"
        case 401: return "Unauthorized"
        case 404: return "Not Found"
        case 405: return "Method Not Allowed"
        case 413: return "Payload Too Large"
        case 429: return "Too Many Requests"
        default: return "Error"
        }
    }
}
