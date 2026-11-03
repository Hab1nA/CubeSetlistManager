import SwiftUI

struct SettingsView: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.dismiss) private var dismiss
    @State private var portText = ""
    @State private var tokenText = ""

    var body: some View {
        NavigationStack {
            Form {
                Section("电脑") {
                    LabeledContent("地址",
                                   value: model.connection?.displayAddress ?? "—")
                    LabeledContent("翻谱端口",
                                   value: model.turnRunning
                                   ? "\(model.turnPort) 监听中" : "未监听")
                    TextField("配对令牌", text: $tokenText)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .onSubmit { model.updateToken(tokenText) }
                }

                Section("MIDI 翻页") {
                    Stepper(value: nextBinding, in: 0...127) {
                        Text("下一页音符：\(model.nextNote)")
                    }
                    Stepper(value: prevBinding, in: 0...127) {
                        Text("上一页音符：\(model.prevNote)")
                    }
                    Text("虚拟 MIDI 源名称「Cube Remote」。在谱面 App 的翻页设置里"
                         + "学习这两个音符即可；电脑端推来的 next/prev 会发出对应音符。")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }

                Section("本机翻谱端口") {
                    TextField("8766", text: $portText)
                        .keyboardType(.numberPad)
                    Button("应用端口") {
                        if let value = UInt16(portText), value >= 1024 {
                            model.setTurnPort(value)
                        }
                    }
                    Text("电脑端「网页遥控 → 平板转发端口」需与此一致；默认 8766。"
                         + "iOS 只在前台监听，锁屏后端口会停止响应。")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }

                Section("踩钉") {
                    Toggle("转发本机 MIDI 踏板", isOn: pedalBinding)
                    Text("USB / 蓝牙 MIDI 踏板接到本机后，按键经 Wi-Fi 转发给电脑的"
                         + "「平板转发链路」；音符按音高类映射为 F13–F24。")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }

                Section("后台") {
                    Toggle("音频保活（实验）", isOn: keepAliveBinding)
                    Text("只延长锁屏后的存活时间，iOS 仍可能回收；若上架 App Store "
                         + "需向审核说明该静音播放用途。")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }

                Section {
                    Button("断开连接", role: .destructive) {
                        model.disconnect()
                        dismiss()
                    }
                }
            }
            .navigationTitle("设置")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("完成") {
                        model.updateToken(tokenText)
                        dismiss()
                    }
                }
            }
        }
        .onAppear {
            portText = String(model.turnPort)
            tokenText = model.connection?.token ?? ""
        }
    }

    private var nextBinding: Binding<Int> {
        Binding(
            get: { Int(model.nextNote) },
            set: { model.setMidiNotes(next: UInt8(clamping: $0),
                                      prev: model.prevNote) })
    }

    private var prevBinding: Binding<Int> {
        Binding(
            get: { Int(model.prevNote) },
            set: { model.setMidiNotes(next: model.nextNote,
                                      prev: UInt8(clamping: $0)) })
    }

    private var pedalBinding: Binding<Bool> {
        Binding(get: { model.pedalForward },
                set: { model.setPedalForward($0) })
    }

    private var keepAliveBinding: Binding<Bool> {
        Binding(get: { model.keepAlive },
                set: { model.setKeepAlive($0) })
    }
}
