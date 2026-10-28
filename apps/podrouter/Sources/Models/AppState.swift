import Foundation
import SwiftUI

@MainActor
public final class AppState: ObservableObject {
    private let lastPodKey = "podrouter.lastPodFields"

    @Published public var repoPath: String {
        didSet {
            // Only remember a manual choice that differs from the detected folder.
            if repoPath == RepoLocator.detected || repoPath.isEmpty {
                UserDefaults.standard.removeObject(forKey: RepoLocator.defaultsKey)
            } else {
                UserDefaults.standard.set(repoPath, forKey: RepoLocator.defaultsKey)
            }
            runtime = Runtime.resolve(repoPath: repoPath)
            prepareRuntime()
            refreshKeyStatus()
            refreshSavedPods()
        }
    }

    /// Clone or installed layout. nil when neither a clone nor a bundled podrouter is found.
    @Published private(set) var runtime: Runtime?

    public enum SetupState: Equatable { case idle, working, ready, failed(String) }
    /// Python environment and launcher preparation, run on every launch.
    @Published public var setupState: SetupState = .idle

    @Published public var runpodKeyStatus: String = "없음"
    @Published public var tsKeyStatus: String = "없음"
    @Published public var hfKeyStatus: String = "없음"
    @Published public var cfKeyStatus: String = "없음"
    @Published public var modelKeyStatus: String = "없음"

    /// Public address mode saved as PODROUTER_TUNNEL in .env: none / tailscale / cloudflare.
    /// Empty means the old rule (Tailscale when TS_AUTHKEY is saved).
    @Published public var tunnelMode: String = ""
    @Published public var cfHostname: String = ""
    @Published public var tsTailnet: String = ""
    /// Names from PODROUTER_PROTECTED_PODS. Empty by default.
    @Published public var protectedPods: [String] = []

    // Pod fields
    @Published public var podName: String = "qwen38-27b"
    @Published public var podModel: String = "bartowski/orcarouter_Qwen3.8-27B-Uncensored-GGUF:Q4_K_M"
    @Published public var podGpuId: String = "NVIDIA A40"
    @Published public var podCloud: String = "SECURE"
    @Published public var podContextTokens: Int = 196608
    @Published public var podTTL: String = "3h"

    /// Names of YAML files saved in the pods folder, newest first.
    @Published public var savedPodNames: [String] = []

    // Console output for action logs
    @Published public var lastOutput: String = ""
    @Published public var isExecuting: Bool = false

    public init() {
        let path = RepoLocator.resolve()
        self.repoPath = path
        self.runtime = Runtime.resolve(repoPath: path)
        prepareRuntime()
        refreshKeyStatus()
        restoreLastPodFields()
        migrateLegacyPods()
        refreshSavedPods()
    }

    /// The app knows where podrouter lives (a clone, or the bundled copy).
    public var isRepoValid: Bool { runtime != nil }

    public var isInstalledMode: Bool { runtime?.mode == .installed }

    /// The Python environment is ready to run podrouter.
    public var isVenvReady: Bool { runtime?.isPythonReady ?? false }

    /// Folder with `.env` and `pods/`; Claude opens here.
    public var dataRootPath: String { runtime?.dataRoot.path ?? "" }

    public var pythonPath: String { runtime?.python.path ?? "" }

    /// podrouter always runs as `python -m podrouter`.
    public var gpuUpCLIPath: String { pythonPath }
    public var isGpuUpDirectExecutable: Bool { false }

    var renderPlanScriptURL: URL? { runtime?.renderPlanScript }

    /// Make the venv (installed mode) and the Claude launcher in the background.
    public func prepareRuntime() {
        guard let rt = runtime else {
            setupState = .idle
            return
        }
        setupState = .working
        DispatchQueue.global(qos: .userInitiated).async {
            let error = PythonSetup.prepare(rt)
            DispatchQueue.main.async {
                guard self.runtime == rt else { return }
                self.setupState = error.map { .failed($0) } ?? .ready
                self.refreshSavedPods()
            }
        }
    }

    /// Saved Pod YAML files: `<clone>/pods` (ignored by git) or `~/podrouter/pods`.
    public var podsDirectoryURL: URL {
        runtime?.podsDir ?? Runtime.installedDataRoot.appendingPathComponent("pods")
    }

    /// Where older app versions saved YAML files.
    nonisolated static var legacyPodsDirectoryURL: URL {
        let appSupport = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first!
        return appSupport.appendingPathComponent("podrouter/pods")
    }

    /// Move YAML files from the old Application Support folder into `<repo>/pods`
    /// and point the timer at the new path. A name that already exists is left in place.
    public func migrateLegacyPods() {
        let fm = FileManager.default
        let old = AppState.legacyPodsDirectoryURL
        guard let files = try? fm.contentsOfDirectory(at: old, includingPropertiesForKeys: nil, options: [.skipsHiddenFiles]),
              files.contains(where: { $0.pathExtension == "yaml" }) else { return }
        try? fm.createDirectory(at: podsDirectoryURL, withIntermediateDirectories: true)
        var moved: [String: String] = [:]
        for file in files where file.pathExtension == "yaml" {
            let target = podsDirectoryURL.appendingPathComponent(file.lastPathComponent)
            if fm.fileExists(atPath: target.path) { continue }
            if (try? fm.moveItem(at: file, to: target)) != nil {
                moved[file.path] = target.path
            }
        }
        if let left = try? fm.contentsOfDirectory(atPath: old.path), left.filter({ !$0.hasPrefix(".") }).isEmpty {
            try? fm.removeItem(at: old)
        }
        rewriteScheduleConfigPath(moved)
    }

    private func rewriteScheduleConfigPath(_ moved: [String: String]) {
        guard !moved.isEmpty else { return }
        var entries = ScheduleFile.load(from: scheduleFileURL)
        var changed = false
        for idx in entries.indices {
            if let newPath = moved[entries[idx].configPath] {
                entries[idx].configPath = newPath
                changed = true
            }
        }
        if changed {
            try? ScheduleFile.save(entries, to: scheduleFileURL)
        }
    }

    /// Delete one saved YAML. Returns an error message, or nil on success.
    public func deleteSavedPod(named name: String) -> String? {
        let url = yamlURL(forPodNamed: name)
        guard url.deletingLastPathComponent().standardizedFileURL == podsDirectoryURL.standardizedFileURL else {
            return "pods 폴더 밖의 파일은 지울 수 없습니다."
        }
        do {
            try FileManager.default.removeItem(at: url)
        } catch {
            return error.localizedDescription
        }
        refreshSavedPods()
        return nil
    }

    /// Paths of the YAML files the timer uses.
    public var scheduledConfigPaths: Set<String> {
        Set(ScheduleFile.load(from: scheduleFileURL).map(\.configPath))
    }

    public var currentYamlURL: URL {
        podsDirectoryURL.appendingPathComponent("\(podName.trimmingCharacters(in: .whitespacesAndNewlines)).yaml")
    }

    public var currentYamlExists: Bool {
        FileManager.default.fileExists(atPath: currentYamlURL.path)
    }

    public var scheduleFileURL: URL {
        let appSupport = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first!
        return appSupport.appendingPathComponent("podrouter/schedule.json")
    }

    public var canPowerOnOrOff: Bool {
        runpodKeyStatus == "저장됨" && currentYamlExists
    }

    public func refreshKeyStatus() {
        runpodKeyStatus = SecretStore.keyExists(account: SecretStore.runpodAccount) ? "저장됨" : "없음"
        tsKeyStatus = SecretStore.keyExists(account: SecretStore.tsAccount) ? "저장됨" : "없음"
        hfKeyStatus = SecretStore.keyExists(account: SecretStore.hfAccount) ? "저장됨" : "없음"
        cfKeyStatus = SecretStore.keyExists(account: SecretStore.cfTokenAccount) ? "저장됨" : "없음"
        modelKeyStatus = SecretStore.keyExists(account: SecretStore.modelApiAccount) ? "저장됨" : "없음"
        tunnelMode = SecretStore.loadKeyForChildProcess(account: SecretStore.tunnelModeKey)?.lowercased() ?? ""
        cfHostname = SecretStore.loadKeyForChildProcess(account: SecretStore.cfHostnameKey) ?? ""
        tsTailnet = SecretStore.loadKeyForChildProcess(account: SecretStore.tsTailnetKey) ?? ""
        protectedPods = (SecretStore.loadKeyForChildProcess(account: SecretStore.protectedPodsKey) ?? "")
            .split(separator: ",")
            .map { $0.trimmingCharacters(in: .whitespaces) }
            .filter { !$0.isEmpty }
    }

    /// The mode podrouter will actually use, after applying the empty-value rule.
    /// True for names listed in PODROUTER_PROTECTED_PODS.
    public func isProtected(_ name: String) -> Bool {
        protectedPods.contains(name.trimmingCharacters(in: .whitespacesAndNewlines))
    }

    public var effectiveTunnelMode: String {
        if ["none", "tailscale", "cloudflare"].contains(tunnelMode) { return tunnelMode }
        return tsKeyStatus == "저장됨" ? "tailscale" : "none"
    }

    // MARK: - Saved pod settings

    public struct PodFields: Codable, Equatable {
        public var name: String
        public var model: String
        public var gpu_id: String
        public var cloud: String
        public var context_tokens: Int
        public var ttl: String

        enum CodingKeys: String, CodingKey {
            case name
            case model
            case gpu_id
            case cloud
            case context_tokens
            case ttl
        }

        public init(
            name: String,
            model: String,
            gpu_id: String,
            cloud: String,
            context_tokens: Int,
            ttl: String = "3h"
        ) {
            self.name = name
            self.model = model
            self.gpu_id = gpu_id
            self.cloud = cloud
            self.context_tokens = context_tokens
            let trimmed = ttl.trimmingCharacters(in: .whitespacesAndNewlines)
            self.ttl = trimmed.isEmpty ? "3h" : trimmed
        }

        public init(from decoder: Decoder) throws {
            let container = try decoder.container(keyedBy: CodingKeys.self)
            self.name = try container.decode(String.self, forKey: .name)
            self.model = try container.decode(String.self, forKey: .model)
            self.gpu_id = try container.decode(String.self, forKey: .gpu_id)
            self.cloud = try container.decode(String.self, forKey: .cloud)
            self.context_tokens = try container.decode(Int.self, forKey: .context_tokens)
            let decodedTTL = try container.decodeIfPresent(String.self, forKey: .ttl)?
                .trimmingCharacters(in: .whitespacesAndNewlines)
            if let decodedTTL, !decodedTTL.isEmpty {
                self.ttl = decodedTTL
            } else {
                self.ttl = "3h"
            }
        }

        public func encode(to encoder: Encoder) throws {
            var container = encoder.container(keyedBy: CodingKeys.self)
            try container.encode(name, forKey: .name)
            try container.encode(model, forKey: .model)
            try container.encode(gpu_id, forKey: .gpu_id)
            try container.encode(cloud, forKey: .cloud)
            try container.encode(context_tokens, forKey: .context_tokens)
            try container.encode(ttl, forKey: .ttl)
        }
    }

    public var currentFields: PodFields {
        PodFields(
            name: podName,
            model: podModel,
            gpu_id: podGpuId,
            cloud: podCloud,
            context_tokens: podContextTokens,
            ttl: podTTL
        )
    }

    public func applyFields(_ fields: PodFields) {
        podName = fields.name
        podModel = fields.model
        podGpuId = fields.gpu_id
        podCloud = fields.cloud
        podContextTokens = fields.context_tokens
        let trimmedTTL = fields.ttl.trimmingCharacters(in: .whitespacesAndNewlines)
        podTTL = trimmedTTL.isEmpty ? "3h" : trimmedTTL
        rememberCurrentFields()
    }

    /// Remember the form so the next launch starts with the last settings.
    public func rememberCurrentFields() {
        if let data = try? JSONEncoder().encode(currentFields) {
            UserDefaults.standard.set(data, forKey: lastPodKey)
        }
    }

    private func restoreLastPodFields() {
        guard let data = UserDefaults.standard.data(forKey: lastPodKey),
              let fields = try? JSONDecoder().decode(PodFields.self, from: data) else { return }
        podName = fields.name
        podModel = fields.model
        podGpuId = fields.gpu_id
        podCloud = fields.cloud
        podContextTokens = fields.context_tokens
        let trimmedTTL = fields.ttl.trimmingCharacters(in: .whitespacesAndNewlines)
        podTTL = trimmedTTL.isEmpty ? "3h" : trimmedTTL
    }

    public func yamlURL(forPodNamed name: String) -> URL {
        podsDirectoryURL.appendingPathComponent("\(name).yaml")
    }

    /// YAML files in the pods folder with their modification dates, newest first.
    public func savedPodFiles() -> [(name: String, modified: Date)] {
        let fm = FileManager.default
        guard let urls = try? fm.contentsOfDirectory(
            at: podsDirectoryURL,
            includingPropertiesForKeys: [.contentModificationDateKey],
            options: [.skipsHiddenFiles]
        ) else { return [] }
        return urls
            .filter { $0.pathExtension == "yaml" }
            .map { url in
                let date = (try? url.resourceValues(forKeys: [.contentModificationDateKey]))?.contentModificationDate ?? .distantPast
                return (url.deletingPathExtension().lastPathComponent, date)
            }
            .sorted { $0.modified > $1.modified }
    }

    public func refreshSavedPods() {
        savedPodNames = savedPodFiles().map(\.name)
    }

    /// Read form fields from a YAML via `render_plan.py inspect`. Runs off the main thread.
    public func readFields(from url: URL, completion: @escaping (Result<PodFields, Error>) -> Void) {
        guard let rt = runtime else {
            completion(.failure(NSError(domain: "podrouter", code: 1, userInfo: [NSLocalizedDescriptionKey: "podrouter 위치를 찾지 못했습니다."])))
            return
        }
        let script = rt.renderPlanScript
        let python = rt.python
        let repo = rt.dataRoot
        DispatchQueue.global(qos: .userInitiated).async {
            let result = ProcessRunner.run(
                executableURL: python,
                arguments: [script.path, "inspect", "--file", url.path],
                currentDirectoryURL: repo,
                includeKeys: false
            )
            let outcome: Result<PodFields, Error>
            if result.isSuccess,
               let line = result.combinedOutput.split(separator: "\n").first(where: { $0.hasPrefix("{") }),
               let fields = try? JSONDecoder().decode(PodFields.self, from: Data(line.utf8)) {
                outcome = .success(fields)
            } else {
                let text = result.combinedOutput.trimmingCharacters(in: .whitespacesAndNewlines)
                outcome = .failure(NSError(domain: "podrouter", code: Int(result.exitCode), userInfo: [
                    NSLocalizedDescriptionKey: text.isEmpty ? "YAML을 읽지 못했습니다." : text
                ]))
            }
            DispatchQueue.main.async { completion(outcome) }
        }
    }

    public func applyPreset() {
        podName = "qwen38-27b"
        podModel = "bartowski/orcarouter_Qwen3.8-27B-Uncensored-GGUF:Q4_K_M"
        podGpuId = "NVIDIA A40"
        podCloud = "SECURE"
        podContextTokens = 196608
        podTTL = "3h"
    }

    /// Validate duration text like `3h`, `30m`, or `1h30m`.
    /// Returns nil if valid, or an error message in Korean.
    nonisolated public static func validateDuration(_ raw: String) -> String? {
        let trimmed = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        if trimmed.isEmpty {
            return "자동 삭제까지 시간 (TTL)을 입력해주세요. (예: 3h, 30m, 1h30m)"
        }

        guard let regex = try? NSRegularExpression(pattern: "^(?:(\\d+)h)?(?:(\\d+)m)?$") else {
            return "정규표현식 오류"
        }
        let nsString = trimmed as NSString
        guard let match = regex.firstMatch(in: trimmed, range: NSRange(location: 0, length: nsString.length)),
              match.range.location == 0, match.range.length == nsString.length else {
            return "TTL은 3h, 30m, 1h30m과 같은 형식(양의 시간·분)이어야 합니다."
        }

        let hoursRange = match.range(at: 1)
        let minutesRange = match.range(at: 2)

        if hoursRange.location == NSNotFound && minutesRange.location == NSNotFound {
            return "TTL은 3h, 30m, 1h30m과 같은 형식(양의 시간·분)이어야 합니다."
        }

        var hasPositiveDigit = false

        if hoursRange.location != NSNotFound {
            let hStr = nsString.substring(with: hoursRange)
            if hStr.contains(where: { $0 >= "1" && $0 <= "9" }) {
                hasPositiveDigit = true
            }
        }

        if minutesRange.location != NSNotFound {
            let mStr = nsString.substring(with: minutesRange)
            if mStr.contains(where: { $0 >= "1" && $0 <= "9" }) {
                hasPositiveDigit = true
            }
        }

        if !hasPositiveDigit {
            return "TTL은 0보다 커야 합니다."
        }

        return nil
    }
}
