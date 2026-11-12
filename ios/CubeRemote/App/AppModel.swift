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
    /// 首次连接/改地址：页面标题核验通过（pageDidLoad）才写 store——连错
    /// 设备不再把坏地址存成有效连接、重启自动直连错误页面（对齐 Android）。
    /// 记待核验对象本身：核验异步窗口内改址/换令牌不把未核验对象落库
    private var pendingPersist: ConnectionInfo?
    private let turnServer = TurnHTTPServer()
    // CoreMIDI / AVAudioEngine 首次创建可能要几百毫秒，延后到真正用到时，
    // 避免启动画面被拖成白屏。
    private lazy var midiOut = MIDITurnService()
    private lazy var pedalIn = PedalMIDIService()
    private let forwarder = PedalForwarder()
    private lazy var audio = AudioKeepAlive()

    init() {
        turnPort = store.turnPort
        pedalForward = store.pedalForward
        nextNote = store.nextNote
        prevNote = store.prevNote
        keepAlive = store.keepAlive
        connection = store.connection
        wireServices()
        if connection != nil {
            syncTurnToken()
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

    func pageDidLoad(for verified: URL?) {
        pageLoaded = true
        webError = nil
        if let pending = pendingPersist, pending == connection,
           verified == connection?.pageURL {
            store.connection = connection   // 标题核验通过且核验的正是当前
            pendingPersist = nil            //   待核验连接才落库（异步窗口
        }                                   //   内改址/换令牌不误写 store）
        pushBridge()
    }

    func pageFailed(_ message: String) {
        pageLoaded = false
        webError = message
    }

    /// 回到前台：NWListener 可能已被系统挂起，重新拉起端口与转发。
    func resume() {
        guard connection != nil else { return }
        if !turnRunning { turnServer.start(port: turnPort) }
        if pedalForward { startForwarder() }
        pushBridge()
    }

    func retryPage() {
        webError = nil
        webView?.reload()
    }

    func connect(_ info: ConnectionInfo) {
        // 不立即持久化：标题核验通过（pageDidLoad）才写 store——连错设备
        // 不再把坏地址存成有效连接（对齐 Android 内容核验）
        pendingPersist = info
        connection = info
        webError = nil
        midiOut.start()
        syncTurnToken()
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
        pedalIn.stop()
        store.connection = nil
        connection = nil
        pendingPersist = nil
        syncTurnToken()
        webError = nil
        statusNote = "已断开"
    }

    /// /turn 监听口的配对令牌随连接走：未连接或未配令牌=不校验（兼容旧
    /// 安卓端与未开 authToken 的电脑），配了就要求推送体带匹配 t 字段。
    private func syncTurnToken() {
        turnServer.expectedToken = connection?.token ?? ""
    }

    // MARK: - 翻谱 / 踩钉

    private func handleTurn(_ direction: TurnDirection) {
        midiOut.start()
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
            pedalIn.stop()      // 转发关掉输入口也停：不留常驻回调耗资源
        }
        pushBridge()
    }

    private func startForwarder() {
        guard let connection else { return }
        pedalIn.onEvent = { [weak self] note, down in
            Task { @MainActor in self?.handlePedal(note: note, down: down) }
        }
        // 排除自己的虚拟源：翻谱 note on/off 不许回流成踏板事件（回环）
        pedalIn.isSelfSource = { [weak self] uid in
            guard let self, uid != 0 else { return false }
            return self.midiOut.sourceUniqueID == uid
        }
        pedalIn.start()
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
        if pendingPersist == nil {
            store.connection = info   // 待核验窗口内换令牌只改内存，落库
        }                             //   等标题核验通过一并写（复审攻击轮）
        syncTurnToken()
        pushBridge()
    }

    func changeAddress(_ raw: String) {
        guard let parsed = ConnectionInfo.parse(raw) else {
            statusNote = "地址格式不对：\(raw)"
            return
        }
        guard parsed != connection else { return }
        pendingPersist = parsed
        connection = parsed
        syncTurnToken()
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
