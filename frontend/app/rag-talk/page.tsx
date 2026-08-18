import type { Metadata } from "next";
import { TalkDeck } from "./TalkDeck";

export const metadata: Metadata = {
  title: "A plain-English talk on RAG",
  description:
    "Twelve slides on Retrieval-Augmented Generation: why it exists, how the pipeline works, and what production systems add.",
};

export default function RagTalkPage() {
  return <TalkDeck />;
}
