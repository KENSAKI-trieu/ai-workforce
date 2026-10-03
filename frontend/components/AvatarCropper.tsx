"use client";

import {
  Contrast,
  Crop,
  Palette,
  RotateCcw,
  RotateCw,
  SlidersHorizontal,
  Sun,
  X,
} from "lucide-react";
import {
  PointerEvent as ReactPointerEvent,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";

const OUTPUT_SIZE = 512;

interface AvatarCropperProps {
  file: File;
  busy?: boolean;
  onCancel: () => void;
  onApply: (blob: Blob) => Promise<void> | void;
}

interface Position {
  x: number;
  y: number;
}

function clamp(value: number, min: number, max: number) {
  return Math.min(max, Math.max(min, value));
}

function getImageMetrics(image: HTMLImageElement, rotation: number, zoom: number) {
  const isQuarterTurn = Math.abs(rotation % 180) === 90;
  const rotatedWidth = isQuarterTurn ? image.height : image.width;
  const rotatedHeight = isQuarterTurn ? image.width : image.height;
  const scale = Math.max(OUTPUT_SIZE / rotatedWidth, OUTPUT_SIZE / rotatedHeight) * zoom;

  return {
    scale,
    maxX: Math.max(0, (rotatedWidth * scale - OUTPUT_SIZE) / 2),
    maxY: Math.max(0, (rotatedHeight * scale - OUTPUT_SIZE) / 2),
  };
}

export default function AvatarCropper({ file, busy = false, onCancel, onApply }: AvatarCropperProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const dragRef = useRef<{ pointerId: number; startX: number; startY: number; origin: Position } | null>(null);
  const [image, setImage] = useState<HTMLImageElement | null>(null);
  const [position, setPosition] = useState<Position>({ x: 0, y: 0 });
  const [zoom, setZoom] = useState(1);
  const [rotation, setRotation] = useState(0);
  const [brightness, setBrightness] = useState(100);
  const [contrast, setContrast] = useState(100);
  const [saturation, setSaturation] = useState(100);
  const [processing, setProcessing] = useState(false);
  const [loadError, setLoadError] = useState("");

  useEffect(() => {
    let disposed = false;
    const objectUrl = URL.createObjectURL(file);
    const source = new window.Image();

    source.onload = () => {
      if (disposed) return;
      setImage(source);
      setLoadError("");
    };
    source.onerror = () => {
      if (!disposed) setLoadError("Không thể đọc ảnh đã chọn. Vui lòng thử ảnh khác.");
    };
    source.src = objectUrl;

    return () => {
      disposed = true;
      URL.revokeObjectURL(objectUrl);
    };
  }, [file]);

  useEffect(() => {
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape" && !busy && !processing) onCancel();
    }
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [busy, onCancel, processing]);

  const drawImage = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas || !image) return;

    const context = canvas.getContext("2d");
    if (!context) return;

    const { scale, maxX, maxY } = getImageMetrics(image, rotation, zoom);
    const x = clamp(position.x, -maxX, maxX);
    const y = clamp(position.y, -maxY, maxY);

    context.save();
    context.clearRect(0, 0, OUTPUT_SIZE, OUTPUT_SIZE);
    context.fillStyle = "#E2E8F0";
    context.fillRect(0, 0, OUTPUT_SIZE, OUTPUT_SIZE);
    context.filter = `brightness(${brightness}%) contrast(${contrast}%) saturate(${saturation}%)`;
    context.translate(OUTPUT_SIZE / 2 + x, OUTPUT_SIZE / 2 + y);
    context.rotate((rotation * Math.PI) / 180);
    context.scale(scale, scale);
    context.drawImage(image, -image.width / 2, -image.height / 2);
    context.restore();
  }, [brightness, contrast, image, position.x, position.y, rotation, saturation, zoom]);

  useEffect(() => {
    const frame = requestAnimationFrame(drawImage);
    return () => cancelAnimationFrame(frame);
  }, [drawImage]);

  function beginDrag(event: ReactPointerEvent<HTMLCanvasElement>) {
    if (!image || busy || processing) return;
    event.currentTarget.setPointerCapture(event.pointerId);
    dragRef.current = {
      pointerId: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      origin: position,
    };
  }

  function moveImage(event: ReactPointerEvent<HTMLCanvasElement>) {
    const drag = dragRef.current;
    const canvas = canvasRef.current;
    if (!drag || !canvas || !image || drag.pointerId !== event.pointerId) return;

    const displayScale = OUTPUT_SIZE / canvas.getBoundingClientRect().width;
    const { maxX, maxY } = getImageMetrics(image, rotation, zoom);
    setPosition({
      x: clamp(drag.origin.x + (event.clientX - drag.startX) * displayScale, -maxX, maxX),
      y: clamp(drag.origin.y + (event.clientY - drag.startY) * displayScale, -maxY, maxY),
    });
  }

  function endDrag(event: ReactPointerEvent<HTMLCanvasElement>) {
    if (dragRef.current?.pointerId !== event.pointerId) return;
    dragRef.current = null;
    event.currentTarget.releasePointerCapture(event.pointerId);
  }

  function rotate(delta: number) {
    setRotation((current) => (current + delta + 360) % 360);
    setPosition({ x: 0, y: 0 });
  }

  function reset() {
    setPosition({ x: 0, y: 0 });
    setZoom(1);
    setRotation(0);
    setBrightness(100);
    setContrast(100);
    setSaturation(100);
  }

  async function applyCrop() {
    const canvas = canvasRef.current;
    if (!canvas || !image || busy || processing) return;

    setProcessing(true);
    setLoadError("");
    drawImage();

    try {
      const blob = await new Promise<Blob>((resolve, reject) => {
        canvas.toBlob(
          (result) => result ? resolve(result) : reject(new Error("Không thể xuất ảnh.")),
          "image/webp",
          0.9,
        );
      });
      await onApply(blob);
    } catch {
      setLoadError("Không thể xử lý ảnh. Vui lòng thử lại.");
    } finally {
      setProcessing(false);
    }
  }

  const disabled = busy || processing;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="avatar-crop-title"
      style={{
        position: "fixed",
        inset: 0,
        zIndex: 300,
        display: "grid",
        placeItems: "center",
        padding: 18,
        background: "rgba(15, 23, 42, .68)",
        backdropFilter: "blur(5px)",
      }}
    >
      <section className="ta-card" style={{ width: "min(820px, 100%)", maxHeight: "calc(100vh - 36px)", overflowY: "auto", padding: 0 }}>
        <header style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12, padding: "17px 20px", borderBottom: "1px solid var(--border)" }}>
          <div>
            <h2 id="avatar-crop-title" style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 17, fontWeight: 800 }}><Crop size={19} color="#3C50E0"/> Cắt và chỉnh ảnh</h2>
            <p style={{ marginTop: 4, color: "var(--text-muted)", fontSize: 12 }}>Kéo ảnh để chọn vùng hiển thị. Ảnh chỉ được tải lên sau khi bạn xác nhận.</p>
          </div>
          <button type="button" aria-label="Đóng" onClick={onCancel} disabled={disabled} style={{ width: 34, height: 34, display: "grid", placeItems: "center", border: 0, borderRadius: 9, background: "#F1F5F9", color: "#64748B", cursor: disabled ? "not-allowed" : "pointer" }}><X size={18}/></button>
        </header>

        <div className="stack-mobile" style={{ display: "grid", gridTemplateColumns: "minmax(280px, 1fr) minmax(250px, .8fr)", gap: 22, padding: 20 }}>
          <div>
            <div style={{ position: "relative", width: "min(100%, 420px)", margin: "0 auto", overflow: "hidden", borderRadius: 16, background: "#E2E8F0", boxShadow: "inset 0 0 0 1px rgba(15,23,42,.1)" }}>
              <canvas
                ref={canvasRef}
                width={OUTPUT_SIZE}
                height={OUTPUT_SIZE}
                onPointerDown={beginDrag}
                onPointerMove={moveImage}
                onPointerUp={endDrag}
                onPointerCancel={endDrag}
                style={{ display: "block", width: "100%", aspectRatio: "1", cursor: disabled ? "wait" : "grab", touchAction: "none" }}
              />
              <div aria-hidden="true" style={{ position: "absolute", inset: "7%", border: "2px solid rgba(255,255,255,.9)", borderRadius: "50%", boxShadow: "0 0 0 999px rgba(15,23,42,.30)", pointerEvents: "none" }}/>
              <div aria-hidden="true" style={{ position: "absolute", inset: 0, pointerEvents: "none", background: "linear-gradient(90deg, transparent 33.1%, rgba(255,255,255,.3) 33.3%, transparent 33.5%, transparent 66.4%, rgba(255,255,255,.3) 66.6%, transparent 66.8%), linear-gradient(transparent 33.1%, rgba(255,255,255,.3) 33.3%, transparent 33.5%, transparent 66.4%, rgba(255,255,255,.3) 66.6%, transparent 66.8%)" }}/>
            </div>
            <p style={{ marginTop: 9, color: "#94A3B8", fontSize: 11, textAlign: "center" }}>Khung tròn là vùng hiển thị ảnh đại diện. File xuất ra có kích thước 512 × 512 px.</p>
          </div>

          <div style={{ display: "grid", alignContent: "start", gap: 16 }}>
            <Control icon={<SlidersHorizontal size={15}/>} label="Thu phóng" value={`${zoom.toFixed(1)}×`}>
              <input type="range" min="1" max="3" step="0.05" value={zoom} disabled={disabled || !image} onChange={(event) => setZoom(Number(event.target.value))} style={{ width: "100%", accentColor: "#3C50E0" }}/>
            </Control>
            <Control icon={<Sun size={15}/>} label="Độ sáng" value={`${brightness}%`}>
              <input type="range" min="70" max="130" value={brightness} disabled={disabled || !image} onChange={(event) => setBrightness(Number(event.target.value))} style={{ width: "100%", accentColor: "#3C50E0" }}/>
            </Control>
            <Control icon={<Contrast size={15}/>} label="Tương phản" value={`${contrast}%`}>
              <input type="range" min="70" max="130" value={contrast} disabled={disabled || !image} onChange={(event) => setContrast(Number(event.target.value))} style={{ width: "100%", accentColor: "#3C50E0" }}/>
            </Control>
            <Control icon={<Palette size={15}/>} label="Độ bão hòa" value={`${saturation}%`}>
              <input type="range" min="0" max="160" value={saturation} disabled={disabled || !image} onChange={(event) => setSaturation(Number(event.target.value))} style={{ width: "100%", accentColor: "#3C50E0" }}/>
            </Control>

            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
              <button type="button" className="ta-btn" onClick={() => rotate(-90)} disabled={disabled || !image}><RotateCcw size={15}/> Xoay trái</button>
              <button type="button" className="ta-btn" onClick={() => rotate(90)} disabled={disabled || !image}><RotateCw size={15}/> Xoay phải</button>
            </div>
            <button type="button" className="ta-btn" onClick={reset} disabled={disabled || !image}>Đặt lại bộ lọc</button>
            {loadError && <p role="alert" style={{ padding: 10, borderRadius: 8, background: "#FEF2F2", color: "#B91C1C", fontSize: 12 }}>{loadError}</p>}
          </div>
        </div>

        <footer style={{ display: "flex", justifyContent: "flex-end", gap: 9, padding: "15px 20px", borderTop: "1px solid var(--border)" }}>
          <button type="button" className="ta-btn" onClick={onCancel} disabled={disabled}>Hủy</button>
          <button type="button" className="ta-btn ta-btn-primary" onClick={() => void applyCrop()} disabled={disabled || !image || Boolean(loadError)}>
            <Crop size={15}/> {disabled ? "Đang xử lý..." : "Cắt & sử dụng ảnh"}
          </button>
        </footer>
      </section>
    </div>
  );
}

function Control({ icon, label, value, children }: { icon: React.ReactNode; label: string; value: string; children: React.ReactNode }) {
  return (
    <label style={{ display: "grid", gap: 7 }}>
      <span style={{ display: "flex", justifyContent: "space-between", gap: 8, color: "#475569", fontSize: 12, fontWeight: 700 }}>
        <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>{icon}{label}</span>
        <span style={{ color: "#3C50E0" }}>{value}</span>
      </span>
      {children}
    </label>
  );
}
