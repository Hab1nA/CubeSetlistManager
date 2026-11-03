import Foundation
import UIKit
import CubeRemoteCore

/// 把本机 CoreMIDI 踏板按键转发给电脑的 /pedal/event。
/// 协议与 Android PedalForwarder 相同；音符按音高类映射到 LKC F13–F24。
final class PedalForwarder {
    private var endpoint: URL?
    private var token = ""
    private var seq = 0
    private let sid = String(UUID().uuidString
        .replacingOccurrences(of: "-", with: "")
        .prefix(8)).lowercased()
    private var heartbeat: Timer?
    private var stopped = true
    private let session: URLSession

    var onDisabled: ((String) -> Void)?
    var onStatus: ((String) -> Void)?

    init() {
        let config = URLSessionConfiguration.ephemeral
        config.timeoutIntervalForRequest = 1.5
        config.timeoutIntervalForResource = 2.5
        config.waitsForConnectivity = false
        session = URLSession(configuration: config)
    }

    func start(connection: ConnectionInfo) {
        stop()
        guard var comps = connection.baseURL.flatMap({
            URLComponents(url: $0, resolvingAgainstBaseURL: false)
        }) else { return }
        comps.path = "/pedal/event"
        endpoint = comps.url
        token = connection.token
        seq = 0
        stopped = false
        heartbeat = Timer.scheduledTimer(withTimeInterval: 5.0, repeats: true) {
            [weak self] _ in
            self?.sendHeartbeat()
        }
        onStatus?("踩钉转发已连接")
    }

    func stop() {
        stopped = true
        heartbeat?.invalidate()
        heartbeat = nil
        endpoint = nil
    }

    func send(note: UInt8, down: Bool) {
        guard !stopped, !(endpoint == nil) else { return }
        seq += 1
        let packet = PedalPacket(
            device: "iOS-" + UIDevice.current.name,
            seq: seq,
            et: Int(ProcessInfo.processInfo.systemUptime * 1000),
            sid: sid,
            events: [PedalEvent(vk: PedalKeyMap.lkc(forNote: note), down: down)])
        post(packet, retry: true)
    }

    private func sendHeartbeat() {
        guard !stopped else { return }
        seq += 1
        let packet = PedalPacket(
            device: "iOS-" + UIDevice.current.name,
            seq: seq,
            et: Int(ProcessInfo.processInfo.systemUptime * 1000),
            sid: sid,
            heartbeat: true)
        post(packet, retry: false)
    }

    private func post(_ packet: PedalPacket, retry: Bool) {
        guard let endpoint, !stopped else { return }
        var request = URLRequest(url: endpoint)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        if !token.isEmpty {
            request.setValue(token, forHTTPHeaderField: "X-Cube-Token")
        }
        request.httpBody = packet.jsonData()
        session.dataTask(with: request) { [weak self] _, response, error in
            guard let self else { return }
            if let http = response as? HTTPURLResponse, http.statusCode == 403 {
                DispatchQueue.main.async {
                    self.stop()
                    self.onDisabled?("电脑端未启用平板转发链路")
                }
                return
            }
            if error != nil, retry {
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.2) {
                    self.post(packet, retry: false)
                }
            }
        }.resume()
    }
}
