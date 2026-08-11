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
            display: "flex",
            alignItems: "stretch",
            gap: 8,
            width: 96,
            height: 76,
          }}
        >
          <div
            style={{
              flex: 1,
              borderRadius: "10px 4px 4px 10px",
              background: "rgba(255,252,247,0.95)",
            }}
          />
          <div
            style={{
              width: 10,
              borderRadius: 4,
              background: "rgba(255,252,247,0.45)",
            }}
          />
          <div
            style={{
              flex: 1,
              borderRadius: "4px 10px 10px 4px",
              background: "rgba(255,252,247,0.88)",
            }}
          />
        </div>
      </div>
    ),
    { ...size }
  );
}
