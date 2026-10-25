import SwiftUI

struct TimerView: View {
    @ObservedObject var appState: AppState

    @State private var entries: [ScheduleEntry] = []
    @State private var savedEntries: [ScheduleEntry] = []
    @State private var podStates: [String: [String: Any]] = [:]

    @State private var message: String?
    @State private var isError: Bool = false
    @State private var outputLog: String = ""

    private let weekdayLabels: [(index: Int, name: String)] = [
        (0, "월"), (1, "화"), (2, "수"), (3, "목"), (4, "금"), (5, "토"), (6, "일"),
    ]

    private var hasUnsavedChanges: Bool {
        strip(entries) != strip(savedEntries)
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                Text("5. 타이머 및 자동 스케줄 (Timer)")
                    .font(.title2)
                    .bold()

                // Required visible sentence
                HStack(spacing: 8) {
                    Image(systemName: "clock.badge.exclamationmark.fill")
                        .foregroundColor(.blue)
                    Text("로그인된 동안, 맥이 깨어 있을 때만 실행됩니다. 종료와 잠자기에서는 실행되지 않습니다.")
                        .font(.subheadline)
                        .fontWeight(.semibold)
                        .foregroundColor(.primary)
                }
                .padding(10)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(Color.blue.opacity(0.12))
                .cornerRadius(8)

                Text("Pod마다 켜고 끄는 요일과 시각을 따로 정합니다. 켤 때 Pod 수명은 '끄는 시각 + 15분'으로 맞춰져, 맥이 잠들어 있어도 그 뒤에는 스스로 삭제됩니다.")
                    .font(.callout)
                    .foregroundColor(.secondary)

                GroupBox(label: Label("예약된 Pod (\(entries.count))", systemImage: "calendar")) {
                    VStack(alignment: .leading, spacing: 12) {
                        if entries.isEmpty {
                            Text("예약된 Pod가 없습니다. 아래 'Pod 추가'로 저장된 Pod 설정을 고르세요.")
                                .foregroundColor(.secondary)
                                .padding(.vertical, 6)
                        }

                        ForEach($entries) { $entry in
                            entryCard($entry)
                        }

                        HStack {
                            Menu {
                                ForEach(unscheduledPods, id: \.self) { name in
                                    Button(name) { addEntry(name) }
                                }
                            } label: {
                                Label("Pod 추가", systemImage: "plus.circle")
                            }
                            .menuStyle(.borderlessButton)
                            .fixedSize()
                            .disabled(unscheduledPods.isEmpty)
                            .help(unscheduledPods.isEmpty ? "추가할 저장된 Pod 설정이 없습니다. [3. Pod 설정]에서 먼저 만드세요." : "저장된 Pod 설정을 골라 일정을 추가합니다.")

                            Spacer()
                        }
                    }
                    .padding(8)
                }

                HStack {
                    Button(action: saveSchedule) {
                        Label("스케줄 저장", systemImage: "square.and.arrow.down")
                    }
                    .buttonStyle(.borderedProminent)
                    .disabled(appState.isExecuting || !hasUnsavedChanges)

                    Button(action: installTimer) {
                        Label("타이머 설치", systemImage: "gearshape.arrow.triangle.2.circlepath")
                    }
                    .buttonStyle(.bordered)
                    .disabled(appState.isExecuting)

                    if hasUnsavedChanges {
                        Text("저장하지 않은 변경이 있습니다")
                            .font(.caption)
                            .foregroundColor(.orange)
                    }

                    Spacer()

                    Text("저장 파일: ~/Library/Application Support/podrouter/schedule.json")
                        .font(.caption)
                        .foregroundColor(.secondary)
                }

                Text("일정에서 Pod를 빼도 이미 켜져 있는 Pod는 꺼지지 않습니다. 전원 화면에서 끄세요. 타이머는 15초마다 이 파일을 다시 읽으므로, 한 번 설치한 뒤에는 저장만 해도 바로 적용됩니다.")
                    .font(.caption)
                    .foregroundColor(.secondary)

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
                    GroupBox(label: Label("실행 출력 로그", systemImage: "terminal")) {
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
                appState.refreshSavedPods()
                loadSchedule()
            }
        }
    }

    // MARK: - Entry card

    private var unscheduledPods: [String] {
        let used = Set(entries.map(\.podName))
        return appState.savedPodNames.filter { !used.contains($0) && !appState.isProtected($0) }
    }

    @ViewBuilder
    private func entryCard(_ entry: Binding<ScheduleEntry>) -> some View {
        let e = entry.wrappedValue
        let hasYaml = appState.savedPodNames.contains(e.podName)
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 10) {
                Toggle("", isOn: entry.enabled)
                    .toggleStyle(.switch)
                    .labelsHidden()
                    .help("끄면 이 Pod는 타이머가 건드리지 않습니다.")
                Text(e.podName)
                    .font(.system(.body, design: .monospaced))
                    .fontWeight(.semibold)
                    .foregroundColor(e.enabled ? .primary : .secondary)
                if !hasYaml {
                    Label("YAML 없음", systemImage: "exclamationmark.triangle.fill")
                        .font(.caption)
                        .foregroundColor(.orange)
                }
                if !e.enabled {
                    Text("일시 중지")
                        .font(.caption)
                        .foregroundColor(.secondary)
                }
                Spacer()
                stateText(for: e.podName)
                Button(role: .destructive) {
                    entries.removeAll { $0.id == e.id }
                } label: {
                    Image(systemName: "trash")
                }
                .buttonStyle(.borderless)
                .help("일정에서 빼기")
            }

            HStack(spacing: 6) {
                ForEach(weekdayLabels, id: \.index) { item in
                    let isSelected = e.weekdays.contains(item.index)
                    Button {
                        if isSelected {
                            entry.wrappedValue.weekdays.remove(item.index)
                        } else {
                            entry.wrappedValue.weekdays.insert(item.index)
                        }
                    } label: {
                        Text(item.name)
                            .font(.subheadline)
                            .fontWeight(isSelected ? .bold : .regular)
                            .frame(minWidth: 28, minHeight: 22)
                    }
                    .buttonStyle(.bordered)
                    .tint(isSelected ? .blue : .gray)
                }

                Spacer().frame(width: 16)

                Text("켜기").font(.subheadline)
                TextField("09:00", text: entry.onTime)
                    .textFieldStyle(.roundedBorder)
                    .frame(width: 64)
                Text("끄기").font(.subheadline)
                TextField("18:00", text: entry.offTime)
                    .textFieldStyle(.roundedBorder)
                    .frame(width: 64)
                Spacer()
            }
            .disabled(!e.enabled)
        }
        .padding(10)
        .background(Color.secondary.opacity(0.07))
        .cornerRadius(8)
    }

    @ViewBuilder
    private func stateText(for name: String) -> some View {
        if let state = podStates[name] {
            let action = state["last_action"] as? String ?? "none"
            if let err = state["last_error"] as? String, !err.isEmpty {
                Text("최근 오류: \(err)")
                    .font(.caption)
                    .foregroundColor(.red)
                    .lineLimit(1)
                    .help(err)
            } else {
                Text("최근 동작: \(action == "start" ? "켜기" : action == "stop" ? "끄기" : "없음")")
                    .font(.caption)
                    .foregroundColor(.secondary)
            }
        }
    }

    // MARK: - Load and save

    /// Compare entries without their random ids.
    private func strip(_ list: [ScheduleEntry]) -> [String] {
        list.map { "\($0.podName)|\($0.enabled)|\($0.weekdays.sorted())|\($0.onTime)|\($0.offTime)" }
    }

    private func addEntry(_ name: String) {
        entries.append(ScheduleEntry(podName: name, configPath: appState.yamlURL(forPodNamed: name).path))
    }

    private func loadSchedule() {
        let loaded = ScheduleFile.load(from: appState.scheduleFileURL)
        entries = loaded
        savedEntries = loaded
        loadPodStates()
    }

    private func loadPodStates() {
        let stateURL = appState.scheduleFileURL.deletingLastPathComponent().appendingPathComponent("state.json")
        guard let data = try? Data(contentsOf: stateURL),
              let json = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] else {
            podStates = [:]
            return
        }
        if let pods = json["pods"] as? [String: [String: Any]] {
            podStates = pods
        } else if let only = entries.first, entries.count == 1 {
            podStates = [only.podName: json]
        } else {
            podStates = [:]
        }
    }

    private func isValidTime(_ time: String) -> Bool {
        let parts = time.split(separator: ":")
        guard parts.count == 2, parts[0].count == 2, parts[1].count == 2,
              let h = Int(parts[0]), let m = Int(parts[1]),
              (0...23).contains(h), (0...59).contains(m) else {
            return false
        }
        return true
    }

    private func saveSchedule() {
        var cleaned: [ScheduleEntry] = []
        for var e in entries {
            e.onTime = e.onTime.trimmingCharacters(in: .whitespacesAndNewlines)
            e.offTime = e.offTime.trimmingCharacters(in: .whitespacesAndNewlines)
            if appState.isProtected(e.podName) {
                return fail("\(e.podName)은(는) 보호 목록(PODROUTER_PROTECTED_PODS)에 있어 예약할 수 없습니다.")
            }
            if e.weekdays.isEmpty {
                return fail("\(e.podName): 요일을 하나 이상 고르세요.")
            }
            if !isValidTime(e.onTime) || !isValidTime(e.offTime) {
                return fail("\(e.podName): 시간 형식이 올바르지 않습니다 (HH:MM, 00:00~23:59).")
            }
            if !appState.savedPodNames.contains(e.podName) {
                return fail("\(e.podName): 저장된 YAML이 없습니다. [3. Pod 설정]에서 먼저 저장하거나 일정에서 빼세요.")
            }
            e.configPath = appState.yamlURL(forPodNamed: e.podName).path
            cleaned.append(e)
        }

        do {
            try ScheduleFile.save(cleaned, to: appState.scheduleFileURL)
        } catch {
            return fail("스케줄 저장 실패: \(error.localizedDescription)")
        }

        let removed = Set(savedEntries.map(\.podName)).subtracting(cleaned.map(\.podName))
        entries = cleaned
        savedEntries = cleaned
        let summary = cleaned.map { e in
            let days = e.weekdays.sorted().compactMap { d in weekdayLabels.first { $0.index == d }?.name }.joined()
            return "\(e.podName)\(e.enabled ? "" : "(중지)") \(days) \(e.onTime)~\(e.offTime)"
        }.joined(separator: ", ")
        ActivityLog.append(action: "schedule", pod: cleaned.map(\.podName).joined(separator: ","), ok: true,
                           message: cleaned.isEmpty ? "일정 비움" : "일정 저장: \(summary)")
        var text = "스케줄을 저장했습니다 (Pod \(cleaned.count)개)."
        if !removed.isEmpty {
            text += " 일정에서 뺀 \(removed.sorted().joined(separator: ", "))는 켜져 있으면 전원 화면에서 끄세요."
        }
        message = text
        isError = false
    }

    private func fail(_ text: String) {
        message = text
        isError = true
    }

    private func installTimer() {
        let isDirect = appState.isGpuUpDirectExecutable
        let execURL = URL(fileURLWithPath: appState.gpuUpCLIPath)

        // Command: podrouter schedule install --repo <repo>
        var args: [String] = []
        if !isDirect {
            args.append(contentsOf: ["-m", "podrouter"])
        }
        args.append(contentsOf: ["schedule", "install"])
        // A clone uses its own .venv and .env. The installed app uses the running
        // Python and ~/podrouter/.env (PODROUTER_HOME), so no --repo.
        if !appState.isInstalledMode {
            args.append(contentsOf: ["--repo", appState.dataRootPath])
        }

        let currentRepoPath = appState.dataRootPath
        appState.isExecuting = true
        message = "타이머 설치 명령을 실행 중입니다..."
        isError = false
        outputLog = ""

        DispatchQueue.global(qos: .userInitiated).async {
            let result = ProcessRunner.run(
                executableURL: execURL,
                arguments: args,
                currentDirectoryURL: URL(fileURLWithPath: currentRepoPath),
                includeKeys: true
            )

            DispatchQueue.main.async {
                appState.isExecuting = false
                outputLog = result.combinedOutput
                ActivityLog.append(action: "install", pod: "", ok: result.isSuccess,
                                   message: result.isSuccess ? "타이머 설치 및 등록 완료" : "타이머 설치 실패 (종료 코드: \(result.exitCode))",
                                   detail: result.combinedOutput)
                if result.isSuccess {
                    message = "타이머가 성공적으로 설치되었습니다."
                    isError = false
                } else {
                    message = "타이머 설치 실패 (종료 코드: \(result.exitCode))"
                    isError = true
                }
            }
        }
    }
}
