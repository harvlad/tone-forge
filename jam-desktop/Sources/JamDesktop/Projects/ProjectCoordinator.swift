// ProjectCoordinator.swift
//
// Projects v1 on desktop — the runtime glue between SessionController
// and the shared ProjectSnapshot contract (ToneForgeEngine/Projects),
// mirroring the iOS ProjectCoordinator. Owns WHEN a workspace is
// captured/restored:
//
//   * AUTO-SAVE: any tracked store mutation (pad assignments, pad FX,
//     chop edits, sequencer patterns, arrangement, launchpad surface
//     settings incl. section gate) schedules a debounced (~2 s)
//     capture into the song's WORKING project. Cheap: a few KB of
//     JSON under Application Support/Jamn/projects/working/.
//   * SONG ATTACH: `songDidActivate` (called from
//     SessionController.attach BEFORE the arrangement runtime and
//     chop-edit overlay read their stores) restores the pending
//     project (an explicit Projects-sheet tap) or, absent that, the
//     song's working project. Store-backed state restores
//     synchronously; borrows wait for the auto-kit
//     (`autoKitDidMount`) because the borrow mount replaces the grid.
//   * BORROW RE-DERIVATION: re-requests the donor's borrow and
//     verifies each saved ref resolved by CONTENT (BorrowRef.matches:
//     assetId, else span ±2 ms + stemRole) — NEVER by response
//     padIdx. A missing donor (or drifted kit) surfaces a
//     "Needs <donor>" banner instead of a crash or a silent drop.
//
// GLOBAL-STORE SWAP SEMANTICS: desktop's assignment/FX stores are
// global, exactly like iOS's — restore snapshot-and-swaps their whole
// contents over them (the glue-over-stores approach), and
// reset-to-song empties them + rebuilds the default auto-kit.
// ProjectStateBridge (JamDesktopCore, unit-tested) owns WHAT a
// snapshot is made of; this type only sequences it against the app's
// attach/kit lifecycle.

import Foundation
import Combine
import ToneForgeEngine
import JamDesktopCore

@MainActor
final class ProjectCoordinator: ObservableObject {

    /// Durable projects on disk, newest-updated first (Projects sheet).
    @Published private(set) var savedProjects: [Project] = []
    /// User-facing restore problem ("Needs <donor>…"). Shown as a
    /// dismissible banner in RootView; nil = nothing to report.
    @Published var notice: String?

    /// Unowned: SessionController owns this coordinator (lazy let);
    /// identical lifetimes.
    private unowned let session: SessionController
    private let store: ProjectStore

    /// Snapshot to apply when its base song next activates (an
    /// explicit Projects-sheet load). Keyed by analysisId so a load
    /// failure can never ambush an unrelated song.
    private var pendingRestore: (analysisId: String, snapshot: ProjectSnapshot)?
    /// The last snapshot restored/captured for the current song — the
    /// preserve-on-round-trip source (other modes' pad axes,
    /// hiddenPads, transform chains). nil = fresh song.
    private var preservedSnapshot: ProjectSnapshot?
    /// Borrow refs waiting for the auto-kit to land before re-derive.
    private var pendingBorrowRefs: [BorrowRef] = []
    /// The workspace's saved trigger mode, re-asserted after a borrow
    /// mount (which force-switches the surface to Latch).
    private var pendingPlaybackMode: LaunchpadController.PadPlaybackMode?
    /// The attached song this coordinator is tracking.
    private var currentAnalysisId: String?
    private var currentTitle: String?
    /// Debounced working-project save.
    private var workingSaveTask: Task<Void, Never>?
    /// True while restore/reset writes stores — their onChanged hooks
    /// must not re-schedule an auto-save mid-write.
    private var suppressAutoSave = false

    private var cancellables: Set<AnyCancellable> = []

    init(session: SessionController, store: ProjectStore? = nil) {
        self.session = session
        self.store = store ?? ProjectStore()
        self.savedProjects = self.store.list()

        // Auto-save: every tracked store reports mutations through its
        // onChanged hook (the desktop stores are @Observable, not
        // ObservableObject, so there is no objectWillChange to sink).
        let bump: () -> Void = { [weak self] in self?.noteWorkspaceChanged() }
        session.padAssignmentStore.onChanged = bump
        session.padFXStore.onChanged = bump
        session.patternStore.onChanged = bump
        session.arrangementStore.onChanged = bump
        session.chopEditStore.onChanged = bump
        session.launchpad.onSurfaceSettingChanged = bump

        // Forward own changes so views observing only SessionController
        // (the RootView banner) re-render.
        objectWillChange
            .sink { [weak session] _ in session?.objectWillChange.send() }
            .store(in: &cancellables)
    }

    // MARK: - Capture

    /// The current workspace as a snapshot. nil when no song is
    /// attached (v1 projects are song-anchored). Also refreshes the
    /// preserved snapshot so later captures keep preserving.
    func captureSnapshot() -> ProjectSnapshot? {
        guard let analysisId = currentAnalysisId,
              session.attachedAnalysisId == analysisId else { return nil }
        let snapshot = ProjectStateBridge.capture(
            analysisId: analysisId,
            padAssignments: session.padAssignmentStore,
            padFX: session.padFXStore,
            patternStore: session.patternStore,
            chopEditStore: session.chopEditStore,
            arrangementStore: session.arrangementStore,
            launchpad: session.launchpad,
            preserved: preservedSnapshot,
            borrows: currentBorrowRefs()
        )
        preservedSnapshot = snapshot
        return snapshot
    }

    /// Content-addressed refs for the mounted borrow (empty when
    /// none). Guarded on the borrow grid actually being up — a stale
    /// context after a grid swap must not resurrect refs.
    func currentBorrowRefs() -> [BorrowRef] {
        guard let ctx = session.activeBorrowContext,
              !session.launchpad.borrowSourceLabels.isEmpty else { return [] }
        return ProjectStateBridge.borrowRefs(
            pads: ctx.pads, donorId: ctx.donorId, donorName: ctx.donorName)
    }

    // MARK: - Auto-save (working project)

    /// Schedule a debounced capture into the attached song's working
    /// project. Cheap to call from any mutation hook.
    func noteWorkspaceChanged() {
        guard !suppressAutoSave, let analysisId = currentAnalysisId,
              session.attachedAnalysisId == analysisId else { return }
        workingSaveTask?.cancel()
        workingSaveTask = Task { @MainActor [weak self] in
            try? await Task.sleep(nanoseconds: 2_000_000_000)
            guard !Task.isCancelled else { return }
            self?.saveWorkingNow(analysisId: analysisId)
        }
    }

    private func saveWorkingNow(analysisId: String) {
        guard currentAnalysisId == analysisId,
              let snapshot = captureSnapshot() else { return }
        let existing = store.loadWorking(analysisId: analysisId)
        let project = Project(
            id: existing?.id ?? UUID(),
            name: existing?.name ?? "Working",
            createdAt: existing?.createdAt ?? Date(),
            updatedAt: Date(),
            baseSongId: analysisId,
            baseSongTitle: currentTitle,
            snapshot: snapshot
        )
        try? store.saveWorking(project)
    }

    // MARK: - Attach lifecycle (called from SessionController)

    /// Called from `SessionController.attach` once the bundle + grid
    /// baseline are up but BEFORE the arrangement runtime and chop-edit
    /// overlay read their stores — so a restored workspace is what they
    /// read. Borrows wait for `autoKitDidMount`.
    func songDidActivate(bundle: SongBundle) {
        notice = nil
        pendingBorrowRefs = []
        pendingPlaybackMode = nil
        workingSaveTask?.cancel()
        preservedSnapshot = nil
        let analysisId = bundle.analysisId
        currentAnalysisId = analysisId
        currentTitle = bundle.meta.title

        var snapshot: ProjectSnapshot?
        if let pending = pendingRestore {
            pendingRestore = nil
            if pending.analysisId == analysisId {
                snapshot = pending.snapshot
            }
        }
        if snapshot == nil {
            snapshot = store.loadWorking(analysisId: analysisId)?.snapshot
        }
        guard let snapshot else { return }
        applyRestore(snapshot, analysisId: analysisId)
    }

    private func applyRestore(_ snapshot: ProjectSnapshot, analysisId: String) {
        preservedSnapshot = snapshot
        suppressAutoSave = true
        ProjectStateBridge.restore(
            snapshot,
            analysisId: analysisId,
            padAssignments: session.padAssignmentStore,
            padFX: session.padFXStore,
            patternStore: session.patternStore,
            chopEditStore: session.chopEditStore,
            arrangementStore: session.arrangementStore,
            launchpad: session.launchpad
        )
        suppressAutoSave = false
        pendingBorrowRefs = snapshot.borrows
        pendingPlaybackMode = snapshot.launchpad.flatMap {
            LaunchpadController.PadPlaybackMode
                .migratedFromLegacy($0.sampleTriggerMode)
        }
    }

    /// Called after the attach-time `loadAutoKit` settles (kit mounted
    /// or failed) — the earliest point a borrow mount won't be
    /// clobbered by the kit activation. Consumes the pending refs.
    func autoKitDidMount(analysisId: String) {
        guard currentAnalysisId == analysisId,
              session.attachedAnalysisId == analysisId else {
            pendingBorrowRefs = []
            return
        }
        let refs = pendingBorrowRefs
        pendingBorrowRefs = []
        guard let first = refs.first else { return }
        // v1 supports the single active borrow desktop has today, so
        // all refs share one donor (iOS parity).
        let donorLabel = first.donorName ?? "the borrowed song"
        let savedMode = pendingPlaybackMode
        Task { @MainActor [weak self] in
            guard let self else { return }
            await self.session.loadBorrowLoops(
                donorId: first.donorSongId,
                stem: ProjectStateBridge.logicalStem(first.stemRole),
                donorName: first.donorName
            )
            guard self.session.attachedAnalysisId == analysisId else { return }
            if let ctx = self.session.activeBorrowContext,
               ctx.donorId == first.donorSongId {
                let missing = ProjectStateBridge.missingRefs(refs, in: ctx.pads)
                if !missing.isEmpty {
                    self.notice = "Needs \u{201C}\(donorLabel)\u{201D}: "
                        + "\(missing.count) borrowed pad"
                        + (missing.count == 1 ? "" : "s")
                        + " no longer in that song's kit — the rest restored."
                }
                // The borrow mount forces Latch; the workspace's saved
                // trigger mode wins.
                if let savedMode {
                    self.session.launchpad.playbackMode = savedMode
                }
            } else {
                self.notice = "Needs \u{201C}\(donorLabel)\u{201D} — its "
                    + "borrowed loops couldn't be restored. Re-borrow "
                    + "from the Remix sheet when it's available."
            }
        }
    }

    // MARK: - Explicit save / load / reset

    /// "Save workspace as project…": name a durable copy of the
    /// current state.
    func saveCurrentAsProject(named name: String) {
        guard let analysisId = currentAnalysisId,
              let snapshot = captureSnapshot() else { return }
        let trimmed = name.trimmingCharacters(in: .whitespacesAndNewlines)
        let project = Project(
            name: trimmed.isEmpty ? "Untitled project" : trimmed,
            baseSongId: analysisId,
            baseSongTitle: currentTitle,
            snapshot: snapshot
        )
        try? store.save(project)
        refreshList()
    }

    /// Projects-sheet tap: load the base song, then restore the
    /// snapshot over it (`songDidActivate` consumes `pendingRestore`).
    /// The already-attached song applies in place — a re-load would be
    /// skipped by attach's idempotence guard and strand the snapshot.
    func load(_ project: Project, model: AppModel) {
        notice = nil
        // Desktop v1 loads song-anchored projects only; blank-canvas
        // (nil baseSongId, v2) has no desktop canvas surface yet.
        guard let baseSongId = project.baseSongId else {
            notice = "\u{201C}\(project.name)\u{201D} is a blank-canvas "
                + "project — open it on iPhone for now."
            return
        }
        if session.attachedAnalysisId == baseSongId {
            applyRestore(project.snapshot, analysisId: baseSongId)
            session.reloadArrangementRuntime()
            // A workspace WITHOUT borrows over a live borrow grid must
            // fall back to the default kit, not keep the donor mounted.
            if project.snapshot.borrows.isEmpty,
               !session.launchpad.borrowSourceLabels.isEmpty {
                Task { @MainActor [weak self] in
                    await self?.session.loadAutoKit(
                        kind: "auto", announce: false)
                }
            } else {
                // Kit already mounted — re-derive borrows now.
                autoKitDidMount(analysisId: baseSongId)
            }
            return
        }
        pendingRestore = (baseSongId, project.snapshot)
        Task { @MainActor [weak self] in
            guard let self else { return }
            await model.loadSession(analysisId: baseSongId)
            if model.session?.bundle.analysisId != baseSongId {
                // Load failed — don't let the snapshot ambush the next
                // unrelated song load.
                self.pendingRestore = nil
                self.notice = "Couldn't load \u{201C}"
                    + (project.baseSongTitle ?? "the project's song")
                    + "\u{201D} for this project."
            }
        }
    }

    /// Clear the attached song's workspace back to its default state
    /// (fresh auto-kit, no assignments/FX/gates/edits/arrangement/
    /// borrows) and drop its working project. The sequencer pattern
    /// library is untouched.
    func resetToSong() {
        guard let analysisId = currentAnalysisId,
              session.attachedAnalysisId == analysisId else { return }
        workingSaveTask?.cancel()
        pendingBorrowRefs = []
        suppressAutoSave = true
        ProjectStateBridge.reset(
            analysisId: analysisId,
            padAssignments: session.padAssignmentStore,
            padFX: session.padFXStore,
            chopEditStore: session.chopEditStore,
            arrangementStore: session.arrangementStore,
            launchpad: session.launchpad
        )
        suppressAutoSave = false
        preservedSnapshot = nil
        store.deleteWorking(analysisId: analysisId)
        session.reloadArrangementRuntime()
        // Rebuild the default kit; activating a non-borrow grid also
        // clears any mounted borrow context.
        Task { @MainActor [weak self] in
            await self?.session.loadAutoKit(kind: "auto", announce: false)
        }
    }

    // MARK: - Row operations (Projects sheet)

    func rename(_ project: Project, to name: String) {
        let trimmed = name.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }
        _ = try? store.rename(projectId: project.id, to: trimmed)
        refreshList()
    }

    func duplicate(_ project: Project) {
        _ = try? store.duplicate(projectId: project.id,
                                 name: "\(project.name) copy")
        refreshList()
    }

    func delete(_ project: Project) {
        try? store.delete(projectId: project.id)
        refreshList()
    }

    /// Row "Reset to song": empty the STORED snapshot (the project
    /// becomes "just the song"); when that project's song is attached,
    /// also reset the live surface.
    func resetProjectToSong(_ project: Project) {
        var emptied = project
        emptied.snapshot = ProjectSnapshot()
        emptied.updatedAt = Date()
        try? store.save(emptied)
        refreshList()
        if let baseSongId = project.baseSongId,
           session.attachedAnalysisId == baseSongId {
            resetToSong()
        }
    }

    private func refreshList() {
        savedProjects = store.list()
    }
}
