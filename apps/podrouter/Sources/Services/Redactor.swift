import Foundation

public enum Redactor {
    public static func redact(_ text: String, extraSecrets: [String] = []) -> String {
        var secrets: [String] = []

        if let k1 = SecretStore.loadKeyForChildProcess(account: SecretStore.runpodAccount), !k1.isEmpty {
            secrets.append(k1)
        }
        if let k2 = SecretStore.loadKeyForChildProcess(account: SecretStore.tsAccount), !k2.isEmpty {
            secrets.append(k2)
        }
        if let k3 = SecretStore.loadKeyForChildProcess(account: SecretStore.hfAccount), !k3.isEmpty {
            secrets.append(k3)
        }
        if let k4 = SecretStore.loadKeyForChildProcess(account: SecretStore.cfTokenAccount), !k4.isEmpty {
            secrets.append(k4)
        }
        if let k5 = SecretStore.loadKeyForChildProcess(account: SecretStore.modelApiAccount), !k5.isEmpty {
            secrets.append(k5)
        }
        for s in extraSecrets where !s.isEmpty {
            secrets.append(s)
        }

        // Sort descending by length to avoid partial matches
        secrets.sort { $0.count > $1.count }

        var output = text
        for s in secrets {
            output = output.replacingOccurrences(of: s, with: "***")
        }

        // Redact Bearer tokens
        if let regex = try? NSRegularExpression(pattern: "Bearer\\s+[A-Za-z0-9._~+/-]+", options: []) {
            let range = NSRange(output.startIndex..., in: output)
            output = regex.stringByReplacingMatches(in: output, options: [], range: range, withTemplate: "Bearer ***")
        }

        return output
    }
}
