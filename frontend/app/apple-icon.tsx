import { ImageResponse } from "next/og";

export const size = { width: 180, height: 180 };
export const contentType = "image/png";

/** Home-screen / Apple touch icon — larger sibling of `app/icon.tsx`. */
export default function AppleIcon() {
  return new ImageResponse(
    (
      <div
        style={{
          width: "100%",
          height: "100%",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          borderRadius: 42,
          background: "linear-gradient(145deg, #2dd4bf 0%, #0f766e 50%, #115e59 100%)",
        }}
      >
        <div
          style={{
            width: 72,
            height: 72,
            borderTop: "10px solid rgba(255,252,247,0.88)",
            borderRight: "10px solid rgba(255,252,247,0.88)",
            borderBottom: "10px solid transparent",
            borderLeft: "10px solid transparent",
            borderRadius: 8,
            transform: "rotate(45deg) translateY(4px)",
          }}
        />
      </div>
    ),
    { ...size }
  );
}
