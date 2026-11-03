import SwiftUI

struct RemoteView: View {
    @EnvironmentObject private var model: AppModel
    @State private var showSettings = false

    var body: some View {
        ZStack(alignment: .bottomTrailing) {
            Color.black.ignoresSafeArea()

            if let url = model.connection?.pageURL {
                WKWebViewHost(url: url, model: model)
                    .ignoresSafeArea()
            }

            if !model.pageLoaded && model.webError == nil {
                ProgressView()
                    .tint(.white)
                    .scaleEffect(1.2)
            }

            Menu {
                Button {
                    showSettings = true
                } label: {
                    Label("设置", systemImage: "gearshape")
                }
                Button {
                    model.retryPage()
                } label: {
                    Label("重新加载", systemImage: "arrow.clockwise")
                }
                Divider()
                Button(role: .destructive) {
                    model.disconnect()
                } label: {
                    Label("断开连接", systemImage: "wifi.slash")
                }
            } label: {
                Image(systemName: "ellipsis.circle.fill")
                    .font(.system(size: 30))
                    .symbolRenderingMode(.palette)
                    .foregroundStyle(.white, .black.opacity(0.55))
                    .padding(14)
            }
        }
        .sheet(isPresented: $showSettings) {
            SettingsView()
        }
        .overlay(alignment: .top) {
            if let message = model.webError {
                errorBanner(message)
            }
        }
    }

    private func errorBanner(_ message: String) -> some View {
        HStack(spacing: 12) {
            Image(systemName: "exclamationmark.triangle.fill")
                .foregroundStyle(.yellow)
            VStack(alignment: .leading, spacing: 2) {
                Text("连不上电脑页面").font(.system(size: 14, weight: .semibold))
                Text(message)
                    .font(.system(size: 11))
                    .foregroundStyle(.white.opacity(0.65))
                    .lineLimit(2)
            }
            Spacer()
            Button("重试") { model.retryPage() }
                .font(.system(size: 13, weight: .semibold))
        }
        .padding(14)
        .background(.ultraThinMaterial)
        .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
        .padding(.horizontal, 14)
        .padding(.top, 8)
    }
}
