import Foundation

/// Where the app finds podrouter code, its Python, and the user's data.
///
/// - Clone mode: a git clone was found (install.sh, `swift run`, or picked by hand).
///   Code, `.venv`, `.env` and `pods/` all live in the clone.
/// - Installed mode: the .app came from a release zip or Homebrew. podrouter ships in
///   the bundle as a wheel, the Python env lives in Application Support, and the
///   user's data (`.env`, `pods/`) lives in `~/podrouter`.
struct Runtime: Equatable {
    enum Mode: Equatable { case clone, installed }

    let mode: Mode
    /// `.env`, `pods/`, and the folder Claude opens in.
    let dataRoot: URL
    /// Python virtual environment folder.
    let venvDir: URL
    /// `render_plan.py` (catalog, render, inspect, guide).
    let renderPlanScript: URL
    /// Bundled wheel to install in installed mode.
    let wheel: URL?
    /// Bundled Claude skills folder to copy into `dataRoot/.claude`.
    let claudeTemplate: URL?

    var python: URL { venvDir.appendingPathComponent("bin/python") }
    var envFile: URL { dataRoot.appendingPathComponent(".env") }
    var podsDir: URL { dataRoot.appendingPathComponent("pods") }
    /// Launcher the Claude skill runs: `.podrouter/pod-plan`.
    var podPlanLauncher: URL { dataRoot.appendingPathComponent(".podrouter/pod-plan") }

    static var appSupport: URL {
        FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first!
            .appendingPathComponent("podrouter")
    }

    static var installedDataRoot: URL {
        FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("podrouter")
    }

    static var bundledResources: URL? {
        guard let url = Bundle.main.resourceURL?.appendingPathComponent("podrouter"),
              FileManager.default.fileExists(atPath: url.appendingPathComponent("render_plan.py").path)
        else { return nil }
        return url
    }

    /// Clone mode when `repoPath` is a project folder, otherwise installed mode
    /// when the bundle carries podrouter, otherwise nil.
    static func resolve(repoPath: String) -> Runtime? {
        if RepoLocator.isProjectFolder(repoPath) {
            let root = URL(fileURLWithPath: repoPath)
            return Runtime(
                mode: .clone,
                dataRoot: root,
                venvDir: root.appendingPathComponent(".venv"),
                renderPlanScript: root.appendingPathComponent(".grok/skills/podrouter-yaml/scripts/render_plan.py"),
                wheel: nil,
                claudeTemplate: nil
            )
        }
        guard let res = bundledResources else { return nil }
        let wheel = (try? FileManager.default.contentsOfDirectory(at: res, includingPropertiesForKeys: nil))?
            .first { $0.pathExtension == "whl" }
        return Runtime(
            mode: .installed,
            dataRoot: installedDataRoot,
            venvDir: appSupport.appendingPathComponent("venv"),
            renderPlanScript: res.appendingPathComponent("render_plan.py"),
            wheel: wheel,
            claudeTemplate: res.appendingPathComponent("claude")
        )
    }

    /// The runtime for the folder the app would pick right now.
    static var current: Runtime? { resolve(repoPath: RepoLocator.resolve()) }

    /// A stamp in the venv that says which bundled wheel is installed.
    var installStamp: URL { venvDir.appendingPathComponent(".podrouter-wheel") }

    var isPythonReady: Bool {
        guard FileManager.default.isExecutableFile(atPath: python.path) else { return false }
        guard mode == .installed else { return true }
        let stamp = (try? String(contentsOf: installStamp, encoding: .utf8))?
            .trimmingCharacters(in: .whitespacesAndNewlines)
        return stamp == wheel?.lastPathComponent
    }
}
