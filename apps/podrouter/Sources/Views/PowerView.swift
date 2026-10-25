import AppKit
import Combine
import SwiftUI

struct PowerView: View {
    @ObservedObject var appState: AppState
    @EnvironmentObject private var monitor: PodMonitor

    @State private var query: String = ""
    @State private var pendingOn: String?
    @State private var pendingOff: String?

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                Text("4. 전원 제어 (Power)")
                    .font(.title2)
                    .bold()

                Text("저장된 Pod 설정을 골라 켜거나 끕니다. 상태는 15초마다 헬스 체크로 확인하며, 켜져 있으면 켜기 버튼이, 꺼져 있으면 끄기 버튼이 잠깁니다.")
                    .font(.body)
                    .foregroundColor(.secondary)

                if appState.runpodKeyStatus != "저장됨" {
                    warning(".env에 RUNPOD_API_KEY가 없습니다. [2. 키 관리]에서 먼저 저장하세요.")
                }

                GroupBox(label: Label("Pod 목록", systemImage: "server.rack")) {
                    VStack(alignment: .leading, spacing: 10) {
                        HStack {
                            TextField("이름으로 검색", text: $query)
                                .textFieldStyle(.roundedBorder)
                                .frame(maxWidth: 260)
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
                            .help("상태 새로고침")
                            .disabled(monitor.isChecking)
                        }

                        if let err = monitor.listError {
                            Text("상태 확인 실패: \(err)")
                                .font(.caption)
                                .foregroundColor(.red)
                        }

                        Divider()

                        if visibleNames.isEmpty {
                            Text(query.isEmpty
                                 ? "저장된 Pod 설정이 없습니다. [3. Pod 설정]에서 YAML을 먼저 만드세요."
                                 : "'\(query)'와 일치하는 Pod 설정이 없습니다.")
                                .font(.callout)
                                .foregroundColor(.secondary)
                                .padding(.vertical, 6)
                        } else {
                            ForEach(visibleNames, id: \.self) { name in
                                podRow(name)
                                if name != visibleNames.last {
                                    Divider()
                                }
                            }
                        }
                    }
                    .padding(8)
                }

                if let status = monitor.statusMessage {
                    HStack {
                        Image(systemName: monitor.isError ? "exclamationmark.circle.fill" : "checkmark.circle.fill")
                        Text(status)
                            .font(.subheadline)
                    }
                    .foregroundColor(monitor.isError ? .red : .green)
                    .padding(8)
                    .background((monitor.isError ? Color.red : Color.green).opacity(0.1))
                    .cornerRadius(8)
                }

                GroupBox(label: Label("마지막 명령과 응답 (키 마스킹 적용)", systemImage: "terminal")) {
                    VStack(alignment: .leading, spacing: 6) {
                        if let run = monitor.lastRun {
                            HStack(spacing: 8) {
                                Text("\(run.pod) \(run.label)")
                                    .font(.caption)
                                    .fontWeight(.medium)
                                Text(RunStore.timeText(run.started))
                                    .font(.caption)
                                    .foregroundColor(.secondary)
                                if run.recorded {
                                    Text(run.exitCode == 0 ? "완료" : "실패 (종료 코드 \(run.exitCode ?? -1))")
                                        .font(.caption2)
                                        .foregroundColor(run.exitCode == 0 ? .green : .red)
                                } else {
                                    ProgressView().controlSize(.mini)
                                    Text("진행 중 · 앱을 꺼도 계속됩니다")
                                        .font(.caption2)
                                        .foregroundColor(.blue)
                                }
                                Spacer()
                                Button("로그 폴더") {
                                    NSWorkspace.shared.open(RunStore.directoryURL)
                                }
                                .buttonStyle(.borderless)
                                .font(.caption)
                            }
                        }
                        ScrollViewReader { proxy in
                            ScrollView {
                                Text(monitor.lastRunText.isEmpty ? "켜기·끄기를 누르면 보낸 명령과 응답이 여기에 실시간으로 표시됩니다." : monitor.lastRunText)
                                    .font(.system(.caption, design: .monospaced))
                                    .foregroundColor(monitor.lastRunText.isEmpty ? .secondary : .primary)
                                    .frame(maxWidth: .infinity, alignment: .leading)
                                    .padding(4)
                                    .textSelection(.enabled)
                                Color.clear.frame(height: 1).id("end")
                            }
                            .frame(minHeight: 160, maxHeight: 320)
                            .onChange(of: monitor.lastRunText) { _ in
                                proxy.scrollTo("end", anchor: .bottom)
                            }
                        }
                    }
                }

                Spacer()
            }
            .padding()
            .onAppear {
                appState.refreshKeyStatus()
                appState.refreshSavedPods()
                monitor.refresh()
            }
            .confirmationDialog(
                "시간당 GPU 요금이 청구되기 시작합니다.",
                isPresented: Binding(get: { pendingOn != nil }, set: { if !$0 { pendingOn = nil } }),
                titleVisibility: .visible,
                presenting: pendingOn
            ) { name in
                Button("요금 청구에 동의하고 '\(name)' 켜기") { monitor.powerOn(name) }
                Button("취소", role: .cancel) {}
            } message: { name in
                Text("'\(name)' 설정으로 RunPod에 GPU Pod를 만들고 시간당 요금이 청구됩니다.")
            }
            .confirmationDialog(
                "Pod를 종료하시겠습니까?",
                isPresented: Binding(get: { pendingOff != nil }, set: { if !$0 { pendingOff = nil } }),
                titleVisibility: .visible,
                presenting: pendingOff
            ) { name in
                Button("'\(name)' 종료", role: .destructive) { monitor.powerOff(name) }
                Button("취소", role: .cancel) {}
            } message: { name in
                Text("'\(name)' Pod와 그 안의 컨테이너가 영구 삭제됩니다.")
            }
        }
    }

    // MARK: - Rows

    private var visibleNames: [String] { monitor.visibleNames(query: query) }

    private var lastCheckedText: String {
        guard let date = monitor.lastChecked else { return "확인 전" }
        let fmt = DateFormatter()
        fmt.dateFormat = "HH:mm:ss"
        return "마지막 확인 \(fmt.string(from: date))"
    }

    @ViewBuilder
    private func podRow(_ name: String) -> some View {
        let st = monitor.state(of: name)
        let rt = monitor.runtimes[name]
        let hasYaml = appState.savedPodNames.contains(name)
        let canOn = monitor.canPowerOn(name)
        let canOff = monitor.canPowerOff(name)

        HStack(alignment: .center, spacing: 12) {
            VStack(alignment: .leading, spacing: 4) {
                HStack(spacing: 8) {
                    Text(name)
                        .font(.system(.body, design: .monospaced))
                        .fontWeight(.medium)
                    badge(st)
                    if !hasYaml {
                        Text("YAML 없음")
                            .font(.caption2)
                            .foregroundColor(.orange)
                    }
                }
                if let rt = rt {
                    HStack(spacing: 10) {
                        Text("$\(String(format: "%.2f", rt.hourly_price))/시간")
                            .font(.caption)
                            .foregroundColor(.secondary)
                        if rt.healthy, let url = URL(string: rt.publicURL), !rt.publicURL.isEmpty {
                            Link(rt.publicURL, destination: url)
                                .font(.caption)
                            Button {
                                NSPasteboard.general.clearContents()
                                NSPasteboard.general.setString(rt.publicURL, forType: .string)
                            } label: {
                                Image(systemName: "doc.on.doc")
                            }
                            .buttonStyle(.borderless)
                            .help("주소 복사")
                            if appState.modelKeyStatus == "저장됨" {
                                Button {
                                    if let key = SecretStore.loadKeyForChildProcess(account: SecretStore.modelApiAccount) {
                                        NSPasteboard.general.clearContents()
                                        NSPasteboard.general.setString(key, forType: .string)
                                        monitor.statusMessage = "모델 API 키를 클립보드에 복사했습니다."
                                        monitor.isError = false
                                    }
                                } label: {
                                    Label("API 키", systemImage: "key")
                                        .font(.caption)
                                }
                                .buttonStyle(.borderless)
                                .help("모델 API 키 복사 (Authorization: Bearer <키>)")
                            } else {
                                Label("키 없음 · 누구나 접근 가능", systemImage: "lock.open")
                                    .font(.caption2)
                                    .foregroundColor(.orange)
                            }
                        }
                    }
                }
            }

            Spacer()

            Button { pendingOn = name } label: {
                Label("켜기", systemImage: "play.fill")
            }
            .buttonStyle(.borderedProminent)
            .tint(.green)
            .disabled(!canOn)

            Button { pendingOff = name } label: {
                Label("끄기", systemImage: "stop.fill")
            }
            .buttonStyle(.borderedProminent)
            .tint(.red)
            .disabled(!canOff)
        }
        .padding(.vertical, 4)
    }

    @ViewBuilder
    private func badge(_ st: PowerState) -> some View {
        let (text, color): (String, Color) = {
            switch st {
            case .checking: return ("확인 중", .gray)
            case .unknown: return ("알 수 없음", .gray)
            case .off: return ("꺼짐", .secondary)
            case .starting(let s): return (s == "RUNNING" ? "시작 중 (모델 로딩)" : "시작 중 (\(s))", .orange)
            case .ready: return ("켜짐 · 정상", .green)
            case .busy(let label): return (label, .blue)
            }
        }()
        HStack(spacing: 4) {
            Circle().fill(color).frame(width: 8, height: 8)
            Text(text).font(.caption)
        }
        .padding(.horizontal, 8)
        .padding(.vertical, 2)
        .background(color.opacity(0.12))
        .cornerRadius(6)
    }

    private func warning(_ text: String) -> some View {
        HStack {
            Image(systemName: "exclamationmark.triangle.fill").foregroundColor(.orange)
            Text(text).font(.caption)
        }
        .padding(8)
        .background(Color.orange.opacity(0.1))
        .cornerRadius(8)
    }
}
