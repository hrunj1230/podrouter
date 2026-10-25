import AppKit
import SwiftUI

/// Panel shown from the menu bar icon: pod status, power buttons, window and quit.
struct MenuBarView: View {
    @ObservedObject var appState: AppState
    @ObservedObject var monitor: PodMonitor
    @Environment(\.openWindow) private var openWindow

    @State private var loginEnabled = LoginItem.isEnabled
    @State private var loginError: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text("podrouter").font(.headline)
                Spacer()
                if monitor.isChecking {
                    ProgressView().controlSize(.small)
                }
                Text(lastCheckedText)
                    .font(.caption)
                    .foregroundColor(.secondary)
                Button {
                    appState.refreshSavedPods()
                    monitor.refresh()
                } label: {
                    Image(systemName: "arrow.clockwise")
                }
                .buttonStyle(.borderless)
                .help("상태 새로고침")
                .disabled(monitor.isChecking)
            }

            Divider()

            if appState.runpodKeyStatus != "저장됨" {
                note("Runpod API 키가 없습니다. 창을 열어 [2. 키 관리]에서 저장하세요.")
            } else if let err = monitor.listError {
                note("상태 확인 실패: \(err)")
            }

            let names = monitor.visibleNames()
            if names.isEmpty {
                Text("저장된 Pod 설정이 없습니다.")
                    .font(.callout)
                    .foregroundColor(.secondary)
            } else {
                ForEach(names, id: \.self) { name in
                    row(name)
                }
            }

            if let status = monitor.statusMessage {
                Text(status)
                    .font(.caption)
                    .foregroundColor(monitor.isError ? .red : .secondary)
                    .lineLimit(3)
            }

            Divider()

            Toggle("로그인할 때 자동 실행", isOn: Binding(
                get: { loginEnabled },
                set: { on in
                    loginError = LoginItem.set(on)
                    loginEnabled = LoginItem.isEnabled
                }
            ))
            .toggleStyle(.checkbox)
            .disabled(!LoginItem.isAvailable)

            if LoginItem.needsApproval {
                Button("시스템 설정에서 허용하기") { LoginItem.openSettings() }
                    .buttonStyle(.link)
                    .font(.caption)
            }
            if let err = loginError {
                Text(err).font(.caption).foregroundColor(.red)
            }

            HStack {
                Button("창 열기") { showMainWindow() }
                Spacer()
                Button("종료") { NSApp.terminate(nil) }
            }
        }
        .padding(12)
        .frame(width: 340)
        .onAppear {
            appState.refreshKeyStatus()
            appState.refreshSavedPods()
            loginEnabled = LoginItem.isEnabled
            monitor.refresh()
        }
    }

    private var lastCheckedText: String {
        guard let date = monitor.lastChecked else { return "확인 전" }
        let fmt = DateFormatter()
        fmt.dateFormat = "HH:mm:ss"
        return fmt.string(from: date)
    }

    @ViewBuilder
    private func row(_ name: String) -> some View {
        let rt = monitor.runtimes[name]
        HStack(spacing: 8) {
            VStack(alignment: .leading, spacing: 2) {
                Text(name)
                    .font(.system(.callout, design: .monospaced))
                    .lineLimit(1)
                    .truncationMode(.middle)
                HStack(spacing: 6) {
                    badge(monitor.state(of: name))
                    if let rt = rt {
                        Text("$\(String(format: "%.2f", rt.hourly_price))/시간")
                            .font(.caption2)
                            .foregroundColor(.secondary)
                    }
                }
            }
            Spacer()
            if let rt = rt, rt.healthy, !rt.publicURL.isEmpty {
                Button {
                    NSPasteboard.general.clearContents()
                    NSPasteboard.general.setString(rt.publicURL, forType: .string)
                    monitor.statusMessage = "'\(name)' 주소를 복사했습니다."
                    monitor.isError = false
                } label: {
                    Image(systemName: "doc.on.doc")
                }
                .buttonStyle(.borderless)
                .help("주소 복사")
            }
            if monitor.canPowerOff(name) {
                Button("끄기") { confirmOff(name) }
                    .tint(.red)
            } else {
                Button("켜기") { confirmOn(name) }
                    .tint(.green)
                    .disabled(!monitor.canPowerOn(name))
            }
        }
        .buttonStyle(.borderedProminent)
        .controlSize(.small)
    }

    private func badge(_ st: PowerState) -> some View {
        let (text, color): (String, Color) = {
            switch st {
            case .checking: return ("확인 중", .gray)
            case .unknown: return ("알 수 없음", .gray)
            case .off: return ("꺼짐", .secondary)
            case .starting: return ("시작 중", .orange)
            case .ready: return ("켜짐 · 정상", .green)
            case .busy(let label): return (label, .blue)
            }
        }()
        return HStack(spacing: 4) {
            Circle().fill(color).frame(width: 7, height: 7)
            Text(text).font(.caption2)
        }
    }

    private func note(_ text: String) -> some View {
        Text(text)
            .font(.caption)
            .foregroundColor(.orange)
            .fixedSize(horizontal: false, vertical: true)
    }

    private func confirmOn(_ name: String) {
        let alert = NSAlert()
        alert.messageText = "시간당 GPU 요금이 청구되기 시작합니다."
        alert.informativeText = "'\(name)' 설정으로 RunPod에 GPU Pod를 만들고 시간당 요금이 청구됩니다."
        alert.addButton(withTitle: "켜기")
        alert.addButton(withTitle: "취소")
        NSApp.activate(ignoringOtherApps: true)
        if alert.runModal() == .alertFirstButtonReturn {
            monitor.powerOn(name)
        }
    }

    private func confirmOff(_ name: String) {
        let alert = NSAlert()
        alert.messageText = "Pod를 종료하시겠습니까?"
        alert.informativeText = "'\(name)' Pod와 그 안의 컨테이너가 영구 삭제됩니다."
        alert.alertStyle = .warning
        alert.addButton(withTitle: "종료")
        alert.addButton(withTitle: "취소")
        NSApp.activate(ignoringOtherApps: true)
        if alert.runModal() == .alertFirstButtonReturn {
            monitor.powerOff(name)
        }
    }

    private func showMainWindow() {
        NSApp.setActivationPolicy(.regular)
        openWindow(id: PodrouterApp.mainWindowID)
        NSApp.activate(ignoringOtherApps: true)
    }
}
