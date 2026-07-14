// AuthClient.swift
//
// Client for the backend account API (tone_forge/auth/routes.py):
//
//   POST {base}/api/auth/apple    -> {"token": "...", "user": {...}}
//   GET  {base}/api/auth/session  -> {"user": {...} | null}
//   POST {base}/api/auth/logout
//   POST {base}/api/auth/claim    -> {"claimed": n}
//
// Takes the Apple identity token as an opaque String so this package
// never imports AuthenticationServices — the Mobile layer extracts it
// from the ASAuthorization credential.
//
// The transport is a protocol (`AuthProviding`, mirrors JobSubmitting)
// so UI tests can stub sign-in entirely.

import Foundation

public struct AuthUser: Codable, Equatable, Sendable {
    public let id: String
    public let email: String?
    public let displayName: String?

    enum CodingKeys: String, CodingKey {
        case id, email
        case displayName = "display_name"
    }

    public init(id: String, email: String?, displayName: String?) {
        self.id = id
        self.email = email
        self.displayName = displayName
    }
}

public struct AuthSession: Equatable, Sendable {
    public let token: String
    public let user: AuthUser

    public init(token: String, user: AuthUser) {
        self.token = token
        self.user = user
    }
}

public enum AuthClientError: Error, LocalizedError, Equatable {
    case badStatus(Int)
    case invalidToken       // 401 — expired or revoked session / rejected identity token
    case malformedResponse

    public var errorDescription: String? {
        switch self {
        case .badStatus(let code):
            return "Sign-in failed (HTTP \(code))."
        case .invalidToken:
            return "Your session has expired. Please sign in again."
        case .malformedResponse:
            return "The server returned an unexpected response."
        }
    }
}

/// Transport seam over the account API for test stubbing.
public protocol AuthProviding: Sendable {
    /// Exchange an Apple identity token for a backend session.
    func signInWithApple(
        baseURL: URL,
        identityToken: String,
        nonce: String?,
        deviceId: String?,
        fullName: String?
    ) async throws -> AuthSession

    /// Fetch the user for a session token; nil when the token is
    /// invalid or the caller is anonymous.
    func session(baseURL: URL, token: String) async throws -> AuthUser?

    /// Revoke the session server-side (best effort).
    func logout(baseURL: URL, token: String) async throws

    /// Attach this device's anonymous analyses to the signed-in
    /// account. Returns how many entries were claimed.
    func claim(baseURL: URL, token: String, deviceId: String) async throws -> Int
}

/// Production transport hitting the real backend.
public struct BackendAuthClient: AuthProviding {
    private let timeout: TimeInterval

    public init(timeout: TimeInterval = 30) {
        self.timeout = timeout
    }

    public func signInWithApple(
        baseURL: URL,
        identityToken: String,
        nonce: String?,
        deviceId: String?,
        fullName: String?
    ) async throws -> AuthSession {
        var body: [String: Any] = ["identity_token": identityToken]
        if let nonce { body["nonce"] = nonce }
        if let deviceId { body["device_id"] = deviceId }
        if let fullName, !fullName.isEmpty { body["full_name"] = fullName }

        var request = URLRequest(url: baseURL.appendingPathComponent("api/auth/apple"))
        request.httpMethod = "POST"
        request.timeoutInterval = timeout
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONSerialization.data(withJSONObject: body)
        if let deviceId {
            request.setValue(deviceId, forHTTPHeaderField: "X-Device-Id")
        }

        let (data, response) = try await URLSession.shared.data(for: request)
        try Self.checkStatus(response)
        return try Self.decodeSession(data)
    }

    public func session(baseURL: URL, token: String) async throws -> AuthUser? {
        var request = URLRequest(url: baseURL.appendingPathComponent("api/auth/session"))
        request.timeoutInterval = timeout
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")

        let (data, response) = try await URLSession.shared.data(for: request)
        try Self.checkStatus(response)
        struct Envelope: Codable { let user: AuthUser? }
        guard let envelope = try? JSONDecoder().decode(Envelope.self, from: data) else {
            throw AuthClientError.malformedResponse
        }
        return envelope.user
    }

    public func logout(baseURL: URL, token: String) async throws {
        var request = URLRequest(url: baseURL.appendingPathComponent("api/auth/logout"))
        request.httpMethod = "POST"
        request.timeoutInterval = timeout
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        let (_, response) = try await URLSession.shared.data(for: request)
        try Self.checkStatus(response)
    }

    public func claim(
        baseURL: URL, token: String, deviceId: String
    ) async throws -> Int {
        var request = URLRequest(url: baseURL.appendingPathComponent("api/auth/claim"))
        request.httpMethod = "POST"
        request.timeoutInterval = timeout
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        request.httpBody = try JSONSerialization.data(
            withJSONObject: ["device_id": deviceId]
        )

        let (data, response) = try await URLSession.shared.data(for: request)
        try Self.checkStatus(response)
        guard
            let object = try? JSONSerialization.jsonObject(with: data),
            let dict = object as? [String: Any],
            let claimed = dict["claimed"] as? Int
        else {
            throw AuthClientError.malformedResponse
        }
        return claimed
    }

    // MARK: - Shared decoding (pure, testable)

    static func checkStatus(_ response: URLResponse) throws {
        guard let http = response as? HTTPURLResponse else { return }
        switch http.statusCode {
        case 200...299:
            return
        case 401:
            throw AuthClientError.invalidToken
        default:
            throw AuthClientError.badStatus(http.statusCode)
        }
    }

    /// Decode the `/api/auth/apple` response body.
    public static func decodeSession(_ data: Data) throws -> AuthSession {
        struct Envelope: Codable {
            let token: String
            let user: AuthUser
        }
        guard
            let envelope = try? JSONDecoder().decode(Envelope.self, from: data),
            !envelope.token.isEmpty
        else {
            throw AuthClientError.malformedResponse
        }
        return AuthSession(token: envelope.token, user: envelope.user)
    }
}
