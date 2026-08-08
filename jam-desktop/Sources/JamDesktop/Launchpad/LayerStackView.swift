import SwiftUI
import JamDesktopCore

/// A per-category rack for building a groove by hand: one row per musical layer
/// (Drums/Bass/Chords/Lead/Texture/Vocal), each showing the active loop with a
/// swap menu + play/stop. Complements Instant Groove (auto-fill). Layers loop
/// bar-synced via the LaunchpadController loop path.
struct LayerStackView: View {
    @EnvironmentObject private var session: SessionController
    private var launchpad: LaunchpadController { session.launchpad }
    private let categories: [LaunchpadController.PadCategory] =
        [.drums, .bass, .chords, .lead, .texture, .vocal]

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text("Layers").font(.headline)
                Spacer()
                Button {
                    launchpad.instantGroove()
                } label: { Label("Instant Groove", systemImage: "bolt.fill") }
                    .buttonStyle(.borderedProminent)
                    .disabled(launchpad.assignments.isEmpty)
                Button {
                    for c in categories { launchpad.clearLayer(c) }
                } label: { Label("Stop", systemImage: "stop.fill") }
            }
            ForEach(categories, id: \.self) { cat in
                layerRow(cat)
            }
        }
        .padding(12)
    }

    @ViewBuilder
    private func layerRow(_ cat: LaunchpadController.PadCategory) -> some View {
        let pads = launchpad.pads(in: cat)
        let active = launchpad.activeLayer(cat)
        let color = Color(hex: UInt32(cat.colorHex))
        HStack(spacing: 10) {
            RoundedRectangle(cornerRadius: 3).fill(color).frame(width: 6, height: 32)
            Text(cat.rawValue)
                .font(.caption.bold())
                .foregroundStyle(color)
                .frame(width: 66, alignment: .leading)
            Menu {
                ForEach(pads, id: \.self) { pad in
                    Button(launchpad.padLabel(pad)) { launchpad.setLayer(pad, category: cat) }
                }
                if active != nil {
                    Divider()
                    Button("Clear", role: .destructive) { launchpad.clearLayer(cat) }
                }
            } label: {
                HStack {
                    Text(active.map { launchpad.padLabel($0) } ?? (pads.isEmpty ? "—" : "empty"))
                        .lineLimit(1)
                        .foregroundStyle(active != nil ? Color.primary : Color.secondary)
                    Spacer()
                    Image(systemName: "chevron.down").font(.caption2).foregroundStyle(.secondary)
                }
                .padding(.horizontal, 8).padding(.vertical, 5)
                .background(RoundedRectangle(cornerRadius: 5).fill(active != nil ? color.opacity(0.18) : Color.white.opacity(0.04)))
            }
            .buttonStyle(.plain)
            .disabled(pads.isEmpty)
            Button {
                launchpad.toggleLayer(cat)
            } label: {
                Image(systemName: active != nil ? "stop.fill" : "play.fill")
                    .frame(width: 22)
            }
            .buttonStyle(.plain)
            .foregroundStyle(active != nil ? color : Color.secondary)
            .disabled(pads.isEmpty)
        }
        .opacity(pads.isEmpty ? 0.4 : 1)
    }
}
