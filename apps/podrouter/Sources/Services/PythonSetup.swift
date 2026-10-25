import Foundation

/// Prepares what podrouter needs to run.
///
/// Installed mode: finds Python 3.10+, makes the venv in Application Support,
/// and installs the bundled wheel. Both modes: writes the `.podrouter/pod-plan`
/// launcher that the Claude skill uses, and (installed mode) copies the Claude
/// skills into the data folder.
enum PythonSetup {
    /// Python 3.10+ candidates, newest first. Homebrew installs `python@3.12` with the cask.
    static let pythonCandidates = [
        "/opt/homebrew/bin/python3.13", "/opt/homebrew/bin/python3.12", "/opt/homebrew/bin/python3.11",
        "/opt/homebrew/bin/python3.10", "/opt/homebrew/bin/python3",
        "/usr/local/bin/python3.13", "/usr/local/bin/python3.12", "/usr/local/bin/python3.11",
        "/usr/local/bin/python3.10", "/usr/local/bin/python3",
        "/Library/Frameworks/Python.framework/Versions/Current/bin/python3",
        "/usr/bin/python3",
    ]

    static func findPython() -> URL? {
        for path in pythonCandidates where FileManager.default.isExecutableFile(atPath: path) {
            let result = ProcessRunner.run(
                executableURL: URL(fileURLWithPath: path),
                arguments: ["-c", "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)"],
                includeKeys: false
            )
            if result.isSuccess { return URL(fileURLWithPath: path) }
        }
        return nil
    }

    /// Runs off the main thread. Returns nil on success, or a message for the user.
    static func prepare(_ runtime: Runtime) -> String? {
        let fm = FileManager.default
        try? fm.createDirectory(at: runtime.podsDir, withIntermediateDirectories: true)
        if runtime.mode == .installed {
            if let error = installPython(runtime) { return error }
            copyClaudeFiles(runtime)
        }
        writeLauncher(runtime)
        return nil
    }

    private static func installPython(_ runtime: Runtime) -> String? {
        if runtime.isPythonReady { return nil }
        guard let wheel = runtime.wheel else { return "앱 안에 podrouter 패키지가 없습니다. 앱을 다시 설치하세요." }
        let fm = FileManager.default
        if !fm.isExecutableFile(atPath: runtime.python.path) {
            guard let base = findPython() else {
                return "Python 3.10 이상이 없습니다. 터미널에서 'brew install python@3.12'를 실행한 뒤 앱을 다시 여세요."
            }
            try? fm.createDirectory(at: runtime.venvDir.deletingLastPathComponent(), withIntermediateDirectories: true)
            let venv = ProcessRunner.run(executableURL: base, arguments: ["-m", "venv", runtime.venvDir.path], includeKeys: false)
            guard venv.isSuccess else { return "Python 환경을 만들지 못했습니다: \(venv.combinedOutput.suffix(300))" }
        }
        let pip = ProcessRunner.run(
            executableURL: runtime.python,
            arguments: ["-m", "pip", "install", "--quiet", "--disable-pip-version-check", "--upgrade", wheel.path],
            includeKeys: false
        )
        guard pip.isSuccess else {
            return "podrouter 설치에 실패했습니다. 인터넷 연결을 확인하세요: \(pip.combinedOutput.suffix(300))"
        }
        try? wheel.lastPathComponent.write(to: runtime.installStamp, atomically: true, encoding: .utf8)
        return nil
    }

    /// Skills also installed for Codex under `~/podrouter/.agents/skills`.
    static let codexSkills = ["podrouter-setup"]

    /// `~/podrouter/.claude/skills/podrouter-*` and `settings.json` from the bundle,
    /// plus `codexSkills` under `.agents/skills`.
    /// Our two skills are replaced on every launch so they match the app version.
    private static func copyClaudeFiles(_ runtime: Runtime) {
        guard let template = runtime.claudeTemplate else { return }
        let fm = FileManager.default
        let target = runtime.dataRoot.appendingPathComponent(".claude")
        let skills = target.appendingPathComponent("skills")
        try? fm.createDirectory(at: skills, withIntermediateDirectories: true)
        let sourceSkills = template.appendingPathComponent("skills")
        for name in (try? fm.contentsOfDirectory(atPath: sourceSkills.path)) ?? [] where name.hasPrefix("podrouter") {
            let dest = skills.appendingPathComponent(name)
            try? fm.removeItem(at: dest)
            try? fm.copyItem(at: sourceSkills.appendingPathComponent(name), to: dest)
        }
        // Codex reads skills from `.agents/skills` in the folder it starts in.
        let agentSkills = runtime.dataRoot.appendingPathComponent(".agents/skills")
        try? fm.createDirectory(at: agentSkills, withIntermediateDirectories: true)
        for name in codexSkills {
            let source = sourceSkills.appendingPathComponent(name)
            guard fm.fileExists(atPath: source.path) else { continue }
            let dest = agentSkills.appendingPathComponent(name)
            try? fm.removeItem(at: dest)
            try? fm.copyItem(at: source, to: dest)
        }
        let settings = target.appendingPathComponent("settings.json")
        if !fm.fileExists(atPath: settings.path) {
            try? fm.copyItem(at: template.appendingPathComponent("settings.json"), to: settings)
        }
    }

    /// The skill calls `.podrouter/pod-plan <catalog|render|inspect|guide>` from the data folder.
    private static func writeLauncher(_ runtime: Runtime) {
        let fm = FileManager.default
        let launcher = runtime.podPlanLauncher
        try? fm.createDirectory(at: launcher.deletingLastPathComponent(), withIntermediateDirectories: true)
        let body = """
        #!/bin/zsh
        # Written by the podrouter app. Runs the Pod YAML helper for the Claude skill.
        # Keys are read from \(runtime.envFile.path) by the helper and never printed.
        export PODROUTER_HOME=\(shellQuote(runtime.dataRoot.path))
        exec \(shellQuote(runtime.python.path)) \(shellQuote(runtime.renderPlanScript.path)) "$@"

        """
        try? body.write(to: launcher, atomically: true, encoding: .utf8)
        chmod(launcher.path, 0o700)
    }

    static func shellQuote(_ text: String) -> String {
        "'" + text.replacingOccurrences(of: "'", with: "'\\''") + "'"
    }
}
