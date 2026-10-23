import Foundation
import Security

/// Stores keys in the data folder `.env` (mode 0600), shared with the web server, timer and CLI.
/// Values are never shown in the UI; only "저장됨" or "없음".
public enum SecretStore {
    public static let runpodAccount = "RUNPOD_API_KEY"
    public static let tsAccount = "TS_AUTHKEY"
    public static let hfAccount = "HF_TOKEN"
    public static let cfTokenAccount = "CF_TUNNEL_TOKEN"
    /// Key that clients must send to the model API (`Authorization: Bearer ...`).
    public static let modelApiAccount = "MODEL_API_KEY"

    /// New random model API key: `sk-` + 32 random bytes as hex (64 chars).
    public static func generateModelApiKey() -> String? {
        var bytes = [UInt8](repeating: 0, count: 32)
        guard SecRandomCopyBytes(kSecRandomDefault, bytes.count, &bytes) == errSecSuccess else { return nil }
        return "sk-" + bytes.map { String(format: "%02x", $0) }.joined()
    }
    /// Not secrets: the selected address mode and the named tunnel hostname.
    public static let tunnelModeKey = "PODROUTER_TUNNEL"
    public static let cfHostnameKey = "CF_TUNNEL_HOSTNAME"
    /// Not a secret: the Tailscale tailnet domain, e.g. `your-tailnet.ts.net`.
    public static let tsTailnetKey = "TS_TAILNET"
    /// Not a secret: comma-separated pod names the app must never create, stop, or schedule.
    public static let protectedPodsKey = "PODROUTER_PROTECTED_PODS"

    /// `<clone>/.env` or `~/podrouter/.env`, or nil when neither is known yet.
    static var envFileURL: URL? {
        guard let runtime = Runtime.current else { return nil }
        try? FileManager.default.createDirectory(at: runtime.dataRoot, withIntermediateDirectories: true)
        return runtime.envFile
    }

    @discardableResult
    public static func saveKey(account: String, secret: String) -> Bool {
        let value = secret.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !value.isEmpty, !value.contains("\n"), !value.contains("\r") else { return false }
        var lines = readLines()
        let newLine = "\(account)=\(value)"
        if let idx = lines.firstIndex(where: { keyOf($0) == account }) {
            lines[idx] = newLine
            // Drop later duplicates so the saved value is the one that is read.
            lines = lines.enumerated().filter { $0.offset == idx || keyOf($0.element) != account }.map(\.element)
        } else {
            lines.append(newLine)
        }
        return writeLines(lines)
    }

    public static func keyExists(account: String) -> Bool {
        guard let value = loadKeyForChildProcess(account: account) else { return false }
        return !value.isEmpty
    }

    @discardableResult
    public static func deleteKey(account: String) -> Bool {
        let lines = readLines()
        let kept = lines.filter { keyOf($0) != account }
        if kept.count == lines.count { return true }
        return writeLines(kept)
    }

    /// Internal retrieval only for child process environment or redaction. Never shown in the UI.
    public static func loadKeyForChildProcess(account: String) -> String? {
        for line in readLines() where keyOf(line) == account {
            guard let eq = line.firstIndex(of: "=") else { continue }
            var value = String(line[line.index(after: eq)...]).trimmingCharacters(in: .whitespaces)
            if value.count >= 2, let first = value.first, let last = value.last,
               (first == "\"" && last == "\"") || (first == "'" && last == "'") {
                value = String(value.dropFirst().dropLast())
            }
            return value
        }
        return nil
    }

    // MARK: - File helpers

    private static func keyOf(_ line: String) -> String? {
        var text = line.trimmingCharacters(in: .whitespaces)
        if text.isEmpty || text.hasPrefix("#") { return nil }
        if text.hasPrefix("export ") { text = String(text.dropFirst(7)) }
        guard let eq = text.firstIndex(of: "=") else { return nil }
        return text[..<eq].trimmingCharacters(in: .whitespaces)
    }

    private static func readLines() -> [String] {
        guard let url = envFileURL, let text = try? String(contentsOf: url, encoding: .utf8) else { return [] }
        var lines = text.components(separatedBy: "\n")
        if lines.last == "" { lines.removeLast() }
        return lines
    }

    /// Writes atomically through a 0600 temp file in the same folder.
    private static func writeLines(_ lines: [String]) -> Bool {
        guard let target = envFileURL else { return false }
        let temp = target.deletingLastPathComponent().appendingPathComponent(".env.podrouter.tmp")
        let body = lines.joined(separator: "\n") + "\n"
        let fm = FileManager.default
        try? fm.removeItem(at: temp)
        guard fm.createFile(atPath: temp.path, contents: Data(body.utf8), attributes: [.posixPermissions: 0o600]) else {
            return false
        }
        if rename(temp.path, target.path) != 0 {
            try? fm.removeItem(at: temp)
            return false
        }
        chmod(target.path, 0o600)
        return true
    }
}
