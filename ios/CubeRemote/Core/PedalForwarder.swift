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
    /// 会话代数：stop()/start() 自增，在途请求的完成/重试回调带旧代数
    /// 一律丢弃——否则切换电脑后旧的 403 会停掉新会话、旧重试会发到新地址。
    private var generation = 0
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
        generation += 1
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
        let gen = generation
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
                    guard self.generation == gen else { return }
                    self.stop()
                    self.onDisabled?("电脑端未启用平板转发链路")
                }
                return
            }
            if error != nil, retry {
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.2) {
                    guard self.generation == gen else { return }
                    self.post(packet, retry: false)
                }
            }
        }.resume()
    }
}
