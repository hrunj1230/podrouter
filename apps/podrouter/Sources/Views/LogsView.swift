import AppKit
import Combine
import SwiftUI

struct LogsView: View {
    @ObservedObject var appState: AppState

    private enum Filter: String, CaseIterable, Identifiable {
        case all = "전체"
        case timer = "타이머"
        case app = "앱"
        case failed = "실패만"
        var id: String { rawValue }
    }

    @State private var events: [ActivityEvent] = []
    @State private var filter: Filter = .all
    @State private var query: String = ""
    @State private var timerState: [String: Any] = [:]
    @State private var confirmClear = false

    private let refreshTimer = Timer.publish(every: 10, on: .main, in: .common).autoconnect()

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("6. 로그 (Logs)")
                .font(.title2)
                .bold()

            Text("타이머와 앱에서 실행한 켜기·끄기 기록입니다. 키 값은 가려서 저장됩니다. 10초마다 새로 읽습니다.")
                .font(.body)
                .foregroundColor(.secondary)

            GroupBox(label: Label("타이머 마지막 실행", systemImage: "clock.arrow.circlepath")) {
                HStack(spacing: 16) {
                    Text("시각: \(formatTime(timerState["last_tick"] as? String))")
                    Text("동작: \(actionText(timerState["last_action"] as? String ?? "-"))")
                    if let err = timerState["last_error"] as? String, !err.isEmpty {
                        Text("오류: \(err)")
                            .foregroundColor(.red)
                            .lineLimit(2)
                    } else {
                        Text("오류 없음").foregroundColor(.green)
                    }
                    Spacer()
                }
                .font(.caption)
                .padding(6)
            }

            HStack {
                Picker("", selection: $filter) {
                    ForEach(Filter.allCases) { Text($0.rawValue).tag($0) }
                }
                .pickerStyle(.segmented)
                .frame(maxWidth: 300)

                TextField("Pod 이름이나 내용 검색", text: $query)
                    .textFieldStyle(.roundedBorder)
                    .frame(maxWidth: 240)

                Spacer()

                Button { reload() } label: { Image(systemName: "arrow.clockwise") }
                    .help("새로고침")
                Button("Finder에서 보기") {
                    try? FileManager.default.createDirectory(at: ActivityLog.directoryURL, withIntermediateDirectories: true)
                    NSWorkspace.shared.open(ActivityLog.directoryURL)
                }
                Button("비우기", role: .destructive) { confirmClear = true }
                    .disabled(events.isEmpty)
            }

            if filtered.isEmpty {
                Text(events.isEmpty ? "아직 기록이 없습니다. 켜기·끄기를 하거나 타이머가 동작하면 여기에 쌓입니다." : "조건에 맞는 기록이 없습니다.")
                    .foregroundColor(.secondary)
                    .padding(.vertical, 8)
                Spacer()
            } else {
                List(filtered) { event in
                    row(event)
                }
                .listStyle(.inset)
            }
        }
        .padding()
        .onAppear { reload() }
        .onReceive(refreshTimer) { _ in reload() }
        .confirmationDialog("로그를 모두 지울까요?", isPresented: $confirmClear, titleVisibility: .visible) {
            Button("모두 지우기", role: .destructive) {
                ActivityLog.clear()
                reload()
            }
            Button("취소", role: .cancel) {}
        }
    }

    private var filtered: [ActivityEvent] {
        let q = query.trimmingCharacters(in: .whitespaces).lowercased()
        return events.filter { e in
            switch filter {
            case .all: break
            case .timer: if e.source != "timer" { return false }
            case .app: if e.source != "app" { return false }
            case .failed: if e.ok { return false }
            }
            if q.isEmpty { return true }
            return e.pod.lowercased().contains(q) || e.message.lowercased().contains(q) || e.detail.lowercased().contains(q)
        }
    }

    @ViewBuilder
    private func row(_ e: ActivityEvent) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 8) {
                Image(systemName: e.ok ? "checkmark.circle.fill" : "xmark.octagon.fill")
                    .foregroundColor(e.ok ? .green : .red)
                Text(formatTime(e.time))
                    .font(.system(.caption, design: .monospaced))
                    .foregroundColor(.secondary)
                Text(e.source == "timer" ? "타이머" : "앱")
                    .font(.caption2)
                    .padding(.horizontal, 6)
                    .padding(.vertical, 1)
                    .background((e.source == "timer" ? Color.purple : Color.blue).opacity(0.15))
                    .cornerRadius(4)
                Text(actionText(e.action))
                    .font(.caption)
                    .fontWeight(.medium)
                if !e.pod.isEmpty {
                    Text(e.pod)
                        .font(.system(.caption, design: .monospaced))
                }
                Text(e.message)
                    .font(.caption)
                    .lineLimit(1)
            }
            if !e.detail.isEmpty {
                DisclosureGroup("자세히") {
                    Text(e.detail)
                        .font(.system(.caption2, design: .monospaced))
                        .textSelection(.enabled)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
                .font(.caption)
            }
        }
        .padding(.vertical, 2)
    }

    private func reload() {
        events = ActivityLog.readAll()
        let stateURL = appState.scheduleFileURL.deletingLastPathComponent().appendingPathComponent("state.json")
        if let data = try? Data(contentsOf: stateURL),
           let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
            timerState = obj
        } else {
            timerState = [:]
        }
    }

    private func actionText(_ action: String) -> String {
        switch action {
        case "start": return "켜기"
        case "stop": return "끄기"
        case "check": return "상태 확인"
        case "install": return "타이머 설치"
        case "schedule": return "일정 저장"
        case "yaml": return "YAML 저장"
        case "model-key": return "모델 API 키"
        case "none": return "없음"
        default: return action
        }
    }

    private func formatTime(_ iso: String?) -> String {
        guard let iso = iso, !iso.isEmpty else { return "-" }
        // "2026-10-23T18:04:17.697869" -> "10-23 18:04:17"
        let trimmed = iso.prefix(19)
        guard trimmed.count == 19 else { return String(iso) }
        let date = trimmed.dropFirst(5).prefix(5)
        let time = trimmed.suffix(8)
        return "\(date) \(time)"
    }
}
