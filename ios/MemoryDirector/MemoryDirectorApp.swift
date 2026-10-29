import SwiftUI
import PhotosUI
import VisionKit
import AVFoundation

@main
@MainActor
struct MemoryDirectorApp: App {
    @StateObject private var host = HostClient()
    init() { PickedMedia.cleanTemporaryFiles() }
    var body: some Scene { WindowGroup { ContentView().environmentObject(host) } }
}

@MainActor
struct ContentView: View {
    @EnvironmentObject private var host: HostClient
    @State private var pairingText = ""
    @State private var loginName = ""
    @State private var password = ""
    @State private var selection: [PhotosPickerItem] = []
    @State private var busy = false
    @State private var scanning = false
    @State private var message = ""
    @State private var results: [String] = []
    @State private var current = ""
    private let green = Color(red: 0.25, green: 0.34, blue: 0.29)

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 22) {
                    VStack(alignment: .leading, spacing: 8) {
                        Text("YOUR LIFE. YOUR CINEMA.").font(.caption).tracking(3)
                        Text("把手机里的时光，带回家。").font(.system(size: 29, weight: .medium, design: .serif))
                        Text("只选择想保存的照片与视频，直接传到自己的电脑。").font(.subheadline).foregroundStyle(.secondary)
                    }.padding(.vertical, 12)
                    if let pair = host.pair {
                        GroupBox("已配对的家庭电脑") {
                            VStack(alignment: .leading, spacing: 9) {
                                Label(pair.url, systemImage: "desktopcomputer").font(.subheadline)
                                Text("证书指纹：" + String(pair.certificate_sha256.prefix(16)) + "…").font(.caption.monospaced())
                                if !busy { Button("更换电脑", role: .destructive) { host.disconnect(forget: true); results = [] } }
                            }.frame(maxWidth: .infinity, alignment: .leading)
                        }
                    } else { pairingView }
                    if host.pair != nil && !host.connected { loginView }
                    if host.connected { uploadView }
                    if !message.isEmpty { Text(message).font(.subheadline).foregroundStyle(.secondary).accessibilityIdentifier("status") }
                    GroupBox("素材留在你自己的设备之间") {
                        Text("照片只发送给已配对的家庭电脑。人物识别和回忆影片由电脑本地处理。App 不接入云端模型、广告或统计服务，不保存账号密码。请在上传时保持 App 前台打开。")
                            .font(.footnote).foregroundStyle(.secondary).frame(maxWidth: .infinity, alignment: .leading)
                    }
                }.padding(22)
            }
            .background(Color(red: 0.97, green: 0.96, blue: 0.93))
            .navigationTitle("记忆导演").navigationBarTitleDisplayMode(.inline).tint(green)
            .sheet(isPresented: $scanning) {
                NavigationStack {
                    QRScanner { text in scanning = false; acceptPair(text) }
                        .navigationTitle("扫描电脑配对码")
                        .toolbar { Button("取消") { scanning = false } }
                }
            }
            .onChange(of: selection) { _, items in
                guard !items.isEmpty && !busy else { return }
                Task { await upload(items) }
            }
        }
    }

    private var pairingView: some View {
        GroupBox("连接家里的电脑") {
            VStack(alignment: .leading, spacing: 13) {
                Text("电脑启动记忆导演后，点击“连接手机”，使用下方按钮扫码。").font(.subheadline)
                Button {
                    Task {
                        guard DataScannerViewController.isSupported else { message = "当前设备不支持扫码，请粘贴电脑上的配对信息。"; return }
                        let granted = await AVCaptureDevice.requestAccess(for: .video)
                        if granted && DataScannerViewController.isAvailable { scanning = true }
                        else { message = "相机暂不可用，可以在设置中允许相机，或粘贴电脑上的配对信息。" }
                    }
                } label: {
                    Label("扫描配对二维码", systemImage: "qrcode.viewfinder").frame(maxWidth: .infinity)
                }.buttonStyle(.borderedProminent)
                Text("也可以粘贴配对信息").font(.caption)
                TextEditor(text: $pairingText).frame(height: 90).font(.caption.monospaced()).scrollContentBackground(.hidden)
                    .background(.white).clipShape(RoundedRectangle(cornerRadius: 8))
                Button("确认配对") { acceptPair(pairingText) }.disabled(pairingText.isEmpty)
            }
        }
    }

    private var loginView: some View {
        GroupBox("登录家庭账号") {
            VStack(spacing: 13) {
                TextField("电脑上创建的名字", text: $loginName).textContentType(.username).textInputAutocapitalization(.never)
                SecureField("密码", text: $password).textContentType(.password)
                Button(busy ? "正在连接…" : "连接家庭电脑") {
                    busy = true; message = ""
                    Task {
                        do { try await host.login(name: loginName, password: password); password = "" }
                        catch { message = error.localizedDescription }
                        busy = false
                    }
                }.buttonStyle(.borderedProminent).disabled(busy || loginName.isEmpty || password.isEmpty)
            }.textFieldStyle(.roundedBorder).padding(.vertical, 7)
        }
    }

    private var uploadView: some View {
        GroupBox("你好，" + host.username) {
            VStack(alignment: .leading, spacing: 15) {
                PhotosPicker(selection: $selection, maxSelectionCount: 100, matching: .any(of: [.images, .videos]), preferredItemEncoding: .current) {
                    Label("选择照片与视频", systemImage: "photo.on.rectangle.angled").frame(maxWidth: .infinity)
                }.buttonStyle(.borderedProminent).disabled(busy)
                if busy {
                    Text(current).font(.subheadline)
                    ProgressView(value: host.progress)
                    Text("请保持 App 在前台，电脑不要休眠。").font(.caption).foregroundStyle(.secondary)
                }
                ForEach(Array(results.enumerated()), id: \.offset) { _, result in Text(result).font(.footnote) }
                if !busy { Button("退出登录") { host.disconnect(); results = [] } }
            }.padding(.vertical, 7)
        }
    }

    private func acceptPair(_ text: String) {
        do { try host.setPair(text); pairingText = ""; message = "已核对电脑证书。请使用已有家庭账号登录。" }
        catch { message = error.localizedDescription }
    }

    @MainActor private func upload(_ items: [PhotosPickerItem]) async {
        busy = true; results = []; message = ""
        defer { busy = false; selection = []; PickedMedia.cleanTemporaryFiles() }
        for (index, item) in items.enumerated() {
            current = "正在传送 \(index + 1) / \(items.count)"; host.progress = 0
            do {
                guard let media = try await item.loadTransferable(type: PickedMedia.self) else { throw ClientError.message("无法导出所选素材。") }
                defer { try? FileManager.default.removeItem(at: media.file) }
                let result = try await host.upload(media.file)
                results.append("\(index + 1). " + (result.ignored_live == true ? "已保留静态照片" : result.duplicate ? "已存在，跳过重复" : "已保存到家庭电脑"))
            } catch { results.append("\(index + 1). 上传未完成：" + error.localizedDescription) }
        }
        message = "本次传送已结束。未完成的项目可以重新选择上传，电脑会检查重复。"
    }
}
