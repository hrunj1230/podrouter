import AppKit
import SwiftUI

struct KeysView: View {
    @ObservedObject var appState: AppState

    @State private var runpodInput: String = ""
    @State private var tsInput: String = ""
    @State private var hfInput: String = ""
    @State private var cfInput: String = ""
    @State private var cfHostInput: String = ""
    @State private var modelKeyInput: String = ""
    @State private var tailnetInput: String = ""
    @State private var protectedInput: String = ""
    @State private var confirmRegenerate: Bool = false
    @State private var message: String?
    @State private var isError: Bool = false

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                Text("2. 키 관리 (Keys)")
                    .font(.title2)
                    .bold()

                Text("Runpod API 키, Tailscale Auth 키, Cloudflare 터널 토큰, Hugging Face 토큰을 저장소의 .env 파일(나만 읽기 가능, 권한 0600)에 저장합니다. 타이머와 Claude 실행기도 같은 파일을 씁니다. 비밀값은 다시 화면에 나오지 않고 저장 여부만 표시됩니다.")
                    .font(.body)
                    .foregroundColor(.secondary)

                tunnelSection

                modelKeySection

                protectedSection

                GroupBox(label: Label("현재 .env 저장 상태", systemImage: "key.fill")) {
                    VStack(alignment: .leading, spacing: 12) {
                        HStack {
                            Text("RUNPOD_API_KEY (필수):")
                                .fontWeight(.medium)
                            statusBadge(for: appState.runpodKeyStatus)
                            Spacer()
                            if appState.runpodKeyStatus == "저장됨" {
                                Button("삭제", role: .destructive) {
                                    deleteKey(SecretStore.runpodAccount)
                                }
                                .buttonStyle(.borderless)
                            }
                        }

                        HStack {
                            Text("TS_AUTHKEY (선택):")
                                .fontWeight(.medium)
                            statusBadge(for: appState.tsKeyStatus)
                            Spacer()
                            if appState.tsKeyStatus == "저장됨" {
                                Button("삭제", role: .destructive) {
                                    deleteKey(SecretStore.tsAccount)
                                }
                                .buttonStyle(.borderless)
                            }
                        }

                        HStack {
                            Text("HF_TOKEN (선택):")
                                .fontWeight(.medium)
                            statusBadge(for: appState.hfKeyStatus)
                            Spacer()
                            if appState.hfKeyStatus == "저장됨" {
                                Button("삭제", role: .destructive) {
                                    deleteKey(SecretStore.hfAccount)
                                }
                                .buttonStyle(.borderless)
                            }
                        }

                        HStack {
                            Text("CF_TUNNEL_TOKEN (선택):")
                                .fontWeight(.medium)
                            statusBadge(for: appState.cfKeyStatus)
                            Spacer()
                            if appState.cfKeyStatus == "저장됨" {
                                Button("삭제", role: .destructive) {
                                    deleteKey(SecretStore.cfTokenAccount)
                                }
                                .buttonStyle(.borderless)
                            }
                        }
                    }
                    .padding(8)
                }

                GroupBox(label: Label("새 키 입력 및 저장", systemImage: "lock.shield")) {
                    VStack(alignment: .leading, spacing: 14) {
                        VStack(alignment: .leading, spacing: 6) {
                            Text("RUNPOD_API_KEY")
                                .font(.subheadline)
                                .fontWeight(.medium)
                            SecureField("Runpod API 키를 입력하세요 (필수)", text: $runpodInput)
                                .textFieldStyle(.roundedBorder)
                        }

                        VStack(alignment: .leading, spacing: 6) {
                            Text("TS_AUTHKEY")
                                .font(.subheadline)
                                .fontWeight(.medium)
                            SecureField("Tailscale Auth 키를 입력하세요 (선택)", text: $tsInput)
                                .textFieldStyle(.roundedBorder)
                        }

                        VStack(alignment: .leading, spacing: 6) {
                            Text("HF_TOKEN")
                                .font(.subheadline)
                                .fontWeight(.medium)
                            SecureField("Hugging Face 토큰을 입력하세요 (선택)", text: $hfInput)
                                .textFieldStyle(.roundedBorder)
                            Text("공개 모델은 없어도 됩니다. 모델 다운로드가 권한 오류로 실패하거나 승인이 필요한 모델을 쓸 때 넣으세요. 저장하면 Pod에 HF_TOKEN으로 전달됩니다.")
                                .font(.caption)
                                .foregroundColor(.secondary)
                        }

                        HStack {
                            Button(action: saveKeys) {
                                Label(".env에 저장", systemImage: "square.and.arrow.down")
                            }
                            .buttonStyle(.borderedProminent)
                            .disabled([runpodInput, tsInput, hfInput].allSatisfy { $0.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty })

                            Spacer()
                        }
                    }
                    .padding(8)
                }

                if let msg = message {
                    HStack {
                        Image(systemName: isError ? "exclamationmark.triangle.fill" : "checkmark.circle.fill")
                        Text(msg)
                            .font(.subheadline)
                    }
                    .foregroundColor(isError ? .red : .green)
                    .padding(8)
                    .background((isError ? Color.red : Color.green).opacity(0.1))
                    .cornerRadius(8)
                }

                Spacer()
            }
            .padding()
        }
    }

    // MARK: - Protected pod names

    private var protectedSection: some View {
        GroupBox(label: Label("보호할 Pod 이름 (PODROUTER_PROTECTED_PODS)", systemImage: "hand.raised.fill")) {
            VStack(alignment: .leading, spacing: 10) {
                Text("이 도구는 원래 자기가 만든 Pod만 켜고 끕니다. 여기 적은 이름은 한 번 더 막아서, 같은 이름으로 만들거나 끄거나 예약하지 못하게 합니다. 비밀값은 아닙니다.")
                    .font(.caption)
                    .foregroundColor(.secondary)
                Text(appState.protectedPods.isEmpty ? "지금 보호 중인 이름: 없음" : "지금 보호 중인 이름: \(appState.protectedPods.joined(separator: ", "))")
                    .font(.callout)
                HStack {
                    TextField("쉼표로 구분, 예: team-pod, old-notebook", text: $protectedInput)
                        .textFieldStyle(.roundedBorder)
                    Button("저장") { saveProtected() }
                        .disabled(protectedInput.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                    if !appState.protectedPods.isEmpty {
                        Button("비우기", role: .destructive) {
                            deleteKey(SecretStore.protectedPodsKey)
                        }
                        .buttonStyle(.borderless)
                    }
                }
            }
            .padding(8)
        }
    }

    private func saveProtected() {
        let names = protectedInput
            .split(separator: ",")
            .map { $0.trimmingCharacters(in: .whitespaces) }
            .filter { !$0.isEmpty }
        guard !names.isEmpty,
              names.allSatisfy({ $0.range(of: "^[A-Za-z0-9][A-Za-z0-9-]{0,40}$", options: .regularExpression) != nil }) else {
            message = "Pod 이름은 영문, 숫자, 하이픈만 쓸 수 있습니다."
            isError = true
            return
        }
        let merged = Array(Set(appState.protectedPods + names)).sorted()
        guard SecretStore.saveKey(account: SecretStore.protectedPodsKey, secret: merged.joined(separator: ",")) else {
            message = "보호 목록 저장에 실패했습니다."
            isError = true
            return
        }
        protectedInput = ""
        appState.refreshKeyStatus()
        message = "보호 목록을 저장했습니다: \(merged.joined(separator: ", "))"
        isError = false
    }

    // MARK: - Model API key

    private var modelKeySection: some View {
        GroupBox(label: Label("모델 API 키 (MODEL_API_KEY)", systemImage: "lock.shield")) {
            VStack(alignment: .leading, spacing: 12) {
                Text("이 키가 있으면 모델 주소로 오는 요청에 `Authorization: Bearer <키>`가 있어야 응답합니다. 키가 없으면 주소만 아는 누구나 모델을 쓸 수 있습니다. 헬스 체크(/health)는 키 없이 열려 있습니다.")
                    .font(.caption)
                    .foregroundColor(.secondary)

                HStack(spacing: 10) {
                    Text("상태:")
                        .fontWeight(.medium)
                    statusBadge(for: appState.modelKeyStatus)
                    Spacer()
                    Button {
                        if appState.modelKeyStatus == "저장됨" {
                            confirmRegenerate = true
                        } else {
                            generateModelKey()
                        }
                    } label: {
                        Label(appState.modelKeyStatus == "저장됨" ? "새로 생성" : "자동 생성", systemImage: "wand.and.stars")
                    }
                    Button {
                        copyModelKey()
                    } label: {
                        Label("복사", systemImage: "doc.on.doc")
                    }
                    .disabled(appState.modelKeyStatus != "저장됨")
                    if appState.modelKeyStatus == "저장됨" {
                        Button("삭제", role: .destructive) {
                            deleteKey(SecretStore.modelApiAccount)
                        }
                        .buttonStyle(.borderless)
                    }
                }

                HStack {
                    SecureField("직접 만든 키를 입력하세요 (16자 이상, 공백 없이)", text: $modelKeyInput)
                        .textFieldStyle(.roundedBorder)
                    Button("저장") { saveManualModelKey() }
                        .disabled(modelKeyInput.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                }

                Text("키를 만들거나 바꾸면 켜져 있는 Pod에는 바로 적용되지 않습니다. Power 화면에서 끄고 다시 켜세요.")
                    .font(.caption)
                    .foregroundColor(.orange)
            }
            .padding(8)
        }
        .confirmationDialog("모델 API 키를 새로 만들까요?", isPresented: $confirmRegenerate, titleVisibility: .visible) {
            Button("새로 만들기", role: .destructive) { generateModelKey() }
            Button("취소", role: .cancel) {}
        } message: {
            Text("지금 키를 쓰는 프로그램은 새 키로 바꿔야 합니다.")
        }
    }

    private func copyToClipboard(_ text: String) {
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(text, forType: .string)
    }

    private func generateModelKey() {
        guard let key = SecretStore.generateModelApiKey(),
              SecretStore.saveKey(account: SecretStore.modelApiAccount, secret: key) else {
            message = "모델 API 키를 만들지 못했습니다."
            isError = true
            return
        }
        copyToClipboard(key)
        appState.refreshKeyStatus()
        ActivityLog.append(action: "model-key", pod: "", ok: true, message: "모델 API 키 자동 생성")
        message = "새 모델 API 키를 만들어 저장하고 클립보드에 복사했습니다. 켜져 있는 Pod는 다시 켜야 적용됩니다."
        isError = false
    }

    private func copyModelKey() {
        guard let key = SecretStore.loadKeyForChildProcess(account: SecretStore.modelApiAccount), !key.isEmpty else {
            message = "저장된 모델 API 키가 없습니다."
            isError = true
            return
        }
        copyToClipboard(key)
        message = "모델 API 키를 클립보드에 복사했습니다."
        isError = false
    }

    private func saveManualModelKey() {
        let key = modelKeyInput.trimmingCharacters(in: .whitespacesAndNewlines)
        guard key.count >= 16, key.rangeOfCharacter(from: .whitespacesAndNewlines) == nil,
              key.allSatisfy({ $0.isASCII }) else {
            message = "키는 16자 이상, 공백 없는 영문·숫자·기호여야 합니다."
            isError = true
            return
        }
        guard SecretStore.saveKey(account: SecretStore.modelApiAccount, secret: key) else {
            message = "모델 API 키 저장에 실패했습니다."
            isError = true
            return
        }
        modelKeyInput = ""
        appState.refreshKeyStatus()
        ActivityLog.append(action: "model-key", pod: "", ok: true, message: "모델 API 키 직접 입력")
        message = "직접 입력한 모델 API 키를 저장했습니다. 켜져 있는 Pod는 다시 켜야 적용됩니다."
        isError = false
    }

    // MARK: - Public address mode

    private var tunnelSelection: Binding<String> {
        Binding(
            get: { appState.effectiveTunnelMode },
            set: { saveTunnelMode($0) }
        )
    }

    private var tunnelSection: some View {
        GroupBox(label: Label("공개 주소 방식", systemImage: "network")) {
            VStack(alignment: .leading, spacing: 12) {
                Picker("방식", selection: tunnelSelection) {
                    Text("Runpod 주소만").tag("none")
                    Text("Tailscale Funnel").tag("tailscale")
                    Text("Cloudflare Tunnel").tag("cloudflare")
                }
                .pickerStyle(.segmented)
                .labelsHidden()

                Text("선택하면 바로 .env의 PODROUTER_TUNNEL에 저장됩니다. 다음에 켜는 Pod부터 적용되고, 방식을 바꾸면 이미 켜진 Pod를 재사용하지 않고 새로 만듭니다.")
                    .font(.caption)
                    .foregroundColor(.secondary)

                switch appState.effectiveTunnelMode {
                case "tailscale":
                    if appState.tsKeyStatus != "저장됨" {
                        warning("TS_AUTHKEY가 없습니다. 아래에서 저장해야 Pod를 켤 수 있습니다.")
                    } else {
                        info("Pod가 Tailscale에 스스로 등록하고 https://<Pod 이름>.<테일넷>.ts.net 주소를 엽니다.")
                    }
                    tailnetField
                case "cloudflare":
                    cloudflareFields
                default:
                    info("Runpod 프록시 주소(https://<pod id>-<포트>.proxy.runpod.net)만 씁니다. Pod를 새로 만들면 주소가 바뀝니다.")
                }
            }
            .padding(8)
        }
    }

    /// Tailnet domain used to build `https://<pod>.<tailnet>` in the Connect screen.
    private var tailnetField: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("TS_TAILNET (테일넷 도메인, 비밀값 아님)")
                .font(.subheadline)
                .fontWeight(.medium)
            HStack {
                TextField(appState.tsTailnet.isEmpty ? "예: your-tailnet.ts.net" : appState.tsTailnet, text: $tailnetInput)
                    .textFieldStyle(.roundedBorder)
                Button("저장") { saveTailnet() }
                    .disabled(tailnetInput.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            }
            if appState.tsTailnet.isEmpty {
                Text("Tailscale 관리 화면 → DNS → Tailnet name 에 있습니다. 연결 설정 화면이 Pod 주소를 만들 때 씁니다.")
                    .font(.caption)
                    .foregroundColor(.secondary)
            } else {
                HStack(spacing: 8) {
                    statusBadge(for: "저장됨")
                    Text("https://<Pod 이름>.\(appState.tsTailnet)")
                        .font(.subheadline)
                        .foregroundColor(.secondary)
                        .textSelection(.enabled)
                }
            }
        }
    }

    private func saveTailnet() {
        var text = tailnetInput.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        for prefix in ["https://", "http://"] where text.hasPrefix(prefix) {
            text = String(text.dropFirst(prefix.count))
        }
        while text.hasSuffix("/") { text.removeLast() }
        guard text.range(of: "^[a-z0-9-]+\\.ts\\.net$", options: .regularExpression) != nil else {
            message = "테일넷 도메인은 'your-tailnet.ts.net' 같은 형식이어야 합니다."
            isError = true
            return
        }
        guard SecretStore.saveKey(account: SecretStore.tsTailnetKey, secret: text) else {
            message = "TS_TAILNET 저장에 실패했습니다."
            isError = true
            return
        }
        tailnetInput = ""
        appState.refreshKeyStatus()
        message = "테일넷 도메인을 저장했습니다: \(text)"
        isError = false
    }

    private var cloudflareFields: some View {
        VStack(alignment: .leading, spacing: 10) {
            if appState.cfKeyStatus == "저장됨" {
                info("Named Tunnel: 저장한 토큰으로 터널을 엽니다. 주소는 Cloudflare 대시보드의 Public Hostname(서비스 http://localhost:<포트>)에서 정한 값입니다.")
            } else {
                info("Quick Tunnel: 토큰이 없어서 계정 없이 https://<임의 이름>.trycloudflare.com 주소를 씁니다. Pod를 켤 때마다 주소가 바뀌고, 준비가 끝나면 전원 화면에 표시됩니다. 고정 주소를 쓰려면 터널 토큰을 저장하세요.")
            }

            VStack(alignment: .leading, spacing: 6) {
                Text("CF_TUNNEL_TOKEN")
                    .font(.subheadline)
                    .fontWeight(.medium)
                SecureField("Cloudflare 터널 토큰 (선택, 있으면 Named Tunnel)", text: $cfInput)
                    .textFieldStyle(.roundedBorder)
            }

            VStack(alignment: .leading, spacing: 6) {
                Text("CF_TUNNEL_HOSTNAME")
                    .font(.subheadline)
                    .fontWeight(.medium)
                TextField(appState.cfHostname.isEmpty ? "예: llm.example.com (Named Tunnel 주소, 비밀값 아님)" : appState.cfHostname, text: $cfHostInput)
                    .textFieldStyle(.roundedBorder)
                if !appState.cfHostname.isEmpty {
                    HStack {
                        Text("저장된 주소: https://\(appState.cfHostname)")
                            .font(.caption)
                            .foregroundColor(.secondary)
                        Button("지우기", role: .destructive) {
                            deleteKey(SecretStore.cfHostnameKey)
                        }
                        .buttonStyle(.borderless)
                        .font(.caption)
                    }
                }
            }

            Button(action: saveCloudflare) {
                Label("Cloudflare 설정 저장", systemImage: "square.and.arrow.down")
            }
            .disabled(cfInput.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                      && cfHostInput.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
        }
    }

    private func info(_ text: String) -> some View {
        Text(text)
            .font(.caption)
            .foregroundColor(.secondary)
            .fixedSize(horizontal: false, vertical: true)
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

    private func saveTunnelMode(_ mode: String) {
        guard ["none", "tailscale", "cloudflare"].contains(mode) else { return }
        if SecretStore.saveKey(account: SecretStore.tunnelModeKey, secret: mode) {
            appState.refreshKeyStatus()
            message = "공개 주소 방식을 저장했습니다: \(mode)"
            isError = false
        } else {
            message = "PODROUTER_TUNNEL 저장에 실패했습니다."
            isError = true
        }
    }

    private func saveCloudflare() {
        let token = cfInput.trimmingCharacters(in: .whitespacesAndNewlines)
        var host = cfHostInput.trimmingCharacters(in: .whitespacesAndNewlines)
        for prefix in ["https://", "http://"] where host.lowercased().hasPrefix(prefix) {
            host = String(host.dropFirst(prefix.count))
        }
        while host.hasSuffix("/") { host.removeLast() }

        if !host.isEmpty, host.range(of: "^[A-Za-z0-9.-]+$", options: .regularExpression) == nil {
            message = "주소는 llm.example.com 같은 호스트 이름만 넣으세요."
            isError = true
            return
        }
        if !token.isEmpty, !SecretStore.saveKey(account: SecretStore.cfTokenAccount, secret: token) {
            message = "CF_TUNNEL_TOKEN 저장에 실패했습니다."
            isError = true
            return
        }
        if !host.isEmpty, !SecretStore.saveKey(account: SecretStore.cfHostnameKey, secret: host) {
            message = "CF_TUNNEL_HOSTNAME 저장에 실패했습니다."
            isError = true
            return
        }
        cfInput = ""
        cfHostInput = ""
        appState.refreshKeyStatus()
        message = "Cloudflare 설정을 .env에 저장했습니다."
        isError = false
    }

    @ViewBuilder
    private func statusBadge(for status: String) -> some View {
        HStack(spacing: 4) {
            Circle()
                .fill(status == "저장됨" ? Color.green : Color.gray)
                .frame(width: 8, height: 8)
            Text(status)
                .font(.subheadline)
                .foregroundColor(status == "저장됨" ? .green : .secondary)
        }
        .padding(.horizontal, 8)
        .padding(.vertical, 3)
        .background((status == "저장됨" ? Color.green : Color.gray).opacity(0.1))
        .cornerRadius(6)
    }

    private func saveKeys() {
        let trimmedRunpod = runpodInput.trimmingCharacters(in: .whitespacesAndNewlines)
        let trimmedTs = tsInput.trimmingCharacters(in: .whitespacesAndNewlines)
        let trimmedHf = hfInput.trimmingCharacters(in: .whitespacesAndNewlines)

        var savedCount = 0
        if !trimmedRunpod.isEmpty {
            if SecretStore.saveKey(account: SecretStore.runpodAccount, secret: trimmedRunpod) {
                savedCount += 1
            } else {
                message = "RUNPOD_API_KEY 저장에 실패했습니다."
                isError = true
                return
            }
        }

        if !trimmedTs.isEmpty {
            if SecretStore.saveKey(account: SecretStore.tsAccount, secret: trimmedTs) {
                savedCount += 1
            } else {
                message = "TS_AUTHKEY 저장에 실패했습니다."
                isError = true
                return
            }
        }

        if !trimmedHf.isEmpty {
            if SecretStore.saveKey(account: SecretStore.hfAccount, secret: trimmedHf) {
                savedCount += 1
            } else {
                message = "HF_TOKEN 저장에 실패했습니다."
                isError = true
                return
            }
        }

        // Clear input fields immediately
        runpodInput = ""
        tsInput = ""
        hfInput = ""

        appState.refreshKeyStatus()
        isError = false
        message = ".env에 저장되었습니다 (\(savedCount)건). 입력 필드가 초기화되었습니다."
    }

    private func deleteKey(_ account: String) {
        SecretStore.deleteKey(account: account)
        appState.refreshKeyStatus()
        message = "\(account) 키가 .env에서 삭제되었습니다."
        isError = false
    }
}
