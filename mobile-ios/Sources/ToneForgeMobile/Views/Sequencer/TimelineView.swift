// TimelineView.swift
//
// DAW-style horizontal timeline for clip arrangement (D-023 Phase 5).
// Shows:
//   - Beat ruler at top with bar/beat markers
//   - Track lanes with draggable clips
//   - Vertical playhead synced to transport
//
// Clips can be:
//   - Dragged horizontally to reposition
//   - Resized by dragging edges
//   - Deleted via context menu or swipe
//   - Added via ChopPickerSheet

import SwiftUI
import ToneForgeEngine

struct TimelineView: View {
    @Binding var arrangement: TimelineArrangement
    let bpm: Double
    let currentBeat: Double
    let isPlaying: Bool
    let onPreviewClip: (TimelineClip) -> Void

    @State private var visibleRange: ClosedRange<Double> = 0...16
    @State private var selectedClipId: UUID?
    @State private var dragOffset: [UUID: CGFloat] = [:]
    @State private var showingChopPicker = false
    @State private var addClipTrack: Int = 0

    private let beatWidth: CGFloat = 40
    private let trackHeight: CGFloat = 60
    private let rulerHeight: CGFloat = 30

    var body: some View {
        VStack(spacing: 0) {
            // Header with arrangement info
            arrangementHeader

            Divider()
                .background(TFTheme.stroke)

            // Timeline content
            GeometryReader { geo in
                ScrollView(.horizontal, showsIndicators: true) {
                    ZStack(alignment: .topLeading) {
                        // Background grid
                        gridBackground(width: totalWidth)

                        // Ruler
                        TimelineRulerView(
                            visibleRange: visibleRange,
                            bpm: bpm,
                            beatWidth: beatWidth
                        )
                        .frame(height: rulerHeight)

                        // Track lanes
                        VStack(spacing: 0) {
                            ForEach(0..<arrangement.trackCount, id: \.self) { track in
                                trackLane(track: track)
                            }
                        }
                        .padding(.top, rulerHeight)

                        // Playhead
                        if isPlaying {
                            playheadLine
                        }
                    }
                    .frame(width: totalWidth, height: contentHeight)
                }
            }

            Divider()
                .background(TFTheme.stroke)

            // Controls
            timelineControls
        }
        .background(TFTheme.background)
        .sheet(isPresented: $showingChopPicker) {
            TimelineChopPickerSheet { chopRef, name in
                addClip(chopRef: chopRef, name: name, track: addClipTrack)
            }
        }
    }

    // MARK: - Dimensions

    private var totalWidth: CGFloat {
        let beats = max(arrangement.totalBeats + 4, 16)
        return CGFloat(beats) * beatWidth
    }

    private var contentHeight: CGFloat {
        rulerHeight + CGFloat(arrangement.trackCount) * trackHeight
    }

    // MARK: - Header

    private var arrangementHeader: some View {
        HStack {
            Text(arrangement.name)
                .font(.headline)
                .foregroundStyle(TFTheme.textPrimary)

            Spacer()

            Text("\(Int(bpm)) BPM")
                .tfChip()

            Text("\(arrangement.clips.count) clips")
                .tfChip()
        }
        .padding(.horizontal)
        .padding(.vertical, 10)
    }

    // MARK: - Grid Background

    private func gridBackground(width: CGFloat) -> some View {
        Canvas { context, size in
            let beatCount = Int(width / beatWidth)

            for beat in 0...beatCount {
                let x = CGFloat(beat) * beatWidth
                let isBar = beat % 4 == 0

                // Vertical grid lines
                var path = Path()
                path.move(to: CGPoint(x: x, y: rulerHeight))
                path.addLine(to: CGPoint(x: x, y: size.height))

                context.stroke(
                    path,
                    with: .color(isBar ? TFTheme.stroke : TFTheme.stroke.opacity(0.5)),
                    lineWidth: isBar ? 1 : 0.5
                )
            }

            // Track lane separators
            for track in 0...arrangement.trackCount {
                let y = rulerHeight + CGFloat(track) * trackHeight

                var path = Path()
                path.move(to: CGPoint(x: 0, y: y))
                path.addLine(to: CGPoint(x: size.width, y: y))

                context.stroke(
                    path,
                    with: .color(TFTheme.stroke),
                    lineWidth: 0.5
                )
            }
        }
    }

    // MARK: - Track Lane

    private func trackLane(track: Int) -> some View {
        ZStack(alignment: .leading) {
            // Lane background
            Rectangle()
                .fill(track % 2 == 0 ? Color.clear : TFTheme.surface.opacity(0.3))

            // Clips on this track
            ForEach(arrangement.clips(onTrack: track)) { clip in
                TimelineClipView(
                    clip: clip,
                    beatWidth: beatWidth,
                    trackHeight: trackHeight,
                    isSelected: selectedClipId == clip.id,
                    onTap: {
                        selectedClipId = clip.id
                        onPreviewClip(clip)
                    },
                    onDragChanged: { offset in
                        dragOffset[clip.id] = offset
                    },
                    onDragEnded: { offset in
                        moveClip(clip, byOffset: offset)
                        dragOffset[clip.id] = nil
                    },
                    onDelete: {
                        arrangement.removeClip(id: clip.id)
                    }
                )
                .offset(x: dragOffset[clip.id] ?? 0)
            }

            // Add button when track is empty
            if arrangement.clips(onTrack: track).isEmpty {
                Button {
                    addClipTrack = track
                    showingChopPicker = true
                } label: {
                    Image(systemName: "plus.circle.dashed")
                        .font(.title3)
                        .foregroundStyle(TFTheme.textSecondary)
                }
                .padding(.leading, 16)
            }
        }
        .frame(height: trackHeight)
        .contentShape(Rectangle())
        .onTapGesture {
            selectedClipId = nil
        }
    }

    // MARK: - Playhead

    private var playheadLine: some View {
        let x = CGFloat(currentBeat) * beatWidth

        return Rectangle()
            .fill(Color.red)
            .frame(width: 2)
            .offset(x: x)
    }

    // MARK: - Controls

    private var timelineControls: some View {
        HStack(spacing: 16) {
            // Add track button
            Button {
                arrangement.trackCount += 1
            } label: {
                Label("Add Lane", systemImage: "plus")
                    .font(.caption)
            }
            .foregroundStyle(TFTheme.textSecondary)

            Spacer()

            // Selected clip info
            if let clipId = selectedClipId,
               let clip = arrangement.clips.first(where: { $0.id == clipId }) {
                Text(clip.name ?? clip.chopRef.displayLabel)
                    .font(.caption)
                    .foregroundStyle(TFTheme.textPrimary)

                Text("Beat \(Int(clip.startBeat + 1))")
                    .font(.caption)
                    .foregroundStyle(TFTheme.textSecondary)

                Button {
                    arrangement.removeClip(id: clipId)
                    selectedClipId = nil
                } label: {
                    Image(systemName: "trash")
                        .foregroundStyle(.red)
                }
            }

            Spacer()

            // Zoom controls (placeholder)
            Button {
                // FUTURE: Zoom out
            } label: {
                Image(systemName: "minus.magnifyingglass")
                    .foregroundStyle(TFTheme.textSecondary)
            }

            Button {
                // FUTURE: Zoom in
            } label: {
                Image(systemName: "plus.magnifyingglass")
                    .foregroundStyle(TFTheme.textSecondary)
            }
        }
        .padding(.horizontal)
        .padding(.vertical, 10)
    }

    // MARK: - Actions

    private func moveClip(_ clip: TimelineClip, byOffset offset: CGFloat) {
        let beatOffset = Double(offset / beatWidth)
        let newStartBeat = TimelineArrangement.quantize(
            clip.startBeat + beatOffset,
            grid: 0.25 // 16th note grid
        )

        var updated = clip
        updated.move(to: newStartBeat)
        arrangement.updateClip(updated)
    }

    private func addClip(chopRef: ChopReference, name: String?, track: Int) {
        // Find the first available position on this track
        let existingClips = arrangement.clips(onTrack: track)
        let startBeat = existingClips.map(\.endBeat).max() ?? 0

        let clip = TimelineClip(
            chopRef: chopRef,
            startBeat: TimelineArrangement.quantize(startBeat, grid: 1.0),
            durationBeats: 1.0,
            track: track,
            name: name
        )
        arrangement.addClip(clip)
    }
}

// MARK: - Ruler View

struct TimelineRulerView: View {
    let visibleRange: ClosedRange<Double>
    let bpm: Double
    let beatWidth: CGFloat

    var body: some View {
        GeometryReader { geo in
            ZStack(alignment: .topLeading) {
                // Tick marks via Canvas
                Canvas { context, size in
                    let beatCount = Int(size.width / beatWidth)

                    for beat in 0...beatCount {
                        let x = CGFloat(beat) * beatWidth
                        let isBar = beat % 4 == 0

                        var tickPath = Path()
                        tickPath.move(to: CGPoint(x: x, y: size.height - (isBar ? 12 : 6)))
                        tickPath.addLine(to: CGPoint(x: x, y: size.height))
                        context.stroke(
                            tickPath,
                            with: .color(TFTheme.textSecondary),
                            lineWidth: 1
                        )
                    }
                }

                // Bar numbers via Text overlays
                ForEach(barPositions(in: geo.size.width), id: \.0) { bar, x in
                    Text("\(bar)")
                        .font(.caption2.monospacedDigit())
                        .foregroundStyle(TFTheme.textSecondary)
                        .position(x: x + 8, y: 10)
                }
            }
        }
        .background(TFTheme.surface)
    }

    private func barPositions(in width: CGFloat) -> [(Int, CGFloat)] {
        let beatCount = Int(width / beatWidth)
        var result: [(Int, CGFloat)] = []
        for beat in stride(from: 0, through: beatCount, by: 4) {
            let barNumber = beat / 4 + 1
            let x = CGFloat(beat) * beatWidth
            result.append((barNumber, x))
        }
        return result
    }
}

// MARK: - Clip View

struct TimelineClipView: View {
    let clip: TimelineClip
    let beatWidth: CGFloat
    let trackHeight: CGFloat
    let isSelected: Bool
    let onTap: () -> Void
    let onDragChanged: (CGFloat) -> Void
    let onDragEnded: (CGFloat) -> Void
    let onDelete: () -> Void

    @GestureState private var isDragging = false

    private var width: CGFloat {
        CGFloat(clip.durationBeats) * beatWidth
    }

    private var xOffset: CGFloat {
        CGFloat(clip.startBeat) * beatWidth
    }

    var body: some View {
        ZStack {
            // Clip body
            RoundedRectangle(cornerRadius: 6)
                .fill(clipColor.opacity(0.6))
                .overlay(
                    RoundedRectangle(cornerRadius: 6)
                        .stroke(
                            isSelected ? Color.white : clipColor,
                            lineWidth: isSelected ? 2 : 1
                        )
                )

            // Content
            HStack(spacing: 4) {
                Image(systemName: clip.chopRef.iconName)
                    .font(.caption2)

                Text(clip.name ?? clip.chopRef.displayLabel)
                    .font(.caption2.weight(.medium))
                    .lineLimit(1)

                Spacer(minLength: 0)
            }
            .padding(.horizontal, 6)
            .foregroundStyle(.white)
        }
        .frame(width: width, height: trackHeight - 8)
        .offset(x: xOffset, y: 4)
        .gesture(dragGesture)
        .onTapGesture(perform: onTap)
        .contextMenu {
            Button(role: .destructive) {
                onDelete()
            } label: {
                Label("Delete", systemImage: "trash")
            }
        }
    }

    private var clipColor: Color {
        switch clip.chopRef {
        case .bundleChop:
            return .orange
        case .packPad:
            return .accentColor
        case .localSample:
            return .green
        case .customURL:
            return .purple
        case .sequence:
            return .teal
        case .synthChord:
            return .pink
        }
    }

    private var dragGesture: some Gesture {
        DragGesture()
            .updating($isDragging) { _, state, _ in
                state = true
            }
            .onChanged { value in
                onDragChanged(value.translation.width)
            }
            .onEnded { value in
                onDragEnded(value.translation.width)
            }
    }
}

// MARK: - Chop Picker Sheet

private struct TimelineChopPickerSheet: View {
    let onSelect: (ChopReference, String?) -> Void
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            VStack(spacing: 20) {
                Text("Add Clip")
                    .font(.title2)

                Text("Select a chop or sample to add to the timeline")
                    .foregroundStyle(.secondary)
                    .multilineTextAlignment(.center)

                // Demo buttons
                VStack(spacing: 12) {
                    Button("Add Demo Sample") {
                        onSelect(.packPad(packId: "demo", padIdx: 51), "Sample")
                        dismiss()
                    }
                    .buttonStyle(.borderedProminent)

                    Button("Add Chord Chop") {
                        onSelect(.bundleChop(presetKey: "harmonic", chopIndex: 0, resolvedId: nil), "Chord")
                        dismiss()
                    }
                    .buttonStyle(.bordered)
                }
                .padding()
            }
            .navigationTitle("Add Clip")
            #if os(iOS)
            .navigationBarTitleDisplayMode(.inline)
            #endif
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                }
            }
        }
    }
}

// MARK: - Preview

#if DEBUG
struct TimelineView_Previews: PreviewProvider {
    struct Wrapper: View {
        @State var arrangement = TimelineArrangement(
            analysisId: "demo",
            name: "Demo Arrangement",
            clips: [
                TimelineClip(
                    chopRef: .packPad(packId: "demo", padIdx: 51),
                    startBeat: 0,
                    durationBeats: 2,
                    track: 0,
                    name: "Kick"
                ),
                TimelineClip(
                    chopRef: .packPad(packId: "demo", padIdx: 52),
                    startBeat: 4,
                    durationBeats: 1,
                    track: 1,
                    name: "Snare"
                ),
                TimelineClip(
                    chopRef: .bundleChop(presetKey: "harmonic", chopIndex: 0, resolvedId: nil),
                    startBeat: 8,
                    durationBeats: 4,
                    track: 2,
                    name: "Chord Stab"
                )
            ],
            trackCount: 4
        )

        var body: some View {
            TimelineView(
                arrangement: $arrangement,
                bpm: 120,
                currentBeat: 2.5,
                isPlaying: true,
                onPreviewClip: { _ in }
            )
        }
    }

    static var previews: some View {
        Wrapper()
            .preferredColorScheme(.dark)
    }
}
#endif
