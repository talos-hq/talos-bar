//! `--screenshot`: the popover's pixels from `gfx::snapshot`, written as a PNG.

use std::io;
use std::path::Path;

pub fn write_png(path: &Path, width: u32, height: u32, rgba: &[u8]) -> io::Result<()> {
    let expected = u64::from(width) * u64::from(height) * 4;
    if width == 0 || height == 0 || rgba.len() as u64 != expected {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            format!("{width}x{height} image with {} bytes", rgba.len()),
        ));
    }
    let file = io::BufWriter::new(std::fs::File::create(path)?);
    let mut encoder = png::Encoder::new(file, width, height);
    encoder.set_color(png::ColorType::Rgba);
    encoder.set_depth(png::BitDepth::Eight);
    let mut writer = encoder.write_header().map_err(io::Error::other)?;
    writer.write_image_data(rgba).map_err(io::Error::other)?;
    writer.finish().map_err(io::Error::other)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn writes_a_decodable_png() {
        let dir = std::env::temp_dir().join(format!("talos-popover-shot-{}", std::process::id()));
        std::fs::create_dir_all(&dir).expect("dir");
        let path = dir.join("shot.png");
        let rgba = [10, 20, 30, 255, 40, 50, 60, 255];
        write_png(&path, 2, 1, &rgba).expect("write");
        let decoder = png::Decoder::new(std::fs::File::open(&path).expect("open"));
        let mut reader = decoder.read_info().expect("info");
        let mut buf = vec![0; reader.output_buffer_size()];
        let info = reader.next_frame(&mut buf).expect("frame");
        assert_eq!((info.width, info.height), (2, 1));
        assert_eq!(&buf[..8], &rgba);
    }

    #[test]
    fn a_short_buffer_is_refused() {
        let path = std::env::temp_dir().join("talos-popover-never.png");
        assert!(write_png(&path, 2, 2, &[0; 4]).is_err());
    }
}
