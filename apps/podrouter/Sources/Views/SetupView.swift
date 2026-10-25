import SwiftUI
import AppKit

struct SetupView: View {
    @ObservedObject var appState: AppState
    @State private var feedbackMessage: String?
    @State private var copied: String?

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                Text("1. 설정 안내 (Setup)")
                    .font(.title2)
                    .bold()

                Text("podrouter 초기 설정을 돕는 안내 도구입니다. Claude 또는 Codex 터미널 세션을 시작하여 대화형으로 설정을 진행할 수 있습니다.")
                    .font(.body)
                    .foregroundColor(.secondary)

                GroupBox(label: Label("AI 에이전트 설정 가이드", systemImage: "sparkles")) {
                    VStack(alignment: .leading, spacing: 14) {
                        HStack(spacing: 16) {
                            Button(action: openClaude) {
                                Label("Claude로 설정 안내", systemImage: "terminal")
                                    .padding(.vertical, 4)
                            }
                            .buttonStyle(.borderedProminent)
                            .tint(AIColors.claude)

                            Button(action: openCodex) {
                                Label("Codex로 설정 안내", systemImage: "terminal")
                                    .padding(.vertical, 4)
                            }
                            .buttonStyle(.borderedProminent)
                            .tint(AIColors.codex)
                        }

                        Divider()

                        VStack(alignment: .leading, spacing: 8) {
                            Text("첫 번째 실행 명령어:")
                                .font(.subheadline)
                                .fontWeight(.medium)

                            commandRow(label: "Claude", command: "/podrouter-setup")
                            commandRow(label: "Codex", command: "$podrouter-setup")

                            Text("버튼을 누르면 터미널에서 이 명령어로 시작합니다. 시작되지 않으면 열린 창에 직접 입력하세요.")
                                .font(.caption)
                                .foregroundColor(.secondary)
                        }
                    }
                    .padding(8)
                }

                if let feedback = feedbackMessage {
                    HStack {
                        Image(systemName: "info.circle")
                        Text(feedback)
                            .font(.subheadline)
                    }
                    .foregroundColor(.blue)
                    .padding(8)
                    .background(Color.blue.opacity(0.1))
                    .cornerRadius(8)
                }

                Spacer()
            }
            .padding()
        }
    }

    private func openClaude() {
        feedbackMessage = "Terminal.app에서 Claude를 실행 중입니다..."
        let result = ProcessRunner.openClaudeInTerminal(folder: appState.dataRootPath, initialPrompt: "/podrouter-setup")
        if result.isSuccess {
            feedbackMessage = "Terminal.app에서 Claude가 '/podrouter-setup'으로 시작되었습니다. 시작되지 않으면 그 명령어를 직접 입력하세요."
        } else {
            feedbackMessage = "터미널 실행 오류: \(result.combinedOutput)"
        }
    }

    private func openCodex() {
        feedbackMessage = "Terminal.app에서 Codex를 실행 중입니다..."
        let result = ProcessRunner.openCodexInTerminal(folder: appState.dataRootPath, initialPrompt: "$podrouter-setup")
        if result.isSuccess {
            feedbackMessage = "Terminal.app에서 Codex가 '$podrouter-setup'으로 시작되었습니다. 폴더 신뢰 여부를 물으면 허용하세요. 시작되지 않으면 그 명령어를 직접 입력하세요."
        } else {
            feedbackMessage = "터미널 실행 오류: \(result.combinedOutput)"
        }
    }

    private func commandRow(label: String, command: String) -> some View {
        HStack {
            Text(label)
                .font(.subheadline)
                .frame(width: 52, alignment: .leading)
            Text(command)
                .font(.system(.body, design: .monospaced))
                .padding(.horizontal, 10)
                .padding(.vertical, 6)
                .background(Color(NSColor.controlBackgroundColor))
                .cornerRadius(6)
                .overlay(
                    RoundedRectangle(cornerRadius: 6)
                        .stroke(Color.gray.opacity(0.3), lineWidth: 1)
                )
            Button(action: { copyCommand(command) }) {
                Label(copied == command ? "복사됨" : "명령어 복사", systemImage: copied == command ? "checkmark" : "doc.on.doc")
            }
        }
    }

    private func copyCommand(_ command: String) {
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(command, forType: .string)
        copied = command
        DispatchQueue.main.asyncAfter(deadline: .now() + 2) {
            if copied == command { copied = nil }
        }
    }
}
