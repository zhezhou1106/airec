import { useEffect, useState } from "react";

/** #/tab/rest… routing, so the hub's Reload keeps you where you were. */
export function useHashRoute(): [string[], (path: string) => void] {
  const read = () => window.location.hash.replace(/^#\/?/, "").split("/").filter(Boolean).map(decodeURIComponent);
  const [parts, setParts] = useState(read);
  useEffect(() => {
    const on = () => setParts(read());
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return [parts, (path: string) => { window.location.hash = `/${path}`; }];
}
