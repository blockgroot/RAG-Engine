import { redirect } from "next/navigation";

/** Charts live in Ask now — a second tab made people guess which box to type in. */
export default function VisualizationsRedirect() {
  redirect("/chat");
}
