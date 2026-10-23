import Foundation

/// One pod in `schedule.json`. Matches `podrouter.local_schedule.validate_schedule`.
struct ScheduleEntry: Identifiable, Equatable {
    var id = UUID()
    var podName: String
    var configPath: String
    var enabled: Bool = true
    var weekdays: Set<Int> = [0, 1, 2, 3, 4]
    var onTime: String = "09:00"
    var offTime: String = "18:00"
}

/// Reads and writes `schedule.json` as `{"entries": [...]}`.
/// The older single-pod form (`pod_name` at the top) is still read.
enum ScheduleFile {
    static func load(from url: URL) -> [ScheduleEntry] {
        guard let data = try? Data(contentsOf: url),
              let json = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] else { return [] }
        let raw: [[String: Any]]
        if let list = json["entries"] as? [[String: Any]] {
            raw = list
        } else if json["pod_name"] != nil {
            raw = [json]
        } else {
            raw = []
        }
        return raw.compactMap { item in
            guard let name = item["pod_name"] as? String,
                  let path = item["config_path"] as? String else { return nil }
            var entry = ScheduleEntry(podName: name, configPath: path)
            entry.enabled = item["enabled"] as? Bool ?? true
            if let window = (item["windows"] as? [[String: Any]])?.first {
                if let days = window["weekdays"] as? [Int] { entry.weekdays = Set(days) }
                if let on = window["on"] as? String { entry.onTime = on }
                if let off = window["off"] as? String { entry.offTime = off }
            }
            return entry
        }
    }

    /// Writes the file with mode 0600.
    static func save(_ entries: [ScheduleEntry], to url: URL) throws {
        let list: [[String: Any]] = entries.map { e in
            [
                "pod_name": e.podName,
                "config_path": e.configPath,
                "enabled": e.enabled,
                "windows": [[
                    "weekdays": e.weekdays.sorted(),
                    "on": e.onTime,
                    "off": e.offTime,
                ]],
            ]
        }
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        let data = try JSONSerialization.data(
            withJSONObject: ["entries": list],
            options: [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes]
        )
        try data.write(to: url, options: .atomic)
        chmod(url.path, 0o600)
    }
}
