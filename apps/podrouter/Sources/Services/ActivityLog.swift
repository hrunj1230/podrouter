import Foundation

/// One line of `logs/activity.jsonl`, shared with the Python timer (`podrouter.activity_log`).
struct ActivityEvent: Codable, Identifiable {
    var time: String
    var source: String
    var action: String
    var pod: String
    var ok: Bool
    var message: String
    var detail: String

    var id: String { "\(time)|\(source)|\(action)|\(pod)|\(message)" }
}

enum ActivityLog {
    static let maxBytes = 1_000_000

    static var directoryURL: URL {
        let appSupport = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first!
        return appSupport.appendingPathComponent("podrouter/logs")
    }

    static var fileURL: URL { directoryURL.appendingPathComponent("activity.jsonl") }
    static var rotatedURL: URL { directoryURL.appendingPathComponent("activity.1.jsonl") }

    /// Append one event. `detail` must already be redacted (ProcessRunner output is).
    static func append(source: String = "app", action: String, pod: String, ok: Bool, message: String, detail: String = "") {
        let fm = FileManager.default
        try? fm.createDirectory(at: directoryURL, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
        if let size = (try? fm.attributesOfItem(atPath: fileURL.path))?[.size] as? Int, size > maxBytes {
            try? fm.removeItem(at: rotatedURL)
            try? fm.moveItem(at: fileURL, to: rotatedURL)
        }
        let fmt = DateFormatter()
        fmt.locale = Locale(identifier: "en_US_POSIX")
        fmt.dateFormat = "yyyy-MM-dd'T'HH:mm:ss"
        let event = ActivityEvent(
            time: fmt.string(from: Date()),
            source: source,
            action: action,
            pod: pod,
            ok: ok,
            message: String(message.trimmingCharacters(in: .whitespacesAndNewlines).prefix(300)),
            detail: String(Redactor.redact(detail).trimmingCharacters(in: .whitespacesAndNewlines).suffix(2000))
        )
        guard var data = try? JSONEncoder().encode(event) else { return }
        data.append(0x0A)
        if !fm.fileExists(atPath: fileURL.path) {
            fm.createFile(atPath: fileURL.path, contents: nil, attributes: [.posixPermissions: 0o600])
        }
        guard let handle = try? FileHandle(forWritingTo: fileURL) else { return }
        defer { try? handle.close() }
        _ = try? handle.seekToEnd()
        try? handle.write(contentsOf: data)
    }

    /// Newest first, from the current and the rotated file.
    static func readAll() -> [ActivityEvent] {
        var events: [ActivityEvent] = []
        let decoder = JSONDecoder()
        for url in [rotatedURL, fileURL] {
            guard let text = try? String(contentsOf: url, encoding: .utf8) else { continue }
            for line in text.split(separator: "\n") {
                if let event = try? decoder.decode(ActivityEvent.self, from: Data(line.utf8)) {
                    events.append(event)
                }
            }
        }
        return events.reversed()
    }

    static func clear() {
        try? FileManager.default.removeItem(at: fileURL)
        try? FileManager.default.removeItem(at: rotatedURL)
    }
}
