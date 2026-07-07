// ToneForgeApp.swift (app target entry)
//
// The app target is deliberately tiny — everything lives in the
// ``ToneForgeMobile`` library so we can also run the code in tests
// and (later) previews. This file just declares @main and hands over
// to the library's ``ToneForgeScene``.

import SwiftUI
import ToneForgeMobile

@main
struct ToneForgeAppEntry: App {
    var body: some Scene {
        ToneForgeScene()
    }
}
