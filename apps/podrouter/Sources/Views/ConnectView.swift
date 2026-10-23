import AppKit
import SwiftUI

/// Builds client settings (`.env` lines or a Python dict) for the saved pods,
/// so another app can call them like OpenRouter: one base URL per pod.
struct ConnectView: View {
    @ObservedObject var appState: AppState

    private enum Format: String, CaseIterable, Identifiable {
        case env = ".env"
        case python = "Python"
        var id: String { rawValue }
    }

    private struct Endpoint {
        let name: String
        let baseURL: String?
        /// Why the URL is missing, or a caution about it.
        let note: String?
        /// True when the address stays the same every time the pod is created.
        let fixed: Bool
    }

    @State private var selected: Set<String> = []
    @State private var format: Format = .env
    @State private var includeKey: Bool = true
    @State private var runtimes: [String: PodRuntime] = [:]
    @State private var isChecking = false
    @State private var message: String?

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                Text("7. 연결 설정 (Connect)")
                    .font(.title2)
                    .bold()

                Text("다른 프로그램에서 Pod 모델을 OpenAI 호환 API로 부를 때 쓸 설정을 만듭니다. Pod마다 base URL이 하나씩 생기고, API 키는 모든 Pod가 같이 씁니다.")
                    .font(.body)
                    .foregroundColor(.secondary)

                modeBanner

                GroupBox(label: Label("포함할 Pod", systemImage: "checklist")) {
                    VStack(alignment: .leading, spacing: 8) {
                        if appState.savedPodNames.isEmpty {
                            Text("저장된 Pod 설정이 없습니다. [3. Pod 설정]에서 먼저 만드세요.")
                                .foregroundColor(.secondary)
                        }
                        ForEach(appState.savedPodNames.filter { !appState.isProtected($0) }, id: \.self) { name in
                            let ep = endpoint(for: name)
                            HStack(alignment: .firstTextBaseline, spacing: 10) {
                                Toggle("", isOn: Binding(
                                    get: { selected.contains(name) },
                                    set: { on in if on { selected.insert(name) } else { selected.remove(name) } }
                                ))
                                .labelsHidden()
                                VStack(alignment: .leading, spacing: 2) {
                                    HStack(spacing: 6) {
                                        Text(name)
                                            .font(.system(.body, design: .monospaced))
                                            .fontWeight(.medium)
                                        Text(envName(for: name))
                                            .font(.system(.caption, design: .monospaced))
                                            .foregroundColor(.secondary)
                                    }
                                    if let url = ep.baseURL {
                                        Text(url)
                                            .font(.system(.caption, design: .monospaced))
                                            .foregroundColor(ep.fixed ? .green : .orange)
                                            .textSelection(.enabled)
                                    }
                                    if let note = ep.note {
                                        Text(note)
                                            .font(.caption)
                                            .foregroundColor(.orange)
                                    }
                                }
                                Spacer()
                            }
                        }
                        HStack {
                            Button("모두 선택") { selected = Set(appState.savedPodNames.filter { !appState.isProtected($0) }) }
                                .buttonStyle(.borderless)
                            Button("모두 해제") { selected = [] }
                                .buttonStyle(.borderless)
                            Spacer()
                            if isChecking { ProgressView().controlSize(.small) }
                            Button {
                                refreshRuntimes()
                            } label: {
                                Image(systemName: "arrow.clockwise")
                            }
                            .help("켜져 있는 Pod 주소 다시 확인")
                            .disabled(isChecking)
                        }
                        .font(.caption)
                    }
                    .padding(8)
                }

                GroupBox(label: Label("설정 내용", systemImage: "doc.on.clipboard")) {
                    VStack(alignment: .leading, spacing: 10) {
                        HStack {
                            Picker("", selection: $format) {
                                ForEach(Format.allCases) { Text($0.rawValue).tag($0) }
                            }
                            .pickerStyle(.segmented)
                            .frame(maxWidth: 200)

                            if format == .env {
                                Toggle("실제 API 키 포함", isOn: $includeKey)
                                    .disabled(appState.modelKeyStatus != "저장됨")
                            }
                            Spacer()
                            Button {
                                copy(render(masked: false))
                            } label: {
                                Label("복사", systemImage: "doc.on.doc")
                            }
                            .buttonStyle(.borderedProminent)
                            .disabled(selected.isEmpty)
                        }

                        Text(selected.isEmpty ? "위에서 Pod를 하나 이상 고르세요." : render(masked: true))
                            .font(.system(.callout, design: .monospaced))
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .padding(10)
                            .background(Color.secondary.opacity(0.08))
                            .cornerRadius(6)
                            .textSelection(.enabled)

                        Text(format == .env
                             ? "미리보기에서는 키를 가립니다. 복사하면 실제 키가 들어갑니다. .env 파일은 git에 올리지 마세요."
                             : "Python 코드에는 키를 넣지 않고 환경변수 LLM_API_KEY에서 읽습니다. .env 형식도 함께 쓰세요.")
                            .font(.caption)
                            .foregroundColor(.secondary)

                        if appState.modelKeyStatus != "저장됨" {
                            Label("MODEL_API_KEY가 없어 주소만 알면 누구나 모델을 쓸 수 있습니다. [2. 키 관리]에서 만드세요.", systemImage: "lock.open")
                                .font(.caption)
                                .foregroundColor(.orange)
                        }
                    }
                    .padding(8)
                }

                if let message = message {
                    Label(message, systemImage: "checkmark.circle.fill")
                        .foregroundColor(.green)
                        .font(.subheadline)
                }

                Spacer()
            }
            .padding()
            .onAppear {
                appState.refreshKeyStatus()
                appState.refreshSavedPods()
                if selected.isEmpty {
                    selected = Set(appState.savedPodNames.filter { !appState.isProtected($0) })
                }
                refreshRuntimes()
            }
        }
    }

    // MARK: - Address rules

    @ViewBuilder
    private var modeBanner: some View {
        let (text, ok): (String, Bool) = {
            switch appState.effectiveTunnelMode {
            case "tailscale":
                return appState.tsTailnet.isEmpty
                    ? ("주소 방식: Tailscale Funnel. 테일넷 도메인이 없어 주소를 만들 수 없습니다. [2. 키 관리]에서 TS_TAILNET을 저장하세요.", false)
                    : ("주소 방식: Tailscale Funnel. Pod마다 고정 주소 https://<Pod 이름>.\(appState.tsTailnet) 를 씁니다.", true)
            case "cloudflare":
                if appState.cfKeyStatus == "저장됨" && !appState.cfHostname.isEmpty {
                    return ("주소 방식: Cloudflare Named Tunnel. 주소가 하나(\(appState.cfHostname))라서 동시에 한 Pod만 쓸 수 있습니다.", true)
                }
                return ("주소 방식: Cloudflare Quick Tunnel. Pod를 켤 때마다 주소가 바뀝니다. 지금 켜져 있는 Pod 주소만 넣습니다.", false)
            default:
                return ("주소 방식: Runpod 주소. Pod를 켤 때마다 주소가 바뀝니다. 지금 켜져 있는 Pod 주소만 넣으며, 고정 주소가 필요하면 Tailscale을 쓰세요.", false)
            }
        }()
        Label(text, systemImage: ok ? "checkmark.seal.fill" : "exclamationmark.triangle.fill")
            .font(.callout)
            .foregroundColor(ok ? .primary : .orange)
            .padding(10)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background((ok ? Color.green : Color.orange).opacity(0.1))
            .cornerRadius(8)
    }

    private func endpoint(for name: String) -> Endpoint {
        let live = runtimes[name]
        switch appState.effectiveTunnelMode {
        case "tailscale":
            guard !appState.tsTailnet.isEmpty else {
                return Endpoint(name: name, baseURL: nil, note: "테일넷 도메인(TS_TAILNET)이 필요합니다.", fixed: true)
            }
            return Endpoint(name: name, baseURL: "https://\(name).\(appState.tsTailnet)/v1", note: nil, fixed: true)
        case "cloudflare" where appState.cfKeyStatus == "저장됨" && !appState.cfHostname.isEmpty:
            let note = selected.count > 1 ? "Named Tunnel 주소는 하나라 여러 Pod가 같이 쓸 수 없습니다." : nil
            return Endpoint(name: name, baseURL: "https://\(appState.cfHostname)/v1", note: note, fixed: true)
        case "cloudflare":
            if let t = live?.tunnel_url, !t.isEmpty {
                return Endpoint(name: name, baseURL: "\(t)/v1", note: "지금 주소입니다. 다시 켜면 바뀝니다.", fixed: false)
            }
            return Endpoint(name: name, baseURL: nil, note: "꺼져 있거나 준비 중이라 주소를 모릅니다.", fixed: false)
        default:
            if let live = live, !live.url.isEmpty {
                return Endpoint(name: name, baseURL: "\(live.url)/v1", note: "지금 주소입니다. 다시 켜면 바뀝니다.", fixed: false)
            }
            return Endpoint(name: name, baseURL: nil, note: "꺼져 있어 주소를 모릅니다.", fixed: false)
        }
    }

    /// `qwen38-27b` -> `LLM_BASE_URL_QWEN38_27B`
    private func envName(for name: String) -> String {
        "LLM_BASE_URL_" + name.uppercased().replacingOccurrences(of: "-", with: "_")
    }

    // MARK: - Output

    private func render(masked: Bool) -> String {
        let names = appState.savedPodNames.filter { selected.contains($0) }
        let endpoints = names.map(endpoint(for:))
        let stamp: String = {
            let f = DateFormatter()
            f.dateFormat = "yyyy-MM-dd HH:mm"
            return f.string(from: Date())
        }()
        switch format {
        case .env:
            var lines = ["# podrouter 연결 설정 (\(stamp))"]
            let key = SecretStore.loadKeyForChildProcess(account: SecretStore.modelApiAccount) ?? ""
            if key.isEmpty {
                lines.append("LLM_API_KEY=                     # 키 없음: 모델 API가 열려 있습니다")
            } else if !includeKey {
                lines.append("LLM_API_KEY=<MODEL_API_KEY>      # 모든 Pod 공통")
            } else {
                let shown = masked ? String(key.prefix(6)) + "…(가림)" : key
                lines.append("LLM_API_KEY=\(shown)    # 모든 Pod 공통")
            }
            for ep in endpoints {
                if let url = ep.baseURL {
                    lines.append("\(envName(for: ep.name))=\(url)" + (ep.fixed ? "" : "    # 다시 켜면 바뀜"))
                } else {
                    lines.append("# \(envName(for: ep.name))=    # \(ep.note ?? "주소 없음")")
                }
            }
            return lines.joined(separator: "\n")
        case .python:
            var lines = [
                "import os",
                "",
                "# podrouter 연결 설정 (\(stamp))",
                "LLM_API_KEY = os.environ[\"LLM_API_KEY\"]  # 모든 Pod 공통",
                "LLM_ENDPOINTS = {",
            ]
            for ep in endpoints {
                if let url = ep.baseURL {
                    lines.append("    \"\(ep.name)\": \"\(url)\"," + (ep.fixed ? "" : "  # 다시 켜면 바뀜"))
                } else {
                    lines.append("    # \"\(ep.name)\": None,  # \(ep.note ?? "주소 없음")")
                }
            }
            lines.append("}")
            return lines.joined(separator: "\n")
        }
    }

    private func copy(_ text: String) {
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(text + "\n", forType: .string)
        message = "\(format.rawValue) 형식으로 복사했습니다 (Pod \(selected.count)개)."
    }

    // MARK: - Live addresses

    private func refreshRuntimes() {
        guard !isChecking, appState.runpodKeyStatus == "저장됨", appState.isVenvReady else { return }
        var args: [String] = appState.isGpuUpDirectExecutable ? [] : ["-m", "podrouter"]
        args.append(contentsOf: ["ls", "--json", "--api-key", "${RUNPOD_API_KEY}"])
        let exec = URL(fileURLWithPath: appState.gpuUpCLIPath)
        let repo = URL(fileURLWithPath: appState.dataRootPath)
        isChecking = true
        DispatchQueue.global(qos: .utility).async {
            let result = ProcessRunner.run(executableURL: exec, arguments: args, currentDirectoryURL: repo, includeKeys: true)
            var decoded: [PodRuntime] = []
            if result.isSuccess,
               let line = result.combinedOutput.split(separator: "\n").first(where: { $0.hasPrefix("{") }),
               let payload = try? JSONDecoder().decode(PodListPayload.self, from: Data(line.utf8)) {
                decoded = payload.pods
            }
            DispatchQueue.main.async {
                isChecking = false
                var map: [String: PodRuntime] = [:]
                for pod in decoded { map[pod.name] = pod }
                runtimes = map
            }
        }
    }
}
