import { ImageResponse } from "next/og";

export const size = { width: 32, height: 32 };
export const contentType = "image/png";

/** Browser-tab mark — same teal tile as the in-app `.brand-mark`. */
export default function Icon() {
  return new ImageResponse(
    (
      <div
        style={{
          width: "100%",
          height: "100%",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          borderRadius: 9,
          background: "linear-gradient(145deg, #2dd4bf 0%, #0f766e 50%, #115e59 100%)",
        }}
      >
        <div
          style={{
            width: 14,
            height: 14,
            borderTop: "2.2px solid rgba(255,252,247,0.85)",
            borderRight: "2.2px solid rgba(255,252,247,0.85)",
            borderBottom: "2.2px solid transparent",
            borderLeft: "2.2px solid transparent",
            borderRadius: 2,
            transform: "rotate(45deg) translateY(1px)",
          }}
        />
      </div>
    ),
    { ...size }
  );
}
