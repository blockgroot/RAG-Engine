import { redirect } from "next/navigation";

/** Merged into the home page; old links land on its "How it works" section. */
export default function HowItWorksPage() {
  redirect("/#how");
}
