# mobile-ios SETUP

Bring-up instructions for the Swift package + the iOS app target.
For the release ship gate (automated suites + on-device checklist +
diagnostics budgets), see [docs/mobile-testing.md](docs/mobile-testing.md).

## Prereqs

- macOS with Xcode 15+ installed at `/Applications/Xcode.app`.
- Command Line Tools alone will build the package but **cannot run tests**
  (the CLT toolchain ships without `XCTest`). Use Xcode's toolchain for
  tests — see the two options below.

## Build the package (headless)

From `mobile-ios/`:

```bash
DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer xcrun swift build
```

Use the Xcode developer dir for the same reason as the tests: the
CommandLineTools toolchain (currently 6.2.3) and the Xcode toolchain
(6.3.3) produce incompatible `.swiftmodule` files. If you mix them,
you'll get errors like `module compiled with Swift 6.3.3 cannot be
imported by the Swift 6.2.3 compiler`. Pick one and stick with it —
Xcode's is the only one that also runs tests.

## Run the tests

Because `XCTest` is only shipped with Xcode (not the CommandLineTools
package), invoke the tests via Xcode's developer dir:

```bash
DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer xcrun swift test
```

Or, permanently, switch the active developer dir once:

```bash
sudo xcode-select -s /Applications/Xcode.app/Contents/Developer
```

after which plain `swift test` works.

Note: a handful of tests (`JamScreenSnapshotTests`, parts of
`AudioTranscoderTests`) need UIKit/an iOS runtime and `XCTSkip` on
macOS. To run the whole suite on a simulator, use the generated app
project's scheme (its test action includes both SwiftPM test bundles
and the UI-test bundle):

```bash
xcodegen generate   # once, or after editing project.yml
xcodebuild test -project ToneForgeMobile.xcodeproj \
  -scheme ToneForgeMobileApp \
  -destination 'platform=iOS Simulator,name=iPhone 17 Pro'
```

### Snapshot goldens

The JAM-screen snapshot tests compare against golden PNGs in
`Tests/ToneForgeMobileTests/Fixtures/Goldens/` (see DECISIONS.md
D-012). To (re-)record them:

```bash
TEST_RUNNER_TONEFORGE_SNAPSHOT_RECORD=1 \
TEST_RUNNER_TONEFORGE_SNAPSHOT_DIR="$PWD/Tests/ToneForgeMobileTests/Fixtures/Goldens" \
xcodebuild test -project ToneForgeMobile.xcodeproj \
  -scheme ToneForgeMobileApp \
  -destination 'platform=iOS Simulator,name=iPhone 17 Pro' \
  -only-testing:ToneForgeMobileTests/JamScreenSnapshotTests
```

Record mode intentionally fails the tests after writing the PNGs;
re-run without the env vars to verify the comparison passes. Record
on the same pinned simulator runtime you verify on — text
rasterization drifts between OS releases.

### UI tests (attestation flow)

`UITests/AttestationUITests.swift` drives the app through the
`-uitest-reset-attestation` / `-uitest-stub-import` launch-argument
contract (no network, no permission dialogs):

```bash
xcodebuild test -project ToneForgeMobile.xcodeproj \
  -scheme ToneForgeMobileApp \
  -destination 'platform=iOS Simulator,name=iPhone 17 Pro' \
  -only-testing:ToneForgeMobileUITests
```

### Compliance grep gate

No streaming-service ingestion may exist in the mobile app. This must
come back empty:

```bash
grep -riE "youtube|yt-dlp|spotify" Sources App UITests
```

## Open in Xcode

```bash
open Package.swift
```

Xcode will open the package as a workspace. You can build the
`ToneForgeMobile` scheme against any iOS Simulator. There is no `.xcodeproj`
checked in — SPM is the source of truth.

## Generate the iOS app project

The app target is described in `project.yml` (XcodeGen). Install once:

```bash
brew install xcodegen
```

then from `mobile-ios/`:

```bash
xcodegen generate
```

This produces `ToneForgeMobile.xcodeproj` linked against the SwiftPM
package. The app target's sources live in `App/` (Info.plist, entry
point, asset catalog with app icon + accent color).

### Headless build against the simulator

```bash
xcodebuild -project ToneForgeMobile.xcodeproj \
  -scheme ToneForgeMobileApp \
  -destination 'platform=iOS Simulator,name=iPhone 17' \
  build
```

`ToneForgeMobile.xcodeproj` is regenerated from `project.yml` — do not
edit it by hand. It's gitignored; run `xcodegen generate` after
cloning.

## Backend

Point `AppState.backendBaseURL` at a reachable tone-forge backend. For
local dev on a simulator this is `http://127.0.0.1:8000` (the default).
For a phone on the same wifi, use the Mac's LAN IP, e.g.
`http://192.168.1.10:8000`.

Bundle endpoint:

```
GET /api/song/{analysisId}/bundle
```

returns the v1 `SongBundle` JSON shape — see `Sources/ToneForgeEngine/SongBundle.swift`.
