const EMAIL = "hbook1809@gmail.com";
const SUBJECT = encodeURIComponent("Question about Handbook");

/** "Contact us": a new message to us in the visitor's default mail app. */
export function ContactLink({ label = "Contact us" }: { label?: string }) {
  return (
    <a className="contact-link" href={`mailto:${EMAIL}?subject=${SUBJECT}`} title={EMAIL}>
      {label}
    </a>
  );
}
