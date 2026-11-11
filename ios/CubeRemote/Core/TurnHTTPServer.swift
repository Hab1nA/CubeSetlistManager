import Foundation
import Network
import CubeRemoteCore

/// 本机 HTTP 服务：接收电脑端 push_turn 的 POST /turn。
/// iOS 不允许后台常驻监听，所以只在前台/短时间后台存活。
final class TurnHTTPServer {
    private let queue = DispatchQueue(label: "cube.remote.turn.http")
    private var listener: NWListener?
    private(set) var port: UInt16 = 0

    var onTurn: ((TurnDirection) -> Void)?
    var onState: ((Bool, UInt16) -> Void)?

    func start(port: UInt16) {
        stop()
        guard let nwPort = NWEndpoint.Port(rawValue: port) else {
            onState?(false, port)
            return
        }
        do {
            let params = NWParameters.tcp
            params.allowLocalEndpointReuse = true
            let listener = try NWListener(using: params, on: nwPort)
            listener.stateUpdateHandler = { [weak self] state in
                guard let self else { return }
                switch state {
                case .ready:
                    self.port = port
                    DispatchQueue.main.async { self.onState?(true, port) }
                case .failed, .cancelled:
                    DispatchQueue.main.async { self.onState?(false, port) }
                default:
                    break
                }
            }
            listener.newConnectionHandler = { [weak self] connection in
                self?.accept(connection)
            }
            listener.start(queue: queue)
            self.listener = listener
            self.port = port
        } catch {
            onState?(false, port)
        }
    }

    func stop() {
        listener?.cancel()
        listener = nil
    }

    private func accept(_ connection: NWConnection) {
        connection.start(queue: queue)
        receive(connection, buffer: Data())
    }

    private func receive(_ connection: NWConnection, buffer: Data) {
        connection.receive(minimumIncompleteLength: 1, maximumLength: 64 * 1024) {
            [weak self] data, _, isComplete, error in
            guard let self else { connection.cancel(); return }
            var accumulated = buffer
            if let data { accumulated.append(data) }
            do {
                if let request = try HTTPParser.parse(accumulated) {
                    self.respond(connection, request: request)
                    return
                }
            } catch {
                self.send(connection, status: 400,
                          object: ["error": "bad request"])
                return
            }
            if error != nil || isComplete || accumulated.count > 128 * 1024 {
                connection.cancel()
                return
            }
            self.receive(connection, buffer: accumulated)
        }
    }

    private func respond(_ connection: NWConnection, request: HTTPRequest) {
        if request.method == "GET" && request.path == "/health" {
            send(connection, status: 200,
                 object: ["ok": true, "app": "CubeRemote", "port": Int(port)])
            return
        }
        guard request.method == "POST", request.path == "/turn" else {
            send(connection, status: 404, object: ["error": "not found"])
            return
        }
        guard let turn = TurnRequest(body: request.body) else {
            send(connection, status: 400, object: ["error": "bad turn payload"])
            return
        }
        DispatchQueue.main.async { [weak self] in
            self?.onTurn?(turn.direction)
        }
        send(connection, status: 200,
             object: ["ok": true, "dir": turn.direction.rawValue])
    }

    private func send(_ connection: NWConnection, status: Int,
                      object: [String: Any]) {
        let data = HTTPResponseWriter.json(status: status, object: object)
        connection.send(content: data, completion: .contentProcessed { _ in
            connection.cancel()
        })
    }
}
