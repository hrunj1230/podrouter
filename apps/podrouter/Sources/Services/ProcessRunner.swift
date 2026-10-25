import Foundation

public struct ProcessResult {
    public let exitCode: Int32
    public let combinedOutput: String
    public let isSuccess: Bool

    public init(exitCode: Int32, combinedOutput: String) {
        self.exitCode = exitCode
        self.combinedOutput = combinedOutput
        self.isSuccess = (exitCode == 0)
    }
}

public enum ProcessRunner {
    /// Builds a clean environment containing only safe system vars, PATH, and optionally keys from the repo .env.
    /// Strictly avoids loading any .env file.
    public static func makeCleanEnvironment(includeKeys: Bool = true) -> [String: String] {
        var env: [String: String] = [:]

        if let path = ProcessInfo.processInfo.environment["PATH"] {
            env["PATH"] = path
        } else {
            env["PATH"] = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
        }

        if let home = ProcessInfo.processInfo.environment["HOME"] {
            env["HOME"] = home
        }
        if let user = ProcessInfo.processInfo.environment["USER"] {
            env["USER"] = user
        }
        if let tmp = ProcessInfo.processInfo.environment["TMPDIR"] {
            env["TMPDIR"] = tmp
        }
        if let shell = ProcessInfo.processInfo.environment["SHELL"] {
            env["SHELL"] = shell
        }

        // Tells podrouter where `.env` and `pods/` live (repo clone or ~/podrouter).
        if let runtime = Runtime.current {
            env["PODROUTER_HOME"] = runtime.dataRoot.path
        }

        if includeKeys {
            if let runpodKey = SecretStore.loadKeyForChildProcess(account: SecretStore.runpodAccount), !runpodKey.isEmpty {
                env["RUNPOD_API_KEY"] = runpodKey
            }
            if let tsKey = SecretStore.loadKeyForChildProcess(account: SecretStore.tsAccount), !tsKey.isEmpty {
                env["TS_AUTHKEY"] = tsKey
            }
            if let hfKey = SecretStore.loadKeyForChildProcess(account: SecretStore.hfAccount), !hfKey.isEmpty {
                env["HF_TOKEN"] = hfKey
            }
            for name in [SecretStore.cfTokenAccount, SecretStore.tunnelModeKey, SecretStore.cfHostnameKey, SecretStore.modelApiAccount, SecretStore.protectedPodsKey] {
                if let value = SecretStore.loadKeyForChildProcess(account: name), !value.isEmpty {
                    env[name] = value
                }
            }
        }

        return env
    }

    /// Runs a process synchronously with clean environment and returns redacted output.
    public static func run(
        executableURL: URL,
        arguments: [String],
        currentDirectoryURL: URL? = nil,
        includeKeys: Bool = true,
        extraEnvironment: [String: String] = [:]
    ) -> ProcessResult {
        let process = Process()
        process.executableURL = executableURL
        process.arguments = arguments
        if let currentDirectoryURL = currentDirectoryURL {
            process.currentDirectoryURL = currentDirectoryURL
        }

        var env = makeCleanEnvironment(includeKeys: includeKeys)
        for (k, v) in extraEnvironment {
            env[k] = v
        }
        process.environment = env

        let stdoutPipe = Pipe()
        let stderrPipe = Pipe()
        process.standardOutput = stdoutPipe
        process.standardError = stderrPipe

        do {
            try process.run()
        } catch {
            return ProcessResult(
                exitCode: -1,
                combinedOutput: "프로세스 실행 실패: \(error.localizedDescription)"
            )
        }

        let outData = stdoutPipe.fileHandleForReading.readDataToEndOfFile()
        let errData = stderrPipe.fileHandleForReading.readDataToEndOfFile()
        process.waitUntilExit()

        let rawOut = String(data: outData, encoding: .utf8) ?? ""
        let rawErr = String(data: errData, encoding: .utf8) ?? ""

        var combined = ""
        if !rawOut.isEmpty {
            combined += rawOut
        }
        if !rawErr.isEmpty {
            if !combined.isEmpty && !combined.hasSuffix("\n") {
                combined += "\n"
            }
            combined += rawErr
        }

        let redacted = Redactor.redact(combined)
        return ProcessResult(exitCode: process.terminationStatus, combinedOutput: redacted)
    }

    /// Opens Terminal.app at repo and launches claude with sensitive keys unset.
    /// `initialPrompt` must be a fixed slash command such as `/podrouter-pod` (no quotes).
    public static func openClaudeInTerminal(folder repoPath: String, initialPrompt: String? = nil) -> ProcessResult {
        openAgentInTerminal(folder: repoPath, agent: "claude", initialPrompt: initialPrompt)
    }

    /// Opens Terminal.app at repo and launches codex with sensitive keys unset.
    /// `initialPrompt` must be a fixed skill mention such as `$podrouter-setup`.
    public static func openCodexInTerminal(folder repoPath: String, initialPrompt: String? = nil) -> ProcessResult {
        openAgentInTerminal(folder: repoPath, agent: "codex", initialPrompt: initialPrompt)
    }

    private static func openAgentInTerminal(folder repoPath: String, agent: String, initialPrompt: String?) -> ProcessResult {
        let escapedRepo = repoPath
            .replacingOccurrences(of: "\\", with: "\\\\")
            .replacingOccurrences(of: "\"", with: "\\\"")
        var agentCommand = agent
        // Only a fixed `/name` or `$name` is passed, single-quoted so the shell keeps `$`.
        if let prompt = initialPrompt, prompt.range(of: "^[/$][a-z0-9-]+$", options: .regularExpression) != nil {
            agentCommand = "\(agent) '\(prompt)'"
        }

        let appleScript = """
        tell application "Terminal"
            activate
            do script "cd \\"\(escapedRepo)\\" && env -u RUNPOD_API_KEY -u TS_AUTHKEY -u CF_TUNNEL_TOKEN -u HF_TOKEN -u VLLM_API_KEY -u CLOUDFLARE_API_TOKEN \(agentCommand)"
        end tell
        """

        let osascriptURL = URL(fileURLWithPath: "/usr/bin/osascript")
        return run(
            executableURL: osascriptURL,
            arguments: ["-e", appleScript],
            includeKeys: false
        )
    }
}
