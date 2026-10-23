import AppKit
import SwiftUI

/// `swift run`처럼 .app 번들 없이 실행하면 키보드 입력이 앱으로 오지 않는다.
/// 일반 앱으로 등록하고 앞으로 가져와 텍스트 필드가 입력을 받게 한다.
final class AppDelegate: NSObject, NSApplicationDelegate {
    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.regular)
        // A bundled .app gets its icon from Info.plist. A bare `swift run`
        // binary has none, so load it from the project folder.
        if Bundle.main.bundleIdentifier == nil {
            let repo = RepoLocator.resolve()
            let png = URL(fileURLWithPath: repo).appendingPathComponent("apps/podrouter/Resources/AppIcon.png")
            if !repo.isEmpty, let image = NSImage(contentsOf: png) {
                NSApp.applicationIconImage = image
            }
        }
        NSApp.activate(ignoringOtherApps: true)
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        true
    }
}

@main
struct PodrouterApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate

    var body: some Scene {
        WindowGroup {
            ContentView()
        }
    }
}
