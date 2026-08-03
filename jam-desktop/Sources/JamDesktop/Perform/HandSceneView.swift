// HandSceneView.swift
//
// Runtime 3D neck + rigged hand (Phase 3/4). A real hand of FIXED anatomy is
// placed on a 3D fretboard by its ROOT transform and articulates its fingers
// (from the solver's joint angles) onto the chord's contacts — interpolating
// smoothly between chords, no pose-switching. Recipe validated offline:
//   • bone-local axes: flex = X, abduction = Z (USDZ is Blender Z-up verbatim)
//   • root: set ONLY the outer "Human_rig" node = T(loc) · M0 · E(euler),
//     M0 = its baked rest world-rotation, no Y-up conversion
//   • neck + dots live in raw guitar coords, so fingertips coincide with dots.

import SwiftUI
import SceneKit
import simd
#if canImport(AppKit)
import AppKit
#endif

// MARK: - guitar geometry (raw Blender coords, from blender_mpfb_rig.py)
private enum G {
    static let scaleLen: Float = 0.648
    static let neckThick: Float = 0.021
    static let spanNut: Float = 0.035
    static let span12: Float = 0.0435
    static let boardW: Float = 0.052
    static let window = 7
    static func wire(_ n: Float) -> Float { scaleLen * (1 - pow(2, -n/12)) }
    static func fingerX(_ fret: Float) -> Float { let lo = wire(fret-1), hi = wire(fret); return -(hi - (hi-lo)*0.30) }
    static func stringZ(_ s: Float, _ xAbs: Float) -> Float {
        let t = min(1, xAbs / (scaleLen/2)); let span = spanNut + (span12 - spanNut) * t
        return (2.5 - s) * span / 5
    }
    static func contact(_ s: Float, _ f: Float) -> simd_float3 { [fingerX(f), 0, stringZ(s, abs(fingerX(f)))] }
}

// MARK: - pose model
final class HandPoseModel: ObservableObject {
    @Published var targetPose: [String: simd_float3] = [:]   // bone -> local euler (radians)
    @Published var rootLoc: simd_float3 = [0, 0, 0]
    @Published var rootEuler: simd_float3 = [0, 0, 0]
    @Published var contacts: [SIMD3<Int>] = []               // (string, fret, finger 1..4)
    @Published var hasPose = false

    func apply(symbol: String?) {
        guard let sym = symbol, let e = HandStates.all[sym] else { hasPose = false; return }
        set(e)
    }

    /// Lead mode: a single MIDI pitch → the nearest fretted (string, fret) in
    /// the window → its baked single-note pose. Falls back to a default note so
    /// the hand is always posed (never vanishes when no note is active yet).
    func applyNote(pitch: Int?) {
        if let p = pitch, let e = HandStates.noteEntry(forPitch: p) { set(e); return }
        if let e = HandStates.notes["s2f2"] ?? HandStates.notes.values.first { set(e) }
        else { hasPose = false }
    }

    private func set(_ e: HandStates.Entry) {
        targetPose = HandStates.pose(e.state)
        rootLoc = [e.state[0], e.state[1], e.state[2]]
        rootEuler = [e.state[3], e.state[4], e.state[5]]
        contacts = e.contacts.map { SIMD3<Int>($0[0], $0[1], $0.count > 2 ? $0[2] : 1) }
        hasPose = true
    }
}

// MARK: - solved states (bundled)
enum HandStates {
    struct Entry { let state: [Float]; let contacts: [[Int]] }
    static let all: [String: Entry] = {
        guard let url = Bundle.module.url(forResource: "hand_states", withExtension: "json", subdirectory: "Hand"),
              let d = try? Data(contentsOf: url),
              let j = (try? JSONSerialization.jsonObject(with: d)) as? [String: [String: Any]]
        else { return [:] }
        var out: [String: Entry] = [:]
        for (k, v) in j {
            guard let s = v["state"] as? [Double] else { continue }
            let c = (v["contacts"] as? [[Int]]) ?? []
            out[k] = Entry(state: s.map { Float($0) }, contacts: c)
        }
        return out
    }()

    /// Single-note lead poses, keyed "s{string}f{fret}" (fret 1..7).
    static let notes: [String: Entry] = {
        guard let url = Bundle.module.url(forResource: "note_states", withExtension: "json", subdirectory: "Hand"),
              let d = try? Data(contentsOf: url),
              let j = (try? JSONSerialization.jsonObject(with: d)) as? [String: [String: Any]]
        else { return [:] }
        var out: [String: Entry] = [:]
        for (k, v) in j {
            guard let s = v["state"] as? [Double] else { continue }
            let c = (v["contacts"] as? [[Int]]) ?? []
            out[k] = Entry(state: s.map { Float($0) }, contacts: c)
        }
        return out
    }()

    /// Standard tuning open-string MIDI (string 0=low E … 5=high e).
    static let openMidi = [40, 45, 50, 55, 59, 64]

    /// Map a MIDI pitch to the best fretted note in the window (fret 1..7),
    /// preferring the highest string that keeps a low fret (compact hand).
    static func noteEntry(forPitch pitch: Int) -> Entry? {
        var best: (Int, Int)? = nil   // (string, fret)
        for s in (0..<6).reversed() {
            let fret = pitch - openMidi[s]
            if fret >= 1 && fret <= 7 {
                if best == nil || fret < best!.1 { best = (s, fret) }
            }
        }
        guard let (s, f) = best else { return nil }
        return notes["s\(s)f\(f)"]
    }

    /// bone -> local euler. flex = X, abduction = Z.
    static func pose(_ v: [Float]) -> [String: simd_float3] {
        guard v.count >= 30 else { return [:] }
        var p: [String: simd_float3] = [:]
        let FB = ["finger2", "finger3", "finger4", "finger5"]
        for i in 0..<4 {
            p["metacarpal\(i+1)_L"] = [v[26 + i], 0, 0]
            p["\(FB[i])_1_L"] = [v[6 + i*4], 0, v[6 + i*4 + 1]]   // X flex, Z abd
            p["\(FB[i])_2_L"] = [v[6 + i*4 + 2], 0, 0]
            p["\(FB[i])_3_L"] = [v[6 + i*4 + 3], 0, 0]
        }
        p["finger1_1_L"] = [v[22], 0, v[23]]
        p["finger1_2_L"] = [v[24], 0, 0]
        p["finger1_3_L"] = [v[24] * 0.6, 0, 0]
        return p
    }
}

// MARK: - SwiftUI bridge
struct HandSceneView: NSViewRepresentable {
    let sceneURL: URL
    @ObservedObject var poseModel: HandPoseModel
    var showDots: Bool = true

    func makeCoordinator() -> HandCoordinator { HandCoordinator(sceneURL: sceneURL, poseModel: poseModel) }

    func makeNSView(context: Context) -> SCNView {
        let v = SCNView()
        v.scene = context.coordinator.scene
        v.pointOfView = context.coordinator.cameraNode
        v.allowsCameraControl = false
        v.autoenablesDefaultLighting = false
        v.backgroundColor = .clear
        v.antialiasingMode = .multisampling4X
        v.rendersContinuously = true
        v.isPlaying = true
        v.delegate = context.coordinator
        v.preferredFramesPerSecond = 60
        // Drag to orbit the hand (user picks the viewing angle); double-click resets.
        let pan = NSPanGestureRecognizer(target: context.coordinator, action: #selector(HandCoordinator.handleOrbit(_:)))
        v.addGestureRecognizer(pan)
        let dbl = NSClickGestureRecognizer(target: context.coordinator, action: #selector(HandCoordinator.resetView))
        dbl.numberOfClicksRequired = 2
        v.addGestureRecognizer(dbl)
        return v
    }

    func updateNSView(_ nsView: SCNView, context: Context) {
        context.coordinator.setDotsVisible(showDots)
        context.coordinator.fitCamera(to: nsView.bounds.size)
    }
}

// MARK: - coordinator
final class HandCoordinator: NSObject, SCNSceneRendererDelegate {
    let scene: SCNScene
    var cameraNode = SCNNode()
    private let poseModel: HandPoseModel

    private var bones: [String: SCNNode] = [:]
    private var restOrientation: [String: simd_quatf] = [:]
    private var currentOrientation: [String: simd_quatf] = [:]
    private var outerHR: SCNNode?
    private var rootRotBaked = simd_float3x3(1)   // M0
    private var curRootQuat = simd_quatf(angle: 0, axis: [0, 1, 0])
    private var curRootLoc = simd_float3(0, 0, 0)
    private var rootInit = false
    private var lastContacts: [SIMD3<Int>] = []
    private let dotRoot = SCNNode()
    private var lastTime: TimeInterval = 0
    private static let halfLife: Float = 0.07

    init(sceneURL: URL, poseModel: HandPoseModel) {
        self.poseModel = poseModel
        self.scene = (try? SCNScene(url: sceneURL, options: [.checkConsistency: true])) ?? SCNScene()
        super.init()
        loadRig()
        buildNeck()
        scene.rootNode.addChildNode(dotRoot)
        setupCamera()
        setupLights()
    }

    // MARK: rig
    private func loadRig() {
        scene.rootNode.enumerateHierarchy { node, _ in
            if let sk = node.skinner {
                self.applySkin(node)
                for b in sk.bones {
                    guard let nm = b.name else { continue }
                    self.bones[nm] = b
                    self.restOrientation[nm] = b.simdOrientation
                    self.currentOrientation[nm] = b.simdOrientation
                }
            }
            if node.name == "Human_rig", node.parent?.name == "hand" { self.outerHR = node }
        }
        if let hr = outerHR {
            rootRotBaked = Self.top3(simd_float4x4(hr.worldTransform))   // M0 (baked rest rotation)
        }
        // Shorten the fingers from the middle knuckle to the tip (~0.8 along the
        // bone-length axis) — the MPFB fingers are too long / read spidery.
        // (Finger-length trim removed — it pulled the tips off the strings. The
        // solved pose lands the full-length tips on the dots.)
    }

    private func applySkin(_ node: SCNNode) {
        let m = SCNMaterial()
        m.lightingModel = .physicallyBased
        m.diffuse.contents = NSColor(calibratedRed: 0.84, green: 0.63, blue: 0.53, alpha: 1)
        m.roughness.contents = 0.66
        m.metalness.contents = 0.0
        m.emission.contents = NSColor(calibratedRed: 0.20, green: 0.06, blue: 0.05, alpha: 1)
        node.geometry?.materials = [m]
    }

    // MARK: neck
    private func lineMat(_ c: NSColor) -> SCNMaterial {
        let m = SCNMaterial(); m.diffuse.contents = c; m.emission.contents = c; m.lightingModel = .constant; return m
    }
    private func buildNeck() {
        let span = G.wire(Float(G.window))
        let boardMat = SCNMaterial()
        boardMat.diffuse.contents = NSColor(calibratedRed: 0.15, green: 0.10, blue: 0.07, alpha: 1)
        boardMat.lightingModel = .blinn
        let board = SCNBox(width: CGFloat(span), height: CGFloat(G.neckThick), length: CGFloat(G.boardW), chamferRadius: 0)
        board.materials = [boardMat]
        let bn = SCNNode(geometry: board)
        bn.position = SCNVector3(-span/2, 0.004 + G.neckThick/2, 0)   // board backs the strings (+Y)
        scene.rootNode.addChildNode(bn)
        for s in 0..<6 {
            let cyl = SCNCylinder(radius: 0.0007, height: CGFloat(span)); cyl.materials = [lineMat(NSColor(white: 0.8, alpha: 1))]
            let n = SCNNode(geometry: cyl); n.eulerAngles = SCNVector3(0, 0, Float.pi/2)
            n.position = SCNVector3(-span/2, 0.003, G.stringZ(Float(s), span/2))
            scene.rootNode.addChildNode(n)
        }
        for f in 0...G.window {
            let cyl = SCNCylinder(radius: 0.001, height: CGFloat(G.boardW)); cyl.materials = [lineMat(NSColor(white: 0.5, alpha: 1))]
            let n = SCNNode(geometry: cyl); n.eulerAngles = SCNVector3(Float.pi/2, 0, 0)
            n.position = SCNVector3(-G.wire(Float(f)), 0.0035, 0)
            scene.rootNode.addChildNode(n)
        }
    }

    private static let dotColors: [NSColor] = [
        NSColor(calibratedRed: 0.36, green: 0.62, blue: 1.0, alpha: 1),
        NSColor(calibratedRed: 0.31, green: 0.82, blue: 0.63, alpha: 1),
        NSColor(calibratedRed: 0.95, green: 0.71, blue: 0.36, alpha: 1),
        NSColor(calibratedRed: 0.85, green: 0.55, blue: 1.0, alpha: 1)]

    private let fingerTipBones = ["finger2_3_L", "finger3_3_L", "finger4_3_L", "finger5_3_L"]

    /// Dots mark the true fret contacts (the reference to press). Each pressing
    /// finger is realigned (see renderer) so its tip lands on its dot.
    private func rebuildDots(_ contacts: [SIMD3<Int>]) {
        dotRoot.childNodes.forEach { $0.removeFromParentNode() }
        for c in contacts {
            let ct = G.contact(Float(c.x), Float(c.y))
            let sph = SCNSphere(radius: 0.007)
            let fi = max(1, min(4, c.z))     // colour by finger identity
            sph.materials = [lineMat(Self.dotColors[fi - 1])]
            let n = SCNNode(geometry: sph); n.position = SCNVector3(ct.x, ct.y - 0.001, ct.z)
            dotRoot.addChildNode(n)
        }
    }
    func setDotsVisible(_ on: Bool) { dotRoot.isHidden = !on }

    /// World position of a finger's fingertip (distal bone tip).
    private func fingertipWorld(_ boneName: String) -> simd_float3? {
        guard let b = bones[boneName] else { return nil }
        let wt = simd_float4x4(b.worldTransform)
        let head = simd_float3(wt.columns.3.x, wt.columns.3.y, wt.columns.3.z)
        let yAxis = normalize(simd_float3(wt.columns.1.x, wt.columns.1.y, wt.columns.1.z))
        return head + yAxis * 0.021
    }

    /// Fit the orthographic camera to the content box at the live view aspect,
    /// so neck + hand fill the panel whatever its shape.
    // Centering + framing constants (tuned in the offline harness against the
    // real panel aspect). cx offset from the neck's geometric centre accounts for
    // the hand extending toward the nut side.
    private static let camCX: Float = -0.10
    private static let contentW: Float = 0.44   // neck length
    private static let contentH: Float = 0.30   // neck + wrapping hand
    private static let orbitRadius: Float = 0.6
    // Orbit angles (user-draggable). Defaults = front + slightly above (the wrap view).
    private var azimuth: Float = 0.0            // spin around vertical
    private var elevation: Float = 0.56         // ~32° above → look down onto the board

    private var orbitTarget: simd_float3 { SIMD3(Self.camCX, 0, 0) }

    private func aimCamera() {
        let t = orbitTarget
        // Camera on a sphere around the target; az spins horizontally, el tilts.
        let ce = cos(elevation), se = sin(elevation)
        let dir = simd_float3(sin(azimuth) * ce, -cos(azimuth) * ce, se)  // target→camera
        cameraNode.simdPosition = t + dir * Self.orbitRadius
        // Keep world up +Z until steeply tilted, then flip to avoid gimbal snap.
        let up = SCNVector3(0, 0, elevation > 1.45 ? -1 : 1)
        cameraNode.look(at: SCNVector3(t.x, t.y, t.z), up: up, localFront: SCNVector3(0, 0, -1))
    }

    @objc func handleOrbit(_ g: NSPanGestureRecognizer) {
        let d = g.translation(in: g.view)
        g.setTranslation(.zero, in: g.view)
        azimuth += Float(d.x) * 0.01
        // Clamp tilt to front-above … top-down; never swing under the neck.
        elevation = max(0.05, min(1.25, elevation - Float(d.y) * 0.01))
        aimCamera()
    }

    @objc func resetView() {
        azimuth = 0.0; elevation = 0.56; aimCamera()
    }

    func fitCamera(to size: CGSize) {
        guard size.width > 1, size.height > 1, let cam = cameraNode.camera else { return }
        let aspect = Float(size.width / size.height)
        // FILL the panel (crop overflow) instead of fitting (letterbox voids):
        // take the SMALLER scale so content covers the frame. On a wide-short
        // panel this fills the width (neck big); the lower palm/arm crops off.
        let scale = min(Self.contentH * 0.5, Self.contentW / (2 * aspect)) * 0.98
        cam.orthographicScale = Double(scale)
        aimCamera()
    }

    // MARK: camera + lights (view the -Y playing face)
    private func setupCamera() {
        let cam = SCNCamera(); cam.zNear = 0.001; cam.zFar = 10
        // Orthographic straight-on → the neck reads LEVEL (no perspective tilt).
        // orthographicScale is set per-frame by fitCamera() to the live view size,
        // so the neck + hanging hand fill the panel at any aspect.
        cam.usesOrthographicProjection = true
        cam.projectionDirection = .vertical     // scale controls HEIGHT
        cameraNode.camera = cam
        scene.rootNode.addChildNode(cameraNode)
        // First-frame framing (matches fitCamera at a typical wide aspect) so
        // there's no forearm-zoom flash before layout runs.
        cam.orthographicScale = Double(Self.contentH * 0.5 * 1.04)
        aimCamera()
    }
    private func setupLights() {
        let k = SCNNode(); k.light = SCNLight(); k.light!.type = .directional; k.light!.intensity = 850
        k.light!.color = NSColor(calibratedRed: 1.0, green: 0.96, blue: 0.9, alpha: 1)
        k.eulerAngles = SCNVector3(-0.9, 0.3, 0); scene.rootNode.addChildNode(k)
        let f = SCNNode(); f.light = SCNLight(); f.light!.type = .directional; f.light!.intensity = 380
        f.light!.color = NSColor(calibratedRed: 0.8, green: 0.85, blue: 1.0, alpha: 1)
        f.eulerAngles = SCNVector3(0.4, -0.8, 0); scene.rootNode.addChildNode(f)
        let a = SCNNode(); a.light = SCNLight(); a.light!.type = .ambient; a.light!.intensity = 240
        scene.rootNode.addChildNode(a)
    }

    // MARK: math
    static func q(_ e: simd_float3) -> simd_quatf {
        simd_quatf(angle: e.x, axis: [1,0,0]) * simd_quatf(angle: e.y, axis: [0,1,0]) * simd_quatf(angle: e.z, axis: [0,0,1])
    }
    static func top3(_ m: simd_float4x4) -> simd_float3x3 {
        simd_float3x3(SIMD3(m.columns.0.x, m.columns.0.y, m.columns.0.z),
                      SIMD3(m.columns.1.x, m.columns.1.y, m.columns.1.z),
                      SIMD3(m.columns.2.x, m.columns.2.y, m.columns.2.z))
    }

    // MARK: per-frame
    func renderer(_ renderer: SCNSceneRenderer, updateAtTime time: TimeInterval) {
        let dt = Float(lastTime == 0 ? 1.0/60 : max(0, time - lastTime)); lastTime = time
        let alpha = 1 - pow(2, -dt / Self.halfLife)

        // rebuild fret dots when the chord/note changes
        let contacts = poseModel.contacts
        if contacts != lastContacts { lastContacts = contacts; rebuildDots(contacts) }
        guard poseModel.hasPose, let hr = outerHR else { return }

        // finger joints
        let target = poseModel.targetPose
        SCNTransaction.begin(); SCNTransaction.animationDuration = 0; SCNTransaction.disableActions = true
        for (name, bone) in bones {
            guard let rest = restOrientation[name] else { continue }
            let goal = rest * Self.q(target[name] ?? .zero)
            let cur = currentOrientation[name] ?? goal
            let next = simd_slerp(cur, goal, alpha)
            currentOrientation[name] = next
            bone.simdOrientation = next
        }
        // root: T(loc) · M0 · E(euler), interpolated
        let goalR = rootRotBaked * simd_float3x3(Self.q(poseModel.rootEuler))
        let goalQ = simd_quatf(goalR)
        let goalL = poseModel.rootLoc
        if !rootInit { curRootQuat = goalQ; curRootLoc = goalL; rootInit = true }
        curRootQuat = simd_slerp(curRootQuat, goalQ, alpha)
        curRootLoc += (goalL - curRootLoc) * alpha
        func rootMatrix(_ loc: simd_float3) -> simd_float4x4 {
            let R = simd_float3x3(curRootQuat)
            return simd_float4x4(SIMD4(R.columns.0, 0), SIMD4(R.columns.1, 0), SIMD4(R.columns.2, 0), SIMD4(loc, 1))
        }
        // First place the whole hand by its solved root, then cluster-shift so the
        // fingers are roughly on the contacts, then per-finger seat (below).
        var rootLoc = curRootLoc
        if !contacts.isEmpty {
            hr.simdTransform = rootMatrix(rootLoc)
            var tipC = simd_float3(0,0,0), n: Float = 0
            for bn in fingerTipBones {
                if let t = fingertipWorld(bn), abs(t.y) < 0.03, t.x < 0.05, t.x > -0.46 { tipC += t; n += 1 }
            }
            if n > 0 {
                tipC /= n
                var dotC = simd_float3(0,0,0)
                for c in contacts { dotC += G.contact(Float(c.x), Float(c.y)) }
                dotC /= Float(contacts.count)
                let delta = dotC - tipC
                if simd_length(delta) < 0.12 { rootLoc += delta }
            }
        }
        hr.simdTransform = rootMatrix(rootLoc)
        SCNTransaction.commit()

        // PER-FINGER SEATING: after the cluster shift, realign each pressing
        // finger's knuckle so its OWN tip lands on its OWN contact dot — every
        // finger touches, not just the cluster average.
        if !contacts.isEmpty {
            SCNTransaction.begin(); SCNTransaction.animationDuration = 0; SCNTransaction.disableActions = true
            let names = ["finger2", "finger3", "finger4", "finger5"]
            for c in contacts {
                let fi = max(1, min(4, c.z))
                let d = names[fi - 1]
                guard let mcp = bones["\(d)_1_L"], let tipW = fingertipWorld("\(d)_3_L") else { continue }
                let mp = mcp.simdWorldPosition
                let target = G.contact(Float(c.x), Float(c.y))
                let a = simd_normalize(tipW - mp)
                let b = simd_normalize(target - mp)
                let dot = max(-1, min(1, simd_dot(a, b)))
                let ang = acos(dot)
                if ang < 0.001 { continue }
                let axis = simd_cross(a, b)
                if simd_length(axis) < 1e-5 { continue }
                let wr = simd_quatf(angle: min(ang, 0.6), axis: simd_normalize(axis))   // clamp to stay natural
                let newWO = wr * mcp.simdWorldOrientation
                mcp.simdWorldTransform = simd_float4x4(orient: newWO, pos: mp)
            }
            SCNTransaction.commit()
        }
    }
}

private extension simd_float4x4 {
    init(orient q: simd_quatf, pos p: simd_float3) {
        self = simd_float4x4(q)
        self.columns.3 = SIMD4(p, 1)
    }
}
