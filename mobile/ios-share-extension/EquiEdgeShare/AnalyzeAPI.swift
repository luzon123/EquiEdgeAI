import Foundation

/// The one piece of networking code every iOS surface (Share Extension
/// today; a future "Analyze Latest Screenshot" App Intent — see
/// mobile/ios-screen-capture-research.md) calls to talk to the backend.
/// Kept as a standalone file specifically so a future App Intents
/// extension target can add this same file to its target membership and
/// reuse it verbatim — no rewrite, no duplicated multipart/networking code.
///
/// Same contract as mobile/src/api/client.ts (React Native) and
/// mobile/android-overlay/.../AnalyzeApi.kt (Android). Keep all three in
/// sync if the endpoint URL or response shape ever changes.
enum AnalyzeAPI {
    static let endpoint = URL(string: "https://equiedge-ai.onrender.com/mobile/analyze")!
    static let timeoutSeconds: TimeInterval = 12

    enum Outcome {
        case success(winrate: Double, action: String)
        case failure(message: String)
    }

    static func multipartBody(imageData: Data, boundary: String) -> Data {
        let isPNG = imageData.starts(with: [0x89, 0x50, 0x4E, 0x47])
        let filename = isPNG ? "table.png" : "table.jpg"
        let contentType = isPNG ? "image/png" : "image/jpeg"

        var body = Data()
        func append(_ string: String) { body.append(string.data(using: .utf8)!) }

        append("--\(boundary)\r\n")
        append("Content-Disposition: form-data; name=\"image\"; filename=\"\(filename)\"\r\n")
        append("Content-Type: \(contentType)\r\n\r\n")
        body.append(imageData)
        append("\r\n--\(boundary)--\r\n")
        return body
    }

    /// Async wrapper so both the extension's completion-handler call site
    /// and a future App Intents `perform()` (which is itself `async`) can
    /// call the exact same upload logic without adapting styles.
    static func analyze(imageData: Data) async -> Outcome {
        var request = URLRequest(url: endpoint)
        request.httpMethod = "POST"
        request.timeoutInterval = timeoutSeconds
        let boundary = "EquiEdgeBoundary-\(UUID().uuidString)"
        request.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")
        request.httpBody = multipartBody(imageData: imageData, boundary: boundary)

        let data: Data
        let response: URLResponse
        do {
            (data, response) = try await URLSession.shared.data(for: request)
        } catch {
            return .failure(message: "Could not reach the server. Check your connection and try again.")
        }

        guard
            let http = response as? HTTPURLResponse,
            let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
        else {
            return .failure(message: "Analysis failed. Try again.")
        }

        guard (200...299).contains(http.statusCode) else {
            return .failure(message: (json["error"] as? String) ?? "Analysis failed. Try again.")
        }

        guard
            let winrate = json["winrate"] as? Double,
            let action = json["action"] as? String
        else {
            return .failure(message: "Analysis failed. Try again.")
        }

        return .success(winrate: winrate, action: action)
    }
}
