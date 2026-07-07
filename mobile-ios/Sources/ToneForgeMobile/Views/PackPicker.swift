// PackPicker.swift
//
// Swipeable pack strip above the ModeGridView — the D-016 heir of the
// old PackCarousel (which paged whole grids; the single ModeGridView
// owns the pads now, so only the *name strip* pages). One page per
// loadable pack (Song DNA → Starter → downloaded curated, from
// `AppState.carouselPages`); a horizontal swipe on the strip moves to
// the neighbouring pack via `onSelect` and the grid repaints. Ringing
// voices from other packs keep sounding — the multi-pack scheduler
// never stops voices on pack swap. Tapping the pack name opens
// BrowsePacksSheet via `onOpen` (parent owns the sheet state).
//
// Deliberately NOT a `TabView(.page)`: page style misrenders inside
// the mode-row HStack (dots overlap the label, neighbouring pages
// bleed in, swipe goes dead), so the strip derives its "page" straight
// from `activePackId` and handles the swipe with a plain DragGesture.
// No local selection state → no sync loops with external activations
// (BrowsePacksSheet, boot, song-eject fallback).

import SwiftUI

struct PackPicker: View {
    /// Carousel pages in display order (`AppState.carouselPages`).
    let pages: [PackPage]
    /// packId of the currently active pack (drives which page shows).
    let activePackId: String?
    /// Swipe settled on a neighbour → activate that pack.
    let onSelect: (String) -> Void
    /// Tap on the pack name → open BrowsePacksSheet.
    let onOpen: () -> Void

    var body: some View {
        if pages.isEmpty {
            nameRow(label: "No pack")
                .onTapGesture { onOpen() }
        } else {
            swipeStrip
        }
    }

    // MARK: - Swipe strip

    /// Index of the page shown. Falls back to 0 when the active pack
    /// isn't a page (transient boot state) so the strip still renders.
    private var currentIndex: Int {
        pages.firstIndex(where: { $0.id == activePackId }) ?? 0
    }

    private var swipeStrip: some View {
        let idx = currentIndex
        return VStack(spacing: 5) {
            nameRow(label: pages[idx].displayName)
            dots(current: idx)
        }
        .padding(.vertical, 6)
        .contentShape(Rectangle())
        // Tap + drag as sibling gestures (NOT a Button — a Button
        // swallows the touch before the parent's DragGesture ever
        // sees movement, which killed the swipe).
        .onTapGesture { onOpen() }
        .gesture(
            DragGesture(minimumDistance: 15)
                .onEnded { value in
                    // Horizontal intent only — let vertical scrolls
                    // (if the strip ever lands in one) pass through.
                    guard abs(value.translation.width)
                        > abs(value.translation.height) else { return }
                    if value.translation.width < 0, idx + 1 < pages.count {
                        onSelect(pages[idx + 1].id)
                    } else if value.translation.width > 0, idx > 0 {
                        onSelect(pages[idx - 1].id)
                    }
                }
        )
        .animation(.snappy(duration: 0.2), value: idx)
        .accessibilityElement(children: .combine)
        .accessibilityLabel("Sample pack: \(pages[idx].displayName)")
        .accessibilityHint("Swipe left or right to change pack")
    }

    private func dots(current: Int) -> some View {
        HStack(spacing: 5) {
            ForEach(Array(pages.enumerated()), id: \.element.id) { i, _ in
                Circle()
                    .fill(i == current
                          ? Color.primary
                          : Color.secondary.opacity(0.35))
                    .frame(width: 6, height: 6)
            }
        }
    }

    // MARK: - Name row

    /// Plain label, NOT a Button — the strip's container owns both
    /// the tap (open Browse) and the drag (change pack); a Button
    /// here would swallow the touch and kill the swipe.
    private func nameRow(label: String) -> some View {
        HStack(spacing: 6) {
            Text(label)
                .font(.headline)
                .foregroundStyle(.primary)
                .lineLimit(1)
            Image(systemName: "chevron.down")
                .font(.caption)
                .foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity)
    }
}
