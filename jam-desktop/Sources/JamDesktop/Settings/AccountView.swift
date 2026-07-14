// AccountView.swift
//
// Sign in with Apple against the backend (BackendAuthClient) —
// same flow as the iOS app: identity token -> /api/auth/apple ->
// bearer token in AuthContext; then claim this device's anonymous
// history. Signed-out state shows the Apple button; signed-in shows
// the user and a sign-out button.

import SwiftUI
import AuthenticationServices
import JamDesktopCore
import ToneForgeEngine

struct AccountView: View {
    @EnvironmentObject private var model: AppModel

    @State private var user: AuthUser?
    @State private var busy = false
    @State private var errorMessage: String?

    private let client = BackendAuthClient()

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            if let user {
                LabeledContent(
                    "Signed in",
                    value: user.displayName ?? user.email ?? user.id
                )
                Button("Sign out") { signOut() }
                    .disabled(busy)
            } else {
                SignInWithAppleButton(.signIn) { request in
                    request.requestedScopes = [.fullName, .email]
                } onCompletion: { result in
                    handle(result)
                }
                .frame(width: 200, height: 30)
                .disabled(busy)
            }

            if let errorMessage {
                Text(errorMessage)
                    .font(.caption)
                    .foregroundStyle(.red)
            }
        }
        .task { await restoreSession() }
    }

    // MARK: - Flows

    private func restoreSession() async {
        guard let token = AuthContext.shared.sessionToken else { return }
        user = try? await client.session(
            baseURL: model.backendBaseURL, token: token)
    }

    private func handle(_ result: Result<ASAuthorization, Error>) {
        switch result {
        case .failure(let error):
            // User cancellation is not an error worth surfacing.
            if (error as? ASAuthorizationError)?.code != .canceled {
                errorMessage = error.localizedDescription
            }
        case .success(let authorization):
            guard
                let credential = authorization.credential
                    as? ASAuthorizationAppleIDCredential,
                let tokenData = credential.identityToken,
                let identityToken = String(data: tokenData, encoding: .utf8)
            else {
                errorMessage = "Apple returned no identity token."
                return
            }
            let fullName = [
                credential.fullName?.givenName,
                credential.fullName?.familyName,
            ].compactMap { $0 }.joined(separator: " ")

            busy = true
            errorMessage = nil
            Task {
                defer { busy = false }
                do {
                    let deviceId = DeviceIdentity.id()
                    let session = try await client.signInWithApple(
                        baseURL: model.backendBaseURL,
                        identityToken: identityToken,
                        nonce: nil,
                        deviceId: deviceId,
                        fullName: fullName.isEmpty ? nil : fullName
                    )
                    AuthContext.shared.sessionToken = session.token
                    user = session.user
                    // Attach this device's anonymous history.
                    _ = try? await client.claim(
                        baseURL: model.backendBaseURL,
                        token: session.token,
                        deviceId: deviceId
                    )
                } catch {
                    errorMessage = error.localizedDescription
                }
            }
        }
    }

    private func signOut() {
        busy = true
        errorMessage = nil
        Task {
            defer { busy = false }
            if let token = AuthContext.shared.sessionToken {
                try? await client.logout(
                    baseURL: model.backendBaseURL, token: token)
            }
            AuthContext.shared.sessionToken = nil
            user = nil
        }
    }
}
