"use client";

import { ChatDonePayload } from "@/lib/sse";
import { AnswerText } from "./AnswerText";
import { Chart } from "./Chart";
import { ProvenanceStripe } from "./ProvenanceStripe";

export interface Message {
  role: "user" | "assistant";
  text: string;
  streaming?: boolean;
  done?: ChatDonePayload;
}

/**
 * Chat bubble for Ask. Shows the answer plus a small provenance pill
 * (policy vs web vs GitHub) when the stream finishes, and — when the member
 * picked a model — which model actually answered.
 *
 * A chart-shaped question is answered from counted facts, not RAG: the SVG
 * is the measurement; the caption is only the registry title.
 */
export function ChatMessageView({ message }: { message: Message }) {
  if (message.role === "user") {
    return <div className="chat-bubble chat-bubble-user">{message.text}</div>;
  }

  const thinking = Boolean(message.streaming && !message.text.trim());
  const chart = message.done?.chart;
  const points = chart?.points;

  return (
    <div className="chat-bubble chat-bubble-assistant" data-thinking={thinking || undefined}>
      {message.done && (
        <ProvenanceStripe source={message.done.source} agent={message.done.agent} />
      )}
      {thinking ? (
        <div className="chat-thinking" role="status" aria-live="polite">
          <span className="chat-thinking-dots" aria-hidden>
            <span />
            <span />
            <span />
          </span>
          <span className="chat-thinking-label">Finding a grounded answer…</span>
        </div>
      ) : (
        <>
          <AnswerText text={message.text} />
          {points && points.length > 0 && chart && (
            <div className="chat-chart">
              <Chart
                chart={chart.chart}
                points={points}
                period={message.done?.chart_period || "month"}
                unit={chart.unit}
                groupBy={chart.group_by}
              />
              {chart.caveat && (
                <p className="muted viz-panel-caveat">{chart.caveat}</p>
              )}
              {chart.measured_since && (
                <p className="muted viz-panel-since">
                  Measured since{" "}
                  {new Date(chart.measured_since).toLocaleDateString(undefined, {
                    day: "numeric",
                    month: "short",
                    year: "numeric",
                  })}
                </p>
              )}
            </div>
          )}
          {message.streaming && <span className="chat-stream-caret" aria-hidden />}
          {message.done?.model && (
            <span className="chat-model-tag">Answered by {message.done.model}</span>
          )}
        </>
      )}
    </div>
  );
}
