import Foundation

/// One power command the app started. Saved next to its output file so the
/// window can show it again after the app restarts.
struct RunRecord: Codable, Equatable {
    var id: String
    var pod: String
    /// "start" or "stop".
    var action: String
    /// Command line shown to the user. Keys stay as `${NAME}` placeholders.
    var command: String
    var started: Date
    var pid: Int32?
    /// Set once the result is written to the activity log.
    var recorded: Bool = false
    var exitCode: Int32?

    var label: String { action == "start" ? "켜기" : "끄기" }
    var busyLabel: String { action == "start" ? "켜는 중" : "끄는 중" }
}

struct RunError: Error {
    let message: String
}

/// Runs power commands detached from the app and writes their output to a file
/// line by line. The command keeps running if the app quits, and the app picks
/// the output up again on the next launch.
enum RunStore {
    static let exitMarker = "__PODROUTER_EXIT__ "
    static let keepCount = 30

    static var directoryURL: URL { ActivityLog.directoryURL.appendingPathComponent("runs") }

    static func outputURL(_ id: String) -> URL { directoryURL.appendingPathComponent("\(id).log") }
    static func metaURL(_ id: String) -> URL { directoryURL.appendingPathComponent("\(id).json") }
    static func pidURL(_ id: String) -> URL { directoryURL.appendingPathComponent("\(id).pid") }

    /// Forks into a new session so quitting the app does not stop the command,
    /// writes the pid, then runs the command and appends its exit code.
    private static let launcher = """
    import os, sys
    if os.fork():
        os._exit(0)
    os.setsid()
    with open(sys.argv[1], "w") as f:
        f.write(str(os.getpid()))
    os.execv("/bin/sh", ["sh", "-c", '"$@"; echo "\(exitMarker)$?"', "sh"] + sys.argv[2:])
    """

    /// Start `exec args` in the background. Returns the record, or an error message.
    static func start(pod: String, action: String, exec: URL, args: [String], display: String, cwd: URL) -> Result<RunRecord, RunError> {
        let fm = FileManager.default
        try? fm.createDirectory(at: directoryURL, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
        prune()

        let fmt = DateFormatter()
        fmt.locale = Locale(identifier: "en_US_POSIX")
        fmt.dateFormat = "yyyyMMdd-HHmmss"
        let now = Date()
        let id = "\(fmt.string(from: now))-\(action)-\(UUID().uuidString.prefix(6))"
        var record = RunRecord(id: id, pod: pod, action: action, command: display, started: now)

        let header = "$ \(display)\n시작: \(timeText(now))\n\n"
        fm.createFile(atPath: outputURL(id).path, contents: Data(header.utf8), attributes: [.posixPermissions: 0o600])
        guard let out = try? FileHandle(forWritingTo: outputURL(id)) else {
            return .failure(RunError(message: "출력 파일을 만들지 못했습니다."))
        }
        defer { try? out.close() }
        _ = try? out.seekToEnd()

        let process = Process()
        process.executableURL = exec
        process.arguments = ["-c", launcher, pidURL(id).path, exec.path] + args
        process.currentDirectoryURL = cwd
        var env = ProcessRunner.makeCleanEnvironment(includeKeys: true)
        // Write each line to the file right away so the window shows it live.
        env["PYTHONUNBUFFERED"] = "1"
        process.environment = env
        process.standardInput = FileHandle.nullDevice
        process.standardOutput = out
        process.standardError = out
        do {
            try process.run()
        } catch {
            return .failure(RunError(message: "명령을 실행하지 못했습니다: \(error.localizedDescription)"))
        }
        // The launcher exits right after forking.
        process.waitUntilExit()
        for _ in 0..<20 where !fm.fileExists(atPath: pidURL(id).path) {
            Thread.sleep(forTimeInterval: 0.05)
        }
        if let text = try? String(contentsOf: pidURL(id), encoding: .utf8), let pid = Int32(text.trimmingCharacters(in: .whitespaces)) {
            record.pid = pid
        }
        save(record)
        return .success(record)
    }

    static func save(_ record: RunRecord) {
        let enc = JSONEncoder()
        enc.dateEncodingStrategy = .iso8601
        guard let data = try? enc.encode(record) else { return }
        try? data.write(to: metaURL(record.id), options: .atomic)
    }

    /// All records, newest first.
    static func all() -> [RunRecord] {
        let dec = JSONDecoder()
        dec.dateDecodingStrategy = .iso8601
        guard let urls = try? FileManager.default.contentsOfDirectory(at: directoryURL, includingPropertiesForKeys: nil) else { return [] }
        return urls
            .filter { $0.pathExtension == "json" }
            .compactMap { try? dec.decode(RunRecord.self, from: Data(contentsOf: $0)) }
            .sorted { $0.started > $1.started }
    }

    /// Output with keys masked and the exit marker removed, plus the exit code once known.
    static func read(_ record: RunRecord) -> (text: String, exitCode: Int32?) {
        let raw = (try? String(contentsOf: outputURL(record.id), encoding: .utf8)) ?? ""
        var lines: [Substring] = []
        var code: Int32?
        for line in raw.split(separator: "\n", omittingEmptySubsequences: false) {
            if line.hasPrefix(exitMarker) {
                code = Int32(line.dropFirst(exitMarker.count).trimmingCharacters(in: .whitespaces))
            } else {
                lines.append(line)
            }
        }
        return (Redactor.redact(lines.joined(separator: "\n")), code)
    }

    static func isAlive(_ record: RunRecord) -> Bool {
        guard let pid = record.pid, pid > 0 else { return false }
        return kill(pid, 0) == 0
    }

    /// Rewrite the finished output with keys masked, so the file on disk keeps no secrets.
    static func finalize(_ record: RunRecord, text: String) {
        try? (text + "\n").write(to: outputURL(record.id), atomically: true, encoding: .utf8)
        try? FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: outputURL(record.id).path)
        try? FileManager.default.removeItem(at: pidURL(record.id))
    }

    /// Keep the newest records only. Unfinished ones are never removed.
    static func prune() {
        let records = all()
        guard records.count > keepCount else { return }
        for record in records.dropFirst(keepCount) where record.recorded {
            for url in [outputURL(record.id), metaURL(record.id), pidURL(record.id)] {
                try? FileManager.default.removeItem(at: url)
            }
        }
    }

    static func timeText(_ date: Date) -> String {
        let fmt = DateFormatter()
        fmt.dateFormat = "yyyy-MM-dd HH:mm:ss"
        return fmt.string(from: date)
    }
}
