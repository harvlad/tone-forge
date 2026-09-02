// SignInGateView.swift
//
// Launch gate: the app now requires a signed-in account before use
// (Apple ID primary, emailed 6-digit code fallback). Shown full-screen
// whenever AccountStore has no profile; RootView mounts only after a
// session exists. The session restore in ToneForgeScene still runs
// first, so a valid cached token skips the gate entirely.
//
// UI tests: `-uitest-stub-account` keeps the stubbed button (same
// accessibility ids as Settings), so gated launches stay scriptable.

import AuthenticationServices
import SwiftUI
import ToneForgeEngine

/// Observes AccountStore directly (a nested ObservableObject would
/// not re-render the scene) and swaps gate <-> RootView on sign-in.
struct GatedRoot: View {
    @ObservedObject var account: AccountStore
    let baseURL: URL

    var body: some View {
        if account.profile == nil {
            SignInGateView(account: account, baseURL: baseURL)
        } else {
            RootView()
        }
    }
}

struct SignInGateView: View {
    @ObservedObject var account: AccountStore
    let baseURL: URL

    @State private var email = ""
    @State private var code = ""
    @State private var codeSent = false

    var body: some View {
        VStack(spacing: 0) {
            Spacer()

            JamnWordmark()
                .padding(.bottom, 12)

            Text("Your music. Playable.")
                .font(.title3.weight(.medium))
                .foregroundStyle(.secondary)
                .padding(.bottom, 44)

            VStack(spacing: 14) {
                appleButton
                emailFlow
                if let error = account.lastError {
                    Text(error)
                        .font(.footnote)
                        .foregroundStyle(.red)
                        .multilineTextAlignment(.center)
                }
                if account.isSigningIn {
                    ProgressView()
                        .padding(.top, 4)
                }
            }
            .padding(.horizontal, 32)

            Spacer()

            Text("Sign in to analyze songs and keep your library across devices.")
                .font(.footnote)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
                .padding(.horizontal, 40)
                .padding(.bottom, 28)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Color(red: 0.05, green: 0.05, blue: 0.10))
    }

    @ViewBuilder
    private var appleButton: some View {
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
            .accessibilityIdentifier("gate-signin-apple")
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
            .signInWithAppleButtonStyle(.white)
            .frame(height: 50)
            .accessibilityIdentifier("gate-signin-apple")
        }
    }

    @ViewBuilder
    private var emailFlow: some View {
        if !codeSent {
            HStack {
                TextField("Email", text: $email)
                    .textContentType(.emailAddress)
                    .keyboardType(.emailAddress)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .padding(10)
                    .background(.white.opacity(0.06), in: RoundedRectangle(cornerRadius: 8))
                    .accessibilityIdentifier("gate-email-field")
                Button("Send code") {
                    Task {
                        await account.requestEmailCode(email: email, baseURL: baseURL)
                        if account.lastError == nil { codeSent = true }
                    }
                }
                .disabled(!email.contains("@") || account.isSigningIn)
                .accessibilityIdentifier("gate-email-send")
            }
        } else {
            HStack {
                TextField("6-digit code", text: $code)
                    .textContentType(.oneTimeCode)
                    .keyboardType(.numberPad)
                    .padding(10)
                    .background(.white.opacity(0.06), in: RoundedRectangle(cornerRadius: 8))
                    .accessibilityIdentifier("gate-email-code")
                Button("Sign in") {
                    Task {
                        await account.signInWithEmailCode(
                            email: email, code: code, baseURL: baseURL)
                    }
                }
                .disabled(code.count < 6 || account.isSigningIn)
                .accessibilityIdentifier("gate-email-verify")
            }
        }
    }
}

/// Small gradient-bars JamN mark + wordmark (matches the plugin's
/// header logo language).
private struct JamnWordmark: View {
    var body: some View {
        HStack(spacing: 12) {
            HStack(spacing: 3) {
                ForEach(Array([18, 30, 42, 30, 22, 34, 26].enumerated()),
                        id: \.offset) { item in
                    RoundedRectangle(cornerRadius: 2)
                        .fill(
                            LinearGradient(
                                colors: [Color(red: 0.55, green: 0.35, blue: 0.95),
                                         Color(red: 0.25, green: 0.45, blue: 0.95)],
                                startPoint: .top, endPoint: .bottom))
                        .frame(width: 5, height: CGFloat(item.element))
                }
            }
            Text("JamN")
                .font(.system(size: 34, weight: .bold, design: .rounded))
                .foregroundStyle(.white)
        }
    }
}
