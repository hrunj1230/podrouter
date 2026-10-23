import SwiftUI
import AppKit

struct SetupView: View {
    @ObservedObject var appState: AppState
    @State private var feedbackMessage: String?
    @State private var copied: Bool = false

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                Text("1. 설정 안내 (Setup)")
                    .font(.title2)
                    .bold()

                Text("podrouter 초기 설정을 돕는 안내 도구입니다. Claude 터미널 세션을 시작하여 대화형으로 설정을 진행할 수 있습니다.")
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

                            Button(action: {}) {
                                Label("Codex", systemImage: "bolt.slash")
                                    .padding(.vertical, 4)
                            }
                            .buttonStyle(.bordered)
                            .disabled(true)
                            .help("Codex 구독이 일시 중단되었습니다. (Codex subscription is paused)")
                        }

                        Text("Codex: 구독이 일시 중단되었습니다.")
                            .font(.caption)
                            .foregroundColor(.secondary)

                        Divider()

                        VStack(alignment: .leading, spacing: 8) {
                            Text("첫 번째 실행 명령어:")
                                .font(.subheadline)
                                .fontWeight(.medium)

                            HStack {
                                Text("/podrouter-setup")
                                    .font(.system(.body, design: .monospaced))
                                    .padding(.horizontal, 10)
                                    .padding(.vertical, 6)
                                    .background(Color(NSColor.controlBackgroundColor))
                                    .cornerRadius(6)
                                    .overlay(
                                        RoundedRectangle(cornerRadius: 6)
                                            .stroke(Color.gray.opacity(0.3), lineWidth: 1)
                                    )

                                Button(action: copyCommand) {
                                    Label(copied ? "복사됨" : "명령어 복사", systemImage: copied ? "checkmark" : "doc.on.doc")
                                }
                            }

                            Text("Claude 터미널 창이 열리면 위 슬래시 명령어(/podrouter-setup)를 입력하세요.")
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

    private func copyCommand() {
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString("/podrouter-setup", forType: .string)
        copied = true
        DispatchQueue.main.asyncAfter(deadline: .now() + 2) {
            copied = false
        }
    }
}
