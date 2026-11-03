import Foundation
import SwiftUI
import WebKit
import CubeRemoteCore

@MainActor
final class AppModel: ObservableObject {
    @Published var connection: ConnectionInfo?
    @Published var pageLoaded = false
    @Published var webError: String?
    @Published var turnRunning = false
    @Published var turnPort: UInt16
    @Published var pedalForward: Bool
    @Published var nextNote: UInt8
    @Published var prevNote: UInt8
    @Published var keepAlive: Bool
    @Published var statusNote: String = ""

    let store = SettingsStore.shared
    private weak var webView: WKWebView?
    private let turnServer = TurnHTTPServer()
    private let midiOut = MIDITurnService()
    private let pedalIn = PedalMIDIService()
    private let forwarder = PedalForwarder()
    private let audio = AudioKeepAlive()

    init() {
        turnPort = store.turnPort
        pedalForward = store.pedalForward
        nextNote = store.nextNote
        prevNote = store.prevNote
        keepAlive = store.keepAlive
        connection = store.connection
        wireServices()
        midiOut.start()
        pedalIn.start()
        if connection != nil {
            turnServer.start(port: turnPort)
            if pedalForward { startForwarder() }
            if keepAlive { try? audio.start() }
        }
    }

    var appVersion: String {
        Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String
            ?? "0.1.0"
    }

    var bridgeState: BridgeState {
        BridgeState(version: appVersion,
                    turnMethod: "midi",
                    pedalForward: pedalForward,
                    turnPort: turnRunning ? Int(turnPort) : 0,
                    nextNote: Int(nextNote),
                    prevNote: Int(prevNote),
                    listening: turnRunning)
    }

    // MARK: - 生命周期

    func attach(webView: WKWebView) {
        self.webView = webView
        pushBridge()
    }

    func pageDidLoad() {
        pageLoaded = true
        webError = nil
        pushBridge()
    }

    func pageFailed(_ message: String) {
        pageLoaded = false
        webError = message
    }

    func retryPage() {
        webError = nil
        webView?.reload()
    }

    func connect(_ info: ConnectionInfo) {
        store.connection = info
        connection = info
        webError = nil
        midiOut.start()
        turnServer.start(port: store.turnPort)
        if pedalForward { startForwarder() }
        if keepAlive { try? audio.start() }
        pushBridge()
    }

    func disconnect() {
        forwarder.stop()
        turnServer.stop()
        audio.stop()
        midiOut.stop()
        store.connection = nil
        connection = nil
        webError = nil
        statusNote = "已断开"
    }

    // MARK: - 翻谱 / 踩钉

    private func handleTurn(_ direction: TurnDirection) {
        let note = direction == .next ? store.nextNote : store.prevNote
        midiOut.send(note: note, on: true)
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.08) { [weak self] in
            self?.midiOut.send(note: note, on: false)
        }
        statusNote = direction == .next ? "翻谱：下一页" : "翻谱：上一页"
    }

    private func handlePedal(note: UInt8, down: Bool) {
        guard pedalForward else { return }
        forwarder.send(note: note, down: down)
    }

    func setPedalForward(_ on: Bool) {
        pedalForward = on
        store.pedalForward = on
        if on {
            startForwarder()
        } else {
            forwarder.stop()
        }
        pushBridge()
    }

    private func startForwarder() {
        guard let connection else { return }
        forwarder.start(connection: connection)
    }

    // MARK: - 设置

    func setTurnPort(_ port: UInt16) {
        guard port >= 1024 else { return }
        turnPort = port
        store.turnPort = port
        turnServer.start(port: port)
        pushBridge()
    }

    func setMidiNotes(next: UInt8, prev: UInt8) {
        nextNote = next
        prevNote = prev
        store.nextNote = next
        store.prevNote = prev
        pushBridge()
    }

    func setKeepAlive(_ on: Bool) {
        keepAlive = on
        store.keepAlive = on
        if on { try? audio.start() } else { audio.stop() }
        pushBridge()
    }

    func updateToken(_ token: String) {
        guard var info = connection else { return }
        let trimmed = token.trimmingCharacters(in: .whitespacesAndNewlines)
        guard info.token != trimmed else { return }
        info.token = trimmed
        connection = info
        store.connection = info
        pushBridge()
    }

    func changeAddress(_ raw: String) {
        guard let parsed = ConnectionInfo.parse(raw) else {
            statusNote = "地址格式不对：\(raw)"
            return
        }
        guard parsed != connection else { return }
        store.connection = parsed
        connection = parsed
        turnServer.start(port: store.turnPort)
        if pedalForward { startForwarder() }
        pushBridge()
    }

    // MARK: - 页面桥

    func handleBridge(cmd: String, body: [String: Any]) {
        switch cmd {
        case "setPedalForward":
            setPedalForward(body["value"] as? Bool ?? false)
        case "setTurnPort":
            if let n = body["value"] as? NSNumber {
                setTurnPort(UInt16(clamping: n.intValue))
            }
        case "setMidiNotes":
            if let arr = body["value"] as? [NSNumber], arr.count == 2 {
                setMidiNotes(next: UInt8(clamping: arr[0].intValue),
                             prev: UInt8(clamping: arr[1].intValue))
            }
        case "requestAddress":
            if let text = body["value"] as? String { changeAddress(text) }
        case "openSettings":
            if let url = URL(string: UIApplication.openSettingsURLString) {
                UIApplication.shared.open(url)
            }
        default:
            break
        }
    }

    func pushBridge() {
        guard let webView else { return }
        let js = "window.__cubeApply && window.__cubeApply(\(bridgeState.jsonString))"
        webView.evaluateJavaScript(js, completionHandler: nil)
    }

    private func wireServices() {
        turnServer.onState = { [weak self] ok, port in
            Task { @MainActor in
                guard let self else { return }
                self.turnRunning = ok
                self.turnPort = port
                self.statusNote = ok ? "翻谱端口 \(port) 已监听"
                                     : "翻谱端口 \(port) 启动失败"
                self.pushBridge()
            }
        }
        turnServer.onTurn = { [weak self] direction in
            Task { @MainActor in self?.handleTurn(direction) }
        }
        pedalIn.onEvent = { [weak self] note, down in
            Task { @MainActor in self?.handlePedal(note: note, down: down) }
        }
        forwarder.onDisabled = { [weak self] message in
            Task { @MainActor in
                guard let self else { return }
                self.pedalForward = false
                self.store.pedalForward = false
                self.statusNote = message
                self.pushBridge()
            }
        }
        forwarder.onStatus = { [weak self] message in
            Task { @MainActor in self?.statusNote = message }
        }
    }
}
