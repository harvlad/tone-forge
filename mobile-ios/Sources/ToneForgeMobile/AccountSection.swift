// AccountSection.swift
//
// First Form section in SettingsView. Signed out: the native Sign in
// with Apple button (plus a plain stand-in under -uitest-stub-account,
// same accessibility id). Signed in: identity row, claim status, and
// a destructive Sign out.
//
// Sign-in is optional — anonymous use is a first-class path, hence
// the footer copy.

import AuthenticationServices
import SwiftUI
import ToneForgeEngine

struct AccountSection: View {
    @ObservedObject var account: AccountStore
    let baseURL: URL

    var body: some View {
        Section {
            if let user = account.profile {
                signedIn(user: user)
            } else {
                signedOut
            }
        } header: {
            Text("Account")
        } footer: {
            if account.profile == nil {
                Text("Optional. Sign in to keep your analyses across devices.")
            }
        }
    }

    // MARK: - Signed out

    @ViewBuilder
    private var signedOut: some View {
        if UITestSupport.stubAccountEnabled {
            Button("Sign in with Apple") {
                Task {
                    await account.signIn(
                        identityToken: "uitest-identity-token",
                        appleUserId: nil,
                        fullName: nil,
                        baseURL: baseURL
                    )
                }
            }
            .accessibilityIdentifier("settings-signin-apple")
        } else {
            SignInWithAppleButton(.signIn) { request in
                request.requestedScopes = [.email, .fullName]
                request.nonce = account.prepareNonce()
            } onCompletion: { result in
                guard
                    case .success(let authorization) = result,
                    let credential = authorization.credential
                        as? ASAuthorizationAppleIDCredential
                else { return }
                Task {
                    await account.signIn(credential: credential, baseURL: baseURL)
                }
            }
            // App is forced dark; .white keeps Apple's contrast rules.
            .signInWithAppleButtonStyle(.white)
            .frame(height: 44)
            .accessibilityIdentifier("settings-signin-apple")
        }
        if let error = account.lastError {
            Text(error)
                .font(.footnote)
                .foregroundStyle(.red)
        }
    }

    // MARK: - Signed in

    @ViewBuilder
    private func signedIn(user: AuthUser) -> some View {
        LabeledContent("Signed in") {
            Text(user.displayName ?? user.email ?? "Apple ID")
        }
        .accessibilityIdentifier("settings-account-status")

        switch account.claimStatus {
        case .idle:
            EmptyView()
        case .claiming:
            LabeledContent("Syncing…") { ProgressView() }
        case .synced(let count):
            LabeledContent("Synced") {
                Text(count == 1 ? "1 analysis" : "\(count) analyses")
            }
            .accessibilityIdentifier("settings-claim-status")
        case .failed:
            Button("Retry sync") {
                Task { await account.claim(baseURL: baseURL) }
            }
            .accessibilityIdentifier("settings-claim-button")
        }

        Button("Sign out", role: .destructive) {
            Task { await account.signOut(baseURL: baseURL) }
        }
        .accessibilityIdentifier("settings-signout")
    }
}
