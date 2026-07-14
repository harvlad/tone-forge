// AccountStoreTests.swift
//
// AccountStore state machine against an in-memory secret store and a
// scripted AuthProviding fake: persist/restore, sign-in (+ auto
// claim), sign-out, definitive-401 vs offline restore, claim retry,
// and the -uitest-reset-account hook.
//
// AuthContext.shared is process-global; setUp snapshots it and
// tearDown restores it so these tests never leak state into others.

import XCTest
import ToneForgeEngine
@testable import ToneForgeMobile

/// Scripted transport. Class so tests mutate results after handing it
/// to the store.
private final class FakeAuthClient: AuthProviding, @unchecked Sendable {
    var signInResult: Result<AuthSession, Error> =
        .failure(AuthClientError.badStatus(500))
    var sessionResult: Result<AuthUser?, Error> = .success(nil)
    var claimResult: Result<Int, Error> = .success(0)
    var logoutError: Error?

    private(set) var lastSignInNonce: String?
    private(set) var lastSignInDeviceId: String?
    private(set) var lastClaimDeviceId: String?
    private(set) var logoutCalls = 0

    func signInWithApple(
        baseURL: URL,
        identityToken: String,
        nonce: String?,
        deviceId: String?,
        fullName: String?
    ) async throws -> AuthSession {
        lastSignInNonce = nonce
        lastSignInDeviceId = deviceId
        return try signInResult.get()
    }

    func session(baseURL: URL, token: String) async throws -> AuthUser? {
        try sessionResult.get()
    }

    func logout(baseURL: URL, token: String) async throws {
        logoutCalls += 1
        if let logoutError { throw logoutError }
    }

    func claim(baseURL: URL, token: String, deviceId: String) async throws -> Int {
        lastClaimDeviceId = deviceId
        return try claimResult.get()
    }
}

@MainActor
final class AccountStoreTests: XCTestCase {

    private var suiteName: String!
    private var defaults: UserDefaults!
    private var secrets: InMemorySecretStore!
    private var client: FakeAuthClient!
    private var savedToken: String?
    private var savedDeviceId: String?

    private let baseURL = URL(string: "https://example.test")!
    private let user = AuthUser(
        id: "u1", email: "u1@example.com", displayName: "User One"
    )

    override func setUp() {
        super.setUp()
        suiteName = "AccountStoreTests-\(UUID().uuidString)"
        defaults = UserDefaults(suiteName: suiteName)
        secrets = InMemorySecretStore()
        client = FakeAuthClient()
        savedToken = AuthContext.shared.sessionToken
        savedDeviceId = AuthContext.shared.deviceId
        AuthContext.shared.sessionToken = nil
        AuthContext.shared.deviceId = "test-device"
    }

    override func tearDown() {
        defaults.removePersistentDomain(forName: suiteName)
        AuthContext.shared.sessionToken = savedToken
        AuthContext.shared.deviceId = savedDeviceId
        super.tearDown()
    }

    private func makeStore() -> AccountStore {
        AccountStore(client: client, secrets: secrets, defaults: defaults)
    }

    // MARK: - Init / persistence

    func testInitAnonymousByDefault() {
        let store = makeStore()
        XCTAssertNil(store.profile)
        XCTAssertEqual(store.claimStatus, .idle)
        XCTAssertNil(AuthContext.shared.sessionToken)
    }

    func testInitRestoresPersistedIdentity() throws {
        defaults.set(
            try JSONEncoder().encode(user), forKey: AccountStore.Keys.profile
        )
        secrets.write(key: AccountStore.Keys.token, value: "persisted-token")

        let store = makeStore()
        XCTAssertEqual(store.profile, user)
        XCTAssertEqual(AuthContext.shared.sessionToken, "persisted-token")
    }

    // MARK: - Nonce

    func testPrepareNonceReturnsSHA256HexAndVaries() {
        let store = makeStore()
        let a = store.prepareNonce()
        let b = store.prepareNonce()
        XCTAssertEqual(a.count, 64)
        XCTAssertTrue(a.allSatisfy(\.isHexDigit))
        XCTAssertNotEqual(a, b)
    }

    // MARK: - Sign in

    func testSignInSuccessPersistsAndAutoClaims() async {
        client.signInResult = .success(AuthSession(token: "t1", user: user))
        client.claimResult = .success(3)

        let store = makeStore()
        _ = store.prepareNonce()
        await store.signIn(
            identityToken: "id-token", appleUserId: "apple-1",
            fullName: "User One", baseURL: baseURL
        )

        XCTAssertEqual(store.profile, user)
        XCTAssertNil(store.lastError)
        XCTAssertEqual(store.claimStatus, .synced(3))
        XCTAssertEqual(secrets.read(key: AccountStore.Keys.token), "t1")
        XCTAssertEqual(AuthContext.shared.sessionToken, "t1")
        XCTAssertEqual(
            defaults.string(forKey: AccountStore.Keys.appleUserId), "apple-1"
        )
        XCTAssertNotNil(defaults.data(forKey: AccountStore.Keys.profile))
        // Raw nonce (64 hex) went to the backend, not Apple's digest.
        XCTAssertEqual(client.lastSignInNonce?.count, 64)
        XCTAssertEqual(client.lastSignInDeviceId, "test-device")
        XCTAssertEqual(client.lastClaimDeviceId, "test-device")
    }

    func testSignInFailureSetsErrorKeepsAnonymous() async {
        client.signInResult = .failure(AuthClientError.badStatus(500))

        let store = makeStore()
        await store.signIn(
            identityToken: "id-token", appleUserId: nil,
            fullName: nil, baseURL: baseURL
        )

        XCTAssertNil(store.profile)
        XCTAssertEqual(store.lastError, "Sign-in failed (HTTP 500).")
        XCTAssertNil(secrets.read(key: AccountStore.Keys.token))
        XCTAssertNil(AuthContext.shared.sessionToken)
        XCTAssertEqual(store.claimStatus, .idle)
    }

    // MARK: - Claim

    func testClaimWithoutTokenIsNoop() async {
        let store = makeStore()
        await store.claim(baseURL: baseURL)
        XCTAssertEqual(store.claimStatus, .idle)
        XCTAssertNil(client.lastClaimDeviceId)
    }

    func testClaimFailureThenRetrySucceeds() async {
        client.signInResult = .success(AuthSession(token: "t1", user: user))
        client.claimResult = .failure(AuthClientError.badStatus(503))

        let store = makeStore()
        await store.signIn(
            identityToken: "id-token", appleUserId: nil,
            fullName: nil, baseURL: baseURL
        )
        XCTAssertEqual(store.claimStatus, .failed)

        client.claimResult = .success(1)
        await store.claim(baseURL: baseURL)
        XCTAssertEqual(store.claimStatus, .synced(1))
    }

    // MARK: - Sign out

    func testSignOutRevokesAndClears() async {
        client.signInResult = .success(AuthSession(token: "t1", user: user))
        let store = makeStore()
        await store.signIn(
            identityToken: "id-token", appleUserId: "apple-1",
            fullName: nil, baseURL: baseURL
        )

        await store.signOut(baseURL: baseURL)

        XCTAssertEqual(client.logoutCalls, 1)
        XCTAssertNil(store.profile)
        XCTAssertEqual(store.claimStatus, .idle)
        XCTAssertNil(secrets.read(key: AccountStore.Keys.token))
        XCTAssertNil(AuthContext.shared.sessionToken)
        XCTAssertNil(defaults.data(forKey: AccountStore.Keys.profile))
        XCTAssertNil(defaults.string(forKey: AccountStore.Keys.appleUserId))
    }

    func testSignOutClearsLocallyEvenWhenLogoutFails() async {
        client.signInResult = .success(AuthSession(token: "t1", user: user))
        client.logoutError = AuthClientError.badStatus(500)
        let store = makeStore()
        await store.signIn(
            identityToken: "id-token", appleUserId: nil,
            fullName: nil, baseURL: baseURL
        )

        await store.signOut(baseURL: baseURL)

        XCTAssertNil(store.profile)
        XCTAssertNil(secrets.read(key: AccountStore.Keys.token))
    }

    func testSignOutWithoutTokenSkipsLogoutCall() async {
        let store = makeStore()
        await store.signOut(baseURL: baseURL)
        XCTAssertEqual(client.logoutCalls, 0)
    }

    // MARK: - Restore

    func testRestoreWithoutTokenIsNoop() async {
        let store = makeStore()
        client.sessionResult = .success(user)
        await store.restore(baseURL: baseURL)
        XCTAssertNil(store.profile)
    }

    func testRestoreRefreshesProfile() async throws {
        secrets.write(key: AccountStore.Keys.token, value: "t1")
        defaults.set(
            try JSONEncoder().encode(user), forKey: AccountStore.Keys.profile
        )
        let renamed = AuthUser(
            id: "u1", email: "u1@example.com", displayName: "Renamed"
        )
        client.sessionResult = .success(renamed)

        let store = makeStore()
        await store.restore(baseURL: baseURL)
        XCTAssertEqual(store.profile, renamed)
    }

    func testRestoreDefinitive401ClearsState() async throws {
        secrets.write(key: AccountStore.Keys.token, value: "t1")
        defaults.set(
            try JSONEncoder().encode(user), forKey: AccountStore.Keys.profile
        )
        client.sessionResult = .failure(AuthClientError.invalidToken)

        let store = makeStore()
        XCTAssertEqual(store.profile, user)
        await store.restore(baseURL: baseURL)
        XCTAssertNil(store.profile)
        XCTAssertNil(secrets.read(key: AccountStore.Keys.token))
        XCTAssertNil(AuthContext.shared.sessionToken)
    }

    func testRestoreNullUserClearsState() async throws {
        secrets.write(key: AccountStore.Keys.token, value: "t1")
        defaults.set(
            try JSONEncoder().encode(user), forKey: AccountStore.Keys.profile
        )
        client.sessionResult = .success(nil)

        let store = makeStore()
        await store.restore(baseURL: baseURL)
        XCTAssertNil(store.profile)
        XCTAssertNil(secrets.read(key: AccountStore.Keys.token))
    }

    func testRestoreNetworkErrorKeepsCachedIdentity() async throws {
        secrets.write(key: AccountStore.Keys.token, value: "t1")
        defaults.set(
            try JSONEncoder().encode(user), forKey: AccountStore.Keys.profile
        )
        client.sessionResult = .failure(URLError(.notConnectedToInternet))

        let store = makeStore()
        await store.restore(baseURL: baseURL)
        XCTAssertEqual(store.profile, user)
        XCTAssertEqual(secrets.read(key: AccountStore.Keys.token), "t1")
        XCTAssertEqual(AuthContext.shared.sessionToken, "t1")
    }

    // MARK: - Reset hook

    func testResetPersistedClearsDefaultsAndSecrets() throws {
        defaults.set(
            try JSONEncoder().encode(user), forKey: AccountStore.Keys.profile
        )
        defaults.set("apple-1", forKey: AccountStore.Keys.appleUserId)
        secrets.write(key: AccountStore.Keys.token, value: "t1")

        AccountStore.resetPersisted(defaults: defaults, secrets: secrets)

        XCTAssertNil(defaults.data(forKey: AccountStore.Keys.profile))
        XCTAssertNil(defaults.string(forKey: AccountStore.Keys.appleUserId))
        XCTAssertNil(secrets.read(key: AccountStore.Keys.token))
    }
}
