import SwiftUI
import CubeRemoteCore

struct ConnectView: View {
    @EnvironmentObject private var model: AppModel
    @State private var host = ""
    @State private var port = "8767"
    @State private var token = ""
    @State private var scanning = false
    @State private var error: String?

    private let accent = Color(red: 0.36, green: 0.78, blue: 0.98)

    var body: some View {
        ZStack {
            LinearGradient(
                colors: [Color(red: 0.03, green: 0.05, blue: 0.09),
                         Color(red: 0.06, green: 0.10, blue: 0.16)],
                startPoint: .topLeading, endPoint: .bottomTrailing)
                .ignoresSafeArea()

            ScrollView {
                VStack(alignment: .leading, spacing: 24) {
                    header
                    card
                    if let error {
                        Text(error)
                            .font(.footnote)
                            .foregroundStyle(Color(red: 1.0, green: 0.45, blue: 0.4))
                    }
                    recents
                    footer
                }
                .padding(.horizontal, 24)
                .padding(.top, 48)
                .padding(.bottom, 32)
            }
        }
        .onAppear(perform: load)
        .sheet(isPresented: $scanning) {
            ScannerView { code in
                scanning = false
                guard let parsed = ConnectionInfo.parse(code) else {
                    error = "二维码里没有可用的电脑地址"
                    return
                }
                host = parsed.host
                port = String(parsed.port)
                token = parsed.token
                connect(parsed)
            }
        }
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("CUBE SETLIST")
                .font(.system(size: 12, weight: .semibold))
                .tracking(2.4)
                .foregroundStyle(accent)
            Text("Cube Remote")
                .font(.system(size: 40, weight: .bold, design: .rounded))
                .foregroundStyle(.white)
            Text("把 iPhone / iPad 变成舞台遥控器：\n曲目、走带、翻谱与踩钉转发。")
                .font(.system(size: 15))
                .foregroundStyle(.white.opacity(0.62))
                .lineSpacing(4)
        }
    }

    private var card: some View {
        VStack(spacing: 18) {
            VStack(spacing: 0) {
                field("电脑地址", text: $host, placeholder: "192.168.1.5",
                      keyboard: .numbersAndPunctuation)
                Divider().overlay(Color.white.opacity(0.08))
                field("APP 端口", text: $port, placeholder: "8767",
                      keyboard: .numberPad)
                Divider().overlay(Color.white.opacity(0.08))
                field("配对令牌（可选）", text: $token,
                      placeholder: "电脑端 authToken",
                      keyboard: .asciiCapable)
            }
            .background(
                RoundedRectangle(cornerRadius: 18, style: .continuous)
                    .fill(Color.white.opacity(0.06))
                    .overlay(RoundedRectangle(cornerRadius: 18, style: .continuous)
                        .stroke(Color.white.opacity(0.10), lineWidth: 1))
            )

            VStack(spacing: 12) {
                Button(action: { connect(nil) }) {
                    HStack {
                        Image(systemName: "bolt.horizontal.circle.fill")
                        Text("连接电脑").fontWeight(.semibold)
                    }
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 15)
                    .background(
                        LinearGradient(colors: [accent, accent.opacity(0.72)],
                                       startPoint: .leading,
                                       endPoint: .trailing))
                    .foregroundStyle(Color(red: 0.02, green: 0.08, blue: 0.12))
                    .clipShape(RoundedRectangle(cornerRadius: 15,
                                                style: .continuous))
                }
                Button {
                    scanning = true
                } label: {
                    Label("扫描电脑二维码", systemImage: "qrcode.viewfinder")
                        .font(.system(size: 15, weight: .medium))
                        .foregroundStyle(.white.opacity(0.85))
                }
            }
        }
    }

    private func field(_ title: String, text: Binding<String>,
                       placeholder: String,
                       keyboard: UIKeyboardType) -> some View {
        HStack(spacing: 14) {
            Text(title)
                .font(.system(size: 14))
                .foregroundStyle(.white.opacity(0.55))
                .frame(width: 116, alignment: .leading)
            ZStack(alignment: .trailing) {
                if text.wrappedValue.isEmpty {
                    Text(placeholder)
                        .font(.system(size: 16, design: .monospaced))
                        .foregroundStyle(.white.opacity(0.8))
                        .lineLimit(1)
                        .allowsHitTesting(false)
                }
                TextField("", text: text)
                    .font(.system(size: 16, design: .monospaced))
                    .foregroundStyle(.white)
                    .tint(accent)
                    .keyboardType(keyboard)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .multilineTextAlignment(.trailing)
            }
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 15)
    }

    @ViewBuilder
    private var recents: some View {
        let list = model.store.recent
        if !list.isEmpty {
            VStack(alignment: .leading, spacing: 10) {
                Text("最近连接")
                    .font(.system(size: 12, weight: .semibold))
                    .tracking(1.2)
                    .foregroundStyle(.white.opacity(0.45))
                ForEach(list, id: \.self) { item in
                    Button {
                        guard let parsed = ConnectionInfo.parse(item) else { return }
                        host = parsed.host
                        port = String(parsed.port)
                        connect(parsed)
                    } label: {
                        HStack {
                            Image(systemName: "wifi")
                                .foregroundStyle(accent)
                            Text(item)
                                .font(.system(size: 15, design: .monospaced))
                                .foregroundStyle(.white.opacity(0.9))
                            Spacer()
                            Image(systemName: "chevron.right")
                                .font(.system(size: 12, weight: .semibold))
                                .foregroundStyle(.white.opacity(0.3))
                        }
                        .padding(.horizontal, 16)
                        .padding(.vertical, 13)
                        .background(RoundedRectangle(cornerRadius: 13)
                            .fill(Color.white.opacity(0.04)))
                    }
                    .buttonStyle(.plain)
                }
            }
        }
    }

    private var footer: some View {
        Text("电脑端需先运行 Cube Setlist Manager，并开启「网页遥控 / APP 页面」。"
             + "手机与电脑要连同一个 Wi-Fi 或电脑热点。")
            .font(.system(size: 12))
            .foregroundStyle(.white.opacity(0.38))
            .lineSpacing(3)
    }

    private func load() {
        if let saved = model.store.connection {
            host = saved.host
            port = String(saved.port)
            token = saved.token
        } else if let first = model.store.recent.first,
                  let parsed = ConnectionInfo.parse(first) {
            host = parsed.host
            port = String(parsed.port)
        }
    }

    private func connect(_ parsed: ConnectionInfo?) {
        error = nil
        let info: ConnectionInfo
        if let parsed {
            info = parsed
        } else {
            let trimmed = host.trimmingCharacters(in: .whitespacesAndNewlines)
            guard !trimmed.isEmpty else {
                error = "先填电脑的局域网地址"
                return
            }
            guard let p = Int(port), (1...65535).contains(p) else {
                error = "端口需要是 1–65535 的数字"
                return
            }
            info = ConnectionInfo(host: trimmed, port: p,
                                  token: token.trimmingCharacters(in: .whitespaces))
        }
        model.connect(info)
    }
}
