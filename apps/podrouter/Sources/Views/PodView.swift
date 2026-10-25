import AppKit
import SwiftUI
import UniformTypeIdentifiers

struct PodView: View {
    @ObservedObject var appState: AppState

    @State private var contextString: String = "196608"
    @State private var message: String?
    @State private var isError: Bool = false
    @State private var outputLog: String = ""
    @State private var selectedSaved: String = ""
    /// Set when Claude was opened; YAML newer than this is loaded when the app is focused again.
    @State private var claudeOpenedAt: Date?
    @State private var pendingDelete: String?

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                Text("3. Pod 설정 및 YAML 생성 (Pod)")
                    .font(.title2)
                    .bold()

                Text("Runpod에서 실행할 LLM 컨테이너 사양을 구성하고 YAML 설정 파일을 생성합니다.")
                    .font(.body)
                    .foregroundColor(.secondary)

                GroupBox(label: Label("불러오기", systemImage: "tray.and.arrow.down")) {
                    VStack(alignment: .leading, spacing: 12) {
                        HStack {
                            Picker("저장된 설정", selection: $selectedSaved) {
                                if appState.savedPodNames.isEmpty {
                                    Text("저장된 설정 없음").tag("")
                                }
                                ForEach(appState.savedPodNames, id: \.self) { name in
                                    Text(name).tag(name)
                                }
                            }
                            .disabled(appState.savedPodNames.isEmpty)

                            Button("불러오기") {
                                guard !selectedSaved.isEmpty else { return }
                                loadYaml(appState.yamlURL(forPodNamed: selectedSaved), label: "저장된 설정 '\(selectedSaved)'")
                            }
                            .disabled(selectedSaved.isEmpty || appState.isExecuting)

                            Button {
                                appState.refreshSavedPods()
                                syncSelection()
                            } label: {
                                Image(systemName: "arrow.clockwise")
                            }
                            .help("목록 새로고침")
                        }

                        HStack {
                            Button(action: openYamlFile) {
                                Label("YAML 파일 열기…", systemImage: "doc.badge.arrow.up")
                            }
                            .disabled(appState.isExecuting)

                            Button(action: openClaudeChat) {
                                Label("Claude와 대화로 만들기", systemImage: "bubble.left.and.text.bubble.right")
                            }
                            .disabled(appState.isExecuting)
                        }

                        Text("Claude 창에서 원하는 용도를 말하거나 모델 카드, 메모 같은 문서를 붙여넣으세요. Claude가 저장한 설정은 이 앱으로 돌아오면 자동으로 채워집니다.")
                            .font(.caption)
                            .foregroundColor(.secondary)
                    }
                    .padding(8)
                }

                GroupBox(label: Label("사양 입력", systemImage: "slider.horizontal.3")) {
                    VStack(alignment: .leading, spacing: 14) {
                        HStack {
                            Button(action: applyPreset) {
                                Label("기본 프리셋 채우기 (Qwen 3.8 27B / A40)", systemImage: "wand.and.stars")
                            }
                            .buttonStyle(.bordered)

                            Spacer()

                            if appState.currentYamlExists {
                                Label("YAML 파일 존재함", systemImage: "doc.fill")
                                    .font(.caption)
                                    .foregroundColor(.green)
                            } else {
                                Label("YAML 파일 없음", systemImage: "doc.badge.plus")
                                    .font(.caption)
                                    .foregroundColor(.secondary)
                            }
                        }

                        Divider()

                        VStack(alignment: .leading, spacing: 4) {
                            Text("Pod 이름 (Name)")
                                .font(.subheadline)
                                .fontWeight(.medium)
                            TextField("예: qwen38-27b", text: $appState.podName)
                                .textFieldStyle(.roundedBorder)
                        }

                        VStack(alignment: .leading, spacing: 4) {
                            Text("Hugging Face GGUF 모델 (Model)")
                                .font(.subheadline)
                                .fontWeight(.medium)
                            TextField("예: bartowski/orcarouter_Qwen3.8-27B-Uncensored-GGUF:Q4_K_M", text: $appState.podModel)
                                .textFieldStyle(.roundedBorder)
                        }

                        HStack(spacing: 16) {
                            VStack(alignment: .leading, spacing: 4) {
                                Text("GPU ID")
                                    .font(.subheadline)
                                    .fontWeight(.medium)
                                TextField("예: NVIDIA A40", text: $appState.podGpuId)
                                    .textFieldStyle(.roundedBorder)
                            }

                            VStack(alignment: .leading, spacing: 4) {
                                Text("클라우드 (Cloud)")
                                    .font(.subheadline)
                                    .fontWeight(.medium)
                                Picker("", selection: $appState.podCloud) {
                                    Text("SECURE").tag("SECURE")
                                    Text("COMMUNITY").tag("COMMUNITY")
                                }
                                .pickerStyle(.segmented)
                            }
                        }

                        VStack(alignment: .leading, spacing: 4) {
                            Text("문맥 토큰 수 (Context Tokens)")
                                .font(.subheadline)
                                .fontWeight(.medium)
                            TextField("예: 196608 (2048 ~ 262144)", text: $contextString)
                                .textFieldStyle(.roundedBorder)
                                .onChange(of: contextString) { _, newValue in
                                    if let val = Int(newValue) {
                                        appState.podContextTokens = val
                                    }
                                }
                        }

                        HStack {
                            Button(action: saveYaml) {
                                Label("YAML 생성 및 저장", systemImage: "arrow.down.doc.fill")
                            }
                            .buttonStyle(.borderedProminent)
                            .disabled(appState.isExecuting)

                            Spacer()

                            Text("저장 위치: \(appState.currentYamlURL.path)")
                                .font(.caption)
                                .foregroundColor(.secondary)
                                .lineLimit(1)
                                .truncationMode(.head)
                                .textSelection(.enabled)
                        }
                    }
                    .padding(8)
                }

                savedFilesSection

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

                if !outputLog.isEmpty {
                    GroupBox(label: Label("생성 출력 로그", systemImage: "terminal")) {
                        ScrollView {
                            Text(outputLog)
                                .font(.system(.caption, design: .monospaced))
                                .frame(maxWidth: .infinity, alignment: .leading)
                                .padding(4)
                        }
                        .frame(maxHeight: 140)
                    }
                }

                Spacer()
            }
            .padding()
            .onAppear {
                contextString = "\(appState.podContextTokens)"
                appState.refreshSavedPods()
                syncSelection()
            }
            .onChange(of: appState.podContextTokens) { _, newValue in
                if Int(contextString) != newValue {
                    contextString = "\(newValue)"
                }
            }
            .onReceive(NotificationCenter.default.publisher(for: NSApplication.didBecomeActiveNotification)) { _ in
                appState.refreshSavedPods()
                loadNewYamlFromClaude()
            }
        }
    }

    // MARK: - Saved file management

    private var savedFilesSection: some View {
        GroupBox(label: Label("저장된 설정 관리", systemImage: "folder")) {
            VStack(alignment: .leading, spacing: 10) {
                HStack {
                    Text(appState.podsDirectoryURL.path)
                        .font(.system(.caption, design: .monospaced))
                        .foregroundColor(.secondary)
                        .lineLimit(1)
                        .truncationMode(.head)
                        .textSelection(.enabled)
                    Spacer()
                    Button {
                        try? FileManager.default.createDirectory(at: appState.podsDirectoryURL, withIntermediateDirectories: true)
                        NSWorkspace.shared.open(appState.podsDirectoryURL)
                    } label: {
                        Label("Finder에서 폴더 열기", systemImage: "folder")
                    }
                }

                let files = appState.savedPodFiles()
                if files.isEmpty {
                    Text("저장된 설정이 없습니다.")
                        .font(.caption)
                        .foregroundColor(.secondary)
                } else {
                    Divider()
                    ForEach(files, id: \.name) { file in
                        savedFileRow(file.name, modified: file.modified)
                    }
                }
            }
            .padding(8)
        }
        .alert(
            "설정 삭제",
            isPresented: Binding(get: { pendingDelete != nil }, set: { if !$0 { pendingDelete = nil } }),
            presenting: pendingDelete
        ) { name in
            Button("삭제", role: .destructive) { deleteSaved(name) }
            Button("취소", role: .cancel) {}
        } message: { name in
            Text(deleteWarning(name))
        }
    }

    private func savedFileRow(_ name: String, modified: Date) -> some View {
        let url = appState.yamlURL(forPodNamed: name)
        let usedByTimer = appState.scheduledConfigPaths.contains(url.path)
        return HStack(spacing: 10) {
            Image(systemName: "doc.text")
                .foregroundColor(.secondary)
            Text(name)
                .font(.system(.body, design: .monospaced))
            if usedByTimer {
                Text("타이머 사용 중")
                    .font(.caption2)
                    .padding(.horizontal, 6)
                    .padding(.vertical, 2)
                    .background(Color.orange.opacity(0.15))
                    .foregroundColor(.orange)
                    .cornerRadius(4)
            }
            Spacer()
            Text(modified.formatted(date: .numeric, time: .shortened))
                .font(.caption)
                .foregroundColor(.secondary)
            Button("불러오기") {
                loadYaml(url, label: "저장된 설정 '\(name)'")
            }
            .disabled(appState.isExecuting)
            Button {
                NSWorkspace.shared.activateFileViewerSelecting([url])
            } label: {
                Image(systemName: "magnifyingglass")
            }
            .buttonStyle(.borderless)
            .help("Finder에서 보기")
            Button {
                pendingDelete = name
            } label: {
                Image(systemName: "trash")
            }
            .buttonStyle(.borderless)
            .foregroundColor(.red)
            .help("삭제")
            .disabled(appState.isExecuting)
        }
    }

    private func deleteWarning(_ name: String) -> String {
        var text = "\(name).yaml 파일을 삭제합니다. 되돌릴 수 없습니다. 켜져 있는 Pod는 꺼지지 않으며, 전원 화면에서 끌 수 있습니다."
        if appState.scheduledConfigPaths.contains(appState.yamlURL(forPodNamed: name).path) {
            text += " 이 파일은 타이머가 쓰고 있어서, 삭제하면 예약된 켜기가 실패합니다."
        }
        return text
    }

    private func deleteSaved(_ name: String) {
        if let error = appState.deleteSavedPod(named: name) {
            message = "삭제 실패: \(error)"
            isError = true
            return
        }
        ActivityLog.append(action: "yaml", pod: name, ok: true, message: "YAML 삭제", detail: "")
        if selectedSaved == name { selectedSaved = "" }
        syncSelection()
        message = "'\(name)' 설정을 삭제했습니다."
        isError = false
    }

    private func syncSelection() {
        if !appState.savedPodNames.contains(selectedSaved) {
            let current = appState.podName.trimmingCharacters(in: .whitespacesAndNewlines)
            selectedSaved = appState.savedPodNames.contains(current) ? current : (appState.savedPodNames.first ?? "")
        }
    }

    private func loadYaml(_ url: URL, label: String) {
        appState.isExecuting = true
        appState.readFields(from: url) { result in
            appState.isExecuting = false
            switch result {
            case .success(let fields):
                if appState.isProtected(fields.name) {
                    message = "\(fields.name)은(는) 보호 목록에 있는 이름이라 불러오지 않습니다."
                    isError = true
                    return
                }
                appState.applyFields(fields)
                contextString = "\(fields.context_tokens)"
                selectedSaved = appState.savedPodNames.contains(fields.name) ? fields.name : selectedSaved
                let saved = url.standardizedFileURL == appState.yamlURL(forPodNamed: fields.name).standardizedFileURL
                message = saved
                    ? "\(label)을(를) 불러왔습니다."
                    : "\(label)을(를) 불러왔습니다. 앱에 저장하려면 'YAML 생성 및 저장'을 누르세요."
                isError = false
            case .failure(let error):
                message = "불러오기 실패: \(error.localizedDescription)"
                isError = true
            }
        }
    }

    private func openYamlFile() {
        let panel = NSOpenPanel()
        panel.allowedContentTypes = [UTType(filenameExtension: "yaml"), UTType(filenameExtension: "yml")].compactMap { $0 }
        panel.allowsMultipleSelection = false
        panel.canChooseDirectories = false
        try? FileManager.default.createDirectory(at: appState.podsDirectoryURL, withIntermediateDirectories: true)
        panel.directoryURL = appState.podsDirectoryURL
        if panel.runModal() == .OK, let url = panel.url {
            loadYaml(url, label: "파일 '\(url.lastPathComponent)'")
        }
    }

    private func openClaudeChat() {
        let result = ProcessRunner.openClaudeInTerminal(folder: appState.dataRootPath, initialPrompt: "/podrouter-pod")
        if result.isSuccess {
            claudeOpenedAt = Date()
            message = "Terminal에서 Claude가 열렸습니다. 대화로 설정을 정하면 이 앱으로 돌아올 때 자동으로 채워집니다."
            isError = false
        } else {
            message = "Terminal을 열지 못했습니다: \(result.combinedOutput)"
            isError = true
        }
    }

    /// After a Claude chat, load the newest YAML written since the chat opened.
    private func loadNewYamlFromClaude() {
        guard let since = claudeOpenedAt, !appState.isExecuting else { return }
        guard let newest = appState.savedPodFiles().first, newest.modified > since else { return }
        claudeOpenedAt = Date()
        loadYaml(appState.yamlURL(forPodNamed: newest.name), label: "Claude가 만든 설정 '\(newest.name)'")
    }

    private func applyPreset() {
        appState.applyPreset()
        contextString = "\(appState.podContextTokens)"
        message = "기본 프리셋이 적용되었습니다."
        isError = false
    }

    private func saveYaml() {
        let name = appState.podName.trimmingCharacters(in: .whitespacesAndNewlines)

        // 1. Refuse protected names before the process starts
        if appState.isProtected(name) {
            message = "\(name)은(는) 보호 목록(PODROUTER_PROTECTED_PODS)에 있어 만들 수 없습니다."
            isError = true
            return
        }

        if name.isEmpty {
            message = "Pod 이름을 입력해주세요."
            isError = true
            return
        }

        // 2. Validate destination path
        let outURL = appState.currentYamlURL
        let resolvedPath = outURL.resolvingSymlinksInPath().path
        if resolvedPath.hasSuffix("/.env") ||
           resolvedPath.hasSuffix("/examples/a40.yaml") {
            message = "보호된 시스템 파일 경로에는 저장할 수 없습니다."
            isError = true
            return
        }

        // 3. Ensure pods directory exists
        do {
            try FileManager.default.createDirectory(at: appState.podsDirectoryURL, withIntermediateDirectories: true)
        } catch {
            message = "디렉토리 생성 실패: \(error.localizedDescription)"
            isError = true
            return
        }

        guard let scriptURL = appState.renderPlanScriptURL, appState.isVenvReady else {
            message = "Python 환경이 아직 준비되지 않았습니다. 잠시 후 다시 시도하세요."
            isError = true
            return
        }
        let pythonURL = URL(fileURLWithPath: appState.pythonPath)

        guard FileManager.default.fileExists(atPath: scriptURL.path) else {
            message = "render_plan.py 스크립트를 찾을 수 없습니다: \(scriptURL.path)"
            isError = true
            return
        }

        let contextTokens = appState.podContextTokens
        let arguments = [
            scriptURL.path,
            "render",
            "--name", name,
            "--wish", "맥 앱에서 저장한 공개 GGUF",
            "--gpu-id", appState.podGpuId,
            "--cloud", appState.podCloud,
            "--model", appState.podModel,
            "--context", "\(contextTokens)",
            "--reason", "podrouter preset",
            "--out", outURL.path
        ]

        let currentRepoPath = appState.dataRootPath
        appState.isExecuting = true
        message = "YAML 파일을 렌더링하고 있습니다..."
        isError = false

        DispatchQueue.global(qos: .userInitiated).async {
            let result = ProcessRunner.run(
                executableURL: pythonURL,
                arguments: arguments,
                currentDirectoryURL: URL(fileURLWithPath: currentRepoPath),
                includeKeys: true
            )

            DispatchQueue.main.async {
                appState.isExecuting = false
                outputLog = result.combinedOutput
                ActivityLog.append(action: "yaml", pod: name, ok: result.isSuccess,
                                   message: result.isSuccess ? "YAML 저장" : "YAML 생성 실패 (종료 코드: \(result.exitCode))",
                                   detail: result.combinedOutput)
                if result.isSuccess {
                    appState.rememberCurrentFields()
                    appState.refreshSavedPods()
                    selectedSaved = name
                    message = "YAML 파일이 성공적으로 생성되었습니다: \(outURL.path)"
                    isError = false
                } else {
                    message = "YAML 생성 실패 (종료 코드: \(result.exitCode))"
                    isError = true
                }
            }
        }
    }
}
