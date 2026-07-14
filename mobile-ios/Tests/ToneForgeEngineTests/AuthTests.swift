// AuthTests.swift
//
// DeviceIdentity persistence, AuthContext request stamping, and
// AuthClient response decoding / status mapping. No network.

import Foundation
import XCTest
@testable import ToneForgeEngine

final class DeviceIdentityTests: XCTestCase {
    private var defaults: UserDefaults!
    private let suite = "toneforge.tests.deviceidentity"

    override func setUp() {
        super.setUp()
        defaults = UserDefaults(suiteName: suite)
        defaults.removePersistentDomain(forName: suite)
    }

    override func tearDown() {
        defaults.removePersistentDomain(forName: suite)
        super.tearDown()
    }

    func testGeneratesAndPersists() {
        let first = DeviceIdentity.id(defaults: defaults)
        XCTAssertFalse(first.isEmpty)
        XCTAssertEqual(DeviceIdentity.id(defaults: defaults), first)
        XCTAssertEqual(defaults.string(forKey: DeviceIdentity.defaultsKey), first)
    }

    func testRespectsExistingValue() {
        defaults.set("fixed-device", forKey: DeviceIdentity.defaultsKey)
        XCTAssertEqual(DeviceIdentity.id(defaults: defaults), "fixed-device")
    }

    func testLowercaseUUIDFormat() {
        let id = DeviceIdentity.id(defaults: defaults)
        XCTAssertEqual(id, id.lowercased())
        XCTAssertNotNil(UUID(uuidString: id))
    }
}

final class AuthContextTests: XCTestCase {
    private func makeRequest() -> URLRequest {
        URLRequest(url: URL(string: "https://example.com/api/history")!)
    }

    func testAppliesNothingWhenEmpty() {
        let ctx = AuthContext()
        var request = makeRequest()
        ctx.apply(to: &request)
        XCTAssertNil(request.value(forHTTPHeaderField: "Authorization"))
        XCTAssertNil(request.value(forHTTPHeaderField: "X-Device-Id"))
    }

    func testAppliesDeviceIdOnly() {
        let ctx = AuthContext()
        ctx.deviceId = "dev-1"
        var request = makeRequest()
        ctx.apply(to: &request)
        XCTAssertNil(request.value(forHTTPHeaderField: "Authorization"))
        XCTAssertEqual(request.value(forHTTPHeaderField: "X-Device-Id"), "dev-1")
    }

    func testAppliesBearerAndDevice() {
        let ctx = AuthContext()
        ctx.deviceId = "dev-1"
        ctx.sessionToken = "tok123"
        var request = makeRequest()
        ctx.apply(to: &request)
        XCTAssertEqual(
            request.value(forHTTPHeaderField: "Authorization"), "Bearer tok123"
        )
        XCTAssertEqual(request.value(forHTTPHeaderField: "X-Device-Id"), "dev-1")
    }

    func testSignOutClearsBearer() {
        let ctx = AuthContext()
        ctx.sessionToken = "tok123"
        ctx.sessionToken = nil
        var request = makeRequest()
        ctx.apply(to: &request)
        XCTAssertNil(request.value(forHTTPHeaderField: "Authorization"))
    }

    func testEmptyStringsNotApplied() {
        let ctx = AuthContext()
        ctx.sessionToken = ""
        ctx.deviceId = ""
        var request = makeRequest()
        ctx.apply(to: &request)
        XCTAssertNil(request.value(forHTTPHeaderField: "Authorization"))
        XCTAssertNil(request.value(forHTTPHeaderField: "X-Device-Id"))
    }
}

final class AuthClientTests: XCTestCase {
    // MARK: decodeSession (POST /api/auth/apple response)

    func testDecodeSessionGolden() throws {
        let json = """
        {"token": "abc123", "user": {"id": "u-1", "email": "a@b.co",
         "display_name": "Ann"}}
        """
        let session = try BackendAuthClient.decodeSession(Data(json.utf8))
        XCTAssertEqual(session.token, "abc123")
        XCTAssertEqual(session.user.id, "u-1")
        XCTAssertEqual(session.user.email, "a@b.co")
        XCTAssertEqual(session.user.displayName, "Ann")
    }

    func testDecodeSessionNullableFields() throws {
        let json = """
        {"token": "t", "user": {"id": "u-2", "email": null, "display_name": null}}
        """
        let session = try BackendAuthClient.decodeSession(Data(json.utf8))
        XCTAssertNil(session.user.email)
        XCTAssertNil(session.user.displayName)
    }

    func testDecodeSessionRejectsEmptyToken() {
        let json = #"{"token": "", "user": {"id": "u"}}"#
        XCTAssertThrowsError(
            try BackendAuthClient.decodeSession(Data(json.utf8))
        ) { error in
            XCTAssertEqual(error as? AuthClientError, .malformedResponse)
        }
    }

    func testDecodeSessionRejectsGarbage() {
        XCTAssertThrowsError(
            try BackendAuthClient.decodeSession(Data("not json".utf8))
        ) { error in
            XCTAssertEqual(error as? AuthClientError, .malformedResponse)
        }
    }

    // MARK: status mapping

    private func response(_ code: Int) -> HTTPURLResponse {
        HTTPURLResponse(
            url: URL(string: "https://example.com")!,
            statusCode: code, httpVersion: nil, headerFields: nil
        )!
    }

    func testStatus200Passes() {
        XCTAssertNoThrow(try BackendAuthClient.checkStatus(response(200)))
    }

    func testStatus401MapsToInvalidToken() {
        XCTAssertThrowsError(
            try BackendAuthClient.checkStatus(response(401))
        ) { error in
            XCTAssertEqual(error as? AuthClientError, .invalidToken)
        }
    }

    func testStatus500MapsToBadStatus() {
        XCTAssertThrowsError(
            try BackendAuthClient.checkStatus(response(500))
        ) { error in
            XCTAssertEqual(error as? AuthClientError, .badStatus(500))
        }
    }
}
