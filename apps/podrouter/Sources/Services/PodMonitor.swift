import Foundation

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

enum PowerState {
    case checking, off, starting(String), ready, busy(String), unknown
}

/// Pod status and power commands shared by the window and the menu bar.
/// Checks every 15 seconds while the app runs, even with the window closed.
@MainActor
final class PodMonitor: ObservableObject {
    @Published private(set) var runtimes: [String: PodRuntime] = [:]
    @Published private(set) var lastChecked: Date?
    @Published private(set) var isChecking = false
    @Published private(set) var listError: String?
    /// Pod name -> "켜는 중" / "끄는 중" while a command runs.
    @Published private(set) var busy: [String: String] = [:]
    @Published var statusMessage: String?
    @Published var isError = false
    /// The newest power command and its output (command line first), kept on disk.
    @Published private(set) var lastRun: RunRecord?
    @Published private(set) var lastRunText = ""

    private let appState: AppState
    private var timer: Timer?
    private var followTimer: Timer?
    /// Commands still running, by pod name.
    private var active: [String: RunRecord] = [:]
    /// Last state text per pod, to log only changes.
    private var lastStates: [String: String]?
    private var lastListError: String?

    init(appState: AppState) {
        self.appState = appState
        timer = Timer.scheduledTimer(withTimeInterval: 15, repeats: true) { [weak self] _ in
            Task { @MainActor in self?.refresh() }
        }
        resumeRuns()
        // The Python environment may still be preparing; try again shortly.
        DispatchQueue.main.asyncAfter(deadline: .now() + 2) { [weak self] in self?.refresh() }
    }

    func state(of name: String) -> PowerState {
        if let label = busy[name] { return .busy(label) }
        if listError != nil && lastChecked == nil { return .unknown }
        guard lastChecked != nil else { return .checking }
        guard let rt = runtimes[name] else { return .off }
        if rt.healthy { return .ready }
        return .starting(rt.state)
    }

    /// Saved YAML names plus running pods without a YAML, minus protected names.
    func visibleNames(query: String = "") -> [String] {
        var names = appState.savedPodNames
        for name in runtimes.keys.sorted() where !names.contains(name) {
            names.append(name)
        }
        names.removeAll { appState.isProtected($0) }
        let q = query.trimmingCharacters(in: .whitespaces).lowercased()
        return q.isEmpty ? names : names.filter { $0.lowercased().contains(q) }
    }

    var readyCount: Int {
        runtimes.values.filter { $0.healthy && !appState.isProtected($0.name) }.count
    }

    var startingCount: Int {
        runtimes.values.filter { !$0.healthy && !appState.isProtected($0.name) }.count
    }

    /// SF Symbol for the menu bar: on, starting, or off.
    var menuBarIcon: String {
        if !busy.isEmpty || startingCount > 0 { return "bolt.badge.clock" }
        if readyCount > 0 { return "bolt.fill" }
        return "bolt.slash"
    }

    func canPowerOn(_ name: String) -> Bool {
        guard case .off = state(of: name) else { return false }
        return appState.savedPodNames.contains(name) && appState.runpodKeyStatus == "저장됨"
    }

    func canPowerOff(_ name: String) -> Bool {
        runtimes[name] != nil && busy[name] == nil
    }

    // MARK: - Commands

    private func command(_ tail: [String]) -> (URL, [String]) {
        var args: [String] = appState.isGpuUpDirectExecutable ? [] : ["-m", "podrouter"]
        args.append(contentsOf: tail)
        return (URL(fileURLWithPath: appState.gpuUpCLIPath), args)
    }

    func refresh() {
        guard !isChecking, appState.runpodKeyStatus == "저장됨", appState.isVenvReady else { return }
        let (exec, args) = command(["ls", "--json", "--api-key", "${RUNPOD_API_KEY}"])
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
                self.isChecking = false
                if let pods = decoded {
                    var map: [String: PodRuntime] = [:]
                    for pod in pods { map[pod.name] = pod }
                    self.runtimes = map
                    self.lastChecked = Date()
                    self.listError = nil
                    self.logStateChanges(map)
                } else {
                    let text = result.combinedOutput.trimmingCharacters(in: .whitespacesAndNewlines)
                    let error = text.isEmpty ? "종료 코드 \(result.exitCode)" : String(text.suffix(300))
                    self.listError = error
                    if self.lastListError == nil {
                        ActivityLog.append(action: "check", pod: "", ok: false,
                                           message: "상태 확인 실패",
                                           detail: "$ \(self.display(args))\n\n\(result.combinedOutput)")
                    }
                }
                self.lastListError = self.listError
            }
        }
    }

    func powerOn(_ name: String) {
        guard !appState.isProtected(name), canPowerOn(name) else { return }
        let yaml = appState.yamlURL(forPodNamed: name).path
        let (exec, args) = command(["up", yaml, "--wait", "false", "--api-key", "${RUNPOD_API_KEY}"])
        launch(name: name, action: "start", exec: exec, args: args)
    }

    func powerOff(_ name: String) {
        guard !appState.isProtected(name), canPowerOff(name) else { return }
        // Never pass --all.
        let (exec, args) = command(["down", name, "--api-key", "${RUNPOD_API_KEY}"])
        launch(name: name, action: "stop", exec: exec, args: args)
    }

    /// `podrouter up ...` as the user would type it. Keys stay as `${NAME}`.
    private func display(_ args: [String]) -> String {
        let tail = args.first == "-m" ? Array(args.dropFirst(2)) : args
        let quoted = tail.map { $0.contains(" ") ? "'\($0)'" : $0 }
        return (["podrouter"] + quoted).joined(separator: " ")
    }

    private func launch(name: String, action: String, exec: URL, args: [String]) {
        let label = action == "start" ? "켜는 중" : "끄는 중"
        let cwd = URL(fileURLWithPath: appState.dataRootPath)
        let shown = display(args)
        busy[name] = label
        statusMessage = "'\(name)' \(label)..."
        isError = false
        DispatchQueue.global(qos: .userInitiated).async {
            let started = RunStore.start(pod: name, action: action, exec: exec, args: args, display: shown, cwd: cwd)
            DispatchQueue.main.async {
                switch started {
                case .success(let record):
                    ActivityLog.append(action: action, pod: name, ok: true,
                                       message: "'\(name)' \(record.label) 시작",
                                       detail: "$ \(shown)")
                    self.active[name] = record
                    self.lastRun = record
                    self.followRuns()
                case .failure(let error):
                    self.busy[name] = nil
                    self.statusMessage = "'\(name)' \(action == "start" ? "켜기" : "끄기") 실패: \(error.message)"
                    self.isError = true
                }
            }
        }
    }

    /// After a restart: keep following commands that still run, and record
    /// ones that finished while the app was closed.
    private func resumeRuns() {
        let records = RunStore.all()
        lastRun = records.first
        for record in records where !record.recorded {
            busy[record.pod] = record.busyLabel
            active[record.pod] = record
        }
        followRuns()
    }

    private func followRuns() {
        pollRuns()
        guard followTimer == nil, !active.isEmpty else { return }
        followTimer = Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { [weak self] _ in
            Task { @MainActor in self?.pollRuns() }
        }
    }

    /// Read new output once a second and finish commands that ended.
    private func pollRuns() {
        if let run = lastRun {
            lastRunText = RunStore.read(run).text
        }
        for (name, record) in active {
            let (text, code) = RunStore.read(record)
            if code == nil && RunStore.isAlive(record) { continue }
            finish(name: name, record: record, text: text, code: code)
        }
        if active.isEmpty {
            followTimer?.invalidate()
            followTimer = nil
        }
    }

    private func finish(name: String, record: RunRecord, text: String, code: Int32?) {
        var done = record
        done.recorded = true
        done.exitCode = code ?? -1
        RunStore.finalize(done, text: text)
        RunStore.save(done)
        active[name] = nil
        busy[name] = nil
        if lastRun?.id == done.id {
            lastRun = done
            lastRunText = text
        }

        let ok = code == 0
        let what = record.label
        let message: String
        if ok {
            message = record.action == "start"
                ? "'\(name)' 켜기 요청 완료. 모델 로딩이 끝나면 '켜짐 · 정상'으로 바뀝니다."
                : "'\(name)' 종료 완료."
        } else if let code = code {
            message = "'\(name)' \(what) 실패 (종료 코드: \(code))\(Self.reason(text).map { " · \($0)" } ?? "")"
        } else {
            message = "'\(name)' \(what)이 중간에 멈췄습니다."
        }
        ActivityLog.append(action: record.action, pod: name, ok: ok, message: message, detail: text)
        statusMessage = message
        isError = !ok
        refresh()
    }

    /// The last `error:` line, which says why podrouter stopped.
    private static func reason(_ text: String) -> String? {
        let line = text.split(separator: "\n").last { $0.hasPrefix("error:") }
        return line.map { String($0.dropFirst("error:".count)).trimmingCharacters(in: .whitespaces) }
    }

    /// Log a line when a pod turns on, becomes ready, or goes away.
    private func logStateChanges(_ map: [String: PodRuntime]) {
        var now: [String: String] = [:]
        for (name, rt) in map where !appState.isProtected(name) {
            now[name] = rt.healthy ? "켜짐 · 정상" : "시작 중 (\(rt.state))"
        }
        defer { lastStates = now }
        guard let before = lastStates else { return }
        for name in Set(before.keys).union(now.keys).sorted() {
            let old = before[name] ?? "꺼짐"
            let new = now[name] ?? "꺼짐"
            guard old != new else { continue }
            var detail = "\(old) → \(new)"
            if let rt = map[name] {
                detail += "\npod_id=\(rt.pod_id) state=\(rt.state) healthy=\(rt.healthy) $\(String(format: "%.2f", rt.hourly_price))/시간"
                if !rt.publicURL.isEmpty { detail += "\n주소: \(rt.publicURL)" }
            }
            ActivityLog.append(action: "check", pod: name, ok: true, message: "상태 바뀜: \(new)", detail: detail)
        }
    }
}
