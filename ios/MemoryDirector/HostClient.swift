import Foundation
import Combine
import CryptoKit
import Security

// No analytics SDK, remote model call, public discovery service or persistent cookie jar.
final class PinnedTransport: NSObject, URLSessionDelegate, URLSessionTaskDelegate {
    let pair: Pairing
    let progress: (Double) -> Void
    lazy var session: URLSession = {
        let config = URLSessionConfiguration.ephemeral
        config.urlCache = nil
        config.allowsCellularAccess = false
        config.timeoutIntervalForRequest = 60
        config.timeoutIntervalForResource = 3600
        config.httpMaximumConnectionsPerHost = 2
        return URLSession(configuration: config, delegate: self, delegateQueue: nil)
    }()

    init(pair: Pairing, progress: @escaping (Double) -> Void) {
        self.pair = pair; self.progress = progress
    }

    func urlSession(_ session: URLSession, didReceive challenge: URLAuthenticationChallenge,
                    completionHandler: @escaping (URLSession.AuthChallengeDisposition, URLCredential?) -> Void) {
        guard challenge.protectionSpace.authenticationMethod == NSURLAuthenticationMethodServerTrust,
              challenge.protectionSpace.host == pair.baseURL.host,
              let trust = challenge.protectionSpace.serverTrust,
              let cert = SecTrustGetCertificateAtIndex(trust, 0) else {
            completionHandler(.cancelAuthenticationChallenge, nil); return
        }
        let bytes = SecCertificateCopyData(cert) as Data
        let digest = SHA256.hash(data: bytes).map { String(format: "%02x", $0) }.joined()
        guard digest == pair.certificate_sha256.lowercased() else {
            completionHandler(.cancelAuthenticationChallenge, nil); return
        }
        completionHandler(.useCredential, URLCredential(trust: trust))
    }

    func urlSession(_ session: URLSession, task: URLSessionTask,
                    willPerformHTTPRedirection response: HTTPURLResponse, newRequest request: URLRequest,
                    completionHandler: @escaping (URLRequest?) -> Void) {
        completionHandler(nil) // Never forward credentials or media to a redirect target.
    }

    func urlSession(_ session: URLSession, task: URLSessionTask, didSendBodyData bytesSent: Int64,
                    totalBytesSent: Int64, totalBytesExpectedToSend: Int64) {
        if totalBytesExpectedToSend > 0 { progress(Double(totalBytesSent) / Double(totalBytesExpectedToSend)) }
    }
}

struct HostSession: Decodable {
    struct User: Decodable { let id: String; let name: String }
    let user: User?
    let setup: Bool
    let csrf: String
}
struct UploadResult: Decodable { let id: String; let duplicate: Bool; let ignored_live: Bool? }
private struct HostFailure: Decodable { let error: String }

@MainActor
final class HostClient: ObservableObject {
    @Published var pair: Pairing?
    @Published var username = ""
    @Published var connected = false
    @Published var progress: Double = 0
    private var transport: PinnedTransport?
    private var csrf = ""

    init() {
        if let saved = UserDefaults.standard.string(forKey: "pairedHost") {
            pair = try? Pairing.parse(saved)
        }
    }

    func setPair(_ text: String) throws {
        let validated = try Pairing.parse(text)
        transport?.session.invalidateAndCancel()
        transport = nil; csrf = ""; connected = false; pair = validated
        UserDefaults.standard.set(text, forKey: "pairedHost")
    }

    private func request(_ path: String, method: String = "GET") throws -> URLRequest {
        guard let pair else { throw ClientError.message("请先配对家庭电脑。") }
        var request = URLRequest(url: pair.baseURL.appendingPathComponent(path))
        request.httpMethod = method
        request.setValue(csrf, forHTTPHeaderField: "X-CSRF-Token")
        return request
    }

    private func validate(_ data: Data, _ response: URLResponse) throws {
        guard let http = response as? HTTPURLResponse, (200...299).contains(http.statusCode) else {
            let failure = try? JSONDecoder().decode(HostFailure.self, from: data)
            throw ClientError.message(failure?.error ?? "连接未完成，请检查家庭网络和电脑服务。")
        }
    }

    func login(name: String, password: String) async throws {
        guard let pair else { throw ClientError.message("请先配对家庭电脑。") }
        transport?.session.invalidateAndCancel()
        let transport = PinnedTransport(pair: pair) { [weak self] value in
            Task { @MainActor in self?.progress = value }
        }
        self.transport = transport
        let (stateData, stateResponse) = try await transport.session.data(for: request("api/session"))
        try validate(stateData, stateResponse)
        let state = try JSONDecoder().decode(HostSession.self, from: stateData)
        guard !state.setup else { throw ClientError.message("请先在电脑本机网页创建首个账号。") }
        csrf = state.csrf
        var login = try request("api/login", method: "POST")
        login.setValue("application/json", forHTTPHeaderField: "Content-Type")
        login.httpBody = try JSONSerialization.data(withJSONObject: ["name": name, "password": password])
        let (data, response) = try await transport.session.data(for: login); try validate(data, response)
        let (fresh, freshResponse) = try await transport.session.data(for: request("api/session")); try validate(fresh, freshResponse)
        let account = try JSONDecoder().decode(HostSession.self, from: fresh)
        guard let user = account.user else { throw ClientError.message("登录未完成，请重试。") }
        csrf = account.csrf; username = user.name; connected = true
    }

    func upload(_ file: URL) async throws -> UploadResult {
        guard connected, let transport else { throw ClientError.message("请先登录家庭电脑。") }
        let size = try file.resourceValues(forKeys: [.fileSizeKey]).fileSize ?? 0
        guard size > 0 && Int64(size) <= 4 * 1024 * 1024 * 1024 else { throw ClientError.message("文件为空或超过 4 GB。") }
        var request = try self.request("api/upload", method: "POST")
        var parts = URLComponents(url: request.url!, resolvingAgainstBaseURL: false)!
        parts.queryItems = [URLQueryItem(name: "name", value: file.lastPathComponent)]
        request.url = parts.url
        request.setValue("application/octet-stream", forHTTPHeaderField: "Content-Type")
        progress = 0
        let (data, response) = try await transport.session.upload(for: request, fromFile: file)
        try validate(data, response)
        return try JSONDecoder().decode(UploadResult.self, from: data)
    }

    func disconnect(forget: Bool = false) {
        transport?.session.invalidateAndCancel(); transport = nil
        csrf = ""; username = ""; connected = false; progress = 0
        if forget { pair = nil; UserDefaults.standard.removeObject(forKey: "pairedHost") }
    }
}
