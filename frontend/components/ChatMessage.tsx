"use client";

import type { TurnMeta } from "@/lib/api";
import { ChatDonePayload } from "@/lib/sse";
import { AnswerFeedback } from "./AnswerFeedback";
import { AnswerText } from "./AnswerText";
import { Chart } from "./Chart";
import { ProvenanceStripe } from "./ProvenanceStripe";
import { RememberedNote } from "./RememberedNote";

export interface Message {
  role: "user" | "assistant";
  text: string;
  streaming?: boolean;
  done?: ChatDonePayload;
  /** Sources for an answer loaded from history. A live answer carries them on `done`. */
  cited?: ChatDonePayload["cited"];
  /** The chart for an answer loaded from history. A live answer carries it on `done`. */
  chart?: ChatDonePayload["chart"];
  chartPeriod?: string;
  /** Who answered, for an answer loaded from history. */
  meta?: TurnMeta;
}

/**
 * Chat bubble for Ask. Shows the answer plus a small provenance pill
 * (policy vs web vs GitHub) when the stream finishes, and — when the member
 * picked a model — which model actually answered.
 *
 * A chart-shaped question is answered from counted facts, not RAG: the SVG
 * is the measurement; the caption is only the registry title.
 */
export function ChatMessageView({
  message,
  conversationId,
  question,
  workspaceId,
  onEnableChart,
  onDisableChart,
}: {
  message: Message;
  /** The chat this answer belongs to. Feedback is stored against it, and the
   *  route checks it belongs to this person — so with no id there is nothing
   *  to rate and the thumbs are simply absent. */
  conversationId?: string | null;
  /** The question this answer replied to. Kept beside the answer so an admin
   *  reading a downvote is not holding half an exchange. */
  question?: string;
  workspaceId?: string | null;
  /** Turns on Chart mode, for the hint under an answer. Absent = no button. */
  onEnableChart?: () => void;
  /** Turns Chart mode off, for a text question asked in Chart mode. */
  onDisableChart?: () => void;
}) {
  if (message.role === "user") {
    return <div className="chat-bubble chat-bubble-user">{message.text}</div>;
  }

  const thinking = Boolean(message.streaming && !message.text.trim());
  const chart = message.done?.chart ?? message.chart;
  const points = chart?.points;

  return (
    <div className="chat-bubble chat-bubble-assistant" data-thinking={thinking || undefined}>
      {message.done ? (
        <ProvenanceStripe
          source={message.done.source}
          agent={message.done.agent}
          connected={message.done.connected_providers ?? undefined}
          attachments={message.done.attachments}
          citations={message.done.citations?.length}
          live={message.done.live_sources}
        />
      ) : message.meta ? (
        // A reopened answer: the same pill, from what the turn kept.
        <ProvenanceStripe
          source={message.meta.source}
          agent={message.meta.agent}
          connected={message.meta.connected_providers}
          attachments={message.meta.attachments}
          citations={message.meta.citation_count}
          live={message.meta.live_sources}
        />
      ) : null}
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
          <AnswerText text={message.text} cited={message.streaming ? undefined : message.done?.cited ?? message.cited} />
          {points && points.length > 0 && chart && (
            <div className="chat-chart">
              <Chart
                chart={chart.chart}
                points={points}
                period={message.done?.chart_period || message.chartPeriod || "month"}
                unit={chart.unit}
                groupBy={chart.group_by}
                splitBy={chart.split_by}
                blankLabels={chart.blank_labels ?? undefined}
                // The rows the bars are made of. They belong in the HOVER --
                // repeating a chart's contents underneath it makes the card a
                // table with a picture on top.
                details={chart.details}
              />
              {chart.explain && (
                // What each bar/point IS, in words, so a number is never
                // left for the reader to decode from its unit.
                <p className="viz-panel-explain">{chart.explain}</p>
              )}
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
          {(message.done?.model ?? message.meta?.model) && (
            <span className="chat-model-tag">
              Answered by {message.done?.model ?? message.meta?.model}
            </span>
          )}
          {message.done?.chart_hint && onEnableChart && (
            <button type="button" className="chat-chart-hint" onClick={onEnableChart}>
              <ChartIcon /> Turn on Chart
            </button>
          )}
          {message.done?.ask_hint && onDisableChart && (
            <button type="button" className="chat-chart-hint" onClick={onDisableChart}>
              Turn off Chart
            </button>
          )}
          {message.done?.remembered && message.done.remembered.length > 0 && (
            <RememberedNote facts={message.done.remembered} />
          )}
          {message.done && conversationId && question && (
            <AnswerFeedback
              conversationId={conversationId}
              question={question}
              done={message.done}
              workspaceId={workspaceId}
            />
          )}
        </>
      )}
    </div>
  );
}

export function ChartIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden>
      <path d="M4 20V10M10 20V4M16 20v-7M22 20H2" stroke="currentColor" strokeWidth="2"
        strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
