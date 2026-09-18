// ProjectCoordinator.swift
//
// Projects/Workspaces v1 — the runtime glue between AppState and the
// ProjectSnapshot contract (ToneForgeEngine/Projects). Owns WHEN a
// workspace is captured/restored:
//
//   * AUTO-SAVE: any tracked store mutation (pad assignments, pad FX /
//     hidden pads / section gates, sequencer patterns, launchpad
//     settings, pack activation) schedules a debounced (~2 s) capture
//     into the song's WORKING project. Cheap: a few KB of JSON.
//   * SONG LOAD: `songDidActivate` restores the pending project (an
//     explicit Library → Projects tap) or, absent that, the song's
//     working project. Store-backed state restores synchronously;
//     borrows are re-derived AFTER the auto-kit lands
//     (`autoKitDidMount`) because the borrow mount replaces the
//     active pack.
//   * BORROW RE-DERIVATION: re-requests the donor's borrow and
//     matches the returned pads by content address (span/assetId —
//     BorrowRef.matches), NEVER by response padIdx. A missing donor
//     (or drifted kit) surfaces a "Needs <donor>" banner instead of a
//     crash or a silent drop.
//
// State ownership: ProjectStateBridge does the store⇄snapshot
// translation (unit-tested); this type only sequences it against the
// app's load/kit lifecycle.

import Foundation
import Combine
import ToneForgeEngine

@MainActor
public final class ProjectCoordinator: ObservableObject {

    /// Durable projects on disk, newest-updated first (Library list).
    @Published public private(set) var savedProjects: [Project] = []
    /// User-facing restore problem ("Needs <donor>…"). Shown as a
    /// dismissible banner in RootView; nil = nothing to report.
    @Published public var notice: String?

    /// Unowned: AppState owns this coordinator; identical lifetimes
    /// (same pattern as ArrangementModel / ModeCoordinator).
    private unowned let app: AppState
    private let store: ProjectStore
    /// Same UserDefaults-backed per-song arrangement persistence the
    /// ArrangementModel writes (`jamn.arrangement.<analysisId>`) — the
    /// bridge reads/writes it directly; ArrangementModel re-reads it
    /// on `loadSong`.
    private let arrangementStore = ArrangementStore()

    /// Snapshot to apply on the next song activation (an explicit
    /// project load). Takes precedence over the working project.
    private var pendingRestore: ProjectSnapshot?
    /// Borrow refs waiting for the auto-kit to land before re-derive.
    private var pendingBorrowRefs: [BorrowRef] = []
    /// Debounced working-project save.
    private var workingSaveTask: Task<Void, Never>?
    /// True while restore/reset writes stores — their objectWillChange
    /// must not re-schedule an auto-save mid-write.
    private var suppressAutoSave = false

    private var cancellables: Set<AnyCancellable> = []

    init(app: AppState, store: ProjectStore? = nil) {
        self.app = app
        self.store = store ?? ProjectStore()
        self.savedProjects = self.store.list()

        // Auto-save: every tracked store schedules the debounced
        // working capture. objectWillChange fires BEFORE the mutation;
        // the 2 s debounce guarantees capture reads the settled state.
        let sources: [ObservableObjectPublisher] = [
            app.padAssignmentStore.objectWillChange,
            app.sampleSettings.objectWillChange,
            app.sequencerPatternStore.objectWillChange,
            app.jamSettings.objectWillChange,
        ]
        for source in sources {
            source
                .sink { [weak self] _ in self?.noteWorkspaceChanged() }
                .store(in: &cancellables)
        }
        // Forward own changes so views observing only AppState (the
        // RootView banner) re-render — same pattern as outputRecorder.
        objectWillChange
            .sink { [weak app] _ in app?.objectWillChange.send() }
            .store(in: &cancellables)
    }

    // MARK: - Capture

    /// The current workspace as a snapshot. nil when no song is loaded
    /// (v1 projects are song-anchored).
    public func captureSnapshot() -> ProjectSnapshot? {
        guard let bundle = app.currentBundle else { return nil }
        return ProjectStateBridge.capture(
            analysisId: bundle.analysisId,
            padAssignments: app.padAssignmentStore,
            sampleSettings: app.sampleSettings,
            patternStore: app.sequencerPatternStore,
            jamSettings: app.jamSettings,
            arrangementStore: arrangementStore,
            borrows: currentBorrowRefs()
        )
    }

    /// Content-addressed refs for the mounted borrow (empty when none).
    /// Reads the ARRANGED active pack when it is the borrow (so
    /// targetPadIdx records real placement), falling back to the raw
    /// fetched pack. BorrowRef.init? drops pads without a content
    /// address — identity NEVER falls back to response padIdx.
    func currentBorrowRefs() -> [BorrowRef] {
        guard let ctx = app.activeBorrowContext else { return [] }
        let activePads = app.activeSamplePack?.pack.pads ?? []
        let isBorrowActive = activePads.contains {
            $0.source == "donor" || $0.source == "initial"
        }
        let pads = isBorrowActive ? activePads : ctx.fetched.pads
        return pads.compactMap {
            BorrowRef(pad: $0, donorSongId: ctx.donorId,
                      donorName: ctx.donorName)
        }
    }

    // MARK: - Auto-save (working project)

    /// Schedule a debounced capture into the loaded song's working
    /// project. Cheap to call from any mutation point.
    public func noteWorkspaceChanged() {
        guard !suppressAutoSave, let bundle = app.currentBundle else { return }
        let analysisId = bundle.analysisId
        workingSaveTask?.cancel()
        workingSaveTask = Task { @MainActor [weak self] in
            try? await Task.sleep(nanoseconds: 2_000_000_000)
            guard !Task.isCancelled else { return }
            self?.saveWorkingNow(analysisId: analysisId)
        }
    }

    private func saveWorkingNow(analysisId: String) {
        guard app.currentBundle?.analysisId == analysisId,
              let snapshot = captureSnapshot() else { return }
        let existing = store.loadWorking(analysisId: analysisId)
        let project = Project(
            id: existing?.id ?? UUID(),
            name: existing?.name ?? "Working",
            createdAt: existing?.createdAt ?? Date(),
            updatedAt: Date(),
            baseSongId: analysisId,
            baseSongTitle: app.currentBundle?.meta.title,
            snapshot: snapshot
        )
        try? store.saveWorking(project)
    }

    // MARK: - Song-load lifecycle (called from AppState)

    /// Called from `AppState.activate` once the song's baseline state
    /// is up (grid, arrangement, layers) and BEFORE `onReady`.
    /// Restores the explicit pending project or the song's working
    /// project; borrows wait for `autoKitDidMount`.
    func songDidActivate(bundle: SongBundle) {
        notice = nil
        pendingBorrowRefs = []
        workingSaveTask?.cancel()
        let analysisId = bundle.analysisId
        let snapshot = pendingRestore
            ?? store.loadWorking(analysisId: analysisId)?.snapshot
        pendingRestore = nil
        guard let snapshot else { return }

        suppressAutoSave = true
        ProjectStateBridge.restore(
            snapshot,
            analysisId: analysisId,
            padAssignments: app.padAssignmentStore,
            sampleSettings: app.sampleSettings,
            patternStore: app.sequencerPatternStore,
            jamSettings: app.jamSettings,
            arrangementStore: arrangementStore
        )
        suppressAutoSave = false

        // Re-derive the surface from the restored stores: gates/tempo
        // context, pad visuals + bindings, and the arrangement runtime
        // (activate() built all three from pre-restore state).
        app.modeCoordinator.applyGridContext()
        app.modeCoordinator.refreshLayout()
        reloadArrangement(bundle)

        pendingBorrowRefs = snapshot.borrows
    }

    /// Called after `loadAutoKit` settles (kit mounted or failed) for
    /// the song-open load — the earliest point a borrow mount won't be
    /// clobbered by the kit activation. Consumes the pending refs.
    func autoKitDidMount(analysisId: String) {
        guard !pendingBorrowRefs.isEmpty,
              app.currentBundle?.analysisId == analysisId else { return }
        let refs = pendingBorrowRefs
        pendingBorrowRefs = []
        restoreBorrows(refs)
    }

    /// Re-request the donor's borrow and verify each ref resolved —
    /// matching by CONTENT (BorrowRef.matches: assetId, else span ±2 ms
    /// + stemRole), never by response index. v1 supports the single
    /// active borrow iOS has today, so all refs share one donor.
    private func restoreBorrows(_ refs: [BorrowRef]) {
        guard let first = refs.first else { return }
        let donorLabel = first.donorName ?? "the borrowed song"
        app.loadBorrowLoops(
            donorId: first.donorSongId,
            stem: Self.logicalStem(first.stemRole)
        ) { [weak self] result in
            guard let self else { return }
            switch result {
            case .success(let fetched):
                let missing = refs.filter { ref in
                    !fetched.pads.contains { ref.matches(pad: $0) }
                }
                if !missing.isEmpty {
                    self.notice = "Needs \u{201C}\(donorLabel)\u{201D}: "
                        + "\(missing.count) borrowed pad"
                        + (missing.count == 1 ? "" : "s")
                        + " no longer in that song's kit — the rest restored."
                }
            case .failure:
                self.notice = "Needs \u{201C}\(donorLabel)\u{201D} — its "
                    + "borrowed loops couldn't be restored. Re-borrow "
                    + "from the Remix sheet when it's available."
            }
        }
    }

    /// Concrete stem role → the logical family the borrow endpoint
    /// takes as its `stem` param (backend `_logical_stem` twin). The
    /// borrow response carries BOTH songs' full curated kits whatever
    /// the param, so this only needs to be a valid family.
    static func logicalStem(_ role: String) -> String {
        let r = role.lowercased()
        if r.contains("drum") { return "drums" }
        if r == "bass" { return "bass" }
        if r == "vocals" || r == "vocal" { return "vocals" }
        return "other"
    }

    // MARK: - Explicit save / load / reset

    /// "Save workspace": name a durable copy of the current state.
    public func saveCurrentAsProject(named name: String) {
        guard let bundle = app.currentBundle,
              let snapshot = captureSnapshot() else { return }
        let trimmed = name.trimmingCharacters(in: .whitespacesAndNewlines)
        let project = Project(
            name: trimmed.isEmpty ? "Untitled project" : trimmed,
            baseSongId: bundle.analysisId,
            baseSongTitle: bundle.meta.title,
            snapshot: snapshot
        )
        try? store.save(project)
        refreshList()
    }

    /// Library → Projects tap: load the base song, then restore the
    /// snapshot over it (songDidActivate consumes `pendingRestore`).
    public func load(_ project: Project) {
        pendingRestore = project.snapshot
        Task { @MainActor [weak self] in
            guard let self else { return }
            await self.app.loadBundle(analysisId: project.baseSongId) {
                [weak self] in self?.app.openSong()
            }
            if self.app.currentBundle?.analysisId != project.baseSongId {
                // Load failed — don't let the snapshot ambush the next
                // unrelated song load.
                self.pendingRestore = nil
                self.notice = "Couldn't load \u{201C}"
                    + (project.baseSongTitle ?? "the project's song")
                    + "\u{201D} for this project."
            }
        }
    }

    /// Clear the loaded song's workspace back to its default state
    /// (fresh auto-kit, no assignments/FX/gates/arrangement/borrows)
    /// and drop its working project. The sequencer pattern library is
    /// untouched.
    public func resetToSong() {
        guard let bundle = app.currentBundle else { return }
        workingSaveTask?.cancel()
        pendingBorrowRefs = []
        suppressAutoSave = true
        ProjectStateBridge.reset(
            analysisId: bundle.analysisId,
            padAssignments: app.padAssignmentStore,
            sampleSettings: app.sampleSettings,
            arrangementStore: arrangementStore
        )
        suppressAutoSave = false
        store.deleteWorking(analysisId: bundle.analysisId)
        app.modeCoordinator.applyGridContext()
        app.modeCoordinator.refreshLayout()
        reloadArrangement(bundle)
        // Rebuild the default kit; activating a non-borrow pack also
        // clears any mounted borrow context.
        app.loadAutoKit(kind: "auto", announce: false)
    }

    // MARK: - Row operations (Library list)

    public func rename(_ project: Project, to name: String) {
        let trimmed = name.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }
        _ = try? store.rename(projectId: project.id, to: trimmed)
        refreshList()
    }

    public func duplicate(_ project: Project) {
        _ = try? store.duplicate(projectId: project.id,
                                 name: "\(project.name) copy")
        refreshList()
    }

    public func delete(_ project: Project) {
        try? store.delete(projectId: project.id)
        refreshList()
    }

    /// Row "Reset to song": empty the STORED snapshot (the project
    /// becomes "just the song"); when that project's song is loaded,
    /// also reset the live surface.
    public func resetProjectToSong(_ project: Project) {
        var emptied = project
        emptied.snapshot = ProjectSnapshot()
        emptied.updatedAt = Date()
        try? store.save(emptied)
        refreshList()
        if app.currentBundle?.analysisId == project.baseSongId {
            resetToSong()
        }
    }

    private func refreshList() {
        savedProjects = store.list()
    }

    private func reloadArrangement(_ bundle: SongBundle) {
        app.arrangement.loadSong(
            analysisId: bundle.analysisId,
            sections: bundle.timeline.sections.map {
                ArrangementSectionInput(
                    type: $0.label ?? "", start: $0.start, end: $0.end)
            }
        )
    }
}
