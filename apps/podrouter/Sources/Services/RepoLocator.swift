import Foundation

/// Finds the podrouter project folder (the git clone) without hard-coded user paths.
///
/// Order: 1) a folder the user picked (UserDefaults), 2) `repo-path.txt` that
/// install.sh writes into the .app bundle, 3) walking up from the running
/// executable (works for `swift run` inside the clone).
enum RepoLocator {
    static let defaultsKey = "podrouter.repoPath"
    static let bundleFileName = "repo-path.txt"

    /// A project folder has `pyproject.toml` and `src/podrouter/cli.py`.
    static func isProjectFolder(_ path: String) -> Bool {
        guard !path.isEmpty else { return false }
        let fm = FileManager.default
        let root = URL(fileURLWithPath: path)
        return fm.fileExists(atPath: root.appendingPathComponent("pyproject.toml").path)
            && fm.fileExists(atPath: root.appendingPathComponent("src/podrouter/cli.py").path)
    }

    /// True when install.sh (or `python -m venv` + `pip install -e .`) has been run.
    static func hasVirtualEnv(_ path: String) -> Bool {
        FileManager.default.isExecutableFile(
            atPath: URL(fileURLWithPath: path).appendingPathComponent(".venv/bin/python").path
        )
    }

    static var userChoice: String? {
        let value = UserDefaults.standard.string(forKey: defaultsKey) ?? ""
        return isProjectFolder(value) ? value : nil
    }

    static var bundleRecorded: String? {
        guard let url = Bundle.main.url(forResource: "repo-path", withExtension: "txt"),
              let text = try? String(contentsOf: url, encoding: .utf8) else { return nil }
        let path = text.trimmingCharacters(in: .whitespacesAndNewlines)
        return isProjectFolder(path) ? path : nil
    }

    static var nearExecutable: String? {
        var dir = Bundle.main.executableURL?.resolvingSymlinksInPath().deletingLastPathComponent()
        for _ in 0..<10 {
            guard let current = dir else { return nil }
            if isProjectFolder(current.path) { return current.path }
            let parent = current.deletingLastPathComponent()
            if parent.path == current.path { return nil }
            dir = parent
        }
        return nil
    }

    /// Automatic location, ignoring the user's manual choice.
    static var detected: String? { bundleRecorded ?? nearExecutable }

    /// The folder to use, or "" when none is found (the app then asks the user).
    static func resolve() -> String {
        userChoice ?? detected ?? ""
    }
}
