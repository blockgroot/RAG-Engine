import { ImageResponse } from "next/og";

export const size = { width: 32, height: 32 };
export const contentType = "image/png";

/** Browser-tab mark — open handbook on the teal tile. */
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
            display: "flex",
            alignItems: "stretch",
            gap: 1.5,
            width: 18,
            height: 14,
          }}
        >
          <div
            style={{
              flex: 1,
              borderRadius: "2px 1px 1px 2px",
              background: "rgba(255,252,247,0.95)",
            }}
          />
          <div
            style={{
              width: 2,
              borderRadius: 1,
              background: "rgba(255,252,247,0.45)",
            }}
          />
          <div
            style={{
              flex: 1,
              borderRadius: "1px 2px 2px 1px",
              background: "rgba(255,252,247,0.88)",
            }}
          />
        </div>
      </div>
    ),
    { ...size }
  );
}
