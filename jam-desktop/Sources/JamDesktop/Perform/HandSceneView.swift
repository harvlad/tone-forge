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
    @Published var contacts: [SIMD2<Int>] = []               // (string, fret)
    @Published var hasPose = false

    func apply(symbol: String?) {
        guard let sym = symbol, let e = HandStates.all[sym] else { hasPose = false; return }
        set(e)
    }

    /// Lead mode: a single MIDI pitch → the nearest fretted (string, fret) in
    /// the window → its baked single-note pose.
    func applyNote(pitch: Int?) {
        guard let p = pitch, let e = HandStates.noteEntry(forPitch: p) else { hasPose = false; return }
        set(e)
    }

    private func set(_ e: HandStates.Entry) {
        targetPose = HandStates.pose(e.state)
        rootLoc = [e.state[0], e.state[1], e.state[2]]
        rootEuler = [e.state[3], e.state[4], e.state[5]]
        contacts = e.contacts.map { SIMD2<Int>($0[0], $0[1]) }
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
        return v
    }

    func updateNSView(_ nsView: SCNView, context: Context) {
        context.coordinator.setDotsVisible(showDots)
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
    private let dotRoot = SCNNode()
    private var lastContacts: [SIMD2<Int>] = []
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

    private func rebuildDots(_ contacts: [SIMD2<Int>]) {
        dotRoot.childNodes.forEach { $0.removeFromParentNode() }
        for (i, c) in contacts.enumerated() {
            let ct = G.contact(Float(c.x), Float(c.y))
            let sph = SCNSphere(radius: 0.006)
            sph.materials = [lineMat(Self.dotColors[i % Self.dotColors.count])]
            let n = SCNNode(geometry: sph); n.position = SCNVector3(ct.x, ct.y - 0.001, ct.z)
            dotRoot.addChildNode(n)
        }
    }
    func setDotsVisible(_ on: Bool) { dotRoot.isHidden = !on }

    // MARK: camera + lights (view the -Y playing face)
    private func setupCamera() {
        let cam = SCNCamera(); cam.zNear = 0.001; cam.zFar = 10
        // Orthographic → the neck reads LEVEL (no perspective tilt); orthographicScale
        // sets the zoom to fill the panel. Slight left yaw + above for a 3/4 read.
        cam.usesOrthographicProjection = true
        cam.orthographicScale = 0.135
        cam.projectionDirection = .vertical
        cameraNode.camera = cam
        cameraNode.position = SCNVector3(G.fingerX(3.4) - 0.05, -0.5, 0.26)
        cameraNode.look(at: SCNVector3(G.fingerX(3.3), 0, 0), up: SCNVector3(0, 0, 1), localFront: SCNVector3(0, 0, -1))
        scene.rootNode.addChildNode(cameraNode)
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

        // dots when the chord changes
        let contacts = poseModel.contacts
        if contacts != lastContacts { lastContacts = contacts; rebuildDots(contacts) }
        guard poseModel.hasPose else { return }

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
        if let hr = outerHR {
            let goalR = rootRotBaked * simd_float3x3(Self.q(poseModel.rootEuler))
            let goalQ = simd_quatf(goalR)
            let goalL = poseModel.rootLoc
            if !rootInit { curRootQuat = goalQ; curRootLoc = goalL; rootInit = true }
            curRootQuat = simd_slerp(curRootQuat, goalQ, alpha)
            curRootLoc += (goalL - curRootLoc) * alpha
            let R = simd_float3x3(curRootQuat)
            hr.simdTransform = simd_float4x4(SIMD4(R.columns.0, 0), SIMD4(R.columns.1, 0), SIMD4(R.columns.2, 0), SIMD4(curRootLoc, 1))
        }
        SCNTransaction.commit()
    }
}
