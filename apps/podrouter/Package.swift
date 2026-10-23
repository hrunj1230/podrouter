// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "podrouter",
    platforms: [
        .macOS(.v14)
    ],
    products: [
        .executable(
            name: "podrouter",
            targets: ["podrouter"]
        )
    ],
    targets: [
        .executableTarget(
            name: "podrouter",
            path: "Sources"
        )
    ]
)
