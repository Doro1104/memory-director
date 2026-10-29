import Foundation
import CoreTransferable
import UniformTypeIdentifiers

struct PickedMedia: Transferable {
    let file: URL

    private static func stage(_ source: URL, fallback: String) throws -> PickedMedia {
        let folder = FileManager.default.temporaryDirectory.appendingPathComponent("selected-media", isDirectory: true)
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        let ext = source.pathExtension.isEmpty ? fallback : source.pathExtension
        let cleanName = String(source.deletingPathExtension().lastPathComponent.prefix(120))
        let destination = folder.appendingPathComponent(UUID().uuidString + "-" + cleanName + "." + ext)
        try FileManager.default.copyItem(at: source, to: destination)
        return PickedMedia(file: destination)
    }

    static var transferRepresentation: some TransferRepresentation {
        FileRepresentation(importedContentType: .image) { received in
            try stage(received.file, fallback: "heic")
        }
        FileRepresentation(importedContentType: .movie) { received in
            try stage(received.file, fallback: "mov")
        }
    }

    static func cleanTemporaryFiles() {
        let folder = FileManager.default.temporaryDirectory.appendingPathComponent("selected-media", isDirectory: true)
        try? FileManager.default.removeItem(at: folder)
    }
}
