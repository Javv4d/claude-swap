// swift-tools-version:5.9
// The macOS menu bar panel: a status item whose click drops down a popover
// with the live `cswap` TUI inside an embedded terminal. Built on demand by
// `cswap panel` (see ../panel.py); not part of the Python package's import
// graph. Needs the Xcode Command Line Tools.
import PackageDescription

let package = Package(
    name: "CswapPanel",
    platforms: [.macOS(.v13)],
    dependencies: [
        .package(url: "https://github.com/migueldeicaza/SwiftTerm.git", from: "1.2.0"),
    ],
    targets: [
        .executableTarget(
            name: "CswapPanel",
            dependencies: [.product(name: "SwiftTerm", package: "SwiftTerm")],
            path: "Sources/CswapPanel"
        ),
    ]
)
