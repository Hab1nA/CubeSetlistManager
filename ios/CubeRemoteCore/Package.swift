// swift-tools-version:5.9
import PackageDescription

let package = Package(
    name: "CubeRemoteCore",
    platforms: [.iOS(.v16), .macOS(.v13)],
    products: [
        .library(name: "CubeRemoteCore", targets: ["CubeRemoteCore"]),
    ],
    targets: [
        .target(name: "CubeRemoteCore"),
        .testTarget(name: "CubeRemoteCoreTests",
                    dependencies: ["CubeRemoteCore"]),
    ]
)
