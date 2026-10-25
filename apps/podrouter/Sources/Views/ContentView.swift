import AppKit
import SwiftUI

enum NavigationItem: String, CaseIterable, Identifiable {
    case setup = "1. 설정 안내"
    case keys = "2. 키 관리"
    case pod = "3. Pod 설정"
    case power = "4. 전원 제어"
    case timer = "5. 타이머"
    case logs = "6. 로그"
    case connect = "7. 연결 설정"

    var id: String { rawValue }

    var icon: String {
        switch self {
        case .setup: return "info.circle"
        case .keys: return "key.fill"
        case .pod: return "cube.fill"
        case .power: return "bolt.fill"
        case .timer: return "clock.fill"
        case .logs: return "list.bullet.rectangle"
        case .connect: return "link"
        }
    }
}

struct ContentView: View {
    @ObservedObject var appState: AppState
    @State private var selectedItem: NavigationItem? = .setup
    @State private var showingSettings: Bool = false
    @State private var folderMessage: String?

    var body: some View {
        NavigationSplitView {
            List(NavigationItem.allCases, selection: $selectedItem) { item in
                NavigationLink(value: item) {
                    Label(item.rawValue, systemImage: item.icon)
                }
            }
            .navigationTitle("podrouter")
            .safeAreaInset(edge: .bottom) {
                VStack(alignment: .leading, spacing: 6) {
                    Divider()
                    HStack {
                        VStack(alignment: .leading, spacing: 2) {
                            Text(appState.isInstalledMode ? "데이터 폴더:" : "저장소(개발용):")
                                .font(.caption2)
                                .foregroundColor(.secondary)
                            Text(appState.dataRootPath)
                                .font(.caption2)
                                .lineLimit(1)
                                .truncationMode(.middle)
                        }
                        Spacer()
                        Button(action: { showingSettings.toggle() }) {
                            Image(systemName: "gearshape")
                        }
                        .buttonStyle(.plain)
                        .popover(isPresented: $showingSettings) {
                            repoSettingsPopover
                        }
                    }
                    .padding(.horizontal, 10)
                    .padding(.vertical, 4)
                }
            }
        } detail: {
            if !appState.isRepoValid {
                projectMissingView
            } else if let selected = selectedItem {
                switch selected {
                case .setup:
                    SetupView(appState: appState)
                case .keys:
                    KeysView(appState: appState)
                case .pod:
                    PodView(appState: appState)
                case .power:
                    PowerView(appState: appState)
                case .timer:
                    TimerView(appState: appState)
                case .logs:
                    LogsView(appState: appState)
                case .connect:
                    ConnectView(appState: appState)
                }
            } else {
                SetupView(appState: appState)
            }
        }
        .frame(minWidth: 780, minHeight: 520)
        .safeAreaInset(edge: .top) {
            setupBanner
        }
    }

    @ViewBuilder
    private var setupBanner: some View {
        switch appState.setupState {
        case .working where !appState.isVenvReady:
            HStack {
                ProgressView().controlSize(.small)
                Text("Python 환경을 준비하는 중입니다. 처음 한 번만 1~2분 걸립니다.")
                    .font(.callout)
                Spacer()
            }
            .padding(10)
            .background(Color.blue.opacity(0.12))
        case .failed(let reason):
            HStack {
                Image(systemName: "exclamationmark.triangle.fill").foregroundColor(.orange)
                Text(reason).font(.callout).textSelection(.enabled)
                Spacer()
                Button("다시 시도") { appState.prepareRuntime() }
            }
            .padding(10)
            .background(Color.orange.opacity(0.15))
        default:
            if appState.isRepoValid && !appState.isVenvReady && !appState.isInstalledMode {
                HStack {
                    Image(systemName: "exclamationmark.triangle.fill").foregroundColor(.orange)
                    Text("Python 환경(.venv)이 없습니다. 프로젝트 폴더에서 ./install.sh 를 먼저 실행하세요.")
                        .font(.callout)
                    Spacer()
                }
                .padding(10)
                .background(Color.orange.opacity(0.15))
            }
        }
    }

    private var projectMissingView: some View {
        VStack(spacing: 16) {
            Image(systemName: "folder.badge.questionmark")
                .font(.system(size: 48))
                .foregroundColor(.secondary)
            Text("podrouter 프로젝트 폴더를 찾을 수 없습니다")
                .font(.title2)
                .bold()
            Text("git clone 한 폴더(pyproject.toml 이 있는 곳)를 선택하세요.\n그 폴더에서 ./install.sh 를 실행하면 다음부터 자동으로 찾습니다.")
                .multilineTextAlignment(.center)
                .foregroundColor(.secondary)
            Button("프로젝트 폴더 선택…") { chooseProjectFolder() }
                .buttonStyle(.borderedProminent)
            if let message = folderMessage {
                Text(message).foregroundColor(.red).font(.callout)
            }
        }
        .padding(40)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    private func chooseProjectFolder() {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.allowsMultipleSelection = false
        panel.prompt = "선택"
        panel.message = "podrouter 프로젝트 폴더(pyproject.toml 이 있는 곳)를 선택하세요."
        guard panel.runModal() == .OK, let url = panel.url else { return }
        if RepoLocator.isProjectFolder(url.path) {
            appState.repoPath = url.path
            folderMessage = nil
        } else {
            folderMessage = "그 폴더에는 pyproject.toml 과 src/podrouter 이 없습니다: \(url.path)"
        }
    }

    private var repoSettingsPopover: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("환경 설정")
                .font(.headline)

            VStack(alignment: .leading, spacing: 4) {
                Text(appState.isInstalledMode
                     ? "설치형으로 실행 중입니다. 키와 Pod 설정은 \(appState.dataRootPath) 에 있습니다. 개발용 git 저장소를 쓰려면 아래에서 고르세요."
                     : "개발용 저장소 경로 (Repo Path):")
                    .font(.caption)
                    .frame(width: 320, alignment: .leading)
                    .fixedSize(horizontal: false, vertical: true)
                Text("저장소 경로:")
                    .font(.caption)
                    .fontWeight(.medium)
                TextField("저장소 경로", text: $appState.repoPath)
                    .textFieldStyle(.roundedBorder)
                    .frame(width: 320)
            }

            VStack(alignment: .leading, spacing: 4) {
                Text("Python 경로 (Default):")
                    .font(.caption)
                    .fontWeight(.medium)
                Text(appState.pythonPath)
                    .font(.system(.caption, design: .monospaced))
                    .foregroundColor(.secondary)
            }

            HStack {
                Button("폴더 선택…") {
                    showingSettings = false
                    chooseProjectFolder()
                }
                .buttonStyle(.borderless)

                Button(RepoLocator.detected == nil ? "설치형으로 되돌리기" : "자동 찾기로 복원") {
                    appState.repoPath = RepoLocator.detected ?? ""
                }
                .buttonStyle(.borderless)
                .disabled(appState.repoPath == (RepoLocator.detected ?? ""))

                Spacer()

                Button("닫기") {
                    showingSettings = false
                }
                .buttonStyle(.borderedProminent)
            }
        }
        .padding()
    }
}
