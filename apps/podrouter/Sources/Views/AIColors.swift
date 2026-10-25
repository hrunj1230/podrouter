import SwiftUI

/// Colors for buttons that open an AI agent, so they stand apart from the
/// app's regular (system blue) buttons.
enum AIColors {
    /// Claude: warm orange.
    static let claude = Color(red: 0.85, green: 0.47, blue: 0.34)
    /// Codex: deep cobalt, darker than the system accent blue.
    static let codex = Color(red: 0.13, green: 0.30, blue: 0.78)
}
