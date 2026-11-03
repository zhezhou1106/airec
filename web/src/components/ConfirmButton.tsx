import { useEffect, useState, type ReactNode } from "react";
import { Button } from "../hub/ui";

/** First click arms it, second click acts. No browser dialogs. */
export function ConfirmButton({ onConfirm, children, confirmLabel = "Sure?", variant = "danger", size = "md", disabled }: {
  onConfirm: () => void; children: ReactNode; confirmLabel?: string; variant?: "danger" | "secondary" | "ghost"; size?: "sm" | "md"; disabled?: boolean;
}) {
  const [armed, setArmed] = useState(false);
  useEffect(() => {
    if (!armed) return;
    const t = setTimeout(() => setArmed(false), 4000);
    return () => clearTimeout(t);
  }, [armed]);
  return (
    <Button variant={armed ? "danger" : variant} size={size} disabled={disabled} onClick={() => (armed ? (setArmed(false), onConfirm()) : setArmed(true))}>
      {armed ? confirmLabel : children}
    </Button>
  );
}
