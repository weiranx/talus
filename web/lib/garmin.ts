// Pulls FIT files straight off a Garmin watch over Web Bluetooth (Chrome), the way the Garmin
// Connect phone app does, then hands each one to the engine's normal file import.
//
// Protocol notes are in tools/garmin-ble/NOTES.md: ML (multi-link) handles on the 6A4E2800
// service, GFDI frames (COBS + CRC) for the handshake, protobuf FileSync to list files and ask
// for one, and the file bytes over a separate MLR (reliable) channel as a zlib stream.
// Read only: nothing on the watch is marked synced, archived or deleted, its clock and settings
// are never touched, and an auth request ends the sync.

/* eslint-disable @typescript-eslint/no-explicit-any */

const ml = (n: number) => `6a4e${n.toString(16).padStart(4, "0")}-667b-11e3-949a-0800200c9a66`;
export const ML_SERVICE = ml(0x2800);
const GARMIN_COMPANY = 0x0087;
const CLIENT_ID = 2n;
const WRITE_CHUNK = 20; // safe for every ATT MTU; our writes are small

const MSG = {
  response: 5000, deviceInformation: 5024, protobufRequest: 5043, protobufResponse: 5044,
  configuration: 5050, currentTimeRequest: 5052, authNegotiation: 5101,
} as const;

// The kinds Talus reads: activities, plus the overnight files (monitor, sleep, HRV status, metrics).
export const ACTIVITY_KIND = "FIT_TYPE_4";
export const HEALTH_KINDS = ["FIT_TYPE_32", "FIT_TYPE_49", "FIT_TYPE_68", "FIT_TYPE_44"];
const GARMIN_EPOCH_S = 631065600;

// ---------- bytes ----------

class W {
  b: number[] = [];
  u8(v: number) { this.b.push(v & 0xff); return this; }
  u16(v: number) { return this.u8(v).u8(v >> 8); }
  u32(v: number) { return this.u16(v & 0xffff).u16((v >>> 16) & 0xffff); }
  u64(v: bigint) { for (let i = 0n; i < 8n; i++) this.u8(Number((v >> (8n * i)) & 0xffn)); return this; }
  str(s: string) { const e = new TextEncoder().encode(s).slice(0, 255); this.u8(e.length); this.b.push(...e); return this; }
  bytes(d: Uint8Array | number[]) { this.b.push(...d); return this; }
  get data() { return Uint8Array.from(this.b); }
}

class R {
  p = 0;
  constructor(public d: Uint8Array) {}
  get remaining() { return this.d.length - this.p; }
  u8() { return this.d[this.p++]; }
  u16() { const v = this.d[this.p] | (this.d[this.p + 1] << 8); this.p += 2; return v; }
  u32() { const v = (this.d[this.p] | (this.d[this.p + 1] << 8) | (this.d[this.p + 2] << 16)) + this.d[this.p + 3] * 0x1000000; this.p += 4; return v; }
  bytes(n: number) { const v = this.d.slice(this.p, this.p + n); this.p += n; return v; }
  str() { return new TextDecoder().decode(this.bytes(this.u8())); }
}

const CRC_TABLE = [0x0000, 0xcc01, 0xd801, 0x1400, 0xf001, 0x3c00, 0x2800, 0xe401,
  0xa001, 0x6c00, 0x7800, 0xb401, 0x5000, 0x9c01, 0x8801, 0x4400];

export function crc16(data: Uint8Array, seed = 0): number {
  let crc = seed;
  for (const b of data) {
    crc = ((crc >> 4) & 0x0fff) ^ CRC_TABLE[crc & 15] ^ CRC_TABLE[b & 15];
    crc = ((crc >> 4) & 0x0fff) ^ CRC_TABLE[crc & 15] ^ CRC_TABLE[(b >> 4) & 15];
  }
  return crc;
}

/** COBS with Garmin's framing: a zero before and after each frame. */
export function cobsEncode(data: Uint8Array): Uint8Array {
  const out = [0];
  let i = 0;
  let lastWasZero = false;
  while (i < data.length) {
    let start = i;
    while (i < data.length && data[i] !== 0) i++;
    lastWasZero = i < data.length;
    let size = i - start;
    while (size >= 0xfe) {
      out.push(0xff, ...data.slice(start, start + 0xfe));
      size -= 0xfe;
      start += 0xfe;
    }
    out.push(size + 1, ...data.slice(start, start + size));
    if (lastWasZero) i++;
  }
  if (lastWasZero) out.push(1);
  out.push(0);
  return Uint8Array.from(out);
}

export class CobsDecoder {
  private buf: number[] = [];
  feed(d: Uint8Array): Uint8Array[] {
    this.buf.push(...d);
    const out: Uint8Array[] = [];
    for (;;) {
      if (this.buf[0] !== 0) {
        const z = this.buf.indexOf(0);
        if (z < 0) { this.buf = []; return out; }
        this.buf = this.buf.slice(z);
        continue;
      }
      const end = this.buf.indexOf(0, 1);
      if (end < 0) return out;
      const frame = this.buf.slice(1, end);
      this.buf = this.buf.slice(end + 1);
      const dec: number[] = [];
      let p = 0;
      while (p < frame.length) {
        const code = frame[p++];
        const n = Math.min(code - 1, frame.length - p);
        dec.push(...frame.slice(p, p + n));
        p += n;
        if (code !== 0xff && p < frame.length) dec.push(0);
      }
      out.push(Uint8Array.from(dec));
    }
  }
}

/** [length u16][type u16][body][crc u16]; the length counts the crc. */
export function gfdi(type: number, body: (w: W) => void = () => {}): Uint8Array {
  const w = new W().u16(0).u16(type);
  body(w);
  const len = w.b.length + 2;
  w.b[0] = len & 0xff;
  w.b[1] = len >> 8;
  const c = crc16(Uint8Array.from(w.b));
  return w.u16(c).data;
}

const ack = (orig: number, status = 0) => gfdi(MSG.response, (w) => w.u16(orig).u8(status));

// ---------- protobuf (just the wire format) ----------

type PB = { f: number; v?: bigint; d?: Uint8Array };

export function pbDecode(b: Uint8Array): PB[] {
  const out: PB[] = [];
  let i = 0;
  const varint = (): bigint | null => {
    let v = 0n;
    let shift = 0n;
    while (i < b.length) {
      const x = b[i++];
      v |= BigInt(x & 0x7f) << shift;
      if (!(x & 0x80)) return v;
      shift += 7n;
    }
    return null;
  };
  while (i < b.length) {
    const key = varint();
    if (key === null) break;
    const f = Number(key >> 3n);
    const wt = Number(key & 7n);
    if (wt === 0) { const v = varint(); if (v === null) break; out.push({ f, v }); }
    else if (wt === 1) { let v = 0n; for (let k = 0; k < 8; k++) v |= BigInt(b[i + k]) << BigInt(8 * k); i += 8; out.push({ f, v }); }
    else if (wt === 2) { const n = varint(); if (n === null || i + Number(n) > b.length) break; out.push({ f, d: b.slice(i, i + Number(n)) }); i += Number(n); }
    else if (wt === 5) { let v = 0n; for (let k = 0; k < 4; k++) v |= BigInt(b[i + k]) << BigInt(8 * k); i += 4; out.push({ f, v }); }
    else break;
  }
  return out;
}

class PBW {
  b: number[] = [];
  varint(v: bigint | number) { let x = BigInt(v); while (x >= 0x80n) { this.b.push(Number(x & 0x7fn) | 0x80); x >>= 7n; } this.b.push(Number(x)); return this; }
  uint(f: number, v: bigint | number) { return this.varint(f << 3).varint(v); }
  bytes(f: number, d: Uint8Array | number[]) { this.varint((f << 3) | 2).varint(d.length); this.b.push(...d); return this; }
  get data() { return Uint8Array.from(this.b); }
}

const first = (fields: PB[], f: number) => fields.find((x) => x.f === f);
const fileSync = (inner: PBW) => new PBW().bytes(43, inner.data).data;

export type WatchFile = {
  raw: Uint8Array; // the File message as the watch sent it, echoed back to ask for it
  key: string;
  kind: string;
  size: number;
  time: Date | null;
};

const hex16 = (v: bigint) => v.toString(16).padStart(16, "0");

export function parseFile(d: Uint8Array, names: Map<number, string>): WatchFile {
  const f = pbDecode(d);
  const id = pbDecode(first(f, 1)?.d ?? new Uint8Array());
  const t = pbDecode(first(f, 2)?.d ?? new Uint8Array());
  const nameId = first(t, 3)?.v;
  const nameBytes = first(t, 2)?.d;
  // Names come once per page; later entries carry only the id.
  let kind = nameBytes ? new TextDecoder().decode(nameBytes) : undefined;
  if (kind && nameId !== undefined) names.set(Number(nameId), kind);
  if (!kind && nameId !== undefined) kind = names.get(Number(nameId));
  kind ??= `code${first(t, 1)?.v ?? "?"}`;
  const ts = Number(first(f, 7)?.v ?? 0n);
  return {
    raw: d, kind, size: Number(first(f, 3)?.v ?? 0n),
    key: `${kind}-${hex16(first(id, 1)?.v ?? 0n)}-${hex16(first(id, 2)?.v ?? 0n)}`,
    time: ts ? new Date((ts + GARMIN_EPOCH_S) * 1000) : null,
  };
}

/** The watch sends a complete zlib stream (2 KB window, Adler-32 trailer). */
export async function inflate(data: Uint8Array): Promise<Uint8Array> {
  const stream = new Blob([data as BlobPart]).stream().pipeThrough(new DecompressionStream("deflate"));
  return new Uint8Array(await new Response(stream).arrayBuffer());
}

export const isFit = (d: Uint8Array) => d.length > 12 && new TextDecoder().decode(d.slice(8, 12)) === ".FIT";

// ---------- the session ----------

type Waiter<T> = { resolve: (v: T) => void; reject: (e: Error) => void };

export type Progress = { phase: string; done?: number; total?: number; bytes?: number };

/** One Bluetooth session with the watch: connect, handshake, list, then fetch files one by one. */
export class WatchSession {
  private send!: any;
  private recv!: any;
  private device: any;
  private decoder = new CobsDecoder();
  private gfdiHandle: number | null = null;
  private writes: Promise<void> = Promise.resolve();
  private requestId = 0;
  private chunks = new Map<number, number[]>();
  private names = new Map<number, string>();
  private listed: WatchFile[] = [];
  private ready?: Waiter<{ model: string; firmware: string }>;
  private listing?: Waiter<WatchFile[]>;
  private fileHandle?: Waiter<number>;
  private transfer?: { handle: number | null; fileHandle: number; started: boolean; buf: number[]; nextRcv: number;
    lastAck: number; acked: boolean; waiter: Waiter<Uint8Array>; timer?: ReturnType<typeof setTimeout> };
  private seenHandles = new Set<number>();
  private failure: Error | null = null;
  info = { model: "", firmware: "" };

  constructor(private onProgress: (p: Progress) => void = () => {}) {}

  static supported(): boolean {
    return typeof navigator !== "undefined" && "bluetooth" in navigator;
  }

  async connect(): Promise<void> {
    const bt = (navigator as any).bluetooth;
    this.onProgress({ phase: "Choose your watch in the browser's list" });
    this.device = await bt.requestDevice({
      filters: [{ manufacturerData: [{ companyIdentifier: GARMIN_COMPANY }] }, { services: [ML_SERVICE] }],
      optionalServices: [ML_SERVICE],
    });
    this.device.addEventListener("gattserverdisconnected", () => this.fail(new Error("The watch disconnected.")));
    this.onProgress({ phase: "Connecting" });
    const server = await this.device.gatt.connect();
    const svc = await server.getPrimaryService(ML_SERVICE);
    this.recv = await svc.getCharacteristic(ml(0x2810));
    this.send = await svc.getCharacteristic(ml(0x2820));
    this.recv.addEventListener("characteristicvaluechanged", (e: any) => {
      try { this.onPacket(new Uint8Array(e.target.value.buffer.slice(e.target.value.byteOffset, e.target.value.byteOffset + e.target.value.byteLength))); }
      catch (err) { this.fail(err as Error); }
    });
    await this.recv.startNotifications();
    const handshake = new Promise<{ model: string; firmware: string }>((resolve, reject) => { this.ready = { resolve, reject }; });
    this.write(new W().u8(0).u8(5).u64(CLIENT_ID).u16(0).data); // close our client's old handles
    this.info = await withTimeout(handshake, 20000, "The watch didn't answer the handshake.");
    await sleep(1000);
  }

  disconnect() {
    try { this.device?.gatt?.disconnect(); } catch { /* already gone */ }
  }

  async list(): Promise<WatchFile[]> {
    this.onProgress({ phase: "Listing the files on the watch" });
    this.listed = [];
    const p = new Promise<WatchFile[]>((resolve, reject) => { this.listing = { resolve, reject }; });
    this.requestList();
    return withTimeout(p, 60000, "The watch didn't send its file list.");
  }

  /** Download one file and return its inflated FIT bytes. */
  async fetch(file: WatchFile, onBytes: (n: number) => void = () => {}): Promise<Uint8Array> {
    const handle = new Promise<number>((resolve, reject) => { this.fileHandle = { resolve, reject }; });
    this.sendProtobuf(fileSync(new PBW().bytes(1, new PBW().bytes(1, file.raw).uint(2, 24).uint(3, 0).uint(4, 0).uint(5, 15).data)));
    const fh = await withTimeout(handle, 20000, "The watch didn't offer the file.");
    this.seenHandles.add(fh);
    const done = new Promise<Uint8Array>((resolve, reject) => {
      this.transfer = { handle: null, fileHandle: fh, started: false, buf: [], nextRcv: 0, lastAck: 0, acked: false, waiter: { resolve, reject } };
    });
    this.write(new W().u8(0).u8(0).u64(CLIENT_ID).u16(0x2018).u8(2).data); // register the file channel, reliable (MLR)
    const progress = setInterval(() => onBytes(this.transfer?.buf.length ?? 0), 500);
    try {
      // About 7 KB/s compressed: allow a minute plus a second per 4 KB of file.
      const raw = await withTimeout(done, 60000 + file.size / 4, "The file transfer stalled.");
      const fit = await inflate(raw);
      if (!isFit(fit)) throw new Error("The watch sent something that isn't a FIT file.");
      return fit;
    } finally {
      clearInterval(progress);
    }
  }

  // ----- plumbing -----

  private fail(e: Error) {
    if (this.failure) return;
    this.failure = e;
    for (const w of [this.ready, this.listing, this.fileHandle, this.transfer?.waiter]) w?.reject(e);
  }

  private write(d: Uint8Array) {
    // One write in flight at a time, in order.
    this.writes = this.writes.then(() => this.send.writeValueWithResponse(d)).catch((e: Error) => this.fail(e));
  }

  private sendGfdi(msg: Uint8Array) {
    if (this.gfdiHandle === null) return;
    const enc = cobsEncode(msg);
    for (let i = 0; i < enc.length; i += WRITE_CHUNK - 1) {
      this.write(Uint8Array.from([this.gfdiHandle, ...enc.slice(i, i + WRITE_CHUNK - 1)]));
    }
  }

  private sendProtobuf(smart: Uint8Array, type: number = MSG.protobufRequest, id?: number) {
    const rid = id ?? (this.requestId = (this.requestId + 1) % 65536);
    this.sendGfdi(gfdi(type, (w) => w.u16(rid).u32(0).u32(smart.length).u32(smart.length).bytes(smart)));
  }

  private requestList(cursor?: bigint) {
    const req = new PBW();
    if (cursor !== undefined) req.uint(1, cursor);
    req.uint(7, 0);
    this.sendProtobuf(fileSync(new PBW().bytes(9, req.data)));
  }

  private onPacket(v: Uint8Array) {
    if (!v.length) return;
    const h = v[0];
    if (h & 0x80) {
      const t = this.transfer;
      if (t?.handle != null && ((h & 0x70) >> 4) === (t.handle & 7)) this.mlrReceive(v);
      return;
    }
    if (h === 0) return this.onManagement(new R(v.slice(1)));
    if (h === this.gfdiHandle) for (const frame of this.decoder.feed(v.slice(1))) this.onGfdi(frame);
  }

  private onManagement(r: R) {
    const type = r.u8();
    const cid = BigInt(r.u32()) | (BigInt(r.u32()) << 32n);
    if (cid !== CLIENT_ID) return;
    if (type === 6) { // close-all done: register GFDI (service 1), plain
      this.write(new W().u8(0).u8(0).u64(CLIENT_ID).u16(1).u8(0).data);
    } else if (type === 1) {
      const svc = r.u16();
      const status = r.u8();
      const handle = r.u8();
      if (status !== 0) return this.fail(new Error(`The watch refused service ${svc} (status ${status}).`));
      if (svc === 1) this.gfdiHandle = handle;
      if (svc === 0x2018 && this.transfer) this.transferOpened(handle);
    } else if (type === 3) {
      const svc = r.u16();
      if (svc === 0x2018) this.transferClosed();
    }
  }

  private onGfdi(frame: Uint8Array) {
    if (frame.length < 6) return;
    const r = new R(frame);
    const len = r.u16();
    if (len !== frame.length) return;
    if (crc16(frame.slice(0, len - 2)) !== (frame[len - 2] | (frame[len - 1] << 8))) return;
    let type = r.u16();
    if (type & 0x8000) type = (type & 0xff) + 5000;
    const body = new R(frame.slice(4, len - 2));
    switch (type) {
      case MSG.deviceInformation: return this.onDeviceInfo(body);
      case MSG.configuration: return this.onConfiguration(body);
      case MSG.protobufRequest: case MSG.protobufResponse: return this.onProtobuf(type, body);
      case MSG.authNegotiation: return this.fail(new Error("The watch asked to authenticate; Talus stops here."));
      case MSG.currentTimeRequest: return this.sendGfdi(ack(type, 2)); // unsupported: never set the clock
      case MSG.response: return;
      default: return this.sendGfdi(ack(type));
    }
  }

  private onDeviceInfo(r: R) {
    const proto = r.u16();
    r.u16(); r.u32(); // product, unit id
    const sw = r.u16();
    r.u16(); // max packet
    r.str(); r.str();
    const model = r.str();
    this.info = { model, firmware: `${Math.floor(sw / 100)}.${String(sw % 100).padStart(2, "0")}` };
    this.sendGfdi(gfdi(MSG.response, (w) => w.u16(MSG.deviceInformation).u8(0)
      .u16(150).u16(0xffff).u32(0xffffffff).u16(7791).u16(0xffff)
      .str("Talus").str("Talus").str("Web").u8(Math.floor(proto / 100) === 1 ? 1 : 0)));
  }

  private onConfiguration(r: R) {
    const n = r.u8();
    this.sendGfdi(ack(MSG.configuration));
    // Ours: fit link and host-initiated sync only.
    const ours = new Array(n).fill(0);
    if (n) ours[0] = 0b0011_1001;
    this.sendGfdi(gfdi(MSG.configuration, (w) => w.u8(n).bytes(ours)));
    this.ready?.resolve(this.info);
  }

  private onProtobuf(type: number, r: R) {
    const id = r.u16();
    const offset = r.u32();
    const total = r.u32();
    const len = r.u32();
    const part = r.bytes(Math.min(len, r.remaining));
    const buf = offset === 0 ? [] : this.chunks.get(id) ?? [];
    buf.push(...part);
    if (buf.length < total) {
      this.chunks.set(id, buf);
      this.sendGfdi(gfdi(MSG.response, (w) => w.u16(type).u8(0).u16(id).u32(offset).u8(0).u8(0)));
      return;
    }
    this.chunks.delete(id);
    this.sendGfdi(ack(type));
    const svc = first(pbDecode(Uint8Array.from(buf)), 43);
    if (svc?.d) this.onFileSync(pbDecode(svc.d), id);
  }

  private onFileSync(f: PB[], requestId: number) {
    const list = first(f, 10);
    const response = first(f, 2);
    if (list?.d) {
      const l = pbDecode(list.d);
      const entries = l.filter((x) => x.f === 4 && x.d).map((x) => parseFile(x.d!, this.names));
      this.listed.push(...entries);
      const cursor = first(l, 2)?.v;
      this.onProgress({ phase: `Listing the files on the watch (${this.listed.length})` });
      if (cursor !== undefined && entries.length) return this.requestList(cursor);
      this.listing?.resolve(this.listed);
    } else if (response?.d) {
      const x = pbDecode(response.d);
      const status = Number(first(x, 1)?.v ?? 99n);
      const handle = Number(first(x, 3)?.v ?? 0n);
      // The watch repeats each offer every 5 s; reopening would restart the transfer empty.
      if (this.seenHandles.has(handle)) return;
      if (status !== 0) this.fileHandle?.reject(new Error(`The watch couldn't send the file (status ${status}).`));
      else this.fileHandle?.resolve(handle);
    } else if (first(f, 5)) {
      // Transfer status request after a file: answer as the phone app does.
      this.sendProtobuf(fileSync(new PBW().bytes(21, new PBW().uint(1, 1).data)), MSG.protobufResponse, requestId);
    }
  }

  // ----- MLR file channel -----

  private mlrPacket(req: number, seq: number, data: Uint8Array | number[] = []) {
    const h = (this.transfer?.handle ?? 0) & 7;
    return Uint8Array.from([0x80 | (h << 4) | ((req >> 2) & 0x0f), ((req & 3) << 6) | (seq & 0x3f), ...data]);
  }

  private transferOpened(handle: number) {
    const t = this.transfer!;
    t.handle = handle;
    const body = new W().u8(0).u8(0).u16(t.fileHandle).u8(0).u8(0).data;
    const attempt = (n: number) => {
      if (this.transfer !== t || t.acked) return;
      this.write(this.mlrPacket(t.nextRcv, 0, body));
      if (n < 6) setTimeout(() => attempt(n + 1), n * 1000);
    };
    attempt(1);
  }

  private mlrReceive(p: Uint8Array) {
    const t = this.transfer!;
    if (p.length < 2) return;
    const reqNum = ((p[0] & 0x0f) << 2) | ((p[1] >> 6) & 3);
    const seq = p[1] & 0x3f;
    if (reqNum !== 0) t.acked = true;
    if (p.length === 2) return;
    if (seq !== t.nextRcv) return this.mlrAck(); // out of order: ask again from what we have
    t.nextRcv = (t.nextRcv + 1) % 64;
    const data = p.slice(2);
    if (!t.started) {
      t.started = true;
      if (data.length >= 3 && data[0] === 0 && data[1] === 0 && data[2] === 0) return this.maybeAck(); // start header
    }
    t.buf.push(...data);
    this.maybeAck();
  }

  private maybeAck() {
    const t = this.transfer!;
    if ((t.nextRcv - t.lastAck + 64) % 64 >= 5) return this.mlrAck();
    const expect = t.nextRcv;
    clearTimeout(t.timer);
    t.timer = setTimeout(() => { if (this.transfer === t && t.nextRcv === expect && t.lastAck !== expect) this.mlrAck(); }, 250);
  }

  private mlrAck() {
    const t = this.transfer;
    if (!t || t.handle === null) return;
    this.write(this.mlrPacket(t.nextRcv, 0));
    t.lastAck = t.nextRcv;
  }

  private transferClosed() {
    const t = this.transfer;
    if (!t) return;
    clearTimeout(t.timer);
    this.transfer = undefined;
    if (!t.buf.length) t.waiter.reject(new Error("The watch closed the transfer without sending the file."));
    else t.waiter.resolve(Uint8Array.from(t.buf));
  }
}

function sleep(ms: number) { return new Promise((r) => setTimeout(r, ms)); }

function withTimeout<T>(p: Promise<T>, ms: number, message: string): Promise<T> {
  return Promise.race([p, new Promise<T>((_, reject) => setTimeout(() => reject(new Error(message)), ms))]);
}

/** Which files to pull: overnight files not imported yet, and activities not imported yet that
 * are newer than `activitiesSince` (the first sync doesn't spend half an hour on old runs). */
export function pick(files: WatchFile[], imported: Set<string>, activitiesSince: Date): WatchFile[] {
  const fresh = files.filter((f) => f.size > 0 && !imported.has(f.key));
  const health = fresh.filter((f) => HEALTH_KINDS.includes(f.kind));
  const acts = fresh.filter((f) => f.kind === ACTIVITY_KIND && f.time && f.time >= activitiesSince);
  return [...health, ...acts].sort((a, b) => (a.time?.getTime() ?? 0) - (b.time?.getTime() ?? 0));
}
