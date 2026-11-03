import Foundation
import AVFoundation

/// 可选：播放 0 电平的循环 buffer，让 App 在锁屏/切后台后多活一会儿。
/// 这不是“后台常驻监听”：iOS 仍可能回收；上架审核需说明用途。
final class AudioKeepAlive {
    private let engine = AVAudioEngine()
    private var player: AVAudioPlayerNode?
    private(set) var running = false

    func start() throws {
        guard !running else { return }
        let session = AVAudioSession.sharedInstance()
        try session.setCategory(.playback, mode: .default,
                                options: [.mixWithOthers])
        try session.setActive(true)
        let player = AVAudioPlayerNode()
        engine.attach(player)
        guard let format = AVAudioFormat(standardFormatWithSampleRate: 44_100,
                                         channels: 1) else { return }
        engine.connect(player, to: engine.mainMixerNode, format: format)
        engine.mainMixerNode.outputVolume = 0
        let frames: AVAudioFrameCount = 44_100
        guard let buffer = AVAudioPCMBuffer(pcmFormat: format,
                                            frameCapacity: frames) else { return }
        buffer.frameLength = frames
        if let channels = buffer.floatChannelData {
            for i in 0..<Int(frames) { channels[0][i] = 0 }
        }
        try engine.start()
        player.scheduleBuffer(buffer, at: nil, options: [.loops],
                              completionHandler: nil)
        player.play()
        self.player = player
        running = true
    }

    func stop() {
        player?.stop()
        engine.stop()
        player = nil
        running = false
        try? AVAudioSession.sharedInstance().setActive(
            false, options: [.notifyOthersOnDeactivation])
    }
}
