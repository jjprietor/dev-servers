// Run: xcrun swift scripts/render-icon.swift /path/to/output.iconset
import AppKit

func color(_ hex: UInt32) -> NSColor {
    NSColor(srgbRed: CGFloat((hex >> 16) & 255) / 255,
            green: CGFloat((hex >> 8) & 255) / 255,
            blue: CGFloat(hex & 255) / 255, alpha: 1)
}

func rounded(_ rect: NSRect, _ radius: CGFloat, _ fill: NSColor) {
    fill.setFill()
    NSBezierPath(roundedRect: rect, xRadius: radius, yRadius: radius).fill()
}

func render(size: Int) -> Data {
    let bitmap = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: size, pixelsHigh: size,
                                 bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true,
                                 isPlanar: false, colorSpaceName: .deviceRGB,
                                 bytesPerRow: 0, bitsPerPixel: 0)!
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: bitmap)
    let transform = AffineTransform(scale: CGFloat(size) / 1024)
    (transform as NSAffineTransform).concat()

    let tile = NSBezierPath(roundedRect: NSRect(x: 78, y: 78, width: 868, height: 868),
                            xRadius: 196, yRadius: 196)
    NSGraphicsContext.saveGraphicsState()
    let shadow = NSShadow()
    shadow.shadowColor = NSColor.black.withAlphaComponent(0.24)
    shadow.shadowBlurRadius = 28
    shadow.shadowOffset = NSSize(width: 0, height: -12)
    shadow.set()
    color(0x142344).setFill()
    tile.fill()
    NSGraphicsContext.restoreGraphicsState()
    NSGradient(starting: color(0x294B83), ending: color(0x101C35))!.draw(in: tile, angle: -90)
    color(0xFFFFFF).withAlphaComponent(0.12).setStroke()
    tile.lineWidth = 3
    tile.stroke()

    // Two server trays, with a single status light on each.
    for y: CGFloat in [532, 304] {
        NSGraphicsContext.saveGraphicsState()
        let shadow = NSShadow()
        shadow.shadowColor = NSColor.black.withAlphaComponent(0.22)
        shadow.shadowBlurRadius = 16
        shadow.shadowOffset = NSSize(width: 0, height: -8)
        shadow.set()
        rounded(NSRect(x: 238, y: y, width: 548, height: 178), 44, color(0xE5EFFD))
        NSGraphicsContext.restoreGraphicsState()
        rounded(NSRect(x: 282, y: y + 56, width: 258, height: 66), 17, color(0x192D50))
        for x: CGFloat in [307, 340, 373] {
            rounded(NSRect(x: x, y: y + 73, width: 12, height: 32), 6, color(0x718AAF))
        }
        color(0x18A98C).setFill()
        NSBezierPath(ovalIn: NSRect(x: 662, y: y + 66, width: 46, height: 46)).fill()
        color(0x91F1D5).setFill()
        NSBezierPath(ovalIn: NSRect(x: 672, y: y + 88, width: 11, height: 11)).fill()
    }
    NSGraphicsContext.restoreGraphicsState()
    return bitmap.representation(using: .png, properties: [:])!
}

let output = URL(fileURLWithPath: CommandLine.arguments[1])
try FileManager.default.createDirectory(at: output, withIntermediateDirectories: true)
for size in [16, 32, 128, 256, 512] {
    for scale in [1, 2] {
        let suffix = scale == 2 ? "@2x" : ""
        try render(size: size * scale).write(to: output.appendingPathComponent("icon_\(size)x\(size)\(suffix).png"))
    }
}
