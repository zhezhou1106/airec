import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import { ToastProvider } from "./hub/ui";
import "./hub/index.css";
import "./app.css";
import { followHubAppearance } from "./lib/appearance";

followHubAppearance();
const client = new QueryClient({ defaultOptions: { queries: { refetchOnWindowFocus: true, retry: 1, staleTime: 5_000 } } });

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={client}>
      <ToastProvider>
        <App />
      </ToastProvider>
    </QueryClientProvider>
  </StrictMode>,
);
