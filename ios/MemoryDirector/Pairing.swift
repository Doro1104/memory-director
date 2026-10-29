import Foundation
import Network

struct Pairing: Codable, Equatable {
    let version: Int
    let url: String
    let certificate_sha256: String

    var baseURL: URL { URL(string: url)! }

    static func parse(_ text: String) throws -> Pairing {
        let pair = try JSONDecoder().decode(Pairing.self, from: Data(text.utf8))
        guard pair.version == 1, let parts = URLComponents(string: pair.url),
              parts.scheme == "https", let host = parts.host,
              parts.user == nil, parts.password == nil, parts.query == nil, parts.fragment == nil,
              parts.path.isEmpty || parts.path == "/",
              let address = IPv4Address(host), pair.certificate_sha256.count == 64,
              pair.certificate_sha256.allSatisfy({ $0.isHexDigit }) else {
            throw ClientError.message("配对信息无效，请扫描电脑网页中的配对二维码。")
        }
        let octets = Array(address.rawValue)
        guard octets[0] == 10 || octets[0] == 127 ||
                (octets[0] == 192 && octets[1] == 168) ||
                (octets[0] == 172 && (16...31).contains(octets[1])) else {
            throw ClientError.message("首版仅连接家庭局域网 IPv4 地址。")
        }
        return pair
    }
}

enum ClientError: LocalizedError {
    case message(String)
    var errorDescription: String? { if case .message(let text) = self { return text }; return nil }
}
