// Google's encoded polyline (5 decimals), as the engine's heatmap.encode writes it. Returns [lon, lat] pairs for maplibre.
export function decodePolyline(line: string): [number, number][] {
  const out: [number, number][] = [];
  let i = 0, lat = 0, lon = 0;
  while (i < line.length) {
    for (let k = 0; k < 2; k++) {
      let shift = 0, res = 0, b: number;
      do {
        b = line.charCodeAt(i++) - 63;
        res |= (b & 0x1f) << shift;
        shift += 5;
      } while (b >= 0x20);
      const v = res & 1 ? ~(res >> 1) : res >> 1;
      if (k === 0) lat += v;
      else lon += v;
    }
    out.push([lon / 1e5, lat / 1e5]);
  }
  return out;
}
