import AppKit
import Combine
import SwiftUI

/// One managed pod as reported by `podrouter ls --json`.
struct PodRuntime: Decodable {
    let name: String
    let pod_id: String
    let state: String
    let hourly_price: Double
    let port: Int
    let url: String
    let healthy: Bool
    let tunnel_url: String?

    /// Public tunnel address when known, otherwise the Runpod proxy address.
    var publicURL: String {
        if let t = tunnel_url, !t.isEmpty { return t }
        return url
    }
}

struct PodListPayload: Decodable {
    let pods: [PodRuntime]
}

struct PowerView: View {
    @ObservedObject var appState: AppState

    @State private var query: String = ""
    @State private var runtimes: [String: PodRuntime] = [:]
    @State private var lastChecked: Date?
    @State private var isChecking: Bool = false
    @State private var listError: String?
    /// Pod name -> "켜는 중" / "끄는 중" while a command runs.
    @State private var busy: [String: String] = [:]
    @State private var pendingOn: String?
    @State private var pendingOff: String?
    @State private var outputLog: String = ""
    @State private var statusMessage: String?
    @State private var isError: Bool = false

    private let refreshTimer = Timer.publish(every: 15, on: .main, in: .common).autoconnect()

    private enum PowerState {
        case checking, off, starting(String), ready, busy(String), unknown
    }

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
                            if isChecking {
                                ProgressView().controlSize(.small)
                            }
                            Text(lastCheckedText)
                                .font(.caption)
                                .foregroundColor(.secondary)
                            Button {
                                appState.refreshSavedPods()
                                refreshStatus()
                            } label: {
                                Image(systemName: "arrow.clockwise")
                            }
                            .help("상태 새로고침")
                            .disabled(isChecking)
                        }

                        if let err = listError {
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

                if let status = statusMessage {
                    HStack {
                        Image(systemName: isError ? "exclamationmark.circle.fill" : "checkmark.circle.fill")
                        Text(status)
                            .font(.subheadline)
                    }
                    .foregroundColor(isError ? .red : .green)
                    .padding(8)
                    .background((isError ? Color.red : Color.green).opacity(0.1))
                    .cornerRadius(8)
                }

                GroupBox(label: Label("명령어 실행 출력 (키 마스킹 적용)", systemImage: "terminal")) {
                    ScrollView {
                        Text(outputLog.isEmpty ? "실행 결과가 여기에 표시됩니다." : outputLog)
                            .font(.system(.caption, design: .monospaced))
                            .foregroundColor(outputLog.isEmpty ? .secondary : .primary)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .padding(4)
                            .textSelection(.enabled)
                    }
                    .frame(minHeight: 120, maxHeight: 220)
                }

                Spacer()
            }
            .padding()
            .onAppear {
                appState.refreshKeyStatus()
                appState.refreshSavedPods()
                refreshStatus()
            }
            .onReceive(refreshTimer) { _ in
                refreshStatus()
            }
            .confirmationDialog(
                "시간당 GPU 요금이 청구되기 시작합니다.",
                isPresented: Binding(get: { pendingOn != nil }, set: { if !$0 { pendingOn = nil } }),
                titleVisibility: .visible,
                presenting: pendingOn
            ) { name in
                Button("요금 청구에 동의하고 '\(name)' 켜기") { powerOn(name) }
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
                Button("'\(name)' 종료", role: .destructive) { powerOff(name) }
                Button("취소", role: .cancel) {}
            } message: { name in
                Text("'\(name)' Pod와 그 안의 컨테이너가 영구 삭제됩니다.")
            }
        }
    }

    // MARK: - Rows

    /// Saved YAML names plus running pods without a YAML, minus protected names.
    private var visibleNames: [String] {
        var names = appState.savedPodNames
        for name in runtimes.keys.sorted() where !names.contains(name) {
            names.append(name)
        }
        names.removeAll { appState.isProtected($0) }
        let q = query.trimmingCharacters(in: .whitespaces).lowercased()
        return q.isEmpty ? names : names.filter { $0.lowercased().contains(q) }
    }

    private var lastCheckedText: String {
        guard let date = lastChecked else { return "확인 전" }
        let fmt = DateFormatter()
        fmt.dateFormat = "HH:mm:ss"
        return "마지막 확인 \(fmt.string(from: date))"
    }

    private func state(of name: String) -> PowerState {
        if let label = busy[name] { return .busy(label) }
        if listError != nil && lastChecked == nil { return .unknown }
        guard lastChecked != nil else { return .checking }
        guard let rt = runtimes[name] else { return .off }
        if rt.healthy { return .ready }
        return .starting(rt.state)
    }

    @ViewBuilder
    private func podRow(_ name: String) -> some View {
        let st = state(of: name)
        let rt = runtimes[name]
        let hasYaml = appState.savedPodNames.contains(name)
        let isBusy = busy[name] != nil
        let canOn: Bool = {
            if case .off = st { return hasYaml && appState.runpodKeyStatus == "저장됨" }
            return false
        }()
        let canOff = rt != nil && !isBusy

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
                                        statusMessage = "모델 API 키를 클립보드에 복사했습니다."
                                        isError = false
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

    // MARK: - Commands

    private func gpuUpCommand(_ tail: [String]) -> (URL, [String]) {
        var args: [String] = appState.isGpuUpDirectExecutable ? [] : ["-m", "podrouter"]
        args.append(contentsOf: tail)
        return (URL(fileURLWithPath: appState.gpuUpCLIPath), args)
    }

    private func refreshStatus() {
        guard !isChecking, appState.runpodKeyStatus == "저장됨" else { return }
        let (exec, args) = gpuUpCommand(["ls", "--json", "--api-key", "${RUNPOD_API_KEY}"])
        let repo = URL(fileURLWithPath: appState.dataRootPath)
        isChecking = true
        DispatchQueue.global(qos: .utility).async {
            let result = ProcessRunner.run(executableURL: exec, arguments: args, currentDirectoryURL: repo, includeKeys: true)
            var decoded: [PodRuntime]?
            if result.isSuccess,
               let line = result.combinedOutput.split(separator: "\n").first(where: { $0.hasPrefix("{") }) {
                decoded = (try? JSONDecoder().decode(PodListPayload.self, from: Data(line.utf8)))?.pods
            }
            DispatchQueue.main.async {
                isChecking = false
                if let pods = decoded {
                    var map: [String: PodRuntime] = [:]
                    for pod in pods { map[pod.name] = pod }
                    runtimes = map
                    lastChecked = Date()
                    listError = nil
                } else {
                    let text = result.combinedOutput.trimmingCharacters(in: .whitespacesAndNewlines)
                    listError = text.isEmpty ? "종료 코드 \(result.exitCode)" : String(text.suffix(300))
                }
            }
        }
    }

    private func powerOn(_ name: String) {
        guard !appState.isProtected(name) else { return }
        let yaml = appState.yamlURL(forPodNamed: name).path
        let (exec, args) = gpuUpCommand(["up", yaml, "--wait", "false", "--api-key", "${RUNPOD_API_KEY}"])
        run(name: name, label: "켜는 중", exec: exec, args: args,
            ok: "'\(name)' 켜기 요청 완료. 모델 로딩이 끝나면 '켜짐 · 정상'으로 바뀝니다.",
            fail: "'\(name)' 켜기 실패")
    }

    private func powerOff(_ name: String) {
        guard !appState.isProtected(name) else { return }
        // Never pass --all.
        let (exec, args) = gpuUpCommand(["down", name, "--api-key", "${RUNPOD_API_KEY}"])
        run(name: name, label: "끄는 중", exec: exec, args: args,
            ok: "'\(name)' 종료 완료.", fail: "'\(name)' 끄기 실패")
    }

    private func run(name: String, label: String, exec: URL, args: [String], ok: String, fail: String) {
        let action = args.contains("up") ? "start" : "stop"
        let repo = URL(fileURLWithPath: appState.dataRootPath)
        busy[name] = label
        statusMessage = "'\(name)' \(label)..."
        isError = false
        outputLog = ""
        DispatchQueue.global(qos: .userInitiated).async {
            let result = ProcessRunner.run(executableURL: exec, arguments: args, currentDirectoryURL: repo, includeKeys: true)
            DispatchQueue.main.async {
                busy[name] = nil
                outputLog = result.combinedOutput
                ActivityLog.append(
                    action: action,
                    pod: name,
                    ok: result.isSuccess,
                    message: result.isSuccess ? ok : "\(fail) (종료 코드: \(result.exitCode))",
                    detail: result.combinedOutput
                )
                statusMessage = result.isSuccess ? ok : "\(fail) (종료 코드: \(result.exitCode))"
                isError = !result.isSuccess
                refreshStatus()
            }
        }
    }
}
