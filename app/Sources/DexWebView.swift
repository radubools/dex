import SwiftUI
import WebKit

/// dex's own page in a web view. The server serves the UI and the API from
/// one origin, so this is a browser tab with no address bar: sign-in cookies
/// persist in the default website data store, the event stream and uploads
/// work as they do in Safari, and links to other sites open in the browser.
struct DexWebView {
    let url: URL
    @EnvironmentObject private var page: Page

    func makeCoordinator() -> Coordinator { Coordinator(url: url, page: page) }

    fileprivate func makeWebView(_ coordinator: Coordinator) -> WKWebView {
        let config = WKWebViewConfiguration()
        config.websiteDataStore = .default()
        #if os(iOS)
        config.allowsInlineMediaPlayback = true
        #endif
        config.mediaTypesRequiringUserActionForPlayback = []
        let web = WKWebView(frame: .zero, configuration: config)
        web.navigationDelegate = coordinator
        web.uiDelegate = coordinator
        web.allowsBackForwardNavigationGestures = true
        #if os(iOS)
        // dex's UI fills the screen, so the way back to the server list is a
        // gesture rather than a bar: a two-finger double tap.
        let tap = UITapGestureRecognizer(target: coordinator, action: #selector(Coordinator.chooseServer))
        tap.numberOfTouchesRequired = 2
        tap.numberOfTapsRequired = 2
        tap.delegate = coordinator
        web.addGestureRecognizer(tap)
        let refresh = UIRefreshControl()
        refresh.addTarget(coordinator, action: #selector(Coordinator.pullToReload(_:)), for: .valueChanged)
        web.scrollView.refreshControl = refresh
        #endif
        coordinator.web = web
        page.reload = { [weak web, url] in
            if web?.url == nil { web?.load(URLRequest(url: url)) } else { web?.reload() }
        }
        web.load(URLRequest(url: url))
        return web
    }

    final class Coordinator: NSObject, WKNavigationDelegate, WKUIDelegate {
        let home: URL
        let page: Page
        weak var web: WKWebView?

        init(url: URL, page: Page) {
            self.home = url
            self.page = page
        }

        // Pages on the dex server stay here; anything else is the browser's.
        func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction,
                     decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
            guard let url = action.request.url, action.navigationType == .linkActivated,
                  url.host != home.host || url.port != home.port,
                  ["http", "https"].contains(url.scheme ?? "") else {
                decisionHandler(.allow)
                return
            }
            decisionHandler(.cancel)
            Self.openExternally(url)
        }

        // `target="_blank"`: the same rule, rather than a new web view.
        func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
                     for action: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
            if let url = action.request.url {
                if url.host == home.host && url.port == home.port { webView.load(action.request) }
                else { Self.openExternally(url) }
            }
            return nil
        }

        func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
            Task { @MainActor in page.error = nil }
            #if os(iOS)
            webView.scrollView.refreshControl?.endRefreshing()
            #endif
        }

        func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
            failed(error)
        }

        func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
            failed(error)
        }

        private func failed(_ error: Error) {
            // A navigation replaced by another (a quick second click) is not a failure.
            if (error as NSError).code == NSURLErrorCancelled { return }
            Task { @MainActor in page.error = error.localizedDescription }
            #if os(iOS)
            web?.scrollView.refreshControl?.endRefreshing()
            #endif
        }

        func webView(_ webView: WKWebView, runJavaScriptAlertPanelWithMessage message: String,
                     initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping () -> Void) {
            Self.alert(message, buttons: ["OK"]) { _ in completionHandler() }
        }

        func webView(_ webView: WKWebView, runJavaScriptConfirmPanelWithMessage message: String,
                     initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping (Bool) -> Void) {
            Self.alert(message, buttons: ["OK", "Cancel"]) { completionHandler($0 == 0) }
        }

        #if os(macOS)
        // <input type="file">: iOS shows its own picker; macOS asks the delegate.
        func webView(_ webView: WKWebView, runOpenPanelWith parameters: WKOpenPanelParameters,
                     initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping ([URL]?) -> Void) {
            let panel = NSOpenPanel()
            panel.allowsMultipleSelection = parameters.allowsMultipleSelection
            panel.canChooseDirectories = parameters.allowsDirectories
            panel.canChooseFiles = true
            panel.begin { completionHandler($0 == .OK ? panel.urls : nil) }
        }
        #endif

        #if os(iOS)
        @objc func chooseServer() { Task { @MainActor in page.choosing = true } }
        @objc func pullToReload(_ control: UIRefreshControl) { web?.reload() }
        #endif

        static func openExternally(_ url: URL) {
            #if os(macOS)
            NSWorkspace.shared.open(url)
            #else
            UIApplication.shared.open(url)
            #endif
        }

        static func alert(_ message: String, buttons: [String], done: @escaping (Int) -> Void) {
            #if os(macOS)
            let alert = NSAlert()
            alert.messageText = message
            buttons.forEach { alert.addButton(withTitle: $0) }
            done(alert.runModal().rawValue - NSApplication.ModalResponse.alertFirstButtonReturn.rawValue)
            #else
            let alert = UIAlertController(title: nil, message: message, preferredStyle: .alert)
            for (i, title) in buttons.enumerated() {
                alert.addAction(UIAlertAction(title: title, style: i == 1 ? .cancel : .default) { _ in done(i) })
            }
            let scene = UIApplication.shared.connectedScenes.first as? UIWindowScene
            var top = scene?.keyWindow?.rootViewController
            while let next = top?.presentedViewController { top = next }
            if let top { top.present(alert, animated: true) } else { done(buttons.count - 1) }
            #endif
        }
    }
}

#if os(iOS)
extension DexWebView.Coordinator: UIGestureRecognizerDelegate {
    // The page keeps its own taps and scrolling.
    func gestureRecognizer(_ g: UIGestureRecognizer,
                           shouldRecognizeSimultaneouslyWith other: UIGestureRecognizer) -> Bool { true }
}

extension DexWebView: UIViewRepresentable {
    func makeUIView(context: Context) -> WKWebView { makeWebView(context.coordinator) }
    func updateUIView(_ web: WKWebView, context: Context) {}
}
#else
extension DexWebView: NSViewRepresentable {
    func makeNSView(context: Context) -> WKWebView { makeWebView(context.coordinator) }
    func updateNSView(_ web: WKWebView, context: Context) {}
}
#endif
