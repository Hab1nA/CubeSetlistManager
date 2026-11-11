import Foundation
import CoreMIDI

/// 虚拟 MIDI 源：电脑端推来的 next/prev 变成音符，供 forScore 等谱面 App 学习。
final class MIDITurnService {
    private var client = MIDIClientRef()
    private var source = MIDIEndpointRef()
    private(set) var running = false
    /// 虚拟源的 CoreMIDI 唯一 ID：踏板输入端据此排除自己（否则每次翻页
    /// 的 note on/off 会被当成踏板事件转发回电脑形成回环），stop 后归零。
    private(set) var sourceUniqueID: MIDIUniqueID = 0

    func start() {
        guard !running else { return }
        var status = MIDIClientCreate("CubeRemote" as CFString, nil, nil, &client)
        guard status == noErr else { return }
        status = MIDISourceCreate(client, "Cube Remote" as CFString, &source)
        if status == noErr {
            var uid: MIDIUniqueID = 0
            if MIDIObjectGetIntegerProperty(source, kMIDIPropertyUniqueID,
                                            &uid) == noErr {
                sourceUniqueID = uid
            }
        }
        running = status == noErr
    }

    func stop() {
        if source != 0 { MIDIEndpointDispose(source); source = 0 }
        if client != 0 { MIDIClientDispose(client); client = 0 }
        running = false
        sourceUniqueID = 0
    }

    func send(note: UInt8, on: Bool) {
        guard source != 0 else { return }
        let bytes: [UInt8] = [0x90, note & 0x7F, on ? 100 : 0]
        let capacity = 1024
        let raw = UnsafeMutableRawPointer.allocate(
            byteCount: capacity,
            alignment: MemoryLayout<MIDIPacketList>.alignment)
        defer { raw.deallocate() }
        let list = raw.bindMemory(to: MIDIPacketList.self, capacity: 1)
        let packet = MIDIPacketListInit(list)
        bytes.withUnsafeBufferPointer { buffer in
            _ = MIDIPacketListAdd(list, capacity, packet, 0,
                                  buffer.count, buffer.baseAddress!)
        }
        MIDIReceived(source, list)
    }
}

/// 监听所有 CoreMIDI 输入源（USB / 蓝牙 MIDI 踏板），把 note on/off 交给上层。
final class PedalMIDIService {
    private var client = MIDIClientRef()
    private var port = MIDIPortRef()
    private var connected = Set<MIDIUniqueID>()
    private(set) var running = false
    var onEvent: ((UInt8, Bool) -> Void)?
    /// 由 AppModel 注入：返回 true 的源是本 App 自己的虚拟源，不接入。
    var isSelfSource: ((MIDIUniqueID) -> Bool)?

    func start() {
        guard !running else { return }
        var status = MIDIClientCreateWithBlock("CubeRemotePedal" as CFString,
                                               &client) { [weak self] _ in
            DispatchQueue.main.async { self?.connectAll() }
        }
        guard status == noErr else { return }
        status = MIDIInputPortCreateWithBlock(client,
                                              "CubeRemoteIn" as CFString,
                                              &port) { [weak self] list, _ in
            self?.parse(list)
        }
        guard status == noErr else { return }
        running = true
        connectAll()
    }

    func stop() {
        if port != 0 { MIDIPortDispose(port); port = 0 }
        if client != 0 { MIDIClientDispose(client); client = 0 }
        connected.removeAll()
        running = false
    }

    private func connectAll() {
        guard port != 0 else { return }
        let count = MIDIGetNumberOfSources()
        for index in 0..<count {
            let source = MIDIGetSource(index)
            var uid: MIDIUniqueID = 0
            guard MIDIObjectGetIntegerProperty(source, kMIDIPropertyUniqueID,
                                               &uid) == noErr else { continue }
            if connected.contains(uid) { continue }
            if let isSelfSource, uid != 0, isSelfSource(uid) { continue }
            if MIDIPortConnectSource(port, source, nil) == noErr {
                connected.insert(uid)
            }
        }
    }

    private func parse(_ list: UnsafePointer<MIDIPacketList>) {
        let count = Int(list.pointee.numPackets)
        var packet = list.pointee.packet
        for _ in 0..<count {
            let length = Int(packet.length)
            withUnsafeBytes(of: packet.data) { raw in
                let bytes = raw.bindMemory(to: UInt8.self)
                var i = 0
                while i + 1 < length {
                    let status = bytes[i] & 0xF0
                    if status == 0x90 || status == 0x80 {
                        guard i + 2 < length else { break }
                        let note = bytes[i + 1] & 0x7F
                        let velocity = bytes[i + 2] & 0x7F
                        let down = status == 0x90 && velocity > 0
                        DispatchQueue.main.async { [weak self] in
                            self?.onEvent?(note, down)
                        }
                        i += 3
                    } else if status == 0xC0 || status == 0xD0 {
                        i += 2
                    } else if status >= 0x80 {
                        i += 3
                    } else {
                        i += 1
                    }
                }
            }
            packet = MIDIPacketNext(&packet).pointee
        }
    }
}
