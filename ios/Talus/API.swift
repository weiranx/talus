import Foundation

enum APIError: LocalizedError {
    case signedOut
    case unreachable(String)
    case server(Int, String)

    var errorDescription: String? {
        switch self {
        case .signedOut: "Sign in to continue."
        case .unreachable(let url): "Could not reach the Talus engine at \(url). Is `talus serve` running?"
        case .server(_, let detail): detail
        }
    }
}

/// Talks to the Talus engine. Sign-in is a session cookie (`talus_session`), which URLSession keeps
/// in the shared cookie store, so it survives app launches for the 30 days the engine allows.
@MainActor
final class API: ObservableObject {
    static let defaultServer = "http://localhost:8000"

    @Published var server: String {
        didSet { UserDefaults.standard.set(server, forKey: "server") }
    }

    private let session: URLSession
    private let decoder: JSONDecoder = {
        let d = JSONDecoder()
        d.keyDecodingStrategy = .convertFromSnakeCase
        return d
    }()
    private let encoder: JSONEncoder = {
        let e = JSONEncoder()
        e.keyEncodingStrategy = .convertToSnakeCase
        return e
    }()

    init(session: URLSession = .shared) {
        self.session = session
        server = UserDefaults.standard.string(forKey: "server") ?? API.defaultServer
    }

    private func url(_ path: String) throws -> URL {
        let base = server.trimmingCharacters(in: CharacterSet(charactersIn: "/ "))
        guard let url = URL(string: base + path) else { throw APIError.unreachable(server) }
        return url
    }

    private func send(_ method: String, _ path: String, body: Data? = nil) async throws -> (Data, Int) {
        var req = URLRequest(url: try url(path))
        req.httpMethod = method
        req.timeoutInterval = 60
        if let body {
            req.httpBody = body
            req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        let data: Data, response: URLResponse
        do {
            (data, response) = try await session.data(for: req)
        } catch {
            throw APIError.unreachable(server)
        }
        let status = (response as? HTTPURLResponse)?.statusCode ?? 0
        if status == 401 && !path.hasPrefix("/api/auth/") { throw APIError.signedOut }
        if !(200..<300).contains(status) && status != 404 {
            throw APIError.server(status, Self.detail(data) ?? "The engine answered \(status).")
        }
        return (data, status)
    }

    /// FastAPI errors are `{"detail": "..."}`.
    static func detail(_ data: Data) -> String? {
        (try? JSONSerialization.jsonObject(with: data) as? [String: Any])?["detail"] as? String
    }

    func get<T: Decodable>(_ path: String, as: T.Type = T.self) async throws -> T {
        let (data, status) = try await send("GET", path)
        if status == 404 { throw APIError.server(404, Self.detail(data) ?? "Not found.") }
        return try decoder.decode(T.self, from: data)
    }

    @discardableResult
    func post(_ path: String, _ body: some Encodable) async throws -> Data {
        let (data, status) = try await send("POST", path, body: try encoder.encode(body))
        if status == 404 { throw APIError.server(404, Self.detail(data) ?? "Not found.") }
        return data
    }

    /// Posts JSON that is already shaped as the engine wants it (keys untouched).
    func postRaw(_ path: String, json: Data) async throws -> Data {
        let (data, status) = try await send("POST", path, body: json)
        if status == 404 { throw APIError.server(404, Self.detail(data) ?? "This engine has no \(path) yet.") }
        return data
    }

    // MARK: Accounts

    /// nil when the engine predates accounts (no `/api/auth/status`): everything is open.
    func authStatus() async throws -> AuthStatus? {
        let (data, status) = try await send("GET", "/api/auth/status")
        if status == 404 { return nil }
        return try decoder.decode(AuthStatus.self, from: data)
    }

    func signIn(email: String, password: String, create: Bool) async throws {
        struct Credentials: Encodable { let email: String; let password: String }
        try await post(create ? "/api/auth/signup" : "/api/auth/login", Credentials(email: email, password: password))
    }

    func signOut() async {
        _ = try? await send("POST", "/api/auth/logout", body: Data("{}".utf8))
        if let url = try? url("/"), let cookies = HTTPCookieStorage.shared.cookies(for: url) {
            cookies.forEach(HTTPCookieStorage.shared.deleteCookie)
        }
    }
}
