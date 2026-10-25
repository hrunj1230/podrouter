import AppKit
import SwiftUI

/// `swift run`처럼 .app 번들 없이 실행하면 키보드 입력이 앱으로 오지 않는다.
/// 일반 앱으로 등록하고 앞으로 가져와 텍스트 필드가 입력을 받게 한다.
/// 창을 닫아도 앱은 상단바에 남고, 창이 없으면 Dock 아이콘을 숨긴다.
final class AppDelegate: NSObject, NSApplicationDelegate {
    func applicationDidFinishLaunching(_ notification: Notification) {
        // A bundled .app gets its icon from Info.plist. A bare `swift run`
        // binary has none, so load it from the project folder.
        if Bundle.main.bundleIdentifier == nil {
            let repo = RepoLocator.resolve()
            let png = URL(fileURLWithPath: repo).appendingPathComponent("apps/podrouter/Resources/AppIcon.png")
            if !repo.isEmpty, let image = NSImage(contentsOf: png) {
                NSApp.applicationIconImage = image
            }
        }

        let center = NotificationCenter.default
        center.addObserver(self, selector: #selector(windowWillClose(_:)), name: NSWindow.willCloseNotification, object: nil)
        center.addObserver(self, selector: #selector(windowDidBecomeKey(_:)), name: NSWindow.didBecomeKeyNotification, object: nil)

        if LoginItem.launchedAtLogin {
            // Started at login: stay in the menu bar only.
            NSApp.setActivationPolicy(.accessory)
            DispatchQueue.main.async {
                for window in NSApp.windows where Self.isMainWindow(window) {
                    window.close()
                }
            }
        } else {
            NSApp.setActivationPolicy(.regular)
            NSApp.activate(ignoringOtherApps: true)
        }
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        false
    }

    /// Opening the app again from Finder or Spotlight brings the window back.
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        NSApp.setActivationPolicy(.regular)
        return true
    }

    /// Titled windows are the app window; the menu bar panel has no title bar.
    static func isMainWindow(_ window: NSWindow) -> Bool {
        window.styleMask.contains(.titled) && !(window is NSPanel)
    }

    @objc private func windowWillClose(_ note: Notification) {
        guard let closing = note.object as? NSWindow, Self.isMainWindow(closing) else { return }
        let others = NSApp.windows.filter { $0 !== closing && $0.isVisible && Self.isMainWindow($0) }
        if others.isEmpty {
            NSApp.setActivationPolicy(.accessory)
        }
    }

    @objc private func windowDidBecomeKey(_ note: Notification) {
        guard let window = note.object as? NSWindow, Self.isMainWindow(window) else { return }
        if NSApp.activationPolicy() != .regular {
            NSApp.setActivationPolicy(.regular)
        }
    }
}

@main
struct PodrouterApp: App {
    static let mainWindowID = "main"

    @NSApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate
    @StateObject private var appState: AppState
    @StateObject private var monitor: PodMonitor

    init() {
        let state = AppState()
        _appState = StateObject(wrappedValue: state)
        _monitor = StateObject(wrappedValue: PodMonitor(appState: state))
    }

    var body: some Scene {
        Window("podrouter", id: Self.mainWindowID) {
            ContentView(appState: appState)
                .environmentObject(monitor)
        }

        MenuBarExtra {
            MenuBarView(appState: appState, monitor: monitor)
        } label: {
            Image(systemName: monitor.menuBarIcon)
        }
        .menuBarExtraStyle(.window)
    }
}
