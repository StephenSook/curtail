import SwiftUI
import WebKit

/// The iOS wrapper around the field surface.
///
/// **What this is honestly for: distribution, not capability.** The same route installs
/// from Safari with Add to Home Screen and behaves identically. This exists so the app
/// can go through TestFlight, which is a distribution channel the web cannot reach, and
/// nothing here does anything the installed PWA cannot.
///
/// Two things it deliberately does NOT do, both of which are the reasons a wrapper
/// usually gets rejected under guideline 4.2 and both of which would be wrong anyway:
///
/// - It does not re-implement the field loop natively. There would then be two
///   implementations of an evidence path that must agree exactly, and they would
///   diverge, which is the whole argument for one codebase in the first place.
/// - It does not hold credentials, tokens or signing material. Hard rule 14: the device
///   authenticates a human and submits append-only evidence. The phone says yes, the
///   server acts. A native shell does not change who holds authority.
@main
struct CurtailFieldApp: App {
    var body: some Scene {
        WindowGroup {
            FieldView()
                .ignoresSafeArea(edges: .bottom)
                .preferredColorScheme(.light)  // the field surface is light-first, for sun
        }
    }
}

/// The one URL this app exists to open.
///
/// Not configurable at runtime on purpose. An app that can be pointed at an arbitrary
/// origin is an app whose Digital Asset Links verification means nothing, and this one
/// is verified against exactly one host.
private let fieldURL = URL(string: "https://curtail-console-api-672785135387.us-central1.run.app/field")!

struct FieldView: UIViewRepresentable {
    func makeCoordinator() -> Coordinator { Coordinator() }

    func makeUIView(context: Context) -> WKWebView {
        let configuration = WKWebViewConfiguration()
        // A persistent store, so the offline queue in IndexedDB survives app restarts.
        // The default is already persistent; stated explicitly because a non-persistent
        // store here would silently discard measurements somebody waded into a river for.
        configuration.websiteDataStore = .default()
        configuration.allowsInlineMediaPlayback = true
        configuration.mediaTypesRequiringUserActionForPlayback = []

        let webView = WKWebView(frame: .zero, configuration: configuration)
        webView.navigationDelegate = context.coordinator
        webView.allowsBackForwardNavigationGestures = false
        // No bounce. This is a form, not a document, and rubber-banding on a control
        // surface reads as a web page in a costume.
        webView.scrollView.bounces = false
        webView.isOpaque = false
        webView.backgroundColor = UIColor(red: 0.969, green: 0.969, blue: 0.961, alpha: 1)
        webView.load(URLRequest(url: fieldURL))
        return webView
    }

    func updateUIView(_ webView: WKWebView, context: Context) {}

    final class Coordinator: NSObject, WKNavigationDelegate {
        /// Keep navigation inside the one origin this app is verified against.
        ///
        /// Anything else opens in Safari rather than inside the shell. A wrapper that
        /// will render any URL is a browser, and a browser that looks like our app is a
        /// phishing surface pointed at people who sign legal orders.
        func webView(
            _ webView: WKWebView,
            decidePolicyFor navigationAction: WKNavigationAction,
            decisionHandler: @escaping (WKNavigationActionPolicy) -> Void
        ) {
            guard let url = navigationAction.request.url else {
                decisionHandler(.cancel)
                return
            }
            if url.host == fieldURL.host {
                decisionHandler(.allow)
            } else {
                UIApplication.shared.open(url)
                decisionHandler(.cancel)
            }
        }

        /// A failed load must say so rather than showing a blank white screen.
        ///
        /// A watermaster out of signal needs to know the app did not reach the service,
        /// not to stare at nothing and guess. The installed PWA gets the same treatment
        /// from its service worker; this is the shell's equivalent.
        func webView(
            _ webView: WKWebView,
            didFail navigation: WKNavigation!,
            withError error: Error
        ) {
            showFailure(in: webView, message: error.localizedDescription)
        }

        func webView(
            _ webView: WKWebView,
            didFailProvisionalNavigation navigation: WKNavigation!,
            withError error: Error
        ) {
            showFailure(in: webView, message: error.localizedDescription)
        }

        private func showFailure(in webView: WKWebView, message: String) {
            let escaped = message
                .replacingOccurrences(of: "&", with: "&amp;")
                .replacingOccurrences(of: "<", with: "&lt;")
            let html = """
            <!doctype html><meta name="viewport" content="width=device-width,initial-scale=1">
            <body style="margin:0;padding:32px;background:#F7F7F5;color:#16181D;
                         font:17px/1.5 -apple-system,system-ui,sans-serif">
              <h1 style="font-size:22px;margin:0 0 12px">Could not reach the service</h1>
              <p style="color:#5A5F6A;margin:0 0 16px">
                This is a connection problem, not an empty ledger. Nothing you saved on
                this device has been lost, and it will send when you are back in signal.
              </p>
              <p style="color:#5A5F6A;font-size:14px;margin:0"><code>\(escaped)</code></p>
            </body>
            """
            webView.loadHTMLString(html, baseURL: nil)
        }
    }
}
