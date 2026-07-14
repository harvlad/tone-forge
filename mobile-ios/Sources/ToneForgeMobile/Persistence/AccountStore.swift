// AccountStore.swift
//
// Optional sign-in state (Sign in with Apple). Owns:
//   - the session token (Keychain via SecretStore)
//   - the cached profile (UserDefaults, display only)
//   - the SIWA nonce dance (raw nonce retained, SHA-256 to Apple)
//   - pushing token + device id into AuthContext so every Engine
//     client stamps its requests
//   - the one-shot post-sign-in claim ("Synced N analyses")
//
// Anonymous use is untouched: no token means AuthContext only carries
// the device id and the backend keeps treating the app as anonymous.

import AuthenticationServices
import Combine
import CryptoKit
import Foundation
import ToneForgeEngine

@MainActor
public final class AccountStore: ObservableObject {

    public enum ClaimStatus: Equatable {
        case idle
        case claiming
        case synced(Int)
        case failed
    }

    enum Keys {
        static let profile = "toneforge.account.profile"
        static let appleUserId = "toneforge.account.appleUserId"
        static let token = "toneforge.account.sessionToken"
    }

    /// Signed-in user, or nil when anonymous.
    @Published public private(set) var profile: AuthUser?
    @Published public private(set) var claimStatus: ClaimStatus = .idle
    @Published public private(set) var lastError: String?
    @Published public private(set) var isSigningIn = false

    private let client: any AuthProviding
    private let secrets: any SecretStore
    private let defaults: UserDefaults
    /// Raw nonce for the in-flight SIWA request; Apple gets its
    /// SHA-256, the backend verifies the raw value against the token.
    private var pendingNonce: String?
    private var revocationObserver: NSObjectProtocol?

    public init(
        client: any AuthProviding = BackendAuthClient(),
        secrets: any SecretStore = KeychainStore(),
        defaults: UserDefaults = .standard
    ) {
        self.client = client
        self.secrets = secrets
        self.defaults = defaults

        // Restore persisted identity synchronously so the first frame
        // and the first network request both see it.
        if let data = defaults.data(forKey: Keys.profile),
           let user = try? JSONDecoder().decode(AuthUser.self, from: data) {
            profile = user
        }
        AuthContext.shared.sessionToken = secrets.read(key: Keys.token)

        // Apple can revoke the credential out-of-band (user removes the
        // app in Settings → Apple ID). Drop the local session when
        // that lands.
        revocationObserver = NotificationCenter.default.addObserver(
            forName: ASAuthorizationAppleIDProvider.credentialRevokedNotification,
            object: nil,
            queue: .main
        ) { [weak self] _ in
            Task { @MainActor in self?.clearLocal() }
        }
    }

    deinit {
        if let revocationObserver {
            NotificationCenter.default.removeObserver(revocationObserver)
        }
    }

    // MARK: - Nonce

    /// Generate + retain a fresh raw nonce and return the SHA-256 hex
    /// digest for `ASAuthorizationAppleIDRequest.nonce`.
    public func prepareNonce() -> String {
        var bytes = [UInt8](repeating: 0, count: 32)
        _ = SecRandomCopyBytes(kSecRandomDefault, bytes.count, &bytes)
        let raw = bytes.map { String(format: "%02x", $0) }.joined()
        pendingNonce = raw
        let digest = SHA256.hash(data: Data(raw.utf8))
        return digest.map { String(format: "%02x", $0) }.joined()
    }

    // MARK: - Sign in

    /// Entry point for the real SIWA credential.
    public func signIn(
        credential: ASAuthorizationAppleIDCredential, baseURL: URL
    ) async {
        guard
            let tokenData = credential.identityToken,
            let identityToken = String(data: tokenData, encoding: .utf8)
        else {
            lastError = "Apple returned no identity token."
            return
        }
        let fullName = [
            credential.fullName?.givenName, credential.fullName?.familyName,
        ].compactMap { $0 }.joined(separator: " ")
        await signIn(
            identityToken: identityToken,
            appleUserId: credential.user,
            fullName: fullName.isEmpty ? nil : fullName,
            baseURL: baseURL
        )
    }

    /// Stub-friendly core (UI tests + unit tests inject the token
    /// directly, skipping AuthenticationServices).
    public func signIn(
        identityToken: String,
        appleUserId: String?,
        fullName: String?,
        baseURL: URL
    ) async {
        isSigningIn = true
        lastError = nil
        defer { isSigningIn = false }
        do {
            let session = try await client.signInWithApple(
                baseURL: baseURL,
                identityToken: identityToken,
                nonce: pendingNonce,
                deviceId: AuthContext.shared.deviceId,
                fullName: fullName
            )
            pendingNonce = nil
            persist(session: session, appleUserId: appleUserId)
            await claim(baseURL: baseURL)
        } catch {
            lastError = (error as? LocalizedError)?.errorDescription
                ?? "Sign-in failed."
        }
    }

    // MARK: - Claim

    /// Attach this device's anonymous analyses to the account.
    public func claim(baseURL: URL) async {
        guard let token = secrets.read(key: Keys.token),
              let deviceId = AuthContext.shared.deviceId else {
            return
        }
        claimStatus = .claiming
        do {
            let count = try await client.claim(
                baseURL: baseURL, token: token, deviceId: deviceId
            )
            claimStatus = .synced(count)
        } catch {
            claimStatus = .failed
        }
    }

    // MARK: - Sign out / restore

    public func signOut(baseURL: URL) async {
        if let token = secrets.read(key: Keys.token) {
            // Best effort — local state clears regardless.
            try? await client.logout(baseURL: baseURL, token: token)
        }
        clearLocal()
    }

    /// Launch-time session check. A definitive 401 clears local state;
    /// a network error keeps the cached identity (offline app stays
    /// signed in).
    public func restore(baseURL: URL) async {
        guard let token = secrets.read(key: Keys.token) else { return }
        do {
            if let user = try await client.session(baseURL: baseURL, token: token) {
                persist(user: user)
            } else {
                clearLocal()
            }
        } catch AuthClientError.invalidToken {
            clearLocal()
        } catch {
            // Offline / transient — keep the cached identity.
        }
        // Apple-side revocation check (no-op when we never stored the
        // Apple user id, e.g. magic-link-only accounts).
        if let appleUserId = defaults.string(forKey: Keys.appleUserId) {
            let provider = ASAuthorizationAppleIDProvider()
            if let state = try? await provider.credentialState(forUserID: appleUserId),
               state == .revoked {
                clearLocal()
            }
        }
    }

    // MARK: - Persistence

    private func persist(session: AuthSession, appleUserId: String?) {
        secrets.write(key: Keys.token, value: session.token)
        AuthContext.shared.sessionToken = session.token
        if let appleUserId {
            defaults.set(appleUserId, forKey: Keys.appleUserId)
        }
        persist(user: session.user)
    }

    private func persist(user: AuthUser) {
        profile = user
        if let data = try? JSONEncoder().encode(user) {
            defaults.set(data, forKey: Keys.profile)
        }
    }

    private func clearLocal() {
        secrets.delete(key: Keys.token)
        defaults.removeObject(forKey: Keys.profile)
        defaults.removeObject(forKey: Keys.appleUserId)
        AuthContext.shared.sessionToken = nil
        profile = nil
        claimStatus = .idle
    }

    /// Nonisolated variant for the `-uitest-reset-account` launch hook,
    /// which runs before any store instance exists.
    public nonisolated static func resetPersisted(
        defaults: UserDefaults = .standard,
        secrets: any SecretStore = KeychainStore()
    ) {
        defaults.removeObject(forKey: Keys.profile)
        defaults.removeObject(forKey: Keys.appleUserId)
        secrets.delete(key: Keys.token)
    }
}
