import SwiftUI
import WebKit
import CubeRemoteCore

/// WKWebView 外壳：document-start 注入同步桥 window.CubeApp。
struct WKWebViewHost: UIViewRepresentable {
    let url: URL
    @ObservedObject var model: AppModel

    func makeCoordinator() -> Coordinator { Coordinator(model: model) }

    func makeUIView(context: Context) -> WKWebView {
        let config = WKWebViewConfiguration()
        config.allowsInlineMediaPlayback = true
        config.mediaTypesRequiringUserActionForPlayback = []
        let controller = WKUserContentController()
        controller.addUserScript(WKUserScript(
            source: WebBridge.userScript(state: model.bridgeState),
            injectionTime: .atDocumentStart,
            forMainFrameOnly: true))
        controller.add(context.coordinator, name: "cube")
        config.userContentController = controller

        let webView = WKWebView(frame: .zero, configuration: config)
        webView.navigationDelegate = context.coordinator
        webView.uiDelegate = context.coordinator
        webView.allowsBackForwardNavigationGestures = false
        webView.scrollView.contentInsetAdjustmentBehavior = .never
        webView.isOpaque = false
        webView.backgroundColor = .clear
        webView.scrollView.backgroundColor = .clear
        model.attach(webView: webView)
        webView.load(URLRequest(url: url))
        return webView
    }

    func updateUIView(_ webView: WKWebView, context: Context) {
        if webView.url?.absoluteString != url.absoluteString {
            webView.load(URLRequest(url: url))
        }
        context.coordinator.push(state: model.bridgeState)
    }

    final class Coordinator: NSObject, WKNavigationDelegate, WKUIDelegate,
                             WKScriptMessageHandler {
        private let model: AppModel
        private weak var webView: WKWebView?

        init(model: AppModel) { self.model = model }

        func webView(_ webView: WKWebView,
                     didFinish navigation: WKNavigation!) {
            self.webView = webView
            Task { @MainActor in model.pageDidLoad() }
        }

        func webView(_ webView: WKWebView,
                     didFail navigation: WKNavigation!, withError error: Error) {
            Task { @MainActor in
                model.pageFailed(error.localizedDescription)
            }
        }

        func webView(_ webView: WKWebView,
                     didFailProvisionalNavigation navigation: WKNavigation!,
                     withError error: Error) {
            Task { @MainActor in
                model.pageFailed(error.localizedDescription)
            }
        }

        /// 只允许停留在当前电脑页面；其它链接交给系统。
        func webView(_ webView: WKWebView,
                     decidePolicyFor navigationAction: WKNavigationAction,
                     decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
            guard let target = navigationAction.request.url else {
                decisionHandler(.cancel); return
            }
            if target.scheme == "http" || target.scheme == "https",
               target.host == model.connection?.host {
                decisionHandler(.allow)
            } else if navigationAction.navigationType == .linkActivated {
                UIApplication.shared.open(target)
                decisionHandler(.cancel)
            } else {
                decisionHandler(.allow)
            }
        }

        func userContentController(_ userContentController: WKUserContentController,
                                   didReceive message: WKScriptMessage) {
            guard message.name == "cube",
                  let body = message.body as? [String: Any],
                  let cmd = body["cmd"] as? String else { return }
            Task { @MainActor in model.handleBridge(cmd: cmd, body: body) }
        }

        func push(state: BridgeState) {
            guard let webView else { return }
            let js = "window.__cubeApply && window.__cubeApply(\(state.jsonString))"
            webView.evaluateJavaScript(js, completionHandler: nil)
        }
    }
}
