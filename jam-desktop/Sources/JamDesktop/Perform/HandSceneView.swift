// HandSceneView.swift
//
// Runtime 3D rigged hand (Phase 2). Loads the exported MPFB hand USDZ in
// SceneKit and poses its skeleton bones live — the foundation for a real hand
// that moves fingers independently to the dot targets (single notes + chords),
// instead of switching baked poses.
//
// Recipe basis: drive skinner.bones with quaternions (rest * delta), per-frame
// slerp in the render-loop delegate; USDZ may import as a static mesh — we log
// skinner status and fall back to a plain display if so.

import SwiftUI
import SceneKit
import simd

/// Live pose source: bone name -> local euler delta (radians). Written by the
/// posing layer (Phase 3); empty = rest pose.
final class HandPoseModel: ObservableObject {
    @Published var targetPose: [String: simd_float3] = [:]
}

struct HandSceneView: NSViewRepresentable {
    let sceneURL: URL
    @ObservedObject var poseModel: HandPoseModel
    /// Debug: curl the fingers with a static test pose to prove live deformation.
    var debugCurl: Bool = false

    func makeCoordinator() -> HandCoordinator {
        HandCoordinator(sceneURL: sceneURL, poseModel: poseModel, debugCurl: debugCurl)
    }

    func makeNSView(context: Context) -> SCNView {
        let v = SCNView()
        v.scene = context.coordinator.scene
        if let cam = context.coordinator.cameraNode { v.pointOfView = cam }
        v.allowsCameraControl = true          // TEMP debug: orbit to find framing
        v.autoenablesDefaultLighting = false
        v.backgroundColor = .clear
        v.antialiasingMode = .multisampling4X
        v.rendersContinuously = true
        v.isPlaying = true
        v.delegate = context.coordinator
        v.preferredFramesPerSecond = 60
        return v
    }

    func updateNSView(_ nsView: SCNView, context: Context) {}
}

final class HandCoordinator: NSObject, SCNSceneRendererDelegate {
    let scene: SCNScene
    var cameraNode: SCNNode?
    private let poseModel: HandPoseModel
    private let debugCurl: Bool

    private var bones: [String: SCNNode] = [:]
    private var restOrientation: [String: simd_quatf] = [:]
    private var currentOrientation: [String: simd_quatf] = [:]
    private var lastTime: TimeInterval = 0
    private static let smoothingHalfLife: Float = 0.06

    init(sceneURL: URL, poseModel: HandPoseModel, debugCurl: Bool) {
        self.poseModel = poseModel
        self.debugCurl = debugCurl
        self.scene = (try? SCNScene(url: sceneURL, options: [.checkConsistency: true])) ?? SCNScene()
        super.init()
        setupCamera()
        setupLights()
        findBones()
    }

    private func setupCamera() {
        let cam = SCNCamera()
        cam.fieldOfView = 35; cam.zNear = 0.001; cam.zFar = 100
        let node = SCNNode(); node.camera = cam
        node.position = SCNVector3(0, 0.05, 0.35)
        node.look(at: SCNVector3(0, 0.0, 0))
        scene.rootNode.addChildNode(node)
        cameraNode = node
    }

    private func setupLights() {
        let key = SCNNode(); key.light = SCNLight(); key.light!.type = .directional
        key.light!.intensity = 900; key.eulerAngles = SCNVector3(-Float.pi/4, Float.pi/6, 0)
        scene.rootNode.addChildNode(key)
        let amb = SCNNode(); amb.light = SCNLight(); amb.light!.type = .ambient
        amb.light!.intensity = 350
        scene.rootNode.addChildNode(amb)
    }

    private func findBones() {
        var skinnerFound = false
        scene.rootNode.enumerateHierarchy { node, _ in
            if let sk = node.skinner, !skinnerFound {
                skinnerFound = true
                for bone in sk.bones {
                    guard let name = bone.name else { continue }
                    bones[name] = bone
                    restOrientation[name] = bone.simdOrientation
                    currentOrientation[name] = bone.simdOrientation
                }
            }
        }
        print("[HandScene] skinner=\(skinnerFound) bones=\(bones.count)")
        for name in bones.keys.sorted() { print("[HandScene] bone: \(name)") }
        if !skinnerFound {
            print("[HandScene] WARNING: no skinner — USDZ imported static; convert to .scn")
        }
        if debugCurl { applyDebugCurl() }
    }

    /// Static curl on the index/middle/ring/pinky to prove live skin deformation.
    private func applyDebugCurl() {
        for (name, bone) in bones {
            guard let rest = restOrientation[name] else { continue }
            // bend the middle phalanx bones (…-2.L) about local X
            if name.hasSuffix("-2.L") || name.hasSuffix("_2_L") {
                bone.simdOrientation = rest * simd_quatf(angle: 0.7, axis: [1, 0, 0])
            }
        }
    }

    func quat(_ e: simd_float3) -> simd_quatf {
        simd_quatf(angle: e.x, axis: [1,0,0]) * simd_quatf(angle: e.y, axis: [0,1,0]) * simd_quatf(angle: e.z, axis: [0,0,1])
    }

    func renderer(_ renderer: SCNSceneRenderer, updateAtTime time: TimeInterval) {
        if debugCurl { return }               // static test pose; skip streaming
        let dt = Float(lastTime == 0 ? 1.0/60 : max(0, time - lastTime)); lastTime = time
        let alpha = 1 - pow(2, -dt / Self.smoothingHalfLife)
        let target = poseModel.targetPose
        SCNTransaction.begin(); SCNTransaction.animationDuration = 0; SCNTransaction.disableActions = true
        for (name, bone) in bones {
            guard let rest = restOrientation[name] else { continue }
            let goal = rest * quat(target[name] ?? .zero)
            let cur = currentOrientation[name] ?? goal
            let next = simd_slerp(cur, goal, alpha)
            currentOrientation[name] = next
            bone.simdOrientation = next
        }
        SCNTransaction.commit()
    }
}
