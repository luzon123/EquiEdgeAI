import UIKit
import UniformTypeIdentifiers

/// Lightweight iOS Share Extension: screenshot -> upload -> winrate + action.
///
/// Deliberately NOT SLComposeServiceViewController — that base class builds
/// a caption/compose UI meant for posting to social networks, which this
/// flow has no use for. The whole point is the shortest possible path:
///
///     ClubGG screenshot -> Share -> EquiEdgeAI -> result -> Done
///
/// This runs in the extension's own process, entirely separate from the
/// React Native app (extensions cannot host a full RN/JS runtime), so the
/// upload is a small self-contained URLSession call. It carries zero poker
/// logic: it uploads bytes and displays exactly what the server returns.
/// Keep AnalyzeAPI in sync with mobile/src/api/config.ts + client.ts if the
/// endpoint URL or response shape ever changes.
final class ShareViewController: UIViewController {

    private let spinner       = UIActivityIndicatorView(style: .large)
    private let winrateLabel  = UILabel()
    private let actionLabel   = UILabel()
    private let messageLabel  = UILabel()
    private let doneButton    = UIButton(type: .system)

    override func viewDidLoad() {
        super.viewDidLoad()
        view.backgroundColor = UIColor(red: 0x0B / 255, green: 0x0F / 255, blue: 0x14 / 255, alpha: 1)
        buildLayout()
        loadSharedImage()
    }

    // MARK: - Layout (built in code — no storyboard to keep in sync)

    private func buildLayout() {
        spinner.color = .white
        spinner.startAnimating()

        winrateLabel.font = .systemFont(ofSize: 64, weight: .heavy)
        winrateLabel.textColor = .white
        winrateLabel.textAlignment = .center
        winrateLabel.isHidden = true

        actionLabel.font = .systemFont(ofSize: 32, weight: .heavy)
        actionLabel.textAlignment = .center
        actionLabel.isHidden = true

        messageLabel.font = .systemFont(ofSize: 17, weight: .medium)
        messageLabel.textColor = UIColor(white: 0.6, alpha: 1)
        messageLabel.textAlignment = .center
        messageLabel.numberOfLines = 0
        messageLabel.isHidden = true

        doneButton.setTitle("Done", for: .normal)
        doneButton.setTitleColor(UIColor(white: 0.55, alpha: 1), for: .normal)
        doneButton.titleLabel?.font = .systemFont(ofSize: 16, weight: .semibold)
        doneButton.addTarget(self, action: #selector(finish), for: .touchUpInside)
        doneButton.isHidden = true

        let stack = UIStackView(arrangedSubviews: [spinner, winrateLabel, actionLabel, messageLabel, doneButton])
        stack.axis = .vertical
        stack.alignment = .center
        stack.spacing = 16
        stack.translatesAutoresizingMaskIntoConstraints = false
        view.addSubview(stack)

        NSLayoutConstraint.activate([
            stack.centerXAnchor.constraint(equalTo: view.centerXAnchor),
            stack.centerYAnchor.constraint(equalTo: view.centerYAnchor),
            stack.leadingAnchor.constraint(greaterThanOrEqualTo: view.leadingAnchor, constant: 32),
            stack.trailingAnchor.constraint(lessThanOrEqualTo: view.trailingAnchor, constant: -32),
        ])
    }

    // MARK: - Image extraction

    private func loadSharedImage() {
        guard
            let item = extensionContext?.inputItems.first as? NSExtensionItem,
            let provider = item.attachments?.first
        else {
            showError("No image was shared.")
            return
        }

        let imageType = UTType.image.identifier
        guard provider.hasItemConformingToTypeIdentifier(imageType) else {
            // NSExtensionActivationRule in Info.plist should already keep this
            // extension out of the share sheet for non-image items, but a
            // multi-item share (photo + caption) can still land here.
            showError("Only screenshots can be analyzed.")
            return
        }

        provider.loadItem(forTypeIdentifier: imageType, options: nil) { [weak self] item, error in
            guard let self else { return }
            guard error == nil, let data = Self.extractImageData(from: item) else {
                DispatchQueue.main.async { self.showError("Couldn't read the shared image.") }
                return
            }
            self.upload(imageData: data)
        }
    }

    /// NSItemProvider hands back a file URL, a UIImage, or raw Data
    /// depending on the source app — normalise all three to raw bytes.
    /// Real screenshots (the iOS Screenshot feature) are always PNG; a
    /// photo shared from the camera roll could be HEIC, which the backend
    /// does not decode (Pillow without a HEIC plugin) — that's an accepted
    /// limitation of the documented "screenshot" flow, not a bug here.
    private static func extractImageData(from item: NSSecureCoding?) -> Data? {
        if let url = item as? URL, let data = try? Data(contentsOf: url) {
            return data
        }
        if let image = item as? UIImage {
            return image.pngData()
        }
        if let data = item as? Data {
            return data
        }
        return nil
    }

    // MARK: - Upload
    //
    // Delegates to AnalyzeAPI.swift, shared with any future App Intents
    // extension (see mobile/ios-screen-capture-research.md) — this file
    // has no networking/multipart code of its own.

    private func upload(imageData: Data) {
        Task { @MainActor in
            switch await AnalyzeAPI.analyze(imageData: imageData) {
            case .success(let winrate, let action):
                showResult(winrate: winrate, action: action)
            case .failure(let message):
                showError(message)
            }
        }
    }

    // MARK: - Result UI

    private func showResult(winrate: Double, action: String) {
        spinner.stopAnimating()
        spinner.isHidden = true

        winrateLabel.text = "\(Int(winrate.rounded()))%"
        winrateLabel.isHidden = false

        actionLabel.text = action
        actionLabel.textColor = action == "FOLD"
            ? UIColor(red: 0xE5 / 255, green: 0x48 / 255, blue: 0x4D / 255, alpha: 1)
            : UIColor(red: 0x3D / 255, green: 0xD6 / 255, blue: 0x8C / 255, alpha: 1)
        actionLabel.isHidden = false

        doneButton.isHidden = false
    }

    private func showError(_ message: String) {
        spinner.stopAnimating()
        spinner.isHidden = true
        messageLabel.text = message
        messageLabel.isHidden = false
        doneButton.isHidden = false
    }

    @objc private func finish() {
        extensionContext?.completeRequest(returningItems: nil)
    }
}
